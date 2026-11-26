"""Explicit, metered single-page capture with optional observed color selection."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
import time

from app.accounting import Charge, BudgetDenied, money
from app.provider_http import post_json
from app.sources import FetchOutcome, MAX_RESPONSE_BYTES


@dataclass(frozen=True)
class ManagedPage:
    outcome: FetchOutcome
    provider: str
    request_id: str | None
    max_age_ms: int = 0


@dataclass(frozen=True)
class ColorOption:
    """A single product color radio observed on the listing. No free-form selector."""
    name: str
    value: str

    def selector(self):
        if (not re.fullmatch(r'(?:opt\[colou?r\]|colou?r)', self.name, re.I)
                or not re.fullmatch(r'[\w -]{1,60}', self.value)):
            raise ValueError('Unsupported product color control')
        return f'input[type="radio"][name="{self.name}"][value="{self.value}"]'


class Firecrawl:
    def __init__(self, api_key, gate, *, usd_per_credit, transport=None):
        included = usd_per_credit == 'included'
        rate = Decimal(0) if included else money(usd_per_credit)
        if not api_key.strip() or (not included and not rate):
            raise ValueError('Explicit key and positive rate or confirmed included quota required')
        self.api_key, self.gate, self.rate, self.transport = api_key, gate, rate, transport

    @classmethod
    def from_env(cls, gate, env):
        key = env.get('FIRECRAWL_API_KEY')
        rate = env.get('FIRECRAWL_SCRAPE_USD_PER_CREDIT')
        if not key or not rate:
            return None
        try:
            return cls(key, gate, usd_per_credit=rate if rate == 'included' else Decimal(rate))
        except (ValueError, InvalidOperation):
            return None

    def scrape(self, job, source_id, url, *, capability=None, international=False, color: ColorOption | None = None):
        selector = color.selector() if color else None
        limits = self.gate.check_provider_target(source_id,url,expect='html',capability=capability)
        attempt = job.reserve('retrieval',Charge(self.rate),20,source=source_id,limits=limits,
                              international=international)
        payload = dict(url=url,formats=['rawHtml'],onlyMainContent=False,maxAge=0,storeInCache=False,
            timeout=10000,proxy='basic',skipTlsVerification=False,parsers=[],blockAds=True)
        if selector:
            payload['actions'] = [dict(type='click', selector=selector), dict(type='wait', milliseconds=500)]
        started = time.monotonic()
        attempt.dispatch()
        try:
            raw = post_json('https://api.firecrawl.dev/v2/scrape',self.api_key,payload,transport=self.transport,
                            seconds=15,max_bytes=MAX_RESPONSE_BYTES+262144)
            if raw.get('success') is not True:
                raise ValueError('Managed extraction did not succeed')
            data = raw['data']
            metadata = data['metadata']
            # No inferred original URL, and no redirect acceptance based solely
            # on provider text. This fallback intentionally accepts exact targets.
            if (metadata.get('sourceURL') != url or metadata.get('url') != url
                or metadata.get('statusCode') != 200 or metadata.get('error')
                or metadata.get('contentType','').split(';')[0].lower() != 'text/html'
                or metadata.get('cacheState') in {'hit','cached'} or metadata.get('cachedAt')
                or data.get('warning')):
                raise ValueError('Managed page provenance, freshness or status is not established')
            self.gate.check_provider_target(source_id,url,expect='html',capability=capability)
            html = data.get('rawHtml')
            if not isinstance(html,str) or not html.strip() or len(html.encode('utf-8')) > MAX_RESPONSE_BYTES:
                raise ValueError('Managed HTML is absent or exceeds the source body bound')
            credits = metadata.get('provider',{}).get('creditsCost')
            if type(credits) is int and credits > 1 and self.rate:
                attempt.complete(self.rate*credits,time.monotonic()-started)
            if credits is not None and (type(credits) is not int or credits != 1):
                raise ValueError('Invalid provider usage')
            # Older responses do not expose usage. Keep the entire quote in that
            # case instead of reporting a measured zero cost.
        except Exception:
            try:
                attempt.uncertain()
            except BudgetDenied:
                job.ledger.recover()
            raise
        if credits is None:
            attempt.uncertain()
        else:
            attempt.complete(self.rate,time.monotonic()-started)
        body = html.encode('utf-8')
        outcome = FetchOutcome(source_id,url,(url,),1,int((time.monotonic()-started)*1000),
            final_url=url,status_code=200,content_type='text/html',body=body,
            response_sha256=sha256(body).hexdigest(),observed_at=datetime.now(timezone.utc))
        return ManagedPage(outcome,'firecrawl-v2',metadata.get('scrapeId'))


def retrieve_page(gate, job, source_id, url, *, capability=None, fallback=None, international=False):
    """Direct gate first. Fallback cannot override an admission/budget refusal."""
    direct = gate.fetch(job.source_budget(gate._registry_path,international=international),
                        source_id,url,expect='html',capability=capability)
    if direct.status == 'ok' or fallback is None:
        return direct
    if not direct.attempts or any(e.code in {'BLOCKED','SOURCE_DISABLED','BUDGET_EXHAUSTED','RATE_LIMIT'}
            or any(token in e.message for token in ('http:401','http:404','http:410')) for e in direct.errors):
        return direct
    return fallback.scrape(job,source_id,url,capability=capability,international=international).outcome
