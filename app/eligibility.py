"""Eligibility rules (MVP 08): whether one offer meets every buyer constraint on the evidence held.

Each constraint is one named check that passes, fails or stays unresolved, and the offer's outcome
follows its checks (app.assessment.combine). A known value decides. An unknown or conflicting fact
never passes a required check: it stays unresolved, with an evidence gap naming the offer field that
more evidence might settle. A known value the rules cannot read either way (a plain 'Blue' for a
requested finish, place text naming the required place among other words) stays unresolved too.
Nothing here relaxes a constraint; only a new buyer request can.

Device identity is compared in canonical form (MVP 07), and only the selected variant's facts count:
page links and other hints never stand in for them. Online stock never proves stock at a pickup
branch, and an arrival estimate never satisfies a guaranteed deadline. The readers already turned
delivery claims into absolute ArrivalWindows, so deadlines compare instants only.

Check reasons use fixed wording plus normalized values: numbers, contract values, names from our own
tables and the buyer's own choices. Gap reasons use fixed wording alone. Store text appears in
neither, so reasons are safe to show people and gaps are safe to show the planner.
"""
from collections.abc import Callable
from datetime import datetime, timezone
import re
import unicodedata

from app.assessment import Check, Eligibility, EvidenceGap, Outcome, combine
from app.contracts import BranchStock, Fact, Offer, ShoppingRequest
from app.devices import canonical_manufacturer, spell_plus
from app.identity import (
    COLOR_FAMILIES, NormalizedVariant, canonical_color, known_color, known_model, normalize_variant,
)

# Every offer gets these checks, in this order.
CHECKS = ('fulfillment', 'condition', 'manufacturer', 'model', 'storage', 'color', 'sim', 'region',
          'purchasable', 'stock', 'price', 'payment_basis', 'warranty', 'deadline', 'market')
# Spellings (casefolded words) of one place. A warranty territory or device region must name the
# required place in one of these spellings; a wider claim such as 'international' is another
# territory, since an international warranty may not be serviced in Lebanon. Text that names the
# place among other words ('Lebanon only', 'International (excluding Lebanon)') is not read: it
# stays unresolved.
_PLACES = {'lebanon': ('lb', 'lbn', 'lebanon', 'liban', 'lebanese republic', 'republic of lebanon',
                       'لبنان', 'الجمهورية اللبنانية')}
PLACE_ALIASES = {alias: name for name, aliases in _PLACES.items() for alias in aliases}
_IDENTITY = ('manufacturer', 'model', 'storage_gb', 'color', 'sim', 'region')
_NO_BRANCH_STOCK = 'branch stock is not published'
_NO_FINISH = 'color finish is not stated'

# (offer field path, fixed reason) for each fact an unresolved check lacks.
Open = list[tuple[str, str]]


def check_eligibility(request: ShoppingRequest, offer: Offer, *,
                      normalized: NormalizedVariant | None = None) -> Eligibility:
    """Every check in CHECKS order, and a gap for each fact an unresolved check lacks.

    normalized is the offer's identity from app.identity.normalize_variant, computed here when it
    is omitted; one made from another offer, or from another read of this one, raises ValueError.
    """
    if normalized is None:
        normalized = normalize_variant(offer)
    else:
        _matching(offer, normalized)
    results = [
        _fulfillment(request, offer), _condition(request, offer), _manufacturer(request, normalized),
        _model(request, normalized), _storage(request, normalized), _color(request, normalized),
        _sim(request, normalized), _region(request, normalized), _purchasable(offer),
        _stock(request, offer), _price(offer), _payment_basis(request, offer),
        _warranty(request, offer), _deadline(request, offer), _market(request, offer),
    ]
    gaps: dict[str, EvidenceGap] = {}
    for check, open_fields in results:
        if check.outcome != 'unresolved':
            continue  # a failed check already decides; its missing facts would change nothing
        for field_path, reason in open_fields:
            gap = EvidenceGap(offer_id=offer.offer_id, source_id=offer.source_id, field_path=field_path,
                              reason=reason)
            gaps.setdefault(gap.gap_id, gap)
    checks = [check for check, _ in results]
    return Eligibility(offer_id=offer.offer_id, outcome=combine(c.outcome for c in checks), checks=checks,
                       gaps=list(gaps.values()))


