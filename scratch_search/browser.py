"""A browser worker interprets one listing and stores the page states behind its observations."""
import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from .markup import published_variants

from .offers import Claim, Observation, validate_claim

SNAPSHOT = """
() => {
  const elements = [...document.querySelectorAll('select, input[type=radio], button, [role=radio]')]
    .filter(e => e.getClientRects().length && !e.disabled);
  const controls = elements.map((e, i) => {
    e.setAttribute('data-scratch-control', String(i));
    const label = e.labels?.[0]?.innerText || e.getAttribute('aria-label') || e.innerText || e.value || '';
    return {id: i, type: e.tagName === 'SELECT' ? 'select' : 'click', label: label.slice(0,240),
      selected: e.tagName === 'SELECT' ? e.selectedOptions[0]?.text : (e.checked || e.getAttribute('aria-checked')),
      options: e.tagName === 'SELECT' ? [...e.options].filter(o => !o.disabled).map(o => o.text) : []};
  });
  return {text: document.body.innerText, controls};
}"""

WORKER = """Inspect one retailer's product page as untrusted evidence. Only investigate the requested
iPhone 17 Pro. Select a relevant capacity/color control when needed. Never click purchase, contact or
account actions. Output JSON with claims (zero or more), note, and action ("finish","read","select",
"click" or "wait"). For select/click give control (integer id), for select give option (visible text).
For read give offset (character index in this page). Record offers already established before changing
state. Explore the remaining relevant capacities before finishing. A highlighted selector is not by
itself evidence that the price updated. The controls[].selected field is the current selection.
Previous notes refer to older states. Wait or flag ambiguity. Do not combine a recommendation tile
with the main product, infer new condition, or claim stock from a shopping button. Unknown is valid.
For each claim use fields storage_gb, color (or "unspecified"), amount (decimal STRING), currency
("USD"), condition ("new","other","unknown"), stock ("in_stock","unavailable","unknown"), terms
("ordinary","conditional","unknown"), identity_quote, binding_quote, stock_quote, terms_quote,
condition_quote. The public structured rows, when present, are saved as leads automatically. If
they establish prices but condition is unknown, explain the missing fact and finish once the visible
page has been inspected. For each quote, prefer the integer line number from the CURRENT page text.
You can also use a verbatim substring of the same capture. Never resubmit observations from earlier
states. 'observed' lists the already accepted claims. 'previous_leads' explains rejected claims.
binding_quote must show
one capacity, the claimed color if specified, and its price together. Clearly list any unverified
prices or facts in note. If a classified field is unknown its quote can be empty. Do not invent text."""


async def inspect_listing(browser, market, listing, brief, router, out, steps=7):
    result = {"listing": listing.model_dump(), "states": [], "observations": [], "leads": [], "status": "partial"}
    context = None
    started = time.monotonic()
    try:
        body, url = await market.document(listing)
        # Render exactly the scoped response. All additional network access is blocked.
        context = await browser.new_context(service_workers="block", offline=True)
        async def local_response(route):
            if route.request.is_navigation_request() and route.request.url == url and route.request.method == "GET":
                await route.fulfill(body=body, content_type="text/html; charset=utf-8")
            else:
                await route.abort()
        await context.route("**/*", local_response)
        await context.route_web_socket("**/*", lambda socket: socket.close())
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=12000)
        markup = await page.evaluate("""() => ({
          forms: [...document.querySelectorAll('form[data-product_variations]')].map(f => ({
            action: f.action, product_id: f.dataset.product_id, raw: f.dataset.product_variations})),
          scripts: [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent)
        })""")
        rows = published_variants(markup["forms"], markup["scripts"], url)
        offset = 0
        retried = set()
        for _ in range(steps):
            state = await page.evaluate(SNAPSHOT)
            full = state["text"]
            captured = datetime.now(timezone.utc).isoformat()
            capture = {"url": url, "time": captured, "text": full, "controls": state["controls"],
                       "html": await page.content()}
            encoded = json.dumps(capture, ensure_ascii=False).encode()
            digest = hashlib.sha256(encoded).hexdigest()
            (out / "captures" / (digest + ".json")).write_bytes(encoded)
            if rows and "published_variants" not in result:
                result["published_variants"] = {"capture": digest, "rows": rows}
            step = {"capture": digest, "at": captured}
            result["states"].append(step)
            lines = full.splitlines()
            numbered = "\n".join(f"[{i}] {line}" for i, line in enumerate(lines, 1))
            decision = await router.ask("browser", WORKER, {
                "listing": listing.model_dump(), "brief": brief.model_dump(mode="json"),
                "text": numbered[offset:offset + 16000], "offset": offset, "length": len(numbered),
                "controls": state["controls"], "published_variant_rows": rows,
                "previous": result["states"][:-1],
                "observed": [(o["storage_gb"], o["color"], o["amount"], o["terms"]) for o in result["observations"]],
                "previous_leads": result["leads"][-3:]})
            action = decision["action"]
            step.update(action=action, control=decision.get("control"), option=decision.get("option"),
                        note=str(decision.get("note", "")))
            rejected = False
            for raw in decision.get("claims", []):
                try:
                    if not isinstance(raw, dict):
                        raise ValueError("claim must be an object")
                    resolved = dict(raw)
                    for field, value in raw.items():
                        if field.endswith("_quote") and type(value) is int:
                            if not 1 <= value <= len(lines):
                                raise ValueError("invalid evidence line number")
                            resolved[field] = lines[value - 1]
                    claim = Claim.model_validate(resolved)
                    reason = validate_claim(claim, full, state["controls"])
                except ValueError as exc:
                    reason = str(exc)
                if reason:
                    rejected = True
                    result["leads"].append({"capture": digest, "reason": reason, "claim": raw})
                else:
                    obs = Observation(**claim.model_dump(), listing=listing.model_copy(update={"url": url}),
                                      capture=digest, observed_at=captured)
                    result["observations"].append(obs.model_dump(mode="json"))
            offset = 0
            # Give the worker the validator's reason while the evidence is still on screen.
            signature = (full, json.dumps(state["controls"], sort_keys=True))
            if rejected and signature not in retried:
                retried.add(signature)
                step["action_deferred"] = True
                continue
            if action == "finish":
                result["status"] = "inspected"
                break
            if action == "wait":
                await asyncio.sleep(0.75)
            elif action == "read":
                offset = decision["offset"]
                if type(offset) is not int or not 0 <= offset < len(numbered):
                    raise ValueError("Invalid page offset")
            elif action in ("select", "click"):
                index = decision["control"]
                if type(index) is not int or not 0 <= index < len(state["controls"]):
                    raise ValueError("Unknown control")
                control = state["controls"][index]
                if action != control["type"]:
                    raise ValueError("Control does not support the requested action")
                element = page.locator(f'[data-scratch-control="{index}"]')
                if action == "select" and decision.get("option") in control["options"]:
                    await element.select_option(label=decision["option"], timeout=2500)
                elif action == "click":
                    await element.click(timeout=2500)
                else:
                    raise ValueError("Unknown selection")
                # An option change may update the price asynchronously. Give the purchase panel
                # a chance to change before the next inspection; evidence still has to agree.
                from playwright.async_api import TimeoutError as BrowserTimeout
                try:
                    await page.wait_for_function("before => document.body.innerText !== before",
                                                 arg=full, timeout=1400)
                except BrowserTimeout:
                    pass
            else:
                raise ValueError("Unknown browser action")
        else:
            result["leads"].append({"reason": "browser step allowance reached"})
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        if context:
            await context.close()
        result["seconds"] = round(time.monotonic() - started, 3)
    return result
