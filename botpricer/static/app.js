'use strict';

const $ = (query) => document.querySelector(query);
let state = {sources: [], catalog: [], publications: [], runs: [], reviews: [], busy: false};
let settings = {};
let toastTimer;
const labels = {overview: 'Обзор', sources: 'Каналы-источники', catalog: 'Товары и цены', settings: 'Настройки', connection: 'Подключение Telegram', parser: 'Проверка прайса', history: 'История обновлений'};
const runLabels = {success: 'Готово', partial: 'Есть ошибки', failed: 'Ошибка', running: 'Выполняется', interrupted: 'Прервано'};

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
    throw new Error('Панель не отвечает. Проверьте, запущена ли программа.');
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Не удалось выполнить действие');
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
  if (!state.sources.length) return empty('Источников пока нет', 'Добавьте первый канал поставщика в разделе «Каналы-источники».');
  return table(compact ? ['Источник', 'Статус', 'Последняя проверка'] : ['Источник', 'Формат', 'Актуальность', 'Статус', 'Действия'], state.sources.map(source => {
    const title = h('div', {}, h('strong', {}, source.title), h('small', {}, source.address));
    const status = badge(source.enabled ? source.status : 'Отключён', !source.enabled ? '' : source.status.startsWith('ОК') ? 'green' : source.last_checked ? 'orange' : '');
    if (compact) return [title, status, date(source.last_checked)];
    return [title, source.mode === 'snapshot' ? 'Полный прайс' : 'Изменения', source.ttl_hours + ' ч', status,
      h('div', {class: 'actions'}, button('Изменить', () => editSource(source)), button(source.enabled ? 'Отключить' : 'Включить', async () => {
        await api('/sources/' + source.id, 'PUT', {...source, enabled: !source.enabled}); await refresh();
      }), button('Проверить', async () => { const result = await api('/sources/' + source.id + '/check', 'POST', {}); toast('Канал доступен: ' + result.title); }), button('Удалить', async () => {
        if (!confirm('Удалить источник «' + source.title + '»? Его цены перестанут участвовать в расчёте.')) return;
        await api('/sources/' + source.id, 'DELETE'); await refresh(); toast('Источник удалён');
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
  $('#catalog-table').replaceChildren(items.length ? table(['Товар', 'Закупка', 'Ваша цена', 'Поставщик', 'Дата прайса'], items.map(item => [
    h('div', {}, h('strong', {}, item.title), h('small', {}, 'Предложений: ' + item.supplier_count)),
    h('span', {class: 'price'}, money(item.cost_kopeks)), h('strong', {class: 'price'}, money(item.retail_kopeks)), item.source_title, date(item.changed_at)
  ])) : empty(query ? 'Ничего не найдено' : 'Каталог пока пуст', query ? 'Попробуйте другой запрос.' : 'Подключите Telegram, добавьте канал и запустите обновление.'));
}

function renderReviews() {
  const publications = state.publications.filter(p => p.status !== 'ready');
  $('#publication-reviews').replaceChildren(...(publications.length ? publications.map(p => {
    const actions = [];
    if (p.status === 'review') actions.push(button('Разрешить цену ' + money(p.blocked_price), async () => {
      await api('/publications/' + p.id + '/resolve', 'POST', {action: 'approve'}); toast('Цена разрешена для следующего обновления'); await refresh();
    }));
    if (['uncertain', 'failed'].includes(p.status)) {
      actions.push(button('Указать ID поста', async () => {
        const value = prompt('Введите ID существующего поста (последнее число в его ссылке). Проверьте, что это именно этот товар.');
        if (!value) return;
        if (!/^\d+$/.test(value) || Number(value) < 1) throw new Error('Нужен положительный числовой ID');
        await api('/publications/' + p.id + '/resolve', 'POST', {action: 'link', message_id: Number(value)}); await refresh();
      }));
      actions.push(button('Разрешить повтор', async () => {
        if (!confirm('Проверьте канал. Если сообщение уже отправлено, укажите его ID вместо повтора. Разрешить следующую попытку?')) return;
        await api('/publications/' + p.id + '/resolve', 'POST', {action: 'retry'}); await refresh();
      }));
    }
    return h('div', {class: 'review-item'}, h('strong', {}, p.title), h('p', {class: 'error-line'}, p.error || 'Отправка выполняется'), h('small', {}, 'Канал: ' + p.target), h('div', {class: 'form-actions'}, actions));
  }) : [empty('Публикации не требуют проверки')]));
  $('#parse-reviews').replaceChildren(...(state.reviews.length ? state.reviews.map(row => h('div', {class: 'review-item'}, h('strong', {}, row.source_title + ' · сообщение ' + row.message_id), ...row.rejected.map(item => h('div', {}, h('pre', {}, item.text), h('p', {}, 'Строка ' + item.line + ': ' + item.reason))))) : [empty('Нераспознанных строк нет')]));
}

function runNode(run) {
  const details = run.details;
  const kind = run.status === 'success' ? 'green' : run.status === 'failed' ? 'red' : 'orange';
  return h('div', {class: 'run-item'}, h('div', {class: 'run-head'}, h('strong', {}, date(run.started_at)), badge(runLabels[run.status] || run.status, kind)), h('div', {class: 'run-metrics'},
    h('span', {}, details.trigger === 'schedule' ? 'По расписанию' : 'Вручную'), h('span', {}, 'Источников: ' + (details.sources_ok || 0)), h('span', {}, 'Товаров: ' + (details.offers || 0)), h('span', {}, 'Новых постов: ' + (details.created || 0)), h('span', {}, 'Обновлено: ' + (details.updated || 0))),
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
  setText('#sync', state.busy ? '↻ Обновление…' : '↻ Обновить сейчас');
  $('#overview-sources').replaceChildren(sourceTable(true));
  $('#sources-table').replaceChildren(sourceTable());
  renderCatalog(); renderReviews();
  $('#runs-list').replaceChildren(...(state.runs.length ? state.runs.map(runNode) : [empty('История пока пуста', 'Здесь появятся результаты проверок.')]));
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
  setText('#connection-state', 'API: ' + (info.has_api_credentials ? 'сохранены' : 'не настроен') + ' · Аккаунт: ' + (info.authorized ? 'подключён (последний известный статус)' : 'вход не выполнен') + ' · Токен бота: ' + (info.has_bot_token ? 'сохранён' : 'не задан'));
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
  setText('#schedule-zone', 'Каждый день · ' + settings.timezone);
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
  setText('#price-example', 'закупка 60 500 ₽ → ' + money(retail * 100));
  ['markup_fixed', 'markup_percent', 'minimum_markup'].forEach(name => {
    form.elements.namedItem(name).disabled = (name === 'markup_fixed' && mode !== 'fixed') || (name === 'markup_percent' && mode === 'fixed') || (name === 'minimum_markup' && mode !== 'combined');
  });
}

bind('#source-form', 'submit', async () => {
  const id = $('#source-id').value;
  const previous = state.sources.find(s => String(s.id) === id);
  const payload = {title: $('#source-title').value.trim(), address: $('#source-address').value.trim(), mode: $('#source-mode').value, ttl_hours: Number($('#source-ttl').value), enabled: previous ? Boolean(previous.enabled) : true};
  await api('/sources' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', payload);
  resetSource(); await refresh(); toast('Канал сохранён');
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
bind('#sync', 'click', async () => { await api('/sync', 'POST', {}); await refresh(); toast('Проверка источников запущена'); });
bind('#pause', 'click', async () => {
  await api('/pause', 'POST', {}); settings.auto_publish = false;
  $('#settings-form').elements.namedItem('auto_publish').checked = false;
  await refresh(); toast('Автопубликация приостановлена');
});
bind('#check-target', 'click', async () => { const result = await api('/target/check', 'POST', {}); toast('@' + result.bot + ' может публиковать в «' + result.channel + '»'); });
bind('#credentials-form', 'submit', async ev => {
  const form = ev.target;
  await api('/credentials', 'PUT', {api_id: form.api_id.value ? Number(form.api_id.value) : null, api_hash: form.api_hash.value.trim(), bot_token: form.bot_token.value.trim()});
  form.reset(); await connection(); toast('Доступы сохранены');
});
bind('#phone-form', 'submit', async ev => {
  const result = await api('/login/code', 'POST', {phone: ev.target.phone.value.trim()});
  if (result.authorized) { await connection(); toast('Аккаунт уже подключён'); return; }
  $('#login-form').hidden = false;
  toast('Код отправлен в Telegram');
});
bind('#login-form', 'submit', async ev => {
  const form = ev.target;
  const result = await api('/login/confirm', 'POST', {code: form.code.value.trim(), password: form.password.value});
  form.password.value = '';
  if (result.password_required) { $('#password-label').hidden = false; form.password.focus(); toast('Введите пароль двухэтапной проверки'); return; }
  form.reset(); form.hidden = true; $('#password-label').hidden = true;
  $('#phone-form').reset(); await connection(); toast('Аккаунт Telegram подключён');
});
bind('#check-reader', 'click', async () => { const result = await api('/connection/check', 'POST', {}); await connection(); toast(result.authorized ? 'Аккаунт подключён' : 'Необходимо выполнить вход'); });
bind('#logout', 'click', async () => {
  if (!confirm('Отключить аккаунт? Для чтения каналов потребуется войти заново.')) return;
  await api('/logout', 'POST', {}); await connection(); toast('Аккаунт отключён');
});
bind('#parser-form', 'submit', async ev => {
  const result = await api('/parse', 'POST', {text: ev.target.text.value});
  $('#parser-result').replaceChildren(h('article', {class: 'panel'}, h('div', {class: 'panel-heading'}, h('h2', {}, 'Распознано: ' + result.items.length + ' · На проверку: ' + result.rejected.length)),
    result.items.length ? table(['Название', 'Цена', 'Наличие'], result.items.map(item => [item.title, money(item.price_kopeks), item.available ? 'В наличии' : 'Недоступен / под заказ'])) : empty('Товары не распознаны'),
    ...result.rejected.map(item => h('div', {class: 'review-item'}, h('pre', {}, item.text), h('p', {}, 'Строка ' + item.line + ': ' + item.reason)))));
});
bind('#preview-posts', 'click', async () => {
  const posts = await api('/preview');
  $('#preview-content').replaceChildren(...(posts.length ? posts.map(post => h('pre', {}, post.text)) : [empty('Предложений пока нет', 'Сначала выполните проверку источников.')]));
  $('#preview-dialog').showModal();
});
$('#close-preview').addEventListener('click', () => $('#preview-dialog').close());

async function init() {
  try {
    settings = await api('/settings'); renderSettings();
    await refresh(); await connection();
  } catch (error) { toast(error.message, true); }
  setInterval(async () => { try { await refresh(); } catch (error) { toast(error.message, true); } }, 5000);
}
init();
