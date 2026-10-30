# SPDX-License-Identifier: Apache-2.0
"""Whether an OIDC claims dict grants access via one named claim."""

# JSON false/0 often arrives as a string. Those do not grant access.
_DENY_STRINGS = {"", "false", "0", "no", "null", "none"}


def _value_allows(value) -> bool:
    """True when a claim value should let the caller in."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _DENY_STRINGS
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return bool(value)


def _iter_claim_values(claims: dict, name: str):
    """Yield values of ``name`` on the claims dict and one level of nesting.

    Providers put the claim on the token, under ``userinfo``, or (FastMCP) under
    ``upstream_claims``. Any allowing copy is enough.
    """
    if not isinstance(claims, dict) or not name:
        return
    if name in claims:
        yield claims[name]
    for key in ("userinfo", "upstream_claims"):
        nested = claims.get(key)
        if isinstance(nested, dict) and name in nested:
            yield nested[name]


def claim_grants(claims: dict, name: str) -> bool:
    """Whether ``claims`` contains an allowing value for ``name``."""
    return any(_value_allows(value) for value in _iter_claim_values(claims, name))
