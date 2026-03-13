from __future__ import annotations

from werkzeug.security import generate_password_hash


def test_register_hashes_password_and_returns_token(register_user, db_fetchone, strong_password):
    response, payload = register_user(password=strong_password)

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["token"]
    assert payload["user"]["email"] == "user@example.com"

    user_row = db_fetchone("SELECT email, password_hash FROM users WHERE email = ?", ("user@example.com",))
    assert user_row is not None
    assert user_row["email"] == "user@example.com"
    assert user_row["password_hash"] != strong_password
    assert user_row["password_hash"].startswith("scrypt:")


def test_register_duplicate_email_is_rejected(register_user):
    first_response, _payload = register_user()
    second_response, second_payload = register_user()

    assert first_response.status_code == 200
    assert second_response.status_code == 400
    assert second_payload["code"] == "email_already_exists"


def test_login_rejects_invalid_password(client, register_user):
    register_user()

    response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "SenhaErrada!1"},
    )
    payload = response.get_json()

    assert response.status_code == 401
    assert payload["code"] == "invalid_credentials"


def test_login_upgrades_legacy_hash_without_pepper(app_module, client, db_fetchone, strong_password):
    legacy_hash = generate_password_hash(
        strong_password,
        method=app_module.PASSWORD_HASH_METHOD,
        salt_length=app_module.PASSWORD_SALT_LENGTH,
    )

    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO users (email, password_hash, created_at, status) VALUES (?, ?, ?, 'active')",
            ("legacy@example.com", legacy_hash, app_module.now_ts()),
        )
        db.commit()

    response = client.post(
        "/auth/login",
        json={"email": "legacy@example.com", "password": strong_password},
    )
    payload = response.get_json()
    upgraded_row = db_fetchone("SELECT password_hash FROM users WHERE email = ?", ("legacy@example.com",))

    assert response.status_code == 200
    assert payload["token"]
    assert upgraded_row["password_hash"] != legacy_hash
    assert app_module.verify_password(strong_password, upgraded_row["password_hash"]) == (True, False)


def test_auth_me_requires_auth_and_password_policy_is_public(client):
    me_response = client.get("/auth/me")
    me_payload = me_response.get_json()
    policy_response = client.get("/auth/password-policy")
    policy_payload = policy_response.get_json()

    assert me_response.status_code == 401
    assert me_payload["code"] == "unauthenticated"
    assert policy_response.status_code == 200
    assert policy_payload["min_length"] >= 8
    assert policy_payload["requires_symbol"] is True


def test_auth_me_returns_user_and_key_state(
    app_module,
    client,
    register_user,
    auth_headers,
    activate_openai_key,
):
    response, payload = register_user(email="me@example.com")
    user_id = payload["user"]["id"]
    activate_openai_key(user_id, "sk-me-1234")

    me_response = client.get("/auth/me", headers=auth_headers(payload["token"]))
    me_payload = me_response.get_json()

    assert me_response.status_code == 200
    assert me_payload["user"]["email"] == "me@example.com"
    assert me_payload["has_openai_key"] is True


def test_login_forbidden_for_inactive_user(app_module, client, register_user, strong_password):
    register_user(email="inactive@example.com")

    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute("UPDATE users SET status = 'disabled' WHERE email = ?", ("inactive@example.com",))
        db.commit()

    response = client.post(
        "/auth/login",
        json={"email": "inactive@example.com", "password": strong_password},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "forbidden"


def test_openai_key_lifecycle_masks_rotates_and_revokes(
    app_module,
    client,
    register_user,
    auth_headers,
    db_fetchall,
    monkeypatch,
):
    response, payload = register_user()
    token = payload["token"]

    validated_keys = []

    def fake_validate(api_key):
        validated_keys.append(api_key)

    monkeypatch.setattr(app_module, "validate_openai_api_key", fake_validate)

    first_save = client.post(
        "/integrations/openai-key",
        headers=auth_headers(token),
        json={"api_key": "sk-first-1234"},
    )
    second_save = client.post(
        "/integrations/openai-key",
        headers=auth_headers(token),
        json={"api_key": "sk-second-5678"},
    )
    status_response = client.get("/integrations/openai-key/status", headers=auth_headers(token))
    delete_response = client.delete("/integrations/openai-key", headers=auth_headers(token))
    post_delete_status = client.get("/integrations/openai-key/status", headers=auth_headers(token))
    key_rows = db_fetchall(
        """
        SELECT key_last4, is_active
          FROM user_api_keys
         ORDER BY id
        """
    )

    assert first_save.status_code == 200
    assert second_save.status_code == 200
    assert validated_keys == ["sk-first-1234", "sk-second-5678"]
    assert status_response.get_json()["masked_key"] == "sk-...5678"
    assert delete_response.status_code == 200
    assert post_delete_status.get_json()["is_active"] is False
    assert [tuple(row) for row in key_rows] == [("1234", 0), ("5678", 0)]


def test_openai_key_requires_payload(app_module, client, register_user, auth_headers):
    response, payload = register_user()
    token = payload["token"]

    monkeypatch_response = client.post(
        "/integrations/openai-key",
        headers=auth_headers(token),
        json={"api_key": ""},
    )

    assert monkeypatch_response.status_code == 400
    assert monkeypatch_response.get_json()["code"] == "missing_api_key"


def test_login_rate_limit_is_enforced(app_module, client, register_user):
    register_user(email="limited@example.com")
    app_module.RATE_LIMIT_AUTH_LOGIN_PER_MIN = 1
    app_module.rate_limit_buckets.clear()

    first_response = client.post(
        "/auth/login",
        json={"email": "limited@example.com", "password": "SenhaErrada!1"},
    )
    second_response = client.post(
        "/auth/login",
        json={"email": "limited@example.com", "password": "SenhaErrada!1"},
    )

    assert first_response.status_code == 401
    assert second_response.status_code == 429
    assert second_response.get_json()["code"] == "rate_limited"
