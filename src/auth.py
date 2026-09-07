# SPDX-License-Identifier: Apache-2.0
"""OIDC auth: OIDCProxy wiring + the per-call email allowlist check."""

import httpx
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.dependencies import get_access_token

from src import config

auth = OIDCProxy(
    config_url=config.OIDC_CONFIG_URL,
    client_id=config.OIDC_CLIENT_ID,
    client_secret=config.OIDC_CLIENT_SECRET,
    base_url=config.BASE_URL,
    required_scopes=[],
    extra_authorize_params={"scope": "openid email profile"},
)

# Caches to avoid repeated network calls
_email_cache: dict[str, str] = {}        # sub -> email
_userinfo_endpoint_cache: dict[str, str] = {}  # issuer -> userinfo endpoint


def _extract_email_from_claims(claims: dict) -> str:
    return (
        claims.get("email")
        or (claims.get("userinfo") or {}).get("email")
        or ""
    ).lower()


async def _userinfo_endpoint(issuer: str, client: httpx.AsyncClient) -> str:
    """Resolve the userinfo endpoint from the issuer's OIDC discovery doc (cached)."""
    if issuer in _userinfo_endpoint_cache:
        return _userinfo_endpoint_cache[issuer]
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    r = await client.get(url)
    r.raise_for_status()
    endpoint = r.json().get("userinfo_endpoint", "")
    if endpoint:
        _userinfo_endpoint_cache[issuer] = endpoint
    return endpoint


async def _check_user():
    """Allow only whitelisted emails (your Pocket ID account).

    The access token Pocket ID issues carries no email claim, so we resolve it via
    the provider's userinfo endpoint (discovered from the token issuer) and cache it.
    """
    token = get_access_token()
    claims = getattr(token, "claims", {}) or {}

    email = _extract_email_from_claims(claims)
    sub = claims.get("sub") or ""

    if not email and sub in _email_cache:
        email = _email_cache[sub]

    # Resolve via userinfo using the raw access token Claude presented
    if not email:
        issuer = claims.get("iss") or config.OIDC_CONFIG_URL.split("/.well-known")[0]
        raw_token = getattr(token, "token", None)
        if issuer and raw_token:
            try:
                async with httpx.AsyncClient(timeout=15) as c:
                    endpoint = await _userinfo_endpoint(issuer, c)
                    if endpoint:
                        r = await c.get(
                            endpoint,
                            headers={"Authorization": f"Bearer {raw_token}"},
                        )
                        r.raise_for_status()
                        email = (r.json().get("email") or "").lower()
                        if sub and email:
                            _email_cache[sub] = email
            except Exception as e:
                print(f"[auth] userinfo lookup failed: {e}", flush=True)

    if email not in config.ALLOWED_EMAILS:
        print(f"[auth] DENIED user '{email or 'unknown'}' (claims={claims})", flush=True)
        raise PermissionError(
            f"User '{email or 'unknown'}' is not allowed to use this server."
        )
