from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: True)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("KEY_ENCRYPTION_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SERVE_FRONTEND_FROM_FLASK", "0")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("PASSWORD_PEPPER", "pepper-for-tests")
    monkeypatch.setenv("RATE_LIMIT_UPLOAD_PER_MIN", "50")
    monkeypatch.setenv("RATE_LIMIT_ANALYZE_PER_MIN", "50")
    monkeypatch.setenv("RATE_LIMIT_FOLLOWUP_PER_MIN", "50")
    monkeypatch.setenv("RATE_LIMIT_AUTH_REGISTER_PER_MIN", "50")
    monkeypatch.setenv("RATE_LIMIT_AUTH_LOGIN_PER_MIN", "50")
    monkeypatch.setenv("OPENAI_CHAT_INPUT_COST_PER_1K", "0.01")
    monkeypatch.setenv("OPENAI_CHAT_OUTPUT_COST_PER_1K", "0.02")
    monkeypatch.setenv("INTERNAL_ADMIN_KEY", "internal-test-key")

    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    module.REDIS_CLIENT = None
    module.rate_limit_buckets.clear()
    module.app.config["TESTING"] = True

    yield module

    module.rate_limit_buckets.clear()
    sys.modules.pop("app", None)


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()


@pytest.fixture
def strong_password():
    return "SenhaForte!1"


@pytest.fixture
def auth_headers():
    def _build(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return _build


@pytest.fixture
def register_user(client, strong_password):
    def _register(email: str = "user@example.com", password: str | None = None):
        response = client.post(
            "/auth/register",
            json={"email": email, "password": password or strong_password},
        )
        return response, response.get_json()

    return _register


@pytest.fixture
def db_fetchall(app_module):
    def _fetchall(query: str, params: tuple = ()):
        conn = sqlite3.connect(app_module.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    return _fetchall


@pytest.fixture
def db_fetchone(db_fetchall):
    def _fetchone(query: str, params: tuple = ()):
        rows = db_fetchall(query, params)
        return rows[0] if rows else None

    return _fetchone


@pytest.fixture
def activate_openai_key(app_module):
    def _activate(user_id: int, api_key: str = "sk-test-1234567890"):
        with app_module.app.app_context():
            app_module.set_active_openai_key_for_user(user_id, api_key)
        return api_key

    return _activate
