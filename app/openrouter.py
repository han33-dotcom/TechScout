"""Optional OpenRouter gateway adapter behind the ModelProvider interface (T53, #89).

Direct OpenAI (app.model_provider.OpenAIProvider) stays the default for every role
and for the main evaluation. Nothing selects this adapter automatically: it exists
only when a configuration file sets ``enabled: true`` and the backend supplies
OPENROUTER_API_KEY; the caller then passes it to a role as its ``provider``.

Every attempt is pinned to one configured model ID and one approved serving
provider, with OpenRouter's own provider and model fallbacks disabled. A fallback
is a separate request to the next explicitly configured route, allowed only in a
labelled experiment; it gets its own spend reservation and ledger record and stays
inside the same deadline and cancellation. Spend comes from OpenRouter's reported
cost. Missing or malformed accounting keeps the full reservation: unknown is never
zero. The key never leaves the transport closure, and no prompt, raw answer or
provider error text is recorded.
"""
import copy
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StringConstraints, ValidationError, model_validator
import httpx2

from app.accounting import BudgetDenied
from app.contracts import Amount, Model, Text
from app.model_provider import ModelResult, _schema
from app.provider_http import post_json

# Code-owned allowlist: the key is only ever sent to these documented OpenRouter hosts.
# The regional hosts are available to business/enterprise accounts only.
ENDPOINTS = ('https://openrouter.ai/api/v1/chat/completions',
             'https://eu.openrouter.ai/api/v1/chat/completions',
             'https://us.openrouter.ai/api/v1/chat/completions')
# Endpoint capabilities (OpenRouter's supported_parameters names) every request relies on.
REQUIRED_PARAMETERS = ('response_format', 'structured_outputs', 'max_tokens')
FRAMING_BYTES = 4096

