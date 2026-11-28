"""Turn a retailer HTML page into what the agent can read: text, links and structured data.

Standard library only. Script and style bodies are dropped from the text, except
``application/ld+json`` blocks, which are parsed separately as schema.org data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit

BLOCK_TAGS = {'p', 'div', 'li', 'tr', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article',
              'table', 'ul', 'ol', 'dd', 'dt', 'option', 'label', 'header', 'footer', 'td', 'th'}
SKIP_TAGS = {'script', 'style', 'noscript', 'svg', 'template', 'iframe'}


@dataclass
class ParsedPage:
    title: str = ''
    text: str = ''
    links: list[tuple[str, str]] = field(default_factory=list)  # (absolute url, anchor text)
    jsonld: list[dict] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)


class _Parser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.out = base, ParsedPage()
        self._skip = 0
        self._ld: list[str] | None = None
        self._in_title = False
        self._chunks: list[str] = []
        self._link: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'script' and a.get('type', '').lower() == 'application/ld+json':
            self._ld = []
            return
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag == 'title':
            self._in_title = True
        if tag == 'meta':
            key = (a.get('property') or a.get('name') or a.get('itemprop') or '').lower()
            if key and a.get('content') and len(self.out.meta) < 80:
                self.out.meta.setdefault(key, a['content'][:400])
        if tag == 'a' and a.get('href'):
            href = urljoin(self.base, a['href'])
            if href.startswith('https://'):
                self._link = (href.split('#')[0], [])
        if tag in BLOCK_TAGS:
            self._chunks.append('\n')

    def handle_endtag(self, tag):
        if tag == 'script' and self._ld is not None:
            raw = ''.join(self._ld)
            self._ld = None
            try:
                data = json.loads(raw)
            except ValueError:
                return
            for item in (data if isinstance(data, list) else [data]):
                if isinstance(item, dict):
                    graph = item.get('@graph')
                    items = graph if isinstance(graph, list) else [item]
                    self.out.jsonld.extend(i for i in items if isinstance(i, dict))
            return
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == 'title':
            self._in_title = False
        if tag == 'a' and self._link is not None:
            href, words = self._link
            label = ' '.join(''.join(words).split())[:120]
            if len(self.out.links) < 600:
                self.out.links.append((href, label))
            self._link = None
        if tag in BLOCK_TAGS:
            self._chunks.append('\n')

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self.out.title += data
        self._chunks.append(data)
        if self._link is not None:
            self._link[1].append(data)


def parse_html(html: str, base_url: str) -> ParsedPage:
    parser = _Parser(base_url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup keeps whatever parsed
        pass
    out = parser.out
    raw = ''.join(parser._chunks)
    lines = [' '.join(line.split()) for line in raw.split('\n')]
    out.text = '\n'.join(line for line in lines if line)
    out.title = ' '.join(out.title.split())[:200]
    return out


def jsonld_products(items: list[dict]) -> list[dict]:
    """schema.org Product / ProductGroup objects, flattened one level."""
    found = []
    for item in items:
        kind = item.get('@type')
        kinds = kind if isinstance(kind, list) else [kind]
        if any(k in ('Product', 'ProductGroup', 'IndividualProduct') for k in kinds if isinstance(k, str)):
            found.append(item)
            for v in item.get('hasVariant') or []:
                if isinstance(v, dict):
                    found.append(v)
    return found


def offers_of(product: dict) -> list[dict]:
    offers = product.get('offers')
    if isinstance(offers, dict):
        inner = offers.get('offers')
        return [o for o in inner if isinstance(o, dict)] if isinstance(inner, list) else [offers]
    if isinstance(offers, list):
        return [o for o in offers if isinstance(o, dict)]
    return []


def schema_value(value) -> str:
    """'https://schema.org/InStock' -> 'InStock'."""
    if isinstance(value, dict):
        value = value.get('@id') or value.get('name') or ''
    return str(value or '').rstrip('/').rsplit('/', 1)[-1]


def snippets(text: str, terms: list[str], *, width: int = 220, limit: int = 12) -> list[dict]:
    """Passages around each search term, with character offsets into ``text``."""
    hits, seen = [], set()
    lowered = text.lower()
    for term in terms:
        term = term.strip().lower()
        if not term or len(term) > 80:
            continue
        start = 0
        while len(hits) < limit:
            i = lowered.find(term, start)
            if i < 0:
                break
            a, b = max(0, i - width // 2), min(len(text), i + len(term) + width // 2)
            key = a // max(1, width // 2)
            if key not in seen:
                seen.add(key)
                hits.append(dict(offset=a, term=term, text=text[a:b].replace('\n', ' | ')))
            start = i + len(term)
    return hits


def same_site_links(page: ParsedPage, url: str, *, pattern: str | None = None, limit: int = 40) -> list[dict]:
    host = urlsplit(url).hostname
    rx = re.compile(pattern, re.IGNORECASE) if pattern else None
    out, seen = [], set()
    for href, label in page.links:
        if urlsplit(href).hostname != host or href in seen:
            continue
        if rx and not (rx.search(href) or rx.search(label)):
            continue
        seen.add(href)
        out.append(dict(url=href, text=label))
        if len(out) >= limit:
            break
    return out
