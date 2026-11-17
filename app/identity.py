"""Variant identity (MVP 07): canonical device values, link hints and repeated records.

Readers keep values as each store writes them ('bleu', 'Black'); identity maps them onto our own
tables so offers from different stores compare. Only values change: every fact keeps its state and
evidence, and unknown or conflicting facts pass through untouched. Links are hints, never facts: a
link that names another color adds a warning with fixed wording and leaves the color fact alone.
"""
from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import unicodedata
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

from pydantic import Field

from app.contracts import Fact, Model, Offer, PositiveInt, Sim, Text
from app.devices import KNOWN_MANUFACTURERS, canonical_manufacturer, find_model, spell_plus
from app import contracts_v2 as v2

IDENTITY_FIELDS = ('manufacturer', 'model', 'storage_gb', 'color', 'sim', 'region')

# Spellings (casefolded words, accents dropped) of each canonical color: generic names, the colors of
# the phones in app.devices.KNOWN_MODELS, and French names Lebanese stores use ('bleu' at Mojitech).
# 'Mist Blue' and 'Blue' stay distinct, as do 'Sage' and 'Green': a store's plain 'Blue' does not
# show which finish it sells. Eligibility leaves a requested finish unresolved, not failed, when a
# store writes only its family (COLOR_FAMILIES).
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
    'Space Gray': ('space gray', 'space grey'),
    'Black Titanium': ('black titanium', 'titane noir'),
    'White Titanium': ('white titanium', 'titane blanc'),
    'Natural Titanium': ('natural titanium', 'titane naturel'),
    'Desert Titanium': ('desert titanium',),
}
COLOR_ALIASES = {alias: name for name, aliases in _COLORS.items() for alias in aliases}
# The generic color family of each named finish, for finishes that belong to one family only. A
# store writing the family ('Blue') may be selling the finish ('Mist Blue'); Teal and the Natural
# and Desert Titanium finishes sit between families and have none.
COLOR_FAMILIES = {'Mist Blue': 'Blue', 'Deep Blue': 'Blue', 'Sky Blue': 'Blue', 'Ultramarine': 'Blue',
                  'Sage': 'Green', 'Lavender': 'Purple', 'Cosmic Orange': 'Orange', 'Light Gold': 'Gold',
                  'Cloud White': 'White', 'White Titanium': 'White', 'Space Black': 'Black',
                  'Space Gray': 'Gray', 'Black Titanium': 'Black'}


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
    text = _text(name)
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
    """Spacing collapsed. str.split() also breaks on U+001C to U+001F, which the contract's strip
    keeps, so a valid value made only of them stays as written instead of renaming to nothing."""
    return ' '.join(value.split()) or value.strip(' \t\n\r\f\v')


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


# Full-product identity operates on v2 offers. The v1 reader/eligibility path above stays intact.
# These fields define the supported comparison profile, not every possible physical property.
_PHONE_FIELDS = ('storage_gb', 'sim', 'region')
_LAPTOP_FIELDS = (
    'cpu', 'gpu', 'ram_gb', 'storage_gb', 'operating_system', 'display_inches',
    'display_width_px', 'display_height_px', 'display_refresh_hz', 'display_panel',
    'display_touchscreen',
)
# Tablet generation, chip and screen size have no TabletSpecs field: the evidenced model name
# carries them. Stylus support is a capability of the model, not a configuration a buyer picks.
_TABLET_FIELDS = ('storage_gb', 'connectivity')
_PROFILE_FIELDS = {
    'phone': _PHONE_FIELDS,
    'laptop': _LAPTOP_FIELDS,
    'monitor': ('diagonal_inches', 'width_px', 'height_px', 'refresh_hz', 'panel'),
    'audio': ('form_factor', 'connectivity', 'active_noise_cancellation'),
    'tablet': _TABLET_FIELDS,
}


