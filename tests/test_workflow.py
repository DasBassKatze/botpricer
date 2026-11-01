import asyncio
from datetime import datetime, timedelta, timezone

from botpricer.db import Database
from botpricer.models import Settings, SourceInput
from botpricer.parser import parse_text
from botpricer.telegram import DeliveryUncertain, TelegramProblem
from botpricer.worker import Worker, schedule_key


def make_db(tmp_path):
    db = Database(tmp_path / 'test.db')
    db.set_config('settings', Settings(target_channel='@mychannel', auto_publish=True, markup_fixed=0, round_to=1).model_dump(mode='json'))
    return db


def add_source(db, title='Supplier', **kwargs):
    return db.save_source(SourceInput(title=title, address='@' + title.lower(), **kwargs))


def put(db, source_id, text, message_id=1, age=0):
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=age)).isoformat()
    message = {'id': message_id, 'text': text, 'changed_at': timestamp}
    items, rejected = parse_text(text)
    db.replace_messages(source_id, [(message, items, rejected)])
    return message


class FakeReader:
    def __init__(self, messages=None, fail=False):
        self.messages = messages or []
        self.fail = fail

    async def fetch(self, *args):
        if self.fail:
            raise TelegramProblem('Нет доступа')
        return self.messages, []


class FakePublisher:
    def __init__(self, uncertain=False):
        self.sent, self.edited = [], []
        self.uncertain = uncertain

    async def check(self, target):
        return {'channel_id': -1001234567890}

    async def send(self, target, body):
        self.sent.append(body)
        if self.uncertain:
            raise DeliveryUncertain('Неизвестен результат')
        return {'message_id': len(self.sent)}

    async def edit(self, target, message_id, body):
        self.edited.append((message_id, body))


def test_catalog_uses_latest_per_supplier_then_cheapest(tmp_path):
    db = make_db(tmp_path)
    a, b = add_source(db), add_source(db, 'Other')
    put(db, a, 'Sony XM5 — 1000 ₽', 1, age=2)
    put(db, a, 'Sony XM5 — 3000 ₽', 2, age=1)
    put(db, b, 'Sony XM5 — 2000 ₽')
    assert db.catalog()[0]['cost_kopeks'] == 200000
    put(db, b, 'Sony XM5 — 2000 ₽ нет в наличии')
    assert db.catalog()[0]['cost_kopeks'] == 300000


def test_expiry_disabled_and_removed_sources(tmp_path):
    db = make_db(tmp_path)
    a = add_source(db, ttl_hours=24)
    put(db, a, 'Sony XM5 — 1000 ₽', age=25)
    assert not db.catalog()
    put(db, a, 'Sony XM5 — 1000 ₽')
    assert db.catalog()
    db.execute('UPDATE sources SET enabled=0 WHERE id=?', (a,))
    assert not db.catalog()
    db.execute('DELETE FROM sources WHERE id=?', (a,))
    assert not db.rows('SELECT * FROM messages')
    assert not db.rows('SELECT * FROM offers')


def test_edit_replaces_message_and_deleted_items(tmp_path):
    db = make_db(tmp_path)
    a = add_source(db)
    put(db, a, 'Sony XM5 — 1000 ₽\nSony XM4 — 800 ₽')
    put(db, a, 'Sony XM5 — 1200 ₽')
    assert len(db.catalog()) == 1
    assert db.catalog()[0]['cost_kopeks'] == 120000
    db.replace_messages(a, [], deleted=[1])
    assert not db.catalog()


