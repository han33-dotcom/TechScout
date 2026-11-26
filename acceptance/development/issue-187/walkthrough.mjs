// Fixture-backed browser walkthrough for #187: registration, sign-in, add-and-return to the same
// chat, selection for named sellers, later reuse and decline. It starts the real API on a fresh
// local SQLite database, serves the built frontend from it and drives headless Chrome through the
// DevTools protocol. Chats are the synthetic workspace fixture (see seed_chat.py); no worker,
// retailer, model, ledger or provider key is used. Accounts and addresses are the invented values
// in tests/fixtures/accounts.json, so no real person's data appears in the captures.
//
//   cd frontend && npm ci && npm run build && cd ..
//   node acceptance/development/issue-187/walkthrough.mjs --python .venv/Scripts/python.exe --chrome "<chrome>"
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url)), root = resolve(here, '../../..');
const option = (name, fallback) => { const at = process.argv.indexOf(`--${name}`); return at > 0 ? process.argv[at + 1] : fallback; };
const python = option('python', 'python'), chromePath = option('chrome', 'chrome'), port = Number(option('port', '8187'));
const base = `http://127.0.0.1:${port}`, out = here;
const data = JSON.parse(readFileSync(join(root, 'tests/fixtures/accounts.json'), 'utf8'));
const [buyerA, , buyerNew] = data.accounts, [home, office] = data.locations;
if (!existsSync(join(root, 'frontend/dist/index.html'))) throw new Error('Build the frontend first (npm run build in frontend/)');

const work = mkdtempSync(join(tmpdir(), 'techscout-187-'));
const databaseUrl = `sqlite:///${join(work, 'walkthrough.db').replaceAll('\\', '/')}`;
const env = { ...process.env, DATABASE_URL: databaseUrl, TECHSCOUT_COOKIE_SECURE: '0', EVIDENCE_DIR: join(work, 'evidence'),
  OPENROUTER_API_KEY: '', OPENAI_API_KEY: '' };
const sleep = ms => new Promise(done => setTimeout(done, ms));
async function until(check, what, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { try { if (await check()) return; } catch { /* not ready */ } await sleep(150); }
  throw new Error(`Timed out waiting for ${what}`);
}

execFileSync(python, ['-m', 'alembic', 'upgrade', 'head'], { cwd: root, env, stdio: 'inherit' });
const api = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)], { cwd: root, env, stdio: 'ignore' });
const profile = join(work, 'chrome');
const chrome = spawn(chromePath, ['--headless=new', '--remote-debugging-port=9287', `--user-data-dir=${profile}`,
  '--window-size=1280,1000', '--no-first-run', '--no-default-browser-check', '--disable-extensions', 'about:blank'], { stdio: 'ignore' });
const stop = () => { api.kill(); chrome.kill(); };
process.on('exit', stop);