def _specific_model(value: str, manufacturer: str) -> str | None:
    """Reject generic descriptions and explicit ambiguity; never guess a generation.

    Exact source model/version strings are otherwise kept whole. Product names
    without a numeric SKU are valid; punctuation and version suffixes are not lost.
    """
    text = _identity_text(value)
    if re.search(r'\b(?:or|series|assorted|various|unspecified|unknown)\b|[/|]', text):
        return None
    generic = {'monitor', 'display', 'screen', 'gaming', 'oled', 'lcd', 'led', 'ips',
               'headphones', 'headphone', 'earbuds', 'earbud', 'headset', 'buds',
               'wireless', 'wired', 'bluetooth', 'noise', 'cancelling', 'canceling',
               'anc', 'stereo', 'inch', 'inches', 'hz', '4k', '2k', 'uhd', 'fhd',
               *_identity_text(manufacturer).split()}
    tokens = re.findall(r'[\w.-]+', text)
    specific = [token for token in tokens if token not in generic and re.search(r'[a-z]', token)
                and not re.fullmatch(r'\d+(?:\.\d+)?(?:inch|in|hz|p)',token)]
    return text if specific else None

# Tablet model names, after the shared normalization (NFKC, casefold, dashes). Retail spellings of
# a screen size: 11", 11'', 11 in, 11 inch, 11-inch, the double prime (two primes after NFKC) and
# curly quotes; a decimal comma ('12,9"') is a decimal point. Only tablet sizes (7 to 19.99 inches)
# are read, so '2-in-1' or '128 in' never becomes a size and '1.1-inch' never becomes '11-inch'.
_TABLET_DECIMAL_COMMA = re.compile(r'(?<=\d),(?=\d{1,2}(?![\d,]))')
_TABLET_SIZE = re.compile(r'(?<![\w.])((?:[7-9]|1\d)(?:\.\d{1,2})?)(?:-| )?'
                          r'(?:inches|inch|pouces|in\.?(?!\w)|"|\'\'|′′|”|“)')
_TABLET_SIZES = re.compile(r'(?<![\w.])(\d{1,2}(?:\.\d{1,2})?)-inch(?!\w)')
# 'ipad-pro', 'ipadpro' and 'ipad​pro' are 'ipad pro'.
_TABLET_IPAD_LINE = re.compile(r'(?<!\w)ipad[\s_-]*(air|pro|mini)(?!\w)')
# '10th gen', '10th-gen', '10th gen.' and '10th generation' are one spelling; so are 'gen 10'
# and 'generation 10', but never the storage or size after an ordinal ('9th generation 64 gb').
_TABLET_ORDINAL = re.compile(r'(?<!\w)(\d{1,2})(st|nd|rd|th)[ -]?gen(?:eration|\.)?(?!\w)')
_TABLET_GEN_NUMBER = re.compile(r'(?<!\d(?:st|nd|rd|th) )(?<!\w)gen(?:eration)?\.? ?(\d{1,2})'
                                r'(?![\w.]| ?(?:gb|tb|go|to)(?!\w)|-inch)')
