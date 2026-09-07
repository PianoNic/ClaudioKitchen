# SPDX-License-Identifier: Apache-2.0
"""Only literal IPs here: no DNS lookups, so these stay fast and offline-safe."""

from src.files import _host_is_public


def test_loopback_is_not_public():
    assert _host_is_public("127.0.0.1") is False


def test_link_local_metadata_ip_is_not_public():
    assert _host_is_public("169.254.169.254") is False


def test_private_range_is_not_public():
    assert _host_is_public("10.0.0.5") is False
    assert _host_is_public("192.168.1.1") is False


def test_public_ip_is_public():
    assert _host_is_public("8.8.8.8") is True