def _matching(offer: Offer, normalized: NormalizedVariant) -> None:
    """A normalized variant must describe this offer as read: same IDs, fact states and evidence."""
    variant = offer.variant
    if ((normalized.offer_id, normalized.source_id, normalized.product_id, normalized.variant_id)
            != (offer.offer_id, offer.source_id, variant.product_id, variant.variant_id)):
        raise ValueError('The normalized variant belongs to another offer')
    for name in _IDENTITY:
        read, canonical = getattr(variant, name), getattr(normalized, name)
        if (read.state, read.evidence_ids) != (canonical.state, canonical.evidence_ids):
            raise ValueError('The normalized variant does not match the offer as read')


def _check(name: str, outcome: Outcome, reason: str, field_paths, evidence_ids=()) -> Check:
    return Check(name=name, outcome=outcome, reason=reason, field_paths=list(dict.fromkeys(field_paths)),
                 evidence_ids=list(dict.fromkeys(evidence_ids)))


def _say(reason, value) -> str:
    return reason(value) if callable(reason) else reason


def _judge(name: str, fact: Fact, field_path: str, *, ok: Callable[[object], bool | None], passed,
           failed, unknown: str, conflict: str, unclear: str | None = None) -> tuple[Check, Open]:
    """One fact against one constraint: ok decides a known value. An unknown or conflicting fact
    stays unresolved, even when no alternative would pass, and its field becomes a gap.

    passed and failed are fixed texts, or functions of the known value that return one. ok returns
    None for a known value it cannot read either way; that stays unresolved with the unclear gap.
    """
    verdict = ok(fact.value) if fact.state == 'known' else None
    if verdict is not None:
        outcome = 'pass' if verdict else 'fail'
        reason, open_fields = _say(passed if outcome == 'pass' else failed, fact.value), []
    elif fact.state == 'known':
        outcome, reason = 'unresolved', unclear
        open_fields = [(field_path, reason)]
    else:
        outcome = 'unresolved'
        reason = unknown if fact.state == 'unknown' else conflict
        open_fields = [(field_path, reason)]
    return _check(name, outcome, reason, [field_path], fact.evidence_ids), open_fields


def _fulfillment(request: ShoppingRequest, offer: Offer) -> tuple[Check, Open]:
    """An offer is read for one route; its stock, arrival and costs say nothing about another one.

    A request without a branch accepts any branch in its city.
    """
    want, read = request.fulfillment, offer.fulfillment
    if read.mode != want.mode:
        outcome, reason = 'fail', f'offer was read for {read.mode}; the request is for {want.mode}'
    elif read.country != want.country:
        outcome, reason = 'fail', 'offer was read for another country'
    elif _city(read.city) != _city(want.city):
        outcome, reason = 'fail', 'offer was read for another city'
    elif want.branch_id is not None and read.branch_id != want.branch_id:
        outcome, reason = 'fail', 'offer was read for another pickup branch'
    else:
        outcome = 'pass'
        reason = ('offer was read for delivery to the requested city' if want.mode == 'delivery'
                  else 'offer was read for pickup at the requested branch' if want.branch_id is not None
                  else 'offer was read for pickup in the requested city')
    return _check('fulfillment', outcome, reason, ['fulfillment']), []


def _condition(request: ShoppingRequest, offer: Offer) -> tuple[Check, Open]:
    return _judge('condition', offer.condition, 'condition', ok=lambda value: value == request.condition,
                  passed=lambda value: f'condition is {value}',
                  failed=lambda value: f'condition is {value}; the request is for {request.condition}',
                  unknown='condition is not published', conflict='condition observations conflict')


