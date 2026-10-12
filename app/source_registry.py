"""Source selection boundary. Network enforcement belongs to MVP 04."""
import json
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / 'config/sources.json'


def get_active_source(source_id: str, registry_path: Path | str = DEFAULT_REGISTRY) -> dict:
    """Read the current policy on every selection so revocation is not cached."""
    try:
        registry = json.loads(Path(registry_path).read_text(encoding='utf-8'))
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
    endpoints = source.get('allowed_endpoints')
    if not isinstance(endpoints, list) or not endpoints:
        raise PermissionError('No admitted endpoints')
    try:
        origin = urlsplit(source.get('origin', ''))
        if (origin.scheme != 'https' or not origin.hostname or origin.username is not None
                or origin.password is not None or origin.path not in ('', '/')
                or origin.query or origin.fragment):
            raise ValueError('Invalid origin')
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
