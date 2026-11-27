"""Explicit, metered single-page capture."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import time
from uuid import UUID
import httpx2

from app.accounting import Charge, BudgetDenied, money
from app.provider_http import post_json, request_json
from app.sources import FetchOutcome, MAX_RESPONSE_BYTES


@dataclass(frozen=True)
class ManagedPage:
    outcome: FetchOutcome
    provider: str
    request_id: str | None
    max_age_ms: int = 0


class EmptyListing(ValueError):
    """Firecrawl confirmed the public page but returned no readable HTML."""


API = 'https://api.firecrawl.dev/v2/'
# The agent pages through the decoded capture. This bound applies to raw HTML,
# separately from JSON transfer overhead or the model's input window.
AGENT_HTML_BYTES = 8 * 1024 * 1024


def _utc_timestamp(value):
    stamp = datetime.fromisoformat(value)
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


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

    def _archive_get(self, job, path, params, deadline, max_bytes):
        seconds = min(deadline - time.monotonic(), job.room()) - 2
        if seconds < 3 or job.cancelled:
            raise TimeoutError('No time remains to recover the Firecrawl result')
        attempt = job.reserve('provider_read', Charge(Decimal(0)), seconds)
        attempt.dispatch()
        started = time.monotonic()
        try:
            idle = min(12, seconds / 3)
            data, _ = request_json('GET', API + path, headers={'Authorization': 'Bearer ' + self.api_key},
                params=params, transport=self.transport, seconds=seconds-idle, idle_seconds=idle, max_bytes=max_bytes)
            return data
        finally:
            try:
                attempt.complete(Decimal(0), time.monotonic()-started)
            except BudgetDenied:
                job.ledger.recover()
                raise

    def _recover(self, job, url, submitted, interrupted, deadline, wire_bytes):
        # The sync endpoint can lose its response before it supplies a job ID.
        # Recover only a unique dispatch for this exact URL in this call's window.
        matches = set()
        params = {'endpoint': 'scrape', 'limit': 100}
        while True:
            activity = self._archive_get(job, 'team/activity', params, deadline, 1048576)
            rows = activity.get('data')
            if activity.get('success') is not True or not isinstance(rows, list):
                raise TimeoutError('Firecrawl job history was unavailable for the interrupted request')
            if not rows:
                break
            try:
                times = [_utc_timestamp(row['created_at']) for row in rows]
                matches.update(row['id'] for row, at in zip(rows, times) if submitted <= at <= interrupted
                               and row['endpoint'] == 'scrape' and row.get('target') == url)
            except (KeyError, TypeError, ValueError) as exc:
                raise TimeoutError('Firecrawl job history could not identify the interrupted request') from exc
            if min(times) < submitted or not activity.get('has_more'):
                break
            cursor = activity.get('cursor')
            if not cursor or cursor == params.get('cursor'):
                raise TimeoutError('Firecrawl job history could not identify the interrupted request')
            params['cursor'] = cursor
        if len(matches) != 1:
            raise TimeoutError('Firecrawl job history did not identify one result for this request')
        job_id = str(UUID(matches.pop()))
        result = self._archive_get(job, 'scrape/' + job_id, None, deadline, wire_bytes)
        if (result.get('data') or {}).get('metadata', {}).get('scrapeId') != job_id:
            raise ValueError('Firecrawl returned a different stored job')
        return result

    def scrape(self, job, source_id, url, *, capability=None, international=False, resolve_landing=None,
               agent_read=False):
        limits = self.gate.check_provider_target(source_id,url,expect='html',capability=capability)
        lease = min(115, job.room()-2) if agent_read else 20
        if lease < 5:
            raise TimeoutError('No time remains for the listing read')
        html_bytes = AGENT_HTML_BYTES if agent_read else MAX_RESPONSE_BYTES
        wire_bytes = html_bytes + (html_bytes // 2 if agent_read else 262144)
        attempt = job.reserve('retrieval',Charge(self.rate),lease,source=source_id,limits=limits,
                              international=international,resource=url)
        payload = dict(url=url,formats=['rawHtml'],onlyMainContent=False,maxAge=0,storeInCache=False,
            timeout=10000,proxy='basic',skipTlsVerification=False,parsers=[],blockAds=True)
        started = time.monotonic()
        attempt.dispatch()
        submitted = datetime.now(timezone.utc)
        recovered = False
        try:
            try:
                idle = min(12, lease / 4) if agent_read else 5
                # A progressing transfer can use its read budget. Interruptions, such as
                # an idle socket, leave the remaining time for recovering the stored job.
                raw = post_json(API+'scrape',self.api_key,payload,transport=self.transport,
                                seconds=lease-idle-2 if agent_read else 15,
                                idle_seconds=idle, max_bytes=wire_bytes)
            except (TimeoutError, httpx2.TimeoutException, httpx2.RemoteProtocolError):
                if not agent_read:
                    raise
                raw = self._recover(job, url, submitted, datetime.now(timezone.utc), started+lease-2, wire_bytes)
                recovered = True
            if raw.get('success') is not True:
                raise ValueError('Managed extraction did not succeed')
            data = raw['data']
            metadata = data['metadata']
            final_url = metadata.get('url')
            if (metadata.get('sourceURL') != url or type(final_url) is not str or not final_url
                or metadata.get('statusCode') != 200 or metadata.get('error')
                or metadata.get('contentType','').split(';')[0].lower() != 'text/html'
                or metadata.get('cacheState') in {'hit','cached'} or metadata.get('cachedAt')
                or data.get('warning')):
                raise ValueError('Managed page provenance, freshness or status is not established')
            self.gate.check_provider_target(source_id,url,expect='html',capability=capability)
            landing_source = source_id
            if final_url != url:
                if resolve_landing is None:
                    raise ValueError('Managed page changed the configured target')
                landing_source, landing_limits = resolve_landing(final_url)
                if landing_source != source_id:
                    attempt.record_landing(landing_source, landing_limits)
            html = data.get('rawHtml')
            if html is None or isinstance(html, str) and not html.strip():
                raise EmptyListing('Managed HTML is absent from the confirmed page')
            if not isinstance(html,str) or len(html.encode('utf-8')) > html_bytes:
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
        # Firecrawl reports the two endpoints, without intermediate hops.
        endpoints = (url,) if url == final_url else (url, final_url)
        outcome = FetchOutcome(landing_source,url,endpoints,1,int((time.monotonic()-started)*1000),
            final_url=final_url,status_code=200,content_type='text/html',body=body,
            response_sha256=sha256(body).hexdigest(),observed_at=submitted if recovered else datetime.now(timezone.utc))
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
