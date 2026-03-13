from __future__ import annotations

import io


def test_upload_requires_active_openai_key(client, register_user, auth_headers):
    response, payload = register_user()
    token = payload["token"]

    upload_response = client.post(
        "/upload",
        headers=auth_headers(token),
        data={"file": (io.BytesIO(b"fake-video"), "meeting.mp4")},
        content_type="multipart/form-data",
    )
    upload_payload = upload_response.get_json()

    assert upload_response.status_code == 403
    assert upload_payload["code"] == "missing_openai_key"


def test_upload_processes_transient_file_and_cleans_temp_dir(
    app_module,
    client,
    register_user,
    auth_headers,
    db_fetchone,
    monkeypatch,
    tmp_path,
):
    response, payload = register_user()
    token = payload["token"]
    user_id = payload["user"]["id"]

    monkeypatch.setattr(app_module, "get_active_openai_key_or_error", lambda _user_id: "sk-active-9999")

    created_paths = {}
    temp_dir = tmp_path / "transient-upload-dir"

    def fake_validate(file_path):
        assert file_path.endswith("input.mp4")
        assert temp_dir.exists()
        created_paths["validated"] = file_path

    def fake_run_analysis(user_id, api_key, video_path, question="", session_source_label=""):
        assert user_id == payload["user"]["id"]
        assert api_key == "sk-active-9999"
        assert video_path == created_paths["validated"]
        assert question == "Quais decisões?"
        assert session_source_label.endswith(":meeting.mp4")
        with app_module.app.app_context():
            analysis_id = app_module.create_analysis_session(
                user_id=user_id,
                source_label=session_source_label,
                segments=[{"start": 0, "end": 1.2, "text": "Bom dia"}],
                history=[{"role": "assistant", "content": "Resumo pronto"}],
            )
        return {
            "analysis_id": analysis_id,
            "insights": "Resumo pronto",
            "transcription": [{"start": 0, "end": 1.2, "text": "Bom dia"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80},
            "model": "gpt-test",
        }

    monkeypatch.setattr(app_module, "validate_uploaded_video", fake_validate)
    monkeypatch.setattr(
        app_module.tempfile,
        "mkdtemp",
        lambda prefix="": (temp_dir.mkdir(exist_ok=True) or str(temp_dir)),
    )
    monkeypatch.setattr(app_module, "run_video_analysis", fake_run_analysis)

    upload_response = client.post(
        "/upload",
        headers=auth_headers(token),
        data={
            "question": "Quais decisões?",
            "file": (io.BytesIO(b"fake-video"), "meeting.mp4"),
        },
        content_type="multipart/form-data",
    )
    upload_payload = upload_response.get_json()
    usage_row = db_fetchone(
        "SELECT endpoint, http_status, model, input_tokens, output_tokens FROM usage_events WHERE user_id = ?",
        (user_id,),
    )

    assert upload_response.status_code == 200
    assert upload_payload["analysis_id"]
    assert upload_payload["video_retained"] is False
    assert temp_dir.exists() is False
    assert usage_row["endpoint"] == "/upload"
    assert usage_row["http_status"] == 200
    assert usage_row["model"] == "gpt-test"
    assert usage_row["input_tokens"] == 120
    assert usage_row["output_tokens"] == 80


def test_upload_rejects_invalid_extension(client, register_user, auth_headers):
    response, payload = register_user()
    token = payload["token"]

    upload_response = client.post(
        "/upload",
        headers=auth_headers(token),
        data={"file": (io.BytesIO(b"fake-video"), "meeting.txt")},
        content_type="multipart/form-data",
    )

    assert upload_response.status_code == 400
    assert upload_response.get_json()["code"] == "invalid_extension"


def test_upload_validates_missing_file_and_large_payload(
    app_module,
    client,
    register_user,
    auth_headers,
    monkeypatch,
):
    response, payload = register_user()
    token = payload["token"]

    missing_file_response = client.post(
        "/upload",
        headers=auth_headers(token),
        data={},
        content_type="multipart/form-data",
    )

    monkeypatch.setattr(app_module, "get_active_openai_key_or_error", lambda _user_id: "sk-active-9999")
    monkeypatch.setattr(app_module, "validate_uploaded_video", lambda _file_path: None)
    monkeypatch.setattr(app_module, "run_video_analysis", lambda **kwargs: None)
    monkeypatch.setattr(app_module, "current_max_file_size_bytes", lambda: 4)
    monkeypatch.setattr(app_module, "current_max_file_size_mb", lambda: 1)

    too_large_response = client.post(
        "/upload",
        headers=auth_headers(token),
        data={"file": (io.BytesIO(b"0123456789"), "meeting.mp4")},
        content_type="multipart/form-data",
    )

    assert missing_file_response.status_code == 400
    assert missing_file_response.get_json()["code"] == "missing_file"
    assert too_large_response.status_code == 413
    assert too_large_response.get_json()["code"] == "file_too_large"


def test_upload_logs_error_when_analysis_fails(
    app_module,
    client,
    register_user,
    auth_headers,
    db_fetchone,
    monkeypatch,
):
    response, payload = register_user()
    token = payload["token"]
    user_id = payload["user"]["id"]

    monkeypatch.setattr(app_module, "get_active_openai_key_or_error", lambda _user_id: "sk-active-9999")
    monkeypatch.setattr(app_module, "validate_uploaded_video", lambda _file_path: None)
    monkeypatch.setattr(
        app_module,
        "run_video_analysis",
        lambda **kwargs: (_ for _ in ()).throw(
            app_module.ApiError(502, "Falha no provedor externo.", "provider_failure")
        ),
    )

    upload_response = client.post(
        "/upload",
        headers=auth_headers(token),
        data={"file": (io.BytesIO(b"fake-video"), "meeting.mp4")},
        content_type="multipart/form-data",
    )
    usage_row = db_fetchone(
        "SELECT endpoint, http_status FROM usage_events WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )

    assert upload_response.status_code == 502
    assert upload_response.get_json()["code"] == "provider_failure"
    assert usage_row["endpoint"] == "/upload"
    assert usage_row["http_status"] == 502


def test_followup_updates_history_and_logs_usage(
    app_module,
    client,
    register_user,
    auth_headers,
    activate_openai_key,
    db_fetchone,
    monkeypatch,
):
    response, payload = register_user()
    token = payload["token"]
    user_id = payload["user"]["id"]
    activate_openai_key(user_id)

    with app_module.app.app_context():
        analysis_id = app_module.create_analysis_session(
            user_id=user_id,
            source_label="[transient-upload]:meeting.mp4",
            segments=[{"start": 0, "end": 1, "text": "Abertura"}],
            history=[{"role": "assistant", "content": "Resumo inicial"}],
        )

    monkeypatch.setattr(
        app_module,
        "ask_openai",
        lambda api_key, segments, user_prompt, history=None: {
            "answer": f"Resposta para: {user_prompt}",
            "usage": {"prompt_tokens": 10, "completion_tokens": 6},
            "model": "gpt-followup",
        },
    )

    followup_response = client.post(
        "/followup",
        headers=auth_headers(token),
        json={"analysis_id": analysis_id, "question": "Quais são os próximos passos?"},
    )
    followup_payload = followup_response.get_json()
    session_row = db_fetchone(
        "SELECT history_json FROM analysis_sessions WHERE id = ?",
        (analysis_id,),
    )
    usage_row = db_fetchone(
        "SELECT endpoint, model, http_status FROM usage_events WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (analysis_id,),
    )

    assert followup_response.status_code == 200
    assert "Resposta para" in followup_payload["answer"]
    assert "Quais são os próximos passos?" in session_row["history_json"]
    assert usage_row["endpoint"] == "/followup"
    assert usage_row["model"] == "gpt-followup"
    assert usage_row["http_status"] == 200


def test_followup_validates_input_and_handles_provider_failure(
    app_module,
    client,
    register_user,
    auth_headers,
    activate_openai_key,
    db_fetchone,
    monkeypatch,
):
    response, payload = register_user(email="followup@example.com")
    token = payload["token"]
    user_id = payload["user"]["id"]
    activate_openai_key(user_id)

    missing_id_response = client.post(
        "/followup",
        headers=auth_headers(token),
        json={"analysis_id": "", "question": "Oi"},
    )
    empty_question_response = client.post(
        "/followup",
        headers=auth_headers(token),
        json={"analysis_id": "analysis-x", "question": ""},
    )
    not_found_response = client.post(
        "/followup",
        headers=auth_headers(token),
        json={"analysis_id": "analysis-x", "question": "Onde está?"},
    )

    with app_module.app.app_context():
        analysis_id = app_module.create_analysis_session(
            user_id=user_id,
            source_label="[transient-upload]:meeting.mp4",
            segments=[{"start": 0, "end": 1, "text": "Abertura"}],
            history=[],
        )

    monkeypatch.setattr(
        app_module,
        "ask_openai",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            app_module.requests.RequestException("timeout")
        ),
    )

    provider_response = client.post(
        "/followup",
        headers=auth_headers(token),
        json={"analysis_id": analysis_id, "question": "Teste erro"},
    )
    usage_row = db_fetchone(
        "SELECT endpoint, http_status FROM usage_events WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (analysis_id,),
    )

    assert missing_id_response.status_code == 400
    assert missing_id_response.get_json()["code"] == "missing_analysis_id"
    assert empty_question_response.status_code == 400
    assert empty_question_response.get_json()["code"] == "empty_question"
    assert not_found_response.status_code == 404
    assert not_found_response.get_json()["code"] == "analysis_session_not_found"
    assert provider_response.status_code == 502
    assert provider_response.get_json()["code"] == "provider_failure"
    assert usage_row["endpoint"] == "/followup"
    assert usage_row["http_status"] == 502


def test_usage_dashboard_exposes_global_stats_only_for_internal_admin(
    app_module,
    client,
    register_user,
    auth_headers,
):
    first_response, first_payload = register_user(email="first@example.com")
    second_response, second_payload = register_user(email="second@example.com")
    first_user_id = first_payload["user"]["id"]
    second_user_id = second_payload["user"]["id"]

    with app_module.app.app_context():
        app_module.log_usage_event(
            user_id=first_user_id,
            session_id=None,
            endpoint="/upload",
            model="gpt-test",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            http_status=200,
        )
        app_module.log_usage_event(
            user_id=second_user_id,
            session_id=None,
            endpoint="/followup",
            model="gpt-test",
            usage={"prompt_tokens": 200, "completion_tokens": 80},
            http_status=500,
        )

    regular_response = client.get("/usage/dashboard?days=7", headers=auth_headers(first_payload["token"]))
    admin_response = client.get(
        "/usage/dashboard?days=7",
        headers={
            **auth_headers(first_payload["token"]),
            "X-Internal-Admin-Key": app_module.INTERNAL_ADMIN_KEY,
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert regular_response.status_code == 200
    assert "global_top_users" not in regular_response.get_json()
    assert admin_response.status_code == 200
    assert len(admin_response.get_json()["global_top_users"]) == 2


def test_usage_dashboard_validates_days_and_clamps_max(app_module, client, register_user, auth_headers):
    response, payload = register_user(email="days@example.com")
    token = payload["token"]

    invalid_response = client.get("/usage/dashboard?days=abc", headers=auth_headers(token))
    app_module.USAGE_DASHBOARD_MAX_DAYS = 30
    clamped_response = client.get("/usage/dashboard?days=999", headers=auth_headers(token))

    assert response.status_code == 200
    assert invalid_response.status_code == 400
    assert invalid_response.get_json()["code"] == "invalid_days"
    assert clamped_response.status_code == 200
    assert clamped_response.get_json()["range_days"] == 30


def test_frontend_assets_return_expected_errors(app_module, client, tmp_path, monkeypatch):
    disabled_response = client.get("/")
    disabled_payload = disabled_response.get_json()

    app_module.SERVE_FRONTEND_FROM_FLASK = True
    monkeypatch.setattr(app_module, "FRONTEND_DIST", str(tmp_path / "missing-frontend-dist"))
    missing_build_response = client.get("/")
    api_path_response = client.get("/auth/register")

    asset_dir = tmp_path / "frontend-dist"
    asset_dir.mkdir()
    (asset_dir / "index.html").write_text("<html>build ok</html>", encoding="utf-8")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", str(asset_dir))
    served_response = client.get("/")

    assert disabled_response.status_code == 404
    assert disabled_payload["code"] == "frontend_disabled"
    assert missing_build_response.status_code == 404
    assert missing_build_response.get_json()["code"] == "frontend_build_missing"
    assert api_path_response.status_code == 404
    assert served_response.status_code == 200
    assert b"build ok" in served_response.data
