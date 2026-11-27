import asyncio
import importlib.util
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from scratch_search.markup import published_variants
from scratch_search.market import FIXTURES, FixtureMarket
from scratch_search.offers import Brief, Claim, Listing, Observation, compare, validate_claim, wrong_listing
from scratch_search.run import search
from scratch_search.score import score


def claim(size=256, price="1050"):
    return Claim(storage_gb=size, color="Silver", amount=price, currency="USD", condition="new",
                 stock="in_stock", terms="ordinary", identity_quote="Apple iPhone 17 Pro",
                 binding_quote=f"Apple iPhone 17 Pro · {size} GB · Silver · USD {price}",
                 stock_quote="In stock online", condition_quote="Condition: New", terms_quote="Outright purchase")


def observation(shop="Cedar", **changes):
    data = claim().model_dump()
    data.update(changes)
    return Observation(**data, listing=Listing(shop=shop, title="iPhone 17 Pro", url=f"https://{shop}.example"),
                       capture="fixture", observed_at="2026-11-27T10:00:00+00:00")


class FieldChecks(unittest.TestCase):
    def test_wrong_family_even_if_identity_is_cropped(self):
        item = claim()
        text = "\n".join((item.binding_quote.replace(" Pro", " Pro Max"), item.stock_quote,
                          item.condition_quote, item.terms_quote))
        item.binding_quote = "256 GB · Silver · USD 1050"
        self.assertIsNotNone(validate_claim(item, text))

    def test_discovery_preserves_exact_phone_and_rejects_explicit_mismatches(self):
        self.assertIsNone(wrong_listing("Apple | iPhone 17 Pro 1 SIM, 2 years warranty"))
        self.assertIsNotNone(wrong_listing("Apple | iPhone 17 Pro MAX 1 SIM"))
        self.assertIsNotNone(wrong_listing("ZAGG-Cases for iPhone 17 Pro"))
        self.assertIsNone(wrong_listing("Apple phone in silver"))
        self.assertIsNone(wrong_listing("iPhone 17 Pro with free case"))

    def test_delayed_panel_and_unrelated_price(self):
        item = claim()
        page = "\n".join((item.binding_quote, item.stock_quote, item.condition_quote, item.terms_quote))
        self.assertIsNone(validate_claim(item, page))
        self.assertIn("selected storage", validate_claim(item, page, [{"selected": "512 GB"}]))
        self.assertIn("absent", validate_claim(item.model_copy(update={"binding_quote": "256 GB USD 950"}), page))

    def test_floor_ties_budgets_conflicts_and_conditions(self):
        cedar, coast = observation(), observation("Coast")
        offer_set = [cedar, cedar, coast, observation("Outlet", terms="conditional", amount="700.00"),
                     observation("Elsewhere", stock="unknown", amount="900.00")]
        self.assertEqual([o.listing.shop for o in compare(Brief(max_usd="1050"), offer_set)], ["Cedar", "Coast"])
        self.assertEqual(compare(Brief(max_usd="1049.99"), offer_set), [])
        different = observation(amount="1060")
        self.assertEqual(compare(Brief(max_usd="1055"), [cedar, different]), [])
        self.assertEqual(compare(Brief(), [cedar, observation(stock="unavailable")]), [])

    def test_terms_and_unknown_condition(self):
        item = claim()
        page = "\n".join((item.binding_quote, item.stock_quote, item.condition_quote, "with trade-in"))
        item.terms_quote = "with trade-in"
        self.assertIn("conditional", validate_claim(item, page))
        self.assertEqual(compare(Brief(), [observation(condition="unknown")]), [])

    def test_published_variants_require_matching_form_and_currency(self):
        url = "https://shop.example/item"
        form = {"action": url, "product_id": "12", "raw": '[{"attributes":{"size":"256GB"},'
                '"display_price":1100.05,"variation_id":4,"is_in_stock":false}]'}
        script = json.dumps({"@graph": [{"@type": "Product", "offers": {"@type": "Offer",
                             "priceCurrency": "USD", "url": url}}]})
        rows = published_variants([form], [script], url)
        self.assertEqual((rows[0]["display_price"], rows[0]["currency"], rows[0]["is_in_stock"]),
                         ("1100.05", "USD", False))
        self.assertEqual(published_variants([form], [script], url + "/other"), [])
        self.assertIsNone(published_variants([form], [], url)[0]["currency"])


class ScriptedRouter:
    """Offline browser decisions. The independent reference is only loaded by the scorer."""
    model = "scripted-offline"

    def __init__(self):
        self.calls = []

    async def ask(self, role, instructions, data):
        self.calls.append({"role": role, "cost_usd": "0"})
        if role == "brief":
            return {}
        if role == "planner":
            return {"ids": list(data["remaining"])[:2], "reason": "test all candidates"}
        shop = data["listing"]["shop"]
        if shop == "Cedar":
            large = claim(512, "1085")
            if not data["previous"]:
                return {"action": "select", "control": 0, "option": "512 GB",
                        "claims": [claim().model_dump(mode="json")]}
            if large.binding_quote not in data["text"]:
                return {"action": "wait"}
            return {"action": "finish", "claims": [large.model_dump(mode="json")]}
        if shop == "Coast":
            return {"action": "finish", "claims": [claim(price="1069").model_dump(mode="json")]}
        return {"action": "finish", "note": "Wrong model"}


class BrowserCheckpoint(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("playwright"), "install scratch_search/requirements.txt")
    def test_both_strategies_with_real_chromium_and_no_external_calls(self):
        ref = json.loads((FIXTURES / "reference.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ("fixed", "adaptive"):
                with self.subTest(mode=mode):
                    out = Path(tmp) / mode
                    report = asyncio.run(search(ref["query"], FixtureMarket(), ScriptedRouter(), out, mode))
                    self.assertEqual(report["status"], "finished", report.get("error"))
                    stats = score(out / "report.json", ref)
                    self.assertEqual(stats["correctly_won_capacities"], 2)
                    self.assertEqual(stats["unsupported_winners"], 0)
                    self.assertEqual(stats["missing_or_changed_winner_captures"], [])
                    self.assertEqual(stats["missing_winning_offers_including_ties"], 0)
                    self.assertEqual(Decimal(report["cost_usd"]), 0)
                    digest = report["winners"][0]["capture"]
                    (out / "captures" / (digest + ".json")).write_text("changed")
                    self.assertIn(digest, score(out / "report.json", ref)["missing_or_changed_winner_captures"])