def test_repeated_sync_edits_instead_of_duplicate_and_blocks_jump(tmp_path):
    db = make_db(tmp_path)
    a = add_source(db)
    message = put(db, a, 'Sony XM5 — 1000 ₽')
    reader, publisher = FakeReader([message]), FakePublisher()
    worker = Worker(db, reader, publisher)
    asyncio.run(worker.run('manual'))
    asyncio.run(worker.run('manual'))
    assert len(publisher.sent) == 1 and not publisher.edited
    message = put(db, a, 'Sony XM5 — 1100 ₽')
    reader.messages = [message]
    asyncio.run(worker.run('manual'))
    assert len(publisher.sent) == 1 and len(publisher.edited) == 1
    message = put(db, a, 'Sony XM5 — 2000 ₽')
    reader.messages = [message]
    asyncio.run(worker.run('manual'))
    publication = db.publications()[0]
    assert publication['status'] == 'review'
    assert publication['price_kopeks'] == 110000
    db.execute("UPDATE publications SET approved_price=blocked_price,status='ready' WHERE id=?", (publication['id'],))
    asyncio.run(worker.run('manual'))
    assert db.publications()[0]['price_kopeks'] == 200000
    assert len(publisher.edited) == 2


def test_uncertain_send_is_not_automatically_retried(tmp_path):
    db = make_db(tmp_path)
    a = add_source(db)
    message = put(db, a, 'Sony XM5 — 1000 ₽')
    publisher = FakePublisher(uncertain=True)
    worker = Worker(db, FakeReader([message]), publisher)
    asyncio.run(worker.run('manual'))
    asyncio.run(worker.run('manual'))
    assert len(publisher.sent) == 1
    assert db.publications()[0]['status'] == 'uncertain'


def test_failure_does_not_withdraw_expired_products(tmp_path):
    db = make_db(tmp_path)
    a = add_source(db, ttl_hours=1)
    message = put(db, a, 'Sony XM5 — 1000 ₽')
    publisher = FakePublisher()
    worker = Worker(db, FakeReader([message]), publisher)
    asyncio.run(worker.run('manual'))
    put(db, a, 'Sony XM5 — 1000 ₽', age=2)
    worker.reader = FakeReader(fail=True)
    asyncio.run(worker.run('manual'))
    assert not publisher.edited
    assert db.runs()[0]['status'] == 'partial'


def test_snapshot_parse_failure_preserves_old_data(tmp_path):
    db = make_db(tmp_path)
    a = add_source(db, mode='snapshot')
    put(db, a, 'Sony XM5 — 1000 ₽')
    invalid = {'id': 2, 'text': 'Новый прайс\nSony XM5 — 1200 ₽\nНеизвестная строка', 'changed_at': datetime.now(timezone.utc).isoformat()}
    worker = Worker(db, FakeReader([invalid]), FakePublisher())
    asyncio.run(worker.run('manual'))
    assert db.catalog()[0]['cost_kopeks'] == 100000
    assert db.runs()[0]['details']['sources_failed'] == 1


def test_pause_stops_publication(tmp_path):
    db = make_db(tmp_path)
    settings = db.settings()
    settings.auto_publish = False
    db.set_config('settings', settings.model_dump(mode='json'))
    a = add_source(db)
    message = put(db, a, 'Sony XM5 — 1000 ₽')
    publisher = FakePublisher()
    asyncio.run(Worker(db, FakeReader([message]), publisher).run('manual'))
    assert db.catalog() and not publisher.sent


def test_daily_schedule_timezone_and_catchup():
    settings = Settings(schedule_enabled=True)
    at = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
    key = schedule_key(settings, at, None)
    assert key == 'Europe/Moscow:2026-11-01'
    assert schedule_key(settings, at, key) is None
    assert schedule_key(settings, at - timedelta(minutes=1), None) is None
    assert schedule_key(settings, at + timedelta(hours=8), None) == key


def test_restart_marks_inflight_as_uncertain(tmp_path):
    db = make_db(tmp_path)
    db.execute("INSERT INTO publications(target,product_key,title,price_kopeks,body,status,updated_at) VALUES ('x','x','x',100,'','sending','now')")
    recovered = Database(db.path)
    recovered.recover()
    assert recovered.publications()[0]['status'] == 'uncertain'
