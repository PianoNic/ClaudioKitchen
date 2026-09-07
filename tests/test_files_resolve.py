# SPDX-License-Identifier: Apache-2.0
import pytest

from src import config
from src.files import _resolve_generated


def test_resolve_generated_accepts_bare_filename():
    path = _resolve_generated("abc123.png")
    assert path.parent == config.FILES_DIR
    assert path.name == "abc123.png"


def test_resolve_generated_extracts_name_from_download_url():
    url = f"{config.BASE_URL}/files/abc123.png?token=secret"
    assert _resolve_generated(url).name == "abc123.png"


def test_resolve_generated_rejects_path_traversal():
    with pytest.raises(ValueError):
        _resolve_generated("../../etc/passwd")


def test_resolve_generated_rejects_embedded_separators():
    with pytest.raises(ValueError):
        _resolve_generated("sub/dir/file.png")
    with pytest.raises(ValueError):
        _resolve_generated("sub\\dir\\file.png")
