"""发布一篇更新日志：写入 changelogs 并给所有用户的通知中心推一条提醒。

稿子放在仓库 docs/changelogs/<版本>.md，开头是 front matter：

    ---
    version: 2026.10.09
    title: 标题
    summary: 一句话摘要（通知正文）
    ---
    Markdown 正文……

已发布版本不可修改：同版本同内容重复执行为幂等，同版本不同内容会被拒绝。
API 镜像里没有 docs/，稿子经标准输入传入。在本机执行：

    ssh dev 'docker exec -i fusion-api python -m scripts.publish_changelog -' < docs/changelogs/2026.10.09.md

只校验不发布加 --dry-run（本机即可运行）：

    python -m scripts.publish_changelog --dry-run ../docs/changelogs/2026.10.09.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from app.schemas.changelog import ChangelogPublishRequest
from app.schemas.response import ApiException

_FRONT_MATTER_KEYS = ("version", "title", "summary")


class ChangelogDraftError(ValueError):
    pass


def parse_draft(text: str) -> ChangelogPublishRequest:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ChangelogDraftError("稿子必须以 --- 开头的 front matter 起始")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ChangelogDraftError("front matter 缺少结束的 ---") from exc

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or key not in _FRONT_MATTER_KEYS:
            raise ChangelogDraftError(f"无法识别的 front matter 行：{line}")
        if key in meta:
            raise ChangelogDraftError(f"front matter 重复字段：{key}")
        meta[key] = value.strip()
    missing = [key for key in _FRONT_MATTER_KEYS if not meta.get(key)]
    if missing:
        raise ChangelogDraftError(f"front matter 缺少字段：{', '.join(missing)}")

    content = "\n".join(lines[end + 1 :]).strip()
    try:
        return ChangelogPublishRequest(**meta, content=content)
    except ValidationError as exc:
        raise ChangelogDraftError(str(exc)) from exc


def publish(request: ChangelogPublishRequest) -> str:
    from sqlalchemy import select

    from app.db.changelog_repository import ChangelogRepository
    from app.db.database import SessionLocal
    from app.db.models import Changelog
    from app.services.changelog_service import ChangelogService

    with SessionLocal() as db:
        existed = db.scalar(select(Changelog.id).where(Changelog.version == request.version)) is not None
        # user_id 只用于回查当前用户自己的通知 id，脚本不需要。
        detail = ChangelogService(db).publish("", request)
        if existed:
            return f"版本 {detail.version} 已发布过且内容一致，未重复通知（id={detail.id}）"
        recipients = len(ChangelogRepository(db).recipient_ids())
        return f"已发布 {detail.version}「{detail.title}」，通知 {recipients} 位用户（id={detail.id}）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布更新日志")
    parser.add_argument("draft", help="稿子路径，- 表示从标准输入读取")
    parser.add_argument("--dry-run", action="store_true", help="只校验稿子，不写库")
    args = parser.parse_args(argv)

    text = sys.stdin.read() if args.draft == "-" else Path(args.draft).read_text(encoding="utf-8")
    try:
        request = parse_draft(text)
    except ChangelogDraftError as exc:
        print(f"稿子无效：{exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"校验通过：{request.version}「{request.title}」，正文 {len(request.content)} 字符")
        return 0
    try:
        print(publish(request))
    except ApiException as exc:
        print(f"发布失败：{exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
