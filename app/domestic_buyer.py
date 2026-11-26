"""Catalog-first domestic buyer controller. Listings and search hits are leads until rechecked."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from threading import Event
from urllib.parse import urlsplit
import re

from app import contracts_v2 as v2
from app.accounting import BudgetDenied, SourceBudget
from app.assessment import FullProductAssessment, assess_full_product, shortlist
from app.buyer_flow import MAX_UNCERTAIN, MIN_WRITER_SECONDS, _Flow, supported
from app.buyer_sources import FETCH_ROOM_S
from app.directory import DirectoryQuery
from app.domestic_discovery import CHANNELS
from app.domestic_search_plan import choose_search, retailer_query, search_options
from app.eligibility import check_full_product
from app.evidence_critique import CritiqueChoice, PROMPT as EVALUATE
from app.freshness import FreshnessContext, assess_evidence
from app.identity import changed_listing_configuration, conflicting_configurations, known_model, model_identity
from app.model_provider import JobBudget
from app.onboarding import canonical
from app.orchestrator import select_candidates
from app.research import research


@dataclass(frozen=True)
class Listing:
    url: str
    source_id: str | None = None
    earlier: v2.Offer | None = None


class PhaseAttempt:
    """A reservation acquired before a phase ended must still be wanted at dispatch."""
    def __init__(self, attempt, phase):
        self.attempt, self.phase = attempt, phase

    def __getattr__(self, name):
        return getattr(self.attempt, name)

    def dispatch(self):
        if self.phase.cancelled:
            raise BudgetDenied('The research objective stopped before dispatch')
        return self.attempt.dispatch()


class PhaseJob:
    """Stop a timed-out research batch without cancelling the buyer's verification budget."""
    def __init__(self, job, room):
        self.job, self.room = job, room
        self.run_id, self.ledger = job.run_id, job.ledger
        self.stop = Event()

    def cancel(self):
        self.stop.set()

    @property
    def cancelled(self):
        return self.stop.is_set() or self.room() <= 0 or self.job.cancelled

    def reserve(self, kind, charge, seconds, **kwargs):
        if self.cancelled or seconds > self.room():
            raise BudgetDenied('The research objective has reached its deadline')
        return PhaseAttempt(self.job.reserve(kind, charge, seconds, **kwargs), self)

    def source_budget(self, registry_path, *, international=False):
        return SourceBudget(self, registry_path, international=international)


def configuration(offer):
    return offer.variant.product.product_id, offer.variant.variant_id


SELLER_GAP = 'The actual Lebanese seller is unverified for this listing.'
SERVICE_GAP = 'Delivery of this item to the requested destination is unverified.'


def candidate(request, offer):
    """A past price, availability or surcharge is never a permanent exclusion."""
    if not offer.locally_fulfillable(allow_unverified_seller=True):
        return False
    if request.intent.kind == 'product_link' and offer.listing_url != request.intent.url:
        return False
    checked = check_full_product(request, offer)
    return not any(c.outcome == 'fail' and (c.name in {'category', 'model', 'manufacturer', 'condition', 'color'}
                   or any(p.startswith('variant.specs.') for p in c.field_paths)) for c in checked.checks)


def domestic_assessment(request, offer, now, *, estimates=None):
    """The seller rule (#185): warehouse location is irrelevant, the actual merchant is not."""
    assessment = assess_full_product(request, offer, now=now, estimates=estimates)
    if not isinstance(offer.route, v2.DirectRoute) or offer.domestic_seller() is not None:
        return assessment
    # Keep the listed price and published charges as a partial. An unidentified merchant
    # never yields a verified or estimated payable total.
    cost = assessment.cost
    if cost.status == 'verified':
        subtotal, missing = cost.total, ()
    elif cost.status == 'incomplete':
        subtotal, missing = cost.known_subtotal, cost.missing
    else:
        subtotal, missing = None, ('payable total is only estimated',)
    return FullProductAssessment(offer_id=offer.offer_id, eligibility=assessment.eligibility,
        cost=v2.IncompleteCost(status='incomplete', known_subtotal=subtotal,
                               missing=tuple(dict.fromkeys((SELLER_GAP, *missing)))),
        within_maximum=None, route_support=assessment.route_support)