def _manufacturer(request: ShoppingRequest, normalized: NormalizedVariant) -> tuple[Check, Open]:
    wanted, wanted_name = _maker(request.manufacturer), canonical_manufacturer(request.manufacturer)

    def passed(value):
        name = canonical_manufacturer(value)  # only a name from our table is shown
        return f'manufacturer is {name}' if name else 'manufacturer matches the request'

    def failed(value):
        name = canonical_manufacturer(value)
        if name and wanted_name:
            return f'manufacturer {name} is not {wanted_name}'
        return 'manufacturer is not the requested manufacturer'

    return _judge('manufacturer', normalized.manufacturer, 'variant.manufacturer',
                  ok=lambda value: _maker(value) == wanted, passed=passed, failed=failed,
                  unknown='manufacturer is not stated', conflict='manufacturer observations conflict')


def _model(request: ShoppingRequest, normalized: NormalizedVariant) -> tuple[Check, Open]:
    # known_model accepts a name that is exactly one known model, so 'iPhone 17 Pro' and
    # 'iPhone 17 Plus' are never read as the 'iPhone 17' they contain.
    wanted, wanted_name = _name(_model_name(request.model)), known_model(request.model)

    def passed(value):
        name = known_model(value)
        return f'model is {name}' if name else 'model matches the request'

    def failed(value):
        name = known_model(value)
        if name and wanted_name:
            return f'model {name} is not {wanted_name}'
        return 'model is not the requested model'

    return _judge('model', normalized.model, 'variant.model',
                  ok=lambda value: _name(_model_name(value)) == wanted, passed=passed, failed=failed,
                  unknown='model is not stated', conflict='model observations conflict')


def _storage(request: ShoppingRequest, normalized: NormalizedVariant) -> tuple[Check, Open]:
    wanted = request.storage_gb
    return _judge('storage', normalized.storage_gb, 'variant.storage_gb', ok=lambda value: value == wanted,
                  passed=lambda value: f'storage is {value} GB',
                  failed=lambda value: f'storage is {value} GB; the request is for {wanted} GB',
                  unknown='storage is not stated', conflict='storage observations conflict')


def _color(request: ShoppingRequest, normalized: NormalizedVariant) -> tuple[Check, Open]:
    if _any(request.allowed_colors):
        return _check('color', 'pass', 'any color is allowed', ['variant.color']), []
    # Canonical names on both sides, so the buyer's Blue matches a store's 'bleu'.
    wanted = list(dict.fromkeys(canonical_color(color) for color in request.allowed_colors))
    keys, listed = {_name(color) for color in wanted}, ', '.join(wanted)

    def passed(value):
        name = known_color(value)  # only a color from our table is shown
        return f'color {name} is allowed' if name else 'color is allowed'

    def failed(value):
        name = known_color(value)
        return f'color {name} is not in {listed}' if name else f'color is not in {listed}'

    # A store's plain 'Blue' may be the 'Mist Blue' asked for: the finish is missing, not different.
    families = {_name(COLOR_FAMILIES[color]) for color in wanted if color in COLOR_FAMILIES}
    fact = normalized.color
    if fact.state == 'known' and _name(canonical_color(fact.value)) in families - keys:
        return (_check('color', 'unresolved', _NO_FINISH, ['variant.color'], fact.evidence_ids),
                [('variant.color', _NO_FINISH)])
    return _judge('color', normalized.color, 'variant.color',
                  ok=lambda value: _name(canonical_color(value)) in keys, passed=passed, failed=failed,
                  unknown='color is not stated', conflict='color observations conflict')


def _sim(request: ShoppingRequest, normalized: NormalizedVariant) -> tuple[Check, Open]:
    if request.sim == 'any':
        return _check('sim', 'pass', 'any SIM type is allowed', ['variant.sim']), []
    return _judge('sim', normalized.sim, 'variant.sim', ok=lambda value: value == request.sim,
                  passed=lambda value: f'SIM type is {value}',
                  failed=lambda value: f'SIM type is {value}; the request is for {request.sim}',
                  unknown='SIM type is not stated', conflict='SIM type observations conflict')


