"""Read published variation rows without attributing a page-wide stock or condition to them."""
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit


def published_variants(forms, scripts, url):
    """Only join public records associated with the current listing's exact URL."""
    currencies = set()
    def collect(value):
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            if value.get("@type") in ("Offer", "AggregateOffer") and value.get("url") == url:
                currency = value.get("priceCurrency")
                if isinstance(currency, str):
                    currencies.add(currency)
            for field in ("@graph", "offers"):
                collect(value.get(field))
    for script in scripts:
        try:
            collect(json.loads(script))
        except (ValueError, RecursionError):
            continue
    result = []
    for form in forms:
        if form["action"] != url or urlsplit(url).scheme != "https":
            continue
        try:
            rows = json.loads(form["raw"], parse_float=Decimal)
        except ValueError:
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("attributes"), dict):
                continue
            try:
                amount = Decimal(str(row["display_price"]))
                if not amount.is_finite() or amount <= 0:
                    continue
            except (KeyError, InvalidOperation):
                continue
            result.append({"form_product_id": form["product_id"], "variation_id": row.get("variation_id"),
                           "attributes": row["attributes"], "display_price": str(amount),
                           "currency": next(iter(currencies)) if len(currencies) == 1 else None,
                           "is_in_stock": row.get("is_in_stock"), "is_purchasable": row.get("is_purchasable"),
                           "source": "listing form data-product_variations; currency from matching offer URL"})
    return result
