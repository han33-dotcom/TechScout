// Fixture-backed browser walkthrough for #186 (and #187 box 2): delivery quotes in the same chat.
// serve.py starts the real API on a fresh local SQLite database with the real quote loop, gate and
// a fixture ledger; the only fake is the network transport, a scripted Shopify Storefront for
// synthetic sellers. Chats are the offline test chat (seed_chat.py). Accounts and addresses are the
// invented values in tests/fixtures/accounts.json; streets never appear in the evidence.
//
//   cd frontend && npm ci && npm run build && cd ..
//   node acceptance/development/issue-186/walkthrough.mjs --python .venv/Scripts/python.exe --chrome "<chrome>"
import { spawn, execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url)), root = resolve(here, '../../..');
const option = (name, fallback) => { const at = process.argv.indexOf(`--${name}`); return at > 0 ? process.argv[at + 1] : fallback; };
const python = option('python', 'python'), chromePath = option('chrome', 'chrome'), port = Number(option('port', '8186'));
const base = `http://127.0.0.1:${port}`, out = here, debugPort = Number(option('debug-port', '9286'));
const data = JSON.parse(readFileSync(join(root, 'tests/fixtures/accounts.json'), 'utf8'));
const [buyer] = data.accounts, [home, office] = data.locations;
if (!existsSync(join(root, 'frontend/dist/index.html'))) throw new Error('Build the frontend first (npm run build in frontend/)');

const work = mkdtempSync(join(tmpdir(), 'techscout-186-'));
const databaseUrl = `sqlite:///${join(work, 'walkthrough.db').replaceAll('\\', '/')}`;
const requestsLog = join(work, 'requests.jsonl');
const env = { ...process.env, DATABASE_URL: databaseUrl, TECHSCOUT_COOKIE_SECURE: '0', EVIDENCE_DIR: join(work, 'evidence'),
  OPENROUTER_API_KEY: '', OPENAI_API_KEY: '' };
const sleep = ms => new Promise(done => setTimeout(done, ms));
async function until(check, what, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { try { if (await check()) return; } catch { /* not ready */ } await sleep(200); }
  throw new Error(`Timed out waiting for ${what}`);
}

execFileSync(python, ['-m', 'alembic', 'upgrade', 'head'], { cwd: root, env, stdio: 'inherit' });
const api = spawn(python, ['acceptance/development/issue-186/serve.py', '--database-url', databaseUrl, '--port', String(port),
  '--requests', requestsLog], { cwd: root, env, stdio: 'ignore' });
const chrome = spawn(chromePath, ['--headless=new', `--remote-debugging-port=${debugPort}`, `--user-data-dir=${join(work, 'chrome')}`,
  '--window-size=1280,1000', '--no-first-run', '--no-default-browser-check', '--disable-extensions', 'about:blank'], { stdio: 'ignore' });
// On Windows a plain kill leaves Chrome's child processes holding the debugging port.
const end = child => { try { if (process.platform === 'win32') execFileSync('taskkill', ['/F', '/T', '/PID', String(child.pid)], { stdio: 'ignore' }); else child.kill(); } catch { /* already gone */ } };
const stop = () => { end(api); end(chrome); };
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
  await sleep(300);
  const { data: png } = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(out, `${name}.png`), Buffer.from(png, 'base64'));
  return `${name}.png`;
}
async function token() {
  const { cookies } = await send('Network.getCookies', { urls: [base] });
  return cookies.find(cookie => cookie.name === 'techscout_session').value;
}
async function seed(suffix) {
  const result = execFileSync(python, ['acceptance/development/issue-186/seed_chat.py', '--database-url', databaseUrl,
    '--session-token', await token(), '--suffix', suffix], { cwd: root, env });
  return JSON.parse(result.toString());
}
const json = path => evaluate(`fetch(${JSON.stringify(path)}, {cache:'no-store'}).then(async r => ({status: r.status, body: r.headers.get('content-type')?.includes('json') ? await r.json() : await r.text()}))`);
const destination = async runId => (await json(`/api/runs/${runId}/destination`)).body;
const money = cost => !cost ? 'unknown' : cost.status === 'verified' ? `${cost.total.currency} ${cost.total.amount}`
  : cost.known_subtotal ? `${cost.known_subtotal.currency} ${cost.known_subtotal.amount} + unknown` : 'unknown';
const totals = state => Object.fromEntries((state.revised?.offers ?? []).map(offer =>
  [offer.seller, { before: money(offer.before), after: money(offer.after), rank: offer.rank ?? null }]));
const quotes = state => state.quotes.map(quote => ({ seller: quote.seller, status: quote.status,
  destination: quote.destination.detail === 'street' ? `${quote.destination.label} · ${quote.destination.city}` : quote.destination.city,
  fee: quote.fee ? `${quote.fee.label} ${quote.fee.amount.currency} ${quote.fee.amount.amount}` : null,
  quoted_total: quote.quoted_total ? `${quote.quoted_total.currency} ${quote.quoted_total.amount}` : null, reason: quote.reason ?? null }));