def _region(request: ShoppingRequest, normalized: NormalizedVariant) -> tuple[Check, Open]:
    if _any(request.allowed_regions):
        return _check('region', 'pass', 'any region is allowed', ['variant.region']), []
    # Regions have no table of our own, so a store's region is never shown in a reason.
    wanted = {_place(region) for region in request.allowed_regions}
    return _judge('region', normalized.region, 'variant.region', ok=lambda value: _names_place(value, wanted),
                  passed='region is allowed', failed='region is not one of the allowed regions',
                  unknown='region is not published', conflict='region observations conflict',
                  unclear='region is not recognized')


def _purchasable(offer: Offer) -> tuple[Check, Open]:
    return _judge('purchasable', offer.purchasable, 'purchasable', ok=lambda value: value is True,
                  passed='store lists the variant as purchasable',
                  failed='store lists the variant as not purchasable',
                  unknown='purchasability is not stated', conflict='purchasability observations conflict')


def _stock(request: ShoppingRequest, offer: Offer) -> tuple[Check, Open]:
    if request.fulfillment.mode == 'delivery':
        return _judge('stock', offer.online_stock, 'online_stock', ok=lambda value: value is True,
                      passed='variant is in stock online', failed='variant is out of stock online',
                      unknown='online stock is not published', conflict='online stock observations conflict')
    return _pickup_stock(request, offer)


def _pickup_stock(request: ShoppingRequest, offer: Offer) -> tuple[Check, Open]:
    """Only stock observed at a pickup branch counts; online stock never proves it.

    A branch named by the request or selected for the offer must show its own stock.
    A city-wide offer can use any branch in that city. An observation is not a reservation.
    """
    want = request.fulfillment
    branch_id = want.branch_id or offer.fulfillment.branch_id
    branches: dict[str, list[BranchStock]] = {} if branch_id is None else {branch_id: []}
    for entry in offer.pickup_stock:
        if _city(entry.city) == _city(want.city) and branch_id in (None, entry.branch_id):
            branches.setdefault(entry.branch_id, []).append(entry)
    paths = [f'pickup_stock.{branch_id}.stock' for branch_id in branches] or ['pickup_stock']
    evidence = [i for entries in branches.values() for entry in entries for i in entry.stock.evidence_ids]
    verdicts = {branch_id: _branch_stock(entries) for branch_id, entries in branches.items()}
    named = branch_id is not None
    in_stock = [branch_id for branch_id, (outcome, _) in verdicts.items() if outcome == 'pass']
    if in_stock:
        # A pass cites only the branches that show stock, never another branch's record.
        reason = ('pickup branch shows the variant in stock' if named
                  else 'a pickup branch in the requested city shows the variant in stock')
        return _check('stock', 'pass', reason + ' (observed, not reserved)',
                      [f'pickup_stock.{branch_id}.stock' for branch_id in in_stock],
                      [i for branch_id in in_stock for entry in branches[branch_id]
                       for i in entry.stock.evidence_ids]), []
    if verdicts and all(outcome == 'fail' for outcome, _ in verdicts.values()):
        reason = ('pickup branch shows the variant out of stock' if named
                  else 'every pickup branch in the requested city shows the variant out of stock')
        return _check('stock', 'fail', reason, paths, evidence), []
    open_fields = [(f'pickup_stock.{branch_id}.stock', gap) for branch_id, (outcome, gap) in verdicts.items()
                   if outcome == 'unresolved'] or [(paths[0], _NO_BRANCH_STOCK)]
    reasons = list(dict.fromkeys(gap for _, gap in open_fields))
    online = offer.online_stock
    if _NO_BRANCH_STOCK in reasons and online.state == 'known' and online.value is True:
        reasons.append('online stock does not show branch stock')
    return _check('stock', 'unresolved', '; '.join(reasons), paths, evidence), open_fields


