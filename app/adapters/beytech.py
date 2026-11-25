"""Beytech reader (MVP 05): the iPhone 17 '1 Sim - 2 Years Warranty' product and two policy pages.

The product name carries the warranty statement and '1 Sim', whose SIM type is not confirmed
(docs/source-coverage.md), and a non-variation Brand attribute names Apple (the page's JSON-LD
brand reads 'Electronics' and is not used). The warranty page covers repairs only, with the
original invoice; the exchange page allows one exchange within 5 days but lists mobiles as
non-returnable. Neither page settles an offer field.
"""
from dataclasses import dataclass
from hashlib import sha256

from app.adapters.base import Snapshot, tool_result
from app.adapters.woo_catalog import WooCatalog
from app.adapters.woocommerce import visible_text
from app.contracts import ToolResult
from app.contracts_v2 import Evidence
from app.corpus_contracts import Extraction, FieldObservation

import re
from types import MappingProxyType

from app.adapters.base import PolicyFact
from app.adapters.woocommerce import (
    Claim, PageFacts, PolicyPage, ProductEndpoints, StoreConfig, WooCommerceAdapter, WarrantyStatement,
)

SOURCE_ID = 'beytech'
ORIGIN = 'https://beytech.com.lb'

_PAYMENT_ICONS = re.compile(r'Cash\s+On\s+Delivery\s+(?:Visa|MasterCard|American\s+Express)\b', re.IGNORECASE)
_NAME_WARRANTY = re.compile(r'(?<![0-9a-z])([0-9]{1,2})\s*Years?\s+Warranty\b', re.IGNORECASE)
_COVERS = re.compile(r'warranty\s+covers\s+repairs\s+only\b[^.]{0,80}\.?', re.IGNORECASE)
_INVOICE = re.compile(r'Warranty\s+service\s+will\s+only\s+be\s+provided\s+upon\s+presentation\s+of\s+the\s+'
                      r'original\s+sales\s+invoice\b[^.]{0,80}\.?', re.IGNORECASE)
_EXCHANGE = re.compile(r'exchanged\s+once\s+within\s+([0-9]{1,2})\s+days\b[^.]{0,80}\.?', re.IGNORECASE)
_NO_MOBILES = re.compile(r'Non-Returnable\s+Items\s*:[^:]{0,120}?\bMobiles\b', re.IGNORECASE)


def page_facts(text: str) -> PageFacts:
    """The footer's payment icons: cash on delivery and cards."""
    icons = _PAYMENT_ICONS.search(text)
    return PageFacts(payment_methods=None if icons is None else Claim(('cash', 'card'), icons.group(0),
                                                                      scope='source'))


def name_warranty(name: str) -> WarrantyStatement | None:
    """'2 Years Warranty' in the product name: 24 months, provider not named."""
    match = _NAME_WARRANTY.search(name)
    return None if match is None else (None, int(match.group(1)) * 12)


def warranty_facts(text: str) -> tuple[PolicyFact, ...]:
    """What the warranty covers and that it needs the invoice; no length, provider or territory."""
    covers, invoice = _COVERS.search(text), _INVOICE.search(text)
    value = {**({'covers': 'repairs only'} if covers else {}),
             **({'invoice_required': True} if invoice else {})}
    if not value:
        return ()
    return (PolicyFact('warranty', value, ' '.join(m.group(0) for m in (covers, invoice) if m)),)


def exchange_facts(text: str) -> tuple[PolicyFact, ...]:
    """The exchange window, and whether mobiles are excluded from it."""
    exchange = _EXCHANGE.search(text)
    if exchange is None:
        return ()
    excluded = _NO_MOBILES.search(text)
    value = {'days': int(exchange.group(1)), **({'excludes_mobiles': True} if excluded else {})}
    return (PolicyFact('returns', value, ' '.join(m.group(0) for m in (exchange, excluded) if m)),)