# A spaced '+' that ends the name, or is followed only by storage or connection words, is the
# name's 'Plus', as spell_plus spells 'S9+'. Any other spaced '+' joins a bundle ('S9 + Keyboard').
_TABLET_SPACED_PLUS = re.compile(r'(?<=\w) \+(?=$| (?:\d+ ?(?:gb|tb|go|to)|wi-?fi|cellular|lte|[345]g)(?!\w))')
_TABLET_BUNDLE_PLUS = re.compile(r'(?<=\w) \+ ')
# Storage and connection words in a name are checked against the offer's own storage_gb and
# connectivity facts; a name that contradicts them is not trusted. They stay in the compared name.
# Memory is not storage: '8gb ram', 'ram 8gb' and the first number of '8/128' or '8gb/128gb' are
# skipped, and a memory/storage pair is checked by its second number. 'Go' and 'To' are the French
# units.
_TABLET_PAIR = re.compile(r'(?<![\w.])\d{1,2} ?(?:gb|go)? ?/ ?(\d{2,4}) ?(gb|tb|go|to)?(?!\w)')
_TABLET_STORAGE = re.compile(r'(?<![\w./+])(?<!ram )(\d{1,4}) ?(gb|tb|go|to)(?! ?ram)(?! ?[/+] ?\d)(?!\w)')
_TABLET_CELLULAR = re.compile(r'(?<!\w)(?:cellular|lte|[345]g)(?!\w)')
_TABLET_WIFI = re.compile(r'(?<!\w)wi-?fi(?!\w)')
# A name identifies one model only with positive evidence of its edition: an ordinal generation, a
# year, a chip, or a model token joining letters and digits ('s9', 'p11', 'y700', 'sm-x710'). A
# bare number never counts: retail titles carry sizes, storage, warranty years, OS versions and
# accessory numbers ('Pencil 2', 'Windows 11') that name no edition.
_TABLET_EDITION = re.compile(r'(?<!\w)(?:\d{1,2}(?:st|nd|rd|th) generation|(?:19|20)\d{2})(?!\w)')
_TABLET_CHIP = re.compile(r'(?<!\w)(?:m[1-9]|a\d{1,2}[xz]?)(?: bionic| fusion| pro)?(?!\w)')
_TABLET_LINE_TOKEN = re.compile(r'(?<![\w.])(?!(?:wi-?fi|win|ios|ipados|android|usb|gen|lte|ram|rom|hz|mp)\d)'
                                r'[a-z]{1,3}\d{1,4}[a-z]?(?![\w.])')
# Edition tokens shared by two generations of one line: the base iPad's A10 Fusion (6th and 7th)
# and its two 2012 models, and the iPad mini's A7 (mini 2 and 3).
_TABLET_BASE_IPAD = re.compile(r'(?<!\w)ipad(?!\w)(?! (?:air|pro|mini)(?!\w))')
_TABLET_IPAD_MINI = re.compile(r'(?<!\w)ipad mini(?!\w)')
# Lines sold in more than one screen size within a generation need the size in the name.
_TABLET_SIZED_LINES = re.compile(r'(?<!\w)ipad (?:air|pro)(?!\w)')
# Lines sold under one name across several editions need a year or a generation in the name. They
# are matched without spaces or hyphens, with the 'galaxy' and 'tab' words optional. This is a
# short owned list, not a catalog: other reused names can still match (see the docs).
_TABLET_DATED_LINES = re.compile(r'(?:s6lite|tabm(?:10(?:plus|hd)?|8|7)(?![\da-z])|(?<![a-z])m(?:10(?:plus|hd)?|8|7)(?![\da-z])'
                                 r'|p11(?:plus|pro)?|firehd\d{1,2}|fire7|redmipad(?:se)?|a7(?:lite)?(?![\da-z])|legiony700)')

class OfferComparison(Model):
    hardware: v2.MatchAssessment
    differing_identity_fields: tuple[Text, ...] = ()
    unresolved_identity_fields: tuple[Text, ...] = ()
    # Applies only to the enumerated terms, never prices, fulfillment or complete purchases.
    terms_outcome: Literal['matching', 'different', 'unresolved']
    differing_terms: tuple[Text, ...] = ()
    unresolved_terms: tuple[Text, ...] = ()


class VariantGroup(Model):
    identity_key: Text | None
    offers: tuple[v2.Offer, ...]


def _identity_text(value: str) -> str:
    # Preserve punctuation, suffixes and token boundaries: 1355U != 1355H, 2.4 != 24,
    # S25+ != S25. Only typographic dashes and whitespace/case are normalized.
    text = unicodedata.normalize('NFKC', value).casefold()
    text = re.sub(r'[\u2010-\u2015]', '-', text)
    text = re.sub(r'\s*-\s*', '-', text)
    return ' '.join(text.split())