let socket, sequence = 0;
const pending = new Map();
function send(method, params = {}) {
  return new Promise((done, fail) => { const id = ++sequence; pending.set(id, { done, fail }); socket.send(JSON.stringify({ id, method, params })); });
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
}
const helpers = `window.__walk = {
  find(selector, text) { return [...document.querySelectorAll(selector)].find(el => el.textContent.trim() === text); },
  click(selector, text, scope = document) { const el = [...scope.querySelectorAll(selector)].find(e => e.textContent.trim() === text && !e.disabled); if (!el) throw new Error('Missing ' + selector + ' ' + text); el.click(); return true; },
  fill(label, value) { const owner = [...document.querySelectorAll('label')].find(l => l.childNodes[0]?.textContent?.trim() === label);
    const input = owner.querySelector('input'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true })); return true; },
  tick(name) { [...document.querySelectorAll('.seller-choice label')].find(l => l.textContent.trim() === name).querySelector('input').click(); return true; },
  lines(selector) { return [...document.querySelectorAll(selector)].map(el => el.innerText.trim()).filter(Boolean); },
};`;
const walk = expression => evaluate(`window.__walk.${expression}`);
const visible = text => evaluate(`document.body.innerText.includes(${JSON.stringify(text)})`);
async function open(path) { await send('Page.navigate', { url: base + path }); await until(() => visible('techscout'), `page ${path}`); }
async function shot(name, selector) {
  if (selector) await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start'}); window.scrollBy(0,-110); true`);
  else await evaluate('window.scrollTo(0,0); true');
  await sleep(250);
  const { data: png } = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(out, `${name}.png`), Buffer.from(png, 'base64'));
  return `${name}.png`;
}
async function token() {
  const { cookies } = await send('Network.getCookies', { urls: [base] });
  return cookies.find(cookie => cookie.name === 'techscout_session').value;
}
async function seed(suffix) {
  const result = execFileSync(python, ['acceptance/development/issue-187/seed_chat.py', '--database-url', databaseUrl,
    '--session-token', await token(), '--suffix', suffix], { cwd: root, env });
  return JSON.parse(result.toString());
}
const json = path => evaluate(`fetch(${JSON.stringify(path)}, {cache:'no-store'}).then(async r => ({status: r.status, body: r.headers.get('content-type')?.includes('json') ? await r.json() : await r.text()}))`);
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex').slice(0, 16);
const totals = workspace => [...workspace.current.recommendations.flatMap(group => [group.primary, ...group.additional_sellers])
  .map(item => ({ seller: item.offer.seller.name, total: item.cost.total })),
  ...workspace.current.uncertain.map(item => ({ seller: item.offer.seller.name, total: item.cost?.total ?? 'unknown' }))];
async function account(action, buyer) {
  await walk(`click('button', ${JSON.stringify(action === 'register' ? 'Sign in or create account' : 'Sign in or create account')})`);
  await until(() => visible('Sign in to TechScout'), 'account page');
  if (action === 'register') await walk(`click('.category-row button', 'Create account')`);
  await walk(`fill('Username', ${JSON.stringify(buyer.username)})`);
  await walk(`fill('Password', ${JSON.stringify(buyer.password)})`);
  if (action === 'register') await walk(`fill('Confirm password', ${JSON.stringify(buyer.password)})`);
  await walk(`click('form .primary', ${JSON.stringify(action === 'register' ? 'Create account' : 'Sign in')})`);
}
async function signOut() {
  await walk(`click('.account-nav button', 'Account')`);
  await walk(`click('button', 'Sign out')`);
  await walk(`click('button', 'Sign out and clear searches')`);
  await until(() => visible('Sign in or create account'), 'signed out');
}
const steps = [], checks = {};
const record = async (step, extra = {}) => {
  steps.push({ step, url: await evaluate('location.pathname + location.search'), ...extra });
};

