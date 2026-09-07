# Security

## File download token

The `/files/` route requires `?token=<FILES_TOKEN>`. Set `FILES_TOKEN` in `.env` (a
stable secret) so download links keep working across restarts. If it's unset, a random
one is generated per run and logged. Returned download URLs already include the token.

> **Known limitation:** `FILES_TOKEN` is a single global secret carried in the URL query
> string. Don't pass `/files` URLs to third-party APIs, and configure your reverse
> proxy to strip query strings from access logs. Rotating it invalidates all existing
> download links.

## Upload tickets

`create_upload_url` issues a short-lived, single-purpose ticket (`src/files.py`,
`_upload_tickets`) instead of the static token, so a sandboxed agent can PUT a file
without ever holding `FILES_TOKEN`. Tickets are time-limited (`expires_in`, default
900 s, max 24 h) and use-limited (`max_uses`, default 5, max 100).

## Stored-file hardening

Downloads are served with `X-Content-Type-Options: nosniff` and
`Content-Disposition: attachment` (`src/routes.py`), so a stored `.svg` or `.html`
can't execute script in the server's origin (the origin that also carries the OIDC
session).

## Outbound fetch guard (SSRF)

Outbound fetches (`upload_file` from a URL, `edit_image`, `describe_image`,
`transcribe_audio`) go through `_safe_fetch` in `src/files.py`, which:

- only allows `http(s)` URLs (no `file://`, no other schemes),
- resolves the hostname and rejects it unless **every** resolved IP is a routable
  public address: loopback, link-local (including the `169.254.169.254` cloud
  metadata address), and RFC-1918/ULA private ranges are all blocked,
- disables redirects, so a public host can't 302 you into a private one,
- enforces a hard size cap (`FETCH_MAX_BYTES`, default 100 MB).

Files already hosted on this server's own `/files` store skip the host check (they're
trusted), so uploaded files still work when `BASE_URL` is a private/tunnel host.

Inbound uploads are capped separately by `UPLOAD_MAX_BYTES`.

## Auth

Every tool call runs `_check_user()` (`src/auth.py`) first, which resolves the caller's
email from the OIDC access token (falling back to a userinfo lookup, cached per
subject) and rejects anything not in `ALLOWED_EMAILS`. See
[configuration.md](configuration.md#setting-up-the-oidc-provider-pocket-id-example)
for how the OIDC proxy is wired up.
