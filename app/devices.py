"""Device vocabulary shared by the store readers (MVP 05) and identity (MVP 07).

Only our own tables live here, so names matched against them are safe to use in reasons.
"""
import re

KNOWN_MANUFACTURERS = ('Apple', 'Samsung', 'Google', 'Xiaomi', 'Huawei', 'Honor', 'OnePlus', 'Oppo',
                       'Nokia', 'Motorola')
# Each model is matched as whole words, longest first, so 'iPhone 17 Pro' never reads as 'iPhone 17'.
KNOWN_MODELS = ('iPhone 17 Pro Max', 'iPhone 17 Pro', 'iPhone 17e', 'iPhone 17 Air', 'iPhone Air', 'iPhone 17',
                'iPhone 16 Pro Max', 'iPhone 16 Pro', 'iPhone 16 Plus', 'iPhone 16e', 'iPhone 16')


def _pattern(name: str) -> re.Pattern:
    words = (re.escape(w) for w in name.split())
    return re.compile(r'(?<![0-9a-z])' + r'\s*'.join(words) + r'(?![0-9a-z])', re.IGNORECASE)


_MODELS = sorted(((m, _pattern(m)) for m in KNOWN_MODELS), key=lambda pair: -len(pair[0]))
# A '+' written right after a model ('iPhone 16+', Samsung's 'Galaxy S25+') names the Plus model;
# a spaced '+' ('iPhone 17 + AirPods') joins a bundle and is left alone.
_PLUS = re.compile(r'(?<=[0-9a-z])\+(?![0-9a-z])', re.IGNORECASE)
_MANUFACTURERS = [(m, _pattern(m)) for m in KNOWN_MANUFACTURERS]
# A size followed by RAM is memory, not storage ('12GB RAM').
_STORAGE = re.compile(r'(?<![0-9a-z])([0-9]{1,4})\s*(GB|TB)(?![0-9a-z])(?!\s*RAM)', re.IGNORECASE)


def spell_plus(text: str) -> str:
    """The text with a '+' attached to a name spelled 'Plus', so 'iPhone 16+' is 'iPhone 16 Plus'.

    Model names are compared word by word, and a bare '+' is punctuation that would drop out.
    """
    return _PLUS.sub(' Plus', text)


def match_model(text: str) -> str | None:
    """Our canonical model name for the longest known model in the text, or None."""
    text = spell_plus(text)
    for name, pattern in _MODELS:
        if pattern.search(text):
            return name
    return None


def find_manufacturers(text: str) -> set[str]:
    """Canonical manufacturers named in the text; several means the text is ambiguous."""
    return {name for name, pattern in _MANUFACTURERS if pattern.search(text)}


def canonical_manufacturer(text: str) -> str | None:
    found = find_manufacturers(text)
    return found.pop() if len(found) == 1 else None


def storage_tokens(text: str) -> list[int]:
    """Storage sizes in GB written in the text ('256GB', '1 TB'), in order."""
    return [int(n) * (1024 if unit.upper() == 'TB' else 1) for n, unit in _STORAGE.findall(text)]
