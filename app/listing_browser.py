"""Read-only Chromium interaction for a selected public product option."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import time
from urllib.parse import urlsplit

from playwright.async_api import Error as BrowserError, async_playwright

from app.accounting import BudgetDenied, Charge
from app.firecrawl import ManagedPage
from app.onboarding import canonical
from app.sources import MAX_RESPONSE_BYTES


# Chromium serializes the original checked attribute, even when the user selected
# another radio. Copy the live control properties onto a detached document clone.
SNAPSHOT = """() => {
    const copy = document.documentElement.cloneNode(true);
    const controls = document.querySelectorAll('input,option');
    const cloned = copy.querySelectorAll('input,option');
    controls.forEach((el, i) => {
        if (el.type === 'radio') cloned[i].toggleAttribute('checked', el.checked);
        if (el.tagName === 'OPTION') cloned[i].toggleAttribute('selected', el.selected);
        if (el.type === 'hidden') cloned[i].setAttribute('value', el.value);
    });
    return copy.outerHTML;
}"""
# A checked radio alone is insufficient. Wait for the shop's own offer, displayed
# price, stock or selected-variant label to reflect the interaction.
MERCHANT_STATE = r"""() => JSON.stringify([
    ...Array.from(document.querySelectorAll('script[type="application/ld+json"],'
      + '[data-variant-price],[data-variant-stock],[data-dimension-value]'), el => el.textContent),
    ...Array.from(document.querySelectorAll('input[name="variant_id"]'), el => el.value),
    ...document.body.innerText.split(/\n/).map(s => s.trim()).filter(
        s => /(?:\b(?:USD|EUR|LBP)\b|\$)\s*[\d,.]+|\b(?:in|out of) stock\b/i.test(s))
])"""


async def _select(url, option, document, read, cancelled):
    host = urlsplit(url).netloc
    async with async_playwright() as playwright:
        # All allowed traffic is fulfilled through SourceGate. Keep the browser's
        # own network stack offline even for traffic outside Playwright routing.
        browser = await playwright.chromium.launch(chromium_sandbox=True, proxy={'server': 'http://127.0.0.1:9',
            'bypass': '<-loopback>'}, args=['--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        try:
            context = await browser.new_context(service_workers='block', accept_downloads=False)
            page = await context.new_page()
            page.set_default_timeout(4500)
            delivered = False

            async def route_request(route):
                nonlocal delivered
                req = route.request
                target = req.url
                if (req.method != 'GET' or req.frame != page.main_frame
                        or not canonical(target) or urlsplit(target).netloc != host):
                    return await route.abort()
                if req.is_navigation_request() and target == url and not delivered:
                    delivered = True
                    return await route.fulfill(body=document.body, content_type='text/html', headers={
                        'Content-Security-Policy': "default-src 'none'; script-src 'self' 'unsafe-inline'; "
                            "connect-src 'self'; style-src 'unsafe-inline'; form-action 'none'; base-uri 'self'; "
                            "frame-src 'none'; worker-src 'none'"})
                expect = {'script': 'script', 'xhr': 'json', 'fetch': 'json'}.get(req.resource_type)
                if not expect or not delivered:
                    return await route.abort()
                try:
                    resource = await asyncio.to_thread(read, target, expect)
                except (BudgetDenied, ValueError, OSError):
                    return await route.abort()
                if resource.status != 'ok' or resource.final_url != target:
                    return await route.abort()
                await route.fulfill(body=resource.body, content_type=resource.content_type)

            await context.route('**/*', route_request)
            await context.route_web_socket('**/*', lambda socket: socket.close())
            await page.goto(url, wait_until='load', timeout=14000)
            control = page.locator(option.selector())
            if await control.count() != 1 or not await control.is_enabled():
                raise ValueError('The selected retailer option is unavailable.')
            async def is_selected():
                if option.kind == 'select':
                    return await control.input_value() == option.value
                return await control.is_checked()

            if not await is_selected():
                before = await page.evaluate(MERCHANT_STATE)
                if option.kind == 'select':
                    await control.select_option(value=option.value)
                else:
                    await control.check()
                await page.wait_for_function('(before) => (' + MERCHANT_STATE + ')() !== before', arg=before)
            if page.url != url or not await is_selected() or cancelled():
                raise ValueError('The retailer did not confirm the requested selection.')
            return (await page.evaluate(SNAPSHOT)).encode()
        finally:
            await browser.close()


def select_listing(job, url, option, read):
    """Read a fresh listing and return only the confirmed browser state."""
    option.selector()
    document = read(url, 'html')
    if document.status != 'ok' or document.final_url != url or not document.body:
        reason = document.errors[0].code if document.errors else 'unavailable'
        raise ValueError(f'The browser could not load the retailer listing ({reason}).')
    work = job.reserve('work', Charge(Decimal(0)), 20)
    work.dispatch()
    started = time.monotonic()

    async def capture():
        async with asyncio.timeout(18):
            return await _select(url, option, document, read, lambda: job.cancelled)

    try:
        body = asyncio.run(capture())
        if not body or len(body) > MAX_RESPONSE_BYTES:
            raise ValueError('The selected browser document exceeds the capture limit.')
        result = replace(document, body=body, response_sha256=sha256(body).hexdigest(),
                         observed_at=datetime.now(timezone.utc), validators=None)
        return ManagedPage(result, 'playwright', None)
    except (BrowserError, TimeoutError) as exc:
        raise ValueError('The browser could not confirm the selected retailer state.') from exc
    finally:
        work.complete(Decimal(0), time.monotonic() - started)
