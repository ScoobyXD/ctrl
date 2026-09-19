const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('typescript');
const source = ts.transpileModule(fs.readFileSync('web/app.ts', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const html = fs.readFileSync('web/index.html', 'utf8');
const base = { status: 'done', goal: 'Read Wikipedia', max_steps: 200, busy: false, running: false, error: null, page: null, decision: null, history: [], values: [], selected_tab: 'wiki', tabs: [{ id: 'wiki', title: 'Wikipedia', url: 'https://en.wikipedia.org/' }], credentials: { status: 'ready' }, browser: { status: 'connected' } };
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(setImmediate); };
class Node {
  constructor(id) { this.id = id; this.value = ''; this.textContent = ''; this.hidden = false; this.disabled = false; this.dataset = {}; this.options = []; this.listeners = {}; this.attributes = {}; this.span = { textContent: '' }; }
  get innerHTML() { return this.markup ?? ''; }
  set innerHTML(markup) { this.markup = markup; if (this.id === 'tab-select') { this.options = [...markup.matchAll(/<option value="([^"]*)">/g)].map((match) => ({ value: match[1] })); this.value = this.options[0]?.value ?? ''; } }
  addEventListener(name, listener) { (this.listeners[name] ??= []).push(listener); }
  dispatch(name) { for (const listener of this.listeners[name] ?? []) listener({ preventDefault() {} }); }
  click() { assert.equal(this.disabled, false, `${this.id} must be enabled`); this.dispatch('click'); }
  querySelector(selector) { return selector === 'span' ? this.span : null; }
  setAttribute(name, value) { this.attributes[name] = value; }
}
async function boot(overrides = {}, failurePaths = []) {
  let backend = structuredClone({ ...base, ...overrides });
  let finishPending = null;
  const calls = [], timers = [];
  const nodes = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map((match) => [match[1], new Node(match[1])]));
  nodes['max-steps'].value = '200';
  nodes['tab-select'].innerHTML = '<option value="">Connect Chrome</option>';
  const context = {
    exports: {}, console, Date, Promise, AbortController,
    document: { hidden: false, getElementById: (id) => nodes[id], querySelector: () => ({ content: 'test-token' }) },
    window: { setTimeout(callback, ms) { if (ms === 300) queueMicrotask(callback); else timers.push(callback); return timers.length; }, clearTimeout() {} },
    async fetch(path, options) {
      assert.equal(options.headers['X-CTRL-Token'], 'test-token');
      const body = options.body ? JSON.parse(options.body) : undefined;
      calls.push({ path, body });
      if (failurePaths.includes(path)) throw new Error('Test connection failure');
      if (path === '/api/state' && finishPending) { backend = finishPending(); finishPending = null; }
      if (path === '/api/start') {
        const previous = backend;
        backend = { ...previous, busy: true, running: body.run };
        finishPending = () => ({ ...previous, ...body, selected_tab: body.target_id, status: 'ready', busy: false, running: false, history: [], decision: null });
      } else if (path === '/api/supply_values') {
        const previous = backend;
        backend = { ...previous, busy: true };
        finishPending = () => ({ ...previous, values: [...previous.values, ...body.values], status: 'ready', busy: false });
      }
      return { ok: true, headers: { get: () => 'application/json' }, json: async () => structuredClone(backend) };
    },
  };
  vm.runInNewContext(source, context);
  await settle();
  return { nodes, calls, timers, setBackend: (next) => { backend = { ...backend, ...next }; } };
}
(async () => {
  let app = await boot();
  app.nodes.goal.value = 'Check Gmail, then X, then Reddit';
  app.nodes.goal.dispatch('input');
  assert.equal(app.nodes['run-button'].span.textContent, 'Start new task');
  app.nodes['run-button'].click();
  await settle();
  assert.deepEqual(app.calls.filter((call) => call.body).map((call) => call.path), ['/api/start']);
  assert.equal(app.calls.at(-1).body.goal, 'Check Gmail, then X, then Reddit');
  assert.equal(app.calls.at(-1).body.run, true);
  assert.equal(app.calls.at(-1).body.max_steps, 200);

  app = await boot();
  app.nodes['run-button'].click();
  await settle();
  assert.equal(app.calls.at(-1).path, '/api/start');

  app = await boot({ status: 'blocked', stop_reason: 'No eligible navigation option was available.', last_decision: { operation: 'BLOCKED', confidence: 0.98, latency_ms: 200 } });
  assert.match(app.nodes['decision-content'].innerHTML, /No eligible navigation option was available/);
  assert.match(app.nodes['history-content'].innerHTML, /stopped before executing a browser action/);
  assert.match(app.nodes['session-label'].textContent, /Agent stopped/);
  assert.match(app.nodes['decision-content'].innerHTML, /98% confidence/);
  assert.equal(app.nodes['act-button'].disabled, true);
  assert.equal(app.nodes['decision-footer'].hidden, true);

  app = await boot({ status: 'needs_input', input_request: 'Enter the exact search phrase.' });
  assert.equal(app.nodes['session-label'].textContent, 'More input needed');
  assert.equal(app.nodes['task-hint'].textContent, 'Enter the exact search phrase.');
  app.nodes.values.value = 'exact phrase';
  app.nodes.values.dispatch('input');
  assert.equal(app.nodes['run-button'].span.textContent, 'Resume agent');
  app.nodes['run-button'].click();
  await settle();
  assert.deepEqual(app.calls.filter((call) => call.body).map((call) => call.path), ['/api/supply_values', '/api/run']);

  app = await boot();
  app.nodes.goal.value = 'Open gmail.com';
  app.nodes.goal.dispatch('input');
  app.nodes['step-button'].click();
  await settle();
  assert.deepEqual(app.calls.filter((call) => call.body).map((call) => call.path), ['/api/start', '/api/step']);
  assert.equal(app.calls.find((call) => call.path === '/api/start').body.run, false);

  app = await boot();
  app.nodes['max-steps'].value = '0';
  app.nodes['run-button'].click();
  await settle();
  assert.equal(app.calls.filter((call) => call.body).length, 0);
  assert.match(app.nodes['app-error-message'].textContent, /between 1 and 500/);

  app = await boot({ status: 'ready' });
  app.setBackend({ goal: 'Different task from another interface', status: 'ready' });
  app.timers.at(-1)();
  await settle();
  app.nodes['run-button'].click();
  await settle();
  assert.equal(app.calls.at(-1).path, '/api/start');

  app = await boot({ status: 'ready', decision: { fingerprint: 'page-v1', operation: 'CLICK', choice: 'a1' } });
  assert.equal(app.nodes['act-button'].disabled, false);
  app.nodes.goal.value = 'Another task';
  app.nodes.goal.dispatch('input');
  assert.equal(app.nodes['act-button'].disabled, true);
  assert.match(app.nodes['decision-content'].innerHTML, /New command ready/);

  app = await boot({ status: 'ready' });
  app.setBackend({ selected_tab: 'mail', tabs: [...base.tabs, { id: 'mail', title: 'Gmail', url: 'https://mail.google.com/' }] });
  app.timers.at(-1)();
  await settle();
  assert.equal(app.nodes['tab-select'].value, 'mail');
  app.nodes['step-button'].click();
  await settle();
  assert.deepEqual(app.calls.filter((call) => call.body).map((call) => call.path), ['/api/step']);

  app = await boot({ status: 'ready', tabs: [...base.tabs, { id: 'mail', title: 'Gmail', url: 'https://mail.google.com/' }] });
  app.nodes['tab-select'].value = 'mail';
  app.nodes['tab-select'].dispatch('change');
  app.timers.at(-1)();
  await settle();
  assert.equal(app.nodes['tab-select'].value, 'mail');
  app.nodes['run-button'].click();
  await settle();
  assert.equal(app.calls.at(-1).body.target_id, 'mail');

  app = await boot({ status: 'ready', history: Array.from({ length: 125 }, (_, index) => ({ action: `Action ${index + 1}`, kind: 'click' })) });
  assert.equal((app.nodes['history-content'].innerHTML.match(/class="history-row"/g) ?? []).length, 125);

  app = await boot({ status: 'ready', max_steps: 350 });
  assert.equal(app.nodes['max-steps'].value, '350');
  app.nodes['max-steps'].value = '500';
  app.nodes['max-steps'].dispatch('input');
  app.nodes['run-button'].click();
  await settle();
  assert.equal(app.calls.at(-1).body.max_steps, 500);

  for (const [inputReason, message] of [['missing_address', 'Provide the website address to open.'], ['ambiguous_goal', 'Clarify which result you want.']]) {
    app = await boot({ status: 'needs_input', input_reason: inputReason, stop_reason: message });
    assert.equal(app.nodes['session-label'].textContent, 'More input needed');
    assert.equal(app.nodes['task-hint'].textContent, message);
    assert.match(app.nodes['decision-content'].innerHTML, /More input needed/);
    assert.ok(app.nodes['decision-content'].innerHTML.includes(message));
  }

  app = await boot({ status: 'running', running: true, busy: true });
  const scheduledPoll = app.timers.at(-1);
  assert.equal(app.nodes['emergency-stop'].disabled, false);
  app.nodes['emergency-stop'].click();
  await settle();
  assert.equal(app.calls.at(-1).path, '/api/shutdown');
  assert.equal(app.nodes['emergency-stop'].textContent, 'AGENT IS OFF');
  assert.equal(app.nodes['run-button'].disabled, true);
  assert.match(app.nodes['shutdown-notice'].textContent, /restart from CTRL Control Panel/);
  const afterStopCalls = app.calls.length;
  scheduledPoll();
  await settle();
  assert.equal(app.calls.length, afterStopCalls);

  app = await boot({}, ['/api/shutdown']);
  app.nodes['emergency-stop'].click();
  await settle();
  assert.equal(app.nodes['emergency-stop'].disabled, false);
  assert.equal(app.nodes['emergency-stop'].textContent, 'RETRY STOP');
  assert.match(app.nodes['shutdown-notice'].textContent, /Stop could not be confirmed/);
  assert.equal(app.nodes['run-button'].disabled, true);

  app = await boot();
  app.nodes['step-button'].click();
  app.nodes['emergency-stop'].click();
  await settle();
  assert.deepEqual(app.calls.filter((call) => call.body).map((call) => call.path), ['/api/start', '/api/shutdown']);

  app = await boot({}, ['/api/state']);
  app.timers.at(-1)(); await settle();
  app.timers.at(-1)(); await settle();
  assert.equal(app.calls.length, 3);
  assert.equal(app.timers.length, 2);
  assert.match(app.nodes['shutdown-notice'].textContent, /reload this page/);

  console.log('PASS: 18 frontend regressions covering task state, navigation continuity, budgets, stop reasons, emergency shutdown during a busy run, cancellation of queued actions/polling, failed-stop fallback, and offline polling limits.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
