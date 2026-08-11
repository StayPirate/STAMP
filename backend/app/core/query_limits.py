"""Cross-cutting query parameter length limit, shared by every endpoint.

See `docs/api-spec.md` (Query Parameter Length Limit) for the
authoritative contract: every declared string query parameter has an
individual maximum length of 500 characters; a value exceeding the
limit returns the standard `422 VALIDATION_ERROR` envelope. See
`docs/api-spec.md` (Undeclared Query Parameters) for the complementary
rule this dependency respects: a parameter name not declared by the
matched route is never inspected, regardless of its value's length.

See `docs/conventions.md` (FastAPI Conventions, "Cross-cutting query
parameter constraints") for why this is a single shared dependency
rather than a per-schema `Field(max_length=500)` repeated on every
string query field: a shared dependency, registered once at the app
level (`app.main`), applies automatically to every current and future
endpoint — eliminating the risk that a new endpoint forgets to declare
the constraint on one of its fields.
"""

from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin

from fastapi import Request
from fastapi.dependencies.models import Dependant
from fastapi.exceptions import RequestValidationError

# docs/api-spec.md (Query Parameter Length Limit).
_MAX_QUERY_STRING_LENGTH = 500


def _is_string_like(annotation: Any) -> bool:
    """Whether `annotation` denotes a string-shaped query field.

    Covers `str`, `str | None`, and `StrEnum` subclasses (which are
    `str` subclasses) — the only shapes the 500-character limit
    applies to per `docs/api-spec.md` ("Every **string** query
    parameter..."). Numeric, boolean, and UUID fields are exempt: a
    legitimate value for those types is never close to 500 characters,
    and an out-of-range value already fails its own type validation
    with a more specific, more useful error.
    """
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(
            _is_string_like(arg)
            for arg in get_args(annotation)
            if arg is not type(None)
        )
    return isinstance(annotation, type) and issubclass(annotation, str)


def _declared_string_query_field_names(dependant: Dependant) -> set[str]:
    """Every string-shaped query parameter name declared anywhere in
    `dependant`'s tree, including nested dependencies.

    A `Query()` field declared inside a sub-dependency (e.g. a shared
    query-model builder function used via `Depends()`) is listed on
    that sub-dependency's own `query_params`, not on the route's
    top-level `Dependant` — this recurses into `dependant.dependencies`
    to reach it. Mirrors the identical recursive-walk pattern in
    `tests/test_api_conventions.py` (`_iter_dependants()`).

    Uses `field.alias` (the wire name matched against the actual query
    string), not `field.name` (the Python parameter name) — the two
    differ whenever the endpoint declares an explicit `Query(alias=...)`
    (e.g. to avoid shadowing the `fastapi.status` module import with a
    parameter literally named `status`).
    """
    names = {
        field.alias
        for field in dependant.query_params
        if _is_string_like(field.field_info.annotation)
    }
    for sub_dependant in dependant.dependencies:
        names |= _declared_string_query_field_names(sub_dependant)
    return names


async def enforce_query_parameter_length_limit(request: Request) -> None:
    """Reject any declared string query parameter value over 500 characters.

    Q1: `request` is the current request. The matched route (and its
    declared query parameters, at any dependency nesting depth) is read
    from `request.scope["route"]`, populated by Starlette's router
    after route matching and before any dependency executes.

    Q3: for every string-shaped query parameter declared by the matched
    route, checks every raw occurrence of that name in the request's
    query string (`request.query_params.getlist(name)` — a repeated
    parameter is checked individually per occurrence). A route with no
    declared string query parameters, or a request that supplies none
    of them, is a no-op. A query parameter name the route does not
    declare is never inspected, regardless of its value's length, per
    `docs/api-spec.md` (Undeclared Query Parameters).

    Q6: raises `RequestValidationError` — rendered as the standard `422
    VALIDATION_ERROR` envelope by the handler registered in `app.main`
    — carrying one Pydantic-shaped error entry per over-limit
    occurrence, using the same `type`/`msg` pair Pydantic itself
    produces for a `max_length` violation. Otherwise infallible.
    """
    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return

    errors: list[dict[str, Any]] = []
    for name in _declared_string_query_field_names(dependant):
        for value in request.query_params.getlist(name):
            if len(value) > _MAX_QUERY_STRING_LENGTH:
                errors.append(
                    {
                        "loc": ["query", name],
                        "msg": (
                            "String should have at most "
                            f"{_MAX_QUERY_STRING_LENGTH} characters"
                        ),
                        "type": "string_too_long",
                    }
                )
    if errors:
        raise RequestValidationError(errors)
