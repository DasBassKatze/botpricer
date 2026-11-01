from decimal import Decimal

import pytest
from pydantic import ValidationError

from botpricer.models import Settings, channel_address
from botpricer.parser import normalize, parse_text
from botpricer.pricing import money, retail_price


@pytest.mark.parametrize('line,price', [
    ('iPhone 15 128 Black US — 61 000 ₽', 6100000),
    ('iPhone 15 128 Black US 61000', 6100000),
    ('iPhone 15 256 Blue EU = 68.500 руб.', 6850000),
    ('Наушники Sony — 12 345,67 ₽', 1234567),
    ('Наушники Sony | 12\u202f345 ₽', 1234500),
])
def test_price_formats(line, price):
    items, rejected = parse_text(line)
    assert not rejected
    assert items[0].price_kopeks == price


def test_normalization_preserves_variants():
    assert normalize('Apple iPhone 15 128 GB чёрный 🇺🇸') == normalize('айфон 15 128 Black US')
    assert normalize('iPhone 15 128 Black US') != normalize('iPhone 15 128 Black EU')
    assert normalize('iPhone 15 128 Black eSIM') != normalize('iPhone 15 128 Black SIM')
    assert normalize('iPhone 15 128 Black новый') != normalize('iPhone 15 128 Black б/у')


@pytest.mark.parametrize('line', ['iPhone 15 Black — 61000 ₽', 'iPhone 15 128 — 700 USD', 'Товар — -100 ₽', 'Товар — 0 ₽', 'Товар 100 €'])
def test_ambiguous_or_invalid_prices_are_rejected(line):
    items, rejected = parse_text(line)
    assert not items
    assert rejected


def test_stock_and_duplicates():
    items, rejected = parse_text('Товар Sony — 1000 ₽ нет в наличии\nТовар Bose — 2000 ₽ под заказ')
    assert len(items) == 2 and not any(item.available for item in items)
    items, rejected = parse_text('Товар Sony — 1000 ₽\nТовар Sony — 2000 ₽')
    assert not items and len(rejected) == 2


@pytest.mark.parametrize('mode,expected', [('fixed', 6350000), ('percent', 6480000), ('combined', 6480000)])
def test_markup_and_upward_rounding(mode, expected):
    settings = Settings(markup_mode=mode)
    assert retail_price(6050000, settings) == expected
    assert retail_price(6050000, settings) == expected


def test_decimal_minimum_and_delivery():
    settings = Settings(markup_mode='combined', markup_percent=Decimal('0.1'), minimum_markup=Decimal('2500.01'), delivery=Decimal('99.99'), round_to=1)
    assert retail_price(10000, settings) == 270000
    assert money(1234567) == '12 345,67 ₽'


def test_validation():
    assert channel_address('https://t.me/Supplier') == '@supplier'
    assert channel_address('-1001234567890') == '-1001234567890'
    with pytest.raises(ValueError):
        channel_address('https://evil.example/channel')
    with pytest.raises(ValidationError):
        Settings(daily_time='25:70')
    with pytest.raises(ValidationError):
        Settings(timezone='Never/Here')
