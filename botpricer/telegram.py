import asyncio
from datetime import datetime

import httpx
from telethon import TelegramClient, errors


class TelegramProblem(Exception):
    pass


class DeliveryUncertain(TelegramProblem):
    pass


def safe_error(exc):
    if isinstance(exc, TelegramProblem):
        return str(exc)
    if isinstance(exc, errors.FloodWaitError):
        return f'Лимит Telegram: повторите через {exc.seconds} сек.'
    if isinstance(exc, errors.PhoneCodeInvalidError):
        return 'Код входа не подошёл. Проверьте последнее сообщение с кодом в Telegram и введите его ещё раз.'
    if isinstance(exc, errors.PhoneCodeExpiredError):
        return 'Срок действия кода закончился. В шаге 2 нажмите «Получить код Telegram» и введите новый код.'
    if isinstance(exc, errors.PasswordHashInvalidError):
        return 'Пароль не подошёл. Нужен облачный пароль двухэтапной проверки Telegram, а не код из сообщения.'
    if isinstance(exc, errors.PhoneNumberInvalidError):
        return 'Проверьте номер телефона: нужен номер вашего аккаунта Telegram с кодом страны, например +79991234567.'
    if isinstance(exc, (errors.ChannelPrivateError, errors.ChatAdminRequiredError)):
        return 'Аккаунт не может открыть канал. Проверьте адрес и откройте этот канал в Telegram с того же аккаунта. Для закрытого канала сначала нужно вступить.'
    if isinstance(exc, errors.AuthKeyUnregisteredError):
        return 'Telegram завершил вход в программу. Откройте «Подключение Telegram» и войдите снова по коду в шаге 2.'
    if isinstance(exc, (httpx.HTTPError, OSError, asyncio.TimeoutError)):
        return 'Не удалось соединиться с Telegram. Проверьте интернет и доступ к Telegram на этом компьютере, затем повторите действие.'
    return 'Ошибка Telegram (' + type(exc).__name__ + ')'


def peer(address):
    return int(address) if address.startswith('-100') else address


async def resolve_channel(client, address):
    try:
        return await client.get_entity(peer(address))
    except ValueError:
        if not address.startswith('-100'):
            raise
        async for dialog in client.iter_dialogs():
            if dialog.id == int(address):
                return dialog.entity
        raise TelegramProblem('Канал с таким ID не найден в аккаунте. Проверьте ID и убедитесь, что подключённый аккаунт уже вступил в этот канал.') from None


class Reader:
    def __init__(self, db, data_dir):
        self.db, self.data_dir = db, data_dir
        self.client = None
        self.phone = None
        self.code_hash = None
        self.lock = asyncio.Lock()

    async def get_client(self):
        if self.client is None:
            creds = self.db.credentials()
            if not creds.get('api_id') or not creds.get('api_hash'):
                raise TelegramProblem('Откройте «Подключение Telegram» и сохраните API ID и API hash в шаге 1. Затем войдите в аккаунт в шаге 2.')
            self.client = TelegramClient(str(self.data_dir / 'reader'), int(creds['api_id']), creds['api_hash'],
                                         flood_sleep_threshold=20, request_retries=2, connection_retries=2)
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    async def authorized(self):
        client = await self.get_client()
        return await client.is_user_authorized()

    async def request_code(self, phone):
        async with self.lock:
            client = await self.get_client()
            if await client.is_user_authorized():
                return {'authorized': True}
            result = await client.send_code_request(phone)
            self.phone, self.code_hash = phone, result.phone_code_hash
            return {'code_sent': True}

    async def sign_in(self, code='', password=''):
        async with self.lock:
            client = await self.get_client()
            if not self.phone or not self.code_hash:
                raise TelegramProblem('В шаге 2 нажмите «Получить код Telegram». После этого введите присланный код.')
            try:
                if password:
                    await client.sign_in(password=password)
                else:
                    await client.sign_in(phone=self.phone, code=code, phone_code_hash=self.code_hash)
            except errors.SessionPasswordNeededError:
                return {'password_required': True}
            self.phone = self.code_hash = None
            return {'authorized': True}

    async def close(self):
        if self.client:
            await self.client.disconnect()
            self.client = None

    async def logout(self):
        async with self.lock:
            client = await self.get_client()
            await client.log_out()
            self.client = None
            self.phone = self.code_hash = None

    async def fetch(self, address, since: datetime, limit: int, known_ids):
        client = await self.get_client()
        if not await client.is_user_authorized():
            raise TelegramProblem('Откройте «Подключение Telegram» и выполните вход по номеру телефона в шаге 2.')
        entity = await resolve_channel(client, address)
        if not getattr(entity, 'broadcast', False):
            raise TelegramProblem('Источник должен быть Telegram-каналом')
        collected = {}
        reached_end = False
        async for message in client.iter_messages(entity, limit=limit + 1):
            if message.date < since:
                reached_end = True
                break
            if len(collected) >= limit:
                raise TelegramProblem('Превышен лимит сообщений за проверку. В «Настройках» увеличьте «Сколько сообщений читать за проверку» или уменьшите «За сколько дней искать новые прайсы».')
            collected[message.id] = message
        else:
            reached_end = True
        if not reached_end:
            raise TelegramProblem('Не удалось полностью прочитать выбранный период')
        deleted = []
        known_ids = list(known_ids)
        for start in range(0, len(known_ids), 100):
            ids = known_ids[start:start + 100]
            messages = await client.get_messages(entity, ids=ids)
            found = {m.id: m for m in messages if m and getattr(m, 'date', None)}
            collected.update(found)
            deleted.extend(message_id for message_id in ids if message_id not in found)
        result = []
        for message in collected.values():
            result.append({'id': message.id, 'text': message.raw_text or '',
                           'changed_at': (message.edit_date or message.date).isoformat()})
        return sorted(result, key=lambda m: (m['changed_at'], m['id']), reverse=True), deleted


