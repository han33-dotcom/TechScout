"""Bounded direct provider transport; fixed endpoint, no implicit retries/tools."""
import json
import time
import httpx2

_RESERVED_HEADERS = {'authorization', 'accept-encoding'}


def post_json(endpoint, api_key, payload, *, transport=None, seconds=15, max_bytes=2_097_152,
              headers=None, parse_float=None):
    """POST JSON with a bearer key. `headers` adds non-credential headers; `parse_float`
    (for example Decimal) keeps reported money exact instead of binary floats."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError('An explicit provider key is required')
    sent = {'Authorization': f'Bearer {api_key}', 'Accept-Encoding': 'identity'}
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not isinstance(value, str) or name.lower() in _RESERVED_HEADERS:
            raise ValueError('Extra headers cannot replace credentials or encoding')
        sent[name] = value
    body, _ = request_json('POST', endpoint, headers=sent, payload=payload, transport=transport,
                           seconds=seconds, max_bytes=max_bytes, parse_float=parse_float)
    return body


class _QueryTransport(httpx2.BaseTransport):
    """Apply secret or buyer-derived query parameters only to the on-wire request.

    The client logs its own request URL at INFO, including a SerpApi key supplied there.
    Its request, exceptions and redirect history should see only the fixed endpoint.
    """
    def __init__(self, inner, params):
        self.inner = inner if inner is not None else httpx2.HTTPTransport()
        self.params = params

    def handle_request(self, request):
        wire = httpx2.Request(request.method, request.url.copy_merge_params(self.params),
                              headers=request.headers, stream=request.stream, extensions=request.extensions)
        return self.inner.handle_request(wire)

    def close(self):
        self.inner.close()


def request_json(method, endpoint, *, headers, payload=None, params=None, transport=None,
                 seconds=15, max_bytes=2_097_152, parse_float=None):
    """Bound a single provider exchange and expose its response headers for attribution."""
    sent = {**headers, 'Accept-Encoding': 'identity'}
    deadline = time.monotonic() + seconds
    if params is not None:
        transport = _QueryTransport(transport, params)
    with httpx2.Client(transport=transport, timeout=min(5, seconds), follow_redirects=False) as client:
        with client.stream(method, endpoint, headers=sent, json=payload) as response:
            response.raise_for_status()
            if response.headers.get('content-encoding', 'identity') != 'identity':
                raise ValueError('Unexpected provider response encoding')
            if 'application/json' not in response.headers.get('content-type','').lower():
                raise ValueError('Provider response is not JSON')
            body = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() >= deadline:
                    raise TimeoutError('Provider deadline exceeded')
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError('Provider response exceeded its size bound')
            if time.monotonic() >= deadline:
                raise TimeoutError('Provider deadline exceeded')
            data = json.loads(body, parse_float=parse_float,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
            return data, response.headers
