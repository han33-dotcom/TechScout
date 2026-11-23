"""Persistent seller discovery and bounded source selection; never purchase ranking."""
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from sqlalchemy import delete, insert, select, update

from app.collection_contracts import RetailerDirectoryEntry
from app.contracts import HttpsUrl, Model, Text, UtcTime
from app.contracts_v2 import Category, Country
from app.source_capabilities import CapabilityRef, EndpointCapability
from app.source_registry import DEFAULT_REGISTRY, get_active_source
from app.storage import (
    Storage, corpus_guard, directory_entries, directory_categories, directory_markets,
    directory_aliases,
)

ROOT = Path(__file__).resolve().parents[1]


class PolicyLink(Model):
    url: HttpsUrl
    checked_on: date
    summary: Text
    evidence_ids: tuple[Text, ...] = ()


class CategoryCoverage(Model):
    category: Category
    discovered: bool = False
    retrieval_tested: bool = False
    qualifying_offer: bool = False
    evidence_ids: tuple[Text, ...] = ()
    checked_at: UtcTime | None = None
    missing_fields: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def supported_levels(self):
        if self.qualifying_offer and (not self.retrieval_tested or not self.evidence_ids):
            raise ValueError('Qualifying coverage requires tested evidence; never infer it from discovery')
        if self.retrieval_tested and (not self.discovered or not self.evidence_ids or self.checked_at is None):
            raise ValueError('Tested category coverage requires dated evidence references')
        return self


class RetrievalObservation(Model):
    observation_id: Text
    url: HttpsUrl
    checked_at: UtcTime
    status: Annotated[int, Field(strict=True,ge=100,le=599)] | None = None
    error: Text | None = None
    sha256: Text | None = None


class DirectoryRecord(Model):
    directory_schema_version: Literal[1] = 1
    entry: RetailerDirectoryEntry
    updated_at: UtcTime
    source_id: Text | None = None
    source_country: Country | None = None
    seller_id: Text | None = None
    marketplace_id: Text | None = None
    seller_identity: Literal['retailer', 'marketplace', 'unresolved', 'forwarder'] = 'unresolved'
    catalog_urls: tuple[HttpsUrl, ...] = ()
    product_urls: tuple[HttpsUrl, ...] = ()
    policy_urls: tuple[HttpsUrl, ...] = ()
    capabilities: tuple[CapabilityRef, ...] = ()
    policy_links: tuple[PolicyLink, ...] = ()
    coverage: tuple[CategoryCoverage, ...] = ()
    observations: tuple[RetrievalObservation, ...] = ()
    useful_evidence_ids: tuple[Text, ...] = ()
    background_collection: bool = False
    role: Literal['domestic', 'direct_international', 'forwarding_market',
                  'forwarder_primary', 'forwarder_backup', 'candidate'] = 'candidate'
    price_bands: tuple[Literal['budget','midrange','premium'], ...] = ()
    configuration_gaps: tuple[Text, ...] = ()

    @model_validator(mode='after')
    def distinct_coverage(self):
        categories = [item.category for item in self.coverage]
        if len(categories) != len(set(categories)) or not set(categories) <= set(self.entry.categories):
            raise ValueError('Coverage must uniquely describe declared categories')
        if self.seller_identity == 'retailer' and self.seller_id is None:
            raise ValueError('A resolved seller requires its canonical identity')
        if any(item.checked_at > self.updated_at for item in self.observations):
            raise ValueError('Retrieval observations cannot follow the record update')
        if any(item.checked_at and item.checked_at > self.updated_at for item in self.coverage):
            raise ValueError('Category checks cannot follow the record update')
        if self.entry.last_checked_at and self.entry.last_checked_at > self.updated_at:
            raise ValueError('Directory update cannot precede its audit')
        return self