class Publisher:
    def __init__(self, db):
        self.db = db

    async def call(self, method, payload):
        token = self.db.credentials().get('bot_token')
        if not token:
            raise TelegramProblem('Откройте «Подключение Telegram» и сохраните токен из ответа BotFather в шаге 1.')
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(f'https://api.telegram.org/bot{token}/{method}', json=payload)
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise DeliveryUncertain('Нет подтверждения Telegram. Проверьте канал перед повторной отправкой') from exc
            if data.get('ok'):
                return data.get('result', {})
            code = data.get('error_code', response.status_code)
            if method == 'editMessageText' and 'message is not modified' in data.get('description', '').lower():
                return True
            wait = data.get('parameters', {}).get('retry_after', 0)
            if code == 429 and attempt == 0 and 0 < wait <= 20:
                await asyncio.sleep(wait + 1)
                continue
            if code >= 500:
                raise DeliveryUncertain('Telegram не подтвердил операцию. Проверьте канал')
            descriptions = {400: 'Проверьте канал, ID сообщения и формат публикации',
                            401: 'Токен бота не работает. Получите действующий токен у BotFather и сохраните его в «Подключении Telegram».',
                            403: 'Бот не может работать с каналом. Проверьте его адрес в настройках и добавьте бота администратором с правом публикации сообщений.',
                            429: f'Лимит Telegram, повторите через {wait} сек.'}
            raise TelegramProblem(descriptions.get(code, f'Ошибка Bot API: {code}'))

    async def check(self, target):
        if not target:
            raise TelegramProblem('Откройте «Настройки», заполните поле «Ваш канал для объявлений» и сохраните настройки.')
        me = await self.call('getMe', {})
        chat = await self.call('getChat', {'chat_id': target})
        member = await self.call('getChatMember', {'chat_id': target, 'user_id': me['id']})
        if chat.get('type') != 'channel':
            raise TelegramProblem('Для публикации нужен канал')
        if member.get('status') != 'administrator' or not member.get('can_post_messages'):
            raise TelegramProblem('В Telegram откройте управление вашим каналом, добавьте своего бота в администраторы и разрешите публикацию сообщений. Затем повторите проверку прав.')
        return {'bot': me.get('username'), 'channel': chat.get('title'), 'channel_id': chat['id']}

    async def send(self, target, body):
        return await self.call('sendMessage', {'chat_id': target, 'text': body,
                                              'link_preview_options': {'is_disabled': True}})

    async def edit(self, target, message_id, body):
        return await self.call('editMessageText', {'chat_id': target, 'message_id': message_id,
                                                  'text': body, 'link_preview_options': {'is_disabled': True}})
