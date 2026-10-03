"""pytest 入口：先加载 test 包，让 test/__init__.py 的 env fallback 在收集任何用例前生效。

test/scripts、test/ai 等子目录没有 __init__.py，按目录顺序先收集到它们时，
不经过本文件就会在 import app 时直接构造 Settings() 而缺少必填 env（#143）。
"""

import pytest

BUNDLED_SKILLS_MARKER = "bundled_skills"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{BUNDLED_SKILLS_MARKER}: 使用仓库内置 Skills 目录；未标记的用例看到的 Skill 目录为空",
    )


@pytest.fixture(autouse=True)
def _isolate_bundled_skills(request, monkeypatch):
    """路由与 Prompt 组装用例不随内置 Skill 内容变化；Skill 行为由专门用例覆盖。"""

    if request.node.get_closest_marker(BUNDLED_SKILLS_MARKER) is not None:
        return
    monkeypatch.setattr("app.services.stream.skill_loading.discover_skills", lambda root=None: ())
