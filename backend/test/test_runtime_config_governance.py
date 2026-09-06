import unittest
from types import SimpleNamespace


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def order_by(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.committed = False
        self.refreshed = None
        self.added = None

    def query(self, model):
        return _FakeQuery(self.rows)

    def add(self, row):
        self.added = row
        self.rows.append(row)

    def commit(self):
        self.committed = True

    def refresh(self, row):
        self.refreshed = row

    def close(self):
        self.closed = True


def _row(*, row_id: str = "row-1", version: str = "v1", active: bool = False):
    return SimpleNamespace(
        id=row_id,
        namespace="custom_config",
        key="example",
        version=version,
        payload={"enabled": True},
        is_active=active,
        description="测试配置",
        created_at=None,
        updated_at=None,
    )


class RuntimeConfigGovernanceTests(unittest.TestCase):
    def test_prompt_namespaces_are_repository_managed(self):
        from app.schemas.response import ApiException
        from app.services.runtime_config_governance import activate_runtime_config_entry, create_runtime_config_entry

        for namespace in ("prompt_template", "prompt_bundle"):
            row = SimpleNamespace(id="prompt-row", namespace=namespace)
            session = _FakeSession([row])
            with self.subTest(namespace=namespace):
                with self.assertRaises(ApiException):
                    create_runtime_config_entry(
                        namespace=namespace,
                        key="example",
                        version="v2",
                        payload={"template": "正文"},
                        session_factory=lambda: session,
                    )
                with self.assertRaises(ApiException):
                    activate_runtime_config_entry("prompt-row", session_factory=lambda: session)
                self.assertFalse(session.committed)

    def test_snapshot_only_exposes_supported_runtime_defaults(self):
        from app.services.runtime_config_governance import build_runtime_config_snapshot

        snapshot = build_runtime_config_snapshot(session_factory=lambda: _FakeSession([]))
        effective_keys = {(item["namespace"], item["key"]) for item in snapshot["effective"]}
        self.assertEqual(
            effective_keys,
            {("agent_strategy", "default"), ("model_presentation", "default")},
        )

    def test_create_runtime_config_entry_creates_inactive_version(self):
        from app.services.runtime_config_governance import create_runtime_config_entry

        session = _FakeSession([])
        result = create_runtime_config_entry(
            namespace="custom_config",
            key="example",
            version="v2",
            payload={"enabled": True},
            description="安全写入测试",
            session_factory=lambda: session,
        )

        self.assertTrue(session.committed)
        self.assertIs(session.refreshed, session.added)
        self.assertTrue(session.closed)
        self.assertFalse(session.added.is_active)
        self.assertEqual(result["version"], "v2")
        self.assertTrue(result["valid"])

    def test_create_runtime_config_entry_rejects_duplicate_version(self):
        from app.schemas.response import ApiException
        from app.services.runtime_config_governance import create_runtime_config_entry

        session = _FakeSession([_row()])
        with self.assertRaises(ApiException) as raised:
            create_runtime_config_entry(
                namespace="custom_config",
                key="example",
                version="v1",
                payload={"enabled": False},
                session_factory=lambda: session,
            )

        self.assertEqual(raised.exception.code, "CONFLICT")
        self.assertFalse(session.committed)

    def test_activate_runtime_config_entry_enforces_single_active_version(self):
        from app.services.runtime_config_governance import activate_runtime_config_entry

        target = _row(row_id="target", version="v2")
        old_active = _row(row_id="old", active=True)
        session = _FakeSession([target, old_active])
        result = activate_runtime_config_entry("target", session_factory=lambda: session)

        self.assertTrue(target.is_active)
        self.assertFalse(old_active.is_active)
        self.assertTrue(session.committed)
        self.assertTrue(result["is_active"])

    def test_set_runtime_config_entry_inactive_updates_row(self):
        from app.services.runtime_config_governance import set_runtime_config_entry_active

        row = _row(active=True)
        session = _FakeSession([row])
        result = set_runtime_config_entry_active("row-1", False, session_factory=lambda: session)

        self.assertFalse(row.is_active)
        self.assertTrue(session.committed)
        self.assertFalse(result["is_active"])


if __name__ == "__main__":
    unittest.main()
