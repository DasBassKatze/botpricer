'use strict';

const $ = (query) => document.querySelector(query);
let state = {sources: [], catalog: [], publications: [], runs: [], reviews: [], busy: false};
let settings = {};
let toastTimer;
const labels = {overview: 'Обзор', sources: 'Каналы-источники', catalog: 'Товары и цены', settings: 'Настройки', connection: 'Подключение Telegram', parser: 'Проверка прайса', history: 'История обновлений', help: 'Помощь и ответы'};
const runLabels = {success: 'Проверка завершена', partial: 'Нужно внимание', failed: 'Проверка не завершена', running: 'Проверяем цены', interrupted: 'Проверка прервана'};

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  children.flat().forEach(child => {
    if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  });
  return node;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').className = error ? 'error' : '';
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 11000 : 6000);
}

async function api(path, method = 'GET', body) {
  let response;
  try {
    response = await fetch('/api' + path, {method, headers: body !== undefined ? {'Content-Type': 'application/json'} : {}, body: body === undefined ? undefined : JSON.stringify(body)});
  } catch {
    throw new Error('Не удалось связаться с программой. Проверьте, что окно start.bat открыто, затем обновите эту страницу.');
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Действие не выполнено. Обновите страницу и попробуйте ещё раз.');
  }
  return response.json();
}

function bind(selector, event, callback) {
  $(selector).addEventListener(event, async ev => {
    if (event === 'submit') ev.preventDefault();
    const button = event === 'submit' ? ev.target.querySelector('[type="submit"]') : ev.currentTarget;
    if (button) button.disabled = true;
    try { await callback(ev); }
    catch (error) { toast(error.message, true); }
    finally { if (button) button.disabled = false; }
  });
}

function button(text, callback, danger = false) {
  return h('button', {class: 'row-button' + (danger ? ' danger' : ''), type: 'button', onclick: async ev => {
    ev.currentTarget.disabled = true;
    try { await callback(); } catch (error) { toast(error.message, true); }
    finally { ev.target.disabled = false; }
  }}, text);
}

function empty(title, description = '') {
  return h('div', {class: 'empty'}, h('span', {class: 'empty-icon'}, '⌁'), h('strong', {}, title), h('p', {}, description));
}

function badge(text, kind = '') { return h('span', {class: 'badge ' + kind}, text); }
function money(value) { return (value / 100).toLocaleString('ru-RU', {maximumFractionDigits: 2}) + ' ₽'; }
function date(value) { return value ? new Date(value).toLocaleString('ru-RU') : 'Ещё не проверялся'; }
function setText(selector, value) { $(selector).textContent = value; }
function table(headers, rows) {
  return h('div', {class: 'table-wrap'}, h('table', {}, h('thead', {}, h('tr', {}, headers.map(text => h('th', {}, text)))), h('tbody', {}, rows.map(cells => h('tr', {}, cells.map(cell => h('td', {}, cell)))))));
}

function go(page) {
  document.querySelectorAll('.page').forEach(node => { node.hidden = node.id !== 'page-' + page; });
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.page === page));
  setText('#breadcrumb', labels[page]);
  if (page === 'connection') connection().catch(error => toast(error.message, true));
  window.scrollTo({top: 0});
}
document.querySelectorAll('[data-page]').forEach(node => node.addEventListener('click', () => go(node.dataset.page)));
document.querySelectorAll('[data-go]').forEach(node => node.addEventListener('click', () => go(node.dataset.go)));

