"""Technical source selection. Network enforcement belongs to SourceGate."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from app.source_capabilities import EndpointCapability, validate_capability_scope


DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / 'config/sources.json'
_CAPABILITY_RULES = TypeAdapter(tuple[EndpointCapability, ...])


def load_registry(registry) -> dict:
    """Read a registry document from the reviewed JSON file or from a registry object.

    A registry object has read_registry() -> dict and raises OSError when its store is
    unavailable. app.onboarding.ActivationRegistry uses this to add automatically
    validated capabilities from the shared database; every read is fresh, so revocation
    and suspension take effect on the next request.
    """
    reader = getattr(registry, 'read_registry', None)
    if reader is not None:
        return reader()
    return json.loads(Path(registry).read_text(encoding='utf-8'))


def get_active_source(source_id: str, registry_path=DEFAULT_REGISTRY) -> dict:
    """Read the current policy on every selection so revocation is not cached."""
    try:
        registry = load_registry(registry_path)
    except (OSError, ValueError) as exc:
        raise PermissionError('Source registry unavailable') from exc
    sources = registry.get('sources') if isinstance(registry, dict) else None
    if not isinstance(sources, list) or not all(isinstance(s, dict) for s in sources):
        raise PermissionError('Invalid source registry')
    matches = [s for s in sources if s.get('id') == source_id]
    if len(matches) != 1 or matches[0].get('status') != 'active':
        raise PermissionError('Source is not uniquely admitted and active')
    source = matches[0]
    admission = source.get('admission')
    if not isinstance(admission, dict) or not all(
        isinstance(admission.get(k), str) and admission[k].strip()
        for k in ('basis', 'reviewed_at', 'scope')
    ):
        raise PermissionError('Missing admission basis or scope')
    evidence = admission.get('evidence')
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(e, str) and e.strip() for e in evidence
    ):
        raise PermissionError('Missing admission evidence')
    endpoints = source.get('allowed_endpoints', [])
    rules = source.get('endpoint_capabilities', [])
    try:
        if not isinstance(endpoints, list) or not isinstance(rules, list) or not (endpoints or rules):
            raise ValueError('No admitted endpoints or capabilities')
        origin = urlsplit(source.get('origin', ''))
        if (origin.scheme != 'https' or not origin.hostname or origin.username is not None
                or origin.password is not None or origin.path not in ('', '/')
                or origin.query or origin.fragment):
            raise ValueError('Invalid origin')
        if rules:
            if origin.netloc != origin.hostname:
                raise ValueError('Capabilities require a canonical origin on port 443')
            validate_capability_scope(_CAPABILITY_RULES.validate_python(rules), origin.hostname)
        for endpoint in endpoints:
            parsed = urlsplit(endpoint)
            if parsed.scheme != 'https' or parsed.netloc != origin.netloc or parsed.fragment:
                raise ValueError('Endpoint outside admitted origin')
    except (TypeError, ValueError, AttributeError) as exc:
        raise PermissionError('Invalid admitted endpoint scope') from exc
    limits = source.get('limits')
    if not isinstance(limits, dict) or not all(
        type(limits.get(k)) is int and limits[k] > 0
        for k in ('requests_per_minute', 'concurrency', 'max_requests_per_run')
    ):
        raise PermissionError('Missing positive execution limits')
    return source
