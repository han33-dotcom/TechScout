"""Store readers for the admitted sources (MVP 05).

Each reader turns gate replies into version 1 offers and policy statements with evidence; the
shared interfaces live in app.adapters.base and the WooCommerce reader in app.adapters.woocommerce.
"""
from app.adapters import beytech, class_cellulars, mojitech
from app.adapters.base import AdapterResult, CandidateQuery, Fetch, StoreAdapter

ADAPTERS: dict[str, StoreAdapter] = {
    adapter.source_id: adapter for adapter in (class_cellulars.ADAPTER, mojitech.ADAPTER, beytech.ADAPTER)
}


def get_adapter(source_id: str) -> StoreAdapter:
    """The reader for an admitted source; KeyError for any other source."""
    return ADAPTERS[source_id]


def search_candidates(fetch: Fetch, source_id: str, query: CandidateQuery) -> AdapterResult:
    return get_adapter(source_id).search_candidates(fetch, query)


def fetch_offer(fetch: Fetch, source_id: str, offer_id: str, query: CandidateQuery) -> AdapterResult:
    return get_adapter(source_id).fetch_offer(fetch, offer_id, query)


def fetch_policy(fetch: Fetch, source_id: str, policy_id: str) -> AdapterResult:
    return get_adapter(source_id).fetch_policy(fetch, policy_id)