def _identity_value(path: str, value):
    if not isinstance(value, str):
        return value
    if path == 'manufacturer':
        aliases = {'hewlett-packard': 'hp', 'hewlett packard': 'hp',
                   'asus': 'asus', 'asustek': 'asus'}
        return aliases.get(_identity_text(value), _identity_text(value)) or None
    if path == 'model':
        # Exact aliases only; laptop/future model strings do not need an enumerated catalog.
        return _identity_text(known_model(value) or spell_plus(value)) or None
    if path == 'color':
        return _identity_text(canonical_color(value)) or None
    if path == 'region':
        return {'lebanon': 'lb', 'united states': 'us', 'united arab emirates': 'ae'}.get(
            _identity_text(value), _identity_text(value)) or None
    return _identity_text(value) or None


def _ordinal(number: int) -> str:
    suffix = 'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    return f'{number}{suffix}'


def _names_one_edition(text: str) -> bool:
    """Whether the name carries positive evidence of one edition (see _TABLET_EDITION)."""
    found = [m.group(0) for pattern in (_TABLET_EDITION, _TABLET_CHIP, _TABLET_LINE_TOKEN)
             for m in pattern.finditer(text)]
    shared = set()
    if _TABLET_BASE_IPAD.search(text):
        shared |= {'a10', 'a10 fusion', '2012'}
    if _TABLET_IPAD_MINI.search(text):
        shared |= {'a7'}
    return any(token not in shared for token in found)


def _tablet_model(text: str, offer: v2.Offer, maker: str | None) -> str | None:
    """One spelling of a tablet model name, or None when it does not name one model.

    Size and generation spellings are unified ('11"', '11 in' and '11-inch'; '10th gen' and
    'gen 10'), a spaced '+' that ends the name is its 'Plus', and parentheses, commas and a
    leading maker that repeats the evidenced manufacturer drop out. Storage and Wi-Fi/cellular
    words are checked against the offer's own storage_gb and connectivity facts: a name that
    contradicts them is not trusted. Those words stay in the compared name, so they can only keep
    two names apart, never merge them.

    A name identifies one model only with positive evidence of its edition: an ordinal generation,
    a year, a chip or a letters-and-digits model token. 'iPad Air', 'iPad 10.2' and 'Surface Pro
    Windows 11' carry none. A name also stays unresolved when it is an iPad Air or Pro without its
    size, names two sizes, joins a bundle with '+', or is a line sold across several editions
    without a year or generation ('Galaxy Tab S6 Lite'). Numbers, chips and suffixes stay exact:
    M2 is not M3, S9 is not S9 FE, LTE is not 5G, 12.9 is not 13.
    """
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')  # zero-width and soft hyphens
    text = _TABLET_DECIMAL_COMMA.sub('.', text)
    text = _TABLET_IPAD_LINE.sub(lambda m: f'ipad {m.group(1)}', text)
    text = _TABLET_SIZE.sub(lambda m: f'{m.group(1)}-inch', text)
    text = _TABLET_ORDINAL.sub(lambda m: f'{m.group(1)}{m.group(2)} generation', text)
    text = _TABLET_GEN_NUMBER.sub(lambda m: f'{_ordinal(int(m.group(1)))} generation', text)
    text = _TABLET_SPACED_PLUS.sub(' plus', text)
    if _TABLET_BUNDLE_PLUS.search(text):
        return None
    specs = offer.variant.specs
    stated = [(amount, unit or 'gb') for amount, unit in _TABLET_PAIR.findall(text)]
    for amount, unit in [*stated, *_TABLET_STORAGE.findall(text)]:
        sizes = {int(amount)} if unit in ('gb', 'go') else {int(amount) * 1000, int(amount) * 1024}
        if specs.storage_gb.state == 'known' and specs.storage_gb.value not in sizes:
            return None
    named = 'cellular' if _TABLET_CELLULAR.search(text) else 'wifi' if _TABLET_WIFI.search(text) else None
    if named is not None and specs.connectivity.state == 'known' and specs.connectivity.value != named:
        return None
    words = re.sub(r'[(),]', ' ', text).split()
    if maker and words[:len(maker.split())] == maker.split():
        words = words[len(maker.split()):]
    text = ' '.join(words)
    if not _names_one_edition(text):
        return None
    sizes = set(_TABLET_SIZES.findall(text))
    if len(sizes) > 1 or (_TABLET_SIZED_LINES.search(text) and not sizes):
        return None
    if _TABLET_DATED_LINES.search(re.sub(r'[\s-]', '', text)) and not _TABLET_EDITION.search(text):
        return None
    return text


