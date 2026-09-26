/* nanoDesk front end — plain JavaScript, no build step, no framework.
   The server does the real work; this file turns it into cards and buttons.

   One rule above every other rule in this file: nothing that came from a folder
   name or a README is ever given to the page as HTML. Every piece of text is
   set with textContent, so a README containing a <script> tag stays a README
   containing a <script> tag, and a folder called "..\\..\\etc" is just a
   strange-looking folder name. */

'use strict';

const $ = (id) => document.getElementById(id);

const api = {
  async get(url) {
    const response = await fetch(url);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  },
  async post(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  },
  async del(url, body) {
    const response = await fetch(url, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  },
};

/* Server-sent events over fetch, so the card can be filled in as it happens. */
async function stream(url, body, onEvent) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      for (const line of raw.split('\n')) {
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') return;
        try {
          onEvent(JSON.parse(payload));
        } catch (err) {
          /* a half-arrived line; the next chunk will finish it */
        }
      }
    }
  }
}

// ---------------------------------------------------------------- tiny helpers
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function toast(message, kind) {
  const node = el('div', 'toast' + (kind ? ' ' + kind : ''), message);
  $('toasts').appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .3s';
    setTimeout(() => node.remove(), 300);
  }, kind === 'bad' ? 10000 : 5000);
}

function timeAgo(seconds) {
  const value = Number(seconds) || 0;
  if (value < 60) return `${Math.max(1, Math.round(value))} seconds`;
  if (value < 3600) return `${Math.round(value / 60)} minute${Math.round(value / 60) === 1 ? '' : 's'}`;
  return `${Math.round(value / 3600)} hour${Math.round(value / 3600) === 1 ? '' : 's'}`;
}

function note(message, kind) {
  const box = $('folderNote');
  box.className = 'note' + (kind ? ' ' + kind : '');
  box.textContent = message || '';
  box.hidden = !message;
}

// --------------------------------------------------------------------- state
let STATE = null;

async function refresh() {
  STATE = await api.get('/api/state');
  render();
}

function render() {
  renderApps();
  renderFolders();
  renderComputer();
}

// ---------------------------------------------------------------------- apps
function renderApps() {
  const apps = (STATE && STATE.apps) || [];
  const grid = $('appGrid');
  clear(grid);
  apps.forEach((app) => grid.appendChild(cardFor(app)));

  const count = $('appsCount');
  if (!apps.length) {
    count.textContent = '';
  } else {
    const ready = apps.filter((app) => !app.problems.length).length;
    count.textContent = `${apps.length} found · ${ready} ready to start`;
  }

  const empty = $('emptyState');
  empty.hidden = apps.length > 0;
  clear(empty);
  if (!apps.length) renderEmpty(empty);
}

function cardFor(app) {
  const card = el('article', 'card' + (app.running ? ' running' : ''));

  const head = el('div', 'card-head');
  head.appendChild(el('div', 'card-emoji', app.emoji || '🧩'));
  const titles = el('div', 'card-titles');
  titles.appendChild(el('div', 'card-name', app.name));
  const where = el('div', 'card-where', app.path);
  where.title = app.path;
  titles.appendChild(where);
  head.appendChild(titles);
  card.appendChild(head);

  card.appendChild(el('p', 'card-blurb', app.blurb || ''));

  (app.notes || []).forEach((line) => card.appendChild(el('p', 'card-note', line)));

  if ((app.problems || []).length) {
    const warning = el('div', 'warning');
    warning.appendChild(el('div', 'warning-title', 'This one cannot be started'));
    app.problems.forEach((problem) => warning.appendChild(el('p', null, problem)));
    card.appendChild(warning);
  }

  const foot = el('div', 'card-foot');
  const narration = el('div', 'narration');
  narration.hidden = true;

  if (app.running) {
    const link = el('a', 'card-url', app.running.url);
    link.href = app.running.url;
    link.target = '_blank';
    link.rel = 'noopener';
    foot.appendChild(link);
    foot.appendChild(el('span', 'mono', `port ${app.running.port}`));
    const spacer = el('span', 'spacer');
    foot.appendChild(spacer);
    const stop = el('button', 'btn small', 'Stop');
    stop.addEventListener('click', () => stopApp(app, stop, narration));
    foot.appendChild(stop);
    const line = el('div', 'card-note');
    line.textContent = `Running for ${timeAgo(app.running.seconds)}. If the page did not open by itself, click the address above.`;
    card.appendChild(line);
  } else if ((app.problems || []).length) {
    foot.appendChild(el('span', 'mono', 'Nothing to start here'));
  } else {
    const open = el('button', 'btn primary', 'Open');
    open.addEventListener('click', () => openApp(app, open, narration));
    foot.appendChild(open);
    foot.appendChild(el('span', 'mono', app.port ? `prefers port ${app.port}` : 'any free port'));
    if (app.running === null) {
      const log = el('button', 'btn small ghost', 'What did it say?');
      log.addEventListener('click', () => showLog(app, narration));
      foot.appendChild(log);
    }
  }
  card.appendChild(foot);
  card.appendChild(narration);
  return card;
}

