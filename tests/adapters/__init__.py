"""Shared helpers for the store reader tests (MVP 05/06).

A fake gate fetch serves minimized fixture bytes as FetchOutcome objects with a fixed clock and
fails any URL the test did not route. Expected URLs are owned by the tests, never read from the
adapters. Tripwires fail any real socket or DNS use.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from app.adapters.base import CandidateQuery
from app.adapters.woocommerce import FIXED_REASONS
from app.contracts import Fulfillment, Offer, ToolError
from app.sources import FetchOutcome

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures' / 'stores'
OBSERVED = datetime(2026, 11, 14, 19, 3, 47, tzinfo=timezone.utc)  # a Saturday, 21:03 in Beirut
EN_DASH = chr(0x2013)

CLASS_API = 'https://classcellulars.com/wp-json/wc/store/v1/products?slug=iphone-17'
CLASS_PAGE = 'https://classcellulars.com/product/iphone-17/'
CLASS_FAQ = 'https://classcellulars.com/faq/'
MOJI_API = 'https://mojitech.net/wp-json/wc/store/v1/products?slug=apple-iphone-17-256gb'
MOJI_PAGE = 'https://mojitech.net/shop/brands/apple-brands/apple-iphone-17-256gb/'
MOJI_REFUND = 'https://mojitech.net/refund_returns/'
BEY_API = 'https://beytech.com.lb/wp-json/wc/store/v1/products?slug=apple-iphone-17-1-sim-2-years-warranty'
BEY_PAGE = 'https://beytech.com.lb/product/apple-iphone-17-1-sim-2-years-warranty/'
BEY_WARRANTY = 'https://beytech.com.lb/warranty-terms-conditions/'
BEY_EXCHANGE = 'https://beytech.com.lb/exchange-policy-2/'

ROUTES = {
    'class_cellulars': {CLASS_API: 'class_cellulars/api.json', CLASS_PAGE: 'class_cellulars/page.html',
                        CLASS_FAQ: 'class_cellulars/faq.html'},
    'mojitech': {MOJI_API: 'mojitech/api.json', MOJI_PAGE: 'mojitech/page.html',
                 MOJI_REFUND: 'mojitech/refund_returns.html'},
    'beytech': {BEY_API: 'beytech/api.json', BEY_PAGE: 'beytech/page.html',
                BEY_WARRANTY: 'beytech/warranty.html', BEY_EXCHANGE: 'beytech/exchange.html'},
}
TRIPWIRES = ('socket.getaddrinfo', 'socket.create_connection', 'socket.socket.connect')


class UnroutedURL(BaseException):
    """A reader asked for a URL the test did not route; no `except Exception` can swallow it."""


class RealNetwork(BaseException):
    """A tripwire fired."""


def fixture(path: str) -> bytes:
    return (FIXTURES / path).read_bytes()


def routes(source: str, overrides: dict | None = None) -> dict:
    """The store's routes; overrides map a URL to a fixture path, bytes or a ToolError."""
    return {**ROUTES[source], **(overrides or {})}


class FakeFetch:
    """A gate fetch bound to one run. Each call observes one second later than the last.

    cap stands in for the gate's per-run request cap per store.
    """

    def __init__(self, table: dict, *, cap: int | None = None, observed_at: datetime = OBSERVED):
        self.table, self.cap, self.observed_at = dict(table), cap, observed_at
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, source_id, url, *, expect):
        if url not in self.table:
            raise UnroutedURL(url)
        self.calls.append((source_id, url, expect))
        if self.cap is not None and sum(1 for s, _, _ in self.calls if s == source_id) > self.cap:
            error = ToolError(code='BUDGET_EXHAUSTED', message=f'{source_id}: per-run request cap reached',
                              retryable=False)
            return FetchOutcome(source_id, url, (url,), 0, 0, (error,))
        route = self.table[url]
        if isinstance(route, ToolError):
            return FetchOutcome(source_id, url, (url,), 1, 3, (route,))
        body = route if isinstance(route, bytes) else fixture(route)
        return FetchOutcome(source_id, url, (url,), 1, 7, final_url=url, status_code=200,
                            content_type='application/json' if expect == 'json' else 'text/html', body=body,
                            response_sha256=hashlib.sha256(body).hexdigest(),
                            observed_at=self.observed_at + timedelta(seconds=len(self.calls) - 1))

    def urls(self) -> list[str]:
        return [url for _, url, _ in self.calls]


def query(mode='delivery', city='Beirut', branch_id=None, model='iPhone 17') -> CandidateQuery:
    route = Fulfillment(mode=mode, country='LB', city=city, branch_id=branch_id)
    return CandidateQuery('Apple', model, 256, route)


def reasons(offer: Offer) -> list[str]:
    """Every reason an offer states, in facts and costs."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get('state') in ('unknown', 'conflict', 'not_applicable') and node.get('reason'):
                found.append(node['reason'])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(offer.model_dump(mode='json'))
    return found


def by_id(result) -> dict[str, Offer]:
    return {offer.variant.variant_id: offer for offer in result.offers}


def evidence(offer: Offer, ids) -> list:
    table = {e.evidence_id: e for e in offer.evidence}
    return [table[i] for i in ids]


class ReaderTestCase(unittest.TestCase):
    """Fails any real network use, and checks the reader contract on every result it is shown."""

    def setUp(self):
        for target in TRIPWIRES:
            patcher = mock.patch(target, side_effect=RealNetwork(target))
            patcher.start()
            self.addCleanup(patcher.stop)

    def assertContract(self, result):
        """Offers validate and round-trip, cite returned snapshots, and state only fixed reasons."""
        snapshots = {s.sha256: s for s in result.snapshots}
        for s in result.snapshots:
            self.assertEqual(hashlib.sha256(s.body).hexdigest(), s.sha256)
        cited = set()
        for offer in result.offers:
            self.assertEqual(Offer.model_validate(offer.model_dump(mode='json')), offer)
            ids = [e.evidence_id for e in offer.evidence]
            self.assertEqual(len(ids), len(set(ids)))
            for e in offer.evidence:
                self.assertIn(e.response_sha256, snapshots, 'evidence must cite a returned snapshot')
                self.assertEqual(e.source_id, offer.source_id)
                self.assertEqual(e.adapter_version, f'{offer.source_id}/1')
            cited |= set(ids)
            self.assertLessEqual(set(reasons(offer)), FIXED_REASONS)
        self.assertEqual(result.tool_result.evidence_ids, sorted(cited))
