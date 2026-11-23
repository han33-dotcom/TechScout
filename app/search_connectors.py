"""Read-only provider connectors for domestic discovery. No connector selects or ranks offers."""
from dataclasses import dataclass
from html.parser import HTMLParser
import math
from urllib.parse import urlsplit

from app.accounting import money
from app.provider_http import request_json
from app.source_capabilities import safe_endpoint


class SearchFailure(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


@dataclass(frozen=True)
class Hit:
    url: str
    title: str
    snippet: str = ''
    score: float | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class Response:
    hits: tuple[Hit, ...]
    request_id: str | None = None
    units: int | None = None
    valid_results: bool = True

    @classmethod
    def from_rows(cls, rows, limit, *, request_id=None, units=None, **fields):
        # A malformed lead must not hide an otherwise reported overcharge.
        try:
            hits = _results(rows, limit, **fields)
        except (TypeError, ValueError, KeyError, AttributeError):
            return cls((), request_id, units, valid_results=False)
        return cls(hits, request_id, units)


def _results(rows, limit, *, url='url', snippet='description', score=None):
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError('Invalid provider result list')
    hits = []
    for row in rows[:limit]:
        link, title = row[url], row.get('title', '')
        description = row.get(snippet) or ''
        value = row.get(score) if score else None
        if (not isinstance(link, str) or len(link) > 2048 or not isinstance(title, str)
                or len(title) > 1000 or not isinstance(description, str) or len(description) > 16000
                or (value is not None and (type(value) not in (int, float)
                                           or not -1e308 <= value <= 1e308))):
            raise ValueError('Malformed result fields')
        host = urlsplit(link).hostname
        if host and safe_endpoint(link, host):
            hits.append(Hit(link, title, description, value))
    return tuple(hits)


class PaidConnector:
    def __init__(self, key, unit_usd, *, transport=None):
        self.key = key
        self.rate = money(unit_usd)
        self.transport = transport
        if not key.strip() or not self.rate:
            raise ValueError('Provide a key and positive all-inclusive unit rate')

    def quote(self, search):
        return self.rate

    def exchange(self, method, endpoint, **kwargs):
        return request_json(method, endpoint, transport=self.transport, **kwargs)

    def bearer(self):
        return {'Authorization': 'Bearer ' + self.key}


class TavilySearch(PaidConnector):
    def fetch(self, search):
        payload = dict(query=search.query, country='lebanon', language=search.language,
                       topic='general', search_depth='basic', max_results=search.limit,
                       include_usage=True, auto_parameters=False, include_raw_content=False,
                       include_answer=False, include_images=False)
        raw, _ = self.exchange('POST', 'https://api.tavily.com/search',
                               headers=self.bearer(), payload=payload)
        return Response.from_rows(raw.get('results'), search.limit, snippet='content', score='score',
                                  request_id=raw.get('request_id'), units=raw.get('usage', {}).get('credits'))


class SerpApiSearch(PaidConnector):
    def fetch(self, search):
        raw, _ = self.exchange('GET', 'https://serpapi.com/search.json', headers={},
                               params=dict(engine='google', q=search.query, gl='lb', hl=search.language,
                                           google_domain='google.com', api_key=self.key))
        metadata = raw['search_metadata']
        if raw.get('error') or metadata['status'] != 'Success':
            raise SearchFailure('provider_error')
        # Google can return fewer, more, or no organic rows on one results page.
        return Response.from_rows(raw.get('organic_results', []), search.limit, url='link', snippet='snippet',
                                  request_id=metadata.get('id'))


class BraveSearch(PaidConnector):
    def quote(self, search):
        self._query(search)
        return self.rate

    @staticmethod
    def _query(search):
        # Brave has no Lebanon country boost. Keep the local intent in the query.
        query = search.query
        if not any(term in query.casefold() for term in ('lebanon', 'لبنان')):
            query += ' لبنان' if search.language == 'ar' else ' Lebanon'
        if len(query) > 600 or len(query.split()) > 75:
            raise SearchFailure('refused')
        return query

    def fetch(self, search):
        raw, headers = self.exchange('GET', 'https://api.search.brave.com/res/v1/web/search',
                                     headers={'X-Subscription-Token': self.key},
                                     params=dict(q=self._query(search), search_lang=search.language, country='ALL',
                                                 count=search.limit, result_filter='web'))
        if raw.get('type') != 'search' or raw.get('error'):
            raise SearchFailure('provider_error')
        return Response.from_rows((raw.get('web') or {}).get('results', []), search.limit,
                                  request_id=headers.get('x-request-id'))


class FirecrawlSearch(PaidConnector):
    def quote(self, search):
        # Basic, web-only search bills two credits per ten results or fraction thereof.
        return self.rate * 2 * math.ceil(search.limit / 10)

    def fetch(self, search):
        payload = dict(query=search.query, limit=search.limit, sources=['web'], country='LB',
                       location='Lebanon', timeout=10000, domainTools=False, highlights=False)
        raw, _ = self.exchange('POST', 'https://api.firecrawl.dev/v2/search',
                               headers=self.bearer(), payload=payload)
        if raw.get('success') is not True:
            raise SearchFailure('provider_error')
        data = raw.get('data')
        rows = data.get('web') if isinstance(data, dict) else None
        return Response.from_rows(rows, search.limit, score='score', request_id=raw.get('id'),
                                  units=raw.get('creditsUsed'))


@dataclass(frozen=True)
class DirectoryPage:
    brand: str
    source_id: str
    url: str
    section_id: str
    link_class: str

    def __post_init__(self):
        host = urlsplit(self.url).hostname
        if (not all(isinstance(v, str) and v.strip() for v in
                    (self.brand, self.source_id, self.section_id, self.link_class))
                or not host or not safe_endpoint(self.url, host)):
            raise ValueError('Configure an exact directory URL, source, domestic section and retailer link class')


class _DirectoryLinks(HTMLParser):
    """Accept only shop anchors inside the configured domestic retailer section."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
            'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, page, limit):
        super().__init__()
        self.page, self.limit = page, limit
        self.stack = []
        self.active = None
        self.title = []
        self.hits = []
        self.matched_section = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.VOID:
            return
        if not self.stack and attrs.get('id') != self.page.section_id:
            return
        self.matched_section = True
        if len(self.stack) >= 128:
            raise SearchFailure('invalid_response')
        self.stack.append(tag)
        if tag == 'a':
            self.active = None
            link = attrs.get('href') or ''
            if self.page.link_class in (attrs.get('class') or '').split() and len(link) <= 2048:
                host = urlsplit(link).hostname
                if host and host != urlsplit(self.page.url).hostname and safe_endpoint(link, host):
                    self.active, self.title = link, []

    def handle_data(self, data):
        if self.active:
            self.title.append(data)

    def handle_endtag(self, tag):
        if not self.stack or tag not in self.stack:
            return
        if tag == 'a' and self.active:
            title = ' '.join(''.join(self.title).split())[:1000]
            if len(self.hits) < self.limit:
                self.hits.append(Hit(self.active, title, source_url=self.page.url))
            self.active = None
        while self.stack.pop() != tag:
            pass
        if 'a' not in self.stack:
            self.active = None


class ManufacturerDirectories:
    def __init__(self, gate, directory, pages):
        if not 1 <= len(pages) <= 10:
            raise ValueError('Configure 1–10 reviewed manufacturer directory pages')
        self.gate, self.registry_path, self.pages = gate, directory.registry_path, tuple(pages)

    def fetch(self, search, job, cursor):
        matches = [p for p in self.pages if p.brand.casefold() in search.query.casefold()]
        pages = matches or self.pages
        page = pages[cursor % len(pages)]
        outcome = self.gate.fetch(job.source_budget(self.registry_path), page.source_id, page.url, expect='html')
        if outcome.status != 'ok' or outcome.final_url != page.url:
            codes = {error.code for error in outcome.errors}
            if 'BUDGET_EXHAUSTED' in codes:
                raise SearchFailure('budget_denied')
            if 'RATE_LIMIT' in codes:
                raise SearchFailure('rate_limited')
            raise SearchFailure('refused' if not outcome.attempts else 'provider_error')
        parser = _DirectoryLinks(page, search.limit)
        parser.feed(outcome.body.decode('utf-8'))
        parser.close()
        if not parser.matched_section:
            raise SearchFailure('invalid_response')
        return Response(tuple(parser.hits))