function sourceTable(compact = false) {
  if (!state.sources.length) return empty('Поставщики пока не добавлены', 'Откройте «Каналы-источники», укажите название поставщика и ссылку на его Telegram-канал, затем сохраните.');
  return table(compact ? ['Поставщик', 'Состояние', 'Когда проверяли'] : ['Поставщик', 'Как публикует цены', 'Доверять цене', 'Состояние', 'Действия'], state.sources.map(source => {
    const title = h('div', {}, h('strong', {}, source.title), h('small', {}, source.address));
    const status = badge(source.enabled ? (source.status === 'Не проверялся' ? 'Ещё не проверяли' : source.status) : 'Отключён', !source.enabled ? '' : source.status.startsWith('ОК') ? 'green' : source.last_checked ? 'orange' : '');
    if (compact) return [title, status, date(source.last_checked)];
    return [title, source.mode === 'snapshot' ? 'Весь список одним сообщением' : 'Товары в разных сообщениях', source.ttl_hours + ' ч', status,
      h('div', {class: 'actions'}, button('Изменить', () => editSource(source)), button(source.enabled ? 'Отключить' : 'Включить', async () => {
        await api('/sources/' + source.id, 'PUT', {...source, enabled: !source.enabled}); await refresh();
      }), button('Проверить', async () => { const result = await api('/sources/' + source.id + '/check', 'POST', {}); toast('Канал «' + result.title + '» доступен. Теперь запустите проверку цен на странице «Обзор».'); }), button('Удалить', async () => {
        if (!confirm('Удалить «' + source.title + '» из списка поставщиков? Его цены больше не будут использоваться. Сам Telegram-канал останется без изменений.')) return;
        await api('/sources/' + source.id, 'DELETE'); await refresh(); toast('Канал удалён из списка поставщиков');
      }, true))];
  }));
}

function editSource(source) {
  $('#source-id').value = source.id;
  $('#source-title').value = source.title;
  $('#source-address').value = source.address;
  $('#source-mode').value = source.mode;
  $('#source-ttl').value = source.ttl_hours;
  setText('#source-form-title', 'Изменить канал');
  $('#source-cancel').hidden = false;
  $('#source-title').focus();
}

function resetSource() {
  $('#source-form').reset();
  $('#source-id').value = '';
  setText('#source-form-title', 'Добавить канал');
  $('#source-cancel').hidden = true;
}

function renderCatalog() {
  const query = $('#search').value.toLowerCase();
  const items = state.catalog.filter(item => item.title.toLowerCase().includes(query));
  $('#catalog-table').replaceChildren(items.length ? table(['Товар', 'Цена поставщика', 'Цена для покупателей', 'Где дешевле', 'Когда обновлён прайс'], items.map(item => [
    h('div', {}, h('strong', {}, item.title), h('small', {}, 'Поставщиков с этим товаром: ' + item.supplier_count)),
    h('span', {class: 'price'}, money(item.cost_kopeks)), h('strong', {class: 'price'}, money(item.retail_kopeks)), item.source_title, date(item.changed_at)
  ])) : empty(query ? 'Ничего не найдено' : 'Каталог пока пуст', query ? 'Попробуйте другой запрос.' : 'Подключите Telegram, добавьте канал поставщика и запустите проверку цен на странице «Обзор». Если проверка уже была, посмотрите её результат в «Истории обновлений».'));
}

function renderReviews() {
  const publications = state.publications.filter(p => p.status !== 'ready');
  $('#publication-reviews').replaceChildren(...(publications.length ? publications.map(p => {
    const actions = [];
    if (p.status === 'review') actions.push(button('Подтвердить цену ' + money(p.blocked_price), async () => {
      await api('/publications/' + p.id + '/resolve', 'POST', {action: 'approve'}); toast('Новая цена подтверждена. Запустите проверку цен на странице «Обзор», чтобы обновить объявление.'); await refresh();
    }));
    if (['uncertain', 'failed'].includes(p.status)) {
      actions.push(button('Указать уже отправленный пост', async () => {
        const value = prompt('Откройте объявление этого товара в своём канале и скопируйте ссылку на сообщение. Введите последнее число из ссылки: например, для https://t.me/my_channel/125 введите 125.');
        if (!value) return;
        if (!/^\d+$/.test(value) || Number(value) < 1) throw new Error('Введите только последнее число из ссылки на сообщение, например 125.');
        await api('/publications/' + p.id + '/resolve', 'POST', {action: 'link', message_id: Number(value)}); await refresh();
      }));
      actions.push(button('Попробовать снова при следующей проверке', async () => {
        if (!confirm('Сначала откройте свой канал и проверьте, появилось ли объявление. Если оно уже есть, нажмите «Указать уже отправленный пост», чтобы избежать дубля. Разрешить повторную попытку при следующей проверке цен?')) return;
        await api('/publications/' + p.id + '/resolve', 'POST', {action: 'retry'}); await refresh();
      }));
    }
    return h('div', {class: 'review-item'}, h('strong', {}, p.title), h('p', {class: 'error-line'}, p.error || 'Программа отправляет объявление. Дождитесь завершения проверки.'), p.status === 'uncertain' ? h('p', {}, 'Связь оборвалась, поэтому результат отправки неизвестен. Проверьте свой канал: если пост уже появился, укажите его номер кнопкой ниже. Если поста нет, разрешите повторную попытку.') : null, h('small', {}, 'Канал: ' + p.target), h('div', {class: 'form-actions'}, actions));
  }) : [empty('С объявлениями всё в порядке', 'Если программа попросит подтвердить цену или проверить отправку, здесь появятся инструкции.')]));
  $('#parse-reviews').replaceChildren(...(state.reviews.length ? state.reviews.map(row => h('div', {class: 'review-item'}, h('strong', {}, row.source_title + ' · сообщение ' + row.message_id), ...row.rejected.map(item => h('div', {}, h('pre', {}, item.text), h('p', {}, 'Строка ' + item.line + ': ' + item.reason))))) : [empty('Строк для проверки пока нет', 'После чтения прайсов здесь появятся строки, из которых не удалось получить товар и цену.')]));
}

