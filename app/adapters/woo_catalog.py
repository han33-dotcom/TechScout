"""Bounded WooCommerce catalog candidates for the v2 evidence pipeline.

Candidates carry corpus-compatible field observations, not verified recommendations.
The legacy phone adapters remain available until full-flow integration (#76).
"""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import re
from urllib.parse import quote, urlsplit

from app.adapters.base import Snapshot, minor_units, tool_result
from app.adapters.woocommerce import read_html, _attributes, _options, _form_items, _SchemaError
from app.contracts import ToolError, ToolResult
from app.contracts_v2 import Evidence
from app.corpus_contracts import Extraction, FieldObservation
from app.source_capabilities import CapabilityRef, safe_endpoint


@dataclass(frozen=True)
class CatalogCandidate:
    source_id: str
    product_id: str
    variant_id: str
    category: str
    name: str
    listing_url: str
    extraction: Extraction
    selected_attributes: tuple[tuple[str, str], ...] = ()

    @property
    def priced(self):
        facts = {o.evidence[0].field_path: o.fact for o in self.extraction.observations}
        return (facts['price'].state == 'known' and facts['purchasable'].state == 'known'
                and facts['purchasable'].value is True)


@dataclass(frozen=True)
class CatalogResult:
    candidates: tuple[CatalogCandidate, ...]
    tool_result: ToolResult
    snapshots: tuple[Snapshot, ...]


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _records(outcome, limit=10):
    data = json.loads(outcome.body.decode('utf-8'), parse_float=Decimal, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite number')))
    if not isinstance(data, list) or len(data) > limit or not all(isinstance(r, dict) for r in data):
        raise ValueError('Invalid bounded product list')
    return data


def _id(value):
    return str(value) if type(value) is int and 0 < value < 10**12 else None


def _key(value):
    return re.sub(r'[^a-z0-9]', '', value.casefold())


def _spec(name, raw, category):
    """Only explicit attribute values; no configuration guessing from a title."""
    name = _key(name)
    field = {'brand':'variant.product.manufacturer', 'manufacturer':'variant.product.manufacturer',
             'model':'variant.product.model', 'color':'variant.color',
             'cpu':'variant.specs.cpu', 'processor':'variant.specs.cpu',
             'gpu':'variant.specs.gpu', 'graphics':'variant.specs.gpu',
             'region':'variant.specs.region'}.get(name)
    if field:
        return field, raw
    if name in {'ram', 'storage', 'capacity'} and (m := re.fullmatch(r'([1-9][0-9]{0,4})\s*(GB|TB)', raw, re.I)):
        value = int(m[1]) * (1024 if m[2].upper() == 'TB' else 1)
        return ('variant.specs.ram_gb' if name == 'ram' else 'variant.specs.storage_gb'), value
    if category == 'monitor':
        if name in {'refreshrate', 'refresh'} and (m := re.fullmatch(r'([1-9][0-9]{0,3})\s*Hz', raw, re.I)):
            return 'variant.specs.refresh_hz', int(m[1])
        if name in {'screensize', 'displaysize', 'size'} and (m := re.fullmatch(r'([1-9][0-9]?(?:\.[0-9])?)\s*(?:inches|inch|\")', raw, re.I)):
            return 'variant.specs.diagonal_inches', m[1]
    if category == 'phone' and name == 'sim':
        value = {'esim only':'esim-only', 'nano sim / esim':'physical+esim', 'dual physical sim':'dual-physical'}.get(raw.casefold())
        if value:
            return 'variant.specs.sim', value
    return None


class WooCatalog:
    max_products = 2
    max_variants = 40

    def __init__(self, source_id, origin, categories, page_paths):
        self.source_id, self.origin = source_id, origin
        self.categories, self.page_paths = categories, page_paths
        self.adapter_version = source_id + '-catalog/1'

    def _error(self, reason):
        return ToolError(code='INVALID_SCHEMA', message=f'{self.source_id}: {reason}', retryable=False)

    def _cap(self, kind):
        return CapabilityRef(capability_id=f'{self.source_id}-{kind}', version='1')

    def _page_kind(self, url):
        if not isinstance(url, str) or not safe_endpoint(url, urlsplit(self.origin).hostname):
            return None
        parts = urlsplit(url)
        if parts.query or not parts.path.endswith('/'):
            return None
        segments = parts.path.strip('/').split('/')
        for kind, prefix, depth in self.page_paths:
            if segments[:len(prefix)] == list(prefix) and len(segments) == depth and all(
                    re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}', p) for p in segments):
                return kind
        return None

    def search(self, fetch, *, category, search, run=None, only=None, corpus_version="catalog-preview-v1"):
        """Fetch at most one search page and two exact products (five calls).

        `only` limits current-run re-reading to (product_id, variant_id) pairs.
        No cached discovery or stored price substitutes for a fresh read.
        """
        if category not in self.categories or not isinstance(search, str) or not re.fullmatch(
                r'[a-zA-Z0-9][a-zA-Z0-9._-]*( [a-zA-Z0-9][a-zA-Z0-9._-]*)*', search) or len(search) > 64:
            raise ValueError('A supported category and bounded search string are required')
        outcomes, errors, snapshots, candidates = [], [], [], []
        def get(url, kind, expect):
            outcome = fetch(self.source_id, url, expect=expect, capability=self._cap(kind))
            outcomes.append(outcome)
            if not outcome.errors and outcome.body is not None:
                snapshots.append(Snapshot.of(outcome))
                return outcome
            return None
        url = f'{self.origin}/wp-json/wc/store/v1/products?search={quote(search)}&per_page=10&page=1'
        found = get(url, 'search', 'json')
        if found:
            try:
                records = _records(found)
                products = {}
                for record in records:
                    if record.get('type') not in {'simple','variable'} or type(record.get('parent')) is not int or record['parent'] != 0:
                        continue
                    cats = record.get('categories', [])
                    if not isinstance(cats, list) or not any(isinstance(c, dict) and c.get('slug') in self.categories[category] for c in cats):
                        continue
                    pid, slug, page = _id(record.get('id')), record.get('slug'), record.get('permalink')
                    if not pid or not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}', slug) or not self._page_kind(page):
                        errors.append(self._error('invalid or unsafe catalog identity'))
                        continue
                    if pid in products and products[pid] != record:
                        raise ValueError('conflicting product identity')
                    products[pid] = record
                if len(records) == 10 or len(products) > self.max_products:
                    errors.append(self._error('catalog discovery truncated by configured bound'))
                for pid, product in list(products.items())[:self.max_products]:
                    if only is not None and not any(p == pid for p, _ in only):
                        continue
                    api = get(f'{self.origin}/wp-json/wc/store/v1/products?slug={product["slug"]}', 'slug', 'json')
                    if not api:
                        continue
                    try:
                        exact = _records(api, 100)
                        parents = [(i, r) for i, r in enumerate(exact) if _id(r.get('id')) == pid and type(r.get('parent')) is int and r['parent'] == 0]
                        if not parents or any(r != parents[0][1] for _, r in parents):
                            raise ValueError('product changed or has conflicting records')
                        index, parent = parents[0]
                        if any(parent.get(k) != product.get(k) for k in ('id','slug','type','permalink','categories')):
                            raise ValueError('product identity changed after discovery')
                        page = get(product['permalink'], self._page_kind(product['permalink']), 'html')
                        candidates.extend(self._extract(api, index, parent, exact, page, category, run, only, corpus_version))
                    except (ValueError, TypeError, KeyError, _SchemaError, ArithmeticError, RecursionError):
                        errors.append(self._error('unsupported or conflicting product data'))
            except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError):
                errors.append(self._error('invalid or conflicting catalog data'))
        if only is not None and set(only) - {(c.product_id,c.variant_id) for c in candidates}:
            errors.append(self._error('requested configuration is no longer in bounded discovery'))
        data = {'candidates_seen':len(candidates), 'priced_candidates':sum(c.priced for c in candidates),
                'coverage_complete':False, 'requires_current_run_verification':True}
        ids = [e.evidence_id for c in candidates for o in c.extraction.observations for e in o.evidence]
        return CatalogResult(tuple(candidates), tool_result(outcomes, errors, data, ids, bool(candidates)), tuple(snapshots))

    def _extract(self, api, index, parent, records, page, category, run, only, corpus_version):
        pid = str(parent['id'])
        attrs = _attributes(parent.get('attributes'))
        variable = parent['type'] == 'variable'
        options = _options(parent.get('variations')) if variable else {pid:(0,{})}
        if len(options) > self.max_variants:
            raise ValueError('variant bound exceeded')
        items = _form_items(read_html(page.body).forms, pid) if variable and page else {}
        result = []
        for vid, (option_index, chosen) in options.items():
            if only is not None and (pid,vid) not in only:
                continue
            fields = {}
            def add(field, value, outcome, pointer, *, reason=None):
                identity = f'{outcome.response_sha256}/{outcome.observed_at.isoformat()}/{pid}/{vid}/{field}/{pointer}/{run}'
                receipt = Evidence(evidence_id=sha256(identity.encode()).hexdigest(),source_id=self.source_id,
                    product_id=pid,variant_id=vid,scope='variant',field_path=field,
                    origin_url=outcome.origin_url,final_url=outcome.final_url,observed_at=outcome.observed_at,
                    response_sha256=outcome.response_sha256,adapter_version=self.adapter_version,
                    json_pointer=pointer,origin='runtime',run=run,corpus_version=corpus_version)
                fields.setdefault(field,[]).append((value,receipt,reason))
            complete = True
            for attr in attrs:
                selected = [(v, f'/{index}/attributes/{attr.index}/terms/{i}/name') for i, (_, v) in enumerate(attr.terms)]
                if attr.variation:
                    matches = [(k,v) for name,(k,v) in chosen.items() if _key(name) in {_key(attr.name),_key(attr.key.removeprefix('attribute_'))}]
                    if len(matches) != 1 or not matches[0][1] or matches[0][1] not in {t for pair in attr.terms for t in pair}:
                        complete = False
                        selected = []
                    else:
                        k, value = matches[0]
                        selected = [(attr.term_name(value), f'/{index}/variations/{option_index}/attributes/{k}/value')]
                        for entry in items.get(vid, []):
                            if entry.data['attributes'].get(attr.key) not in {value, next((s for s,n in attr.terms if n==value),value)}:
                                complete = False
                for raw, pointer in selected:
                    if category == 'monitor' and _key(attr.name) == 'resolution' and (m := re.fullmatch(r'([1-9][0-9]{2,4})\s*[xX×]\s*([1-9][0-9]{2,4})', raw)):
                        add('variant.specs.width_px',int(m[1]),api,pointer)
                        add('variant.specs.height_px',int(m[2]),api,pointer)
                    elif spec := _spec(attr.name, raw, category):
                        add(*spec, api, pointer)
            if 'variant.product.model' not in fields and isinstance(parent.get('name'),str) and parent['name'].strip():
                add('variant.product.model', parent['name'], api, f'/{index}/name')
            readings = [(index,parent,api,f'/{index}',False)] if not variable else [
                (i,r,api,f'/{i}',False) for i,r in enumerate(records)
                if _id(r.get('id'))==vid and type(r.get('parent')) is int and str(r['parent'])==pid and r.get('type')=='variation']
            readings += [(e.index,e.data,page,f'/forms/{pid}/variations/{e.index}',True) for e in items.get(vid,[])]
            for _, record, outcome, pointer, html in readings:
                if variable and not html and record.get('attributes'):
                    # Unrecognized child attributes cannot override the parent's selected options.
                    complete = False
                for field,key in (('purchasable','is_purchasable'),('stock','is_in_stock')):
                    if type(record.get(key)) is bool:
                        value = record[key]
                        reason = 'backorder is not current stock' if field=='stock' and record.get('is_on_backorder') is True else None
                        add(field, value if not reason else None, outcome, pointer+'/'+key, reason=reason)
                prices = parent.get('prices',{}) if html else record.get('prices',{})
                currency, digits = prices.get('currency_code'), prices.get('currency_minor_unit')
                raw = record.get('display_price') if html else prices.get('price')
                try:
                    amount = Decimal(str(raw)) if html and type(raw) in (int,Decimal,str) else minor_units(raw,digits)
                    if not amount.is_finite() or amount <= 0 or currency not in {'USD','LBP','EUR','AED'}:
                        raise ValueError()
                    if type(digits) is not int or not 0 <= digits <= 6 or amount != amount.quantize(Decimal(1).scaleb(-digits)):
                        raise ValueError()
                    value, reason = {'amount':str(amount),'currency':currency}, None
                except (ValueError, TypeError, ArithmeticError):
                    value, reason = None, 'price is absent, zero, call-for-price or invalid'
                add('price',value,outcome,pointer+('/display_price' if html else '/prices/price'),reason=reason)
            for field in ('price','stock','purchasable'):
                if field not in fields:
                    add(field,None,api,f'/{index}',reason='variant-specific value is not published')
            observations = []
            purchasing = fields['purchasable']
            can_price = complete and all(value is True and not reason for value,_,reason in purchasing)
            for field, entries in fields.items():
                receipts = tuple({r.evidence_id:r for _,r,_ in entries}.values())
                values = []
                for value,_,reason in entries:
                    if reason is None and value not in values:
                        values.append(value)
                fact = {'evidence_ids':[r.evidence_id for r in receipts]}
                if not complete and field.startswith('variant.specs.'):
                    fact.update(state='unknown',reason='selected attributes disagree or configuration is incomplete')
                elif field=='price' and not can_price:
                    fact.update(state='unknown',reason='configuration is incomplete or not explicitly purchasable')
                elif any(reason for _,_,reason in entries) or not values:
                    fact.update(state='unknown',reason='; '.join(sorted({reason for _,_,reason in entries if reason})) or 'not published')
                elif len(values)>1:
                    fact.update(state='conflict',alternatives=values,reason='source values disagree for this configuration')
                else:
                    fact.update(state='known',value=values[0])
                observations.append(FieldObservation(fact=fact,evidence=receipts))
            result.append(CatalogCandidate(self.source_id,pid,vid,category,parent['name'],parent['permalink'],
                                           Extraction(observations=tuple(observations)),
                                           tuple((name,value) for name,(_,value) in chosen.items())))
        return result
