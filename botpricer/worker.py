import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .db import now_iso
from .parser import parse_text
from .pricing import render_offer
from .telegram import DeliveryUncertain, TelegramProblem, safe_error


def schedule_key(settings, now, last_key):
    local = now.astimezone(ZoneInfo(settings.timezone))
    key = f'{settings.timezone}:{local.date().isoformat()}'
    if settings.schedule_enabled and local.strftime('%H:%M') >= settings.daily_time and key != last_key:
        return key
    return None


class Worker:
    def __init__(self, db, reader, publisher):
        self.db, self.reader, self.publisher = db, reader, publisher
        self.task = None
        self.clock = None

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def start(self, trigger='manual'):
        if self.busy:
            raise TelegramProblem('Обновление уже выполняется')
        self.task = asyncio.create_task(self.run(trigger))

    async def close(self):
        if self.clock:
            self.clock.cancel()
        if self.busy:
            self.task.cancel()
        await asyncio.gather(*(t for t in (self.clock, self.task) if t), return_exceptions=True)

    async def scheduler(self):
        while True:
            try:
                if not self.busy:
                    settings = self.db.settings()
                    key = schedule_key(settings, datetime.now(timezone.utc), self.db.get_config('last_schedule_key'))
                    if key:
                        self.db.set_config('last_schedule_key', key)
                        self.start('schedule')
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(15)

    async def run(self, trigger):
        settings = self.db.settings()
        run_id = self.db.execute("INSERT INTO runs(started_at,status,details) VALUES (?,'running',?)",
                                 (now_iso(), json.dumps({'trigger': trigger})))
        report = {'trigger': trigger, 'sources_ok': 0, 'sources_failed': 0,
                  'offers': 0, 'created': 0, 'updated': 0, 'unavailable': 0, 'review': 0, 'errors': []}
        status = 'success'
        try:
            since = datetime.now(timezone.utc) - timedelta(days=settings.lookback_days)
            for source in self.db.sources():
                if not source['enabled']:
                    continue
                try:
                    known = [r['message_id'] for r in self.db.rows('SELECT message_id FROM messages WHERE source_id=?', (source['id'],))]
                    messages, deleted = await self.reader.fetch(source['address'], since, settings.scan_limit, known)
                    prepared = []
                    for message in messages:
                        items, rejected = parse_text(message['text'])
                        prepared.append((message, items, rejected))
                    if source['mode'] == 'snapshot':
                        candidates = [row for row in prepared if row[1]]
                        if not candidates:
                            raise TelegramProblem('Полный прайс не найден. Старые данные сохранены')
                        latest = candidates[0]
                        if latest[2]:
                            raise TelegramProblem('В полном прайсе есть нераспознанные строки. Проверьте формат через тест парсера')
                        prepared = [latest]
                    self.db.replace_messages(source['id'], prepared, source['mode'] == 'snapshot', deleted)
                    self.db.execute('UPDATE sources SET last_checked=?,status=? WHERE id=?',
                                    (now_iso(), f'ОК · сообщений: {len(prepared)}', source['id']))
                    report['sources_ok'] += 1
                except Exception as exc:
                    error = safe_error(exc)
                    self.db.execute('UPDATE sources SET last_checked=?,status=? WHERE id=?', (now_iso(), error, source['id']))
                    report['sources_failed'] += 1
                    report['errors'].append(source['title'] + ': ' + error)
            catalog = self.db.catalog(settings)
            report['offers'] = len(catalog)
            if settings.auto_publish:
                target_info = await self.publisher.check(settings.target_channel)
                target = str(target_info['channel_id'])
                await self.publish(catalog, settings, target, report)
            if report['errors']:
                status = 'partial'
        except asyncio.CancelledError:
            status = 'interrupted'
            raise
        except Exception as exc:
            status = 'failed'
            report['errors'].append(safe_error(exc))
        finally:
            self.db.execute('UPDATE runs SET finished_at=?,status=?,details=? WHERE id=?',
                            (now_iso(), status, json.dumps(report, ensure_ascii=False), run_id))

    async def publish(self, catalog, settings, target, report):
        existing = {r['product_key']: r for r in self.db.publications(target)}
        current_keys = set()
        for item in catalog:
            if not self.db.settings().auto_publish:
                return
            key = item['product_key']
            current_keys.add(key)
            old = existing.get(key)
            if old and old['status'] == 'uncertain':
                continue
            price = item['retail_kopeks']
            body = render_offer(item['title'], price, settings.contact)
            if old and old['message_id'] and old['body'] == body:
                if old['status'] == 'review':
                    self.db.execute("UPDATE publications SET status='ready',blocked_price=NULL,approved_price=NULL,error='' WHERE id=?", (old['id'],))
                continue
            if old and old['message_id'] and old['price_kopeks'] and old['approved_price'] != price:
                change = Decimal(abs(price - old['price_kopeks'])) * 100 / old['price_kopeks']
                if change > settings.max_change_percent:
                    self.db.execute("UPDATE publications SET status='review',blocked_price=?,error=? WHERE id=?",
                                    (price, 'Большое изменение цены: требуется подтверждение', old['id']))
                    report['review'] += 1
                    continue
            await self.apply(item['title'], key, price, body, target, old, report)
        if not report['sources_failed']:
            for key, old in existing.items():
                if not self.db.settings().auto_publish:
                    return
                if key not in current_keys and old['message_id'] and old['status'] != 'uncertain':
                    body = render_offer(old['title'], old['price_kopeks'], settings.contact, available=False)
                    if body != old['body']:
                        changed = await self.apply(old['title'], key, old['price_kopeks'], body, target, old, report)
                        if changed:
                            report['unavailable'] += 1

    async def apply(self, title, key, price, body, target, old, report):
        new = old is None or old['message_id'] is None
        if old:
            publication_id = old['id']
            self.db.execute("UPDATE publications SET status='sending',error='' WHERE id=?", (publication_id,))
        else:
            publication_id = self.db.execute('''INSERT INTO publications(target,product_key,title,price_kopeks,body,status,updated_at)
                                               VALUES (?,?,?,?,?,'sending',?)''',
                                            (target, key, title, price, '', now_iso()))
        try:
            if new:
                result = await self.publisher.send(target, body)
                message_id = result['message_id']
            else:
                await self.publisher.edit(target, old['message_id'], body)
                message_id = old['message_id']
        except asyncio.CancelledError:
            self.db.execute("UPDATE publications SET status='uncertain',error=? WHERE id=?",
                            ('Отправка прервана. Проверьте канал', publication_id))
            raise
        except Exception as exc:
            uncertain = isinstance(exc, DeliveryUncertain) or not isinstance(exc, TelegramProblem)
            self.db.execute('UPDATE publications SET status=?,error=? WHERE id=?',
                            ('uncertain' if uncertain else 'failed', safe_error(exc), publication_id))
            report['errors'].append(title + ': ' + safe_error(exc))
            return False
        with self.db.connect() as conn:
            conn.execute('''UPDATE publications SET title=?,message_id=?,price_kopeks=?,body=?,status='ready',
                            updated_at=?,blocked_price=NULL,approved_price=NULL,error='' WHERE id=?''',
                         (title, message_id, price, body, now_iso(), publication_id))
            if not old or price != old['price_kopeks']:
                conn.execute('INSERT INTO price_history(product_key,target,old_price,new_price,created_at) VALUES (?,?,?,?,?)',
                             (key, target, old['price_kopeks'] if old else None, price, now_iso()))
        report['created' if new else 'updated'] += 1
        await asyncio.sleep(0.1)
        return True