function runNode(run) {
  const details = run.details;
  const kind = run.status === 'success' ? 'green' : run.status === 'failed' ? 'red' : 'orange';
  return h('div', {class: 'run-item'}, h('div', {class: 'run-head'}, h('strong', {}, date(run.started_at)), badge(runLabels[run.status] || run.status, kind)), h('div', {class: 'run-metrics'},
    h('span', {}, details.trigger === 'schedule' ? 'По расписанию' : 'Вручную'), h('span', {}, 'Поставщиков проверено: ' + (details.sources_ok || 0)), h('span', {}, 'Не удалось проверить: ' + (details.sources_failed || 0)), h('span', {}, 'Товаров: ' + (details.offers || 0)), h('span', {}, 'Новых объявлений: ' + (details.created || 0)), h('span', {}, 'Объявлений обновлено: ' + (details.updated || 0))),
    ...(details.errors || []).map(error => h('p', {class: 'error-line'}, error)));
}

function render() {
  setText('#source-count', state.sources.length);
  setText('#stat-sources', state.sources.length);
  setText('#stat-enabled', state.sources.filter(s => s.enabled).length + ' активных');
  setText('#stat-products', state.catalog.length);
  setText('#stat-posts', state.publications.filter(p => p.message_id).length);
  setText('#stat-review', state.reviews.reduce((count, r) => count + r.rejected.length, 0) + state.publications.filter(p => p.status !== 'ready').length);
  $('#sync').disabled = state.busy;
  setText('#sync', state.busy ? '↻ Проверяем цены…' : state.auto_publish ? '↻ Проверить и опубликовать' : '↻ Проверить цены сейчас');
  setText('#sync-hint', state.auto_publish ? 'Публикация включена: эта проверка сможет отправить новые объявления и изменить цены в вашем канале.' : 'Публикация выключена: проверка только соберёт цены. Объявления в вашем канале не появятся.');
  $('#overview-sources').replaceChildren(sourceTable(true));
  $('#sources-table').replaceChildren(sourceTable());
  renderCatalog(); renderReviews();
  $('#runs-list').replaceChildren(...(state.runs.length ? state.runs.map(runNode) : [empty('История пока пуста', 'После первой проверки здесь будет видно, какие каналы прочитаны и какие объявления созданы или обновлены.')]));
  if (state.runs.length) {
    const run = state.runs[0];
    $('#last-run').className = 'last-summary';
    $('#last-run').replaceChildren(runNode(run));
    setText('#run-badge', runLabels[run.status] || run.status);
    $('#run-badge').className = 'badge ' + (run.status === 'success' ? 'green' : 'orange');
  }
  setText('#publish-label', state.auto_publish ? 'Включена' : 'Выключена');
}

async function refresh() {
  state = await api('/state');
  render();
}

