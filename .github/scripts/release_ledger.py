#!/usr/bin/env python3
"""维护 Fusion dev 发布的 SHA → repository digest 台账。"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_ID_PATTERN = DIGEST_PATTERN
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]+$")
COMPONENT_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True)
class ImageIdentity:
    ref: str
    image_id: str

    def as_dict(self) -> dict[str, str]:
        repository, digest = split_digest_ref(self.ref)
        validate_image_id(self.image_id)
        return {
            "repository": repository,
            "digest": digest,
            "ref": self.ref,
            "image_id": self.image_id,
        }


def validate_sha(value: str) -> str:
    if SHA_PATTERN.fullmatch(value) is None:
        raise ValueError("SHA 必须是 40 位小写十六进制")
    return value


def validate_digest(value: str) -> str:
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError("repository digest 格式无效")
    return value


def validate_image_id(value: str) -> str:
    if IMAGE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("image ID 格式无效")
    return value


def validate_repository(value: str) -> str:
    if not value or "@" in value or REPOSITORY_PATTERN.fullmatch(value) is None:
        raise ValueError("镜像 repository 格式无效")
    return value


def split_digest_ref(value: str) -> tuple[str, str]:
    repository, separator, digest = value.rpartition("@")
    if not separator:
        raise ValueError("部署镜像必须使用 repository digest")
    return validate_repository(repository), validate_digest(digest)


def manifest_ref(repository: str, payload: str) -> str:
    repository = validate_repository(repository)
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("registry manifest JSON 无效") from exc
    if not isinstance(document, dict):
        raise ValueError("registry manifest 必须是 JSON object")
    digest = document.get("digest")
    if not isinstance(digest, str):
        raise ValueError("registry manifest 缺少 digest")
    return f"{repository}@{validate_digest(digest)}"


def empty_ledger(app: str) -> dict[str, Any]:
    return {"version": 1, "app": app, "current_sha": None, "releases": {}}


def _validate_ledger(document: Any, app: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("发布台账必须是 JSON object")
    if document.get("version") != 1 or document.get("app") != app:
        raise ValueError("发布台账版本或应用不匹配")
    releases = document.get("releases")
    current = document.get("current_sha")
    if not isinstance(releases, dict):
        raise ValueError("发布台账 releases 无效")
    if current is not None:
        validate_sha(current)
        if current not in releases:
            raise ValueError("发布台账 current_sha 缺少对应 release")
    for sha, release in releases.items():
        validate_sha(sha)
        if not isinstance(release, dict) or release.get("sha") != sha:
            raise ValueError("发布台账 release 结构无效")
        images = release.get("images")
        if not isinstance(images, dict) or not images:
            raise ValueError("发布台账 release 缺少镜像身份")
        for component, image in images.items():
            if COMPONENT_PATTERN.fullmatch(component) is None or not isinstance(image, dict):
                raise ValueError("发布台账镜像组件无效")
            ref = image.get("ref")
            image_id = image.get("image_id")
            if not isinstance(ref, str) or not isinstance(image_id, str):
                raise ValueError("发布台账镜像身份字段无效")
            repository, digest = split_digest_ref(ref)
            if image.get("repository") != repository or image.get("digest") != digest:
                raise ValueError("发布台账镜像身份不一致")
            validate_image_id(image_id)
    return document


def load_ledger(path: Path, app: str) -> dict[str, Any]:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return empty_ledger(app)
    if path.is_symlink():
        raise ValueError("发布台账不能是符号链接")
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.getuid():
        raise ValueError("发布台账必须是当前用户拥有的普通文件")
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise ValueError("发布台账权限必须是 0600")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("发布台账 JSON 无效") from exc
    return _validate_ledger(document, app)


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = path.parent.lstat()
    if path.parent.is_symlink() or not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid():
        raise ValueError("发布台账目录必须是当前用户拥有的真实目录")
    os.chmod(path.parent, 0o700)
    descriptor, temp_name = tempfile.mkstemp(prefix=".release-ledger.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def record_release(
    *,
    path: Path,
    app: str,
    sha: str,
    images: dict[str, ImageIdentity],
    run_id: str,
    recorded_at: str,
) -> None:
    validate_sha(sha)
    if not app or COMPONENT_PATTERN.fullmatch(app) is None:
        raise ValueError("应用名格式无效")
    if not images:
        raise ValueError("发布台账至少需要一个镜像组件")
    normalized_images: dict[str, dict[str, str]] = {}
    for component, identity in images.items():
        if COMPONENT_PATTERN.fullmatch(component) is None:
            raise ValueError("镜像组件名格式无效")
        normalized_images[component] = identity.as_dict()
    ledger = load_ledger(path, app)
    existing = ledger["releases"].get(sha)
    if existing is not None:
        if existing.get("images") != normalized_images:
            raise ValueError("同一 SHA 已绑定不同镜像身份")
    else:
        ledger["releases"][sha] = {
            "sha": sha,
            "run_id": str(run_id),
            "recorded_at": recorded_at,
            "images": normalized_images,
        }
    ledger["current_sha"] = sha
    _atomic_write(path, ledger)


def lookup_release(path: Path, app: str, sha: str) -> dict[str, Any]:
    validate_sha(sha)
    ledger = load_ledger(path, app)
    release = ledger["releases"].get(sha)
    if not isinstance(release, dict):
        raise ValueError(f"发布台账不存在 SHA: {sha}")
    return release


def lookup_ref(path: Path, app: str, sha: str, component: str) -> str:
    release = lookup_release(path, app, sha)
    image = release["images"].get(component)
    if not isinstance(image, dict) or not isinstance(image.get("ref"), str):
        raise ValueError(f"发布台账缺少镜像组件: {component}")
    return image["ref"]


def lookup_image_id(path: Path, app: str, sha: str, component: str) -> str:
    release = lookup_release(path, app, sha)
    image = release["images"].get(component)
    if not isinstance(image, dict) or not isinstance(image.get("image_id"), str):
        raise ValueError(f"发布台账缺少镜像组件: {component}")
    return image["image_id"]


def current_sha(path: Path, app: str) -> str:
    value = load_ledger(path, app).get("current_sha")
    if not isinstance(value, str):
        raise ValueError("发布台账尚无 current_sha")
    return validate_sha(value)


def parse_image_argument(value: str) -> tuple[str, ImageIdentity]:
    component, separator, remainder = value.partition("|")
    ref, separator2, image_id = remainder.partition("|")
    if not separator or not separator2 or "|" in image_id:
        raise ValueError("--image 必须是 component|repository@digest|image_id")
    if COMPONENT_PATTERN.fullmatch(component) is None:
        raise ValueError("镜像组件名格式无效")
    return component, ImageIdentity(ref, image_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest-ref")
    manifest.add_argument("--repository", required=True)

    lookup = subparsers.add_parser("lookup")
    lookup.add_argument("--path", type=Path, required=True)
    lookup.add_argument("--app", required=True)
    lookup.add_argument("--sha", required=True)
    lookup.add_argument("--component", required=True)
    lookup.add_argument("--field", choices=("ref", "image_id"), default="ref")

    current = subparsers.add_parser("current-sha")
    current.add_argument("--path", type=Path, required=True)
    current.add_argument("--app", required=True)

    record = subparsers.add_parser("record")
    record.add_argument("--path", type=Path, required=True)
    record.add_argument("--app", required=True)
    record.add_argument("--sha", required=True)
    record.add_argument("--run-id", required=True)
    record.add_argument("--recorded-at", required=True)
    record.add_argument("--image", action="append", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "manifest-ref":
        print(manifest_ref(args.repository, sys.stdin.read()))
        return
    if args.command == "lookup":
        if args.field == "ref":
            print(lookup_ref(args.path, args.app, args.sha, args.component))
        else:
            print(lookup_image_id(args.path, args.app, args.sha, args.component))
        return
    if args.command == "current-sha":
        print(current_sha(args.path, args.app))
        return
    if args.command == "record":
        images = dict(parse_image_argument(value) for value in args.image)
        record_release(
            path=args.path,
            app=args.app,
            sha=args.sha,
            images=images,
            run_id=args.run_id,
            recorded_at=args.recorded_at,
        )
        return
    raise AssertionError(f"未处理命令: {args.command}")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
