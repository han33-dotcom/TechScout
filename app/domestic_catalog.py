"""Query the shared evidence corpus for dated, exact-variant domestic shopping leads."""
import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from itertools import islice
from typing import get_args
from urllib.parse import quote, unquote, urlsplit

from sqlalchemy import String, cast, func, or_, select

from app.catalog_extraction import normalized_gtin
from app.contracts_v2 import Category
from app.corpus_contracts import FieldObservation, ProcessingVersion, ResourceKey
from app.directory import Directory, normalized_alias
from app.extraction import PageProfile, extract_structured
from app.source_capabilities import CapabilityRef, EndpointCapability
from app.source_registry import DEFAULT_REGISTRY, get_active_source
from app.storage import (
    Storage, corpus_document_observations as links, corpus_documents as documents,
    corpus_observations as observations, corpus_resources as resources, corpus_retirements as retirements, sources,
)

CATEGORIES = get_args(Category)
PREFIX = 'domestic-listing/'
DISPATCH_PREFIX = 'domestic-dispatch/'
LOOKUP_LIMIT = 100
CANDIDATE_LIMIT = 200


def known(fields, name):
    observation = fields.get(name)
    return observation.fact.value if observation and observation.fact.state == 'known' else None


def exact_variant_key(category, observations):
    """Use only strong item identifiers in the supplied extraction, including fresh reads."""
    if category not in CATEGORIES:
        raise ValueError('Unknown device category')
    fields = observations if isinstance(observations, dict) else {i.evidence[0].field_path:i for i in observations}
    if any(fields[name].fact.state == 'conflict' for name in ('catalog.gtin','catalog.mpn','catalog.configuration_id','variant.product.manufacturer')
           if name in fields):
        return None
    gtin = normalized_gtin(known(fields,'catalog.gtin'))
    if gtin:
        return f'{category}:gtin:{gtin}'
    brand, mpn = known(fields,'variant.product.manufacturer'), known(fields,'catalog.mpn')
    if brand and mpn:
        return f'{category}:mpn:{quote(normalized_alias(brand), safe="")}:{quote(mpn.strip().casefold(), safe="")}'
    configuration = known(fields,'catalog.configuration_id')
    if brand and configuration:
        return f'{category}:configuration:{quote(normalized_alias(brand), safe="")}:{quote(configuration, safe="")}'
    return None


@dataclass(frozen=True, kw_only=True)
class ListingProfile:
    source_id: str
    retailer_id: str
    seller_id: str
    seller_name: str
    seller_aliases: tuple[str,...]
    listing_url: str
    product_id: str
    sku: str
    category: str
    can_revisit: bool
    can_enumerate: bool
    capability: CapabilityRef | None
    enumeration_urls: tuple[str,...] = ()
    enumeration_capabilities: tuple[CapabilityRef,...] = ()

    @property
    def representation(self):
        return PREFIX + '/'.join(quote(x, safe='') for x in (self.category,self.retailer_id,self.seller_id))


@dataclass(frozen=True, kw_only=True)
class CatalogListing(ListingProfile):
    manufacturer: str | None
    model: str | None
    variant_key: str | None
    observed_at: datetime
    processing: ProcessingVersion
    document_id: int
    observations: dict[str,FieldObservation]

    def summary(self):
        # Each timestamp belongs to its own receipt, even when the listing has been revisited.
        return dict(source_id=self.source_id, retailer_id=self.retailer_id, seller_id=self.seller_id,
            seller=self.seller_name, url=self.listing_url, product_id=self.product_id, sku=self.sku,
            category=self.category, manufacturer=self.manufacturer, model=self.model, variant_key=self.variant_key,
            observed_at=self.observed_at.isoformat(), document_id=self.document_id,
            can_revisit=self.can_revisit, can_enumerate=self.can_enumerate,
            enumeration_urls=self.enumeration_urls, enumeration_capabilities=[v.model_dump() for v in self.enumeration_capabilities],
            payable_total=None, payable_total_status='requires current buyer route and complete mandatory charges',
            capability=self.capability.model_dump() if self.capability else None,
            fields={name:dict(state=item.fact.state, value=item.fact.value,
                observed_at=item.evidence[0].observed_at.isoformat(), evidence_ids=list(item.fact.evidence_ids),
                receipts=[dict(evidence_id=e.evidence_id,url=str(e.final_url),observed_at=e.observed_at.isoformat(),
                               field=e.field_path,scope=e.scope,locator=e.json_pointer,
                               excerpt=e.excerpt) for e in item.evidence])
                for name,item in self.observations.items()},
            unknown=[field for field in ('price','stock','purchasable','route.origin_country',
                'catalog.delivery_country','catalog.delivery_city','costs.order.tax','costs.order.shipping')
                if known(self.observations,field) is None])


