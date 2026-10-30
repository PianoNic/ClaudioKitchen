# SPDX-License-Identifier: Apache-2.0
"""OIDC auth: proxy an MCP client's OAuth login to any OIDC provider.

The client sends ``resource`` set to this server's MCP URL. Pocket ID (and most other
providers) answer ``invalid_request`` unless that URL is a registered API, so the
resource is not forwarded upstream. A completed login is enough to call tools.
Set ``REQUIRED_CLAIM`` to also require one truthy claim on the id token or
userinfo document. Pocket ID emits custom claims only when ``profile`` is requested.
"""

import httpx
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.dependencies import get_access_token

from src import config
from src.auth_claim import claim_grants

auth = OIDCProxy(
    config_url=config.OIDC_CONFIG_URL,
    client_id=config.OIDC_CLIENT_ID,
    client_secret=config.OIDC_CLIENT_SECRET,
    base_url=config.BASE_URL,
    # id_token carries profile claims (Pocket ID custom claims live there, not
    # on the access token). Audience checked is the OIDC client id.
    verify_id_token=True,
    forward_resource=config.OIDC_FORWARD_RESOURCE,
    extra_authorize_params={"scope": config.OIDC_SCOPES},
)

_userinfo_endpoint_cache: dict[str, str] = {}
_claim_cache: dict[str, bool] = {}  # sub -> whether REQUIRED_CLAIM granted access


async def _userinfo_endpoint(issuer: str, client: httpx.AsyncClient) -> str:
    """Resolve the userinfo endpoint from the issuer's discovery doc (cached)."""
    if issuer in _userinfo_endpoint_cache:
        return _userinfo_endpoint_cache[issuer]
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    r = await client.get(url)
    r.raise_for_status()
    endpoint = r.json().get("userinfo_endpoint", "")
    if endpoint:
        _userinfo_endpoint_cache[issuer] = endpoint
    return endpoint


async def _userinfo_claims(claims: dict, token) -> dict:
    """Load userinfo when the verified token itself has no allowing claim.

    Some providers only attach custom claims to userinfo. The token string after
    verification is the upstream access token, which userinfo accepts when the
    login was granted the openid scope.
    """
    issuer = claims.get("iss") or config.OIDC_CONFIG_URL.split("/.well-known")[0]
    raw_token = getattr(token, "token", None)
    if not issuer or not raw_token:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            endpoint = await _userinfo_endpoint(issuer, client)
            if not endpoint:
                return {}
            response = await client.get(
                endpoint,
                headers={"Authorization": f"Bearer {raw_token}"},
            )
            response.raise_for_status()
            body = response.json()
            return body if isinstance(body, dict) else {}
    except Exception as exc:
        print(f"[auth] userinfo lookup failed: {exc}", flush=True)
        return {}


async def _check_user():
    """Allow the caller. With REQUIRED_CLAIM set, that claim must grant access."""
    name = config.REQUIRED_CLAIM
    if not name:
        return

    token = get_access_token()
    claims = getattr(token, "claims", {}) or {}
    sub = str(claims.get("sub") or "")

    if claim_grants(claims, name) or (sub and _claim_cache.get(sub) is True):
        if sub:
            _claim_cache[sub] = True
        return

    if claim_grants(await _userinfo_claims(claims, token), name):
        if sub:
            _claim_cache[sub] = True
        return

    who = sub or "unknown"
    print(f"[auth] DENIED sub={who!r} claim {name!r} not granted", flush=True)
    raise PermissionError(
        f"User '{who}' is not allowed to use this server. "
        f"The '{name}' claim must be set on the account."
    )