async function connection() {
  const info = await api('/connection');
  $('#connection-state').replaceChildren(
    h('div', {}, h('strong', {}, 'Данные для чтения каналов'), h('p', {}, info.has_api_credentials ? 'API ID и API hash сохранены. Повторно вводить их не нужно.' : 'Ещё не заполнены. Получите API ID и API hash по инструкции шага 1.')),
    h('div', {}, h('strong', {}, 'Вход в ваш аккаунт'), h('p', {}, info.authorized ? 'Вход сохранён. Чтобы проверить, что соединение работает сейчас, нажмите «Проверить подключение» в шаге 2.' : 'Вход ещё не выполнен. После сохранения данных перейдите к шагу 2 и запросите код.')),
    h('div', {}, h('strong', {}, 'Бот для объявлений'), h('p', {}, info.has_bot_token ? 'Токен сохранён. В настройках укажите свой канал и проверьте права бота.' : 'Токен ещё не сохранён. Создайте бота через BotFather и вставьте его токен в шаге 1.'))
  );
  $('#setup-banner').hidden = info.authorized && info.has_bot_token;
}

function renderSettings() {
  const form = $('#settings-form');
  for (const [key, value] of Object.entries(settings)) {
    const input = form.elements.namedItem(key);
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = value;
    else input.value = value;
  }
  setText('#schedule-time', settings.daily_time);
  setText('#schedule-zone', 'По времени: ' + (settings.timezone === 'Europe/Moscow' ? 'Москва' : settings.timezone));
  setText('#target-label', settings.target_channel || 'Не указан');
  setText('#automation-badge', settings.schedule_enabled ? 'Включена' : 'Выключена');
  $('#automation-badge').className = 'badge ' + (settings.schedule_enabled ? 'green' : '');
  priceExample();
}

function priceExample() {
  const form = $('#settings-form');
  const get = name => form.elements.namedItem(name).value;
  const cost = 60500;
  const percent = cost * Number(get('markup_percent')) / 100;
  const mode = get('markup_mode');
  const markup = mode === 'fixed' ? Number(get('markup_fixed')) : mode === 'percent' ? percent : Math.max(percent, Number(get('minimum_markup')));
  const step = Number(get('round_to')) || 1;
  const retail = Math.ceil((cost + markup + Number(get('delivery'))) / step) * step;
  setText('#price-example', 'закупка 60 500 ₽ → цена для покупателей ' + money(retail * 100));
  ['markup_fixed', 'markup_percent', 'minimum_markup'].forEach(name => {
    form.elements.namedItem(name).disabled = (name === 'markup_fixed' && mode !== 'fixed') || (name === 'markup_percent' && mode === 'fixed') || (name === 'minimum_markup' && mode !== 'combined');
  });
}