def extract_listing(capture, listing, *, run=None, destination=None):
    """Project a single admitted first-party listing. The caller owns the SourceGate read."""
    if (capture.key.source_id != listing.source_id or capture.key.product_id != listing.product_id
            or str(capture.key.url) != str(listing.listing_url) or capture.key.variant_id != listing.sku):
        raise ValueError('Listing identity does not match the capture')
    url = urlsplit(str(listing.listing_url))
    extraction = extract_structured(capture, PageProfile(listing.source_id, f'{url.scheme}://{url.netloc}',
        sku=listing.sku, seller_names=(listing.seller_name,*listing.seller_aliases), category=listing.category),
        run=run, destination=destination)
    from app.domestic_retailer_profiles import enrich_listing
    return enrich_listing(capture, listing, extraction, run=run, destination=destination)


def dispatch_representation(profile):
    return DISPATCH_PREFIX + profile.representation[len(PREFIX):] + '/' + quote(profile.listing_url,safe='')


def _combine_dispatch(fields, receipts):
    items = ([fields['route.origin_country']] if 'route.origin_country' in fields else []) + receipts
    if not items:
        return
    evidence = {e.evidence_id:e for item in items for e in item.evidence}
    values = set()
    for item in items:
        if item.fact.state=='known':
            values.add(item.fact.value)
        else:
            values.update(item.fact.alternatives)
    if not values:
        return
    fact = (dict(state='known',value=next(iter(values))) if len(values)==1 else
            dict(state='conflict',alternatives=sorted(values),reason='Exact-item dispatch observations disagree'))
    fields['route.origin_country'] = FieldObservation(fact=dict(**fact,evidence_ids=list(evidence)),evidence=tuple(evidence.values()))


