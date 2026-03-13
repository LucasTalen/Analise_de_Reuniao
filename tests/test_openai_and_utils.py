from __future__ import annotations

from pathlib import Path

import pytest


class FakeResponse:
    def __init__(self, status_code=200, payload=None, ok=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.ok = status_code < 400 if ok is None else ok

    def json(self):
        return self._payload


def test_validate_password_strength_and_hash_roundtrip(app_module, strong_password):
    assert app_module.validate_password_strength(strong_password) == strong_password

    hashed = app_module.hash_password(strong_password)
    assert hashed != strong_password
    assert app_module.verify_password(strong_password, hashed) == (True, False)


def test_mask_encrypt_and_decrypt_roundtrip(app_module):
    masked = app_module.mask_api_key("sk-super-secret-1234")
    encrypted = app_module.encrypt_api_key("sk-super-secret-1234")
    decrypted = app_module.decrypt_api_key(encrypted)

    assert masked == "sk-...1234"
    assert encrypted != "sk-super-secret-1234"
    assert decrypted == "sk-super-secret-1234"


def test_decrypt_api_key_rejects_invalid_ciphertext(app_module):
    with pytest.raises(app_module.ApiError) as error:
        app_module.decrypt_api_key("ciphertext-invalido")

    assert error.value.code == "credential_decrypt_failed"


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("curta", "pelo menos"),
        ("SemNumero!", "numero"),
        ("semmaiuscula1!", "maiuscula"),
        ("SEMMAIUSCULA1!", "minuscula"),
        ("SemSimbolo1", "simbolo"),
        ("Senha Forte!1", "espacos"),
    ],
)
def test_validate_password_strength_rejects_weak_values(app_module, password, message):
    with pytest.raises(app_module.ApiError) as error:
        app_module.validate_password_strength(password)

    assert error.value.code == "weak_password"
    assert message in error.value.message


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "invalid_openai_key"),
        (429, "provider_rate_limited"),
        (500, "provider_failure"),
        (403, "openai_key_validation_failed"),
    ],
)
def test_validate_openai_api_key_maps_provider_errors(app_module, monkeypatch, status_code, expected_code):
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(status_code=status_code),
    )

    with pytest.raises(app_module.ApiError) as error:
        app_module.validate_openai_api_key("sk-test")

    assert error.value.code == expected_code


def test_validate_openai_api_key_handles_request_exception(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(app_module.requests.RequestException("boom")),
    )

    with pytest.raises(app_module.ApiError) as error:
        app_module.validate_openai_api_key("sk-test")

    assert error.value.code == "provider_failure"


def test_env_helpers_and_require_env(app_module, monkeypatch):
    monkeypatch.setenv("TEST_INT", "12")
    monkeypatch.setenv("TEST_FLOAT", "3.5")

    assert app_module.parse_bool("true") is True
    assert app_module.parse_bool("0") is False
    assert app_module.env_int("TEST_INT", 1) == 12
    assert app_module.env_float("TEST_FLOAT", 1.0) == 3.5
    assert app_module.require_env("TEST_FLOAT") == "3.5"

    monkeypatch.setenv("TEST_INT", "invalido")
    assert app_module.env_int("TEST_INT", 7) == 7

    monkeypatch.delenv("MISSING_ENV", raising=False)
    with pytest.raises(RuntimeError):
        app_module.require_env("MISSING_ENV")


def test_ask_openai_builds_messages_and_returns_usage(app_module, monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse(
            status_code=200,
            payload={
                "choices": [{"message": {"content": "Resposta final"}}],
                "usage": {"prompt_tokens": 42, "completion_tokens": 11},
                "model": "gpt-4.1-nano",
            },
        )

    monkeypatch.setattr(app_module.requests, "post", fake_post)

    result = app_module.ask_openai(
        api_key="sk-openai",
        segments=[{"start": 0, "end": 1.5, "text": "Bom dia"}],
        user_prompt="Resuma",
        history=[{"role": "user", "content": "Pergunta anterior"}],
    )

    assert result["answer"] == "Resposta final"
    assert result["usage"]["prompt_tokens"] == 42
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-openai"
    assert captured["json"]["messages"][-1] == {"role": "user", "content": "Resuma"}
    assert "Transcricao" in captured["json"]["messages"][1]["content"]


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "invalid_openai_key"),
        (429, "provider_rate_limited"),
        (500, "provider_failure"),
    ],
)
def test_ask_openai_maps_provider_errors(app_module, monkeypatch, status_code, expected_code):
    monkeypatch.setattr(
        app_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(status_code=status_code),
    )

    with pytest.raises(app_module.ApiError) as error:
        app_module.ask_openai("sk-openai", [], "Resuma")

    assert error.value.code == expected_code


def test_ask_openai_rejects_empty_choices(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(status_code=200, payload={"choices": []}),
    )

    with pytest.raises(app_module.ApiError) as error:
        app_module.ask_openai("sk-openai", [], "Resuma")

    assert error.value.code == "provider_empty_response"


