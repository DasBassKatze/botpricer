import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from botpricer.app import create_app


def main():
    with tempfile.TemporaryDirectory(prefix='botpricer-ui-') as directory:
        app = create_app(Path(directory))
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8000, log_level='warning', access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError('Панель не запустилась; убедитесь, что порт 8000 свободен')
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1050})
                page.set_default_timeout(10000)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
                page.goto('http://127.0.0.1:8000', wait_until='networkidle')
                output = Path('test-results')
                output.mkdir(exist_ok=True)
                page.screenshot(path=str(output / 'overview.png'), full_page=True)
                page.get_by_role('button', name='Каналы-источники').click()
                page.locator('#source-title').fill('Тестовый поставщик')
                page.locator('#source-address').fill('https://t.me/TestSupplier')
                page.get_by_role('button', name='Сохранить канал').click()
                page.locator('#sources-table').get_by_text('@testsupplier', exact=True).wait_for()
                page.get_by_role('button', name='Изменить', exact=True).click()
                page.locator('#source-ttl').fill('24')
                page.get_by_role('button', name='Сохранить канал').click()
                page.get_by_role('cell', name='24 ч', exact=True).wait_for()
                page.get_by_role('button', name='Отключить', exact=True).click()
                page.locator('#sources-table').get_by_text('Отключён', exact=True).wait_for()
                page.get_by_role('button', name='Настройки', exact=True).click()
                page.locator('[name=markup_mode]').select_option('percent')
                page.locator('[name=markup_percent]').fill('8')
                page.get_by_role('button', name='Сохранить настройки').click()
                page.get_by_text('Все изменения сохранены', exact=True).wait_for()
                page.reload(wait_until='networkidle')
                page.get_by_role('button', name='Настройки', exact=True).click()
                assert page.locator('[name=markup_percent]').input_value() == '8'
                page.get_by_role('button', name='Проверка прайса', exact=True).click()
                page.locator('textarea').fill('iPhone 15 128 Black US — 61 000 ₽\nНеизвестная строка')
                page.get_by_role('button', name='Проверить распознавание').click()
                page.get_by_text('Распознано: 1 · На проверку: 1', exact=True).wait_for()
                page.get_by_role('button', name='Подключение Telegram', exact=True).click()
                page.get_by_text('API: не настроен', exact=False).wait_for()
                page.get_by_role('button', name='Каналы-источники').click()
                page.on('dialog', lambda d: d.accept())
                page.get_by_role('button', name='Удалить', exact=True).click()
                page.locator('#sources-table').get_by_text('Источников пока нет', exact=True).wait_for()
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_role('button', name='Обзор', exact=True).click()
                page.screenshot(path=str(output / 'mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Горизонтальное переполнение'
                assert not errors, errors
                browser.close()
                print('UI OK: добавление, изменение, отключение и удаление канала; сохранение настроек; парсер; мобильная вёрстка. Ошибок JavaScript нет.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