def current_problem(offer, assessment, read, run, started_at, now):
    """Bind decisive receipts to real responses for this selected item and purchase route."""
    records = {e.evidence_id: e for e in offer.evidence}

    def current(record):
        return (record.run == run and started_at <= record.observed_at <= now and not record.conflicts
                and (record.response_sha256, record.observed_at) in read.replies)

    def selected(record):
        return (record.product_id == offer.source_product_id and record.variant_id == offer.source_variant_id
                and record.origin_url == offer.listing_url)

    def item_route(record):
        return selected(record) and record.scope == 'route' and record.route_id == offer.route.route_id

    for label, fact in (('Price', offer.price), ('Stock', offer.stock), ('Purchase availability', offer.purchasable)):
        if fact.state != 'known' or not fact.evidence_ids or not all(
                current(records[e]) and selected(records[e]) for e in fact.evidence_ids):
            return f'{label} lacks a current response for this exact configuration.'
    if isinstance(offer.route, v2.DirectRoute):
        # Seller rule (#185). A named merchant needs its listing receipts in this run. A
        # first-party storefront is bound to the item by the current selected-listing
        # receipts checked above. Dispatch origin and warehouse are not consulted.
        seller = offer.domestic_seller()
        if seller is None or seller == 'listing' and not all(
                current(e) for e in offer.domestic_merchant_receipts()):
            return SELLER_GAP
        eligibility = offer.route.item_eligible
        if eligibility.value is not True or not all(current(records[i]) and item_route(records[i])
                                                    for i in eligibility.evidence_ids):
            return SERVICE_GAP
    if not isinstance(offer.route, (v2.DirectRoute, v2.PickupRoute)):
        return 'A domestic purchase route is unverified.'
    if any(records[i].conflicts for check in assessment.eligibility.checks for i in check.evidence_ids):
        return 'The source contains conflicting evidence for a required purchase fact.'
    if offer.condition.state == 'known' and not all(current(records[i]) for i in offer.condition.evidence_ids):
        return 'Condition lacks current evidence for this configuration.'
    for charge in offer.costs:
        if charge.state == 'unknown' or not all(current(records[i]) for i in charge.evidence_ids):
            return 'A mandatory charge lacks current evidence for the purchase route.'
    return None


def supporting_claims(offers, now, targets):
    claims = []
    for offer in offers:
        records = {e.evidence_id: e for e in offer.evidence}
        context = FreshnessContext(now=now, product_id=offer.source_product_id, variant_id=offer.source_variant_id)
        for name in type(offer.variant.specs).model_fields:
            fact = getattr(offer.variant.specs, name)
            if getattr(fact, 'state', None) != 'known' or not fact.evidence_ids:
                continue
            if all(assess_evidence(records[i], context).reusable for i in fact.evidence_ids):
                claims.append(v2.FindingClaim(target='variant', target_id=targets[offer.offer_id],
                    field_path=f'variant.specs.{name}', fact=fact.model_dump(mode='json')))
    return claims