def _branch_stock(entries: list[BranchStock]) -> tuple[Outcome, str | None]:
    """One branch's stock records; a record without a value does not contradict one with it."""
    facts = [entry.stock for entry in entries]
    known = {fact.value for fact in facts if fact.state == 'known'}
    if any(fact.state == 'conflict' for fact in facts) or len(known) > 1:
        return 'unresolved', 'branch stock observations conflict'
    if known == {True}:
        return 'pass', None
    if known == {False}:
        return 'fail', None
    return 'unresolved', _NO_BRANCH_STOCK


def _price(offer: Offer) -> tuple[Check, Open]:
    return _judge('price', offer.price, 'price', ok=lambda value: value.amount > 0,
                  passed=lambda value: f'price is {value.amount} {value.currency}', failed='price is zero',
                  unknown='price is unknown', conflict='prices conflict')


def _payment_basis(request: ShoppingRequest, offer: Offer) -> tuple[Check, Open]:
    wanted = request.payment_basis
    return _judge('payment_basis', offer.payment_basis, 'payment_basis', ok=lambda value: value == wanted,
                  passed=lambda value: f'price terms are for {value} payment',
                  failed=lambda value: f'price terms are for {value} payment; the request pays by {wanted}',
                  unknown='payment terms are not published', conflict='payment terms conflict')


def _warranty(request: ShoppingRequest, offer: Offer) -> tuple[Check, Open]:
    """Duration, territory and, when required, provider; each part must hold on known facts.

    A store's territory or provider text is never shown; only months, a number, are.
    """
    need = request.warranty_requirement
    if need is None:
        return _check('warranty', 'pass', 'no warranty is required', ['warranty']), []
    warranty, parts = offer.warranty, []
    if need.min_months > 0:  # any warranty lasts at least zero months
        parts.append(_judge(
            'warranty', warranty.months, 'warranty.months', ok=lambda months: months >= need.min_months,
            passed=lambda months: f'warranty is {months} months',
            failed=lambda months: f'warranty is {months} months; at least {need.min_months} are required',
            unknown='warranty duration is not published', conflict='warranty duration observations conflict'))
    territory = _place(need.territory)
    parts.append(_judge(
        'warranty', warranty.territory, 'warranty.territory',
        ok=lambda value: _names_place(value, {territory}),
        passed='warranty covers the required territory', failed='warranty territory is not the required one',
        unknown='warranty territory is not published', conflict='warranty territory observations conflict',
        unclear='warranty territory is not recognized'))
    if need.provider is not None:
        provider = _name(need.provider)
        parts.append(_judge(
            'warranty', warranty.provider, 'warranty.provider', ok=lambda value: _name(value) == provider,
            passed='warranty provider is the required one',
            failed='warranty provider is not the required one',
            unknown='warranty provider is not published', conflict='warranty provider observations conflict'))
    outcome = combine(check.outcome for check, _ in parts)
    reason = '; '.join(check.reason for check, _ in parts if check.outcome == outcome)
    paths = [path for check, _ in parts for path in check.field_paths]
    evidence = [i for check, _ in parts for i in check.evidence_ids]
    open_fields = [field for check, fields in parts if check.outcome == 'unresolved' for field in fields]
    return _check('warranty', outcome, reason, paths, evidence), open_fields