try {
  await until(async () => (await fetch(`${base}/health`)).ok, 'API health');
  await until(async () => (await fetch('http://127.0.0.1:9287/json/version')).ok, 'Chrome');
  const browserVersion = (await (await fetch('http://127.0.0.1:9287/json/version')).json()).Browser;
  const target = (await (await fetch('http://127.0.0.1:9287/json/list')).json()).find(item => item.type === 'page');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  socket.onmessage = event => { const message = JSON.parse(event.data); const waiter = pending.get(message.id);
    if (waiter) { pending.delete(message.id); message.error ? waiter.fail(new Error(message.error.message)) : waiter.done(message.result); } };
  await new Promise(done => { socket.onopen = done; });
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Page.addScriptToEvaluateOnNewDocument', { source: helpers });
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });

  // 1. A new buyer creates an account (scenario 13, account creation exercised separately).
  await open('/');
  await until(() => evaluate(`!document.querySelector('.account-nav button')?.disabled`), 'account state');
  await account('register', buyerNew);
  await until(() => visible(`Signed in as ${buyerNew.username}`), 'registration');
  await record('registration', { account: buyerNew.username, screenshot: await shot('01-registered') });
  checks.registration_session = (await json('/api/session')).body.account;
  await signOut();

  // 2. A buyer with an account and no saved address signs in, then the chat names its sellers once.
  await account('register', buyerA);
  await until(() => visible(`Signed in as ${buyerA.username}`), 'buyer A account');
  await signOut();
  await account('sign-in', buyerA);
  await until(() => visible(`Signed in as ${buyerA.username}`), 'sign-in');
  checks.saved_before_chat = (await json('/api/account/locations')).body.items.length;
  const first = await seed('first');
  await open(`/?chat=${first.run_id}`);
  await until(() => visible('Exact delivery totals may need your address'), 'address prompt');
  const before = (await json(`/api/runs/${first.run_id}`)).body;
  await record('chat with address prompt', { account: buyerA.username, run_id: first.run_id,
    request_revision: before.current.request.revision, previous_revision: before.previous?.request.revision,
    prompt: await walk(`lines('.destination h2, .destination p')`), screenshot: await shot('02-chat-address-prompt', '.destination') });
  await shot('02a-chat-before', '.progress');
  await walk(`tick('Book-Local')`); await walk(`tick('Book-Other')`);
  await walk(`click('.destination button', 'Choose a saved address')`);
  await until(() => visible('Choose the address for your comparison'), 'Locations in choose mode');
  await until(() => visible('No saved locations yet'), 'empty Locations');
  await record('Locations opened from the chat', { banner: await walk(`lines('.locations .notice')`), screenshot: await shot('03-locations-from-chat') });
  for (const location of [home, office]) {
    await walk(`fill('Label', ${JSON.stringify(location.label)})`); await walk(`fill('City in Lebanon', ${JSON.stringify(location.city)})`);
    await walk(`fill('Street address', ${JSON.stringify(location.street)})`);
    if (location.details) await walk(`fill('Building, floor (optional)', ${JSON.stringify(location.details)})`);
    await walk(`click('button', 'Save location')`);
    await until(() => visible(location.label), `saved ${location.label}`);
  }
  await record('two locations added', { listed: await walk(`lines('.locations li strong, .locations li small')`), screenshot: await shot('04-locations-added') });
  await evaluate(`[...document.querySelectorAll('.locations li')].find(li => li.querySelector('strong').textContent === ${JSON.stringify(home.label)}).querySelector('.primary').click(); true`);
  await until(() => visible(`Using ${home.label} · ${home.city}`), 'return to the chat');
  const after = (await json(`/api/runs/${first.run_id}`)).body;
  const state = (await json(`/api/runs/${first.run_id}/destination`)).body;
  await record('returned to the same chat', { run_id: first.run_id, selection: state.selection,
    summary: await walk(`lines('.destination h2, .destination p')`), screenshot: await shot('05-same-chat-after-selection', '.destination') });
  await shot('05a-same-chat-after', '.progress');
  const history = (await json('/api/history')).body.items;
  Object.assign(checks, {
    same_chat_url: (await evaluate('location.search')) === `?chat=${first.run_id}`,
    same_run_and_previous_revision: after.current.run.run_id === before.current.run.run_id && after.previous?.run.run_id === first.previous_run_id,
    request_constraints_retained: digest(after.current.request) === digest(before.current.request),
    request_summary: { category: after.current.request.requirements.category, budget: after.current.request.budget,
      destination_city: after.current.request.fulfillment.destination.city, conditions: after.current.request.allowed_conditions },
    totals_before: totals(before), totals_after: totals(after),
    totals_unchanged_until_186: digest(totals(before)) === digest(totals(after)),
    no_new_search_submitted: history.length === 1 && history[0].run_id === first.run_id,
    recipients_named_by_buyer: state.selection.recipients.map(item => item.name),
    quote_state: state.selection.quote_state,
  });

  // 3. A later signed-in search reuses a saved location with a new seller choice (scenarios 12 and 13).
  await signOut();
  await account('sign-in', buyerA);
  await until(() => visible(`Signed in as ${buyerA.username}`), 'second sign-in');
  const later = await seed('later');
  await open(`/?chat=${later.run_id}`);
  await until(() => visible('Exact delivery totals may need your address'), 'later prompt');
  await walk(`tick('Book-Other')`);
  await walk(`click('.destination button', 'Choose a saved address')`);
  await until(() => visible(office.label), 'saved locations listed');
  await record('later search lists saved locations', { listed: await walk(`lines('.locations li strong, .locations li small')`), screenshot: await shot('06-later-search-saved-locations') });
  await evaluate(`[...document.querySelectorAll('.locations li')].find(li => li.querySelector('strong').textContent === ${JSON.stringify(office.label)}).querySelector('.primary').click(); true`);
  await until(() => visible(`Using ${office.label} · ${office.city}`), 'later selection');
  const reused = (await json(`/api/runs/${later.run_id}/destination`)).body.selection;
  await record('later search reused a saved location', { run_id: later.run_id, selection: reused, screenshot: await shot('07-later-search-selected', '.destination') });
  checks.later_reuse = { location: reused.location.label, recipients: reused.recipients.map(item => item.name) };

  // 4. Decline and keep shopping (scenario 14), here without an account.
  await signOut();
  const declined = await seed('decline');
  await open(`/?chat=${declined.run_id}`);
  await until(() => visible('Continue without an address'), 'decline option');
  await walk(`click('.destination button', 'Continue without an address')`);
  await until(() => visible('Continuing without a precise address'), 'decline recorded');
  const links = await evaluate(`[...document.querySelectorAll('a[href^="https://"]')].filter(a => /View at|Open product page/.test(a.textContent)).map(a => ({text: a.textContent.trim(), https: a.href.startsWith('https://')}))`);
  await record('declined and kept shopping', { run_id: declined.run_id, summary: await walk(`lines('.destination h2, .destination p')`),
    product_links: links, screenshot: await shot('08-declined-keeps-offers', '.destination') });
  checks.decline = { selection: (await json(`/api/runs/${declined.run_id}/destination`)).body.selection.status,
    product_links: links.length, uncertain_total: totals((await json(`/api/runs/${declined.run_id}`)).body).at(-1) };
  checks.anonymous_locations_status = (await json('/api/account/locations')).status;

  // Privacy: the street and building details appear nowhere the buyer or an export can see.
  const surfaces = [await evaluate('document.body.innerText'), JSON.stringify(steps),
    JSON.stringify((await json(`/api/history/${declined.run_id}/export`)).body)];
  checks.street_absent_from_page_steps_and_export = data.locations.every(location =>
    surfaces.every(text => !text.includes(location.street) && !(location.details && text.includes(location.details))));
  checks.password_absent_from_evidence = !JSON.stringify({ steps, checks }).includes(buyerA.password);

  const revision = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root }).toString().trim();
  writeFileSync(join(out, 'evidence.json'), JSON.stringify({
    issue: 187, scenarios: [12, 13, 14], revision, recorded_at: new Date().toISOString(),
    source_mode: 'Synthetic v2 workspace fixture stored as each session\'s chat (seed_chat.py). No worker, retailer, model, ledger or provider key. Retailer quotes are #186, so totals are expected to stay unchanged.',
    runtime: { api: 'uvicorn app.main:app on 127.0.0.1 with a fresh SQLite database, TECHSCOUT_COOKIE_SECURE=0, built frontend served by FastAPI', browser: browserVersion },
    test_data: 'Synthetic accounts and invented addresses from tests/fixtures/accounts.json. Passwords are not recorded here.',
    steps, checks,
  }, null, 2) + '\n');
  console.log(JSON.stringify(checks, null, 2));
} finally {
  socket?.close(); stop();
  await sleep(500);
  try { rmSync(work, { recursive: true, force: true }); } catch { /* Windows may hold the database briefly */ }
}
