"""Direct public listing reads for the two-agent experiment."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os

import httpx2
from pydantic import Field

from app.accounting import BudgetDenied, SourceBudget
from app.contracts import Model
from app.firecrawl import EmptyListing, Firecrawl, ManagedPage
from app.model_provider import JobBudget
from app.listing_browser import select_listing
from app.page_observation import PageNavigation, inspect_capture
from app.selected_listing import Selection, with_option
from app.onboarding import ActivationRegistry, PROBE_TTL, ProbeRegistry, canonical, source_id_for
from app.sources import SourceGate


class FrozenInspectionRegistry(ActivationRegistry):
    """Use the evaluation's pinned directory without overlaying activated database rows."""
    def read_registry(self):
        return self._base()


class _ListingScope(ProbeRegistry):
    """One public document. Carry existing shop limits and stops into each gate check."""

    def __init__(self, registry, shop_key, host, url):
        self.registry = registry
        self.shop_key = shop_key
        entry = self._entry()
        super().__init__(source_id=entry['id'] if entry else source_id_for(shop_key), name=host,
                         origin=f'https://{host}', targets=(url,),
                         expires_at=datetime.now(timezone.utc) + PROBE_TTL)

    def _entry(self):
        document = self.registry.read_registry()
        owner = self.registry.reviewed(self.shop_key, self.registry.reviewed_hosts(document))
        if owner is None:
            return None
        if owner.status != 'pending' and (owner.status != 'active' or not owner.admitted):
            raise ValueError('Retailer inspection is disabled')
        return next(entry for entry in document['sources'] if entry['id'] == owner.source_id)

    def read_registry(self):
        document = super().read_registry()
        entry = self._entry()
        current_id = entry['id'] if entry else source_id_for(self.shop_key)
        if current_id != self.source_id:
            raise ValueError('Retailer identity changed during inspection')
        if entry:
            limits = document['sources'][0]['limits']
            for key in limits:
                value = entry['limits'][key]
                if entry['status'] == 'active' or value > 0:
                    limits[key] = min(limits[key], value)
        return document


def inspect_public_listing(job, url: str, registry: ActivationRegistry, *, environ=None, browser=None) -> ManagedPage:
    """Capture one exact public URL for a buyer or catalog job. Seller identity is assessed later."""
    target = canonical(url)
    if target is None:
        raise ValueError('Expected a public HTTPS listing URL')
    shop_key, host = target
    scope = _ListingScope(registry, shop_key, host, url)
    try:
        if browser is not None:
            def read(target_url, expect):
                destination = canonical(target_url)
                if destination != target or (expect == 'html' and target_url != url):
                    raise ValueError('Browser resource outside the selected listing origin.')
                resource = _ListingScope(registry, shop_key, host, target_url)
                try:
                    return SourceGate(resource).fetch(SourceBudget(job, resource, international=False), resource.source_id,
                                                      target_url, expect=expect)
                finally:
                    resource.close()
            return select_listing(job, url, browser, read)
        provider = Firecrawl.from_env(SourceGate(scope), os.environ if environ is None else environ)
        if provider is None:
            raise ValueError('Configure Firecrawl and its credit allowance for listing inspection')
        def resolve_landing(final_url):
            destination = canonical(final_url)
            if destination is None:
                raise ValueError('Expected a public HTTPS landing page')
            landing_scope = _ListingScope(registry, *destination, final_url)
            try:
                limits = SourceGate(landing_scope).check_provider_target(landing_scope.source_id, final_url, expect='html')
                return landing_scope.source_id, limits
            finally:
                landing_scope.close()

        return provider.scrape(job, scope.source_id, url, resolve_landing=resolve_landing)
    finally:
        scope.close()


class InspectionChoice(Model):
    action_index: int = Field(ge=0, strict=True)


@dataclass(frozen=True)
class CatalogCapture(ManagedPage):
    selection: Selection | None = None
    reason: str = ''
    findings: tuple = ()


def recoverable_read_error(exc):
    if isinstance(exc, httpx2.HTTPStatusError):
        return exc.response.status_code in (502, 503, 504)
    return isinstance(exc, (EmptyListing, TimeoutError, httpx2.TimeoutException, httpx2.NetworkError))


