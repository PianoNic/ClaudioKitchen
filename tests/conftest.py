# SPDX-License-Identifier: Apache-2.0
"""Fake env vars so `src.config` (read at import time) doesn't KeyError in tests."""

import os
import tempfile

os.environ.setdefault("OIDC_CONFIG_URL",
                      "https://auth.example.com/.well-known/openid-configuration")
os.environ.setdefault("OIDC_CLIENT_ID", "test-client")
os.environ.setdefault("OIDC_CLIENT_SECRET", "test-secret")
os.environ.setdefault("BASE_URL", "https://mcp.example.com")
os.environ.setdefault("ALLOWED_EMAILS", "test@example.com")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("FILES_DIR", tempfile.mkdtemp(prefix="claudiokitchen-tests-"))