bind('#source-form', 'submit', async () => {
  const id = $('#source-id').value;
  const previous = state.sources.find(s => String(s.id) === id);
  const payload = {title: $('#source-title').value.trim(), address: $('#source-address').value.trim(), mode: $('#source-mode').value, ttl_hours: Number($('#source-ttl').value), enabled: previous ? Boolean(previous.enabled) : true};
  await api('/sources' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', payload);
  resetSource(); await refresh(); toast('Поставщик сохранён. Нажмите «Проверить» в его строке, затем запустите проверку цен на странице «Обзор».');
});
$('#source-cancel').addEventListener('click', resetSource);
$('#search').addEventListener('input', renderCatalog);
$('#settings-form').addEventListener('input', () => { priceExample(); setText('#settings-state', 'Есть несохранённые изменения'); });
bind('#settings-form', 'submit', async () => {
  const form = $('#settings-form');
  const payload = {};
  for (const key of Object.keys(settings)) {
    const input = form.elements.namedItem(key);
    payload[key] = input.type === 'checkbox' ? input.checked : input.value;
  }
  await api('/settings', 'PUT', payload);
  settings = await api('/settings'); renderSettings(); await refresh();
  setText('#settings-state', 'Все изменения сохранены'); toast('Настройки сохранены');
});
bind('#sync', 'click', async () => { await api('/sync', 'POST', {}); await refresh(); toast(state.auto_publish ? 'Проверка запущена. Программа соберёт цены и обновит объявления в вашем канале.' : 'Проверка запущена. Результат появится в «Товарах и ценах». Объявления отправляться не будут.'); });
bind('#pause', 'click', async () => {
  await api('/pause', 'POST', {}); settings.auto_publish = false;
  $('#settings-form').elements.namedItem('auto_publish').checked = false;
  await refresh(); toast('Публикация выключена. Цены можно проверять дальше. Уже начатая отправка может завершиться.');
});
bind('#check-target', 'click', async () => { const result = await api('/target/check', 'POST', {}); toast('Всё готово: бот @' + result.bot + ' может отправлять объявления в канал «' + result.channel + '». Проверка ничего не опубликовала.'); });
bind('#credentials-form', 'submit', async ev => {
  const form = ev.target;
  await api('/credentials', 'PUT', {api_id: form.api_id.value ? Number(form.api_id.value) : null, api_hash: form.api_hash.value.trim(), bot_token: form.bot_token.value.trim()});
  form.reset(); await connection(); toast('Данные подключения сохранены. Теперь в шаге 2 войдите в аккаунт Telegram.');
});
bind('#phone-form', 'submit', async ev => {
  const result = await api('/login/code', 'POST', {phone: ev.target.phone.value.trim()});
  if (result.authorized) { await connection(); toast('Вход уже выполнен. Можно добавлять каналы поставщиков.'); return; }
  $('#login-form').hidden = false;
  toast('Telegram отправил код входа. Найдите его в сообщениях Telegram и введите в появившееся поле.');
});
bind('#login-form', 'submit', async ev => {
  const form = ev.target;
  const result = await api('/login/confirm', 'POST', {code: form.code.value.trim(), password: form.password.value});
  form.password.value = '';
  if (result.password_required) { $('#password-label').hidden = false; form.password.focus(); toast('Telegram просит облачный пароль — пароль двухэтапной проверки, который вы установили в Telegram. Введите его в появившееся поле.'); return; }
  form.reset(); form.hidden = true; $('#password-label').hidden = true;
  $('#phone-form').reset(); await connection(); toast('Вы вошли в Telegram. Теперь откройте «Каналы-источники» и добавьте поставщиков.');
});
bind('#check-reader', 'click', async () => { const result = await api('/connection/check', 'POST', {}); await connection(); toast(result.authorized ? 'Соединение работает. Аккаунт готов читать добавленные каналы.' : 'Вход не выполнен. Укажите номер телефона и запросите код в шаге 2.'); });
bind('#logout', 'click', async () => {
  if (!confirm('Завершить вход в Telegram для этой программы? Прайсы перестанут обновляться, пока вы снова не войдёте по коду. Сохранённые товары и настройки останутся.')) return;
  await api('/logout', 'POST', {}); await connection(); toast('Вход завершён. Чтобы снова читать прайсы, запросите новый код Telegram.');
});
bind('#parser-form', 'submit', async ev => {
  const result = await api('/parse', 'POST', {text: ev.target.text.value});
  $('#parser-result').replaceChildren(h('article', {class: 'panel'}, h('div', {class: 'panel-heading'}, h('h2', {}, 'Товаров с ценой: ' + result.items.length + ' · Строк для проверки: ' + result.rejected.length)),
    result.items.length ? table(['Название', 'Цена', 'Наличие'], result.items.map(item => [item.title, money(item.price_kopeks), item.available ? 'В наличии' : 'Нет в наличии или только под заказ'])) : empty('Пока не удалось прочитать цены', 'Посмотрите причины ниже. Попробуйте строку: iPhone 15 128 Black US — 61 000 ₽.'),
    ...result.rejected.map(item => h('div', {class: 'review-item'}, h('pre', {}, item.text), h('p', {}, 'Строка ' + item.line + ': ' + item.reason)))));
});
bind('#preview-posts', 'click', async () => {
  const posts = await api('/preview');
  $('#preview-content').replaceChildren(...(posts.length ? posts.map(post => h('pre', {}, post.text)) : [empty('Предложений пока нет', 'Запустите проверку цен на странице «Обзор», затем снова откройте этот просмотр.')]));
  $('#preview-dialog').showModal();
});
$('#close-preview').addEventListener('click', () => $('#preview-dialog').close());

const tourKey = 'botpricer.onboarding.v1';
const tourSteps = [
  {page: 'overview', title: 'Добро пожаловать! Давайте разберёмся за несколько шагов',
    description: 'BotPricer собирает цены поставщиков, прибавляет вашу наценку и обновляет объявления в вашем Telegram-канале. Здесь мы покажем, где всё настраивается.',
    tip: 'Можно пропустить обучение сейчас и вернуться к нему через «Помощь и ответы». Продолжая, вы только смотрите подсказки: настройки не меняются.'},
  {page: 'connection', title: '1. Подключите аккаунт и бота',
    description: 'Ваш Telegram-аккаунт нужен для чтения каналов поставщиков. Отдельный бот нужен для отправки объявлений в ваш канал. На этой странице есть ссылки, объяснения полей и порядок входа.',
    tip: 'Сначала сохраните API ID, API hash и токен бота. Затем запросите код входа по своему номеру телефона. Данные нужно вводить на странице подключения после обучения.'},
  {page: 'sources', title: '2. Добавьте каналы поставщиков',
    description: 'Укажите удобное название поставщика и ссылку на его канал. Выберите, публикует ли он товары отдельными сообщениями или каждый раз присылает весь список одним сообщением.',
    tip: '«Сколько часов доверять цене» — срок с публикации или правки прайса поставщиком. Кнопка «Проверить» проверяет доступ к каналу; сбор цен запускается отдельно на «Обзоре».'},
  {page: 'parser', title: '3. Убедитесь, что программа понимает прайс',
    description: 'Скопируйте одно настоящее сообщение поставщика в поле проверки. Программа покажет найденные товары и цены, а для непонятных строк объяснит причину.',
    tip: 'Пример: iPhone 15 128 Black US — 61 000 ₽. Полное название и цена должны быть на одной строке. Этот тест ничего не публикует и не добавляет в каталог.'},
  {page: 'settings', title: '4. Задайте наценку и свой канал',
    description: 'Выберите сумму или процент наценки, укажите канал для покупателей и контакт для заказа. Добавьте своего бота администратором этого канала, сохраните настройки и проверьте права бота.',
    tip: 'Для первой проверки оставьте отправку объявлений выключенной. Пример под полями покажет, как из закупочной цены получится ваша цена продажи.'},
  {page: 'catalog', title: '5. Проверьте результат до отправки',
    description: 'На странице «Обзор» запустите проверку цен. Затем вернитесь в «Товары и цены»: здесь появятся закупочные цены, цены продажи и кнопка «Посмотреть будущие объявления».',
    tip: 'Когда результат вас устраивает, включите публикацию и ежедневное расписание в настройках. Программа должна оставаться запущенной. Сейчас перейдём к первому реальному действию — подключению Telegram.'},
];
let tourStep = 0;

function readTourProgress() {
  try {
    const value = JSON.parse(localStorage.getItem(tourKey) || 'null');
    return value && typeof value === 'object' ? value : null;
  } catch { return null; }
}

function saveTourProgress(status) {
  try { localStorage.setItem(tourKey, JSON.stringify({status, step: tourStep})); }
  catch { }
}

function showTour(step) {
  tourStep = Math.max(0, Math.min(tourSteps.length - 1, Number.isInteger(step) ? step : 0));
  const current = tourSteps[tourStep];
  go(current.page);
  setText('#tour-progress', 'Шаг ' + (tourStep + 1) + ' из ' + tourSteps.length);
  setText('#tour-title', current.title);
  setText('#tour-description', current.description);
  setText('#tour-tip', current.tip);
  $('#tour-back').disabled = tourStep === 0;
  setText('#tour-next', tourStep === tourSteps.length - 1 ? 'Начать настройку Telegram' : 'Далее');
  saveTourProgress('in_progress');
  if (!$('#tour-dialog').open) $('#tour-dialog').showModal();
  $('#tour-next').focus();
}

function finishTour(status) {
  saveTourProgress(status);
  $('#tour-dialog').close();
  if (status === 'completed') go('connection');
}

$('#tour-next').addEventListener('click', () => tourStep === tourSteps.length - 1 ? finishTour('completed') : showTour(tourStep + 1));
$('#tour-back').addEventListener('click', () => showTour(tourStep - 1));
$('#tour-skip').addEventListener('click', () => finishTour('skipped'));
$('#tour-dialog').addEventListener('cancel', event => { event.preventDefault(); finishTour('skipped'); });
$('#restart-tour').addEventListener('click', () => showTour(0));

async function init() {
  try {
    settings = await api('/settings'); renderSettings();
    await refresh(); await connection();
  } catch (error) { toast(error.message, true); }
  const progress = readTourProgress();
  if (!progress || !['completed', 'skipped'].includes(progress.status)) showTour(progress?.step);
  setInterval(async () => { try { await refresh(); } catch (error) { toast(error.message, true); } }, 5000);
}
init();
