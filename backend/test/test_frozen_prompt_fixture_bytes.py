"""冻结 Prompt 夹具的跨平台字节契约。"""

import hashlib
import subprocess
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
BACKEND_ROOT = Path(__file__).resolve().parents[1]
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


def test_fresh_index_export_repairs_stale_crlf_across_branch_transition(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    fixture = repository / "backend/test/fixtures/prompt_bundle/legacy_v2_contract.json"
    fixture.parent.mkdir(parents=True)
    canonical = b'{"prompt":"line one\nline two"}\n'
    fixture.write_bytes(canonical)
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Fusion CI"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fusion-ci@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "legacy"], cwd=repository, check=True)
    subprocess.run(["git", "switch", "-q", "-c", "hotfix"], cwd=repository, check=True)
    (repository / ".gitattributes").write_text(
        LEGACY_V2_FIXTURE_ATTRIBUTE + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".gitattributes"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "force lf"], cwd=repository, check=True)
    subprocess.run(["git", "switch", "-q", "master"], cwd=repository, check=True)
    fixture.unlink()
    subprocess.run(
        ["git", "checkout-index", "--force", "--", str(fixture.relative_to(repository))],
        cwd=repository,
        check=True,
    )
    stale = canonical.replace(b"\n", b"\r\n")
    assert fixture.read_bytes() == stale
    subprocess.run(["git", "switch", "-q", "hotfix"], cwd=repository, check=True)
    assert fixture.read_bytes() == stale

    export_root = repository / ".fusion-prompt-fixture-export"
    subprocess.run(
        [
            "git",
            "checkout-index",
            "--force",
            "--prefix=.fusion-prompt-fixture-export/",
            "--",
            "backend/test/fixtures/prompt_bundle/legacy_v2_contract.json",
        ],
        cwd=repository,
        check=True,
    )
    exported = export_root / "backend/test/fixtures/prompt_bundle/legacy_v2_contract.json"

    assert exported.read_bytes() == canonical
    assert hashlib.sha256(exported.read_bytes()).hexdigest() != hashlib.sha256(stale).hexdigest()
    exported.replace(fixture)
    assert fixture.read_bytes() == canonical


def test_windows_preflight_rematerializes_before_checking_raw_digest():
    script = (BACKEND_ROOT / ".github/scripts/check-frozen-prompt-fixture-bytes.ps1").read_text(
        encoding="utf-8"
    )

    export_index = script.index("checkout-index --force")
    verify_export = script.index("ReadAllBytes($materializedLegacyV2FixturePath)")
    replace_worktree = script.index("[System.IO.File]::Copy")
    verify_worktree = script.index("ReadAllBytes($legacyV2FixturePath)")
    assert export_index < verify_export < replace_worktree < verify_worktree
