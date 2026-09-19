export {};

type JsonObject = Record<string, unknown>;
type ChromeTab = JsonObject & { id?: string; target_id?: string; title?: string; url?: string };
type SessionState = JsonObject & {
  status?: string;
  running?: boolean;
  busy?: boolean;
  goal?: string;
  page?: JsonObject | null;
  history?: unknown[];
  decision?: JsonObject | null;
  credentials?: { status?: string; message?: string };
  browser?: { status?: string; message?: string };
  tabs?: ChromeTab[];
  selected_tab?: string | ChromeTab;
  error?: unknown;
};

const element = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing interface element: ${id}`);
  return found as T;
};
const goal = element<HTMLTextAreaElement>('goal');
const values = element<HTMLTextAreaElement>('values');
const tabs = element<HTMLSelectElement>('tab-select');
const token = document.querySelector<HTMLMetaElement>('meta[name="ctrl-token"]')?.content ?? '';
let state: SessionState = {};
let requestPending = false;
let connected = false;
let taskSignature = '';
let taskIdentity = '';
let latestError = '';
let dismissedError = '';
let lastPageRender = '';
let lastDecisionRender = '';
let lastHistoryRender = '';
let serverMessage = '';
let previousStatus = '';
let initialized = false;

const object = (value: unknown): JsonObject => typeof value === 'object' && value !== null ? value as JsonObject : {};
const string = (value: unknown): string => value == null ? '' : typeof value === 'string' ? value : typeof value === 'object' ? JSON.stringify(value) : String(value);
const escape = (value: unknown): string => string(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!);
const humanize = (value: unknown): string => string(value).replace(/[_-]/g, ' ').replace(/^./, (character) => character.toUpperCase());
const first = (...items: unknown[]): unknown => items.find((item) => item !== undefined && item !== null && item !== '');
const setText = (id: string, value: unknown) => { const node = element(id); const next = string(value); if (node.textContent !== next) node.textContent = next; };
const button = (id: string) => element<HTMLButtonElement>(id);
const isBusy = () => requestPending || Boolean(state.busy) || Boolean(state.running);

async function api(path: string, body?: JsonObject): Promise<unknown> {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Accept: 'application/json', 'X-CTRL-Token': token, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    credentials: 'same-origin',
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const contentType = response.headers.get('content-type') ?? '';
  const result: unknown = contentType.includes('application/json') ? await response.json() : null;
  if (!response.ok) throw new Error(string(first(object(result).error, object(result).detail, `Request failed (${response.status}).`)));
  return result;
}

function showError(message: string) {
  latestError = message;
  setText('app-error-message', message);
  element('app-error').hidden = !message || message === dismissedError;
}

function renderButtons() {
  const busy = isBusy();
  const hasGoal = Boolean(goal.value.trim());
  for (const id of ['run-button', 'step-button', 'predict-button']) button(id).disabled = !connected || busy || !hasGoal;
  button('pause-button').disabled = !connected || !(state.running || state.busy);
  for (const id of ['connect-button', 'credentials-button', 'refresh-tabs', 'reset-button']) button(id).disabled = !connected || busy;
  const fingerprint = first(state.decision?.fingerprint, state.fingerprint);
  button('act-button').disabled = !connected || busy || !fingerprint || !state.decision;
  tabs.disabled = busy || tabs.options.length === 0 || !tabs.options[0]?.value;
  goal.readOnly = Boolean(state.running);
  values.readOnly = Boolean(state.running);
}

function statusTone(status: unknown): 'ready' | 'busy' | 'error' | 'idle' {
  const value = string(status).toLowerCase();
  if (/^(ready|connected|available|loaded|ok|valid|authenticated)$/.test(value)) return 'ready';
  if (/loading|connecting|checking|pending|waiting/.test(value)) return 'busy';
  if (/error|fail|missing|unavailable|expired/.test(value)) return 'error';
  return 'idle';
}

function renderConnection(prefix: 'browser' | 'credentials', connection: SessionState['browser']) {
  element(`${prefix}-dot`).dataset.tone = statusTone(connection?.status);
  const fallback = prefix === 'browser' ? 'Connect to your existing Chrome browser.' : 'Check credentials from AWS Secrets Manager.';
  setText(`${prefix}-message`, connection?.message || fallback);
  if (prefix === 'browser') button('connect-button').textContent = statusTone(connection?.status) === 'ready' ? 'Reconnect' : 'Connect Chrome';
}

function tabId(tab: ChromeTab): string { return string(first(tab.id, tab.target_id, tab.targetId)); }

function renderTabs(items: ChromeTab[]) {
  const previousValue = tabs.value;
  const serverSelection = typeof state.selected_tab === 'string' ? state.selected_tab : tabId(state.selected_tab ?? {});
  const options = items.length ? items.map((tab) => `<option value="${escape(tabId(tab))}">${escape(first(tab.title, tab.url, 'Untitled tab'))}</option>`).join('') : '<option value="">No browser tabs available</option>';
  if (tabs.innerHTML !== options) tabs.innerHTML = options;
  const selected = [previousValue, serverSelection].find((value) => value && items.some((tab) => tabId(tab) === value));
  if (selected) tabs.value = selected;
}

function controlsFor(page: JsonObject): unknown[] {
  const controls = first(page.actions, page.controls, page.elements);
  if (Array.isArray(controls)) return controls;
  if (controls && typeof controls === 'object') return Object.entries(controls).map(([key, value]) => ({ id: key, ...object(value), ...(typeof value === 'string' ? { label: value } : {}) }));
  return [];
}

function renderPage() {
  const key = JSON.stringify(state.page);
  if (key === lastPageRender) return;
  lastPageRender = key;
  const page = state.page;
  element('empty-page').hidden = Boolean(page);
  element('page-content').hidden = !page;
  setText('page-state-label', page ? 'CURRENT OBSERVATION' : 'AWAITING A PAGE');
  if (!page) return;
  setText('page-title', first(page.title, 'Untitled page'));
  setText('page-url', page.url);
  setText('text-pane', first(page.text, page.page_text, page.content, 'No readable text was found in this observation.'));
  const controls = controlsFor(page);
  setText('controls-count', controls.length);
  element('controls-pane').innerHTML = controls.length ? controls.map((item, index) => {
    const control = object(item);
    return `<div class="control-row"><span class="control-index">${escape(first(control.id, control.index, index + 1))}</span><span class="control-type">${escape(first(control.role, control.kind, control.type, control.tag, control.tagName, 'control'))}</span><span class="control-label">${escape(first(control.label, control.name, control.text, control.description, control.placeholder, typeof item === 'string' ? item : 'Unnamed control'))}</span></div>`;
  }).join('') : '<p class="control-empty">No interactive controls found in this observation.</p>';
}

function decisionOperation(decision: JsonObject): unknown {
  return first(decision.operation, decision.action, decision.kind, decision.op, decision.choice, 'Decision ready');
}

function decisionTarget(decision: JsonObject): string {
  const action = state.page ? controlsFor(state.page).map(object).find((control) => control.id === decision.choice) : undefined;
  if (action?.label) return `${string(action.label)}${decision.text ? ` → “${string(decision.text)}”` : ''}`;
  const target = first(decision.target, decision.element, decision.value, decision.url, decision.text, decision.reason, decision.message);
  if (typeof target === 'object' && target !== null) {
    const value = object(target);
    return string(first(value.label, value.name, value.text, value.description, value.id, target));
  }
  return string(target);
}

function renderDecision() {
  const key = JSON.stringify(state.decision);
  if (key === lastDecisionRender) return;
  lastDecisionRender = key;
  const decision = state.decision;
  element('decision-footer').hidden = !decision;
  if (!decision) {
    element('decision-content').innerHTML = '<div class="decision-empty"><span class="decision-empty-icon">↗</span><p>The next decision will appear here.<small>Use Choose only to preview before executing.</small></p></div>';
    return;
  }
  const confidence = Number(first(decision.confidence, decision.probability));
  const latency = Number(first(decision.latency_ms, decision.elapsed_ms, decision.duration_ms));
  const confidenceLabel = Number.isFinite(confidence) ? `${Math.round(confidence <= 1 ? confidence * 100 : confidence)}%` : '—';
  const latencyLabel = Number.isFinite(latency) ? latency < 1000 ? `${Math.round(latency)} ms` : `${(latency / 1000).toFixed(2)} s` : '—';
  element('decision-content').innerHTML = `<div class="decision-details"><div><h3 class="decision-title">${escape(humanize(decisionOperation(decision)))}</h3><p class="decision-target">${escape(decisionTarget(decision) || 'Selected against the current page observation.')}</p></div><div class="decision-metrics"><div class="decision-metric"><span>CONFIDENCE</span><strong>${confidenceLabel}</strong></div><div class="decision-metric"><span>DECISION TIME</span><strong>${latencyLabel}</strong></div></div></div>`;
  const hasFingerprint = Boolean(first(decision.fingerprint, state.fingerprint));
  setText('decision-explanation', hasFingerprint ? 'Ready to execute against the observed page.' : 'Choose a fresh action before executing.');
}

function formatTime(value: unknown): string {
  if (value === undefined || value === null || value === '') return '';
  const date = new Date(typeof value === 'number' ? (value < 1e12 ? value * 1000 : value) : string(value));
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function renderHistory() {
  const history = state.history ?? [];
  const key = JSON.stringify(history);
  if (key === lastHistoryRender) return;
  lastHistoryRender = key;
  setText('history-count', `${history.length} ${history.length === 1 ? 'EVENT' : 'EVENTS'}`);
  if (!history.length) {
    element('history-content').innerHTML = '<div class="history-empty"><span class="history-empty-mark">—</span><p>A clear trail of every decision and action.</p><span class="tiny-label">READY WHEN YOU ARE</span></div>';
    return;
  }
  element('history-content').innerHTML = `<div class="history-list">${history.slice(-60).reverse().map((item, index) => {
    const event = object(item);
    const decision = object(event.decision);
    const title = first(event.message, event.description, event.action, event.operation, event.event, typeof item === 'string' ? item : undefined, decisionOperation(decision));
    const detail = first(event.detail, event.text, event.url, event.target, decisionTarget(decision));
    const status = first(event.status, event.type, event.kind, 'recorded');
    const time = formatTime(first(event.timestamp, event.time, event.created_at)) || (typeof event.elapsed_ms === 'number' ? `+${(event.elapsed_ms / 1000).toFixed(1)} s` : '');
    return `<div class="history-row" data-tone="${statusTone(status) === 'error' ? 'error' : 'normal'}"><span class="history-number">${String(history.length - index).padStart(2, '0')}</span><time class="history-time">${escape(time)}</time><div class="history-description"><strong>${escape(humanize(title))}</strong>${detail ? `<small>${escape(detail)}</small>` : ''}</div><span class="history-badge">${escape(humanize(status))}</span></div>`;
  }).join('')}</div>`;
}

function render() {
  const status = string(state.status);
  const labels: Record<string, string> = { idle: 'Ready for your command', ready: 'Ready for your command', running: 'Agent is running', paused: 'Session paused', completed: 'Task completed', complete: 'Task completed', done: 'Task completed', deciding: 'Jev is choosing an action', observing: 'Reading the current page', acting: 'Executing the chosen action', waiting_for_chrome: 'Waiting for Chrome connection', waiting_for_credentials: 'Waiting for Jev credentials', needs_input: 'More input needed', error: 'Needs your attention' };
  setText('session-label', !connected ? serverMessage || 'Connecting to local app' : requestPending ? 'Working on your request' : state.running ? 'Agent is running' : state.busy ? labels[status] || 'Agent is working' : labels[status] || humanize(status) || 'Ready for your command');
  element('session-status').dataset.tone = state.running || state.busy || requestPending ? 'running' : state.error || !connected ? 'error' : 'idle';
  renderConnection('browser', state.browser);
  renderConnection('credentials', state.credentials);
  if (state.tabs) renderTabs(state.tabs);
  setText('model-name', first(state.model, 'JEV'));
  renderPage();
  renderDecision();
  renderHistory();
  renderButtons();
  if (status === 'needs_input' && previousStatus !== status) element<HTMLDetailsElement>('values-section').open = true;
  setText('task-hint', status === 'needs_input' ? 'The agent needs text to continue. Add the exact value below, then resume.' : 'Jev chooses actions. Provide the exact text it should enter.');
  const runLabel = button('run-button').querySelector('span');
  if (runLabel) runLabel.textContent = /^(paused|needs_input)$/.test(status) ? 'Resume agent' : /^(done|blocked|error)$/.test(status) ? 'Run again' : 'Run agent';
  previousStatus = status;
  const error = string(state.error);
  if (error) showError(error);
}

function consumeState(result: unknown) {
  const data = object(result);
  const next = data.state && typeof data.state === 'object' ? object(data.state) : data;
  if ('status' in next || 'page' in next || 'history' in next || 'credentials' in next) {
    state = next as SessionState;
    let restored = false;
    if (!initialized) {
      if (!goal.value && state.goal) {
        goal.value = state.goal;
        if (Array.isArray(state.values)) values.value = state.values.map(string).join('\n');
        restored = true;
      }
      initialized = true;
    }
    setText('goal-count', `${goal.value.length} CHARACTERS`);
    render();
    if (restored) {
      const input = inputs();
      taskSignature = JSON.stringify(input);
      taskIdentity = JSON.stringify({ goal: input.goal, target_id: input.target_id });
    }
  }
}

async function command(action: () => Promise<void>) {
  if (requestPending) return;
  requestPending = true;
  dismissedError = '';
  showError('');
  render();
  try { await action(); }
  catch (error) { showError(error instanceof Error ? error.message : 'The request could not be completed.'); }
  finally { requestPending = false; render(); }
}

function inputs() { return { goal: goal.value.trim(), target_id: tabs.value, values: values.value.split(/\r?\n/).filter((value) => value.trim()) }; }

async function startOrContinue(mode: 'run' | 'step' | 'predict') {
  const input = inputs();
  if (!input.goal) throw new Error('Enter a command for the agent first.');
  if (!input.target_id) throw new Error('Connect Chrome and select the tab you want to use.');
  const signature = JSON.stringify(input);
  const identity = JSON.stringify({ goal: input.goal, target_id: input.target_id });
  if (state.status === 'needs_input' && taskIdentity === identity && taskSignature !== signature) {
    consumeState(await api('/api/supply_values', { values: input.values }));
    if (!await waitUntilIdle()) return;
    taskSignature = signature;
  }
  if (!state.goal || taskSignature !== signature || /^(done|blocked|error)$/.test(string(state.status))) {
    consumeState(await api('/api/start', { ...input, run: mode === 'run' }));
    taskSignature = signature;
    taskIdentity = identity;
    if (mode === 'run') return;
    if (!await waitUntilIdle()) return;
  }
  consumeState(await api(`/api/${mode}`, {}));
}

async function waitUntilIdle(): Promise<boolean> {
  const started = Date.now();
  while (state.busy) {
    if (Date.now() - started > 120_000) throw new Error('This operation is taking longer than expected. Pause it or wait for the current request to finish.');
    await new Promise<void>((resolve) => window.setTimeout(resolve, 300));
    consumeState(await api('/api/state'));
  }
  if (state.error) throw new Error(string(state.error));
  return state.status !== 'paused' && state.status !== 'pausing';
}

button('run-button').addEventListener('click', () => void command(() => startOrContinue('run')));
button('step-button').addEventListener('click', () => void command(() => startOrContinue('step')));
button('predict-button').addEventListener('click', () => void command(() => startOrContinue('predict')));
button('pause-button').addEventListener('click', () => {
  void api('/api/pause', {}).then(consumeState).catch((error: unknown) => showError(error instanceof Error ? error.message : 'Unable to pause the agent.'));
});
button('connect-button').addEventListener('click', () => void command(async () => consumeState(await api('/api/connect', {}))));
button('credentials-button').addEventListener('click', () => void command(async () => consumeState(await api('/api/credentials', {}))));
button('refresh-tabs').addEventListener('click', () => void command(refreshTabs));
button('reset-button').addEventListener('click', () => void command(async () => { consumeState(await api('/api/reset', {})); taskSignature = ''; taskIdentity = ''; showError(''); }));
button('act-button').addEventListener('click', () => void command(async () => { const fingerprint = first(state.decision?.fingerprint, state.fingerprint); if (!fingerprint) throw new Error('Choose a fresh action before executing.'); consumeState(await api('/api/act', { fingerprint })); }));
button('dismiss-error').addEventListener('click', () => { dismissedError = latestError; element('app-error').hidden = true; });
goal.addEventListener('input', () => { setText('goal-count', `${goal.value.length} CHARACTERS`); renderButtons(); });
goal.addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !button('run-button').disabled) { event.preventDefault(); button('run-button').click(); } });
for (const view of ['text', 'controls']) {
  button(`${view}-tab`).addEventListener('click', () => {
    for (const pane of ['text', 'controls']) { element(`${pane}-pane`).hidden = pane !== view; button(`${pane}-tab`).setAttribute('aria-selected', String(pane === view)); }
  });
}

async function refreshTabs() {
  const result = await api('/api/tabs');
  const data = object(result);
  consumeState(result);
  const items = Array.isArray(result) ? result : Array.isArray(data.tabs) ? data.tabs : [];
  state.tabs = items as ChromeTab[];
  renderTabs(state.tabs);
  renderButtons();
}

async function poll() {
  try {
    const result = await api('/api/state');
    connected = true;
    serverMessage = '';
    consumeState(result);
  } catch {
    connected = false;
    serverMessage = 'Local app is unavailable';
    render();
  } finally {
    window.setTimeout(() => void poll(), document.hidden ? 1800 : 600);
  }
}

renderButtons();
void poll();
