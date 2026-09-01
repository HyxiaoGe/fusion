import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import release_ledger


SHA_A = "a" * 40
SHA_B = "b" * 40
DIGEST_A = "sha256:" + "1" * 64
DIGEST_B = "sha256:" + "2" * 64
IMAGE_ID_A = "sha256:" + "3" * 64
IMAGE_ID_B = "sha256:" + "4" * 64
API_REPOSITORY = "registry.example.com/team/fusion-api"
ADAPTER_REPOSITORY = "registry.example.com/team/fusion-flyai-adapter"


class ReleaseLedgerTests(unittest.TestCase):
    def test_manifest_ref_uses_registry_manifest_digest(self) -> None:
        payload = json.dumps({"digest": DIGEST_A, "mediaType": "application/vnd.oci.image.manifest.v1+json"})

        self.assertEqual(
            release_ledger.manifest_ref(API_REPOSITORY, payload),
            f"{API_REPOSITORY}@{DIGEST_A}",
        )

    def test_record_and_lookup_keep_components_under_one_application_sha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-ledger.json"
            images = {
                "api": release_ledger.ImageIdentity(f"{API_REPOSITORY}@{DIGEST_A}", IMAGE_ID_A),
                "adapter": release_ledger.ImageIdentity(
                    f"{ADAPTER_REPOSITORY}@{DIGEST_B}",
                    IMAGE_ID_B,
                ),
            }

            release_ledger.record_release(
                path=path,
                app="api",
                sha=SHA_A,
                images=images,
                run_id="123",
                recorded_at="2026-09-01T15:00:00+08:00",
            )

            self.assertEqual(release_ledger.lookup_ref(path, "api", SHA_A, "api"), images["api"].ref)
            self.assertEqual(
                release_ledger.lookup_ref(path, "api", SHA_A, "adapter"),
                images["adapter"].ref,
            )
            self.assertEqual(release_ledger.current_sha(path, "api"), SHA_A)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_same_sha_cannot_be_rebound_to_another_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-ledger.json"
            release_ledger.record_release(
                path=path,
                app="ui",
                sha=SHA_A,
                images={
                    "ui": release_ledger.ImageIdentity(f"{API_REPOSITORY}@{DIGEST_A}", IMAGE_ID_A)
                },
                run_id="1",
                recorded_at="2026-09-01T15:00:00+08:00",
            )

            with self.assertRaisesRegex(ValueError, "同一 SHA 已绑定不同镜像身份"):
                release_ledger.record_release(
                    path=path,
                    app="ui",
                    sha=SHA_A,
                    images={
                        "ui": release_ledger.ImageIdentity(
                            f"{API_REPOSITORY}@{DIGEST_B}",
                            IMAGE_ID_B,
                        )
                    },
                    run_id="2",
                    recorded_at="2026-09-01T15:01:00+08:00",
                )

    def test_existing_release_can_be_reactivated_without_rebinding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-ledger.json"
            first = {"ui": release_ledger.ImageIdentity(f"{API_REPOSITORY}@{DIGEST_A}", IMAGE_ID_A)}
            second = {"ui": release_ledger.ImageIdentity(f"{API_REPOSITORY}@{DIGEST_B}", IMAGE_ID_B)}
            release_ledger.record_release(
                path=path,
                app="ui",
                sha=SHA_A,
                images=first,
                run_id="1",
                recorded_at="2026-09-01T15:00:00+08:00",
            )
            release_ledger.record_release(
                path=path,
                app="ui",
                sha=SHA_B,
                images=second,
                run_id="2",
                recorded_at="2026-09-01T15:01:00+08:00",
            )

            release_ledger.record_release(
                path=path,
                app="ui",
                sha=SHA_A,
                images=first,
                run_id="3",
                recorded_at="2026-09-01T15:02:00+08:00",
            )

            self.assertEqual(release_ledger.current_sha(path, "ui"), SHA_A)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload["releases"]), {SHA_A, SHA_B})

    def test_load_rejects_symlink_and_broad_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            symlink = root / "release-ledger.json"
            symlink.symlink_to(target)

            with self.assertRaisesRegex(ValueError, "不能是符号链接"):
                release_ledger.load_ledger(symlink, "api")

            symlink.unlink()
            symlink.symlink_to(root / "missing.json")
            with self.assertRaisesRegex(ValueError, "不能是符号链接"):
                release_ledger.load_ledger(symlink, "api")

            symlink.unlink()
            symlink.write_text(json.dumps({"version": 1, "app": "api", "current_sha": None, "releases": {}}))
            os.chmod(symlink, 0o644)
            with self.assertRaisesRegex(ValueError, "权限必须是 0600"):
                release_ledger.load_ledger(symlink, "api")


if __name__ == "__main__":
    unittest.main()
