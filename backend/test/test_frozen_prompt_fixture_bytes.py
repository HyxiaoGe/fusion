"""冻结 Prompt 夹具的跨平台字节契约。"""

from pathlib import Path

import pytest

from scripts.check_frozen_prompt_fixture_bytes import (
    EXPECTED_LEGACY_V2_SHA256,
    EXPECTED_P3A_SHA256,
    canonicalize_lf,
    load_frozen_legacy_v2_contract,
    load_frozen_p3a_probe_source,
    verify_frozen_prompt_fixture_bytes,
)

FIXTURE = Path(__file__).parent / "fixtures/prompt_bundle/p3a_sync.py"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LEGACY_V2_FIXTURE_ATTRIBUTE = (
    "backend/test/fixtures/prompt_bundle/legacy_v2_contract.json text eol=lf"
)


def test_lf_and_crlf_checkouts_have_the_same_canonical_bytes(tmp_path):
    canonical = load_frozen_p3a_probe_source(FIXTURE)
    windows_checkout = tmp_path / "p3a_sync.py"
    windows_checkout.write_bytes(canonical.replace(b"\n", b"\r\n"))

    assert canonicalize_lf(canonical) == canonical
    assert load_frozen_p3a_probe_source(windows_checkout) == canonical


@pytest.mark.parametrize("invalid_newline", [b"\r", b"\r\r\n"])
def test_non_crlf_carriage_returns_are_rejected(invalid_newline):
    with pytest.raises(ValueError, match="非法回车"):
        canonicalize_lf(b"before" + invalid_newline + b"after")


def test_modified_fixture_content_is_rejected(tmp_path):
    modified = tmp_path / "p3a_sync.py"
    modified.write_bytes(FIXTURE.read_bytes() + b"\n# changed\n")

    with pytest.raises(ValueError, match=EXPECTED_P3A_SHA256):
        load_frozen_p3a_probe_source(modified)


def test_legacy_contract_rejects_checkout_byte_conversion(tmp_path):
    fixture = Path(__file__).parent / "fixtures/prompt_bundle/legacy_v2_contract.json"
    converted = tmp_path / "legacy_v2_contract.json"
    converted.write_bytes(fixture.read_bytes().replace(b"\n", b"\r\n"))

    with pytest.raises(ValueError, match=EXPECTED_LEGACY_V2_SHA256):
        load_frozen_legacy_v2_contract(converted)


def test_real_fixture_and_checkout_independence_self_check_pass():
    assert verify_frozen_prompt_fixture_bytes() == EXPECTED_P3A_SHA256


def test_digest_sensitive_legacy_contract_is_forced_to_lf_on_every_checkout():
    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()

    assert LEGACY_V2_FIXTURE_ATTRIBUTE in attributes