# Explicit "author/slug" IDs only: no ":floor"/":nitro"/":online"/":free" variants, which
# choose providers or add hosted tools, and no router models (checked below).
ModelId = Annotated[str, StringConstraints(
    strict=True, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$')]
ProviderSlug = Annotated[str, StringConstraints(
    strict=True, max_length=64, pattern=r'^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)?$')]
# Evaluation protocol (eval/models.json): optional experiment labels start with exp-.
Label = Annotated[str, StringConstraints(strict=True, pattern=r'^exp-[a-z0-9][a-z0-9._-]{0,59}$')]
FallbackTrigger = Literal['provider_unavailable', 'rate_limited', 'unavailable']


class RouteRates(Model):
    """USD per million tokens at the pinned serving provider, copied from OpenRouter at setup."""
    input: Amount
    cached: Amount
    cache_write: Amount
    output: Amount
    request: Amount = Decimal(0)  # USD per request, where the endpoint charges one


class Route(Model):
    model: ModelId
    provider: ProviderSlug
    supported_parameters: tuple[Text, ...] = Field(min_length=1, max_length=64)
    rates: RouteRates

    @model_validator(mode='after')
    def _explicit_model(self):
        if self.model.split('/', 1)[0].lower() == 'openrouter':
            raise ValueError('Router models choose a model automatically; configure an explicit model ID')
        return self


class OpenRouterConfig(Model):
    provider: Literal['openrouter'] = 'openrouter'
    enabled: StrictBool = False
    purpose: Literal['evaluation', 'experiment'] = 'evaluation'
    experiment: Label | None = None
    endpoint: Literal[ENDPOINTS] = ENDPOINTS[0]
    routes: tuple[Route, ...] = Field(default=(), max_length=3)
    allow_fallback: StrictBool = False
    fallback_on: tuple[FallbackTrigger, ...] = ('provider_unavailable', 'rate_limited')
    data_collection: Literal['allow', 'deny'] = 'deny'
    zdr: StrictBool = False
    effort: Literal['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] | None = None
    max_output_tokens: int = Field(default=4096, strict=True, ge=16, le=16000)
    max_input_tokens: int = Field(default=65536, strict=True, ge=8192, le=100000)
    timeout_seconds: float = Field(default=30, gt=0, le=30)
    prompt_version: Text = 'roles-v1'
    pricing_checked_on: date | None = None

    @model_validator(mode='after')
    def _coherent(self):
        pairs = [(r.model, r.provider) for r in self.routes]
        if len(set(pairs)) != len(pairs):
            raise ValueError('Routes must be distinct model/provider pairs')
        if len(self.routes) > 1 and not self.allow_fallback:
            raise ValueError('Additional routes are fallbacks and need allow_fallback: true')
        if self.allow_fallback and (len(self.routes) < 2 or not self.fallback_on
                                    or len(set(self.fallback_on)) != len(self.fallback_on)):
            raise ValueError('An explicit fallback needs at least two routes and distinct triggers')
        if self.purpose == 'evaluation' and (self.allow_fallback or self.experiment is not None):
            raise ValueError('The main evaluation pins one route, with no fallback or experiment label')
        if self.purpose == 'experiment' and self.experiment is None:
            raise ValueError('An optional experiment needs its own label')
        if self.enabled:
            if not self.routes or self.pricing_checked_on is None:
                raise ValueError('An enabled adapter needs explicit routes and a pricing check date')
            if any(r.rates.input <= 0 or r.rates.output <= 0 for r in self.routes):
                raise ValueError('Enabled routes need positive verified rates; an unknown cost is never zero')
        return self


class ProviderUnavailable(ValueError):
    """The optional gateway is disabled, unconfigured or has no backend key; nothing was sent."""


def load_openrouter_config(path=None):
    """Disabled unless an explicit configuration file's ``openrouter`` section enables it."""
    if path is None:
        return OpenRouterConfig()
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or 'openrouter' not in raw:
        raise ProviderUnavailable('The configuration file has no openrouter section')
    return OpenRouterConfig.model_validate(raw['openrouter'])


def route_fingerprint(settings):
    """Stable identifier of the pinned route/model settings recorded with every attempt."""
    pinned = settings.model_dump(mode='json', exclude={'enabled'})
    return hashlib.sha256(json.dumps(pinned, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]


_HTTP_STATUS = {400: 'invalid_request', 401: 'access_denied', 402: 'insufficient_credits',
                403: 'access_denied', 404: 'unavailable', 408: 'timeout', 413: 'input_too_large',
                429: 'rate_limited', 502: 'provider_unavailable', 503: 'provider_unavailable'}


def _http_status(code):
    if type(code) is not int or not 100 <= code <= 599:
        return 'malformed_response'
    return _HTTP_STATUS.get(code, f'http_{code}')


def _text(value, limit=200):
    return value[:limit] if isinstance(value, str) and value.strip() else None


def _scalar(value):
    if type(value) is int:
        return str(value)
    return _text(value, 40)


def _norm(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def _bound(rates, s):
    """Conservative ceiling: the whole input allowance at the dearest input/cache rate."""
    return ((s.max_input_tokens * max(rates.input, rates.cached, rates.cache_write)
             + s.max_output_tokens * rates.output) / Decimal(1_000_000) + rates.request)


def _accounting(reply):
    """Token counts and OpenRouter's reported cost (USD credits); raises when either is unknown."""
    u = reply['usage']
    p, c, t = u['prompt_tokens'], u['completion_tokens'], u['total_tokens']
    if any(type(n) is not int or n < 0 for n in (p, c, t)) or t != p + c:
        raise ValueError('Malformed token accounting')
    if u.get('is_byok') is not False:  # missing counts as unknown: BYOK cost excludes the upstream bill
        raise ValueError('Only an explicit non-BYOK cost can be accounted for')
    cost = u['cost']
    if type(cost) not in (int, Decimal) or not Decimal(cost).is_finite() or cost < 0:
        raise ValueError('Reported cost is not a known nonnegative decimal')

    def detail(group, name):
        value = (u.get(group) or {}).get(name)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError('Malformed token detail')
        return value
    cached, written = detail('prompt_tokens_details', 'cached_tokens'), detail('prompt_tokens_details', 'cache_write_tokens')
    reasoning = detail('completion_tokens_details', 'reasoning_tokens')
    if (cached or 0) + (written or 0) > p or (reasoning or 0) > c:
        raise ValueError('Token details exceed their totals')
    return dict(input_tokens=p, output_tokens=c, total_tokens=t, cached_tokens=cached,
                cache_write_tokens=written, reasoning_tokens=reasoning), Decimal(cost)


def _estimate(tokens, rates):
    """What the configured rates predict; a higher reported cost is labelled for reconciliation."""
    p, cached, written = tokens['input_tokens'], tokens['cached_tokens'], tokens['cache_write_tokens']
    if cached is None or written is None:
        prompt = p * max(rates.input, rates.cached, rates.cache_write)
    else:
        prompt = (p - cached - written) * rates.input + cached * rates.cached + written * rates.cache_write
    return (prompt + tokens['output_tokens'] * rates.output) / Decimal(1_000_000) + rates.request


def _route_metadata(reply, route, rec):
    """Record serving provider and endpoint metadata when reported; label it when not."""
    reported = _text(reply.get('provider'), 100)
    meta = reply.get('openrouter_metadata')
    if isinstance(meta, dict):
        endpoints = meta.get('endpoints')
        rows = endpoints.get('available') if isinstance(endpoints, dict) else None
        if isinstance(rows, list):
            rows = [r for r in rows if isinstance(r, dict)]
            rec['endpoints_available'] = len(rows)
            chosen = [r for r in rows if r.get('selected') is True]
            if reported is None and len(chosen) == 1:
                reported = _text(chosen[0].get('provider'), 100)
        tried = meta.get('attempts')
        if isinstance(tried, list):
            rec['gateway_attempts'] = [dict(provider=_text(a.get('provider'), 100), model=_text(a.get('model'), 128),
                                            status=_scalar(a.get('status')))
                                       for a in tried[:5] if isinstance(a, dict)]
    rec['serving_provider'] = reported
    gaps = rec['metadata_unavailable']
    if reported is None:
        gaps.append('serving_provider')
    else:
        # Display names differ from slugs ("Google" vs "google-vertex"), so a mismatch is a
        # label for review, not proof; the request itself allowed only the approved provider.
        rec['provider_verified'] = _norm(reported) == _norm(route.provider.split('/')[0])
        if not rec['provider_verified']:
            rec['deviations'].append('serving_provider_unconfirmed')
    if rec['endpoints_available'] is None:
        gaps.append('endpoint_metadata')
    if rec['system_fingerprint'] is None:
        gaps.append('system_fingerprint')
    if rec['actual_model'] is None:
        gaps.append('actual_model')


def _answer(reply, route, output):
    choices = reply['choices']
    if type(choices) is not list or len(choices) != 1:
        return 'invalid_output', None
    choice = choices[0]
    message = choice['message']
    if message.get('model') not in (None, route.model):
        return 'model_mismatch', None
    if message.get('tool_calls'):
        return 'invalid_output', None  # no tools were offered; a tool call is never executed
    finish = choice.get('finish_reason')
    if message.get('refusal') or finish == 'content_filter':
        return 'refused', None
    if finish in ('length', 'error'):
        return 'incomplete', None
    if finish != 'stop' or message.get('role') != 'assistant' or type(message.get('content')) is not str:
        return 'invalid_output', None
    return 'completed', output.model_validate_json(message['content'])


def _wait(call, deadline, stopped):
    """Run one request in a worker thread; stop waiting at cancellation or the deadline."""
    done, box = threading.Event(), {}

    def run():
        try:
            box['reply'] = call()
        except Exception as exc:  # noqa: BLE001 - mapped to a status; its text is never kept
            box['error'] = exc
        finally:
            done.set()
    threading.Thread(target=run, daemon=True, name='openrouter-response').start()
    while not done.wait(min(.05, max(0, deadline - time.monotonic()))):
        if stopped():
            return 'cancelled', None
        if time.monotonic() >= deadline:
            return 'timeout', None
    if stopped():
        return 'cancelled', None
    if time.monotonic() >= deadline:
        return 'timeout', None
    if 'error' in box:
        return 'error', box['error']
    return 'reply', box['reply']


def _failure(exc, rec):
    if isinstance(exc, httpx2.HTTPStatusError):
        rec['error_code'] = exc.response.status_code
        return _http_status(exc.response.status_code)
    if isinstance(exc, (TimeoutError, httpx2.TimeoutException)):
        return 'timeout'
    if isinstance(exc, ValueError):
        return 'malformed_response'
    return 'transport_error'


class OpenRouterProvider:
    """ModelProvider over OpenRouter chat completions, built only on explicit opt-in."""
    name = 'openrouter'

    def __init__(self, config=None, *, api_key=None, client=None, transport=None):
        config = config if config is not None else load_openrouter_config()
        if not isinstance(config, OpenRouterConfig):
            raise TypeError('An OpenRouterConfig is required')
        # Re-validate: model_copy(update=...) would otherwise skip the pinning and allowlist rules.
        self.settings = OpenRouterConfig.model_validate(config.model_dump())
        if not self.settings.enabled:
            raise ProviderUnavailable('OpenRouter is disabled; direct OpenAI remains the default provider')
        if client is None:
            key = api_key if api_key is not None else os.environ.get('OPENROUTER_API_KEY')
            if not isinstance(key, str) or not key.strip():
                raise ProviderUnavailable('OpenRouter requires a backend OPENROUTER_API_KEY')
            endpoint, seconds = self.settings.endpoint, self.settings.timeout_seconds

            def client(payload):
                return post_json(endpoint, key, payload, transport=transport, seconds=seconds,
                                 headers={'X-OpenRouter-Metadata': 'enabled'}, parse_float=Decimal)
        self._client = client
        self.fingerprint = route_fingerprint(self.settings)

    def __repr__(self):
        return f'OpenRouterProvider(purpose={self.settings.purpose!r}, fingerprint={self.fingerprint!r})'

    def _context(self):
        s = self.settings
        return dict(provider='openrouter', purpose=s.purpose, experiment=s.experiment,
                    route_fingerprint=self.fingerprint, endpoint=s.endpoint, prompt_version=s.prompt_version,
                    pricing_checked_on=s.pricing_checked_on.isoformat(),
                    routing=dict(allow_fallbacks=False, require_parameters=True,
                                 data_collection=s.data_collection, zdr=s.zdr))

    def _payload(self, route, instructions, data, output):
        s = self.settings
        provider = dict(order=[route.provider], only=[route.provider], allow_fallbacks=False,
                        require_parameters=True, data_collection=s.data_collection)
        if s.zdr:
            provider['zdr'] = True
        payload = dict(model=route.model,
                       messages=[dict(role='system', content=instructions),
                                 dict(role='user', content=json.dumps(data, ensure_ascii=False, allow_nan=False))],
                       response_format=dict(type='json_schema', json_schema=dict(
                           name='result', strict=True, schema=_schema(output))),
                       max_tokens=s.max_output_tokens, stream=False, provider=provider)
        if s.effort:
            payload['reasoning'] = dict(effort=s.effort)
        return payload

    def generate(self, instructions, data, output, *, budget, cancelled=None):
        s = self.settings
        deadline = time.monotonic() + s.timeout_seconds  # one deadline for every attempt

        def stopped():
            return bool((cancelled and cancelled()) or getattr(budget, 'cancelled', False))
        attempts, status, value = [], None, None
        for route in s.routes:
            status, value, record = self._attempt(route, len(attempts), instructions, data, output,
                                                  budget, stopped, deadline)
            attempts.append(record)
            if status == 'completed' or not s.allow_fallback or status not in s.fallback_on:
                break
        # A fallback refused before sending (budget, deadline, cancellation) leaves the outcome of
        # the last request actually sent; the refusal is recorded under usage['fallback'].
        final = next((a for a in reversed(attempts) if a['dispatched']), attempts[-1])
        if final is not attempts[-1]:
            status, value = final['status'], None
        return ModelResult(status, value, self._summary(attempts, final))

    def _attempt(self, route, index, instructions, data, output, budget, stopped, deadline):
        s = self.settings
        rec = dict(index=index, requested_model=route.model, requested_provider=route.provider,
                   actual_model=None, serving_provider=None, provider_verified=None, response_id=None,
                   system_fingerprint=None, endpoints_available=None, gateway_attempts=None,
                   pricing=route.rates.model_dump(mode='json'), tokens=None, reserved_usd=None,
                   actual_usd=None, rate_estimate_usd=None, billing='not_sent', dispatched=False,
                   latency_ms=None, error_code=None, status=None, metadata_unavailable=[], deviations=[])

        def done(status, value=None):
            rec['status'] = status
            return status, value, rec
        if stopped():
            return done('cancelled')
        if time.monotonic() >= deadline:
            return done('timeout')
        needed = set(REQUIRED_PARAMETERS) | ({'reasoning'} if s.effort else set())
        if not needed <= set(route.supported_parameters):
            return done('unsupported_parameters')
        payload = self._payload(route, instructions, data, output)
        if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + FRAMING_BYTES > s.max_input_tokens:
            return done('input_too_large')
        reserved = _bound(route.rates, s)
        hold = budget.reserve_spend(reserved)
        if hold is None:
            return done('budget_denied')
        rec.update(reserved_usd=str(reserved), billing='unknown')
        sent, actual = False, None

        def ledger():
            if hasattr(hold, 'record'):
                hold.record(dict(self._context(), **rec))
        try:
            ledger()  # durable before dispatch; no key, prompt or raw answer
            if stopped():
                return done('cancelled')
            if time.monotonic() >= deadline:
                return done('timeout')
            if hasattr(hold, 'dispatch'):
                hold.dispatch()
            sent = rec['dispatched'] = True
            started = time.monotonic()
            kind, reply = _wait(lambda: self._client(payload), deadline, stopped)
            rec['latency_ms'] = round((time.monotonic() - started) * 1000)
            if kind in ('cancelled', 'timeout'):
                return done(kind)
            if kind == 'error':
                return done(_failure(reply, rec))
            if type(reply) is not dict:
                return done('malformed_response')
            if 'error' in reply:  # a 200 reply can still carry a mid-generation failure
                error = reply['error']
                code = error.get('code') if isinstance(error, dict) else None
                rec['error_code'] = code if type(code) is int else None
                return done(_http_status(code))
            rec.update(response_id=_text(reply.get('id')), actual_model=_text(reply.get('model'), 128),
                       system_fingerprint=_text(reply.get('system_fingerprint')))
            _route_metadata(reply, route, rec)
            mismatch = reply.get('model') != route.model
            if mismatch:
                rec['deviations'].append('model_mismatch')
            try:
                tokens, actual = _accounting(reply)
            except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
                tokens, actual = None, None
            if actual is not None:
                estimate = _estimate(tokens, route.rates)
                rec.update(tokens=tokens, actual_usd=str(actual), rate_estimate_usd=str(estimate), billing='known')
                if actual > estimate:
                    rec['deviations'].append('cost_above_configured_rates')
                elif actual < estimate / 2:  # e.g. an undeclared BYOK fee or a changed price
                    rec['deviations'].append('cost_below_configured_rates')
            ledger()
            if actual is not None and (actual > reserved or tokens['input_tokens'] > s.max_input_tokens
                                       or tokens['output_tokens'] > s.max_output_tokens):
                return done('budget_denied')
            if mismatch:
                return done('model_mismatch')
            if actual is None:
                return done('unknown_usage')
            status, value = _answer(reply, route, output)
            if status == 'model_mismatch':
                rec['deviations'].append('model_mismatch')
            return done(status, value)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, ValidationError):
            return done('invalid_output' if actual is not None else 'unknown_usage')
        except BudgetDenied:
            return done('budget_denied' if sent or not stopped() else 'cancelled')
        finally:
            if sent:
                try:
                    ledger()
                except BudgetDenied:
                    pass
                finally:  # settle even if the evidence write itself failed
                    try:
                        hold.commit(actual)  # None keeps the whole reservation as uncertain spend
                    except BudgetDenied:
                        pass  # the durable ledger keeps the bound or blocks the campaign on overrun
            else:
                try:
                    hold.release()
                    rec.update(billing='not_sent', reserved_usd=None)
                except BudgetDenied:
                    pass  # an expired unsent hold stays reserved until recovery

    def _summary(self, attempts, final):
        """Caller-facing usage: the identity of the last request actually sent (or the only
        attempt), plus money and token totals over every attempt that still holds spend."""
        s = self.settings
        held = [a for a in attempts if a['billing'] != 'not_sent']
        billing = ('not_sent' if not held else 'known' if all(a['billing'] == 'known' for a in held)
                   else 'unknown')
        known = [a['tokens'] for a in held if a['tokens']]
        tokens = ({k: sum(t[k] for t in known) for k in ('input_tokens', 'output_tokens', 'total_tokens')}
                  if known else None)
        used = any(a['dispatched'] for a in attempts[1:])
        refused = attempts[-1]['status'] if len(attempts) > 1 and not attempts[-1]['dispatched'] else None
        deviations = {d for a in attempts for d in a['deviations']} | ({'fallback_used'} if used else set())
        return copy.deepcopy(dict(
            self._context(), requested_model=final['requested_model'], requested_provider=final['requested_provider'],
            actual_model=final['actual_model'], serving_provider=final['serving_provider'],
            provider_verified=final['provider_verified'], response_id=final['response_id'],
            system_fingerprint=final['system_fingerprint'], pricing=final['pricing'], tokens=tokens,
            reserved_usd=str(sum((Decimal(a['reserved_usd']) for a in held), Decimal(0))) if held else None,
            actual_usd=str(sum((Decimal(a['actual_usd']) for a in held), Decimal(0))) if billing == 'known' else None,
            billing=billing, latency_ms=final['latency_ms'],
            fallback=dict(allowed=s.allow_fallback, used=used, triggers=[a['status'] for a in attempts[:-1]],
                          refused=refused),
            attempts=attempts, metadata_unavailable=sorted({g for a in attempts for g in a['metadata_unavailable']}),
            deviations=sorted(deviations)))