def test_transcribe_with_openai_maps_413_error(app_module, monkeypatch, tmp_path):
    sample_file = tmp_path / "sample.mp4"
    sample_file.write_bytes(b"video")

    monkeypatch.setattr(
        app_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(status_code=413),
    )

    with pytest.raises(app_module.ApiError) as error:
        app_module.transcribe_with_openai("sk-openai", str(sample_file))

    assert error.value.code == "openai_transcription_file_too_large"


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "invalid_openai_key"),
        (429, "provider_rate_limited"),
        (500, "provider_failure"),
    ],
)
def test_transcribe_with_openai_maps_other_provider_errors(app_module, monkeypatch, tmp_path, status_code, expected_code):
    sample_file = tmp_path / f"sample-{status_code}.mp4"
    sample_file.write_bytes(b"video")

    monkeypatch.setattr(
        app_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(status_code=status_code),
    )

    with pytest.raises(app_module.ApiError) as error:
        app_module.transcribe_with_openai("sk-openai", str(sample_file))

    assert error.value.code == expected_code


def test_transcribe_large_video_offsets_segments_and_cleans_parts(app_module, monkeypatch, tmp_path):
    main_file = tmp_path / "main.mp4"
    part_one = tmp_path / "main_part1.mp4"
    part_two = tmp_path / "main_part2.mp4"
    for path in (main_file, part_one, part_two):
        path.write_bytes(b"video")

    monkeypatch.setattr(
        app_module,
        "split_video_ffmpeg",
        lambda video_path, max_size_mb=None: [str(part_one), str(part_two)],
    )

    def fake_transcribe(api_key, video_path):
        if Path(video_path) == part_one:
            return {"segments": [{"start": 0, "end": 2, "text": "Parte um"}]}
        return {"segments": [{"start": 0, "end": 3, "text": "Parte dois"}]}

    monkeypatch.setattr(app_module, "transcribe_with_openai", fake_transcribe)

    segments = app_module.transcribe_large_video("sk-openai", str(main_file))

    assert segments == [
        {"start": 0.0, "end": 2.0, "text": "Parte um"},
        {"start": 2.0, "end": 5.0, "text": "Parte dois"},
    ]
    assert part_one.exists() is False
    assert part_two.exists() is False
    assert main_file.exists() is True


def test_split_video_ffmpeg_returns_same_file_when_under_limit(app_module, tmp_path):
    sample_file = tmp_path / "small.mp4"
    sample_file.write_bytes(b"1234")

    parts = app_module.split_video_ffmpeg(str(sample_file), max_size_mb=1)

    assert parts == [str(sample_file)]


def test_split_video_ffmpeg_splits_when_file_is_large(app_module, monkeypatch, tmp_path):
    sample_file = tmp_path / "large.mp4"
    sample_file.write_bytes(b"x" * (2 * 1024 * 1024))
    outputs = []

    monkeypatch.setattr(
        app_module.ffmpeg,
        "probe",
        lambda _path: {"format": {"duration": "12"}},
    )

    class FakeOutputChain:
        def __init__(self, output_path):
            self.output_path = output_path

        def run(self, overwrite_output=True, quiet=True):
            Path(self.output_path).write_bytes(b"part")

    class FakeInputChain:
        def output(self, output_path, c="copy"):
            outputs.append((output_path, c))
            return FakeOutputChain(output_path)

    monkeypatch.setattr(app_module.ffmpeg, "input", lambda *args, **kwargs: FakeInputChain())

    parts = app_module.split_video_ffmpeg(str(sample_file), max_size_mb=1)

    assert len(parts) == 2
    assert outputs[0][1] == "copy"
    assert all(Path(part).exists() for part in parts)


def test_run_video_analysis_creates_initial_history(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "transcribe_large_video",
        lambda api_key, video_path: [{"start": 0, "end": 1, "text": "Abertura"}],
    )
    monkeypatch.setattr(
        app_module,
        "ask_openai",
        lambda api_key, segments, user_prompt: {
            "answer": "Resumo gerado",
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            "model": "gpt-analysis",
        },
    )
    monkeypatch.setattr(app_module, "create_analysis_session", lambda *args: "analysis-created")

    result = app_module.run_video_analysis(
        user_id=1,
        api_key="sk-openai",
        video_path="/tmp/fake.mp4",
        question="Quais são os riscos?",
        session_source_label="[transient-upload]:fake.mp4",
    )

    assert result["analysis_id"] == "analysis-created"
    assert result["insights"] == "Resumo gerado"
    assert result["history"][0]["content"] == "Quais são os riscos?"
    assert result["history"][1]["content"] == "Resumo gerado"


def test_health_reflects_runtime_limits(client, app_module, monkeypatch):
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "37")
    monkeypatch.setenv("MAX_VIDEO_DURATION_SECONDS", "915")

    response = client.get("/health")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["max_file_size_mb"] == 37
    assert payload["max_video_duration_seconds"] == 915
    assert payload["services"]["database"] == "ok"


def test_parse_dashboard_days_and_api_path_helpers(app_module):
    assert app_module.parse_dashboard_days("7") == 7
    assert app_module.is_api_like_path("/upload") is True
    assert app_module.is_api_like_path("/qualquer-coisa") is False

    with pytest.raises(app_module.ApiError) as error:
        app_module.parse_dashboard_days("0")

    assert error.value.code == "invalid_days"
