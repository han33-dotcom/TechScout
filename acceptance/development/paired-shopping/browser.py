"""Interact with a reviewed WooCommerce variation form in an isolated Chromium context."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import re
from urllib.parse import urlsplit

from playwright.sync_api import Error, sync_playwright

from app.accounting import BudgetDenied, Charge
from app.domestic_buyer import Listing
from app.domestic_buyer_sources import ReadResult, _ReadJob
from app.firecrawl import retrieve_page
from app.source_capabilities import CapabilityRef
from app.onboarding import canonical
from bank import digest
from selected_phone import selected_phone

FORM = 'form.variations_form'
FIELDS = 'select[name^="attribute_"]'


def options(form):
    fields = {}
    for control in form.locator(FIELDS).all():
        name = control.get_attribute('name')
        if name in fields or not re.fullmatch(r'attribute_[\w-]{1,80}', name):
            return {}
        fields[name] = {item.get_attribute('value'): item.inner_text().strip()[:100]
                        for item in control.locator('option:not([disabled])').all() if item.get_attribute('value')}
    raw = json.loads(form.get_attribute('data-product_variations') or '[]', parse_float=Decimal)
    if not isinstance(raw, list) or len(raw) > 300 or not fields:
        return {}
    rows = {}
    duplicates = set()
    for row in raw:
        attrs = row.get('attributes')
        if (not isinstance(attrs, dict) or attrs.keys() != fields.keys()
                or any(value not in fields[name] for name, value in attrs.items())
                or type(row.get('variation_id')) is not int):
            continue
        labels = [fields[name][value] for name, value in attrs.items()]
        key = digest(attrs)
        if key in rows:
            duplicates.add(key)
        rows[key] = dict(attributes=attrs, labels=labels, variant=str(row['variation_id']),
                         price=str(row['display_price']) if isinstance(row.get('display_price'), (int, Decimal)) else None,
                         stock=row.get('is_in_stock') if type(row.get('is_in_stock')) is bool else None)
    return {k: v for k, v in rows.items() if k not in duplicates}


class Session:
    def __init__(self, request, run, sources, bank, destination):
        self.request, self.run, self.sources, self.bank = request, run, sources, bank
        self.destination = destination
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.driver = self.engine = None
        self.pages, self.states, self.reads = {}, {}, {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        def close():
            if self.engine:
                self.engine.close()
            if self.driver:
                self.driver.stop()
        try:
            self.worker.submit(close).result()
        finally:
            self.worker.shutdown()

    def scoped(self, listing):
        url = listing.url
        target = canonical(url)
        if not target or urlsplit(url).query or urlsplit(url).fragment:
            return None
        sid, owner = self.sources._source(target[1])
        if (not owner or listing.source_id not in (None, sid)
                or self.request.requirements.category not in owner.entry.categories):
            return None
        try:
            policy = self.sources.gate.admitted_policy(sid)
            refs = {*owner.capabilities, owner.entry.capability}
            for cap in policy.endpoint_capabilities:
                ref = CapabilityRef(capability_id=cap.capability_id, version=cap.version)
                if (cap.operation in ('product', 'variant') and cap.expect == 'html' and cap.matches(url)
                        and (not any(refs) or ref in refs)):
                    self.sources.gate.check_scope(sid, url, expect='html', capability=ref)
                    return sid, ref
        except (ValueError, PermissionError):
            pass
        return None

    def actions(self, listing):
        scope = self.scoped(listing)
        if not scope:
            return {}
        state = self.states.get(listing.url)
        if state is None:
            return {'open': 'Open reviewed retailer variant selector on ' + listing.url}
        document = self.sources.gate.documents.get(listing.url)
        if document is None or document.response_sha256 != state['document']:
            return {'refresh': 'Refresh the reviewed retailer variant selector on ' + listing.url}
        return {digest([state['selected'], key]): 'Select ' + ', '.join(row['labels']) + ' on ' + listing.url
                for key, row in state['options'].items() if row['attributes'] != state['selected']}

    def perform(self, listing, action_id, job, *, room):
        return self.worker.submit(self._perform, listing, action_id, job, room).result()

    def _perform(self, listing, action_id, job, room):
        scope = self.scoped(listing)
        if not scope or job.cancelled or job.run_id != self.run.run_id or room() < 2:
            return listing, ReadResult(None, status='refused', reason='Retailer scope or budget unavailable')
        sid, ref = scope
        url = listing.url
        prior = self.states.get(url)
        if (action_id == 'open' and prior is None) or (action_id == 'refresh' and prior):
            args = {}
        elif prior:
            args = next((row['attributes'] for key, row in prior['options'].items()
                         if digest([prior['selected'], key]) == action_id and row['attributes'] != prior['selected']), None)
            if not args:
                return listing, ReadResult(sid, status='refused', reason='Stale selection')
        else:
            return listing, ReadResult(sid, status='refused', reason='Unknown action')
        key = dict(source=sid, url=url, prior=digest(prior) if prior else None,
                   action='select_variant' if args else action_id, arguments=args, quantity=1,
                   destination=self.destination, branch=self.request.fulfillment.branch_id)

        bounded = _ReadJob(job, room)
        outcome = self.sources.gate.documents.get(url)
        if outcome is None:
            outcome = retrieve_page(self.sources.gate, bounded, sid, url, capability=ref,
                                    fallback=self.sources.fallback)
        def capture():
            import time
            if outcome.status != 'ok' or outcome.final_url != url:
                return dict(status='failed', reason='No current reviewed product document')
            if args and prior and prior['document'] != outcome.response_sha256:
                return dict(status='failed', reason='The product document changed')
            work = bounded.reserve('work', Charge(Decimal(0)), 15 if not args else 6)
            work.dispatch()
            started = time.monotonic()
            try:
                if args:
                    state = self.select(url, args, prior)
                else:
                    state = self.open(url, outcome)
                return dict(status='ok', state=state)
            except (Error, ValueError, KeyError, TypeError, AttributeError, IndexError):
                return dict(status='failed', reason='Could not establish a unique retailer selection')
            finally:
                work.complete(Decimal(0), time.monotonic() - started)

        try:
            result = self.bank.exchange('browser', key, capture)
        except (BudgetDenied, TimeoutError):
            result = dict(status='refused', reason='Insufficient remaining allowance')
        if not result or result['status'] != 'ok':
            self.states.pop(url, None)
            reason = result['reason'] if result else 'Uncaptured browser state transition'
            self.bank.event('browser', **key, status='failed', reason=reason)
            return listing, ReadResult(sid, status='failed', reason=reason)
        state = self.states[url] = result['state']
        selected = next((r for r in state['options'].values() if r['attributes'] == state['selected']), None)
        self.bank.event('browser', **key, status='ok', before=prior['selected'] if prior else None,
                        selected=state['selected'], selected_row=selected, title=state['title'],
                        product=state['product'], source_sha256=state['document'], observed_at=state['observed_at'])
        # Only the original reader can supply purchase facts. Discard earlier offers
        # unless both the selected SKU and its exact document receipt agree.
        read = self.reads.get(url)
        matches = tuple(o for o in (read.offers if read and selected else ())
                        if o.source_variant_id == selected['variant'] and o.source_product_id == state['product']
                        and any(e.response_sha256 == state['document'] and e.run == self.run for e in o.evidence))
        if matches:
            from dataclasses import replace
            return listing, replace(read, offers=matches)
        if selected and args and sid == 'class_cellulars':
            checked = selected_phone(self.sources, self.request, self.run, url, state, selected, bounded)
            if checked:
                return listing, checked
        reason = ('Selected retailer variant ' + selected['variant'] + ' (' + ', '.join(selected['labels'])
                  + '). Payable total unresolved.' if selected else 'Retailer variant selection remains unresolved.')
        return listing, ReadResult(sid, status='failed', reason=reason)

    def open(self, url, outcome):
        if self.driver is None:
            self.driver = sync_playwright().start()
            self.engine = self.driver.chromium.launch()
            self.context = self.engine.new_context(service_workers='block', accept_downloads=False)
            self.context.route('**/*', lambda route: route.abort())
            self.context.route_web_socket('**/*', lambda socket: socket.close())
        page = self.context.new_page()
        page.set_default_timeout(1500)
        # Only the exact SourceGate response can enter Chromium. The form is the
        # retailer's actual HTML. External scripts/assets/POSTs never reach a socket.
        def document(route):
            if route.request.is_navigation_request() and route.request.method == 'GET' and route.request.url == url:
                route.fulfill(body=outcome.body, content_type='text/html', headers={
                    'Content-Security-Policy': "default-src 'none'; form-action 'none'; base-uri 'none'"})
            else:
                route.abort()
        page.route(url, document)
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=2000)
            form = page.locator(FORM)
            if form.count() != 1:
                raise ValueError('No unique variation form')
            heading = page.locator('h1.product_title')
            state = dict(product=form.get_attribute('data-product_id'), title=page.title()[:250],
                         heading=heading.inner_text().strip() if heading.count() == 1 else None,
                         options=options(form), selected=self.selected(form), document=outcome.response_sha256,
                         observed_at=outcome.observed_at.isoformat())
            if not state['options'] or not str(state['product']).isdigit():
                raise ValueError('No supported variations')
            if url in self.pages:
                self.pages[url].close()
            self.pages[url] = page
            return state
        except Exception:
            page.close()
            raise

    @staticmethod
    def selected(form):
        return {c.get_attribute('name'): c.input_value() for c in form.locator(FIELDS).all()}

    def select(self, url, args, prior):
        form = self.pages[url].locator(FORM)
        if self.selected(form) != prior['selected']:
            raise ValueError('Retailer controls changed')
        for name, value in args.items():
            form.locator(f'select[name="{name}"]').select_option(value, timeout=1000)
        selected = self.selected(form)
        if selected != args or selected == prior['selected']:
            raise ValueError('No observed state change')
        return dict(prior, selected=selected)
