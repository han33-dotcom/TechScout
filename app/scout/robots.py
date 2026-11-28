"""robots.txt rules for the scout's public retailer reads (RFC 9309).

Python's urllib.robotparser ignores the ``*`` and ``$`` patterns most Lebanese
storefronts use (Shopify's defaults, for example ``Disallow: /*search*``), so this
module matches them itself. The longest matching rule wins and Allow wins a tie.
A missing file (4xx) allows everything; an unreachable one (5xx, network error)
allows nothing, as the RFC requires.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from urllib.parse import unquote, urlsplit

PRODUCT_TOKEN = 'techscout'
MAX_ROBOTS_BYTES = 500_000


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str

    def length(self) -> int:
        return len(self.pattern)

    def matches(self, path: str) -> bool:
        return _compile(self.pattern).match(path) is not None


_CACHE: dict[str, re.Pattern] = {}


def _compile(pattern: str) -> re.Pattern:
    compiled = _CACHE.get(pattern)
    if compiled is None:
        anchored = pattern.endswith('$')
        body = pattern[:-1] if anchored else pattern
        regex = ''.join('.*' if ch == '*' else re.escape(ch) for ch in body)
        compiled = re.compile(regex + ('$' if anchored else ''), re.DOTALL)
        if len(_CACHE) < 4096:
            _CACHE[pattern] = compiled
    return compiled


@dataclass(frozen=True)
class Robots:
    """Rules for one host. ``disallow_all`` models an unreachable robots.txt."""
    rules: tuple[Rule, ...] = ()
    disallow_all: bool = False
    crawl_delay: float | None = None
    sitemaps: tuple[str, ...] = field(default=())

    def allows(self, url: str) -> bool:
        if self.disallow_all:
            return False
        parts = urlsplit(url)
        path = unquote(parts.path or '/') + (f'?{unquote(parts.query)}' if parts.query else '')
        best: Rule | None = None
        for rule in self.rules:
            if not rule.pattern or not rule.matches(path):
                continue
            if best is None or rule.length() > best.length() or (rule.length() == best.length() and rule.allow):
                best = rule
        return True if best is None else best.allow


ALLOW_ALL = Robots()
DISALLOW_ALL = Robots(disallow_all=True)


def parse(text: str, agent: str = PRODUCT_TOKEN) -> Robots:
    """Rules of the group naming ``agent``, else the ``*`` group (RFC 9309 section 2.2.1)."""
    groups: list[tuple[list[str], list[Rule], list[float]]] = []
    sitemaps: list[str] = []
    current: tuple[list[str], list[Rule], list[float]] | None = None
    last_was_agent = False
    for raw in text[:MAX_ROBOTS_BYTES].splitlines():
        line = raw.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        key, value = key.strip().lower(), value.strip()
        if key == 'user-agent':
            if current is None or not last_was_agent:
                current = ([], [], [])
                groups.append(current)
            current[0].append(value.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if key == 'sitemap' and value:
            sitemaps.append(value)
            continue
        if current is None:
            continue
        if key in ('allow', 'disallow'):
            if key == 'disallow' and not value:
                continue  # an empty Disallow allows everything
            pattern = value if value.startswith(('/', '*')) else '/' + value
            current[1].append(Rule(key == 'allow', pattern))
        elif key == 'crawl-delay':
            try:
                current[2].append(max(0.0, min(float(value), 30.0)))
            except ValueError:
                pass
    agent = agent.lower()
    chosen = [g for g in groups if any(name and name != '*' and name in agent for name in g[0])]
    if not chosen:
        chosen = [g for g in groups if '*' in g[0]]
    rules = tuple(rule for g in chosen for rule in g[1])
    delays = [d for g in chosen for d in g[2]]
    return Robots(rules=rules, crawl_delay=max(delays) if delays else None, sitemaps=tuple(sitemaps))


def from_response(status: int | None, text: str | None) -> Robots:
    """RFC 9309 section 2.3.1: 2xx parses, 4xx allows all, anything else disallows all."""
    if status is not None and 200 <= status < 300:
        return parse(text or '')
    if status is not None and 400 <= status < 500:
        return ALLOW_ALL
    return DISALLOW_ALL
