"""Device vocabulary shared by the store readers (MVP 05) and identity (MVP 07).

Only our own tables live here, so names matched against them are safe to use in reasons.
"""
import re

KNOWN_MANUFACTURERS = ('Apple', 'Samsung', 'Google', 'Xiaomi', 'Huawei', 'Honor', 'OnePlus', 'Oppo',
                       'Nokia', 'Motorola', 'Lenovo', 'HP', 'Dell', 'Asus', 'Acer', 'MSI', 'Microsoft')
# Each model is matched as whole words, longest spelling first, so 'iPhone 17 Pro' never reads as
# 'iPhone 17'.
KNOWN_MODELS = ('iPhone 17 Pro Max', 'iPhone 17 Pro', 'iPhone 17e', 'iPhone Air', 'iPhone 17',
                'iPhone 16 Pro Max', 'iPhone 16 Pro', 'iPhone 16 Plus', 'iPhone 16e', 'iPhone 16',
                'Galaxy S25 Ultra', 'Galaxy S25 Plus', 'Galaxy S25', 'Galaxy A27',
                'Galaxy S24 Ultra', 'Galaxy S24 Plus', 'Galaxy S24',
                'Pixel 9 Pro XL', 'Pixel 9 Pro', 'Pixel 9', 'Pixel 9a')
# Other spellings stores use for one of KNOWN_MODELS. Apple sells one Air, the 'iPhone Air'.
MODEL_ALIASES = {'iPhone 17 Air': 'iPhone Air'}


def _pattern(name: str) -> re.Pattern:
    words = (re.escape(w) for w in name.split())
    return re.compile(r'(?<![0-9a-z])' + r'\s*'.join(words) + r'(?![0-9a-z])', re.IGNORECASE)


_SPELLINGS = {**{m: m for m in KNOWN_MODELS}, **MODEL_ALIASES}
# (spelling, canonical name, pattern), longest spelling first.
_MODELS = sorted(((s, m, _pattern(s)) for s, m in _SPELLINGS.items()), key=lambda t: -len(t[0]))
# A '+' written right after a model ('iPhone 16+', Samsung's 'Galaxy S25+') names the Plus model,
# also when a storage size follows it directly ('iPhone 16+128GB'). A spaced '+' ('iPhone 17 +
# AirPods') or one followed by another item ('iPhone 16+AirPods', 'iPhone 16+20W Adapter') joins
# a bundle and is left alone.
_PLUS = re.compile(r'(?<=[0-9a-z])\+(?:(?![0-9a-z])|(?=[0-9]{1,4}(?:[.,][0-9]+)?\s*(?:GB|TB)(?![0-9a-z])))',
                   re.IGNORECASE)
# A word right after a known model that makes it another phone: 'Pro-Max', 'Plus', 'PM', 'Mini', a
# stray 'e' or a '(Pro)'. A fixed list, so other words after a model ('256GB', 'Black', even
# injected text) leave it alone, and 'e' before 'SIM' is the eSIM. A spaced '+' not followed by
# a named item ('iPhone 16 +') cannot be read either.
_QUALIFIER = re.compile(r'\s*\+(?!\s*[0-9a-z])|[\s\-\u2010-\u2015_(\[]*'
                        r'(?:plus|pro|max|promax|pm|mini|ultra|air|se|xl|fe|e(?![\s\-\u2010-\u2015]*sim))'
                        r'(?![0-9a-z])', re.IGNORECASE)
_MANUFACTURERS = [(m, _pattern(m)) for m in KNOWN_MANUFACTURERS]
# A size followed by RAM is memory, not storage ('12GB RAM'). Digits after a decimal point or a
# digit group separator are not a size ('0.25TB', '1,024GB'); a comma after a word still ends it
# ('Black,256GB').
_STORAGE = re.compile(r'(?<![0-9a-z])(?<![0-9][.,])([0-9]{1,4})\s*(GB|TB)(?![0-9a-z])(?!\s*RAM)',
                      re.IGNORECASE)


def spell_plus(text: str) -> str:
    """The text with a '+' attached to a name spelled 'Plus', so 'iPhone 16+' is 'iPhone 16 Plus'.

    Model names are compared word by word, and a bare '+' is punctuation that would drop out.
    """
    return _PLUS.sub(lambda m: ' Plus ' if text[m.end():m.end() + 1].isdigit() else ' Plus', text)


def find_model(text: str) -> tuple[str, str] | None:
    """(spelling found, our canonical name) for the longest known model spelling in the text, or None.

    None too when a model qualifier follows the match ('iPhone 17 Pro-Max', 'iPhone 17 PM'): the
    text then names a phone our table does not hold, not the shorter model it contains.
    """
    text = spell_plus(text)
    for spelling, name, pattern in _MODELS:
        found = pattern.search(text)
        if found:
            return None if _QUALIFIER.match(text, found.end()) else (spelling, name)
    return None


def match_model(text: str) -> str | None:
    """Our canonical model name for the longest known model in the text, or None (see find_model)."""
    found = find_model(text)
    return found[1] if found else None


def find_manufacturers(text: str) -> set[str]:
    """Canonical manufacturers named in the text; several means the text is ambiguous."""
    return {name for name, pattern in _MANUFACTURERS if pattern.search(text)}


def canonical_manufacturer(text: str) -> str | None:
    found = find_manufacturers(text)
    return found.pop() if len(found) == 1 else None


def storage_tokens(text: str) -> list[int]:
    """Storage sizes in GB written in the text ('256GB', '1 TB' is 1024), in order; never 0."""
    sizes = (int(n) * (1024 if unit.upper() == 'TB' else 1) for n, unit in _STORAGE.findall(text))
    return [size for size in sizes if size]
