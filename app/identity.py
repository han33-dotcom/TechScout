"""Variant identity (MVP 07): canonical device values, link hints and repeated records.

Readers keep values as each store writes them ('bleu', 'Black'); identity maps them onto our own
tables so offers from different stores compare. Only values change: every fact keeps its state and
evidence, and unknown or conflicting facts pass through untouched. Links are hints, never facts: a
link that names another color adds a warning with fixed wording and leaves the color fact alone.
"""
from collections.abc import Sequence
from datetime import datetime, timezone
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit

from pydantic import Field

from app.contracts import Fact, Model, Offer, PositiveInt, Sim, Text
from app.devices import KNOWN_MANUFACTURERS, canonical_manufacturer, find_model, spell_plus

IDENTITY_FIELDS = ('manufacturer', 'model', 'storage_gb', 'color', 'sim', 'region')

# Spellings (casefolded words, accents dropped) of each canonical color: generic names, the colors of
# the phones in app.devices.KNOWN_MODELS, and French names Lebanese stores use ('bleu' at Mojitech).
# 'Mist Blue' and 'Blue' stay distinct, as do 'Sage' and 'Green': a store's plain 'Blue' does not
# show which finish it sells, so only a buyer's own choice of colors can treat them as one.
_COLORS = {
    'Black': ('black', 'noir', 'noire'),
    'White': ('white', 'blanc', 'blanche'),
    'Blue': ('blue', 'bleu'),
    'Green': ('green', 'vert', 'verte'),
    'Red': ('red', 'rouge', 'product red'),
    'Pink': ('pink',),
    'Purple': ('purple',),
    'Yellow': ('yellow', 'jaune'),
    'Orange': ('orange',),
    'Gold': ('gold',),
    'Silver': ('silver', 'argent'),
    'Gray': ('gray', 'grey', 'gris', 'grise'),
    'Lavender': ('lavender', 'lavande'),
    'Sage': ('sage', 'sauge'),
    'Mist Blue': ('mist blue', 'bleu brume'),
    'Teal': ('teal',),
    'Ultramarine': ('ultramarine',),
    'Deep Blue': ('deep blue', 'bleu intense'),
    'Cosmic Orange': ('cosmic orange', 'orange cosmique'),
    'Sky Blue': ('sky blue', 'bleu ciel'),
    'Light Gold': ('light gold',),
    'Cloud White': ('cloud white',),
    'Space Black': ('space black', 'noir sideral'),
    'Black Titanium': ('black titanium', 'titane noir'),
    'White Titanium': ('white titanium', 'titane blanc'),
    'Natural Titanium': ('natural titanium', 'titane naturel'),
    'Desert Titanium': ('desert titanium',),
}
COLOR_ALIASES = {alias: name for name, aliases in _COLORS.items() for alias in aliases}


def _phrase_index(aliases: dict[str, str]) -> dict[str, list[tuple[tuple[str, ...], str]]]:
    """Spellings by first word, longest first, so 'space black' never reads as plain 'black'."""
    index = {}
    for alias, name in sorted(aliases.items(), key=lambda pair: -len(pair[0].split())):
        index.setdefault(alias.split()[0], []).append((tuple(alias.split()), name))
    return index


_PHRASES = _phrase_index(COLOR_ALIASES)

# Query parameters that carry a color, as in WooCommerce's attribute_pa_color=white.
_COLOR_PARAMS = ('color', 'colour', 'couleur')
_LINK_SCOPES = ('product', 'variant')
# Longer links are not ordinary product links, so they are not read for hints.
_MAX_LINK = 2048
_MAKER = re.compile(r'^\W*(?:' + '|'.join(re.escape(m) for m in KNOWN_MANUFACTURERS) + r')(?![0-9a-z])',
                    re.IGNORECASE)
_PHONE_SCOPES = ('product', 'variant', 'branch')
_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)
_DISAGREE = 'repeated records for this variant disagree'


class NormalizedVariant(Model):
    """One offer's device identity in canonical values, for eligibility and duplicate checks."""
    offer_id: Text
    source_id: Text
    product_id: Text
    variant_id: Text
    manufacturer: Fact[Text]
    model: Fact[Text]
    storage_gb: Fact[PositiveInt]
    color: Fact[Text]
    sim: Fact[Sim]
    region: Fact[Text]
    warnings: list[Text] = Field(default_factory=list)  # fixed wording; hints never change a fact

    @property
    def identity_key(self) -> tuple | None:
        """(manufacturer, model, storage, color, sim, region) when all six are known, else None."""
        facts = [getattr(self, name) for name in IDENTITY_FIELDS]
        if any(fact.state != 'known' for fact in facts):
            return None
        return tuple(fact.value for fact in facts)