def _deadline(request: ShoppingRequest, offer: Offer) -> tuple[Check, Open]:
    """The arrival window against the deadline, in absolute instants.

    estimate_ok: the whole window before the deadline passes, a window starting after it fails, and
    one that straddles it stays open. guaranteed: only a guaranteed window ending in time passes; an
    estimate cannot satisfy it (open, or failed when even its earliest time is late). A guaranteed
    window that ends after the deadline stays open too: the readers widen latest to the later of
    the UTC+2 and UTC+3 readings (an hour in summer, up to days for a late-evening observation),
    so it is not the end the store promised.
    """
    deadline = request.deadline
    if deadline is None:
        return _check('deadline', 'pass', 'no deadline is set', ['arrival']), []
    delivery = request.fulfillment.mode == 'delivery'
    noun = 'delivery' if delivery else 'pickup'
    arrival = offer.arrival
    if arrival.state != 'known':
        if arrival.state == 'unknown':
            reason = 'delivery time is not published' if delivery else 'pickup readiness is not published'
        else:
            reason = 'delivery estimates conflict' if delivery else 'pickup readiness estimates conflict'
        open_fields = [('arrival', reason)]
        return _check('deadline', 'unresolved', reason, ['arrival'], arrival.evidence_ids), open_fields
    window, by = arrival.value, deadline.arrive_by
    open_fields = []
    if window.earliest > by:
        late, due = _apart(window.earliest, by)
        outcome, reason = 'fail', f'earliest {noun} {late} is after the deadline {due}'
    elif deadline.basis == 'guaranteed' and window.basis == 'estimate':
        outcome, reason = 'unresolved', f'a {noun} estimate cannot satisfy a guaranteed deadline'
        open_fields = [('arrival', f'{noun} guarantee is not published')]
    elif window.latest <= by:
        promise = 'guaranteed' if window.basis == 'guaranteed' else 'expected'
        outcome = 'pass'
        reason = f'{noun} is {promise} by {_at(window.latest)}, before the deadline {_at(by)}'
    else:
        outcome = 'unresolved'
        late, due = _apart(window.latest, by)
        reason = f'{noun} window ends {late}, after the deadline {due}'
        open_fields = [('arrival', f'{noun} window extends past the deadline')]
    return _check('deadline', outcome, reason, ['arrival'], arrival.evidence_ids), open_fields


def _market(request: ShoppingRequest, offer: Offer) -> tuple[Check, Open]:
    """The MVP compares domestic offers only; an offer's market is the country it is read for."""
    market = offer.fulfillment.country
    if market in request.allowed_markets:
        return _check('market', 'pass', f'offer is in the {market} market', ['fulfillment.country']), []
    return _check('market', 'fail', f'offer is in the {market} market, which the request does not allow',
                  ['fulfillment.country']), []


def _any(choices: list[str]) -> bool:
    return [choice.casefold() for choice in choices] == ['any']


def _maker(text: str) -> str:
    return (canonical_manufacturer(text) or ' '.join(text.split())).casefold()


def _model_name(text: str) -> str:
    # '+' is punctuation to _name, so it is spelled out first: 'Galaxy S25+' is not 'Galaxy S25'.
    text = spell_plus(text)
    return known_model(text) or ' '.join(text.split())


def _words(text: str) -> str:
    """Casefolded words without accents or punctuation, so 'Liban.' and 'LIBAN' read alike."""
    folded = unicodedata.normalize('NFKD', text.casefold())
    return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in folded if not unicodedata.combining(c))))


def _name(text: str) -> str:
    """A name for comparison: its words, or its casefolded text when it has no letters or digits."""
    return _words(text) or ' '.join(text.split()).casefold()


def _place(text: str) -> str:
    """A territory or region for comparison, with the spellings of one place made one."""
    name = _name(text)
    return PLACE_ALIASES.get(name, name)


def _names_place(text: str, wanted: set[str]) -> bool | None:
    """True when the text is one of the wanted places; None when it names one among other words,
    which could narrow or exclude it; False otherwise."""
    if _place(text) in wanted:
        return True
    words = f' {_name(text)} '
    spellings = wanted | {alias for alias, name in PLACE_ALIASES.items() if name in wanted}
    return None if any(f' {spelling} ' in words for spelling in spellings) else False


def _city(text: str) -> str:
    # The same comparison the cost engine uses for the route, so the two never disagree.
    return ' '.join(text.split()).casefold()


def _apart(later: datetime, earlier: datetime) -> tuple[str, str]:
    """Both instants for an 'after' reason, with microseconds when whole seconds would print one time."""
    if _at(later) != _at(earlier):
        return _at(later), _at(earlier)
    exact = '%Y-%m-%dT%H:%M:%S.%fZ'
    return later.astimezone(timezone.utc).strftime(exact), earlier.astimezone(timezone.utc).strftime(exact)


def _at(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
