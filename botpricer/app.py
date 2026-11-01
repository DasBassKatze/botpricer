import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import Database
from .models import Credentials, Settings, SourceInput
from .parser import preview
from .pricing import render_offer
from .runtime import InstanceLock
from .telegram import Publisher, Reader, TelegramProblem, safe_error
from .worker import Worker


class ParseInput(BaseModel):
    text: str = Field(max_length=100000)


class PhoneInput(BaseModel):
    phone: str = Field(pattern=r'^\+\d{8,15}$')


class LoginInput(BaseModel):
    code: str = Field(default='', max_length=20)
    password: str = Field(default='', max_length=256)


class ResolveInput(BaseModel):
    action: Literal['approve', 'link', 'retry']
    message_id: int | None = Field(default=None, gt=0)


def create_app(data_dir=None):
    data_dir = Path(data_dir or os.environ.get('BOTPRICER_DATA_DIR', Path(__file__).resolve().parents[1] / 'data'))
    db = Database(data_dir / 'botpricer.sqlite3')
    reader, publisher = Reader(db, data_dir), Publisher(db)
    worker = Worker(db, reader, publisher)
    instance = InstanceLock(data_dir / 'app.lock')

    @asynccontextmanager
    async def lifespan(app):
        instance.acquire()
        try:
            db.recover()
            worker.clock = asyncio.create_task(worker.scheduler())
            yield
        finally:
            await worker.close()
            await reader.close()
            instance.release()

    app = FastAPI(title='BotPricer', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.worker = db, worker
    app.state.reader, app.state.publisher = reader, publisher

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        hostname = urlparse('http://' + request.headers.get('host', '')).hostname
        if hostname not in {'127.0.0.1', 'localhost', '[::1]', '::1'}:
            return JSONResponse({'detail': 'Панель доступна только локально'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'Запрос с другого сайта запрещён'}, status_code=403)
        if request.method in {'POST', 'PUT', 'PATCH'} and request.headers.get('content-type', '').split(';')[0] != 'application/json':
            return JSONResponse({'detail': 'Нужен application/json'}, status_code=415)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        messages = ['.'.join(str(v) for v in error['loc'][1:]) + ': ' + error['msg'] for error in exc.errors()]
        return JSONResponse({'detail': '; '.join(messages)}, status_code=422)

    @app.exception_handler(TelegramProblem)
    async def telegram_problem(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    def idle():
        if worker.busy:
            raise HTTPException(409, 'Дождитесь окончания обновления')

    async def telegram_action(action):
        try:
            return await action
        except Exception as exc:
            raise HTTPException(422, safe_error(exc)) from None

    @app.get('/')
    async def index():
        return FileResponse(Path(__file__).parent / 'static' / 'index.html')

    @app.get('/api/state')
    async def state():
        return {'busy': worker.busy, 'sources': db.sources(), 'catalog': db.catalog(),
                'publications': db.publications(), 'runs': db.runs(), 'reviews': db.reviews(),
                'auto_publish': db.settings().auto_publish}

    @app.get('/api/settings')
    async def get_settings():
        return db.settings().model_dump(mode='json')

    @app.put('/api/settings')
    async def save_settings(settings: Settings):
        idle()
        if settings.auto_publish and not settings.target_channel:
            raise HTTPException(422, 'Сначала укажите канал публикации')
        db.set_config('settings', settings.model_dump(mode='json'))
        return {'ok': True}

    @app.post('/api/pause')
    async def pause():
        settings = db.settings()
        settings.auto_publish = False
        db.set_config('settings', settings.model_dump(mode='json'))
        return {'ok': True}

    @app.post('/api/sources')
    async def add_source(source: SourceInput):
        idle()
        try:
            return {'id': db.save_source(source)}
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Этот канал уже добавлен') from None

    @app.put('/api/sources/{source_id}')
    async def update_source(source_id: int, source: SourceInput):
        idle()
        try:
            db.save_source(source, source_id)
        except LookupError:
            raise HTTPException(404, 'Канал не найден') from None
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Этот канал уже добавлен') from None
        return {'ok': True}

    @app.delete('/api/sources/{source_id}')
    async def delete_source(source_id: int):
        idle()
        db.execute('DELETE FROM sources WHERE id=?', (source_id,))
        return {'ok': True}

    @app.post('/api/sources/{source_id}/check')
    async def check_source(source_id: int):
        idle()
        sources = db.rows('SELECT * FROM sources WHERE id=?', (source_id,))
        if not sources:
            raise HTTPException(404, 'Канал не найден')
        async def check():
            client = await reader.get_client()
            if not await client.is_user_authorized():
                raise TelegramProblem('Войдите в Telegram в разделе «Подключение»')
            from .telegram import resolve_channel
            entity = await resolve_channel(client, sources[0]['address'])
            if not getattr(entity, 'broadcast', False):
                raise TelegramProblem('По этому адресу находится не канал')
            return {'ok': True, 'title': entity.title}
        return await telegram_action(check())

    @app.post('/api/parse')
    async def parse(payload: ParseInput):
        return preview(payload.text)

    @app.post('/api/sync')
    async def sync():
        worker.start()
        return {'started': True}

    @app.get('/api/preview')
    async def post_preview():
        settings = db.settings()
        return [{'product_key': item['product_key'], 'text': render_offer(item['title'], item['retail_kopeks'], settings.contact)}
                for item in db.catalog(settings)]

    @app.get('/api/connection')
    async def connection():
        creds = db.credentials()
        return {'has_api_credentials': bool(creds.get('api_id') and creds.get('api_hash')),
                'has_bot_token': bool(creds.get('bot_token')),
                'authorized': db.get_config('reader_authorized', False)}

    @app.put('/api/credentials')
    async def save_credentials(payload: Credentials):
        idle()
        creds = db.credentials()
        changed = (payload.api_id is not None and payload.api_id != creds.get('api_id')) or (payload.api_hash and payload.api_hash != creds.get('api_hash'))
        if changed:
            if db.get_config('reader_authorized', False):
                raise HTTPException(409, 'Перед сменой API ID/hash отключите аккаунт кнопкой ниже')
            await reader.close()
        for key, value in payload.model_dump().items():
            if value not in ('', None):
                creds[key] = value
        if bool(creds.get('api_id')) != bool(creds.get('api_hash')):
            raise HTTPException(422, 'Укажите API ID и API hash вместе')
        db.set_config('credentials', creds)
        return {'ok': True}

    @app.post('/api/login/code')
    async def login_code(payload: PhoneInput):
        idle()
        result = await telegram_action(reader.request_code(payload.phone))
        if result.get('authorized'):
            db.set_config('reader_authorized', True)
        return result

    @app.post('/api/login/confirm')
    async def login_confirm(payload: LoginInput):
        idle()
        result = await telegram_action(reader.sign_in(payload.code, payload.password))
        if result.get('authorized'):
            db.set_config('reader_authorized', True)
        return result

    @app.post('/api/logout')
    async def logout():
        idle()
        await telegram_action(reader.logout())
        db.set_config('reader_authorized', False)
        return {'ok': True}

    @app.post('/api/connection/check')
    async def connection_check():
        idle()
        result = await telegram_action(reader.authorized())
        db.set_config('reader_authorized', result)
        return {'authorized': result}

    @app.post('/api/target/check')
    async def target_check():
        idle()
        return await telegram_action(publisher.check(db.settings().target_channel))

    @app.post('/api/publications/{publication_id}/resolve')
    async def resolve(publication_id: int, payload: ResolveInput):
        idle()
        rows = db.rows('SELECT * FROM publications WHERE id=?', (publication_id,))
        if not rows:
            raise HTTPException(404, 'Публикация не найдена')
        row = rows[0]
        if payload.action == 'approve' and row['status'] == 'review':
            db.execute("UPDATE publications SET approved_price=blocked_price,status='ready',error='' WHERE id=?", (publication_id,))
        elif payload.action == 'link' and row['status'] in {'uncertain', 'failed'} and payload.message_id:
            db.execute("UPDATE publications SET message_id=?,status='ready',body='',error='' WHERE id=?", (payload.message_id, publication_id))
        elif payload.action == 'retry' and row['status'] in {'uncertain', 'failed'}:
            db.execute("UPDATE publications SET status='ready',error='' WHERE id=?", (publication_id,))
        else:
            raise HTTPException(422, 'Действие недоступно для этой публикации')
        return {'ok': True}

    app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')
    return app