class DedupResult(Model):
    offers: list[Offer]
    merged: list[Text] = Field(default_factory=list)  # fixed wording with our offer IDs, never store text
    conflicts: list[Text] = Field(default_factory=list)


def _words(text: str) -> list[str]:
    """Casefolded words without accents, so 'Mist-Blue', 'mist blue' and 'sidéral'/'sideral' read alike."""
    folded = unicodedata.normalize('NFKD', text.casefold())
    return re.findall(r'[^\W_]+', ''.join(c for c in folded if not unicodedata.combining(c)))


def known_color(name: str) -> str | None:
    """Our canonical name for a color in the table, or None; only these are safe to show in reasons."""
    return COLOR_ALIASES.get(' '.join(_words(name)))


def canonical_color(name: str) -> str:
    """The table's name for a known spelling ('bleu' is Blue), else the name in stripped title case."""
    known = known_color(name)
    if known is not None:
        return known
    text = ' '.join(name.split())
    if not text:
        raise ValueError('A color name needs text')
    return text.title()


def _squash(text: str) -> str:
    return re.sub(r'[\W_]+', '', text.casefold())


def known_model(text: str) -> str | None:
    """Our model name when the text is exactly one known model, optionally after its maker's name.

    match_model finds a model anywhere in a product name; a model value must be that model alone,
    since 'iPhone 17 Plus' contains 'iPhone 17' but would be another phone. A '+' attached to the
    model is spelled out first, so 'iPhone 16+' is 'iPhone 16 Plus' and 'iPhone 17+' is no model.
    """
    text = spell_plus(text)
    found = find_model(text)
    # Compared with the spelling found, so the alias 'iPhone 17 Air' is the iPhone Air.
    if found is None or _squash(_MAKER.sub('', text, count=1)) != _squash(found[0]):
        return None
    return found[1]


def _text(value: str) -> str:
    return ' '.join(value.split())


def _manufacturer(value: str) -> str:
    return canonical_manufacturer(value) or _text(value)


def _model(value: str) -> str:
    return known_model(value) or _text(value)


def _same(value):
    return value


# How each identity value is canonicalized. Region names have no table yet (no store publishes one),
# so they are compared as written.
_RENAME = {'manufacturer': _manufacturer, 'model': _model, 'storage_gb': _same, 'color': canonical_color,
           'sim': _same, 'region': _text}


def _canonical(fact: Fact, rename) -> Fact:
    """The same fact with its value renamed; unknowns and conflicts pass through untouched."""
    if fact.state != 'known':
        return fact
    return type(fact)(state='known', value=rename(fact.value), evidence_ids=list(fact.evidence_ids))


def _links(offer: Offer) -> list[str]:
    """The listing's own links: its permalink and the product pages its evidence was read from."""
    links = []
    permalink = offer.variant.raw_attributes.get('permalink')
    if isinstance(permalink, str):
        links.append(permalink)
    for record in offer.evidence:
        if record.scope in _LINK_SCOPES:
            links += [str(record.origin_url), str(record.final_url)]
    return list(dict.fromkeys(links))


def _colors_in(text: str) -> set[str]:
    """Canonical colors written as whole words in free text such as a product slug."""
    words, found, i = _words(text), set(), 0
    while i < len(words):
        for phrase, name in _PHRASES.get(words[i], ()):
            if tuple(words[i:i + len(phrase)]) == phrase:
                found.add(name)
                i += len(phrase)
                break
        else:
            i += 1
    return found


def _link_colors(offer: Offer) -> tuple[set[str], set[str]]:
    """Colors the links name: canonical names from our table, and color parameters we cannot map."""
    named, unmapped = set(), set()
    for link in _links(offer):
        if len(link) > _MAX_LINK:
            continue
        try:
            parts = urlsplit(link)
        except ValueError:
            continue
        for key, value in parse_qsl(parts.query):
            if any(word in key.casefold() for word in _COLOR_PARAMS):
                words = ' '.join(_words(value))
                if words in COLOR_ALIASES:
                    named.add(COLOR_ALIASES[words])
                elif words:
                    unmapped.add(words)
            elif key.casefold() == 'slug':
                named |= _colors_in(value)
        # Only the last path segment names the product; folders such as /black-friday/ do not.
        segments = [s for s in unquote(parts.path).split('/') if s]
        if segments:
            named |= _colors_in(segments[-1])
    return named, unmapped


def _color_warnings(color: Fact, named: set[str], unmapped: set[str]) -> list[str]:
    """Fixed wording plus colors from our table only; a store's own color text never appears."""
    warnings = []
    if color.state == 'known':
        ours = known_color(color.value)
        for hint in sorted(named - {ours}):
            warnings.append(f'page link names color {hint}; selected variant is {ours}' if ours
                            else f'page link names color {hint}; selected variant is a different color')
        if unmapped - {' '.join(_words(color.value))}:
            warnings.append('page link names a different color than the selected variant')
    else:
        warnings += [f'page link names color {hint}; selected variant color is not settled'
                     for hint in sorted(named)]
        if unmapped:
            warnings.append('page link names a color; selected variant color is not settled')
    return warnings


