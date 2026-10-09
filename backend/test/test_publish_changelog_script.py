"""更新日志发布脚本：稿子解析、首次发布广播、重复发布幂等与冲突退出码。"""

import io

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Changelog, Notification
from scripts.publish_changelog import ChangelogDraftError, main, parse_draft
from test.test_changelogs import make_engine

DRAFT = """---
version: 2026.10.09
title: 通知中心上线
summary: 任务完成和版本更新都会出现在左下角铃铛里
---

## 新功能

- 通知中心
"""


def test_parse_draft_reads_front_matter_and_keeps_markdown_body():
    request = parse_draft(DRAFT)

    assert request.version == "2026.10.09"
    assert request.title == "通知中心上线"
    assert request.summary == "任务完成和版本更新都会出现在左下角铃铛里"
    assert request.content == "## 新功能\n\n- 通知中心"


@pytest.mark.parametrize(
    ("draft", "message"),
    [
        ("version: 1\n", "--- 开头"),
        ("---\nversion: 1\ntitle: t\nsummary: s\n正文", "结束的 ---"),
        ("---\nversion: 1\ntitle: t\n---\n正文", "缺少字段：summary"),
        ("---\nversion: 1\ntitle: t\nsummary: s\nauthor: x\n---\n正文", "无法识别"),
        ("---\nversion: 1\nversion: 2\ntitle: t\nsummary: s\n---\n正文", "重复字段"),
        ("---\nversion: 1\ntitle: t\nsummary: s\n---\n   \n", "content"),
        ("---\nversion: 版本一\ntitle: t\nsummary: s\n---\n正文", "version"),
    ],
)
def test_parse_draft_rejects_malformed_drafts(draft, message):
    with pytest.raises(ChangelogDraftError, match=message):
        parse_draft(draft)


@pytest.fixture
def session_factory(monkeypatch):
    engine = make_engine(connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.database.SessionLocal", factory)
    yield factory
    engine.dispose()


def run(monkeypatch, capsys, draft, *args):
    monkeypatch.setattr("sys.stdin", io.StringIO(draft))
    code = main([*args, "-"])
    return code, capsys.readouterr()


def counts(factory):
    with factory() as db:
        return (
            db.scalar(select(func.count()).select_from(Changelog)),
            db.scalar(select(func.count()).select_from(Notification)),
        )


def test_publish_broadcasts_once_and_rerun_is_idempotent(session_factory, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, DRAFT)
    assert code == 0
    assert "已发布 2026.10.09「通知中心上线」，通知 3 位用户" in out.out
    assert counts(session_factory) == (1, 3)

    code, out = run(monkeypatch, capsys, DRAFT)
    assert code == 0
    assert "已发布过且内容一致" in out.out
    assert counts(session_factory) == (1, 3)


def test_same_version_with_changed_content_fails_without_writing(session_factory, monkeypatch, capsys):
    run(monkeypatch, capsys, DRAFT)

    code, out = run(monkeypatch, capsys, DRAFT.replace("- 通知中心", "- 通知中心（改）"))

    assert code == 1
    assert "该版本已发布不同内容" in out.err
    assert counts(session_factory) == (1, 3)
    with Session(session_factory.kw["bind"]) as db:
        assert db.scalar(select(Changelog.content)) == "## 新功能\n\n- 通知中心"


def test_dry_run_and_invalid_draft_never_touch_database(session_factory, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, DRAFT, "--dry-run")
    assert code == 0
    assert "校验通过" in out.out

    code, out = run(monkeypatch, capsys, "no front matter")
    assert code == 2
    assert "稿子无效" in out.err
    assert counts(session_factory) == (0, 0)
