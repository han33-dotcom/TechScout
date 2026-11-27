"""Deterministic checks on the agent's proposed offers (the buyer contract lives here).

The agent proposes; this module decides what the buyer is told is true.

* Price, currency and stock always come from the shop's own structured data in a product
  observation made in this run, never from the model. A page without structured data needs
  a price quote that occurs in the captured page.
* Every quote the agent cites must occur in the observation it names. A missing quote turns
  the claim into an "unknown" gap; it never passes.
* Code can only downgrade identity (numeric storage/RAM/size conflicts, Pro vs Pro Max and
  similar tier words, used or refurbished markers). It never upgrades a model judgement.
* Money is Decimal. A delivery total needs a published fee quoted from the same shop.
  Unknown charges stay unknown, never zero. VAT is reported as a gap unless the shop states it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.scout.evidence import Evidence, Observation, contains_quote, normalize


# ---- what the agent submits -----------------------------------------------------------------
class _M(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Requirement(_M):
    requirement: str = Field(max_length=200)
    status: Literal['met', 'unmet', 'unknown']
    evidence_obs: str | None = Field(max_length=8)
    quote: str | None = Field(max_length=300)


class OfferClaim(_M):
    obs: str = Field(max_length=8)
    variant_id: str = Field(max_length=80)
    identity: Literal['exact', 'near', 'different']
    identity_reason: str = Field(max_length=300)
    condition: Literal['new', 'used', 'refurbished', 'open_box', 'unknown']
    condition_quote: str | None = Field(max_length=200)
    condition_obs: str | None = Field(max_length=8)
    requirements: list[Requirement] = Field(max_length=8)
    delivery_obs: str | None = Field(max_length=8)
    delivery_quote: str | None = Field(max_length=300)
    delivery_fee_usd: str | None = Field(max_length=12)
    free_delivery_over_usd: str | None = Field(max_length=12)
    vat_quote: str | None = Field(max_length=200)
    price_quote: str | None = Field(max_length=200)


class Final(_M):
    status: Literal['offers', 'no_match', 'clarify', 'impossible']
    request_kind: Literal['exact', 'needs', 'link']
    message: str = Field(max_length=2000)
    offers: list[OfferClaim] = Field(max_length=8)
    question: str | None = Field(max_length=300)
    budget_max_usd: str | None = Field(max_length=12)


# ---- what the buyer sees -------------------------------------------------------------------
@dataclass
class CheckedOffer:
    tier: str                       # best | alternative | unavailable | rejected
    shop: str
    shop_id: str
    title: str
    variant: str
    url: str
    price: Decimal | None
    currency: str | None
    in_stock: bool | None
    condition: str                  # new (evidenced) | used | refurbished | open_box | unknown
    identity: str
    identity_reason: str
    delivery_fee: Decimal | None = None
    total: Decimal | None = None
    total_basis: str | None = None
    complete: bool = False          # exact, in stock, new, all requirements met, fee and VAT evidenced
    gaps: list[str] = field(default_factory=list)
    requirements: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    seller_basis: str = ''
    downgrades: list[str] = field(default_factory=list)

    def public(self) -> dict:
        d = asdict(self)
        for k in ('price', 'delivery_fee', 'total'):
            d[k] = None if d[k] is None else format(d[k], 'f')
        return d


USED = re.compile(r'\b(used|pre-?owned|refurb\w*|renewed|open[- ]?box|second[- ]?hand|like[- ]new|grade [abc]|'
                  r'cpo|ex[- ]?demo|demo unit|activated|مستعمل)\b', re.IGNORECASE)
NEW = re.compile(r'(brand[- ]new|\bnew\b|sealed|newcondition|جديد)', re.IGNORECASE)
DEADLINE = re.compile(r'\b(by|before|within|tomorrow|today|tonight|deadline|same[- ]day|next[- ]day|'
                      r'monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d+\s?(?:days?|hours?))\b',
                      re.IGNORECASE)
VAT_WORD = re.compile(r'(\bvat\b|\btax(?:es)?\b|\btva\b|ضريبة)', re.IGNORECASE)
VAT_INCLUDED = re.compile(r"(includ\w*|inclusive|\bttc\b|no vat|not charg\w*|don.?t charge|do not charge|exempt|"
                          r"free of (?:vat|tax))", re.IGNORECASE)
VAT_EXCLUDED = re.compile(r'(exclud\w*|not included|\+\s?vat|plus vat|added at checkout|calculated at checkout|'
                          r'will be added)', re.IGNORECASE)
CONDITIONAL_FREE = re.compile(r'\b(over|above|orders? of|minimum|more than|exceed\w*|starting|from|at least)\b',
                              re.IGNORECASE)
NATIONWIDE = re.compile(r'(all (?:of )?lebanon|anywhere|nationwide|all (?:areas|regions|over)|across lebanon|flat|'
                        r'all lebanese|any region|everywhere)', re.IGNORECASE)
TIER_WORDS = ('max', 'plus', 'ultra', 'mini', 'lite', 'pro', 'fe', 'air', 'slim', 'edge')
GB = re.compile(r'(\d{1,4})\s?(gb|tb)\b', re.IGNORECASE)
INCH = re.compile(r'(\d{2}(?:\.\d)?)\s?(?:-?\s?inch|in\b|"|”|\'\')', re.IGNORECASE)
MONEY_IN = re.compile(r'(\d[\d,]*(?:\.\d+)?)')
_NUM = r'(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)'
_USD_BEFORE = re.compile(r'(?:US\$|\$|\bUSD)\s?' + _NUM, re.IGNORECASE)
_USD_AFTER = re.compile(_NUM + r'\s?(?:\$|USD\b|US\s?dollars?\b|dollars?\b)', re.IGNORECASE)
_LBP_BEFORE = re.compile(r'(?:\bLBP|\bL\.L\.?|ل\.ل\.?)\s?' + _NUM, re.IGNORECASE)
_LBP_AFTER = re.compile(_NUM + r'\s?(?:LBP\b|L\.L\.?|ل\.ل\.?|lira\b|ليرة)', re.IGNORECASE)
BUDGET_WORDS = re.compile(r'\b(?:at most|no more than|max(?:imum)?|under|below|up to|budget(?: of| is)?|less than|'
                          r'not (?:more|over) than|cap(?:ped)? at)\b', re.IGNORECASE)
UNIT_AFTER = re.compile(r'^\s?(?:gb|tb|mb|inch|in\b|"|hz|kg|g\b|days?|hours?|hrs?|years?|months?|weeks?|mah|w\b|'
                        r'mm|cm|ms|nits|%)', re.IGNORECASE)


def currency_amounts(text: str) -> list[tuple[Decimal, str, int, int]]:
    """Amounts written next to a currency marker: (amount, 'USD'|'LBP', start, end). Bare numbers never count."""
    found = []
    for rx, cur in ((_USD_BEFORE, 'USD'), (_USD_AFTER, 'USD'), (_LBP_BEFORE, 'LBP'), (_LBP_AFTER, 'LBP')):
        for m in rx.finditer(text or ''):
            amount = dec(m.group(1))
            if amount is not None and not any(a == amount and abs(st - m.start()) < 3 for a, _, st, _ in found):
                found.append((amount, cur, m.start(), m.end()))
    return sorted(found, key=lambda x: x[2])


def dec(value) -> Decimal | None:
    if value in (None, ''):
        return None
    try:
        d = Decimal(str(value).replace(',', '').replace('$', '').strip())
    except (InvalidOperation, ValueError):
        return None
    return d.quantize(Decimal('0.01')) if d >= 0 else None


def storage_values(text: str) -> set[int]:
    out = set()
    for n, unit in GB.findall(text or ''):
        out.add(int(n) * (1024 if unit.lower() == 'tb' else 1))
    return out


def tier_words(text: str) -> set[str]:
    words = set(re.findall(r'[a-z]+', (text or '').lower()))
    return {w for w in TIER_WORDS if w in words}


def hard_budget(text: str) -> Decimal | None:
    """A stated maximum ("at most $60", "under 800 USD", "budget is $1,000"). The number must carry a
    currency marker, so "Pro Max 256GB", "within 3 days" or "up to 1TB" never become a budget."""
    for m in BUDGET_WORDS.finditer(text or ''):
        tail = (text or '')[m.end():m.end() + 30]
        amounts = [a for a in currency_amounts(tail) if a[1] == 'USD' and a[2] <= 6]
        if amounts and not UNIT_AFTER.match(tail[amounts[0][3]:]):
            return amounts[0][0]
    return None


def identity_conflicts(request_text: str, offer_text: str, kind: str) -> list[str]:
    """Reasons an 'exact' claim cannot stand. Only applied to exact-model requests."""
    if kind != 'exact':
        return []
    problems = []
    req_gb, off_gb = storage_values(request_text), storage_values(offer_text)
    missing = {g for g in req_gb if off_gb and g not in off_gb}
    if missing:
        problems.append(f'requested {", ".join(f"{g} GB" for g in sorted(missing))} not in the listed variant')
    req_tiers, off_tiers = tier_words(request_text), tier_words(offer_text)
    extra = off_tiers - req_tiers - {'pro'} if 'pro' in req_tiers else off_tiers - req_tiers
    # "pro" alone is a common family word ("MacBook Pro", "AirPods Pro"); only flag it when the request lacks it
    if 'pro' in off_tiers and 'pro' not in req_tiers:
        extra = extra | {'pro'}
    lacking = req_tiers - off_tiers
    if extra & {'max', 'plus', 'ultra', 'mini', 'lite', 'fe', 'pro', 'edge'}:
        problems.append('listing is a different tier (' + ', '.join(sorted(extra)) + ')')
    if lacking & {'max', 'plus', 'ultra', 'mini', 'lite', 'fe', 'pro', 'edge'}:
        problems.append('listing lacks the requested tier (' + ', '.join(sorted(lacking)) + ')')
    req_in = {float(x) for x in INCH.findall(request_text or '')}
    off_in = {float(x) for x in INCH.findall(offer_text or '')}
    # Nominal sizes: a "13-inch" MacBook Air is listed as 13.6", a "24-inch" monitor as 23.8".
    if req_in and off_in and not any(abs(a - b) < 0.95 for a in req_in for b in off_in):
        problems.append('screen size differs from the request')
    return problems


def _variant(obs: Observation, variant_id: str):
    product = obs.product
    if product is None:
        return None
    want = normalize(variant_id)
    for v in product.variants:
        if normalize(v.variant_id) == want or (v.sku and normalize(v.sku) == want):
            return v
    for v in product.variants:
        if normalize(v.title) == want:
            return v
    return product.variants[0] if len(product.variants) == 1 else None


def check_offers(final: Final, evidence: Evidence, shops: dict, request: dict) -> list[CheckedOffer]:
    request_text = request.get('text', '')
    mode = request.get('mode') or 'delivery'
    budget = hard_budget(request_text) or dec(final.budget_max_usd)
    out: list[CheckedOffer] = []
    seen = set()
    for claim in final.offers:
        obs = evidence.get(claim.obs)
        if obs is None:
            continue  # cites nothing observed in this run: dropped silently from the answer, kept in trace
        shop = shops.get(obs.shop_id or '')
        if obs.kind == 'product':
            variant = _variant(obs, claim.variant_id)
            if variant is None:
                continue
            product = obs.product
            price, currency, available = variant.price, variant.currency, variant.available
            published_condition = variant.condition or product.condition or ''
            stock_text = variant.stock_text
            title, vtitle = product.title, variant.title
            url = variant.url or product.url
            offer_text = ' '.join([product.title, variant.title, ' '.join(variant.options.values())])
            evidence_rows = [dict(obs=obs.obs_id, url=product.url, read=product.endpoint, source=product.source,
                                  observed_at=obs.observed_at.isoformat(timespec='seconds'), sha256=obs.sha256)]
        else:
            # A page without structured product data: the price must be quoted from the capture.
            if not (claim.price_quote and contains_quote(obs.text, claim.price_quote)):
                continue
            amounts = {(a[0], a[1]) for a in currency_amounts(claim.price_quote)}
            if len(amounts) != 1:
                continue  # no currency amount, or several: the price cannot be read safely from this quote
            price, currency = next(iter(amounts))
            published_condition, stock_text = '', None
            available, title, vtitle, url = None, obs.meta.get('title', ''), '', obs.url
            offer_text = title
            evidence_rows = [dict(obs=obs.obs_id, url=obs.url, read=obs.url, source='page text quote',
                                  observed_at=obs.observed_at.isoformat(timespec='seconds'), sha256=obs.sha256)]
        key = (url, vtitle)
        if key in seen:
            continue
        seen.add(key)
        gaps, downgrades = [], []
        identity = claim.identity
        conflicts = identity_conflicts(request_text, offer_text, final.request_kind) if identity == 'exact' else []
        if conflicts:
            identity = 'near'
            downgrades.extend(conflicts)
        # Condition: used markers in the listing win over anything the model says.
        published = re.sub(r'condition$', '', published_condition.strip().lower())  # schema 'UsedCondition'
        marker = USED.search(offer_text) or USED.search(published) or (
            re.search(r'damaged|refurbished', published) if published else None)
        if marker:
            condition = 'refurbished' if 'refurb' in marker.group(0).lower() else 'used'
            downgrades.append(f'listing says "{marker.group(0)}"')
        elif published.startswith('new') or published in ('brand new', 'sealed'):
            condition = 'new'
        elif claim.condition == 'new' and claim.condition_quote and NEW.search(claim.condition_quote) \
                and not USED.search(claim.condition_quote) \
                and any(contains_quote(o.text, claim.condition_quote)
                        for o in condition_sources(evidence, obs, claim.condition_obs, url)):
            condition = 'new'
        else:
            condition = 'unknown'
            gaps.append('new condition is not stated by the shop')
        # Requirements: a "met" without a findable quote becomes unknown.
        reqs, unmet = [], []
        for r in claim.requirements:
            status = r.status
            src = evidence.get(r.evidence_obs) or obs
            if status == 'met' and not (r.quote and contains_quote(src.text, r.quote)
                                        and (src.shop_id == obs.shop_id)):
                status = 'unknown'
            deadline = bool(DEADLINE.search(r.requirement))
            if deadline and status == 'met':
                status = 'unknown'  # a published delivery estimate is not a guaranteed date
            covered = not deadline and re.search(
                r'\b(new|condition|deliver\w*|shipping|stock|available|price|vat|pick ?up|branch)\b',
                r.requirement, re.IGNORECASE)
            if deadline and status == 'unknown':
                gaps.append(f'not guaranteed: {r.requirement} (delivery estimates are not guarantees)')
            elif status == 'unknown' and not covered:
                # condition, delivery, stock, price and VAT already have their own gap wording
                gaps.append(f'not confirmed: {r.requirement}')
            if status == 'unmet':
                unmet.append(r.requirement)
            reqs.append(dict(requirement=r.requirement, status=status, quote=r.quote if status == 'met' else None,
                             obs=src.obs_id if status == 'met' else None, covered_elsewhere=bool(covered)))
        if price is None:
            gaps.append('no current price published')
        usd = bool(currency) and currency.upper() == 'USD'
        if price is not None and not currency:
            gaps.append('currency is not published; no total is computed')
        elif currency and not usd:
            gaps.append(f'price is in {currency}; no USD total is computed')
        if available is None:
            gaps.append(f'stock is not confirmed ({stock_text})' if stock_text else 'stock is not published')
        # Delivery fee and total.
        fee, total, basis = None, None, None
        if mode == 'delivery':
            dobs = evidence.get(claim.delivery_obs)
            fee = dec(claim.delivery_fee_usd)
            quote = claim.delivery_quote or ''
            threshold = dec(claim.free_delivery_over_usd) or free_threshold(quote)
            ok = (dobs is not None and dobs.shop_id == obs.shop_id and quote
                  and contains_quote(dobs.text, quote) and fee is not None)
            problem = fee_problem(quote, fee, threshold, price, request.get('destination') or '') if ok else None
            if problem:
                ok = False
                downgrades.append(problem)
            if ok and price is not None and usd:
                if threshold is not None and price >= threshold and any(
                        a[0] == threshold for a in currency_amounts(quote)) and 'free' in quote.lower():
                    fee = Decimal('0.00')
                total = (price + fee).quantize(Decimal('0.01'))
                basis = (f'listed price + delivery fee published by {obs.shop_id} '
                         f'("{quote[:120]}", {dobs.obs_id})')
                evidence_rows.append(dict(obs=dobs.obs_id, url=dobs.url, source='shop delivery terms',
                                          observed_at=dobs.observed_at.isoformat(timespec='seconds'),
                                          sha256=dobs.sha256))
            else:
                fee = None
                gaps.append('delivery fee to your area is not published or not confirmed')
        else:
            gaps.append('pickup branch stock is not confirmed (online stock only)')
        vat_found = bool(claim.vat_quote) and any(contains_quote(o.text, claim.vat_quote)
                                                  for o in evidence.all() if o.shop_id == obs.shop_id)
        vat = vat_status(claim.vat_quote, vat_found)
        vat_ok = vat == 'included'
        if vat == 'excluded':
            gaps.append('VAT is added on top of the listed price (the total excludes it)')
        elif not vat_ok:
            gaps.append('VAT not stated by the shop')
        # Seller location basis from the directory (Shopify: the shop's own meta.json).
        if shop is not None:
            where = ', '.join(x for x in (shop.city, shop.country) if x)
            seller = (f'{shop.name}: Lebanese shop in the reviewed directory'
                      + (f' (shop metadata: {where})' if where else ''))
        else:
            seller = 'shop is outside the directory; Lebanese location not established'
            gaps.append('seller location not established')
        tier = 'best'
        if identity == 'different' or condition in ('used', 'refurbished', 'open_box') or unmet:
            tier = 'rejected'
            if unmet:
                downgrades.append('does not meet: ' + '; '.join(unmet))
            # For a request that cannot exist as asked, the closest real product is the useful answer.
            if (final.status == 'impossible' and unmet and identity != 'different'
                    and condition not in ('used', 'refurbished', 'open_box')):
                tier, identity = 'alternative', 'near'
        if tier != 'rejected':
            if available is False:
                tier = 'unavailable'
            elif identity == 'near':
                tier = 'alternative'
        if budget is not None and tier in ('best', 'alternative') and not usd and price is not None:
            gaps.append(f'cannot compare with your USD {budget} maximum (price not in USD)')
        if budget is not None and tier in ('best', 'alternative') and usd:
            amount = total if total is not None else price
            if amount is not None and amount > budget:
                tier = 'rejected'
                downgrades.append(f'over the stated maximum of USD {budget}')
            elif total is None and price is not None:
                gaps.append(f'may exceed USD {budget} once delivery is added')
        complete = (tier == 'best' and identity == 'exact' and available is True and condition == 'new'
                    and all(r['status'] == 'met' for r in reqs if not r['covered_elsewhere'])
                    and mode == 'delivery' and total is not None and usd
                    and vat_ok and shop is not None)
        out.append(CheckedOffer(
            tier=tier, shop=obs.product.shop_name if obs.product else (shop.name if shop else obs.shop_id or ''),
            shop_id=obs.shop_id or '', title=title, variant=vtitle, url=url, price=price, currency=currency,
            in_stock=available, condition=condition, identity=identity, identity_reason=claim.identity_reason,
            delivery_fee=fee, total=total, total_basis=basis, complete=complete, gaps=_dedupe(gaps),
            requirements=reqs, evidence=evidence_rows, seller_basis=seller, downgrades=downgrades))
    order = {'best': 0, 'alternative': 1, 'unavailable': 2, 'rejected': 3}
    # Within a tier: offers with an evidenced delivered total, ranked by total; then listed prices with the
    # fee still to confirm, established-new first, ranked by price. Totals and listed prices are never
    # ranked against each other (buyer contract: only comparable totals are ranked).
    out.sort(key=lambda o: (order[o.tier], o.total is None,
                            (o.total if o.total is not None else Decimal('1e9')),
                            o.condition != 'new', o.price if o.price is not None else Decimal('1e9')))
    return out


def region_mismatch(quote: str, destination: str) -> str | None:
    """A fee quoted for inside Beirut cannot price a delivery outside it, and vice versa."""
    q, d = quote.lower(), destination.lower()
    if not d:
        return None
    in_beirut = 'beirut' in d or 'بيروت' in d
    inside = re.search(r'(inside|within|in)\s+(greater\s+)?beirut', q)
    outside = re.search(r'(outside|out of|beyond)\s+(of\s+)?beirut', q)
    if inside and not outside and not in_beirut:
        return f'the quoted fee is for Beirut, not {destination}'
    if outside and not inside and in_beirut:
        return f'the quoted fee is for outside Beirut, not {destination}'
    return None


def fee_problem(quote: str, fee: Decimal, threshold: Decimal | None, price: Decimal | None,
                destination: str) -> str | None:
    """Why a claimed delivery fee cannot be applied to this offer, or None when it can.

    The fee must be a currency amount in the quote (or an unconditional "free"); a free-delivery
    threshold only applies when the price reaches it; when the quote names several fees, the fee
    must be the one written next to the buyer's region or a nationwide rule."""
    if not destination:
        return 'no delivery destination was given'
    amounts = [a for a in currency_amounts(quote) if a[1] == 'USD']
    if fee == 0:
        q = quote.lower()
        if 'free' not in q:
            return 'the quote does not say delivery is free'
        if CONDITIONAL_FREE.search(q):
            limit = threshold if threshold is not None else (amounts[0][0] if amounts else None)
            if limit is None or price is None or price < limit or not any(a[0] == limit for a in amounts):
                return 'free delivery in the quote depends on a minimum order this price does not reach'
        return None
    if not any(a[0] == fee for a in amounts):
        return 'the fee is not written as a currency amount in the quote'
    problem = region_mismatch(quote, destination)
    if problem:
        return problem
    for amount, _, a_start, a_end in amounts:
        if amount != fee:
            continue
        kind = nearest_region(quote, a_start, a_end, destination)
        if kind is None and len({a[0] for a in amounts}) == 1:
            return None  # a single fee with no region named: the shop's general delivery fee
        d = destination.lower()
        in_beirut = 'beirut' in d or 'بيروت' in d
        if kind in ('destination', 'nationwide') or (kind == 'inside' and in_beirut) \
                or (kind == 'outside' and not in_beirut):
            return None
    return 'the quote lists several fees and this one is not stated for your area'