def normalize_variant(offer: Offer) -> NormalizedVariant:
    variant = offer.variant
    return NormalizedVariant(
        offer_id=offer.offer_id, source_id=offer.source_id, product_id=variant.product_id,
        variant_id=variant.variant_id,
        **{name: _canonical(getattr(variant, name), _RENAME[name]) for name in IDENTITY_FIELDS},
        warnings=_color_warnings(variant.color, *_link_colors(offer)))


def same_device(a: NormalizedVariant, b: NormalizedVariant) -> bool | None:
    """False as soon as a known fact differs; True only when all six facts are known and equal.

    Anything else, an unknown or conflicting fact with no known difference, leaves it open (None).
    No store publishes a region yet, so offers that differ in a known fact are still told apart.
    """
    pairs = [(getattr(a, name), getattr(b, name)) for name in IDENTITY_FIELDS]
    if any(x.state == y.state == 'known' and x.value != y.value for x, y in pairs):
        return False
    if all(x.state == y.state == 'known' for x, y in pairs):
        return True
    return None


def _observed(offer: Offer) -> datetime:
    """When the phone itself was last observed; store-wide policy pages read later do not count."""
    own = [e.observed_at for e in offer.evidence if e.scope in _PHONE_SCOPES]
    return max(own or [e.observed_at for e in offer.evidence], default=_EARLIEST)


def _merge(records: list[Offer]) -> tuple[Offer, list[str]]:
    """The first (latest) record, with each identity fact the records disagree on made a conflict.

    A value one record lacks is not a disagreement; the latest record is kept as it is.
    """
    latest = records[0]
    normalized = [normalize_variant(record) for record in records]
    changes, disagreements = {}, []
    for name in IDENTITY_FIELDS:
        facts = [getattr(n, name) for n in normalized]
        if all(fact == facts[0] for fact in facts):
            continue
        values = list(dict.fromkeys(
            value for fact in facts
            for value in ([fact.value] if fact.state == 'known' else map(_RENAME[name], fact.alternatives))))
        if len(values) < 2:
            continue
        evidence_ids = list(dict.fromkeys(i for fact in facts for i in fact.evidence_ids))
        changes[name] = {'state': 'conflict', 'alternatives': values, 'reason': _DISAGREE,
                         'evidence_ids': evidence_ids}
        disagreements.append(name)
    if not changes:
        return latest, []
    data = latest.model_dump(mode='json')
    data['variant'].update(changes)
    wanted = {i for change in changes.values() for i in change['evidence_ids']}
    have = {record.evidence_id for record in latest.evidence}
    for offer in records[1:]:
        for record in offer.evidence:
            if record.evidence_id in wanted and record.evidence_id not in have:
                data['evidence'].append(record.model_dump(mode='json'))
                have.add(record.evidence_id)
    return Offer.model_validate(data), disagreements


def deduplicate(offers: Sequence[Offer]) -> DedupResult:
    """One offer per actual variant, keyed by (source, product, variant).

    A SKU is never a key: Class Cellulars shares one SKU across four colors. Different colors or
    SIMs are different variants and never merge. A product-level record is dropped when the same
    product has variant offers.
    """
    groups: dict[tuple[str, str, str], list[tuple[int, Offer]]] = {}
    for index, offer in enumerate(offers):
        key = (offer.source_id, offer.variant.product_id, offer.variant.variant_id)
        groups.setdefault(key, []).append((index, offer))
    kept, merged, conflicts = {}, [], []
    for key, records in groups.items():
        if len(records) == 1:
            kept[key] = records[0][1]
            continue
        # Latest observation first; at equal times the record produced later wins.
        ordered = [offer for _, offer in sorted(records, key=lambda r: (_observed(r[1]), r[0]), reverse=True)]
        offer, disagreements = _merge(ordered)
        kept[key] = offer
        merged.append(f'{offer.offer_id}: kept the latest of {len(records)} records')
        conflicts += [f'{offer.offer_id} variant.{name}: {_DISAGREE}' for name in disagreements]
    with_variants = {(source_id, product_id) for source_id, product_id, variant_id in kept
                     if variant_id != product_id}
    result = []
    for (source_id, product_id, variant_id), offer in kept.items():
        if variant_id == product_id and (source_id, product_id) in with_variants:
            merged.append(f'{offer.offer_id}: product-level record dropped; the product has variant offers')
        else:
            result.append(offer)
    return DedupResult(offers=result, merged=merged, conflicts=conflicts)