function narrate(node, line, kind) {
  node.hidden = false;
  const row = el('div', kind === 'bad' ? 'bad' : null, line);
  node.appendChild(row);
  node.scrollTop = node.scrollHeight;
}

async function openApp(app, button, narration) {
  clear(narration);
  button.disabled = true;
  button.textContent = 'Starting…';
  narrate(narration, `Opening ${app.name}.`);
  try {
    await stream('/api/start/stream', { id: app.id }, (event) => {
      if (event.type === 'step') narrate(narration, event.text);
      else if (event.type === 'running') narrate(narration, `It is up at ${event.url}`);
      else if (event.type === 'error') narrate(narration, event.message, 'bad');
    });
    const again = await api.get('/api/state');
    STATE = again;
    render();
    const now = (STATE.apps || []).find((item) => item.id === app.id);
    if (now && now.running) {
      toast(`${app.name} is running at ${now.running.url}`, 'good');
    }
  } catch (err) {
    narrate(narration, err.message, 'bad');
    toast(err.message, 'bad');
    button.disabled = false;
    button.textContent = 'Open';
  }
}

async function stopApp(app, button, narration) {
  button.disabled = true;
  button.textContent = 'Stopping…';
  try {
    const result = await api.post('/api/stop', { id: app.id });
    toast(result.message || `${app.name} has stopped.`, result.confirmed === false ? 'bad' : 'good');
  } catch (err) {
    toast(err.message, 'bad');
  }
  await refresh();
}

async function showLog(app, narration) {
  try {
    const data = await api.get(`/api/log/${encodeURIComponent(app.id)}`);
    clear(narration);
    if (!data.log || !data.log.length) {
      narrate(narration, `${app.name} has not printed anything yet.`);
      return;
    }
    narrate(narration, `The last ${data.log.length} line(s) it printed:`);
    data.log.slice(-40).forEach((line) => narrate(narration, line));
  } catch (err) {
    toast(err.message, 'bad');
  }
}

// ------------------------------------------------------------------- folders
function renderFolders() {
  const chips = $('folderChips');
  clear(chips);
  const entries = (STATE && STATE.watched) || [];
  entries.forEach((entry) => {
    const chip = el('span', 'chip' + (entry.kind === 'beside' ? ' beside' : '') + (entry.exists ? '' : ' missing'));
    const text = el('span', 'chip-text', entry.path);
    text.title = entry.path;
    chip.appendChild(text);
    const apps = entry.apps || 0;
    chip.appendChild(el('span', 'chip-count', apps === 1 ? '1 app' : `${apps} apps`));
    if (entry.kind === 'beside') {
      chip.title = 'Where nanoDesk itself lives — the family normally sits here, side by side.';
    } else {
      const remove = el('button', 'chip-x', '✕');
      remove.title = 'Stop looking in this folder';
      remove.addEventListener('click', () => forgetFolder(entry.path));
      chip.appendChild(remove);
    }
    if (!entry.exists) {
      chip.appendChild(el('span', 'chip-count', 'not there any more'));
    }
    chips.appendChild(chip);
  });
}