class DomesticBuyerFlow(_Flow):
    def __init__(self, context, deps):
        super().__init__(context, deps)
        self.verification_by = self.final_by - MIN_WRITER_SECONDS - 1
        self.read_by = self.verification_by - (8 if self.request.intent.kind == 'needs' else 0)
        self.nonfinal_by = max(0, min(self.nonfinal_by, self.final_by - 30, self.read_by - FETCH_ROOM_S - 2))
        if deps.research_parallelism not in (1, 2, 3):
            raise ValueError('Use one to three independent researchers')
        self.trace['limits'].update(nonfinal_by_s=self.nonfinal_by, concurrent_workers=deps.research_parallelism, recommendation_groups=3)
        self.trace['limits'].pop('finalists')
        self.trace['controller'] = 'domestic'
        self.trace['actions'] = []
        self.catalog = {}
        self.sources = {}
        self.unknown = set()
        self.assessed, self.uncertain = {}, {}
        self.reread = set()
        self.search_report = None
        self.checked = {}
        self.retries = []

    def action(self, missing, effect, source):
        self.trace['actions'].append(dict(missing_fact=missing, decision_effect=effect, source=source))

    def _run(self):
        request = self.request
        self.publish(status='running', phase='interpret')
        self.phase('interpret', intent=request.intent.kind, market='LB')
        self.job = self.deps.ledger.open_job(self.run_ref.run_id)
        self.publish(status='running', phase='discover')
        sources = self.deps.domestic_sources
        if request.intent.kind == 'needs' and hasattr(type(sources), 'needs_lookup'):
            initial, semantic_hits = sources.needs_lookup(request, PhaseJob(self.job, lambda: self.left(self.nonfinal_by)))
            self.trace['catalog_semantic_hits'] = semantic_hits
        else:
            initial = sources.lookup(request, limit=20)
        self.remember(initial)
        self.initial_ids = set(self.catalog)
        self.phase('discover', catalog_offers=len(self.catalog))
        provisional = []
        for offer in self.catalog.values():
            dates = [e.observed_at for e in offer.evidence if e.evidence_id in offer.price.evidence_ids
                     and e.observed_at <= self.deps.wall_clock()]
            if dates:
                provisional.append(v2.ProvisionalCandidate(offer=offer, observed_at=min(dates)))
        self.update_coverage()
        from app.maintenance import signal
        signal(self.deps.ledger, request.requirements.category, gap=not self.enough(self.catalog.values()),
               catalog_urls=(offer.listing_url for offer in self.catalog.values()))
        self.publish(status='running', phase='research', coverage=self.coverage, source_outcomes=self.outcomes,
                     provisional=tuple(provisional[:MAX_UNCERTAIN]))
        if request.intent.kind != 'product_link' and not self.enough(self.catalog.values()):
            self.find_candidates()
        self.phase('research', candidates=len(self.catalog), handed_off=len(self.unknown))
        self.publish(status='running', phase='evaluate')
        self.phase('evaluate', decision='current payable total required')
        self.job.enter_final_phase()
        self.publish(status='running', phase='verify')
        pending = self.listings()
        if request.intent.kind == 'product_link':
            pending = [Listing(str(request.intent.url))]  # A held SKU cannot select a variant on the buyer's behalf.
        self.verify_listings(pending)
        if not self.catalog:
            self.read_exact_phone_leads()
        self.evaluator_follow_up()
        rows = list(self.assessed.values())
        budget = request.budget
        currency = budget.maximum.currency if budget and budget.maximum else None
        ranked = shortlist(((o, a) for o, a, _ in rows), currency=currency)
        self.uncertain.update((u.offer.offer_id, u) for u in ranked.uncertain)
        verified = {o.offer_id: v for o, _, v in rows}
        items = [verified[o.offer_id] for o, _ in ranked.primary]
        if request.intent.kind == 'needs':
            items = self.select_fit(items)
        groups = self.group(items)
        self.phase('verify', recommendations=len(groups), rechecked=len(self.reread))
        self.update_coverage(groups)
        self.trace['verify'] = dict(rechecked=len(self.reread), verified=[v.offer.offer_id for g in groups for v in g.offers()],
                                    uncertain=list(self.uncertain), independent_sellers_checked=len(set(self.checked.values())))
        self.trace['source'] = dict(outcomes=[o.model_dump() for o in self.outcomes], coverage=self.coverage.model_dump())
        if self.search_report:
            finalists = [v for group in groups for v in group.offers()]
            self.trace['discovery_channels'] = [dict(attempts=[asdict(a) for a in self.search_report.attempts],
                coverage=self.search_report.coverage(finalists, baseline=[
                    v for v in finalists if v.offer.offer_id in self.initial_ids]))]
        uncertain = tuple(self.uncertain.values())[:MAX_UNCERTAIN]
        if self.model_writer and self.deps.inference is not None:
            self.action('A buyer-readable comparison of checked results', 'Explain qualifying offers and coverage',
                        'OpenRouter writer')
        explanation = self._write(groups, uncertain)
        checked = len(set(self.checked.values()))
        count = sum(len(g.offers()) for g in groups)
        reason = f'Checked {checked} independent domestic seller(s). {count} offer(s) qualified.'
        if request.intent.kind == 'exact_model' and count < 2:
            reason += ' A two-seller comparison of verified payable totals was unavailable.'
        if not groups and request.intent.kind == 'product_link':
            failed = next((o.reason for o in self.outcomes if o.status == 'failed' and o.reason), None)
            if failed:
                reason += ' ' + failed
        if self.unknown:
            reason += ' New retailer leads were handed to background onboarding.'
        if self._limit():
            reason += f' Stopped at the {self._limit()} limit.'
        if not groups and not self.unknown and not self.catalog and not uncertain:
            reason += ' No in-scope listing could be verified.'
        status = ('partial' if self._limit() else 'complete') if groups else (
            'partial' if self._limit() else 'abstained')
        return self._finish(status, reason, provisional=(), recommendations=groups, uncertain=uncertain,
                            explanation=explanation)

    def remember(self, offers):
        for offer in offers:
            if candidate(self.request, offer):
                self.catalog[offer.offer_id] = offer
                self.sources.setdefault(offer.source_id, (offer.seller.name, 'unchecked', None))

    def enough(self, offers):
        offers = tuple(offers)
        if self.request.intent.kind == 'needs':
            return len({model_identity(o) for o in offers}) >= 3
        collisions = conflicting_configurations(offers)
        by_variant = defaultdict(set)
        for offer in offers:
            if configuration(offer) not in collisions:
                by_variant[configuration(offer)].add(offer.seller.seller_id)
        return any(len(sellers) >= 2 for sellers in by_variant.values())

    def listings(self):
        offers = list(self.catalog.values())
        # Put the best-supported common configuration first. Give each independent seller
        # (or model for needs-based requests) a turn before using another slot on a duplicate.
        sellers = defaultdict(set)
        for o in offers:
            sellers[configuration(o)].add(o.seller.seller_id)
        offers.sort(key=lambda o: -len(sellers[configuration(o)]))
        counts = Counter()
        ordered = []
        for index, offer in enumerate(offers):
            key = (model_identity(offer) if self.request.intent.kind == 'needs'
                   else offer.seller.seller_id)
            ordered.append((counts[key], index, Listing(str(offer.listing_url), offer.source_id, offer)))
            counts[key] += 1
        return [item for _, _, item in sorted(ordered)]

    def find_candidates(self):
        setup = self.deps.research
        if not setup or self.stopped():
            return
        directory = setup.workers.directory
        seen_sources = {o.source_id for o in self.catalog.values()}
        seeds = []
        term = retailer_query(self.request)
        words = set(re.findall(r'[a-z0-9]+', (term or '').lower()))
        sources = self.deps.domestic_sources
        if hasattr(type(sources), 'search_listings'):
            room = lambda: self.left(self.nonfinal_by)
            searches = self.trace['retailer_searches'] = []
            for search in sources.search_listings(self.request, PhaseJob(self.job, room), room=room):
                searches.append(search)
                source_id = search['source_id']
                seen_sources.add(source_id)
                self.read_candidates([Listing(url, source_id) for url in search['links'][:2]])
                if self.enough(self.catalog.values()):
                    return
                if search['reason']:
                    self.sources.setdefault(source_id, (directory.get(source_id).entry.name,
                                                        'failed', search['reason']))
        for selection in directory.select(DirectoryQuery(category=self.request.requirements.category,
                seek_competing_offers=True, limit=10)):
            record = selection.record
            if not selection.configured or record.source_country != 'LB' or record.source_id in seen_sources:
                continue
            permitted = set(selection.permitted_urls)
            urls = [str(url) for url in record.product_urls if str(url) in permitted]
            if urls:
                # A relevant slug can order the reads. Opaque reviewed URLs are
                # still valid leads; only the selected product page decides identity.
                def relevance(url):
                    tokens = re.findall(r'[a-z0-9]+', urlsplit(url).path.lower())
                    return len(words.intersection(tokens))
                seeds.append(Listing(max(urls, key=relevance), record.source_id))
        self.read_candidates(seeds[:3])
        if self.enough(self.catalog.values()) or self.stopped() or self.left(self.nonfinal_by) < 2:
            return
        tools = setup.workers.domestic_tools
        if tools is None:
            return
        options = search_options(self.request, tuple(self.catalog.values()))
        configured = getattr(tools, 'connectors', CHANNELS)
        channels = tuple(channel for channel in CHANNELS if channel in configured)
        objective, method, index = self.plan_search(options, channels)
        self.trace['search_plan'] = dict(method=method, channel=objective.channel,
            language=objective.language, objective=objective.objective,
            query_index=index, query=objective.query)
        self.action(objective.objective, 'Decide whether broader seller or model comparison is feasible',
                    'domestic retailer discovery channels')
        room = lambda: self.left(self.nonfinal_by)
        if self.stopped() or room() < 2:
            return
        phase = PhaseJob(self.job, room)
        def discovery(_, job, stop):
            return tools.search(objective, job, alternatives=tuple(c for c in CHANNELS if c != objective.channel))
        outcome, = research(phase, [discovery], None, timeout=min(60, room() - .2))
        if outcome['status'] != 'completed':
            self.trace['discovery_failure'] = outcome['status']
            return
        report = outcome['result']
        if report.run_id != self.run_ref.run_id:
            return
        self.search_report = report
        self.trace['discovery_leads'] = [dict(source_id=l.source_id, url=l.url) for l in report.leads]
        known_urls = {str(o.listing_url) for o in self.catalog.values()}
        fresh = []
        for lead in report.leads:
            if not lead.source_id:
                self.handoff(lead.url)
            elif urlsplit(lead.url).path.strip('/') and lead.url not in known_urls:
                known_urls.add(lead.url)
                fresh.append(Listing(lead.url, lead.source_id))
        self.read_candidates(fresh[:6])

    def plan_search(self, options, channels):
        # Leave discovery time and the separate final verification/writing reserve intact.
        def generate(instructions, data, schema):
            return self.generate(instructions, data, schema, self.nonfinal_by - 2)
        planner = generate if self.roles is not None and self.left(self.nonfinal_by) > 3 else None
        return choose_search(options, channels, planner, self.request,
                             len({offer.seller.seller_id for offer in self.catalog.values()}))

    def handoff(self, url):
        target = canonical(url)
        if not target or self.deps.onboarding is None or target[0] in self.unknown or self.stopped():
            return
        # A buyer-supplied link can carry a private identifier. A bare shop origin is enough
        # for background onboarding. Public provider-discovered product leads retain their URL.
        if self.request.intent.kind == 'product_link':
            url = 'https://' + urlsplit(url).netloc + '/'
        request = self.request.model_copy(update={'market_scope': v2.MarketScope()})
        try:
            results = self.deps.onboarding.enqueue_buyer([url], request)
        except Exception as exc:
            self.trace['onboarding_failure'] = type(exc).__name__
            return
        if any(r.status in ('queued', 'merged') for r in results):
            self.unknown.add(target[0])
            self.sources.setdefault('lead:' + target[0], (target[0], 'unchecked', 'In background onboarding.'))

    def read_candidates(self, listings):
        for batch in self.batches(listings):
            results = self.read_batch(batch, self.nonfinal_by, 'Find an in-scope configuration at a known shop')
            for _, read in results:
                if read is not None and read.status == 'ok':
                    self.remember(read.offers)
            if self.enough(self.catalog.values()):
                break

    def batches(self, listings):
        # Different sellers may be checked concurrently. Avoid spending two source slots
        # on the same shop in the same batch when its gate permits only one.
        pending = list(listings)
        while pending:
            sources, batch, rest = set(), [], []
            for listing in pending:
                if len(batch) == self.deps.research_parallelism or listing.source_id in sources:
                    rest.append(listing)
                else:
                    batch.append(listing)
                    sources.add(listing.source_id)
            yield batch
            pending = rest

    def read_batch(self, listings, by, effect):
        room = lambda: self.left(by)
        if self.stopped() or room() < FETCH_ROOM_S:
            if by == self.read_by:
                self.limits.add('deadline')
            return [(item, None) for item in listings]
        phase_job = PhaseJob(self.job, room)
        clients = []
        for item in listings:
            self.action('Selected SKU, current price, stock, actual seller, destination service and charges',
                        effect, item.source_id or 'linked retailer')
            def client(_, job, stop, item=item):
                if not stop.is_set() and not job.cancelled:
                    return self.deps.domestic_sources.read(self.request, item.url, job, self.run_ref,
                                                           earlier=item.earlier, room=room)
                return None
            clients.append(client)
        timeout = min(45.0, room() - 1.0)
        results = research(phase_job, clients, None, timeout=timeout)
        resolved = []
        for item, outcome in zip(listings, results):
            read = outcome.get('result')
            if read and read.status == 'unconfigured':
                self.handoff(item.url)
            sid = (read.source_id if read else None) or item.source_id
            if sid:
                offers = read.offers if read else ()
                name = offers[0].seller.name if offers else self.sources.get(sid, (sid,))[0]
                if read and read.status == 'ok' and read.replies:
                    self.sources[sid] = (name, 'checked', None)
                    self.checked[sid] = offers[0].seller.seller_id if offers else sid
                elif self.sources.get(sid, ('', '', None))[1] != 'checked':
                    self.sources[sid] = (name, 'failed', (getattr(read, 'reason', None) if read else None)
                                         or 'The selected listing could not be checked.')
            if outcome['status'] == 'cancelled' or read and read.status == 'refused':
                if by == self.read_by and room() < FETCH_ROOM_S:
                    self.limits.add('deadline')
            resolved.append((item, read))
        return resolved

    def verify_listings(self, listings):
        for batch in self.batches(listings):
            for item, read in self.read_batch(batch, self.read_by, 'Decide whether to rank this offer'):
                self.record_verification(item, read)
            if self.stopped() or self.left(self.read_by) < FETCH_ROOM_S:
                break
            if self.enough(o for o, _, _ in self.assessed.values()):
                if self.request.intent.kind == 'exact_model':
                    break
                selected_models = {model_identity(o) for o, _, _ in self.assessed.values()}
                # Seek independent seller offers on already checked models before stopping.
                if not any(item.earlier and item.earlier.offer_id not in self.reread
                           and model_identity(item.earlier) in selected_models for item in listings):
                    break

    def read_exact_phone_leads(self):
        """Recover current partial offers through the reviewed phone search on a catalog miss."""
        request, source = self.request, self.deps.source
        room = lambda: self.left(self.read_by)
        if (supported(request) or request.fulfillment.mode != 'delivery' or not known_model(request.intent.model)
                or self.stopped() or room() < FETCH_ROOM_S or self.deps.research is None):
            return
        record = self.deps.research.workers.directory.get(source.retailer_id)
        if (record is None or record.source_id != source.source_id or record.seller_id != source.source_id
                or record.seller_identity != 'retailer' or record.role != 'domestic' or record.source_country != 'LB'
                or 'LB' not in record.entry.markets or 'phone' not in record.entry.categories
                or record.entry.discovery_status == 'retired'):
            return
        self.action('Current exact phone variants, prices and online stock',
                    'Show a priced listing when a verified payable offer is unavailable', source.source_id)
        read = source.read_exact_phone(request, PhaseJob(self.job, room), self.run_ref, room=room)
        self.trace['exact_phone_read'] = dict(source_id=source.source_id, fetches=read.fetches, variants=[])
        maximum = request.budget.maximum if request.budget else None
        for offer in read.offers:
            if maximum and offer.price.value.currency == maximum.currency and offer.price.value.amount > maximum.amount:
                continue
            assessment = domestic_assessment(request, offer, self.deps.wall_clock())
            if assessment.decision == 'rejected':
                continue
            reasons = [check.reason for check in assessment.eligibility.checks if check.outcome == 'unresolved']
            self.uncertain[offer.offer_id] = v2.UncertainAlternative(
                offer=offer, cost=assessment.cost, reasons=tuple(dict.fromkeys([*reasons, *assessment.cost.missing])))
            self.trace['exact_phone_read']['variants'].append(dict(
                id=offer.source_variant_id, url=str(offer.listing_url), model=offer.variant.product.model,
                color=offer.variant.color.value, price=offer.price.value.model_dump(mode='json'), stock=offer.stock.value))
        if read.offers:
            self.sources[source.source_id] = (record.entry.name, 'checked', None)
            self.checked[source.source_id] = record.seller_id
        elif self.sources.get(source.source_id, ('', '', None))[1] != 'checked':
            self.sources[source.source_id] = (record.entry.name, 'failed', 'No current priced variant from the retailer search.')

    def record_verification(self, listing, read, *, preserve_earlier=True):
        earlier = listing.earlier
        key = earlier.offer_id if earlier else listing.url
        self.reread.add(key)
        returned = [] if read is None or read.status != 'ok' else [
            o for o in read.offers if str(o.listing_url) == listing.url and candidate(self.request, o)
            and (earlier is None or ((o.source_id, o.source_product_id, o.source_variant_id) ==
                 (earlier.source_id, earlier.source_product_id, earlier.source_variant_id)
                 and not changed_listing_configuration(earlier, o)))]
        if not returned and earlier and preserve_earlier:
            self.uncertain[earlier.offer_id] = v2.UncertainAlternative(offer=earlier,
                reasons=('The selected listing and configuration could not be rechecked.',))
            if read is None or read.status == 'failed':
                self.retries.append(listing)
        for offer in returned:
            self.remember([offer])
            oid = offer.offer_id
            self.uncertain.pop(oid, None)
            self.assessed.pop(oid, None)
            now = self.deps.wall_clock()
            assessment = domestic_assessment(self.request, offer, now, estimates=getattr(read, 'estimates', {}).get(oid))
            maximum = self.request.budget.maximum if self.request.budget else None
            if (assessment.decision == 'rejected' or
                    (maximum and offer.price.state == 'known' and offer.price.value.currency == maximum.currency
                     and offer.price.value.amount > maximum.amount)):
                continue
            problem = current_problem(offer, assessment, read, self.run_ref, self.started_at, now)
            if problem is None and assessment.decision == 'recommendable':
                try:
                    verified = v2.VerifiedOffer(offer=offer, cost=assessment.cost,
                        verification=v2.Verification(checked_at=now, run=self.run_ref))
                except ValueError:
                    problem = 'The current evidence does not verify the complete payable offer.'
                else:
                    self.assessed[oid] = (offer, assessment, verified)
                    continue
            reasons = [c.reason for c in assessment.eligibility.checks if c.outcome == 'unresolved']
            if assessment.cost.status != 'verified':
                reasons.extend(getattr(assessment.cost, 'missing', ()) or ('The payable total is unverified.',))
            cost = assessment.cost
            if problem and cost.status == 'verified':
                # A seller or service gap leaves the current price and published fees as a
                # useful partial. Other problems (stale or conflicting receipts) show no subtotal.
                partial = cost.total if problem in (SELLER_GAP, SERVICE_GAP) else None
                cost = v2.IncompleteCost(status='incomplete', known_subtotal=partial, missing=(problem,))
            self.uncertain[oid] = v2.UncertainAlternative(offer=offer, cost=cost,
                reasons=tuple(dict.fromkeys([*reasons, *([problem] if problem else [])])))
            self.retries.append(Listing(listing.url, offer.source_id, offer))

    def generate(self, instructions, payload, schema, by):
        if self.roles is None:
            self.trace['reasoning_failure'] = 'OpenRouter inference route unavailable'
            return None
        seconds = min(12, self.left(by) - .5)
        if seconds <= 0 or self.stopped():
            return None
        deadline = self.elapsed() + seconds
        try:
            reply = self.roles.generate(instructions, payload, schema,
                budget=JobBudget(self.job, seconds=seconds), cancelled=lambda: self.stopped() or self.left(deadline) <= 0)
            if reply.status == 'completed' and isinstance(reply.value, schema) and self.left(by) > 0:
                return reply.value
        except Exception as exc:
            self.trace['reasoning_failure'] = type(exc).__name__
        return None

    def evaluator_follow_up(self):
        if self.roles is None or not self.retries or self.stopped() or self.left(self.read_by) < FETCH_ROOM_S + 2:
            return
        if ((self.request.intent.kind == 'product_link' and self.assessed)
                or self.enough(o for o, _, _ in self.assessed.values())):
            return
        candidates = [item for item in self.retries if item.earlier.offer_id in self.uncertain][:6]
        if not candidates:
            return
        self.action('Which unresolved fact a second direct read could settle', 'Recover a qualifying offer',
                    'OpenRouter evidence evaluator')
        options = [dict(source=item.source_id, reasons=self.uncertain[item.earlier.offer_id].reasons) for item in candidates]
        for index, option in enumerate(options):
            option['resource_index'] = index
        choice = self.generate(EVALUATE, {'issues': options, 'can_follow_up': True}, CritiqueChoice,
            self.read_by - FETCH_ROOM_S)
        if choice is None or choice.issue_index is None or choice.issue_index >= len(candidates):
            return
        item = candidates[choice.issue_index]
        self.trace['evaluator_follow_up'] = item.earlier.offer_id
        for item, read in self.read_batch([item], self.read_by, 'Resolve the evaluator-selected evidence gap'):
            self.record_verification(item, read)

    def select_fit(self, items):
        if not items:
            return []
        if self.roles is not None:
            self.action('Which verified model specifications support the stated needs',
                        'Limit recommendations to supported models', 'OpenRouter suitability selector')
        gaps = []
        offers = [v.offer for v in items]
        collisions = conflicting_configurations(offers)
        targets = {o.offer_id: o.offer_id if configuration(o) in collisions else o.variant.variant_id for o in offers}
        claims = supporting_claims(offers, self.deps.wall_clock(), targets)
        models = {targets[o.offer_id]: model_identity(o) for o in offers}
        def choose(prompt, data, schema):
            if self.roles is None:
                return None
            return self.generate(prompt + ' Choose distinct models using the supplied model identities.',
                                 dict(data, model_identities=models), schema, self.verification_by)
        result = select_candidates(self.request, self.context.answers, (), gaps, choose, specifications=claims)
        selected = {c.target_id for c in result.candidates}
        for v in items:
            if targets[v.offer.offer_id] not in selected:
                self.uncertain[v.offer.offer_id] = v2.UncertainAlternative(offer=v.offer, cost=v.cost,
                    reasons=('The available specifications did not establish suitability for the requested needs.',))
        return [v for v in items if targets[v.offer.offer_id] in selected]

    def group(self, items):
        groups = defaultdict(dict)
        collisions = conflicting_configurations(v.offer for v in items)
        for item in items:
            offer = item.offer
            config = configuration(offer)
            if config in collisions:
                config = (*config, offer.offer_id)
            groups[config].setdefault(offer.seller.seller_id, item)
        # For an exact comparison, prefer a confirmed same-variant comparison when possible.
        keys = list(groups)
        if self.request.intent.kind == 'exact_model':
            keys.sort(key=lambda k: len(groups[k]) < 2)
        chosen = []
        models = set()
        for key in keys:
            sellers = list(groups[key].values())
            model = model_identity(sellers[0].offer)
            if model in models:
                continue
            chosen.append(v2.Recommendation(primary=sellers[0], additional_sellers=tuple(sellers[1:])))
            models.add(model)
            if self.request.intent.kind != 'needs' or len(chosen) == 3:
                break
        return tuple(chosen)

    def update_coverage(self, groups=()):
        self.outcomes = tuple(v2.SourceOutcome(source_id=s, name=n, status=status, reason=reason)
                              for s, (n, status, reason) in self.sources.items())
        statuses = [s.status for s in self.outcomes]
        self.coverage = v2.CoverageCounts(discovered=len(statuses), attempted=sum(s != 'unchecked' for s in statuses),
            retrieved=statuses.count('checked'), qualifying=len({v.offer.retailer_id for g in groups for v in g.offers()}))