def _identity_facts(offer: v2.Offer) -> dict[str, tuple[object | None, tuple[str, ...]]]:
    """Known normalized values with their own source evidence; missing is never a wildcard."""
    variant = offer.variant
    facts = {}
    for name in ('manufacturer', 'model'):
        ids = tuple(e.evidence_id for e in offer.evidence
                    if e.field_path == 'variant.product.' + name
                    and e.scope in ('product', 'variant')
                    and e.product_id == offer.source_product_id
                    and (e.scope != 'variant' or e.variant_id == offer.source_variant_id))
        value = _identity_value(name, getattr(variant.product, name)) if ids else None
        if ids and name == 'model' and variant.product.category in ('monitor', 'audio'):
            value = _specific_model(variant.product.model,variant.product.manufacturer)
        elif value is not None and name == 'model' and variant.product.category == 'tablet':
            value = _tablet_model(value, offer, facts['manufacturer'][0])
        facts[name] = (value, ids)
    names = _PROFILE_FIELDS.get(variant.product.category)
    if names is None:
        return facts
    for name in (*names, 'color'):
        fact = variant.color if name == 'color' else getattr(variant.specs, name)
        facts[name] = ((_identity_value(name, fact.value), tuple(fact.evidence_ids))
                       if fact is not None and fact.state == 'known' else (None, ()))
    return facts


def _terms(offer: v2.Offer) -> dict[str, Fact | None]:
    terms = {'condition': offer.condition, 'payment': offer.payment}
    if offer.variant.product.category == 'phone' or offer.activation is not None:
        terms['activation'] = offer.activation
    for name in ('provider', 'months', 'territory'):
        terms['warranty.' + name] = getattr(offer.warranty, name) if offer.warranty else None
    return terms


