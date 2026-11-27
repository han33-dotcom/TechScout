"""Offline model replies for the synthetic product pages used in buyer/catalog journeys."""
from datetime import datetime, timezone
import re

from app.page_observation import PageDecision, PageOffer, normalized
from app.selected_listing import ConflictingSelection, ListingDocument, PageReading


def fixture_read(data, text_reader=None):
    html = data['page']['html']
    assert len(html) == data['page']['length']
    doc = ListingDocument(html)
    now = datetime.now(timezone.utc)
    try:
        selected = doc.selected(data['url'], now, rendered=data['reader'] == 'playwright')
    except ConflictingSelection as exc:
        return PageDecision(fits=False, reason=str(exc))
    except ValueError:
        if not text_reader:
            return PageDecision(fits=False, reason='No selected offer in the fixture capture.')
        reading = text_reader(doc.evidence(data['url'], now))
        if not isinstance(reading, PageReading):
            reading = PageReading.model_validate(reading)
        try:
            selected = doc.interpret(reading, data['url'], now)
        except ValueError:
            return PageDecision(fits=False, reason=reading.reason or 'No priced offer in the fixture capture.')
    raw = normalized(html)
    def quote(*values):
        return next((v for v in values if v and normalized(v) in raw), '')
    address = next((s for s in selected.address_lines if 'Beirut' in s and s in raw), '')
    if not address and selected.structured_address:
        address = re.search(r'"addressCountry"\s*:\s*"LB"', raw).group()
    fits = selected.color == 'Black' if selected.brand.lower() == 'soundcore' else selected.storage() == (128, '128 GB')
    return PageDecision(offer=PageOffer(
        title=selected.title, sku=quote(selected.sku), brand=selected.brand, seller=selected.seller,
        color=selected.color, options=[dict(name=k, value=v) for k,v in selected.options],
        price=selected.money, price_quote=quote(selected.price_excerpt, str(selected.money.amount)),
        currency_quote=quote(selected.currency_excerpt, selected.money.currency, '$'),
        condition=selected.condition or None, condition_quote=quote(selected.condition_excerpt, selected.condition),
        stock=selected.availability or None, stock_quote=quote(selected.stock_excerpt, selected.availability),
        merchant_address_quote=address, variant_href=selected.published_link or ''),
        fits=fits, reason='Checked the selected fixture configuration.')
