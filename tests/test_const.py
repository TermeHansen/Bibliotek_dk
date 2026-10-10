"""Regression guards for module-level constants."""
from __future__ import annotations

from custom_components.bibliotek_dk.const import (
    HEADERS,
    JSON_HEADERS,
    MUNICIPALITY_LOOKUP_URL,
)


def test_municipality_lookup_uses_mirror_not_retired_dawa():
    # api.dataforsyningen.dk (DAWA) was retired 2026-10-01 and returns 410.
    assert "dawa.companydata.dk" in MUNICIPALITY_LOOKUP_URL
    assert "dataforsyningen.dk" not in MUNICIPALITY_LOOKUP_URL
    assert "LON" in MUNICIPALITY_LOOKUP_URL and "LAT" in MUNICIPALITY_LOOKUP_URL


def test_headers_do_not_advertise_undecodable_encodings():
    # requests cannot decode br/zstd without optional packages; advertising them
    # makes servers return bytes we cannot parse.
    for headers in (HEADERS, JSON_HEADERS):
        encoding = headers["Accept-Encoding"]
        assert "br" not in encoding
        assert "zstd" not in encoding
