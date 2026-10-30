# SPDX-License-Identifier: Apache-2.0
"""The required-claim gate, without standing up OIDCProxy."""

from src.auth_claim import claim_grants


def test_truthy_claim_values_grant_access():
    assert claim_grants({"allowed": True}, "allowed")
    assert claim_grants({"allowed": "true"}, "allowed")
    assert claim_grants({"allowed": "yes"}, "allowed")
    assert claim_grants({"allowed": "kitchen"}, "allowed")
    assert claim_grants({"allowed": 1}, "allowed")
    assert claim_grants({"userinfo": {"allowed": True}}, "allowed")
    assert claim_grants({"upstream_claims": {"allowed": "1"}}, "allowed")


def test_missing_or_negative_claim_denies():
    assert not claim_grants({}, "allowed")
    assert not claim_grants({"allowed": False}, "allowed")
    assert not claim_grants({"allowed": "false"}, "allowed")
    assert not claim_grants({"allowed": ""}, "allowed")
    assert not claim_grants({"allowed": 0}, "allowed")
    assert not claim_grants({"allowed": None}, "allowed")
    assert not claim_grants({"Allowed": True}, "allowed")
    assert not claim_grants({"email": "you@example.com"}, "allowed")