class DomesticCatalog:
    def __init__(self, storage, *, registry_path=DEFAULT_REGISTRY):
        self.storage = storage
        self.directory = Directory(storage, registry_path=registry_path)
        self.registry = registry_path

    def _profile(self, key, category, record, policy):
        if (not record or record.role != 'domestic' or record.source_country != 'LB'
                or record.source_id != key.source_id or record.seller_identity != 'retailer'
                or record.entry.discovery_status == 'retired' or category not in record.entry.categories
                or 'LB' not in record.entry.markets or not key.product_id or not key.variant_id):
            raise ValueError('No matching canonical first-party domestic seller for this listing')
        url = str(key.url)
        origin = policy['origin'].rstrip('/')
        if (origin not in {str(v).rstrip('/') for v in (record.entry.origin,*record.entry.related_origins)}
                or urlsplit(url).netloc != urlsplit(origin).netloc):
            raise ValueError('Listing and registered seller have different origins')
        rules = [EndpointCapability.model_validate(r) for r in policy.get('endpoint_capabilities',())]
        refs = {(r.capability_id,r.version) for r in record.capabilities}
        if record.entry.capability:
            refs.add((record.entry.capability.capability_id,record.entry.capability.version))
        if refs:
            if not refs <= {(r.capability_id,r.version) for r in rules}:
                raise ValueError('The directory references a superseded source capability')
            rules = [r for r in rules if (r.capability_id,r.version) in refs]
        exact = set(policy.get('allowed_endpoints',()))
        matching = next((r for r in rules if r.operation in ('product','variant') and r.expect=='html'
                         and r.matches(url)), None)
        # Exact product revisits are independent of catalog/search permissions.
        capability = CapabilityRef(capability_id=matching.capability_id,version=matching.version) if matching else None
        enumeration_rules = tuple(CapabilityRef(capability_id=r.capability_id,version=r.version) for r in rules
                                  if r.operation in ('catalog','feed','sitemap','search'))
        enumeration_urls = tuple(str(u) for u in record.catalog_urls if str(u) in exact and str(u)!=url)
        return ListingProfile(source_id=key.source_id, retailer_id=record.entry.retailer_id,
            seller_id=record.seller_id, seller_name=record.entry.name, seller_aliases=record.entry.aliases,
            listing_url=url, product_id=key.product_id, sku=key.variant_id, category=category,
            capability=capability, can_revisit=url in exact or matching is not None,
            can_enumerate=bool(enumeration_urls or enumeration_rules), enumeration_urls=enumeration_urls,
            enumeration_capabilities=enumeration_rules)

    def seed_profile(self, seed):
        descriptor = seed.listing or seed.dispatch_for
        listing_url = seed.dispatch_for.listing_url if seed.dispatch_for else seed.url
        key = ResourceKey(source_id=seed.source_id,url=listing_url,representation='html',
                          product_id=seed.product_id,variant_id=seed.sku)
        record = self.directory.get(descriptor.retailer_id)
        profile = self._profile(key, seed.category, record, get_active_source(key.source_id,self.registry))
        if not profile.can_revisit or (not seed.dispatch_for and seed.capability is not None
                                       and seed.capability != profile.capability):
            raise PermissionError('Domestic listing lacks the selected direct product permission')
        if seed.dispatch_for:
            policy = get_active_source(key.source_id,self.registry)
            if not self._collection_permitted(str(seed.url),policy,seed.capability):
                raise PermissionError('Dispatch membership requires an admitted exact collection read')
        return profile

    @staticmethod
    def _collection_permitted(url, policy, ref=None):
        if not ref and url in policy.get('allowed_endpoints',()):
            return True
        for rule in map(EndpointCapability.model_validate,policy.get('endpoint_capabilities',())):
            if (rule.operation=='catalog' and rule.expect=='html' and rule.matches(url)
                    and (ref is None or (ref.capability_id,ref.version)==(rule.capability_id,rule.version))):
                return True
        return False

    def _dispatch(self, db, profiles, heads, as_of):
        # Companion document URL is the read resource, with the observed listing explicitly bound
        # in its representation. Only current, nonretired membership can support this exact item.
        by_rep = defaultdict(list)
        for profile in profiles:
            by_rep[dispatch_representation(profile)].append(profile)
        if not by_rep:
            return {}
        rep = resources.c.resource_key['representation'].as_string()
        retired = select(1).where(retirements.c.resource_id==resources.c.resource_id,
                                  retirements.c.observed_at>=documents.c.observed_at)
        if as_of:
            retired = retired.where(retirements.c.observed_at<=as_of)
        statement = (select(resources.c.resource_key,observations.c.payload)
            .select_from(documents.join(heads,heads.c.head_id==documents.c.document_id)
                .join(resources,resources.c.resource_id==heads.c.resource_id)
                .join(links,links.c.document_id==documents.c.document_id).join(observations))
            .where(rep.in_(tuple(by_rep)), observations.c.field_path=='route.origin_country',
                   ~resources.c.needs_processing,~retired.exists()))
        by_id = defaultdict(list)
        policies = {}
        for raw,payload in db.execute(statement):
            key = ResourceKey.model_validate(raw)
            if key.source_id not in policies:
                try:
                    policies[key.source_id] = get_active_source(key.source_id,self.registry)
                except PermissionError:
                    policies[key.source_id] = {}
            if self._collection_permitted(str(key.url),policies[key.source_id]):
                for p in by_rep[key.representation]:
                    if (key.source_id,key.product_id,key.variant_id)==(p.source_id,p.product_id,p.sku):
                        by_id[p.document_id].append(FieldObservation.model_validate(payload))
        return by_id

    def _listings(self, category, *, text=None, listing_url=None, as_of=None, candidate_limit=None):
        if category not in CATEGORIES:
            raise ValueError('Unknown device category')
        heads = select(documents.c.resource_id,func.max(documents.c.document_id).label('head_id'))
        if as_of:
            heads = heads.where(documents.c.observed_at <= as_of)
        heads = heads.group_by(documents.c.resource_id).subquery()
        retired = select(1).where(retirements.c.resource_id==resources.c.resource_id,
                                  retirements.c.observed_at>=documents.c.observed_at)
        if as_of:
            retired = retired.where(retirements.c.observed_at<=as_of)
        statement = (select(documents.c.document_id,documents.c.observed_at,documents.c.processing,resources.c.resource_key)
            .join(heads,heads.c.head_id==documents.c.document_id)
            .join(resources,resources.c.resource_id==heads.c.resource_id)
            .join(sources,sources.c.id==resources.c.source_id)
            .where(sources.c.status=='active',~resources.c.needs_processing,resources.c.resource_key['representation'].as_string().startswith(PREFIX+category+'/'),
                   ~retired.exists()))
        if listing_url is not None:
            statement = statement.where(resources.c.resource_key['url'].as_string()==str(listing_url))
        if text:
            if len(text)>300 or len(text.split())>20:
                raise ValueError('Product query must fit within 300 characters and 20 terms')
            for term in text.lower().split():
                matches = select(1).select_from(links.join(observations)).where(
                    links.c.document_id==documents.c.document_id, observations.c.fact_state=='known',
                    or_(observations.c.field_path.startswith('variant.'), observations.c.field_path.in_(
                        ('catalog.gtin','catalog.mpn','catalog.configuration_id'))),
                    func.lower(cast(observations.c.fact_value,String)).contains(term,autoescape=True))
                statement = statement.where(or_(matches.exists(),func.lower(
                    resources.c.resource_key['variant_id'].as_string()).contains(term,autoescape=True)))
        statement = statement.order_by(documents.c.observed_at.desc(),documents.c.document_id.desc())
        if candidate_limit is not None:
            statement = statement.limit(candidate_limit)
        records, policies = {}, {}
        with self.storage.engine.connect() as db:
            for batch in db.execute(statement).partitions(100):
                ids = [r.document_id for r in batch]
                fields = defaultdict(dict)
                data = select(links.c.document_id,observations.c.field_path,observations.c.payload).join(observations).where(
                    links.c.document_id.in_(ids))
                for document_id, name, payload in db.execute(data):
                    fields[document_id][name] = FieldObservation.model_validate(payload)
                projected = []
                for row in batch:
                    key = ResourceKey.model_validate(row.resource_key)
                    if not fields[row.document_id]:
                        continue
                    parts = key.representation[len(PREFIX):].split('/')
                    if len(parts)!=3:
                        continue
                    _, retailer, seller = map(unquote,parts)
                    if retailer not in records:
                        records[retailer] = self.directory.get(retailer)
                    if key.source_id not in policies:
                        try:
                            policies[key.source_id] = get_active_source(key.source_id,self.registry)
                        except PermissionError:
                            policies[key.source_id] = None
                    if policies[key.source_id] is None:
                        continue
                    try:
                        profile = self._profile(key,category,records[retailer],policies[key.source_id])
                    except ValueError:
                        continue
                    if profile.seller_id!=seller:
                        continue
                    facts = fields[row.document_id]
                    projected.append(CatalogListing(**vars(profile), manufacturer=known(facts,'variant.product.manufacturer'),
                        model=known(facts,'variant.product.model'),variant_key=exact_variant_key(category,facts),
                        observed_at=row.observed_at,processing=ProcessingVersion.model_validate(row.processing),
                        document_id=row.document_id,observations=facts))
                companion = self._dispatch(db,projected,heads,as_of)
                for item in projected:
                    if item.document_id in companion:
                        _combine_dispatch(item.observations,companion[item.document_id])
                    yield item

    def lookup(self, *, category, text=None, listing_url=None, limit=20):
        if type(limit) is not int or not 1<=limit<=LOOKUP_LIMIT:
            raise ValueError(f'Listing limit must be 1 to {LOOKUP_LIMIT}')
        return tuple(islice(self._listings(category,text=text,listing_url=listing_url,candidate_limit=CANDIDATE_LIMIT),limit))

    def price_history(self, listing, *, limit=20):
        """Return original price changes, including retained conflicting observations."""
        if type(limit) is not int or not 1<=limit<=LOOKUP_LIMIT:
            raise ValueError(f'Price history limit must be 1 to {LOOKUP_LIMIT}')
        key = resources.c.resource_key
        statement = (select(observations.c.payload)
            .select_from(observations.join(documents,documents.c.document_id==observations.c.source_document_id)
                .join(resources,resources.c.resource_id==documents.c.resource_id))
            .where(resources.c.source_id==listing.source_id,key['representation'].as_string()==listing.representation,
                   key['url'].as_string()==listing.listing_url,key['product_id'].as_string()==listing.product_id,
                   key['variant_id'].as_string()==listing.sku,observations.c.field_path=='price')
            .order_by(observations.c.observed_at.desc(),observations.c.observation_id.desc()).limit(limit))
        with self.storage.engine.connect() as db:
            return tuple(FieldObservation.model_validate(payload) for payload in db.execute(statement).scalars())

    def coverage(self, *, as_of, recent_for=timedelta(days=7)):
        if as_of.tzinfo is None or recent_for<=timedelta(0):
            raise ValueError('Coverage needs an aware timestamp and positive price window')
        by_category, examples = {}, []
        for category in CATEGORIES:
            recent, domestic = defaultdict(list), defaultdict(list)
            listing_count = 0
            for item in self._listings(category,as_of=as_of):
                listing_count += 1
                if not item.variant_key or not item.can_revisit or known(item.observations,'price') is None:
                    continue
                dated = item.observations['price'].evidence[0].observed_at
                if not as_of-recent_for<=dated<=as_of:
                    continue
                recent[item.variant_key].append(item)
                if known(item.observations,'route.origin_country')=='LB' and any(
                        as_of-recent_for<=e.observed_at<=as_of for e in item.observations['route.origin_country'].evidence):
                    domestic[item.variant_key].append(item)
            comparable = {k:v for k,v in recent.items() if _consistent_configuration(v)}
            pairs = {k:v for k,v in comparable.items() if len({i.seller_id for i in v})>=2}
            local = {k:v for k,v in domestic.items() if k in comparable}
            domestic_pairs = 0
            for key,items in local.items():
                sellers = {i.seller_id:i for i in reversed(items)}
                if len(sellers)>=2:
                    domestic_pairs += 1
                    if not examples:
                        examples.append(dict(variant_key=key, listings=[i.summary() for i in list(sellers.values())[:2]]))
            by_category[category] = dict(listings=listing_count,recent_priced_variants=len(comparable),
                recent_priced_domestic_sellers=len({item.seller_id for items in local.values() for item in items}),
                recent_priced_variants_with_two_sellers=len(pairs),recent_priced_domestic_dispatch_variants=len(local),
                domestic_variants_with_two_sellers=domestic_pairs,conflicting_variant_keys=len(recent)-len(comparable))
        totals = {name:sum(c[name] for c in by_category.values()) for name in next(iter(by_category.values()))}
        return dict(as_of=as_of.isoformat(),price_window_seconds=int(recent_for.total_seconds()),
                    categories=by_category,totals=totals,example=examples[0] if examples else None)


