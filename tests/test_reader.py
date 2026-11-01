import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from botpricer.telegram import Reader, TelegramProblem, resolve_channel


class FakeClient:
    def __init__(self, history=(), known=()):
        self.history, self.known = history, known
        self.requested_ids = []

    async def is_user_authorized(self):
        return True

    async def get_entity(self, address):
        return SimpleNamespace(broadcast=True)

    async def iter_messages(self, entity, limit):
        for message in self.history[:limit]:
            yield message

    async def get_messages(self, entity, ids):
        self.requested_ids.extend(ids)
        return [m for m in self.known if m.id in ids]


def make_reader(client):
    reader = Reader(None, None)
    async def get_client():
        return client
    reader.get_client = get_client
    return reader


def message(message_id, date, edit_date=None):
    return SimpleNamespace(id=message_id, date=date, edit_date=edit_date, raw_text='Sony XM5 — 1000 ₽')


def test_rereads_known_messages_outside_lookback_and_finds_deletion():
    now = datetime.now(timezone.utc)
    old = message(1, now - timedelta(days=30), edit_date=now)
    client = FakeClient([message(100, now)], [old])
    rows, deleted = asyncio.run(make_reader(client).fetch('@supplier', now - timedelta(days=7), 10, [1, 2]))
    assert {row['id'] for row in rows} == {1, 100}
    assert deleted == [2]
    assert next(row for row in rows if row['id'] == 1)['changed_at'] == now.isoformat()
    assert client.requested_ids == [1, 2]


def test_scan_limit_is_reported_instead_of_partial_success():
    now = datetime.now(timezone.utc)
    client = FakeClient([message(i, now) for i in range(12)])
    with pytest.raises(TelegramProblem, match='лимит'):
        asyncio.run(make_reader(client).fetch('@supplier', now - timedelta(days=7), 10, []))


def test_numeric_channel_is_resolved_from_dialogs_when_not_cached():
    class DialogClient:
        async def get_entity(self, address):
            raise ValueError('Unknown peer')
        async def iter_dialogs(self):
            yield SimpleNamespace(id=-1001234567890, entity='private_channel')
    assert asyncio.run(resolve_channel(DialogClient(), '-1001234567890')) == 'private_channel'
