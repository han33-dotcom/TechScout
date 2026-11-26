"""Read only the Class Cellulars phone variant physically selected in the retailer form."""
from app import contracts as v1
from app.adapters import class_cellulars as shop
from app.adapters.base import CandidateQuery
from app.adapters.woocommerce import MODEL_UNKNOWN, ProductEndpoints, _OfferBuilder, _SchemaError, parse_api, read_page
from app.buyer_sources import DeliveryChoice, convert_offer
from app.domestic_buyer_sources import ReadResult
from app.source_capabilities import CapabilityRef


def selected_phone(sources, request, run, url, state, row, job):
    source = 'class_cellulars'
    city = request.fulfillment.destination.city
    if (request.requirements.category != 'phone' or not city or request.fulfillment.mode == 'pickup'
            or not url.startswith(shop.ORIGIN + '/product/')):
        return None
    document = sources.gate.documents.get(url)
    if document is None or document.response_sha256 != state['document']:
        return None
    slug = url.split('/')[-2]
    api_url = shop.ORIGIN + '/wp-json/wc/store/v1/products?slug=' + slug
    api_doc = sources.gate.fetch(job.source_budget(sources.directory.registry_path), source, api_url,
                                 expect='json', capability=CapabilityRef(capability_id='class-slug', version='1'))
    if api_doc.status != 'ok' or api_doc.final_url != api_url:
        return None
    try:
        api = parse_api(api_doc, state['product'])
        if (api.record.get('permalink') != url or api.record.get('slug') != slug
                or not any(c.get('slug') in ('mobiles', 'smartphones', 'mobile-phones')
                           for c in api.record['categories']) or api.name != state['heading']):
            return None
        page, problem = read_page(document, state['product'])
        vid = row['variant']
        if problem or not page.items or len(page.items.get(vid, [])) != 1 or vid not in api.options:
            return None
        if page.items[vid][0].data.get('attributes') != state['selected']:
            return None
        product = ProductEndpoints(state['product'], api_url, url, api.name)
        query = CandidateQuery('', api.name, 0, v1.Fulfillment(mode='delivery', country='LB', city=city))
        builder = _OfferBuilder(shop.ADAPTER, product, api, page, shop.page_facts(page.text or ''),
                                shop.price_tax_claims(page.product_price), query, vid)
        raw = builder.build()
        # The fixed reader's small known-model dictionary may lack a name that
        # the current catalog parent and its product-page heading both state.
        if raw.variant.model.reason == MODEL_UNKNOWN and not raw.variant.model.evidence_ids:
            record = builder.name_record('variant.model', 'product')
            data = raw.model_dump()
            data['variant']['model'] = dict(state='known', value=api.name, evidence_ids=[record.evidence_id])
            data['evidence'].append(record.model_dump())
            raw = v1.Offer.model_validate(data)
        replies = frozenset((d.response_sha256, d.observed_at) for d in (api_doc, document))
        offer = convert_offer(raw, run=run, current=replies, route=DeliveryChoice(city),
                              retailer_id=source, seller_name='Class Cellulars')
        # The selection establishes no warehouse location or delivery fee.
        offer = offer.model_copy(update={'route': offer.route.model_copy(update={'origin_country': 'ZZ'})})
        return ReadResult(source, (offer,), replies)
    except (_SchemaError, ValueError, TypeError, AttributeError, KeyError, IndexError):
        return None
