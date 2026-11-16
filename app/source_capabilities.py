"""Typed, read-only URL scopes for the technical source registry."""
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, model_validator


# Include query words because some servers route requests or override methods via query parameters.
ACTION_PATH_DENYLIST = frozenset({
    'cart', 'checkout', 'checkouts', 'my-account', 'account', 'login', 'register', 'wp-login.php',
    'wp-admin', 'xmlrpc.php', 'add-to-cart', 'add_to_cart', 'remove-item', 'remove_item',
    'update-cart', 'update_cart', 'logout', 'batch', 'contact-form-7', 'wc-ajax', 'wc-api', '_method',
})
MAX_CAPABILITY_URL_LENGTH = 2048
MAX_PARAMETER_LENGTH = 128
MAX_INTEGER = 10**20 - 1

ReferenceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, strict=True)]
LiteralSegment = Annotated[str, StringConstraints(
    strict=True, max_length=128, pattern=r'^[a-zA-Z0-9_-][a-zA-Z0-9._-]*$',
)]
QueryKey = Annotated[str, StringConstraints(strict=True, pattern=r'^[a-z][a-z0-9_-]{0,31}$')]
SafeHost = Annotated[str, StringConstraints(
    strict=True, max_length=253, pattern=r'^[a-z0-9-]+(\.[a-z0-9-]+)+$',
)]
Expect = Literal['json', 'html', 'xml']


def _has_action_words(target: str) -> bool:
    words = set(re.findall(r'[a-z0-9._-]+', target.lower()))
    # PHP reads dots in a query parameter name as underscores.
    return bool((words | {word.replace('.', '_') for word in words}) & ACTION_PATH_DENYLIST)


def _named_host(host: str) -> bool:
    labels = host.split('.')
    return (len(labels) > 1 and all(labels) and labels[-1] != 'localhost'
            and not re.fullmatch(r'\d+|0x[0-9a-f]*', labels[-1], re.IGNORECASE))


def safe_endpoint(url: str, host: str, *, query_spaces: bool = False) -> bool:
    """Require canonical HTTPS on a named host. A scoped query may encode only spaces."""
    try:
        parsed = httpx2.URL(url)
    except httpx2.InvalidURL:
        return False
    path, sep, query = parsed.raw_path.decode('ascii').partition('?')
    if query_spaces:
        query = query.replace('%20', ' ')
    target = path + sep + query
    return (str(parsed) == url and parsed.scheme == 'https' and parsed.host == host
            and parsed.port is None and not parsed.userinfo and not parsed.fragment
            and (query_spaces or '%' not in url) and '%' not in target and _named_host(host)
            and not _has_action_words(target))


class _ScopeModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class CapabilityRef(_ScopeModel):
    capability_id: ReferenceText
    version: ReferenceText


class TokenParameter(_ScopeModel):
    kind: Literal['token']
    max_length: Annotated[StrictInt, Field(ge=1, le=MAX_PARAMETER_LENGTH)]

    def matches(self, value: str) -> bool:
        return (1 <= len(value) <= self.max_length
                and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', value) is not None)


class IntegerParameter(_ScopeModel):
    kind: Literal['integer']
    minimum: Annotated[StrictInt, Field(ge=0, le=MAX_INTEGER)]
    maximum: Annotated[StrictInt, Field(ge=0, le=MAX_INTEGER)]

    @model_validator(mode='after')
    def ordered_range(self):
        if self.minimum > self.maximum:
            raise ValueError('Parameter minimum must be at most its maximum')
        return self

    def matches(self, value: str) -> bool:
        return (re.fullmatch(r'0|[1-9][0-9]{0,19}', value) is not None
                and self.minimum <= int(value) <= self.maximum)


class SearchParameter(_ScopeModel):
    kind: Literal['search_text']
    max_length: Annotated[StrictInt, Field(ge=1, le=MAX_PARAMETER_LENGTH)]

    def matches(self, raw: str) -> bool:
        value = raw.replace('%20', ' ').replace('+', ' ')
        return (1 <= len(value) <= self.max_length
                and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*( [a-zA-Z0-9][a-zA-Z0-9._-]*)*',
                                 value) is not None)


class ChoiceParameter(_ScopeModel):
    kind: Literal['choice']
    values: Annotated[tuple[LiteralSegment, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode='after')
    def distinct_safe_choices(self):
        if any(_has_action_words(value) for value in self.values) or len(set(self.values)) != len(self.values):
            raise ValueError('Parameter choices must be distinct and read-only')
        return self

    def matches(self, value: str) -> bool:
        return value in self.values


PathParameter = Annotated[TokenParameter | IntegerParameter | ChoiceParameter, Field(discriminator='kind')]
QueryParameter = Annotated[
    TokenParameter | IntegerParameter | ChoiceParameter | SearchParameter, Field(discriminator='kind'),
]


class QueryArgument(_ScopeModel):
    name: QueryKey
    parameter: QueryParameter
    required: StrictBool = True


class EndpointCapability(CapabilityRef):
    """One operation on one host and exact path shape with declared parameters."""
    host: SafeHost
    operation: Literal['search', 'product', 'variant', 'catalog', 'feed', 'sitemap']
    expect: Expect
    path: Annotated[tuple[LiteralSegment | PathParameter, ...], Field(max_length=12)]
    trailing_slash: StrictBool = False
    query: Annotated[tuple[QueryArgument, ...], Field(max_length=12)] = ()

    @model_validator(mode='after')
    def safe_scope(self):
        if not _named_host(self.host):
            raise ValueError('Capabilities need a named host')
        if self.path and not isinstance(self.path[0], str):
            raise ValueError('Path capabilities must begin with a literal segment')
        if any(_has_action_words(part) for part in self.path if isinstance(part, str)):
            raise ValueError('Action paths cannot be configured')
        names = [argument.name for argument in self.query]
        if len(names) != len(set(names)) or any(_has_action_words(name) for name in names):
            raise ValueError('Query names must be distinct and read-only')
        return self

    def matches(self, url: str) -> bool:
        if (len(url) > MAX_CAPABILITY_URL_LENGTH or not url.isascii() or '#' in url
                or not safe_endpoint(url, self.host, query_spaces=True)):
            return False
        parsed = urlsplit(url)
        if (parsed.netloc != self.host or not parsed.path.startswith('/')
                or ('?' in url and not parsed.query)
                or (parsed.path != '/' and parsed.path.endswith('/') != self.trailing_slash)):
            return False
        path_segments = parsed.path[1:-1] if self.trailing_slash else parsed.path[1:]
        parts = path_segments.split('/') if parsed.path != '/' else []
        if len(parts) != len(self.path) or not all(
            part == rule if isinstance(rule, str) else rule.matches(part)
            for rule, part in zip(self.path, parts)
        ):
            return False

        arguments = {argument.name: argument for argument in self.query}
        seen = set()
        for field in parsed.query.split('&') if parsed.query else ():
            name, separator, value = field.partition('=')
            argument = arguments.get(name)
            if not (separator and argument and name not in seen and argument.parameter.matches(value)):
                return False
            seen.add(name)
        return all(not argument.required or argument.name in seen for argument in self.query)


def validate_capability_scope(capabilities: tuple[EndpointCapability, ...], host: str) -> None:
    ids = set()
    for rule in capabilities:
        if rule.host != host or rule.capability_id in ids:
            raise ValueError('Capability IDs must be unique per source and belong to its configured host')
        ids.add(rule.capability_id)