class DirectoryQuery(Model):
    category: Category
    purchase_markets: Annotated[tuple[Country,...],Field(min_length=1,max_length=10)] = ('LB',)
    allow_international: bool = False
    purpose: Literal['runtime','collection'] = 'runtime'
    include_unconfigured: bool = False
    price_band: Literal['budget','midrange','premium'] | None = None
    configuration_gaps: tuple[Text,...] = ()
    seek_competing_offers: bool = False
    limit: Annotated[int,Field(strict=True,ge=1,le=20)] = 10
    candidate_limit: Annotated[int,Field(strict=True,ge=1,le=200)] = 100

    @model_validator(mode='after')
    def explicit_market_scope(self):
        if self.purpose == 'runtime' and not self.allow_international and any(m != 'LB' for m in self.purchase_markets):
            raise ValueError('Foreign purchase markets require explicit opt-in')
        if self.limit > self.candidate_limit:
            raise ValueError('Result bound exceeds candidate bound')
        return self


@dataclass(frozen=True)
class SourceSelection:
    record: DirectoryRecord
    configured: bool
    permitted_urls: tuple[str,...]
    reasons: tuple[str,...]


def normalized_alias(value):
    return ' '.join(value.casefold().split())


class Directory:
    def __init__(self, storage: Storage, *, registry_path=DEFAULT_REGISTRY):
        self.storage = storage
        self.registry_path = registry_path

    def upsert(self, record: DirectoryRecord) -> bool:
        record = DirectoryRecord.model_validate(record.model_dump(mode='json'))
        entry = record.entry
        payload = record.model_dump(mode='json')
        with self.storage.transaction() as db:
            db.execute(update(corpus_guard).where(corpus_guard.c.id==1).values(id=1))
            previous = db.execute(select(directory_entries).where(directory_entries.c.retailer_id==entry.retailer_id)).first()
            if previous:
                if record.updated_at < previous.updated_at or payload == previous.payload:
                    return False
                if record.updated_at == previous.updated_at:
                    raise ValueError('Conflicting directory revisions need distinct timestamps')
                db.execute(update(directory_entries).where(directory_entries.c.retailer_id==entry.retailer_id)
                    .values(payload=payload,updated_at=record.updated_at))
                for table in (directory_categories,directory_markets,directory_aliases):
                    db.execute(delete(table).where(table.c.retailer_id==entry.retailer_id))
            else:
                db.execute(insert(directory_entries).values(retailer_id=entry.retailer_id,payload=payload,updated_at=record.updated_at))
            for category in set(entry.categories):
                db.execute(insert(directory_categories).values(retailer_id=entry.retailer_id,category=category))
            for market in set(entry.markets):
                db.execute(insert(directory_markets).values(retailer_id=entry.retailer_id,market=market))
            for alias in {normalized_alias(v) for v in (entry.retailer_id,entry.name,*entry.aliases)}:
                db.execute(insert(directory_aliases).values(retailer_id=entry.retailer_id,alias=alias))
        return True

    def get(self, retailer_id: str) -> DirectoryRecord | None:
        with self.storage.engine.connect() as db:
            row = db.execute(select(directory_entries.c.payload).where(directory_entries.c.retailer_id==retailer_id)).first()
        return DirectoryRecord.model_validate(row.payload) if row else None

    async def get_entry(self, retailer_id: str) -> RetailerDirectoryEntry | None:
        record = self.get(retailer_id)
        return record.entry if record else None

    def resolve_alias(self, alias: str) -> DirectoryRecord | None:
        with self.storage.engine.connect() as db:
            ids = db.execute(select(directory_aliases.c.retailer_id)
                .where(directory_aliases.c.alias==normalized_alias(alias)).limit(2)).scalars().all()
        if len(ids)>1:
            raise ValueError('Ambiguous seller alias; use the canonical retailer ID')
        return self.get(ids[0]) if ids else None

    def _configured_urls(self, record):
        if not record.source_id or record.entry.discovery_status == 'retired':
            return ()
        try:
            source = get_active_source(record.source_id,self.registry_path)
            origins = {str(url).rstrip('/') for url in (record.entry.origin,*record.entry.related_origins)}
            if source['origin'].rstrip('/') not in origins:
                return ()
            rules = [EndpointCapability.model_validate(item) for item in source.get('endpoint_capabilities',[])]
            refs = (*record.capabilities, *((record.entry.capability,) if record.entry.capability else ()))
            if any(not any((r.capability_id,r.version)==(ref.capability_id,ref.version) for r in rules) for ref in refs):
                return ()
            if refs:
                rules = [r for r in rules if any((r.capability_id,r.version)==(ref.capability_id,ref.version) for ref in refs)]
            permitted = []
            for url in (*record.catalog_urls,*record.product_urls,*record.policy_urls):
                text = str(url)
                if text in source.get('allowed_endpoints',[]) or any(rule.matches(text) for rule in rules):
                    permitted.append(text)
            return tuple(dict.fromkeys(permitted))
        except (PermissionError,ValueError):
            return ()

    def select(self, query: DirectoryQuery) -> tuple[SourceSelection,...]:
        eligible_market = select(1).where(directory_markets.c.retailer_id==directory_entries.c.retailer_id,
            directory_markets.c.market.in_(query.purchase_markets)).exists()
        statement = select(directory_entries.c.payload).join(directory_categories).where(
            directory_categories.c.category==query.category,eligible_market)
        with self.storage.engine.connect() as db:
            rows = db.execute(statement.order_by(directory_entries.c.retailer_id).limit(query.candidate_limit)).all()
        selected = []
        for row in rows:
            record = DirectoryRecord.model_validate(row.payload)
            if record.entry.discovery_status=='retired' or (query.purpose=='collection' and not record.background_collection):
                continue
            urls = self._configured_urls(record)
            if not urls and not query.include_unconfigured:
                continue
            reasons = ['category coverage']
            if not urls:
                reasons.append('technical configuration gap; lead only')
            band = query.price_band is not None and query.price_band in record.price_bands
            gaps = bool(set(query.configuration_gaps) & set(record.configuration_gaps))
            if band:
                reasons.append('requested price-band coverage')
            if gaps:
                reasons.append('configuration coverage gap')
            if query.seek_competing_offers:
                reasons.append('investigate competing seller offers')
            if any(c.category==query.category and not c.retrieval_tested for c in record.coverage):
                reasons.append('category retrieval coverage gap')
            successes = sum(o.status==200 for o in record.observations)
            if record.observations:
                reasons.append(f'measured retrieval: {successes}/{len(record.observations)} successful responses; not offer qualification')
            if record.useful_evidence_ids:
                reasons.append(f'{len(set(record.useful_evidence_ids))} useful evidence references recorded')
            selection = SourceSelection(record,bool(urls),urls,tuple(reasons))
            # Collection/research priority only. No score enters offer eligibility/ranking.
            selected.append((int(bool(urls)),int(band)+int(gaps),len(set(record.useful_evidence_ids)),
                             successes,record.entry.retailer_id,selection))
        selected.sort(key=lambda item:(-item[0],-item[1],-item[2],-item[3],item[4]))
        return tuple(item[-1] for item in selected[:query.limit])


