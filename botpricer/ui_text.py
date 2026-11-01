FIELD_NAMES = {
    'title': 'Название поставщика', 'address': 'Ссылка на канал поставщика',
    'mode': 'Как поставщик публикует цены', 'ttl_hours': 'Срок доверия к цене',
    'target_channel': 'Ваш канал для объявлений', 'markup_mode': 'Способ наценки',
    'markup_fixed': 'Наценка в рублях', 'markup_percent': 'Наценка в процентах',
    'minimum_markup': 'Минимальная наценка', 'delivery': 'Доплата за доставку',
    'round_to': 'Шаг округления', 'daily_time': 'Время проверки цен',
    'timezone': 'Часовой пояс', 'lookback_days': 'Период поиска прайсов',
    'scan_limit': 'Количество сообщений за проверку', 'max_change_percent': 'Порог проверки новой цены',
    'contact': 'Контакт для заказа', 'phone': 'Номер телефона',
    'code': 'Код входа в Telegram', 'password': 'Облачный пароль Telegram',
    'api_id': 'API ID', 'api_hash': 'API hash', 'bot_token': 'Токен бота',
    'text': 'Текст прайса', 'message_id': 'Номер сообщения',
}


def validation_message(error):
    field = str(error['loc'][-1]) if len(error['loc']) > 1 else ''
    label = FIELD_NAMES.get(field, 'Данные формы')
    kind, context = error['type'], error.get('ctx', {})
    if kind == 'value_error':
        message = error['msg'].removeprefix('Value error, ')
    elif field == 'phone' and kind == 'string_pattern_mismatch':
        message = 'введите номер с кодом страны, без пробелов и скобок, например +79991234567'
    elif kind in {'int_parsing', 'int_type', 'int_from_float'}:
        message = 'введите целое число'
    elif kind in {'decimal_parsing', 'decimal_type', 'finite_number'}:
        message = 'введите обычное число, например 3000 или 7.5'
    elif kind == 'greater_than_equal':
        message = f'значение должно быть не меньше {context["ge"]}'
    elif kind == 'greater_than':
        message = f'значение должно быть больше {context["gt"]}'
    elif kind == 'less_than_equal':
        message = f'значение должно быть не больше {context["le"]}'
    elif kind == 'string_too_short':
        message = f'введите не меньше {context["min_length"]} символов'
    elif kind == 'string_too_long':
        message = f'оставьте не больше {context["max_length"]} символов'
    elif kind == 'missing':
        message = 'заполните это поле'
    else:
        message = 'проверьте значение и попробуйте сохранить ещё раз'
    return f'{label}: {message}'
