import asyncio

import pytest

from fastapi.testclient import TestClient

from botpricer.app import create_app


def client_for(tmp_path):
    app = create_app(tmp_path)
    return app, TestClient(app, base_url='http://127.0.0.1:8000')


def test_source_crud_persists_and_duplicate_is_rejected(tmp_path):
    app, client = client_for(tmp_path)
    with client:
        source = {'title': 'Test', 'address': 'https://t.me/Supplier'}
        response = client.post('/api/sources', json=source)
        assert response.status_code == 200
        source_id = response.json()['id']
        assert client.post('/api/sources', json=source).status_code == 409
        source.update(enabled=False, ttl_hours=12)
        assert client.put(f'/api/sources/{source_id}', json=source).status_code == 200
        state = client.get('/api/state').json()
        assert state['sources'][0]['enabled'] == 0
        assert state['sources'][0]['address'] == '@supplier'
    app2, client2 = client_for(tmp_path)
    with client2:
        assert client2.get('/api/state').json()['sources'][0]['ttl_hours'] == 12
        assert client2.delete(f'/api/sources/{source_id}').status_code == 200
        assert not client2.get('/api/state').json()['sources']


def test_secret_fields_are_write_only_and_validation_does_not_echo(tmp_path):
    app, client = client_for(tmp_path)
    with client:
        secret = '123456:' + 's' * 30
        response = client.put('/api/credentials', json={'api_id': 12345, 'api_hash': 'a' * 32, 'bot_token': secret})
        assert response.status_code == 200
        response = client.get('/api/connection')
        assert response.json()['has_bot_token']
        assert secret not in response.text
        assert secret not in client.get('/api/state').text
        invalid_secret = 'super-secret-invalid-token'
        response = client.put('/api/credentials', json={'bot_token': invalid_secret})
        assert response.status_code == 422
        assert invalid_secret not in response.text


def test_local_origin_and_host_protection(tmp_path):
    app, client = client_for(tmp_path)
    with client:
        assert client.get('/api/state', headers={'host': 'evil.example'}).status_code == 403
        assert client.post('/api/pause', json={}, headers={'origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/pause', content='{}', headers={'content-type': 'text/plain'}).status_code == 415
        assert client.post('/api/pause', json={}, headers={'origin': 'http://127.0.0.1:8000'}).status_code == 200


def test_preview_has_no_telegram_side_effects(tmp_path):
    app, client = client_for(tmp_path)
    with client:
        response = client.post('/api/parse', json={'text': 'iPhone 15 128 Black — 61000 ₽'})
        assert response.status_code == 200 and len(response.json()['items']) == 1
        assert client.get('/api/preview').json() == []
        assert not app.state.db.publications()


def test_settings_validation_and_pause(tmp_path):
    app, client = client_for(tmp_path)
    with client:
        settings = client.get('/api/settings').json()
        settings['daily_time'] = '99:99'
        assert client.put('/api/settings', json=settings).status_code == 422
        settings['daily_time'] = '10:30'
        settings['auto_publish'] = True
        assert client.put('/api/settings', json=settings).status_code == 422
        settings['target_channel'] = '@mychannel'
        assert client.put('/api/settings', json=settings).status_code == 200
        assert client.post('/api/pause', json={}).status_code == 200
        assert not client.get('/api/settings').json()['auto_publish']


def test_parallel_sync_is_rejected_and_shutdown_cancels(tmp_path):
    app, client = client_for(tmp_path)
    async def long_run(trigger):
        await asyncio.Event().wait()
    app.state.worker.run = long_run
    with client:
        assert client.post('/api/sync', json={}).status_code == 200
        assert client.post('/api/sync', json={}).status_code == 422
        assert client.post('/api/sources', json={'title': 'Test', 'address': '@supplier'}).status_code == 409


def test_second_instance_cannot_touch_active_run(tmp_path):
    app, client = client_for(tmp_path)
    with client:
        app.state.db.execute("INSERT INTO runs(started_at,status) VALUES ('now','running')")
        app2, client2 = client_for(tmp_path)
        with pytest.raises(RuntimeError, match='уже запущен'):
            with client2:
                pass
        assert app.state.db.runs()[0]['status'] == 'running'