async function addFolder(event) {
  event.preventDefault();
  const input = $('folderInput');
  const button = $('addFolderBtn');
  const typed = input.value.trim();
  if (!typed) {
    note('Type or paste the folder path first, for example  C:\\Users\\you\\Downloads\\apps', 'bad');
    return;
  }
  button.disabled = true;
  note('Looking…');
  try {
    const data = await api.post('/api/folders', { path: typed });
    note(data.message, 'good');
    input.value = '';
    await refresh();
  } catch (err) {
    note(err.message, 'bad');
  }
  button.disabled = false;
}

async function forgetFolder(path) {
  try {
    const data = await api.del('/api/folders?path=' + encodeURIComponent(path));
    note(data.message, 'good');
    await refresh();
  } catch (err) {
    toast(err.message, 'bad');
  }
}

// ------------------------------------------------------------------ computer
function renderComputer() {
  const box = $('computerPanel');
  clear(box);
  if (!STATE || !STATE.computer) return;
  const computer = STATE.computer;

  const python = el('div', 'row');
  const pythonHead = el('div', 'row-head');
  pythonHead.appendChild(el('span', 'dot live'));
  pythonHead.appendChild(el('span', 'label', `Python ${computer.python}`));
  python.appendChild(pythonHead);
  box.appendChild(python);
  box.appendChild(el('div', 'say', computer.python_sentence));
  box.appendChild(el('div', 'say', computer.path_sentence));

  box.appendChild(el('div', 'divider'));
  const enginesHead = el('div', 'row-head');
  enginesHead.appendChild(el('span', 'dot ' + (computer.engines_running ? 'live' : 'off')));
  enginesHead.appendChild(
    el('span', 'label', computer.engines_running ? 'A local AI engine is running' : 'No local AI engines running')
  );
  box.appendChild(enginesHead);
  box.appendChild(
    el('div', 'say', 'nanoDesk does not start these — it only says whether they are there, because some apps need one.')
  );

  (computer.engines || []).forEach((engine) => {
    const row = el('div', 'row');
    const head = el('div', 'row-head');
    head.appendChild(el('span', 'dot ' + (engine.running ? 'live' : 'off')));
    head.appendChild(el('span', 'label', `${engine.name}`));
    head.appendChild(el('span', 'mono', `port ${engine.port}`));
    row.appendChild(head);
    box.appendChild(row);
    box.appendChild(el('div', 'say', engine.sentence));
  });
}

// --------------------------------------------------------------- empty state
function renderEmpty(box) {
  box.appendChild(el('h3', null, 'No apps found yet'));
  box.appendChild(el('p', null, 'nanoDesk looked in these folders:'));

  const list = el('ul');
  ((STATE && STATE.watched) || []).forEach((entry) => {
    const item = el('li');
    item.appendChild(el('span', 'mono', entry.path));
    if (!entry.exists) item.appendChild(el('span', 'chip-count', '  (this folder is not there)'));
    list.appendChild(item);
  });
  box.appendChild(list);

  const beside = (STATE && STATE.beside) || {};
  box.appendChild(
    el(
      'p',
      null,
      'The quickest fix is to put each app folder next to this one' +
        (beside.path ? ` — that is ${beside.path} — ` : ' — ') +
        'or to add the folder they are in below, on the left of this page.'
    )
  );

  if (STATE && STATE.nonoforge_here) {
    box.appendChild(
      el(
        'p',
        null,
        'If you have no apps yet, nonoForge makes you one: pick a card, answer two questions, and it ' +
          'builds a small working app of your own. It is in the folder beside this one.'
      )
    );
  }
}

// --------------------------------------------------------------------- start
async function main() {
  $('addFolderForm').addEventListener('submit', addFolder);
  $('rescanBtn').addEventListener('click', async () => {
    $('rescanBtn').disabled = true;
    try {
      const data = await api.post('/api/rescan', {});
      STATE = Object.assign({}, STATE, { apps: data.apps, watched: data.watched });
      render();
      toast(`${data.count} app${data.count === 1 ? '' : 's'} found.`, 'good');
    } catch (err) {
      toast(err.message, 'bad');
    }
    $('rescanBtn').disabled = false;
  });

  try {
    await refresh();
  } catch (err) {
    toast(`I could not read the list of apps: ${err.message}`, 'bad');
  }
}

main();
