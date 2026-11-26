"""Direct public listing reads for the two-agent experiment."""

from datetime import datetime, timezone
import os

from app.firecrawl import Firecrawl, ManagedPage
from app.onboarding import ActivationRegistry, PROBE_TTL, ProbeRegistry, canonical, source_id_for
from app.sources import SourceGate


class _ListingScope(ProbeRegistry):
    """One public document. Carry existing shop limits and stops into each gate check."""

    def __init__(self, registry, shop_key, host, url):
        self.registry = registry
        self.shop_key = shop_key
        entry = self._entry()
        super().__init__(source_id=entry['id'] if entry else source_id_for(shop_key), name=host,
                         origin=f'https://{host}', targets=(url,),
                         expires_at=datetime.now(timezone.utc) + PROBE_TTL)

    def _entry(self):
        document = self.registry.read_registry()
        owner = self.registry.reviewed(self.shop_key, self.registry.reviewed_hosts(document))
        if owner is None:
            return None
        if owner.status != 'pending' and (owner.status != 'active' or not owner.admitted):
            raise ValueError('Retailer inspection is disabled')
        return next(entry for entry in document['sources'] if entry['id'] == owner.source_id)

    def read_registry(self):
        document = super().read_registry()
        entry = self._entry()
        current_id = entry['id'] if entry else source_id_for(self.shop_key)
        if current_id != self.source_id:
            raise ValueError('Retailer identity changed during inspection')
        if entry:
            limits = document['sources'][0]['limits']
            for key in limits:
                value = entry['limits'][key]
                if entry['status'] == 'active' or value > 0:
                    limits[key] = min(limits[key], value)
        return document


def inspect_public_listing(job, url: str, registry: ActivationRegistry, *, environ=None) -> ManagedPage:
    """Capture one exact public URL for a buyer or catalog job. Seller identity is assessed later."""
    target = canonical(url)
    if target is None:
        raise ValueError('Expected a public HTTPS listing URL')
    shop_key, host = target
    scope = _ListingScope(registry, shop_key, host, url)
    try:
        provider = Firecrawl.from_env(SourceGate(scope), os.environ if environ is None else environ)
        if provider is None:
            raise ValueError('Configure Firecrawl and its credit allowance for listing inspection')
        return provider.scrape(job, scope.source_id, url)
    finally:
        scope.close()