def import_audit(directory: Directory, *, seed_path=ROOT/'config/retailer-directory-seed.json',
                 audit_path=ROOT/'docs/retailer-audit-2026-11-16.json') -> int:
    """Import dated local research. Never activate a source or infer qualifying offers."""
    seeds = [RetailerDirectoryEntry.model_validate(item) for item in json.loads(Path(seed_path).read_text(encoding='utf-8'))]
    audit = json.loads(Path(audit_path).read_text(encoding='utf-8'))
    research = {item['retailer_id']:item for item in audit['retailers']}
    observations = {item['observation_id']:item for item in audit['observations']}
    changed = 0
    for entry in seeds:
        details = research[entry.retailer_id]
        routes = details['entry_points']
        attempts = tuple(RetrievalObservation.model_validate({k:v for k,v in observations[oid].items()
            if k in RetrievalObservation.model_fields}) for oid in details['observation_ids'])
        updated = max([o.checked_at for o in attempts]+([entry.last_checked_at] if entry.last_checked_at else [])) if attempts or entry.last_checked_at else f"{audit['date']}T00:00:00Z"
        identity = 'retailer' if details['canonical_seller'] else ('marketplace' if entry.retailer_id in {'ayoub_computers','hicart'} else 'unresolved')
        record = DirectoryRecord(entry=entry,updated_at=updated,source_id=entry.retailer_id,
            source_country='LB',seller_id=entry.retailer_id if identity=='retailer' else None,
            marketplace_id=entry.retailer_id if identity=='marketplace' else None,seller_identity=identity,
            catalog_urls=routes['catalog'],product_urls=routes['product'],policy_urls=routes['policy'],
            policy_links=[PolicyLink(url=p['url'],checked_on=p['checked_date'],summary=p['summary']) for p in details['policy_findings']],
            coverage=[CategoryCoverage(category=c,discovered=True,missing_fields=entry.coverage_gaps) for c in entry.categories],
            observations=attempts,background_collection=details['priority_audit'],role='domestic',
            configuration_gaps=entry.coverage_gaps)
        changed += directory.upsert(record)
    return changed