def compare_offers(left: v2.Offer, right: v2.Offer) -> OfferComparison:
    """Compare supported evidenced hardware, separately exposing purchase-term differences.

    A decisive known difference wins over other unknowns. Unsupported categories and absent
    evidence remain unresolved. Never rewrite source/canonical IDs or lend facts across sellers.
    """
    if left.offer_id == right.offer_id:
        raise ValueError('Compare distinct offer observations')
    a, b = _identity_facts(left), _identity_facts(right)
    category_a, category_b = left.variant.product.category, right.variant.product.category
    differing, unresolved, evidence = [], [], []
    if category_a != category_b:
        # Category metadata has no field-bound Fact in v2; do not invent evidence for it.
        unresolved.append('category')
    elif category_a not in _PROFILE_FIELDS:
        unresolved.append('unsupported_category')
    else:
        for name in a:
            av, ae = a[name]
            bv, be = b[name]
            if av is None or bv is None:
                unresolved.append(name)
            elif av != bv:
                differing.append(name)
                evidence.extend((*ae, *be))
        if not differing and not unresolved:
            evidence = [eid for pairs in (a, b) for _, ids in pairs.values() for eid in ids]
    outcome = ('different_configuration' if differing else
               'unresolved' if unresolved else 'confirmed_equivalent')
    if differing and left.variant.variant_id == right.variant.variant_id:
        # A reused canonical ID cannot override conflicting hardware. Keep the conflict visible;
        # the existing MatchAssessment wire contract forbids different configurations with one ID.
        outcome = 'unresolved'
        unresolved.append('canonical_variant_id_collision')
    reasons = tuple([*('identity field differs: ' + name for name in differing),
                     *('identity field unresolved: ' + name for name in unresolved)]
                    or ['All supported hardware identity fields agree with source evidence'])
    ta, tb = _terms(left), _terms(right)
    term_differences, term_unknowns = [], []
    for name in dict.fromkeys((*ta, *tb)):
        x, y = ta.get(name), tb.get(name)
        if x is None or y is None or x.state != 'known' or y.state != 'known':
            term_unknowns.append(name)
        elif x.value != y.value:
            term_differences.append(name)
    return OfferComparison(
        hardware=v2.MatchAssessment(
            left_offer_id=left.offer_id, right_offer_id=right.offer_id,
            left_variant_id=left.variant.variant_id, right_variant_id=right.variant.variant_id,
            outcome=outcome, reasons=reasons, evidence_ids=tuple(dict.fromkeys(evidence))),
        differing_identity_fields=tuple(differing), unresolved_identity_fields=tuple(unresolved),
        terms_outcome='different' if term_differences else 'unresolved' if term_unknowns else 'matching',
        differing_terms=tuple(term_differences), unresolved_terms=tuple(term_unknowns),
    )


def variant_identity_key(offer: v2.Offer) -> str | None:
    """Versioned cross-seller hardware key only for a fully evidenced supported configuration."""
    if offer.variant.product.category not in _PROFILE_FIELDS:
        return None
    facts = _identity_facts(offer)
    if any(value is None for value, _ in facts.values()):
        return None
    values = {name: _decimal_identity(value) if isinstance(value, Decimal) else value
              for name, (value, _) in facts.items()}
    payload = json.dumps([offer.variant.product.category, values], sort_keys=True,
                         ensure_ascii=True, separators=(',', ':'))
    return 'hardware-v1:' + hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _decimal_identity(value: Decimal) -> tuple:
    # Decimal.normalize() rounds in the caller's decimal context. A hardware key must not
    # collapse distinct exact values, even when that context has low precision.
    sign, digits, exponent = value.as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return sign, digits, exponent


def seller_offer_key(offer: v2.Offer) -> tuple[str | None, ...]:
    """A source-local listing identity includes its merchant, fulfiller and purchase route."""
    return (offer.retailer_id, offer.source_id, offer.seller.seller_id, offer.fulfiller_id,
            offer.source_product_id, offer.source_variant_id, offer.route.kind, offer.route.route_id)


def group_variants(offers: Sequence[v2.Offer]) -> tuple[VariantGroup, ...]:
    """Deduplicate hardware groups while preserving every seller offer and its evidence.

    Unknown configurations stay separate even when a source SKU or canonical variant ID agrees.
    Repeated identical payloads are idempotent. Conflicting payloads for a global offer ID must
    be resolved by the caller rather than silently selecting one by an unrelated policy timestamp.
    """
    groups: list[list[v2.Offer]] = []
    keys: list[str | None] = []
    positions: dict[str, int] = {}
    seen: dict[str, v2.Offer] = {}
    for offer in offers:
        if offer.offer_id in seen:
            if seen[offer.offer_id] != offer:
                raise ValueError('Conflicting observations share an offer ID')
            continue
        seen[offer.offer_id] = offer
        key = variant_identity_key(offer)
        if key is not None and key in positions:
            groups[positions[key]].append(offer)
        else:
            if key is not None:
                positions[key] = len(groups)
            keys.append(key)
            groups.append([offer])
    return tuple(VariantGroup(identity_key=key, offers=tuple(items))
                 for key, items in zip(keys, groups))
