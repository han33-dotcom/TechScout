"""Typed observations and deterministic comparison for the item-price experiment."""
import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Brief(Record):
    model: Literal["iPhone 17 Pro"] = "iPhone 17 Pro"
    storage_gb: int | None = Field(default=None, gt=0)
    color: str | None = None
    max_usd: Decimal | None = Field(default=None, ge=0)
    unsupported: list[str] = Field(default_factory=list)


class Claim(Record):
    storage_gb: int = Field(gt=0)
    color: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    currency: Literal["USD"]
    condition: Literal["new", "other", "unknown"]
    stock: Literal["in_stock", "unavailable", "unknown"]
    terms: Literal["ordinary", "conditional", "unknown"]
    # Verbatim snippets from one capture. Binding must identify this priced variant.
    identity_quote: str = Field(min_length=1, max_length=350)
    binding_quote: str = Field(min_length=1, max_length=350)
    stock_quote: str = Field(max_length=250)
    terms_quote: str = Field(max_length=250)
    condition_quote: str = Field(max_length=250)

    @field_validator("amount", "storage_gb", mode="before")
    @classmethod
    def no_float(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("use an exact decimal string or integer")
        return value


class Listing(Record):
    shop: str
    url: str
    title: str


class Observation(Claim):
    listing: Listing
    capture: str
    observed_at: str


def compact(text):
    return " ".join(text.casefold().split())


CAPACITY = re.compile(r"\b(\d+)\s*(gb|tb)\b", re.I)
PRICE = re.compile(r"(?:USD\s*\$?|\$)\s*(\d[\d,]*(?:\.\d+)?)|\b(\d[\d,]*(?:\.\d+)?)\s*(?:USD\b|\$)", re.I)
NON_ORDINARY = re.compile(r"\b(?:trade[\s-]?in|installments?|per month|monthly|deposit|with coupon)\b", re.I)
FAMILY = re.compile(r"\biphone\s*17\s*pro\b(?![\s-]*max\b)", re.I)


def wrong_listing(title):
    accessory = re.search(r"\b(?:cases?|screen protectors?|tempered glass|camera protectors?)\b", title, re.I)
    bundle = re.search(r"\b(?:with|includes?|bundle)\b|\+", title, re.I)
    if accessory and not bundle:
        return "accessory in listing title"
    if re.search(r"\biphone\s*17\s*pro[\s-]*max\b", title, re.I) and not FAMILY.search(title):
        return "Pro Max in listing title"
    return None


def validate_claim(claim, page_text, controls=()):
    """Return a reason for a lead that cannot enter the confirmed comparison."""
    text = compact(page_text)
    for field in ("identity_quote", "binding_quote", "stock_quote", "terms_quote", "condition_quote"):
        quote = getattr(claim, field)
        if quote and compact(quote) not in text:
            return f"{field} is absent from the captured page"
    identity = compact(claim.identity_quote)
    binding = compact(claim.binding_quote)
    if not FAMILY.search(identity) or re.search(r"\b17\s*pro[\s-]*max\b", identity + " " + binding):
        return "model identity is ambiguous or different"
    # A model can quote "iPhone 17 Pro" as a substring of "iPhone 17 Pro Max".
    occurrences = list(re.finditer(re.escape(identity), text))
    if occurrences and all(re.match(r"[\s-]*max\b", text[m.end():]) for m in occurrences):
        return "the full product name is Pro Max"
    sizes = {int(n) * (1024 if unit.lower() == "tb" else 1) for n, unit in CAPACITY.findall(binding)}
    prices = {Decimal((prefix or suffix).replace(",", "")) for prefix, suffix in PRICE.findall(binding)}
    if sizes != {claim.storage_gb} or prices != {claim.amount}:
        return "the quoted price is not bound to one capacity and amount"
    for control in controls:
        selected = str(control.get("selected") or "")
        match = CAPACITY.fullmatch(selected.strip())
        if match and int(match[1]) * (1024 if match[2].lower() == "tb" else 1) != claim.storage_gb:
            return "selected storage conflicts with the priced variant"
    if claim.color.lower() != "unspecified" and compact(claim.color) not in binding:
        return "the quoted price is not bound to the color"
    for status, quote in ((claim.condition, claim.condition_quote), (claim.stock, claim.stock_quote),
                          (claim.terms, claim.terms_quote)):
        if status != "unknown" and not quote:
            return "missing evidence for a classified field"
    if claim.condition == "new" and not re.search(
        r"\bcondition\s*[:=-]?\s*(?:brand\s+)?new\b|\b(?:factory[- ]sealed|brand new)\b|\bnewcondition\b",
        claim.condition_quote, re.I
    ):
        return "new condition is not explicit in the quotation"
    if claim.stock == "in_stock" and not re.search(
        r"\bin[- ]stock\b|\bavailable(?:\s+(?:now|online|for purchase))?\b", claim.stock_quote, re.I
    ):
        return "stock is not explicit in the quotation"
    if claim.terms == "ordinary" and NON_ORDINARY.search(binding + " " + claim.terms_quote):
        return "conditional wording in the ordinary price evidence"
    if claim.stock == "in_stock" and re.search(r"out.of.stock|not in stock|unavailable|call to confirm|sold out", claim.stock_quote, re.I):
        return "in-stock interpretation conflicts with the quoted text"
    return None


def compare(brief, observations):
    """Cheapest verified ordinary item price (USD) per capacity, retaining all ties."""
    groups = {}
    states = {}
    for item in observations:
        if item.terms == "ordinary":
            identity = (item.listing.shop, item.listing.url, item.storage_gb, compact(item.color))
            states.setdefault(identity, set()).add((item.amount, item.stock, item.condition))
    for item in observations:
        identity = (item.listing.shop, item.listing.url, item.storage_gb, compact(item.color))
        if len(states.get(identity, ())) > 1:
            continue
        if brief.unsupported or (brief.storage_gb and item.storage_gb != brief.storage_gb):
            continue
        if brief.color and compact(brief.color) != compact(item.color):
            continue
        if brief.max_usd is not None and item.amount > brief.max_usd:
            continue
        if (item.condition, item.stock, item.terms) != ("new", "in_stock", "ordinary"):
            continue
        groups.setdefault(item.storage_gb, []).append(item)
    winners = []
    for size, offers in sorted(groups.items()):
        minimum = min(offer.amount for offer in offers)
        seen = set()
        for offer in sorted(offers, key=lambda o: (o.listing.shop, o.listing.url, o.color)):
            key = (offer.listing.shop, offer.listing.url, compact(offer.color))
            if offer.amount == minimum and key not in seen:
                winners.append(offer)
                seen.add(key)
    return winners