def import_international(directory: Directory) -> int:
    """Confirmed planning targets only; no retrieval or eligibility claim."""
    targets = (
        ('microless_uae','Microless UAE','https://global.microless.com','AE','direct_international'),
        ('bh_us','B&H US','https://www.bhphotovideo.com','US','direct_international'),
        ('amazon_us','Amazon US','https://www.amazon.com','US','forwarding_market'),
        ('myus','MyUS','https://www.myus.com','US','forwarder_primary'),
        ('stackry','Stackry','https://www.stackry.com','US','forwarder_backup'),
        ('shop_and_ship','Shop & Ship','https://www.shopandship.com','AE','candidate'),
    )
    changed = 0
    for retailer_id,name,origin,country,role in targets:
        forwarder = role.startswith('forwarder') or retailer_id=='shop_and_ship'
        entry = RetailerDirectoryEntry(retailer_id=retailer_id,name=name,origin=origin,
            markets=(country,),categories=('phone','laptop','tablet','monitor','audio'),
            discovery_status='discovered',coverage_gaps=('Planning target; retrieval, seller/route eligibility and quotes unverified.',))
        changed += directory.upsert(DirectoryRecord(entry=entry,updated_at='2026-11-15T00:00:00Z',
            source_id=retailer_id,source_country=country,seller_id=None if forwarder or retailer_id=='amazon_us' else retailer_id,
            seller_identity='forwarder' if forwarder else 'marketplace' if retailer_id=='amazon_us' else 'retailer',
            marketplace_id=retailer_id if retailer_id=='amazon_us' else None,
            catalog_urls=() if forwarder else (origin,),policy_urls=(origin,) if forwarder else (),
            background_collection=role!='candidate',role=role))
    return changed


def import_domestic_listings(directory: Directory) -> int:
    path = ROOT / 'config/domestic-listing-sellers.json'
    records = json.loads(path.read_text(encoding='utf-8'))
    return sum(directory.upsert(DirectoryRecord.model_validate(record)) for record in records)


class DirectoryPort:
    """Adapter for the async shared RetailerDirectory protocol without changing v2."""
    def __init__(self, directory: Directory):
        self.directory = directory

    async def get(self, retailer_id: str) -> RetailerDirectoryEntry | None:
        return await self.directory.get_entry(retailer_id)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['import-seeds'])
    parser.parse_args()
    storage = Storage()
    try:
        directory = Directory(storage)
        changed = import_audit(directory) + import_domestic_listings(directory) + import_international(directory)
        print(f'Imported/updated {changed} directory records; source configuration unchanged')
    finally:
        storage.engine.dispose()


if __name__ == '__main__':
    main()
