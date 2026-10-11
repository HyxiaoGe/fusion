import importlib
import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

os.environ.setdefault("DATABASE_URL", "sqlite:///./fusion-test.db")
os.environ.setdefault("AUTH_SERVICE_BASE_URL", "http://auth.example:8100")
os.environ.setdefault("AUTH_SERVICE_CLIENT_ID", "fusion-client")
os.environ.setdefault("AUTH_SERVICE_JWKS_URL", "http://auth.example:8100/.well-known/jwks.json")

from app.core import guest_identity, security  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.schemas.response import ApiException  # noqa: E402

SECRET = "test-guest-secret-0123456789abcdef"


def _guest_settings(enabled=True, secret=SECRET):
    return patch.multiple(settings, GUEST_MODE_ENABLED=enabled, GUEST_TOKEN_SECRET=secret)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expire_calls = []

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key, seconds):
        self.expire_calls.append((key, seconds))


def _request(headers=None, host="10.0.0.9"):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": (host, 1234)})


class GuestTokenTests(unittest.TestCase):
    def test_issued_token_verifies_to_same_user(self):
        with _guest_settings():
            token, expires_at = guest_identity.issue_guest_token("guest-1")
            self.assertTrue(guest_identity.is_guest_token(token))
            self.assertEqual(guest_identity.verify_guest_token(token), "guest-1")
        self.assertGreater(expires_at, time.time() + 300 * 24 * 3600)

    def test_token_signed_with_other_secret_is_rejected(self):
        forged = jwt.encode(
            {
                "iss": "fusion-guest",
                "sub": "victim",
                "typ": "guest",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
            },
            "a_secret_key",
            algorithm="HS256",
        )
        with _guest_settings():
            self.assertTrue(guest_identity.is_guest_token(forged))
            with self.assertRaises(guest_identity.GuestTokenError):
                guest_identity.verify_guest_token(forged)

    def test_expired_token_is_rejected(self):
        with _guest_settings():
            token, _ = guest_identity.issue_guest_token("guest-1", now=time.time() - 400 * 24 * 3600)
            with self.assertRaises(guest_identity.GuestTokenError):
                guest_identity.verify_guest_token(token)

    def test_disabled_guest_mode_rejects_valid_token(self):
        with _guest_settings():
            token, _ = guest_identity.issue_guest_token("guest-1")
        with _guest_settings(enabled=False):
            with self.assertRaises(guest_identity.GuestTokenError):
                guest_identity.verify_guest_token(token)
        with _guest_settings(secret=""):
            self.assertFalse(guest_identity.guest_mode_available())

    def test_auth_service_token_is_not_treated_as_guest(self):
        other = jwt.encode({"iss": "http://auth.example:8100", "sub": "u"}, "x", algorithm="HS256")
        self.assertFalse(guest_identity.is_guest_token(other))
        self.assertFalse(guest_identity.is_guest_token("not-a-jwt"))


class GuestRateLimitTests(unittest.TestCase):
    def test_new_guest_slots_are_limited_per_ip_per_day(self):
        fake = FakeRedis()
        with (
            patch.object(guest_identity, "get_redis", return_value=fake),
            patch.object(settings, "GUEST_NEW_IDENTITIES_PER_IP_PER_DAY", 5),
        ):
            results = [guest_identity.reserve_new_guest_slot("1.2.3.4", now=1_000_000) for _ in range(6)]
            other_ip = guest_identity.reserve_new_guest_slot("5.6.7.8", now=1_000_000)
        self.assertEqual(results, [True] * 5 + [False])
        self.assertTrue(other_ip)
        self.assertEqual(len(fake.expire_calls), 2)
        self.assertNotIn("1.2.3.4", next(iter(fake.values)))

    def test_client_ip_prefers_cloudflare_header(self):
        request = _request({"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2, 3.3.3.3"})
        self.assertEqual(guest_identity.client_ip(request), ("1.1.1.1", "cf-connecting-ip"))
        request = _request({"X-Forwarded-For": "2.2.2.2, 3.3.3.3"})
        self.assertEqual(guest_identity.client_ip(request), ("2.2.2.2", "x-forwarded-for"))
        self.assertEqual(guest_identity.client_ip(_request()), ("10.0.0.9", "peer"))


