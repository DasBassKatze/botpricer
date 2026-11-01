import re
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


def channel_address(value: str) -> str:
    value = value.strip()
    if value.startswith(('https://', 'http://', 't.me/')):
        parsed = urlparse(value if '://' in value else 'https://' + value)
        if parsed.hostname not in {'t.me', 'telegram.me'}:
            raise ValueError('Скопируйте ссылку на канал из Telegram, например https://t.me/supplier, или укажите @supplier.')
        value = parsed.path.strip('/')
        if '/' in value or value.startswith(('+', 'joinchat')):
            raise ValueError('Ссылка на сообщение или приглашение не подходит. Укажите ссылку на сам канал. Для закрытого канала сначала вступите в него и укажите числовой ID вида -1001234567890.')
    if re.fullmatch(r'-100\d{6,}', value):
        return value
    value = value.lstrip('@')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', value):
        raise ValueError('Введите адрес канала: @supplier, https://t.me/supplier или числовой ID вида -1001234567890.')
    return '@' + value.lower()


class SourceInput(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    address: str = Field(max_length=100)
    enabled: bool = True
    mode: Literal['incremental', 'snapshot'] = 'incremental'
    ttl_hours: int = Field(default=48, ge=1, le=8760)

    _address = field_validator('address')(channel_address)


class Settings(BaseModel):
    target_channel: str = ''
    markup_mode: Literal['fixed', 'percent', 'combined'] = 'fixed'
    markup_fixed: Decimal = Field(default=Decimal('3000'), ge=0, le=10000000)
    markup_percent: Decimal = Field(default=Decimal('7'), ge=0, le=1000)
    minimum_markup: Decimal = Field(default=Decimal('2500'), ge=0, le=10000000)
    delivery: Decimal = Field(default=Decimal('0'), ge=0, le=10000000)
    round_to: int = Field(default=100, ge=1, le=100000)
    daily_time: str = '09:00'
    timezone: str = 'Europe/Moscow'
    schedule_enabled: bool = False
    auto_publish: bool = False
    lookback_days: int = Field(default=7, ge=1, le=90)
    scan_limit: int = Field(default=500, ge=10, le=5000)
    max_change_percent: Decimal = Field(default=Decimal('15'), gt=0, le=1000)
    contact: str = Field(default='', max_length=150)

    @field_validator('target_channel')
    @classmethod
    def validate_target(cls, value):
        return channel_address(value) if value.strip() else ''

    @field_validator('daily_time')
    @classmethod
    def validate_time(cls, value):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('Укажите время в формате ЧЧ:ММ, например 09:00.')
        return value

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Проверьте название часового пояса. Для московского времени укажите Europe/Moscow.')
        return value


class Credentials(BaseModel):
    api_id: int | None = Field(default=None, gt=0)
    api_hash: str = Field(default='', max_length=64)
    bot_token: str = Field(default='', max_length=200)

    @field_validator('api_hash')
    @classmethod
    def validate_hash(cls, value):
        if value and not re.fullmatch(r'[a-fA-F0-9]{32}', value):
            raise ValueError('Скопируйте api_hash с my.telegram.org/apps целиком: нужны 32 символа из цифр и букв a–f.')
        return value

    @field_validator('bot_token')
    @classmethod
    def validate_token(cls, value):
        if value and not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', value):
            raise ValueError('Скопируйте полный токен из ответа BotFather: число, двоеточие и длинная строка символов.')
        return value
