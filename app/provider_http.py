"""Bounded direct provider transport; fixed endpoint, no implicit retries/tools."""
import json
import time
import httpx2


def post_json(endpoint, api_key, payload, *, transport=None, seconds=15, max_bytes=2_097_152):
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError('An explicit provider key is required')
    deadline = time.monotonic() + seconds
    with httpx2.Client(transport=transport, timeout=min(5, seconds), follow_redirects=False) as client:
        with client.stream('POST', endpoint, headers={'Authorization': f'Bearer {api_key}',
                'Accept-Encoding': 'identity'}, json=payload) as response:
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
            return json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