INSPECTION_POLICY = (
    'Read every new listing with Firecrawl first. Stop inspecting once you have the evidence needed for '
    'the objective. If an ordinary retailer link can supply missing evidence, you may read it with Firecrawl. '
    'When you judge further Firecrawl reads unlikely to help, choose Playwright to render the listing or '
    'select an observed product option. You decide which path to use. Browser availability alone is no '
    'reason to use it. Never treat an unavailable field or a default sibling as verified evidence.')


def read_catalog_listing(inspect, job, url, *, objective, roles, trace=None):
    """Give the catalog agent the evidence and an indexed menu after each capture."""
    browser = None
    attempted = set()
    last_capture = None
    while not job.cancelled:
        attempted.add((url, browser))
        try:
            page = inspect(job, url, browser=browser)
            if page.outcome.final_url != url:
                if trace is not None:
                    trace.append(dict(reader=page.provider, requested_url=url, final_url=page.outcome.final_url,
                                      sha256=page.outcome.response_sha256))
                url = page.outcome.final_url
                attempted.add((url, browser))
        except (OSError, ValueError, httpx2.HTTPError, BudgetDenied) as exc:
            if trace is not None:
                trace.append(dict(reader='playwright' if browser is not None else 'firecrawl-v2',
                                  status='read_failed', error=type(exc).__name__))
            if browser is not None or not recoverable_read_error(exc):
                if last_capture is not None:
                    break
                raise
            page = None
        document = PageNavigation(page.outcome.body.decode('utf-8', errors='replace') if page else '', url)
        reading = None
        if page:
            last_capture = CatalogCapture(page.outcome, page.provider, page.request_id)
        if roles is None:
            if trace is not None:
                trace.append(dict(status='inference_unavailable'))
            break

        def generate(instructions, data, schema):
            reply = roles.generate(instructions, data, schema, budget=JobBudget(job, seconds=roles.settings.timeout_seconds),
                                   cancelled=lambda: job.cancelled)
            return reply.value if reply.status == 'completed' else None

        if page:
            reading = inspect_capture(page, objective, generate, checked=browser or ())
            last_capture = replace(last_capture, selection=reading.selection, reason=reading.reason, findings=reading.findings)
        moves = [('Finish with current evidence', None)]
        paths = [('Render this listing with Playwright', (url, browser or ()))]
        for option in tuple(dict.fromkeys(document.options))[:32]:
            if option not in document.checked:
                paths.append((f'Select {option.value} ({option.dimension}) with Playwright',
                              (url, with_option(browser or (), option))))
        links = document.suggestions()
        for link, label in (*(reading.links if reading else ()), *links):
            paths.append((f'Read {label} at {link} with Firecrawl', (link, None)))
        moves.extend((label, move) for label, move in dict.fromkeys(paths) if move not in attempted)
        if len(moves) == 1:
            break
        choice = generate(INSPECTION_POLICY + ' Choose one zero-based action_index.',
            dict(objective=objective, url=url, reader=page.provider if page else 'Firecrawl returned no readable page',
                 selected=reading.selection.facts() if reading and reading.selection else None,
                 reason=reading.reason if reading else None, findings=reading.findings if reading else (),
                 available=[label for label, _ in moves]), InspectionChoice)
        if not isinstance(choice, InspectionChoice) or choice.action_index >= len(moves):
            if trace is not None:
                trace.append(dict(status='inference_unavailable' if choice is None else 'invalid_choice'))
            break
        next_move = moves[choice.action_index][1]
        if trace is not None:
            trace.append(dict(reader=page.provider if page else 'firecrawl-v2', action=moves[choice.action_index][0],
                              sha256=page.outcome.response_sha256 if page else None))
        if trace is not None and reading:
            trace[-1].update(selected=reading.selection.facts() if reading.selection else None, reason=reading.reason)
        if next_move is None:
            break
        url, browser = next_move
    if last_capture is None:
        raise ValueError('The catalog could not read the requested listing.')
    return last_capture
