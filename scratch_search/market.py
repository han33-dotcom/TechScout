"""Discover listing candidates. The only shared project integration is scoped retailer transport."""
import asyncio
import html
import json
from pathlib import Path
from urllib.parse import urlencode

from .offers import Listing

FIXTURES = Path(__file__).with_name("fixtures")


class FixtureMarket:
    name = "synthetic"
    shops = ("Cedar", "Coast", "Outlet")

    async def discover(self, shop):
        return [Listing(shop=shop, title="Apple iPhone 17 Pro range",
                        url=f"https://{shop.lower()}.example/phones/iphone")]

    async def document(self, listing):
        return (FIXTURES / (listing.shop.lower() + ".html")).read_bytes(), listing.url


class RetailMarket:
    name = "live"
    shops = ("class_cellulars", "mojitech", "beytech")

    def __init__(self, run_id):
        from app.runs import RunBudget
        from app.sources import DEFAULT_REGISTRY, SourceGate
        from app.source_capabilities import EndpointCapability

        self.gate = SourceGate()
        self.budget = RunBudget(run_id)
        rows = json.loads(Path(DEFAULT_REGISTRY).read_text(encoding="utf-8"))["sources"]
        self.rules = {s["id"]: [EndpointCapability.model_validate(c) for c in s["endpoint_capabilities"]]
                      for s in rows if s["id"] in self.shops}

    async def fetch(self, shop, url, operation, kind):
        from app.source_capabilities import CapabilityRef

        rule = next((r for r in self.rules[shop] if r.operation == operation
                     and r.expect == kind and r.matches(url)), None)
        if rule is None:
            raise ValueError(f"{shop} has no reviewed {operation} scope for this URL")
        ref = CapabilityRef(capability_id=rule.capability_id, version=rule.version)
        response = await asyncio.to_thread(self.gate.fetch, self.budget, shop, url, expect=kind, capability=ref)
        if response.errors or not response.body:
            raise RuntimeError(", ".join(e.code for e in response.errors) or "empty retailer response")
        return response.body, response.final_url or url

    async def discover(self, shop):
        rule = next(r for r in self.rules[shop] if r.operation == "search" and r.expect == "json")
        query = {q.name: ("iPhone 17 Pro" if q.name == "search" else q.parameter.minimum)
                 for q in rule.query}
        url = f"https://{rule.host}/{'/'.join(rule.path)}?{urlencode(query)}"
        body, _ = await self.fetch(shop, url, "search", "json")
        rows = json.loads(body)
        if not isinstance(rows, list):
            raise ValueError("Unexpected public catalog response")
        return [Listing(shop=shop, title=html.unescape(r["name"]), url=r["permalink"])
                for r in rows if isinstance(r, dict) and isinstance(r.get("permalink"), str)
                and isinstance(r.get("name"), str)]

    async def document(self, listing):
        return await self.fetch(listing.shop, listing.url, "product", "html")
