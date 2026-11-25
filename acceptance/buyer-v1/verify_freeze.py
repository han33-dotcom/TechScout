"""Validate the November buyer-reference freeze offline. Uses only the standard library."""

import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATE = "2026-11-25"
CONTRACT = "buyer-product-contract/v1"


def load(name):
    return json.loads((ROOT / name).read_bytes())


def keyed(rows):
    result = {row["id"]: row for row in rows}
    assert len(result) == len(rows), "duplicate IDs"
    return result


def check_offer(offer, observations, destination):
    listing = observations[offer["listing"]]
    assert listing["url"] == offer["url"] and listing["status"] == 200
    assert offer["new_condition_and_online_stock"] == listing["id"]
    product = listing["product"]
    price = Decimal(offer["price_usd"])
    for advertised in product["advertised_offers"]:
        if (advertised["priceCurrency"] == "USD" and Decimal(advertised["price"]) == price
                and advertised["availability"].endswith("/InStock")):
            break
    else:
        raise ValueError(f"No matching in-stock price at {listing['id']}")
    condition = advertised.get("itemCondition", product.get("itemCondition", ""))
    assert condition.endswith("NewCondition") or "new" in listing.get("observed", "").lower()
    if "selected_purchase_option" in listing:
        selected = listing["selected_purchase_option"]
        assert selected["stock"] and Decimal(selected["USD"]) == price
    for source in offer["seller_and_service_evidence"] + offer["total_evidence"]:
        assert observations[source]["url"].startswith("https://")
    total = Decimal(offer["payable_usd"])
    if offer["delivery_usd"] is not None:
        assert price + Decimal(offer["delivery_usd"]) == total
    quotes = [observations[key] for key in offer["total_evidence"]]
    if offer["seller"] == "ITWorks Lebanon":
        assert set(offer["total_evidence"]) == {"J7", "J8"}
        assert offer["delivery_usd"] == "5.00"
    else:
        quote, = quotes
        assert quote["snapshot_sha256"]
        if "lines" in quote:
            assert quote["city"] == destination and quote["url"] == listing["url"]
            assert Decimal(quote["lines"][-1].lstrip("$")) == total
        else:
            assert destination in quote["observed"] and f"USD {total:.2f}" in quote["observed"]


def main():
    freeze = load(f"freeze-v1-{DATE}.json")
    assert (freeze["status"], freeze["contract"], freeze["scored"]) == ("frozen", CONTRACT, False)
    pool = []
    for filename, expected in freeze["input_sha256"].items():
        payload = (ROOT / filename).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(payload).hexdigest() == expected, filename
        if filename.startswith("requests"):
            pool.extend(json.loads(payload)["cases"])
    inputs = keyed(pool)
    original = load(f"screening-{DATE}.json")
    labels = keyed(original["cases"])
    observations = keyed(original["observations"] + load(freeze["evidence"])["observations"])
    review = load(freeze["review"])
    audit = keyed(review["cases"])
    cases = keyed(freeze["cases"])
    assert len(labels) == 25 and len(inputs) == 43 and inputs.keys() == audit.keys()
    assert len(set(c["prompt"] for c in pool)) == len(pool)
    study = load("../../eval/heldout.json")
    assert {c["buyer_text"] for c in study["cases"]}.isdisjoint(c["prompt"] for c in pool)
    assert Counter(r["class"] for r in audit.values()) == review["counts"]
    for key, row in audit.items():
        assert row["reviewed_on_utc"] == DATE and row["contract"] == CONTRACT
        assert row["reference_and_reason"] and row["observations"]
        assert set(row["observations"]) <= observations.keys()
        assert row["selected"] == (key in cases)
        assert inputs[key] in load(row["input_file"])["cases"]
        if key in labels:
            prior = row["original_dated_label"]
            assert prior["label"] == labels[key]["screened_as"] and prior["on_utc"] == DATE
        else:
            assert row["original_dated_label"] is None
    classes = Counter(row["expected_class"] for row in cases.values())
    assert classes == {"full": 10, "partial": 5, "blocked": 5}
    assert len({r["input"]["category"] for r in cases.values()}) == 5
    assert {r["input"]["entry"] for r in cases.values()} == {"exact", "needs"}
    assert {r["input"]["mode"] for r in cases.values()} == {"delivery", "pickup"}
    assert sum(r["comparison_flag"] for r in cases.values()) == 5
    for key, row in cases.items():
        assert row["input"] == inputs[key] and row["expected_class"] == audit[key]["class"]
        assert row["observations"] == audit[key]["observations"]
        assert row["expected_buyer_outcome"] == audit[key]["reference_and_reason"]
        offers = row.get("offers", [])
        assert bool(offers) == (row["expected_class"] == "full")
        assert row["comparison_flag"] == (len(offers) == 2)
        if offers:
            terms = row["fulfillment"]
            assert row["variant"] and terms["destination"] == inputs[key]["destination"]
            assert terms["quantity"] == 1 and terms["condition"] == "new"
            for offer in offers:
                check_offer(offer, observations, terms["destination"])
        if row["comparison_flag"]:
            low, high = sorted(offers, key=lambda o: Decimal(o["payable_usd"]))
            assert low["seller"] != high["seller"]
            assert row["comparison"]["cheaper_seller"] == low["seller"]
            assert Decimal(row["comparison"]["difference_usd"]) == (
                Decimal(high["payable_usd"]) - Decimal(low["payable_usd"]))
        if row["expected_class"] == "partial":
            part = row["partial_offer"]
            assert part["missing"] and part["payable_usd"] is None
            assert observations[part["listing"]]["url"] == part["url"]
    print("43 screened, 25 original labels preserved. 20 frozen, 10 full, 5 partial, 5 blocked.")
    print("5 comparable seller pairs. All 5 categories, both entry paths and both fulfillment modes.")


if __name__ == "__main__":
    main()