REGION_MENTIONS = (
    ('inside', re.compile(r'(inside|within|in)\s+(greater\s+)?beirut', re.IGNORECASE)),
    ('outside', re.compile(r'(outside|out of|beyond)\s+(of\s+)?beirut', re.IGNORECASE)),
    ('nationwide', NATIONWIDE),
    ('inside', re.compile(r'\bbeirut\b|بيروت', re.IGNORECASE)),  # a bare "Beirut:" label means Beirut itself
)


def nearest_region(quote: str, start: int, end: int, destination: str) -> str | None:
    """The region label written closest to an amount, without crossing a comma, semicolon or full stop
    (a crossing costs heavily). 'destination' when the buyer's own city is the nearest label."""
    mentions = []
    for kind, rx in REGION_MENTIONS:
        mentions += [(kind, m.start(), m.end()) for m in rx.finditer(quote)]
    city = destination.lower().split(',')[0].strip()
    if city and city != 'beirut':
        mentions += [('destination', m.start(), m.end()) for m in re.finditer(re.escape(city), quote.lower())]
    best, best_score = None, None
    for kind, m_start, m_end in mentions:
        gap = quote[m_end:start] if m_end <= start else quote[end:m_start] if m_start >= end else ''
        score = len(gap) + 100 * sum(gap.count(c) for c in ',;.|\n')
        if best_score is None or score < best_score:
            best, best_score = kind, score
    return best


