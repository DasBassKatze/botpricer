import re
import unicodedata
from dataclasses import asdict, dataclass
from decimal import Decimal


PRICE = re.compile(
    r'^(?P<name>.+?)(?:\s+[—–\-=|:]\s*|\s+)'
    r'(?P<price>\d{1,3}(?:[ \u00a0\u202f.,]\d{3})+|\d{3,9})'
    r'(?:[,.](?P<cents>\d{2}))?\s*(?:₽|руб\.?|rub|р\.)?\s*$', re.I
)
COLORS = {
    'черный': 'black', 'чёрный': 'black', 'белый': 'white',
    'синий': 'blue', 'голубой': 'blue', 'розовый': 'pink',
    'зеленый': 'green', 'зелёный': 'green', 'фиолетовый': 'purple',
    'серый': 'gray', 'желтый': 'yellow', 'жёлтый': 'yellow',
}
REGIONS = {'🇺🇸': 'us', '🇨🇳': 'cn', '🇭🇰': 'hk', '🇯🇵': 'jp',
           '🇪🇺': 'eu', '🇷🇺': 'ru', '🇮🇳': 'in', '🇦🇪': 'ae'}


@dataclass
class ParsedItem:
    title: str
    product_key: str
    price_kopeks: int
    available: bool
    line: int


def normalize(name: str) -> str:
    name = unicodedata.normalize('NFKC', name).casefold()
    for flag, region in REGIONS.items():
        name = name.replace(flag, ' ' + region + ' ')
    name = re.sub(r'\bайфон\b', 'iphone', name)
    name = re.sub(r'\b(\d+)\s*(?:gb|гб)\b', r'\1', name)
    name = re.sub(r'\b(\d+)\s*(?:tb|тб)\b', r'\1tb', name)
    name = re.sub(r'\bapple\s+(?=iphone\b)', '', name)
    for word, replacement in COLORS.items():
        name = re.sub(r'\b' + word + r'\b', replacement, name)
    name = re.sub(r'\s*[/|]\s*', ' ', name)
    return ' '.join(name.split()).strip(' —–-:')


def parse_text(text: str) -> tuple[list[ParsedItem], list[dict]]:
    items, rejected = [], []
    for index, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line:
            continue
        if re.search(r'(?:[—–=:|]\s*-\s*\d|\s-\d)', line):
            rejected.append({'line': index, 'text': line, 'reason': 'Цена не может быть отрицательной. Проверьте знак минус перед суммой.'})
            continue
        if re.search(r'[$€]|\b(?:usd|eur|долл|евро)\b', line, re.I):
            rejected.append({'line': index, 'text': line, 'reason': 'Цена указана в другой валюте. Сейчас программа читает только рубли и не пересчитывает доллары или евро.'})
            continue
        unavailable = bool(re.search(r'нет в наличии|продан[оа]?|закончился|❌', line, re.I))
        preorder = bool(re.search(r'под заказ|предзаказ', line, re.I))
        cleaned = re.sub(r'\s*(?:[([]?\s*(?:нет в наличии|продан[оа]?|закончился|в наличии|под заказ|предзаказ)\s*[)\]]?|[✅❌])', '', line, flags=re.I).strip()
        match = PRICE.fullmatch(cleaned)
        if not match:
            rejected.append({'line': index, 'text': line, 'reason': 'Не удалось найти название и цену. Нужна отдельная строка вида: iPhone 15 128 Black US — 61 000 ₽.'})
            continue
        title = match['name'].strip(' —–-=:|')
        key = normalize(title)
        amount = re.sub(r'[ \u00a0\u202f.,]', '', match['price'])
        price = int(Decimal(amount + '.' + (match['cents'] or '00')) * 100)
        if len(key) < 3 or price <= 0 or price > 1_000_000_000_00:
            rejected.append({'line': index, 'text': line, 'reason': 'Название или цена выглядят неверно. Укажите полное название и положительную цену в рублях.'})
            continue
        if re.search(r'\biphone\b', key) and not re.search(r'\b(?:64|128|256|512|1024|1tb|2tb)\b', key):
            rejected.append({'line': index, 'text': line, 'reason': 'В этой строке не указан объём памяти iPhone. Добавьте его к названию, например: iPhone 15 128 Black US — 61 000 ₽.'})
            continue
        items.append(ParsedItem(title, key, price, not (unavailable or preorder), index))
    keys = [item.product_key for item in items]
    duplicates = {key for key in keys if keys.count(key) > 1}
    for item in items:
        if item.product_key in duplicates:
            rejected.append({'line': item.line, 'text': item.title, 'reason': 'В одном сообщении найдены две цены для одинакового товара. Уточните название вариантов или оставьте одну действующую цену.'})
    return [item for item in items if item.product_key not in duplicates], rejected


def preview(text):
    items, rejected = parse_text(text)
    return {'items': [asdict(item) for item in items], 'rejected': rejected}
