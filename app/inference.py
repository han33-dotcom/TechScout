"""Runtime model inference for the buyer worker (#75, T39): one pinned OpenRouter route.

Every model-driven buyer role (orchestrator planning, research ordering, evidence critique
and the writer) gets an OpenRouterProvider built from this one configuration. It lives
outside git (``var/inference.json`` by default, or the path in TECHSCOUT_INFERENCE_CONFIG)
and is separate from the evaluation configuration. The evaluation factory builds the same
provider from the pinned route in config/evaluation.json.

Nothing here constructs the direct OpenAI provider or reads OPENAI_API_KEY, and the route
cannot name a fallback. A missing configuration, key or price fails explicitly with
InferenceUnavailable before anything is reserved or sent. The key is read from the backend
environment only and stays inside the provider transport closure; traces built here keep
identifiers, counts, timings and money, never prompts, answers or credentials.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import threading
import time
from typing import Literal

from pydantic import Field, StrictBool, ValidationError, model_validator

from app.contracts import Model
from app.openrouter import ENDPOINTS, InputTokenLimit, OpenRouterConfig, OpenRouterProvider, ProviderUnavailable, Route, _bound
from app.writer import PROMPTS as WRITER_PROMPTS, WRITER_MAX_INPUT_TOKENS, WRITER_PROMPT_VERSION

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / 'var' / 'inference.json'
PATH_VARIABLE = 'TECHSCOUT_INFERENCE_CONFIG'
KEY_VARIABLE = 'OPENROUTER_API_KEY'
ROLE_PROMPT_VERSION = 'roles-v1'
# app.accounting keeps the last USD 0.25 of a buyer run's USD 1 for the final phase
# (verification and writer); everything before it shares USD 0.75.
FINAL_RESERVE_USD = Decimal('0.25')
RESEARCH_SPEND_USD = Decimal('0.75')
Role = Literal['roles', 'writer']


class InferenceUnavailable(ValueError):
    """Inference is not configured for the buyer worker; nothing was reserved or sent."""


class RoleLimits(Model):
    """Token allowances and the per-call wait for one group of roles."""
    max_input_tokens: InputTokenLimit
    max_output_tokens: int = Field(strict=True, ge=16, le=16000)
    timeout_seconds: float = Field(default=30, gt=0, le=30)


class InferenceConfig(Model):
    """The buyer worker's single pinned route. Unknown keys (an API key, a fallback list,
    a second route) are rejected, so neither a credential nor a fallback can be configured here."""
    provider: Literal['openrouter'] = 'openrouter'
    route: Route
    pricing_checked_on: date
    endpoint: Literal[ENDPOINTS] = ENDPOINTS[0]
    data_collection: Literal['allow', 'deny'] = 'deny'
    zdr: StrictBool = False
    effort: Literal['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] | None = None
    # Orchestrator, research workers and evidence critique share these limits and the
    # roles-v1 prompts; the writer keeps its own prompt version and token allowance.
    roles: RoleLimits = RoleLimits(max_input_tokens=16384, max_output_tokens=2048)
    writer: RoleLimits = RoleLimits(max_input_tokens=WRITER_MAX_INPUT_TOKENS, max_output_tokens=4096)

    @model_validator(mode='after')
    def _fits_the_buyer_ledger(self):
        # The adapter's own rules (positive verified rates, an explicit model, no router or
        # variant IDs) apply now, when the file is loaded, not at the first paid call.
        self.provider_config('roles'), self.provider_config('writer')
        roles, writer = self.reservation('roles'), self.reservation('writer')
        if roles > RESEARCH_SPEND_USD:
            raise ValueError(f'One role call would reserve USD {roles}, above the USD {RESEARCH_SPEND_USD} '
                             'research allowance of a buyer run')
        if writer > FINAL_RESERVE_USD:
            raise ValueError(f'One writer call would reserve USD {writer}, above the USD {FINAL_RESERVE_USD} '
                             'final-phase reserve; lower writer.max_input_tokens or max_output_tokens')
        return self

    def provider_config(self, role: Role, *, timeout_seconds: float | None = None) -> OpenRouterConfig:
        limits = self.roles if role == 'roles' else self.writer
        return OpenRouterConfig(
            enabled=True, purpose='buyer', endpoint=self.endpoint, routes=(self.route,),
            data_collection=self.data_collection, zdr=self.zdr, effort=self.effort,
            max_input_tokens=limits.max_input_tokens, max_output_tokens=limits.max_output_tokens,
            timeout_seconds=limits.timeout_seconds if timeout_seconds is None else timeout_seconds,
            prompt_version=ROLE_PROMPT_VERSION if role == 'roles' else WRITER_PROMPT_VERSION,
            pricing_checked_on=self.pricing_checked_on)

    def reservation(self, role: Role) -> Decimal:
        """What one call of this role reserves before it is sent (USD)."""
        limits = self.roles if role == 'roles' else self.writer
        return _bound(self.route.rates, limits)


def _problems(exc: ValidationError) -> str:
    """Field locations and messages only; never the submitted values."""
    parts = []
    for error in exc.errors()[:5]:
        where = '.'.join(str(p) for p in error.get('loc', ())) or 'inference'
        parts.append(f"{where}: {error.get('msg', 'invalid')}")
    return '; '.join(parts)


def config_path(environ: Mapping[str, str] = os.environ) -> Path:
    value = (environ.get(PATH_VARIABLE) or '').strip()
    return Path(value) if value else DEFAULT_PATH


def load_inference_config(path=None, *, environ: Mapping[str, str] = os.environ) -> InferenceConfig:
    """The validated runtime route, or InferenceUnavailable naming what is missing."""
    path = Path(path) if path is not None else config_path(environ)
    if not path.is_file():
        raise InferenceUnavailable('No runtime inference configuration (var/inference.json or the file in '
                                   f'{PATH_VARIABLE}); copy config/inference.example.json and fill the confirmed route')
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeDecodeError, ValueError):
        raise InferenceUnavailable('The runtime inference configuration is not readable JSON') from None
    if not isinstance(raw, dict) or set(raw) != {'inference'} or not isinstance(raw['inference'], dict):
        raise InferenceUnavailable('The runtime inference configuration needs exactly one "inference" section')
    try:
        return InferenceConfig.model_validate(raw['inference'])
    except ValidationError as exc:
        raise InferenceUnavailable('Invalid runtime inference configuration: ' + _problems(exc)) from None
    except ValueError as exc:  # validators that raise outside pydantic's wrapping
        raise InferenceUnavailable(f'Invalid runtime inference configuration: {exc}') from None


@dataclass(frozen=True)
class BuyerInference:
    """The roles' provider plus a factory for the writer's (its timeout must fit its lease)."""
    config: InferenceConfig
    roles: object
    _make: Callable = field(repr=False)

    def writer(self, seconds: float):
        """A writer provider whose wait fits inside a ledger lease of ``seconds``."""
        limit = min(self.config.writer.timeout_seconds, seconds)
        if not limit > 0:
            raise InferenceUnavailable('No time is left for the writer call')
        return self._make(self.config.provider_config('writer', timeout_seconds=limit))

    def versions(self) -> dict:
        """Settings recorded with every run: model, serving provider, prompts, prices and bounds."""
        writer = self._make(self.config.provider_config('writer'))
        return dict(provider='openrouter', purpose='buyer', model=self.config.route.model,
                    serving_provider=self.config.route.provider, endpoint=self.config.endpoint,
                    pricing_checked_on=self.config.pricing_checked_on.isoformat(),
                    rates=self.config.route.rates.model_dump(mode='json'),
                    data_collection=self.config.data_collection, zdr=self.config.zdr,
                    fallback='disabled',
                    roles=dict(prompt_version=ROLE_PROMPT_VERSION, route_fingerprint=self.roles.fingerprint,
                               reservation_usd=str(self.config.reservation('roles')),
                               **self.config.roles.model_dump(mode='json')),
                    writer=dict(prompt_version=WRITER_PROMPT_VERSION, route_fingerprint=writer.fingerprint,
                                reservation_usd=str(self.config.reservation('writer')),
                                **self.config.writer.model_dump(mode='json')))


def build_inference(config: InferenceConfig, *, environ: Mapping[str, str] = os.environ,
                    api_key: str | None = None, transport=None) -> BuyerInference:
    """OpenRouter providers for every model-driven role. Requires OPENROUTER_API_KEY in the
    backend environment (an OpenAI key is never accepted in its place)."""
    if WRITER_PROMPT_VERSION not in WRITER_PROMPTS:
        raise InferenceUnavailable('The writer prompt version has no prompt')
    key = api_key if api_key is not None else environ.get(KEY_VARIABLE)
    if not isinstance(key, str) or not key.strip():
        raise InferenceUnavailable(f'OpenRouter inference requires a backend {KEY_VARIABLE}')

    def make(settings: OpenRouterConfig) -> OpenRouterProvider:
        try:
            return OpenRouterProvider(settings, api_key=key, transport=transport)
        except ProviderUnavailable as exc:
            raise InferenceUnavailable(str(exc)) from None
    roles = make(config.provider_config('roles'))
    make(config.provider_config('writer'))  # fail now, not at the end of a run
    return BuyerInference(config, roles, make)


def load_env_file(path=None, *, names=(KEY_VARIABLE,), environ=None) -> tuple[str, ...]:
    """Copy the named variables from the repository's .env into this backend process.

    Only for a worker started on the host; Compose passes its own environment. A variable
    already set is never replaced. Returns the names it set, never their values.
    """
    environ = os.environ if environ is None else environ
    path = Path(path) if path is not None else ROOT / '.env'
    try:
        text = path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeDecodeError):
        return ()
    loaded = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        name = name.strip()
        if name.startswith('export '):
            name = name[len('export '):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
            value = value[1:-1]
        if name in names and value and not (environ.get(name) or '').strip():
            environ[name] = value
            loaded.append(name)
    return tuple(loaded)


# Run trace: what each model call reports, without prompts, answers or credentials.

_ROLE_BY_SCHEMA = {'DomesticSearchChoice': 'orchestrator', 'Proposal': 'orchestrator', 'Selection': 'orchestrator', 'CritiqueChoice': 'evaluator',
                   'Choice': 'researcher', '_Selection': 'writer'}
_USAGE_KEYS = ('provider', 'purpose', 'route_fingerprint', 'endpoint', 'prompt_version', 'pricing_checked_on',
               'requested_model', 'actual_model', 'requested_provider', 'serving_provider', 'provider_verified',
               'response_id', 'system_fingerprint', 'tokens', 'reserved_usd', 'actual_usd', 'billing',
               'latency_ms', 'fallback', 'metadata_unavailable', 'deviations', 'routing')
_ATTEMPT_KEYS = ('index', 'status', 'billing', 'dispatched', 'reserved_usd', 'actual_usd', 'rate_estimate_usd',
                 'latency_ms', 'error_code', 'tokens', 'serving_provider', 'actual_model', 'payload_bytes')


def _plain(value, depth=0):
    """JSON-safe copy of plain usage values; anything else becomes its type name."""
    if depth > 6:
        return None
    if value is None or isinstance(value, (bool, int, str)):
        return value[:300] if isinstance(value, str) else value
    if isinstance(value, (float, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(k)[:80]: _plain(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_plain(v, depth + 1) for v in list(value)[:20]]
    return type(value).__name__


def sanitize_usage(usage) -> dict:
    """The provider's usage record reduced to its documented identifiers, counts and money.

    Missing metadata stays listed (``metadata_unavailable``) and uncertain billing stays
    explicit (``spend``: known, uncertain or none), so neither is ever reported as zero."""
    usage = usage if isinstance(usage, dict) else {}
    kept = {key: _plain(usage.get(key)) for key in _USAGE_KEYS}
    attempts = usage.get('attempts')
    if isinstance(attempts, list):
        kept['attempts'] = [{k: _plain(a.get(k)) for k in _ATTEMPT_KEYS} for a in attempts[:5]
                            if isinstance(a, dict)]
    billing = usage.get('billing')
    kept['spend'] = ('known' if billing == 'known' else 'none' if billing in (None, 'not_sent')
                     else 'uncertain')
    if billing == 'unknown':
        kept['spend_note'] = 'cost not reported; the full reservation is kept as uncertain spend'
    return kept


class CallLog:
    """Thread-safe list of model calls in one run (research workers call from worker threads)."""
    def __init__(self):
        self._lock, self._calls = threading.Lock(), []

    def add(self, entry: dict) -> None:
        with self._lock:
            self._calls.append(dict(entry, sequence=len(self._calls) + 1))

    def entries(self) -> list[dict]:
        with self._lock:
            return [dict(c) for c in self._calls]


class TracedProvider:
    """Passes calls through unchanged and records each one's sanitized usage."""
    def __init__(self, provider, log: CallLog, *, role: str | None = None):
        self.provider, self.log, self.role = provider, log, role
        self.settings = provider.settings

    def generate(self, instructions, data, output, *, budget, cancelled=None):
        started = time.monotonic()
        role = self.role or _ROLE_BY_SCHEMA.get(getattr(output, '__name__', ''), 'model')
        entry = dict(role=role, schema=getattr(output, '__name__', 'output'),
                     prompt_version=self.settings.prompt_version)
        try:
            result = self.provider.generate(instructions, data, output, budget=budget, cancelled=cancelled)
        except Exception as exc:
            self.log.add(dict(entry, status='provider_error', error=type(exc).__name__, validated=False,
                              wall_ms=round((time.monotonic() - started) * 1000), usage=sanitize_usage(None)))
            raise
        self.log.add(dict(entry, status=result.status,
                          # completed means the answer passed the role's local Pydantic schema
                          validated=result.status == 'completed' and isinstance(result.value, output),
                          wall_ms=round((time.monotonic() - started) * 1000),
                          usage=sanitize_usage(result.usage)))
        return result
