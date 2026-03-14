from __future__ import annotations

import importlib
import sys
from pathlib import Path

import mongomock
import pymongo
import pytest
from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: True)
    monkeypatch.setattr(pymongo, "MongoClient", mongomock.MongoClient)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("KEY_ENCRYPTION_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("MONGODB_DB_NAME", f"test_db_{uuid_suffix(tmp_path)}")
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
    module.MONGO_CLIENT.close()
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


def uuid_suffix(tmp_path):
    return str(abs(hash(str(tmp_path)))).replace("-", "")


@pytest.fixture
def collection(app_module):
    def _collection(name: str):
        return app_module.get_db()[name]

    return _collection


@pytest.fixture
def activate_openai_key(app_module):
    def _activate(user_id: str, api_key: str = "sk-test-1234567890"):
        with app_module.app.app_context():
            app_module.set_active_openai_key_for_user(user_id, api_key)
        return api_key

    return _activate
