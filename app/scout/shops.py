"""The scout's directory of Lebanese shops and how to read each one.

``config/scout-shops.json`` lists shops with their origin, platform and provenance (where
we first learned of the shop). Provenance matters for honest evaluation: a shop that is
known only from the frozen evaluation references must not be added, or the held-out
score would be contaminated. ``detect`` fills in the platform from public endpoints.

Run ``python -m app.scout.shops detect https://shop.example ...`` to check new shops.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import sys

from app.scout.web import FetchError, SafeWeb

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / 'config' / 'scout-shops.json'
PLATFORMS = ('shopify', 'woocommerce', 'merchant_feed', 'html', 'blocked', 'unknown')


@dataclass
class Shop:
    shop_id: str
    name: str
    origin: str                       # https://host, no trailing slash
    platform: str = 'unknown'
    categories: list[str] = field(default_factory=list)
    provenance: str = ''
    feed_url: str | None = None       # merchant feed, when the shop publishes one
    search_url: str | None = None     # HTML search template with {q}, when robots allow it
    country: str | None = None        # as published by the shop itself (Shopify meta.json)
    city: str | None = None
    currency: str | None = None
    notes: str = ''
    enabled: bool = True

    @property
    def host(self) -> str:
        return self.origin.split('://', 1)[-1]


def load(path: Path | str = DEFAULT_PATH) -> list[Shop]:
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    shops = [Shop(**{k: v for k, v in row.items() if k in Shop.__dataclass_fields__}) for row in raw['shops']]
    seen = set()
    for shop in shops:
        if not shop.origin.startswith('https://') or shop.origin.endswith('/'):
            raise ValueError(f'{shop.shop_id}: origin must be https://host without a trailing slash')
        if shop.platform not in PLATFORMS:
            raise ValueError(f'{shop.shop_id}: unknown platform {shop.platform}')
        if shop.shop_id in seen:
            raise ValueError(f'duplicate shop id {shop.shop_id}')
        seen.add(shop.shop_id)
    return shops


def detect(web: SafeWeb, origin: str) -> dict:
    """Which public structured endpoint the shop answers. One request per probe."""
    origin = origin.rstrip('/')
    found = dict(origin=origin, platform='unknown', robots='allow')
    try:
        robots = web.robots_for(origin + '/')
        if robots.disallow_all:
            found.update(platform='blocked', robots='unreachable')
            return found
    except FetchError as exc:
        found.update(platform='blocked', robots=exc.code)
        return found
    try:
        page = web.get(origin + '/meta.json', accept='json')
        data = json.loads(page.body)
        if isinstance(data, dict) and data.get('myshopify_domain'):
            found.update(platform='shopify', country=data.get('country'), city=data.get('city'),
                         currency=data.get('currency'), name=data.get('name'),
                         search_allowed=robots.allows(origin + '/search/suggest.json?q=x'),
                         catalog_allowed=robots.allows(origin + '/products.json?limit=250&page=1'))
            return found
    except (FetchError, ValueError):
        pass
    try:
        page = web.get(origin + '/wp-json/wc/store/v1/products?per_page=1', accept='json')
        data = json.loads(page.body)
        if isinstance(data, list):
            found.update(platform='woocommerce',
                         currency=(data[0].get('prices') or {}).get('currency_code') if data else None,
                         search_allowed=robots.allows(origin + '/wp-json/wc/store/v1/products?search=x'))
            return found
    except (FetchError, ValueError):
        pass
    try:
        page = web.get(origin + '/', accept='html')
        text = page.text().lower()
        found.update(platform='html', hint='shopify' if 'cdn.shopify.com' in text else
                     'magento' if 'mage/' in text or 'magento' in text else
                     'woocommerce' if 'woocommerce' in text else 'custom')
    except FetchError as exc:
        found.update(platform='blocked', error=exc.code)
    return found


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == 'detect':
        web = SafeWeb(min_interval_s=0.5)
        for origin in argv[1:]:
            print(json.dumps(detect(web, origin), ensure_ascii=False))
        return 0
    if argv and argv[0] == 'list':
        for shop in load():
            print(json.dumps(asdict(shop), ensure_ascii=False))
        return 0
    print('usage: python -m app.scout.shops detect https://shop.example ... | list')
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
