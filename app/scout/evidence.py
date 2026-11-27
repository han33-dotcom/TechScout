"""Everything the agent observed in this run, keyed by short IDs it can cite.

Product reads get ``P1, P2, ...``; page and shop-terms reads get ``T1, T2, ...``.
Each observation keeps the URL actually read, the UTC time, the SHA-256 of the response
and the text the agent was shown. The verifier only accepts claims that point at one of
these observations, so nothing from an earlier run or from the model's memory can pass
as current evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import re
import threading

from app.scout.storefronts import Product


@dataclass
class Observation:
    obs_id: str
    kind: str                 # 'product' | 'page'
    url: str
    shop_id: str | None
    observed_at: datetime
    sha256: str
    text: str                 # what the agent saw, used for quote checks
    product: Product | None = None
    meta: dict = field(default_factory=dict)


class Evidence:
    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, Observation] = {}
        self._counters = {'P': 0, 'T': 0}

    def add(self, prefix: str, **kwargs) -> Observation:
        with self._lock:
            self._counters[prefix] += 1
            obs = Observation(obs_id=f'{prefix}{self._counters[prefix]}', **kwargs)
            self._items[obs.obs_id] = obs
            return obs

    def get(self, obs_id: str | None) -> Observation | None:
        if not obs_id:
            return None
        return self._items.get(str(obs_id).strip().upper())

    def all(self) -> list[Observation]:
        return list(self._items.values())

    def products(self) -> list[Observation]:
        return [o for o in self._items.values() if o.kind == 'product']


def product_text(product: Product) -> str:
    """The searchable text of a product observation (what quotes are checked against)."""
    compact = product.compact()
    return json.dumps(compact, ensure_ascii=False)


_WS = re.compile(r'\s+')
_QUOTES = str.maketrans({'’': "'", '‘': "'", '“': '"', '”': '"', '–': '-', '—': '-',
                         ' ': ' '})


def normalize(text: str) -> str:
    text = (text or '').translate(_QUOTES).lower()
    text = text.replace('\\"', '"').replace('\\/', '/')
    return _WS.sub(' ', text).strip()


def contains_quote(haystack: str, quote: str | None, *, min_len: int = 3) -> bool:
    """True when the quote (normalized) occurs in the observation text."""
    q = normalize(quote or '')
    if len(q) < min_len:
        return False
    h = normalize(haystack)
    if q in h:
        return True
    # Tolerate the model dropping JSON punctuation around option values ("Color": "Black").
    stripped = re.sub(r'["{}\[\]]', '', q)
    return len(stripped) >= min_len and stripped in re.sub(r'["{}\[\]]', '', h)
