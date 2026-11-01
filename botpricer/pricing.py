from decimal import Decimal, ROUND_CEILING

from .models import Settings


def retail_price(cost_kopeks: int, settings: Settings) -> int:
    cost = Decimal(cost_kopeks)
    fixed = settings.markup_fixed * 100
    percent = cost * settings.markup_percent / 100
    if settings.markup_mode == 'fixed':
        markup = fixed
    elif settings.markup_mode == 'percent':
        markup = percent
    else:
        markup = max(percent, settings.minimum_markup * 100)
    step = Decimal(settings.round_to * 100)
    value = cost + markup + settings.delivery * 100
    return int((value / step).to_integral_value(rounding=ROUND_CEILING) * step)


def money(kopeks: int) -> str:
    rubles, cents = divmod(kopeks, 100)
    result = f'{rubles:,}'.replace(',', ' ')
    return f'{result},{cents:02d} ₽' if cents else result + ' ₽'


def render_offer(title: str, price: int, contact: str, available=True) -> str:
    parts = [title, '', money(price) if available else 'Нет в наличии']
    if available:
        parts.append('В наличии')
    if contact:
        parts.extend(['', 'Заказ: ' + contact])
    return '\n'.join(parts)