class GuestAuthenticationTests(unittest.TestCase):
    def _resolve(self, token, user):
        repo = MagicMock()
        repo.get.return_value = user
        with (
            _guest_settings(),
            patch("app.core.security.UserRepository", return_value=repo),
            patch.object(security.jwt_validator, "verify") as auth_verify,
        ):
            try:
                return security.get_current_user(db=MagicMock(), token=token)
            finally:
                auth_verify.assert_not_called()

    def test_guest_token_resolves_guest_user_without_auth_service(self):
        guest = SimpleNamespace(id="guest-1", is_guest=True)
        with _guest_settings():
            token, _ = guest_identity.issue_guest_token("guest-1")
        self.assertIs(self._resolve(token, guest), guest)

    def test_guest_token_cannot_impersonate_registered_user(self):
        registered = SimpleNamespace(id="user-1", is_guest=False)
        with _guest_settings():
            token, _ = guest_identity.issue_guest_token("user-1")
        with self.assertRaises(HTTPException) as ctx:
            self._resolve(token, registered)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_require_registered_user_rejects_guest(self):
        with self.assertRaises(ApiException) as ctx:
            security.require_registered_user(SimpleNamespace(is_guest=True))
        self.assertEqual((ctx.exception.code, ctx.exception.status_code), ("LOGIN_REQUIRED", 403))
        user = SimpleNamespace(is_guest=False)
        self.assertIs(security.require_registered_user(user), user)


class GuestApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.pop("main", None)
        cls.main = importlib.import_module("main")
        cls.client = TestClient(cls.main.app)
        cls.deps = {}
        for route in cls.main.app.routes:
            pending = list(getattr(getattr(route, "dependant", None), "dependencies", []))
            while pending:
                dep = pending.pop()
                name = getattr(dep.call, "__qualname__", None)
                if name:
                    cls.deps.setdefault(name, dep.call)
                pending.extend(dep.dependencies)

    def tearDown(self):
        self.main.app.dependency_overrides.clear()

    def _override_db(self, db):
        def override():
            yield db

        self.main.app.dependency_overrides[self.deps["get_db"]] = override

    def test_create_guest_returns_token_for_new_guest_user(self):
        db = MagicMock()
        self._override_db(db)
        with (
            _guest_settings(),
            patch("app.api.auth.reserve_new_guest_slot", return_value=True),
        ):
            response = self.client.post("/api/auth/guest", headers={"CF-Connecting-IP": "1.1.1.1"})
            self.assertEqual(response.status_code, 200)
            data = response.json()["data"]
            self.assertEqual(guest_identity.verify_guest_token(data["access_token"]), data["user"]["id"])
        created = db.add.call_args.args[0]
        self.assertEqual(created.id, data["user"]["id"])
        self.assertTrue(created.is_guest)
        self.assertTrue(created.username.startswith("guest-"))
        self.assertTrue(data["user"]["is_guest"])
        db.commit.assert_called_once()

    def test_create_guest_when_disabled_or_limited(self):
        self._override_db(MagicMock())
        with _guest_settings(enabled=False):
            response = self.client.post("/api/auth/guest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"available": False})
        with _guest_settings(), patch("app.api.auth.reserve_new_guest_slot", return_value=False):
            response = self.client.post("/api/auth/guest")
        self.assertEqual((response.status_code, response.json()["code"]), (429, "GUEST_LIMIT_REACHED"))
        with _guest_settings(), patch("app.api.auth.reserve_new_guest_slot", side_effect=ConnectionError):
            response = self.client.post("/api/auth/guest")
        self.assertEqual((response.status_code, response.json()["code"]), (503, "GUEST_UNAVAILABLE"))

    def test_guest_is_blocked_from_login_only_writes(self):
        self._override_db(MagicMock())
        self.main.app.dependency_overrides[self.deps["get_current_user"]] = lambda: SimpleNamespace(
            id="guest-1", is_guest=True
        )
        for method, path, kwargs in [
            ("patch", "/api/auth/me", {"json": {"system_prompt": "x"}}),
            ("post", "/api/files/upload/init", {"json": {}}),
            ("post", "/api/knowledge-bases/", {"json": {"name": "kb"}}),
        ]:
            response = getattr(self.client, method)(path, **kwargs)
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response.json()["code"], "LOGIN_REQUIRED", path)


if __name__ == "__main__":
    unittest.main()
