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
TIER_WORDS = ('max', 'plus', 'ultra', 'mini', 'lite', 'pro', 'fe', 'air', 'slim', 'edge')
GB = re.compile(r'(\d{1,4})\s?(gb|tb)\b', re.IGNORECASE)
INCH = re.compile(r'(\d{2}(?:\.\d)?)\s?(?:-?\s?inch|in\b|"|”|\'\')', re.IGNORECASE)
MONEY_IN = re.compile(r'(\d[\d,]*(?:\.\d+)?)')
BUDGET = re.compile(r'(?:at most|no more than|max(?:imum)?|under|below|up to|budget(?: of| is)?|less than|'
                    r'within)\s*(?:of\s*)?(?:usd|us\$|\$)?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:usd|\$|dollars)?',
                    re.IGNORECASE)


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
    m = BUDGET.search(text or '')
    return dec(m.group(1)) if m else None


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
            title, vtitle = product.title, variant.title
            url = variant.url or product.url
            offer_text = ' '.join([product.title, variant.title, ' '.join(variant.options.values())])
            evidence_rows = [dict(obs=obs.obs_id, url=product.url, read=product.endpoint, source=product.source,
                                  observed_at=obs.observed_at.isoformat(timespec='seconds'), sha256=obs.sha256)]
        else:
            # A page without structured product data: the price must be quoted from the capture.
            if not (claim.price_quote and contains_quote(obs.text, claim.price_quote)):
                continue
            m = MONEY_IN.search(claim.price_quote)
            price = dec(m.group(1)) if m else None
            currency = 'USD' if '$' in claim.price_quote or 'usd' in claim.price_quote.lower() else None
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
        marker = USED.search(offer_text)
        if marker:
            condition = 'used' if 'refurb' not in marker.group(0).lower() else 'refurbished'
            downgrades.append(f'listing says "{marker.group(0)}"')
        elif obs.kind == 'product' and (obs.product.condition or '').lower().startswith('new'):
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
            covered = re.search(r'\b(new|condition|deliver\w*|shipping|stock|available|price|vat|pick ?up|branch)\b',
                                r.requirement, re.IGNORECASE)
            if status == 'unknown' and not covered:
                # condition, delivery, stock, price and VAT already have their own gap wording
                gaps.append(f'not confirmed: {r.requirement}')
            if status == 'unmet':
                unmet.append(r.requirement)
            reqs.append(dict(requirement=r.requirement, status=status, quote=r.quote if status == 'met' else None,
                             obs=src.obs_id if status == 'met' else None, covered_elsewhere=bool(covered)))
        if price is None:
            gaps.append('no current price published')
        if currency and currency.upper() not in ('USD',):
            gaps.append(f'price is in {currency}; no USD total is computed')
        if available is None:
            gaps.append('stock is not published')
        # Delivery fee and total.
        fee, total, basis = None, None, None
        if mode == 'delivery':
            dobs = evidence.get(claim.delivery_obs)
            fee = dec(claim.delivery_fee_usd)
            threshold = dec(claim.free_delivery_over_usd)
            ok = (dobs is not None and dobs.shop_id == obs.shop_id and claim.delivery_quote
                  and contains_quote(dobs.text, claim.delivery_quote) and fee is not None
                  and (_amount_in(claim.delivery_quote, fee) or (fee == 0 and 'free' in claim.delivery_quote.lower())))
            region_problem = region_mismatch(claim.delivery_quote or '', request.get('destination') or '')
            if ok and region_problem:
                ok = False
                downgrades.append(region_problem)
            if ok and threshold is not None and not _amount_in(claim.delivery_quote, threshold):
                threshold = None
            if ok and price is not None and (currency or 'USD').upper() == 'USD':
                if threshold is not None and price >= threshold:
                    fee = Decimal('0.00')
                total = (price + fee).quantize(Decimal('0.01'))
                basis = (f'listed price + delivery fee published by {obs.shop_id} '
                         f'("{claim.delivery_quote[:120]}", {dobs.obs_id})')
                evidence_rows.append(dict(obs=dobs.obs_id, url=dobs.url, source='shop delivery terms',
                                          observed_at=dobs.observed_at.isoformat(timespec='seconds'),
                                          sha256=dobs.sha256))
            else:
                fee = None
                gaps.append('delivery fee to your area is not published or not confirmed')
        else:
            gaps.append('pickup branch stock is not confirmed (online stock only)')
        vat_ok = bool(claim.vat_quote) and any(contains_quote(o.text, claim.vat_quote)
                                               for o in evidence.all() if o.shop_id == obs.shop_id)
        if not vat_ok:
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
        if budget is not None and tier in ('best', 'alternative'):
            amount = total if total is not None else price
            if amount is not None and amount > budget:
                tier = 'rejected'
                downgrades.append(f'over the stated maximum of USD {budget}')
            elif total is None and price is not None:
                gaps.append(f'may exceed USD {budget} once delivery is added')
        complete = (tier == 'best' and identity == 'exact' and available is True and condition == 'new'
                    and all(r['status'] == 'met' for r in reqs if not r['covered_elsewhere'])
                    and (total is not None or mode != 'delivery')
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
