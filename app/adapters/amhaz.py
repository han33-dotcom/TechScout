"""Amhaz source profile (#56): blocked until the intended business is identified.

The retailer audit (#50, docs/retailer-audit-2026-11-16.md) found one candidate storefront,
amhazofficial.com, which names itself "Amhaz Official" in Saint Therese, Hadath. The audit left
its identity unresolved: nothing shows that it is the Amhaz the user named or the same business
as similarly named shops. Retrieval may use only the identity T14 established, so this profile
declares no retrieval capability, supports no category, sends no request and yields no
candidate. docs/source-coverage.md records the evidence and what unblocks it.

The profile is deliberately left out of app.adapters.ADAPTERS and CATALOGS and out of
config/sources.json, so no run can dispatch to the candidate site.
"""
from dataclasses import dataclass

from app.adapters.base import tool_result
from app.adapters.woo_catalog import CatalogResult
from app.contracts import ToolError

RETAILER_ID = 'amhaz_official_candidate'
CANDIDATE_ORIGIN = 'https://amhazofficial.com/'
# The audit sidecar's identity_status for the candidate; tests keep the two in step.
IDENTITY_STATUS = 'unresolved_user_intent'
BLOCKER = 'identity_unresolved'
# The full-product categories (app.contracts_v2.Category).
CATEGORIES = frozenset({'phone', 'laptop', 'tablet', 'monitor', 'audio'})


@dataclass(frozen=True)
class BlockedProfile:
    """A source that must not be read: every search fails closed before any request."""
    retailer_id: str
    candidate_origin: str
    identity_status: str
    blocker: str
    # Categories the audit saw in the candidate's navigation. Leads only: nothing is retrieved.
    category_leads: tuple[str, ...]
    canonical_origin: str | None = None
    capabilities: tuple = ()
    supported_categories: tuple[str, ...] = ()
    adapter_version: str = 'amhaz-profile/1'

    def search(self, fetch, *, category, search, run=None, only=None,
               corpus_version='catalog-preview-v1') -> CatalogResult:
        """Same call shape as WooCatalog.search; never calls fetch and never returns a candidate."""
        if (not isinstance(category, str) or category not in CATEGORIES or not isinstance(search, str)
                or not search.strip() or len(search) > 64):
            raise ValueError('A supported category and bounded search string are required')
        error = ToolError(code='SOURCE_DISABLED', retryable=False,
                          message=f'{self.retailer_id}: business identity is unresolved; no retrieval is configured')
        data = {'candidates_seen': 0, 'priced_candidates': 0, 'coverage_complete': False,
                'requires_current_run_verification': True, 'blocked': self.blocker,
                'identity_status': self.identity_status}
        return CatalogResult((), tool_result((), [error], data, (), False), ())


PROFILE = BlockedProfile(RETAILER_ID, CANDIDATE_ORIGIN, IDENTITY_STATUS, BLOCKER,
                         category_leads=('phone', 'tablet', 'audio'))