def _consistent_configuration(items):
    fields = {'catalog.gtin','catalog.mpn','catalog.configuration_id','variant.product.manufacturer',
              'variant.product.model','variant.color'}
    fields.update(f for i in items for f in i.observations if f.startswith('variant.specs.'))
    for name in fields:
        if any(i.observations[name].fact.state=='conflict' for i in items if name in i.observations):
            return False
        values = {normalized_alias(str(v)) for i in items if (v:=known(i.observations,name)) is not None}
        if len(values)>1:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--category', choices=CATEGORIES)
    mode.add_argument('--coverage', action='store_true')
    parser.add_argument('--query')
    parser.add_argument('--listing-url')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--as-of', type=datetime.fromisoformat, default=None)
    parser.add_argument('--recent-days', type=int, default=7)
    args = parser.parse_args()
    storage = Storage()
    try:
        catalog = DomesticCatalog(storage)
        result = (catalog.coverage(as_of=args.as_of or datetime.now(timezone.utc), recent_for=timedelta(days=args.recent_days))
                  if args.coverage else [i.summary() for i in catalog.lookup(
                      category=args.category,text=args.query,listing_url=args.listing_url,limit=args.limit)])
        print(json.dumps(result,indent=2))
    finally:
        storage.engine.dispose()


if __name__ == '__main__':
    main()