const requests = () => existsSync(requestsLog) ? readFileSync(requestsLog, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
const settled = runId => until(async () => ['quoted', 'partial', 'unavailable', 'not_needed'].includes((await destination(runId)).selection?.quote_state),
  'quotes to settle');
async function chooseFromChat(label, sellers) {
  for (const name of sellers) await walk(`tick(${JSON.stringify(name)})`);
  await walk(`click('.destination button', 'Choose a saved address')`);
  await until(() => visible('Choose the address for your comparison'), 'Locations in choose mode');
  return async () => {
    await evaluate(`[...document.querySelectorAll('.locations li')].find(li => li.querySelector('strong').textContent === ${JSON.stringify(label)}).querySelector('.primary').click(); true`);
  };
}
const steps = [], checks = {};
const record = async (step, extra = {}) => { steps.push({ step, url: await evaluate('location.pathname + location.search'), ...extra }); };

try {
  await until(async () => (await fetch(`${base}/health`)).ok, 'API health');
  await until(async () => (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).ok, 'Chrome');
  const browserVersion = (await (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json()).Browser;
  const target = (await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()).find(item => item.type === 'page');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  socket.onmessage = event => { const message = JSON.parse(event.data); const waiter = pending.get(message.id);
    if (waiter) { pending.delete(message.id); message.error ? waiter.fail(new Error(message.error.message)) : waiter.done(message.result); } };
  await new Promise(done => { socket.onopen = done; });
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Page.addScriptToEvaluateOnNewDocument', { source: helpers });
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });

  // A signed-in buyer with no saved address (scenario 13).
  await open('/');
  await until(() => evaluate(`!document.querySelector('.account-nav button')?.disabled`), 'account state');
  await walk(`click('button', 'Sign in or create account')`);
  await until(() => visible('Sign in to TechScout'), 'account page');
  await walk(`click('.category-row button', 'Create account')`);
  await walk(`fill('Username', ${JSON.stringify(buyer.username)})`); await walk(`fill('Password', ${JSON.stringify(buyer.password)})`);
  await walk(`fill('Confirm password', ${JSON.stringify(buyer.password)})`);
  await walk(`click('form .primary', 'Create account')`);
  await until(() => visible(`Signed in as ${buyer.username}`), 'registration');

  // 1. Before any address: City-Shop's city-level quote arrives by itself and the two sellers that
  //    need a street address are named once (scenario 11).
  const first = await seed('first');
  await open(`/?chat=${first.run_id}`);
  await until(() => visible('Totals after delivery quotes'), 'the city-level quote');
  await until(() => visible('Exact delivery totals may need your address'), 'the address prompt');
  const before = await destination(first.run_id);
  const search = (await json(`/api/runs/${first.run_id}`)).body;
  await record('city quote shown and sellers named once', { run_id: first.run_id, waiting: before.waiting.map(s => s.name),
    prompt: await walk(`lines('.destination h2, .destination > p')`), quotes: quotes(before), totals: totals(before),
    screenshot: await shot('01-before-address', '.destination') });
  await shot('01a-search-results', '.progress');

  // 2. The buyer ticks both waiting sellers, adds two locations and picks one; the same chat
  //    resumes the quotes by itself (scenarios 12-13, #187 box 2).
  let pick = await chooseFromChat(home.label, ['Street-A', 'Street-B']);
  await until(() => visible('No saved locations yet'), 'empty Locations');
  for (const location of [home, office]) {
    await walk(`fill('Label', ${JSON.stringify(location.label)})`); await walk(`fill('City in Lebanon', ${JSON.stringify(location.city)})`);
    await walk(`fill('Street address', ${JSON.stringify(location.street)})`);
    if (location.details) await walk(`fill('Building, floor (optional)', ${JSON.stringify(location.details)})`);
    await walk(`click('button', 'Save location')`);
    await until(() => visible(location.label), `saved ${location.label}`);
  }
  await record('two locations added from the chat', { screenshot: await shot('02-locations-from-chat') });
  await pick();
  await until(() => visible(`Using ${home.label} · ${home.city}`), 'return to the chat');
  await settled(first.run_id);
  await until(() => visible('Street-A has the lowest verified total'), 'revised advice on the page');
  const afterHome = await destination(first.run_id);
  await record('same chat after choosing Test home', { run_id: first.run_id, quote_state: afterHome.selection.quote_state,
    quotes: quotes(afterHome), totals: totals(afterHome), advice: afterHome.revised.advice,
    screenshot: await shot('03-after-home', '.destination') });

  // 3. Changing the destination invalidates the earlier quotes; a seller with no delivery option
  //    there stays unknown (scenarios 12 and 15).
  await walk(`click('.destination button', 'Change address or sellers')`);
  pick = await chooseFromChat(office.label, ['Street-A', 'Street-B']);
  await until(() => visible(office.label), 'saved locations listed');
  await pick();
  await until(() => visible(`Using ${office.label} · ${office.city}`), 'second selection');
  await until(async () => (await destination(first.run_id)).quotes.some(q => q.destination.label === office.label && q.status === 'quoted'), 'office quotes');
  await settled(first.run_id);
  await until(() => visible('Test office · Beirut: Standard'), 'office quote on the page');
  const afterOffice = await destination(first.run_id);
  await record('same chat after changing to Test office', { run_id: first.run_id, quote_state: afterOffice.selection.quote_state,
    quotes: quotes(afterOffice), totals: totals(afterOffice), advice: afterOffice.revised.advice,
    screenshot: await shot('04-after-office', '.destination') });

  // 4. Decline (scenario 14): nothing is sent to the street sellers; offers and links stay.
  const declined = await seed('decline');
  await open(`/?chat=${declined.run_id}`);
  await until(() => visible('Continue without an address'), 'decline option');
  await walk(`click('.destination button', 'Continue without an address')`);
  await until(() => visible('Continuing without a precise address'), 'decline recorded');
  await until(() => visible('Totals after delivery quotes'), 'the city quote in the declined chat');
  const afterDecline = await destination(declined.run_id);
  const links = await evaluate(`[...new Set([...document.querySelectorAll('a[href^="https://"]')].filter(a => /View at|↗/.test(a.textContent)).map(a => a.href))]`);
  await record('declined and kept shopping', { run_id: declined.run_id, quotes: quotes(afterDecline), totals: totals(afterDecline),
    product_links: links.length, screenshot: await shot('05-declined', '.destination') });

  const sent = requests(), history = (await json('/api/history')).body.items;
  const after = (await json(`/api/runs/${first.run_id}`)).body;
  Object.assign(checks, {
    city_quote_before_any_address: quotes(before).some(q => q.seller === 'City-Shop' && q.status === 'quoted' && q.destination === 'Beirut'),
    named_once_before_address: before.waiting.map(s => s.name),
    same_chat_and_search: after.current.run.run_id === first.run_id && JSON.stringify(after.current) === JSON.stringify(search.current),
    no_new_search_submitted: history.filter(item => item.run_id === first.run_id).length === 1 && history.length === 2,
    totals_before: totals(before), totals_after_home: totals(afterHome), totals_after_office: totals(afterOffice),
    home_quotes_invalidated_by_office: !afterOffice.quotes.some(q => q.destination.label === home.label),
    street_sent_only_to_chosen_sellers: sent.filter(r => r.street_sent).every(r => ['Street-A', 'Street-B'].includes(r.seller)),
    no_street_in_city_quotes: sent.filter(r => r.seller === 'City-Shop').every(r => !r.street_sent),
    // One city quote per chat (two chats), and each street seller once per chosen address; the
    // declined chat adds no street request.
    each_request_sent_once: JSON.stringify(sent.map(r => [r.seller, r.saved_location ?? r.city]).sort()) === JSON.stringify([
      ['City-Shop', 'Beirut'], ['City-Shop', 'Beirut'], ['Street-A', home.label], ['Street-A', office.label],
      ['Street-B', home.label], ['Street-B', office.label]].sort()),
    no_cookie_and_only_post_graphql: sent.every(r => !r.cookie_sent && r.method === 'POST' && r.path === '/api/2025-01/graphql.json'),
    retailer_requests: sent,
    declined_chat_distinct_product_links: links,
  });
  const surfaces = [await evaluate('document.body.innerText'), JSON.stringify(steps), JSON.stringify(checks),
    JSON.stringify(afterOffice), JSON.stringify((await json(`/api/history/${first.run_id}/export`)).body)];
  checks.street_absent_from_page_steps_api_and_export = data.locations.every(location =>
    surfaces.every(text => !text.includes(location.street) && !(location.details && text.includes(location.details))));
  checks.password_absent_from_evidence = !JSON.stringify({ steps, checks }).includes(buyer.password);

  const revision = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root }).toString().trim();
  writeFileSync(join(out, 'evidence.json'), JSON.stringify({
    issues: [186, 187], scenarios: [11, 12, 13, 14, 15], revision, recorded_at: new Date().toISOString(),
    source_mode: 'Offline fixtures. The real API, accounts, quote planner, quote loop, source gate and a fixture ledger ran on a fresh local SQLite database; the only fake was the network transport, a scripted Shopify Storefront for the synthetic sellers City-Shop, Street-A and Street-B (serve.py). The chats are the offline test chat (seed_chat.py). No retailer, model or provider key was contacted. The real ITWorks route stays pending its live check.',
    runtime: { api: 'acceptance/development/issue-186/serve.py on 127.0.0.1 (FastAPI app, quote loop, TECHSCOUT_COOKIE_SECURE=0, built frontend)', browser: browserVersion },
    test_data: 'Synthetic account and invented addresses from tests/fixtures/accounts.json. Streets are recorded only as their saved-location labels; passwords are not recorded.',
    steps, checks,
  }, null, 2) + '\n');
  console.log(JSON.stringify(checks, null, 2));
} finally {
  socket?.close(); stop();
  await sleep(500);
  try { rmSync(work, { recursive: true, force: true }); } catch { /* Windows may hold the database briefly */ }
}