def free_threshold(quote: str) -> Decimal | None:
    """'Free delivery on orders over $100' -> 100 (a currency amount after the condition word)."""
    m = re.search(r'free[^.;|]*?' + CONDITIONAL_FREE.pattern, quote or '', re.IGNORECASE)
    if not m:
        return None
    after = [a for a in currency_amounts((quote or '')[m.end():m.end() + 40]) if a[1] == 'USD']
    return after[0][0] if after else None


def vat_status(quote: str | None, found: bool) -> str:
    """'included' (stated included or not charged), 'excluded' (added on top), or 'unknown'."""
    if not quote or not found or not VAT_WORD.search(quote):
        return 'unknown'
    if VAT_EXCLUDED.search(quote):
        return 'excluded'
    if VAT_INCLUDED.search(quote):
        return 'included'
    return 'unknown'


POLICY_PATH = re.compile(r'polic|shipping|delivery|faq|terms|about|warranty|llm-context|meta\.json', re.IGNORECASE)


def condition_sources(evidence: Evidence, obs: Observation, cited: str | None, url: str) -> list[Observation]:
    """Where a 'new' quote may come from: the product observation itself, a page read of the same
    product URL at the same shop, or the same shop's policy pages (a shop-wide statement)."""
    sources = [obs]
    extra = evidence.get(cited)
    if extra is not None and extra.shop_id == obs.shop_id and extra.obs_id != obs.obs_id:
        same_product = _path(extra.url) == _path(url) or _path(extra.url) == _path(obs.url)
        if same_product or POLICY_PATH.search(extra.url):
            sources.append(extra)
    return sources


def _path(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url or '').path.rstrip('/').lower()


def _amount_in(quote: str, amount: Decimal) -> bool:
    for m in MONEY_IN.findall(quote or ''):
        d = dec(m)
        if d is not None and d == amount:
            return True
    return False


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
