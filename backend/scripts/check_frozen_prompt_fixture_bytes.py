"""在构建前验证冻结 Prompt 夹具的跨平台字节契约。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
P3A_FIXTURE = BACKEND_ROOT / "test/fixtures/prompt_bundle/p3a_sync.py"
EXPECTED_P3A_SHA256 = "1fc5150883d4a5f85f022c9278bb779c9a2d37df1567047728c3f6f8e240fb3f"
LEGACY_V2_FIXTURE = BACKEND_ROOT / "test/fixtures/prompt_bundle/legacy_v2_contract.json"
EXPECTED_LEGACY_V2_SHA256 = "442674b68077a82e1d6d4b5a2d6c290e92bf8842eec3106d05f620376bfbd090"


def canonicalize_lf(source: bytes) -> bytes:
    """只接受 LF 或标准 CRLF，并返回 LF 规范字节。"""

    canonical = source.replace(b"\r\n", b"\n")
    if b"\r" in canonical:
        raise ValueError("冻结 Prompt 夹具包含 CRLF 以外的非法回车字节")
    return canonical


def load_frozen_p3a_probe_source(path: Path) -> bytes:
    """读取夹具并验证其 LF 规范字节没有漂移。"""

    canonical = canonicalize_lf(path.read_bytes())
    actual = hashlib.sha256(canonical).hexdigest()
    if actual != EXPECTED_P3A_SHA256:
        raise ValueError(f"冻结 Prompt 夹具摘要不匹配: expected={EXPECTED_P3A_SHA256}, actual={actual}")
    return canonical


def load_frozen_legacy_v2_contract(path: Path) -> bytes:
    """读取摘要敏感的旧契约；任何 checkout 字节转换都必须失败。"""

    source = path.read_bytes()
    actual = hashlib.sha256(source).hexdigest()
    if actual != EXPECTED_LEGACY_V2_SHA256:
        raise ValueError(f"冻结 legacy v2 契约摘要不匹配: expected={EXPECTED_LEGACY_V2_SHA256}, actual={actual}")
    return source


def verify_frozen_prompt_fixture_bytes() -> str:
    """验证真实夹具，并在内存中覆盖 CRLF 与非法回车分支。"""

    canonical = load_frozen_p3a_probe_source(P3A_FIXTURE)
    load_frozen_legacy_v2_contract(LEGACY_V2_FIXTURE)
    windows_checkout = canonical.replace(b"\n", b"\r\n")
    if canonicalize_lf(windows_checkout) != canonical:
        raise ValueError("冻结 Prompt 夹具的 CRLF 规范化结果不一致")
    try:
        canonicalize_lf(b"invalid\rnewline")
    except ValueError:
        pass
    else:
        raise ValueError("冻结 Prompt 夹具的非法回车负例未被拒绝")
    return hashlib.sha256(canonical).hexdigest()


def main() -> int:
    try:
        digest = verify_frozen_prompt_fixture_bytes()
    except (OSError, ValueError) as error:
        print(f"frozen Prompt fixture byte preflight failed: {error}", file=sys.stderr)
        return 1
    print(f"frozen Prompt fixture byte preflight passed: sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