CONFIG = StoreConfig(
    source_id=SOURCE_ID,
    adapter_version='beytech/1',
    products=(ProductEndpoints(
        key='139968', label='iPhone 17',
        api_url=f'{ORIGIN}/wp-json/wc/store/v1/products?slug=apple-iphone-17-1-sim-2-years-warranty',
        page_url=f'{ORIGIN}/product/apple-iphone-17-1-sim-2-years-warranty/'),),
    policies=(
        # The page states no length, provider or territory, and no policy effect reads it.
        PolicyPage('warranty', f'{ORIGIN}/warranty-terms-conditions/', (), warranty_facts),
        PolicyPage('exchange', f'{ORIGIN}/exchange-policy-2/', (), exchange_facts),
    ),
    # '1 Sim' speaks to the SIM but the store never writes eSIM: reading it as one physical slot
    # plus eSIM is an assumption, so the SIM type stays unknown until the store confirms it.
    name_sim=MappingProxyType({'1 sim': None}),
    page_facts=page_facts,
    name_warranty=name_warranty,
)
ADAPTER = WooCommerceAdapter(CONFIG)


@dataclass(frozen=True)
class CatalogPolicies:
    extraction: Extraction
    tool_result: ToolResult
    snapshots: tuple[Snapshot, ...]
    missing_topics: tuple[str, ...]


class BeytechCatalog(WooCatalog):
    def fetch_policies(self, fetch, *, run=None, corpus_version="catalog-preview-v1"):
        """Preserve published policy text; it never silently becomes a resolved offer cost.

        Existing reviewed warranty/exchange endpoints only. Delivery, VAT and
        condition statements are retained if explicitly published on these pages;
        otherwise the caller receives named gaps. In particular a returns rule
        requiring sealed packaging does not establish a new-device store policy.
        """
        outcomes, snapshots, fields = [], [], {}
        patterns = {
            'delivery': r'Delivery\s+all\s+over\s+Lebanon\b',
            'tax': r'All\s+(?:our\s+)?prices\s+(?:include|exclude)\s+VAT\b',
            'condition': r'All\s+(?:our\s+)?(?:products|devices)\s+are\s+(?:brand\s+)?new\b',
        }
        for policy in CONFIG.policies:
            outcome = fetch(self.source_id,policy.url,expect='html')
            outcomes.append(outcome)
            if outcome.errors or outcome.body is None:
                continue
            snapshots.append(Snapshot.of(outcome))
            text = visible_text(outcome.body)
            statements = [(fact.topic,fact.excerpt) for fact in policy.extract(text)]
            for topic, pattern in patterns.items():
                statements.extend((topic,m.group(0)) for m in re.finditer(pattern,text,re.I))
            for topic, statement in statements:
                field = 'policy.'+topic
                receipt = Evidence(evidence_id=sha256((outcome.response_sha256+outcome.observed_at.isoformat()+
                    field+statement+str(run)+corpus_version).encode()).hexdigest(),source_id=self.source_id,
                    scope='source',field_path=field,origin_url=outcome.origin_url,final_url=outcome.final_url,
                    observed_at=outcome.observed_at,response_sha256=outcome.response_sha256,
                    adapter_version='beytech-policy/2',excerpt=statement,origin='runtime',
                    run=run,corpus_version=corpus_version)
                fields.setdefault(field,[]).append((statement,receipt))
        observations=[]
        for field,entries in fields.items():
            # Different policy sentences are retained together, not mislabelled as contradictions.
            statements=list(dict.fromkeys(s for s,_ in entries))
            receipts=tuple({e.evidence_id:e for _,e in entries}.values())
            observations.append(FieldObservation(fact={'state':'known','value':' | '.join(statements),
                'evidence_ids':[e.evidence_id for e in receipts]},evidence=receipts))
        missing=tuple(t for t in ('delivery','tax','condition','warranty') if 'policy.'+t not in fields)
        extraction=Extraction(observations=tuple(observations))
        trace=tool_result(outcomes,[],{'missing_topics':list(missing),'resolved_offer_costs':False},
                          [e.evidence_id for o in observations for e in o.evidence],bool(observations))
        return CatalogPolicies(extraction,trace,tuple(snapshots),missing)


CATALOG = BeytechCatalog(SOURCE_ID, ORIGIN, {
    'phone': frozenset({'mobiles','smartphones','mobile-phones'}),
    'laptop': frozenset({'laptops'}),
    'tablet': frozenset({'tablets', 'ipads'}),
    'monitor': frozenset({'monitors'}),
}, (('product', ('product',), 2),))
