import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Settings
from .pricing import retail_price


def now_iso():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, address TEXT NOT NULL UNIQUE,
 enabled INTEGER NOT NULL, mode TEXT NOT NULL, ttl_hours INTEGER NOT NULL,
 last_checked TEXT, status TEXT NOT NULL DEFAULT 'Не проверялся'
);
CREATE TABLE IF NOT EXISTS messages (
 source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 message_id INTEGER NOT NULL, text TEXT NOT NULL, changed_at TEXT NOT NULL,
 checked_at TEXT NOT NULL, rejected TEXT NOT NULL,
 PRIMARY KEY (source_id, message_id)
);
CREATE TABLE IF NOT EXISTS offers (
 source_id INTEGER NOT NULL, message_id INTEGER NOT NULL, product_key TEXT NOT NULL,
 title TEXT NOT NULL, price_kopeks INTEGER NOT NULL, available INTEGER NOT NULL,
 changed_at TEXT NOT NULL, line INTEGER NOT NULL,
 PRIMARY KEY (source_id, message_id, product_key),
 FOREIGN KEY (source_id, message_id) REFERENCES messages(source_id, message_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS publications (
 id INTEGER PRIMARY KEY, target TEXT NOT NULL, product_key TEXT NOT NULL,
 title TEXT NOT NULL, message_id INTEGER, price_kopeks INTEGER NOT NULL,
 body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ready', updated_at TEXT NOT NULL,
 blocked_price INTEGER, approved_price INTEGER, error TEXT NOT NULL DEFAULT '', UNIQUE(target, product_key)
);
CREATE TABLE IF NOT EXISTS price_history (
 id INTEGER PRIMARY KEY, product_key TEXT NOT NULL, target TEXT NOT NULL,
 old_price INTEGER, new_price INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS offers_product ON offers(product_key);
'''


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def recover(self):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'", (now_iso(),))
            conn.execute("UPDATE publications SET status='uncertain', error='Приложение остановилось во время отправки' WHERE status='sending'")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA journal_mode=WAL')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def rows(self, sql, args=()):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.connect() as conn:
            cursor = conn.execute(sql, args)
            return cursor.lastrowid

    def get_config(self, key, default=None):
        rows = self.rows('SELECT value FROM config WHERE key=?', (key,))
        return json.loads(rows[0]['value']) if rows else default

    def set_config(self, key, value):
        self.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (key, json.dumps(value, ensure_ascii=False)))

    def settings(self):
        return Settings.model_validate(self.get_config('settings', {}))

    def credentials(self):
        return self.get_config('credentials', {})

    def sources(self):
        return self.rows('SELECT * FROM sources ORDER BY id')

    def save_source(self, source, source_id=None):
        values = (source.title.strip(), source.address, int(source.enabled), source.mode, source.ttl_hours)
        if source_id is None:
            return self.execute('INSERT INTO sources(title,address,enabled,mode,ttl_hours) VALUES (?,?,?,?,?)', values)
        with self.connect() as conn:
            old = conn.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
            if not old:
                raise LookupError('Канал не найден')
            if old['address'] != source.address or old['mode'] != source.mode:
                conn.execute('DELETE FROM messages WHERE source_id=?', (source_id,))
            conn.execute('UPDATE sources SET title=?,address=?,enabled=?,mode=?,ttl_hours=? WHERE id=?', values + (source_id,))
        return source_id

    def replace_messages(self, source_id, messages, snapshot=False, deleted=()):
        with self.connect() as conn:
            if snapshot:
                conn.execute('DELETE FROM messages WHERE source_id=?', (source_id,))
            for message_id in deleted:
                conn.execute('DELETE FROM messages WHERE source_id=? AND message_id=?', (source_id, message_id))
            for message, items, rejected in messages:
                message_id = message['id']
                conn.execute('DELETE FROM messages WHERE source_id=? AND message_id=?', (source_id, message_id))
                conn.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',
                             (source_id, message_id, message['text'], message['changed_at'], now_iso(), json.dumps(rejected, ensure_ascii=False)))
                for item in items:
                    conn.execute('INSERT INTO offers VALUES (?,?,?,?,?,?,?,?)',
                                 (source_id, message_id, item.product_key, item.title, item.price_kopeks,
                                  int(item.available), message['changed_at'], item.line))

    def catalog(self, settings=None, at=None):
        settings = settings or self.settings()
        at = at or datetime.now(timezone.utc)
        rows = self.rows('''SELECT o.*, s.title AS source_title, s.ttl_hours
                           FROM offers o JOIN sources s ON o.source_id=s.id
                           WHERE s.enabled=1 ORDER BY o.changed_at DESC, o.message_id DESC''')
        newest, grouped = {}, {}
        for row in rows:
            key = (row['source_id'], row['product_key'])
            if key in newest:
                continue
            newest[key] = row
            age = (at - datetime.fromisoformat(row['changed_at'])).total_seconds()
            if age > row['ttl_hours'] * 3600 or not row['available']:
                continue
            grouped.setdefault(row['product_key'], []).append(row)
        result = []
        for key, options in grouped.items():
            best = min(options, key=lambda item: (item['price_kopeks'], item['source_id']))
            result.append({
                'product_key': key, 'title': best['title'], 'cost_kopeks': best['price_kopeks'],
                'retail_kopeks': retail_price(best['price_kopeks'], settings),
                'source_title': best['source_title'], 'source_id': best['source_id'],
                'changed_at': best['changed_at'], 'supplier_count': len(options),
            })
        return sorted(result, key=lambda item: item['title'].casefold())

    def reviews(self):
        rows = self.rows('''SELECT m.source_id,m.message_id,m.rejected,s.title AS source_title
                           FROM messages m JOIN sources s ON m.source_id=s.id
                           WHERE s.enabled=1 AND m.rejected!='[]' ORDER BY m.changed_at DESC LIMIT 100''')
        for row in rows:
            row['rejected'] = json.loads(row['rejected'])
        return rows

    def publications(self, target=None):
        if target:
            return self.rows('SELECT * FROM publications WHERE target=? ORDER BY id DESC', (target,))
        return self.rows('SELECT * FROM publications ORDER BY id DESC LIMIT 200')

    def runs(self):
        rows = self.rows('SELECT * FROM runs ORDER BY id DESC LIMIT 20')
        for row in rows:
            row['details'] = json.loads(row['details'])
        return rows
