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
            raise ValueError('Нужна ссылка t.me или @username')
        value = parsed.path.strip('/')
        if '/' in value or value.startswith(('+', 'joinchat')):
            raise ValueError('Для закрытого канала укажите его числовой ID после вступления аккаунта')
    if re.fullmatch(r'-100\d{6,}', value):
        return value
    value = value.lstrip('@')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', value):
        raise ValueError('Нужен @username, ссылка t.me/username или числовой ID -100…')
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
            raise ValueError('Время в формате ЧЧ:ММ')
        return value

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Неизвестный часовой пояс')
        return value


class Credentials(BaseModel):
    api_id: int | None = Field(default=None, gt=0)
    api_hash: str = Field(default='', max_length=64)
    bot_token: str = Field(default='', max_length=200)

    @field_validator('api_hash')
    @classmethod
    def validate_hash(cls, value):
        if value and not re.fullmatch(r'[a-fA-F0-9]{32}', value):
            raise ValueError('API hash должен содержать 32 шестнадцатеричных символа')
        return value

    @field_validator('bot_token')
    @classmethod
    def validate_token(cls, value):
        if value and not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', value):
            raise ValueError('Некорректный токен BotFather')
        return value
