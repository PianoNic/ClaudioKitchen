# SPDX-License-Identifier: Apache-2.0
from src.files import _clean_ext, _decode_b64, _sniff_ext, _sniff_image_ext


def test_clean_ext_strips_non_alnum_and_truncates():
    assert _clean_ext("report.PDF") == "pdf"
    assert _clean_ext("archive.tar.gz") == "gz"
    assert _clean_ext("noext") == ""
    assert _clean_ext("weird.a!b@c#") == "abc"


def test_sniff_ext_prefers_filename_extension_over_magic_bytes():
    # PNG magic bytes, but the caller's filename says .jpg -> respect the filename.
    assert _sniff_ext(b"\x89PNG\r\n\x1a\n...", name="photo.jpg") == "jpg"


def test_sniff_ext_falls_back_to_magic_bytes():
    assert _sniff_ext(b"%PDF-1.4 ...") == "pdf"
    assert _sniff_ext(b"\xff\xd8\xff\xe0") == "jpg"
    assert _sniff_ext(b"RIFF....WAVEfmt ") == "wav"
    assert _sniff_ext(b"PK\x03\x04...") == "zip"


def test_sniff_ext_falls_back_to_content_type_then_bin():
    assert _sniff_ext(b"random bytes", content_type="audio/mpeg") == "mp3"
    assert _sniff_ext(b"random bytes") == "bin"


def test_sniff_image_ext_detects_known_formats():
    assert _sniff_image_ext(b"\xff\xd8\xff\xe0") == "jpg"
    assert _sniff_image_ext(b"\x89PNG\r\n\x1a\n") == "png"
    assert _sniff_image_ext(b"not an image", fallback="webp") == "webp"


def test_decode_b64_strips_data_url_prefix_and_fixes_padding():
    assert _decode_b64("data:image/png;base64,aGVsbG8") == b"hello"
    assert _decode_b64("aGVsbG8") == b"hello"  # missing '=' padding
