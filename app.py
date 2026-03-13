import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from functools import wraps

import ffmpeg
import redis
import requests
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_FILE_PATH = os.path.join(BASE_DIR, ".env")
# Carrega sempre o .env do projeto, sobrescrevendo valores antigos no processo.
load_dotenv(dotenv_path=ENV_FILE_PATH, override=True)


class ApiError(Exception):
    def __init__(self, status_code, message, code):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def parse_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    raw = os.getenv(name)
    if raw is None:
        return int(default)

    text = str(raw).strip()
    if text == "":
        return int(default)

    try:
        return int(text)
    except (TypeError, ValueError):
        app.logger.warning("Valor invalido para %s=%r. Usando default=%s", name, raw, default)
        return int(default)


def env_float(name, default):
    raw = os.getenv(name)
    if raw is None:
        return float(default)

    text = str(raw).strip()
    if text == "":
        return float(default)

    try:
        return float(text)
    except (TypeError, ValueError):
        app.logger.warning("Valor invalido para %s=%r. Usando default=%s", name, raw, default)
        return float(default)


def require_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variavel de ambiente obrigatoria ausente: {name}")
    return value


app = Flask(__name__)

SECRET_KEY = require_env("SECRET_KEY")
app.secret_key = SECRET_KEY

DATABASE_PATH = os.path.abspath(os.getenv("DATABASE_PATH", os.path.join(app.root_path, "app.db")))
UPLOAD_FOLDER = os.path.abspath(os.getenv("UPLOAD_FOLDER", "uploads"))
FRONTEND_DIST = os.path.join(app.root_path, "frontend", "dist")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DEFAULT_MAX_FILE_SIZE_MB = 100
DEFAULT_MAX_VIDEO_DURATION_SECONDS = 7200
OPENAI_TRANSCRIPTION_PROVIDER_MAX_FILE_SIZE_MB = min(
    env_int("OPENAI_TRANSCRIPTION_PROVIDER_MAX_FILE_SIZE_MB", 25),
    25
)
MAX_HISTORY_MESSAGES = env_int("MAX_HISTORY_MESSAGES", 12)
ANALYSIS_SESSION_TTL_SECONDS = env_int("ANALYSIS_SESSION_TTL_MINUTES", 180) * 60
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-nano")
OPENAI_TRANSCRIPTION_MODEL = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_VALIDATE_TIMEOUT_SECONDS = env_int("OPENAI_VALIDATE_TIMEOUT_SECONDS", 15)
OPENAI_CHAT_TIMEOUT_SECONDS = env_int("OPENAI_CHAT_TIMEOUT_SECONDS", 90)
OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS = env_int("OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS", 120)
AUTH_TOKEN_TTL_SECONDS = env_int("AUTH_TOKEN_TTL_SECONDS", 86400)
CHAT_INPUT_COST_PER_1K = env_float("OPENAI_CHAT_INPUT_COST_PER_1K", 0)
CHAT_OUTPUT_COST_PER_1K = env_float("OPENAI_CHAT_OUTPUT_COST_PER_1K", 0)
PASSWORD_HASH_METHOD = (os.getenv("PASSWORD_HASH_METHOD", "scrypt").strip() or "scrypt").lower()
if PASSWORD_HASH_METHOD not in {"scrypt", "pbkdf2:sha256"}:
    app.logger.warning("PASSWORD_HASH_METHOD invalido (%s). Usando scrypt.", PASSWORD_HASH_METHOD)
    PASSWORD_HASH_METHOD = "scrypt"
PASSWORD_SALT_LENGTH = env_int("PASSWORD_SALT_LENGTH", 16)
PASSWORD_MIN_LENGTH = env_int("PASSWORD_MIN_LENGTH", 10)
if PASSWORD_MIN_LENGTH < 8:
    app.logger.warning("PASSWORD_MIN_LENGTH muito baixo (%s). Ajustando para 8.", PASSWORD_MIN_LENGTH)
    PASSWORD_MIN_LENGTH = 8
PASSWORD_PEPPER = os.getenv("PASSWORD_PEPPER", "")
ALLOWED_EXTENSIONS = {
    ext.strip().lower()
    for ext in os.getenv("ALLOWED_VIDEO_EXTENSIONS", "mp4,avi,mov,mkv").split(",")
    if ext.strip()
}

RATE_LIMIT_UPLOAD_PER_MIN = env_int("RATE_LIMIT_UPLOAD_PER_MIN", 10)
RATE_LIMIT_ANALYZE_PER_MIN = env_int("RATE_LIMIT_ANALYZE_PER_MIN", 5)
RATE_LIMIT_FOLLOWUP_PER_MIN = env_int("RATE_LIMIT_FOLLOWUP_PER_MIN", 30)
RATE_LIMIT_AUTH_REGISTER_PER_MIN = env_int("RATE_LIMIT_AUTH_REGISTER_PER_MIN", 10)
RATE_LIMIT_AUTH_LOGIN_PER_MIN = env_int("RATE_LIMIT_AUTH_LOGIN_PER_MIN", 15)
REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_PREFIX = os.getenv("REDIS_PREFIX", "meeting_analysis")
INTERNAL_ADMIN_KEY = os.getenv("INTERNAL_ADMIN_KEY", "").strip()
USAGE_DASHBOARD_DEFAULT_DAYS = env_int("USAGE_DASHBOARD_DEFAULT_DAYS", 7)
USAGE_DASHBOARD_MAX_DAYS = env_int("USAGE_DASHBOARD_MAX_DAYS", 90)
ALEMBIC_HEAD_REVISION = os.getenv("ALEMBIC_HEAD_REVISION", "20260312_0001")
SERVE_FRONTEND_FROM_FLASK = parse_bool(os.getenv("SERVE_FRONTEND_FROM_FLASK", "0"))


def current_max_file_size_mb():
    value = env_int("MAX_FILE_SIZE_MB", DEFAULT_MAX_FILE_SIZE_MB)
    if value < 1:
        app.logger.warning("MAX_FILE_SIZE_MB invalido (%s). Usando default=%s", value, DEFAULT_MAX_FILE_SIZE_MB)
        return DEFAULT_MAX_FILE_SIZE_MB
    return value


def current_max_file_size_bytes():
    return current_max_file_size_mb() * 1024 * 1024


def current_max_video_duration_seconds():
    value = env_int("MAX_VIDEO_DURATION_SECONDS", DEFAULT_MAX_VIDEO_DURATION_SECONDS)
    if value < 1:
        app.logger.warning(
            "MAX_VIDEO_DURATION_SECONDS invalido (%s). Usando default=%s",
            value,
            DEFAULT_MAX_VIDEO_DURATION_SECONDS
        )
        return DEFAULT_MAX_VIDEO_DURATION_SECONDS
    return value


app.config["MAX_CONTENT_LENGTH"] = current_max_file_size_bytes()


def build_fernet():
    configured_key = os.getenv("KEY_ENCRYPTION_MASTER_KEY", "").strip()
    if configured_key:
        key_bytes = configured_key.encode("utf-8")
    else:
        # Fallback deterministic para dev; em producao prefira KEY_ENCRYPTION_MASTER_KEY.
        digest = hashlib.sha256(f"{SECRET_KEY}:openai-key-encryption".encode("utf-8")).digest()
        key_bytes = base64.urlsafe_b64encode(digest)

    try:
        return Fernet(key_bytes)
    except Exception as error:  # pragma: no cover
        raise RuntimeError("KEY_ENCRYPTION_MASTER_KEY invalida para Fernet") from error


FERNET = build_fernet()
TOKEN_SERIALIZER = URLSafeTimedSerializer(SECRET_KEY, salt="auth-token-v1")

rate_limit_lock = threading.Lock()
rate_limit_buckets = {}


def build_redis_client():
    if not REDIS_URL:
        return None

    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        return client
    except Exception:
        app.logger.exception("Falha ao conectar ao Redis. Fallback para memoria/local.")
        return None


REDIS_CLIENT = build_redis_client()


def now_ts():
    return int(time.time())


def error_response(status_code, message, code):
    return jsonify({"success": False, "error": message, "code": code}), status_code


@app.before_request
def refresh_runtime_limits():
    # Permite refletir ajustes de .env sem reiniciar o processo em ambiente de dev.
    load_dotenv(dotenv_path=ENV_FILE_PATH, override=True)
    app.config["MAX_CONTENT_LENGTH"] = current_max_file_size_bytes()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS user_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            key_ciphertext TEXT NOT NULL,
            key_last4 TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            rotated_at INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_user_api_keys_active
            ON user_api_keys(user_id, provider)
            WHERE is_active = 1;

        CREATE TABLE IF NOT EXISTS analysis_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            video_path TEXT NOT NULL,
            transcription_json TEXT NOT NULL,
            history_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_sessions_user
            ON analysis_sessions(user_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id TEXT,
            endpoint TEXT NOT NULL,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            http_status INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(session_id) REFERENCES analysis_sessions(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_usage_events_user
            ON usage_events(user_id, created_at DESC);
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        )
        """
    )
    current_version = db.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    if current_version is None:
        db.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (ALEMBIC_HEAD_REVISION,))
    db.commit()
    db.close()


def normalize_email(value):
    email = str(value or "").strip().lower()
    if not email or "@" not in email:
        raise ApiError(400, "Email invalido.", "invalid_email")
    return email


def validate_password_strength(value):
    password = str(value or "")

    if len(password) < PASSWORD_MIN_LENGTH:
        raise ApiError(
            400,
            f"Senha deve ter pelo menos {PASSWORD_MIN_LENGTH} caracteres.",
            "weak_password"
        )
    if len(password) > 128:
        raise ApiError(400, "Senha deve ter no maximo 128 caracteres.", "weak_password")
    if re.search(r"\s", password):
        raise ApiError(400, "Senha nao pode conter espacos.", "weak_password")
    if not re.search(r"[a-z]", password):
        raise ApiError(400, "Senha deve conter letra minuscula.", "weak_password")
    if not re.search(r"[A-Z]", password):
        raise ApiError(400, "Senha deve conter letra maiuscula.", "weak_password")
    if not re.search(r"\d", password):
        raise ApiError(400, "Senha deve conter numero.", "weak_password")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ApiError(400, "Senha deve conter simbolo.", "weak_password")

    return password


def _password_material(password):
    if PASSWORD_PEPPER:
        return f"{password}{PASSWORD_PEPPER}"
    return password


def hash_password(password):
    return generate_password_hash(
        _password_material(password),
        method=PASSWORD_HASH_METHOD,
        salt_length=PASSWORD_SALT_LENGTH
    )


def verify_password(password, stored_hash):
    candidate = _password_material(password)
    if check_password_hash(stored_hash, candidate):
        return True, False

    # Compatibilidade com contas antigas sem pepper.
    if PASSWORD_PEPPER and check_password_hash(stored_hash, password):
        return True, True

    return False, False


def password_hash_needs_upgrade(stored_hash):
    return not str(stored_hash or "").startswith(PASSWORD_HASH_METHOD)


def issue_auth_token(user_id, email):
    return TOKEN_SERIALIZER.dumps({"user_id": user_id, "email": email})


def decode_auth_token(token):
    if not token:
        raise ApiError(401, "Nao autenticado.", "unauthenticated")

    try:
        payload = TOKEN_SERIALIZER.loads(token, max_age=AUTH_TOKEN_TTL_SECONDS)
    except SignatureExpired as error:
        raise ApiError(401, "Sessao expirada. Faca login novamente.", "token_expired") from error
    except BadSignature as error:
        raise ApiError(401, "Token de autenticacao invalido.", "invalid_token") from error

    user_id = payload.get("user_id")
    if not user_id:
        raise ApiError(401, "Token de autenticacao invalido.", "invalid_token")

    return int(user_id)


def get_user_by_id(user_id):
    db = get_db()
    return db.execute(
        "SELECT id, email, status, created_at FROM users WHERE id = ? LIMIT 1",
        (user_id,)
    ).fetchone()


def get_user_by_email(email):
    db = get_db()
    return db.execute(
        "SELECT id, email, password_hash, status FROM users WHERE email = ? LIMIT 1",
        (email,)
    ).fetchone()


def require_auth(handler=None, *, allow_query_token=False):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            token = ""

            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1].strip()
            elif allow_query_token:
                token = str(request.args.get("token", "")).strip()

            if not token:
                raise ApiError(401, "Nao autenticado.", "unauthenticated")

            user_id = decode_auth_token(token)
            user = get_user_by_id(user_id)
            if user is None or user["status"] != "active":
                raise ApiError(403, "Acesso negado para esta conta.", "forbidden")

            g.current_user = {
                "id": int(user["id"]),
                "email": user["email"]
            }
            return fn(*args, **kwargs)

        return wrapper

    if handler is not None:
        return decorator(handler)
    return decorator


def mask_api_key(api_key):
    key = str(api_key or "").strip()
    if not key:
        return ""
    last4 = key[-4:] if len(key) >= 4 else key
    return f"sk-...{last4}"


def encrypt_api_key(api_key):
    return FERNET.encrypt(api_key.encode("utf-8")).decode("utf-8")


def decrypt_api_key(ciphertext):
    try:
        return FERNET.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as error:
        raise ApiError(500, "Falha ao decifrar credencial do usuario.", "credential_decrypt_failed") from error


def validate_openai_api_key(api_key):
    try:
        response = requests.get(
            f"{OPENAI_API_BASE}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=OPENAI_VALIDATE_TIMEOUT_SECONDS
        )
    except requests.RequestException as error:
        raise ApiError(502, "Falha no provedor externo.", "provider_failure") from error

    if response.status_code == 401:
        raise ApiError(400, "Chave OpenAI invalida ou revogada.", "invalid_openai_key")
    if response.status_code == 429:
        raise ApiError(429, "Limite de requisicoes do provedor excedido.", "provider_rate_limited")
    if response.status_code >= 500:
        raise ApiError(502, "Falha no provedor externo.", "provider_failure")
    if not response.ok:
        raise ApiError(400, "Nao foi possivel validar a chave OpenAI.", "openai_key_validation_failed")


def set_active_openai_key_for_user(user_id, api_key):
    db = get_db()
    current_time = now_ts()
    ciphertext = encrypt_api_key(api_key)
    key_last4 = api_key[-4:]

    db.execute(
        """
        UPDATE user_api_keys
           SET is_active = 0,
               rotated_at = ?
         WHERE user_id = ?
           AND provider = 'openai'
           AND is_active = 1
        """,
        (current_time, user_id)
    )
    db.execute(
        """
        INSERT INTO user_api_keys (
            user_id, provider, key_ciphertext, key_last4, is_active, created_at, rotated_at
        ) VALUES (?, 'openai', ?, ?, 1, ?, ?)
        """,
        (user_id, ciphertext, key_last4, current_time, current_time)
    )
    db.commit()


def revoke_openai_key(user_id):
    db = get_db()
    current_time = now_ts()
    db.execute(
        """
        UPDATE user_api_keys
           SET is_active = 0,
               rotated_at = ?
         WHERE user_id = ?
           AND provider = 'openai'
           AND is_active = 1
        """,
        (current_time, user_id)
    )
    db.commit()


def get_active_openai_key_row(user_id):
    db = get_db()
    return db.execute(
        """
        SELECT id, key_ciphertext, key_last4, created_at, rotated_at
          FROM user_api_keys
         WHERE user_id = ?
           AND provider = 'openai'
           AND is_active = 1
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (user_id,)
    ).fetchone()


def get_active_openai_key_or_error(user_id):
    row = get_active_openai_key_row(user_id)
    if row is None:
        raise ApiError(403, "Cadastre sua chave para usar IA.", "missing_openai_key")
    return decrypt_api_key(row["key_ciphertext"])


def cleanup_expired_analysis_sessions():
    db = get_db()
    current_time = now_ts()
    expired_ids = []

    if REDIS_CLIENT is not None:
        rows = db.execute(
            """
            SELECT id
              FROM analysis_sessions
             WHERE status = 'active'
               AND expires_at <= ?
            """,
            (current_time,)
        ).fetchall()
        expired_ids = [row["id"] for row in rows]

    db.execute(
        """
        UPDATE analysis_sessions
           SET status = 'expired',
               updated_at = ?
         WHERE status = 'active'
           AND expires_at <= ?
        """,
        (current_time, current_time)
    )
    db.commit()

    for analysis_id in expired_ids:
        invalidate_cached_analysis_session(analysis_id)


def create_analysis_session(user_id, video_path, segments, history):
    cleanup_expired_analysis_sessions()

    analysis_id = str(uuid.uuid4())
    current_time = now_ts()
    expires_at = current_time + ANALYSIS_SESSION_TTL_SECONDS

    db = get_db()
    db.execute(
        """
        INSERT INTO analysis_sessions (
            id, user_id, video_path, transcription_json, history_json,
            status, created_at, updated_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (
            analysis_id,
            user_id,
            video_path,
            json.dumps(segments, ensure_ascii=False),
            json.dumps(history, ensure_ascii=False),
            current_time,
            current_time,
            expires_at
        )
    )
    db.commit()

    cache_analysis_session(
        analysis_id,
        {
            "id": analysis_id,
            "user_id": int(user_id),
            "video_path": video_path,
            "segments": segments,
            "history": history,
            "expires_at": int(expires_at)
        },
        expires_at
    )

    return analysis_id


def get_analysis_session(user_id, analysis_id):
    cleanup_expired_analysis_sessions()

    cached = get_cached_analysis_session(analysis_id)
    if cached is not None:
        if int(cached.get("user_id", -1)) != int(user_id):
            raise ApiError(403, "Sem permissao para acessar essa sessao.", "forbidden")
        return cached

    db = get_db()
    row = db.execute(
        """
        SELECT id, user_id, video_path, transcription_json, history_json, status,
               created_at, updated_at, expires_at
          FROM analysis_sessions
         WHERE id = ?
           AND user_id = ?
           AND status = 'active'
         LIMIT 1
        """,
        (analysis_id, user_id)
    ).fetchone()

    if row is None:
        return None

    current_time = now_ts()
    db.execute(
        "UPDATE analysis_sessions SET updated_at = ? WHERE id = ?",
        (current_time, analysis_id)
    )
    db.commit()

    try:
        transcription = json.loads(row["transcription_json"])
    except json.JSONDecodeError:
        transcription = []

    try:
        history = json.loads(row["history_json"])
    except json.JSONDecodeError:
        history = []

    session = {
        "id": row["id"],
        "user_id": int(row["user_id"]),
        "video_path": row["video_path"],
        "segments": transcription,
        "history": history,
        "expires_at": int(row["expires_at"])
    }
    cache_analysis_session(row["id"], session, row["expires_at"])
    return session


def update_analysis_history(analysis_id, history, expires_at):
    clipped_history = history[-(MAX_HISTORY_MESSAGES * 2):]
    db = get_db()
    db.execute(
        """
        UPDATE analysis_sessions
           SET history_json = ?,
               updated_at = ?
         WHERE id = ?
        """,
        (json.dumps(clipped_history, ensure_ascii=False), now_ts(), analysis_id)
    )
    db.commit()

    cached = get_cached_analysis_session(analysis_id)
    if cached is not None:
        cached["history"] = clipped_history
        cache_analysis_session(analysis_id, cached, expires_at)


def extract_usage_tokens(usage):
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    output_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    try:
        return int(input_tokens), int(output_tokens)
    except (TypeError, ValueError):
        return 0, 0


def estimate_chat_cost(input_tokens, output_tokens):
    input_cost = (float(input_tokens) / 1000.0) * CHAT_INPUT_COST_PER_1K
    output_cost = (float(output_tokens) / 1000.0) * CHAT_OUTPUT_COST_PER_1K
    return round(input_cost + output_cost, 6)


def log_usage_event(user_id, session_id, endpoint, model, usage, http_status):
    input_tokens, output_tokens = extract_usage_tokens(usage)
    estimated_cost = estimate_chat_cost(input_tokens, output_tokens)

    db = get_db()
    db.execute(
        """
        INSERT INTO usage_events (
            user_id, session_id, endpoint, model,
            input_tokens, output_tokens, estimated_cost,
            http_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            session_id,
            endpoint,
            model,
            input_tokens,
            output_tokens,
            estimated_cost,
            http_status,
            now_ts()
        )
    )
    db.commit()


def safe_log_usage_event(user_id, session_id, endpoint, model, usage, http_status):
    try:
        log_usage_event(user_id, session_id, endpoint, model, usage, http_status)
    except Exception:
        app.logger.exception("Falha ao gravar usage_event")


def redis_key(*parts):
    suffix = ":".join(str(part).strip() for part in parts if str(part).strip())
    return f"{REDIS_PREFIX}:{suffix}" if suffix else REDIS_PREFIX


def get_client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def enforce_rate_limit(scope, max_requests, window_seconds=60, subject=None):
    if max_requests <= 0:
        return

    user = getattr(g, "current_user", None)
    identity = str(subject).strip() if subject is not None else (user["id"] if user else "anonymous")
    remote_addr = get_client_ip()

    if REDIS_CLIENT is not None:
        window_bucket = int(time.time() // window_seconds)
        key = redis_key("ratelimit", scope, identity, remote_addr, window_bucket)
        try:
            count = REDIS_CLIENT.incr(key)
            if count == 1:
                REDIS_CLIENT.expire(key, window_seconds)
            if count > max_requests:
                raise ApiError(429, "Limite de requisicoes excedido. Tente novamente em instantes.", "rate_limited")
            return
        except redis.RedisError:
            app.logger.exception("Erro no Redis para rate limit. Fallback para memoria local.")

    key = f"{scope}:{identity}:{remote_addr}"

    current_time = time.time()
    with rate_limit_lock:
        entries = rate_limit_buckets.get(key, [])
        entries = [timestamp for timestamp in entries if current_time - timestamp < window_seconds]
        if len(entries) >= max_requests:
            raise ApiError(429, "Limite de requisicoes excedido. Tente novamente em instantes.", "rate_limited")
        entries.append(current_time)
        rate_limit_buckets[key] = entries


def cache_analysis_session(analysis_id, payload, expires_at):
    if REDIS_CLIENT is None:
        return

    ttl = max(1, int(expires_at) - now_ts())
    key = redis_key("analysis_session", analysis_id)

    try:
        REDIS_CLIENT.setex(key, ttl, json.dumps(payload, ensure_ascii=False))
    except redis.RedisError:
        app.logger.exception("Falha ao cachear analysis_session no Redis")


def get_cached_analysis_session(analysis_id):
    if REDIS_CLIENT is None:
        return None

    key = redis_key("analysis_session", analysis_id)
    try:
        cached = REDIS_CLIENT.get(key)
        if not cached:
            return None
        data = json.loads(cached)
        if int(data.get("expires_at", 0)) <= now_ts():
            REDIS_CLIENT.delete(key)
            return None
        return data
    except (redis.RedisError, json.JSONDecodeError, TypeError, ValueError):
        app.logger.exception("Falha ao recuperar analysis_session no Redis")
        return None


def invalidate_cached_analysis_session(analysis_id):
    if REDIS_CLIENT is None:
        return
    key = redis_key("analysis_session", analysis_id)
    try:
        REDIS_CLIENT.delete(key)
    except redis.RedisError:
        app.logger.exception("Falha ao invalidar analysis_session no Redis")


def user_upload_dir(user_id):
    path = os.path.join(UPLOAD_FOLDER, f"user_{user_id}")
    os.makedirs(path, exist_ok=True)
    return path


def allowed_file_extension(filename):
    if "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    return extension in ALLOWED_EXTENSIONS


def validate_uploaded_video(file_path):
    try:
        probe = ffmpeg.probe(file_path)
    except ffmpeg.Error as error:
        stderr = (error.stderr or b"").decode("utf-8", errors="ignore")
        raise ApiError(400, f"Arquivo de video invalido: {stderr[:180]}", "invalid_video_file") from error

    streams = probe.get("streams", [])
    has_video_stream = any(stream.get("codec_type") == "video" for stream in streams)
    if not has_video_stream:
        raise ApiError(400, "Arquivo enviado nao possui stream de video.", "invalid_video_file")

    duration = float(probe.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise ApiError(400, "Nao foi possivel identificar duracao do video.", "invalid_video_file")

    max_duration_seconds = current_max_video_duration_seconds()
    if duration > max_duration_seconds:
        raise ApiError(
            400,
            f"Duracao maxima excedida. Limite atual: {max_duration_seconds} segundos.",
            "video_too_long"
        )


def resolve_user_video_path(user_id, stored_filename="", file_path=""):
    upload_root = user_upload_dir(user_id)

    if stored_filename:
        safe_name = secure_filename(stored_filename)
        if not safe_name or safe_name != stored_filename:
            raise ApiError(400, "Identificador de arquivo invalido.", "invalid_file_reference")
        absolute_path = os.path.abspath(os.path.join(upload_root, safe_name))
    elif file_path:
        absolute_path = os.path.abspath(file_path)
    else:
        raise ApiError(400, "Referencia de arquivo obrigatoria.", "missing_file_reference")

    if os.path.commonpath([absolute_path, upload_root]) != upload_root:
        raise ApiError(403, "Sem permissao para acessar esse arquivo.", "forbidden")

    if not os.path.exists(absolute_path):
        raise ApiError(400, "Arquivo de video nao encontrado.", "file_not_found")

    return absolute_path


def build_transcript_with_times(segments):
    return "\n".join(
        f"[{segment.get('start', 0):.2f}-{segment.get('end', 0):.2f}] {segment.get('text', '').strip()}"
        for segment in segments
        if str(segment.get("text", "")).strip()
    )


def ask_openai(api_key, segments, user_prompt, history=None):
    transcript_with_times = build_transcript_with_times(segments)
    messages = [
        {
            "role": "system",
            "content": (
                "Voce e um assistente que responde perguntas sobre a transcricao de um video. "
                "Sempre cite timestamps relevantes no formato [inicio-fim] quando possivel. "
                "Se o usuario pedir checklist, lista de tarefas, proximos passos ou plano de acao, "
                "responda de forma estruturada e pratica."
            )
        },
        {
            "role": "system",
            "content": (
                "Use somente a transcricao abaixo como fonte de verdade. "
                "Se algo nao estiver na transcricao, sinalize a limitacao.\n\n"
                f"Transcricao:\n{transcript_with_times}"
            )
        }
    ]

    if history:
        for message in history[-MAX_HISTORY_MESSAGES:]:
            role = message.get("role")
            content = str(message.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_prompt})

    response = requests.post(
        f"{OPENAI_API_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": OPENAI_CHAT_MODEL,
            "messages": messages,
            "temperature": 0.2
        },
        timeout=OPENAI_CHAT_TIMEOUT_SECONDS
    )

    if response.status_code == 401:
        raise ApiError(400, "Sua chave OpenAI foi rejeitada. Atualize em Integracoes.", "invalid_openai_key")
    if response.status_code == 429:
        raise ApiError(429, "OpenAI retornou limite excedido para esta chave.", "provider_rate_limited")
    if response.status_code >= 500:
        raise ApiError(502, "Falha no provedor externo.", "provider_failure")
    if not response.ok:
        raise ApiError(502, "Falha ao comunicar com OpenAI.", "provider_failure")

    payload = response.json()
    choices = payload.get("choices", [])
    if not choices:
        raise ApiError(502, "Resposta vazia da OpenAI.", "provider_empty_response")

    content = choices[0].get("message", {}).get("content", "")
    return {
        "answer": content,
        "usage": payload.get("usage", {}),
        "model": payload.get("model", OPENAI_CHAT_MODEL)
    }


def split_video_ffmpeg(input_path, max_size_mb=None):
    max_size_mb = max_size_mb or current_max_file_size_mb()
    file_size = os.path.getsize(input_path)
    max_size = max_size_mb * 1024 * 1024
    if file_size <= max_size:
        return [input_path]

    probe = ffmpeg.probe(input_path)
    duration = float(probe["format"].get("duration") or 0)
    if duration <= 0:
        return [input_path]

    num_parts = max(1, math.ceil(file_size / max_size))
    part_duration = duration / num_parts
    part_paths = []

    for index in range(num_parts):
        start = index * part_duration
        output_path = f"{input_path}_part{index + 1}.mp4"
        (
            ffmpeg
            .input(input_path, ss=start, t=part_duration)
            .output(output_path, c="copy")
            .run(overwrite_output=True, quiet=True)
        )
        part_paths.append(output_path)

    return part_paths


def transcribe_with_openai(api_key, video_path):
    with open(video_path, "rb") as video_file:
        response = requests.post(
            f"{OPENAI_API_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": video_file},
            data={
                "model": OPENAI_TRANSCRIPTION_MODEL,
                "response_format": "verbose_json"
            },
            timeout=OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS
        )

    if response.status_code == 401:
        raise ApiError(400, "Sua chave OpenAI foi rejeitada. Atualize em Integracoes.", "invalid_openai_key")
    if response.status_code == 413:
        raise ApiError(
            400,
            (
                "OpenAI rejeitou o arquivo para transcricao por tamanho. "
                f"Cada parte enviada ao provedor deve ter no maximo "
                f"{OPENAI_TRANSCRIPTION_PROVIDER_MAX_FILE_SIZE_MB}MB."
            ),
            "openai_transcription_file_too_large"
        )
    if response.status_code == 429:
        raise ApiError(429, "OpenAI retornou limite excedido para esta chave.", "provider_rate_limited")
    if response.status_code >= 500:
        raise ApiError(502, "Falha no provedor externo.", "provider_failure")
    if not response.ok:
        raise ApiError(502, "Falha ao comunicar com OpenAI.", "provider_failure")

    return response.json()


def transcribe_large_video(api_key, video_path):
    part_paths = split_video_ffmpeg(
        video_path,
        max_size_mb=OPENAI_TRANSCRIPTION_PROVIDER_MAX_FILE_SIZE_MB
    )
    all_segments = []
    time_offset = 0.0

    try:
        for part_path in part_paths:
            result = transcribe_with_openai(api_key, part_path)
            segments = result.get("segments", [])

            for segment in segments:
                segment_copy = dict(segment)
                segment_copy["start"] = float(segment_copy.get("start", 0)) + time_offset
                segment_copy["end"] = float(segment_copy.get("end", 0)) + time_offset
                all_segments.append(segment_copy)

            if segments:
                time_offset = float(all_segments[-1].get("end", time_offset))
    finally:
        for part_path in part_paths:
            if part_path != video_path and os.path.exists(part_path):
                os.remove(part_path)

    return all_segments


def json_success(payload):
    data = {"success": True}
    data.update(payload)
    return jsonify(data)


def is_api_like_path(path):
    normalized = f"/{str(path or '').lstrip('/')}"
    return normalized in {"/upload", "/analyze", "/followup"} or normalized.startswith(
        ("/auth/", "/integrations/", "/video/", "/usage/", "/health")
    )


def parse_dashboard_days(raw_value):
    try:
        value = int(str(raw_value or USAGE_DASHBOARD_DEFAULT_DAYS).strip())
    except (TypeError, ValueError):
        raise ApiError(400, "Parametro days invalido.", "invalid_days")

    if value < 1:
        raise ApiError(400, "Parametro days deve ser >= 1.", "invalid_days")
    if value > USAGE_DASHBOARD_MAX_DAYS:
        value = USAGE_DASHBOARD_MAX_DAYS
    return value


def get_usage_summary_for_user(user_id, start_ts):
    db = get_db()
    row = db.execute(
        """
        SELECT COUNT(*) AS requests,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(estimated_cost), 0) AS estimated_cost,
               COALESCE(SUM(CASE WHEN http_status >= 400 THEN 1 ELSE 0 END), 0) AS errors
          FROM usage_events
         WHERE user_id = ?
           AND created_at >= ?
        """,
        (user_id, start_ts)
    ).fetchone()

    endpoint_rows = db.execute(
        """
        SELECT endpoint,
               COUNT(*) AS requests,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(estimated_cost), 0) AS estimated_cost,
               COALESCE(SUM(CASE WHEN http_status >= 400 THEN 1 ELSE 0 END), 0) AS errors
          FROM usage_events
         WHERE user_id = ?
           AND created_at >= ?
         GROUP BY endpoint
         ORDER BY requests DESC
        """,
        (user_id, start_ts)
    ).fetchall()

    timeline_rows = db.execute(
        """
        SELECT strftime('%Y-%m-%d', created_at, 'unixepoch') AS day,
               COUNT(*) AS requests,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(estimated_cost), 0) AS estimated_cost,
               COALESCE(SUM(CASE WHEN http_status >= 400 THEN 1 ELSE 0 END), 0) AS errors
          FROM usage_events
         WHERE user_id = ?
           AND created_at >= ?
         GROUP BY day
         ORDER BY day ASC
        """,
        (user_id, start_ts)
    ).fetchall()

    return {
        "summary": {
            "requests": int(row["requests"] or 0),
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "estimated_cost": float(row["estimated_cost"] or 0),
            "errors": int(row["errors"] or 0)
        },
        "by_endpoint": [
            {
                "endpoint": endpoint_row["endpoint"],
                "requests": int(endpoint_row["requests"] or 0),
                "input_tokens": int(endpoint_row["input_tokens"] or 0),
                "output_tokens": int(endpoint_row["output_tokens"] or 0),
                "estimated_cost": float(endpoint_row["estimated_cost"] or 0),
                "errors": int(endpoint_row["errors"] or 0)
            }
            for endpoint_row in endpoint_rows
        ],
        "timeline": [
            {
                "day": timeline_row["day"],
                "requests": int(timeline_row["requests"] or 0),
                "input_tokens": int(timeline_row["input_tokens"] or 0),
                "output_tokens": int(timeline_row["output_tokens"] or 0),
                "estimated_cost": float(timeline_row["estimated_cost"] or 0),
                "errors": int(timeline_row["errors"] or 0)
            }
            for timeline_row in timeline_rows
        ]
    }


def get_global_usage_top_users(start_ts, limit):
    db = get_db()
    rows = db.execute(
        """
        SELECT usage_events.user_id AS user_id,
               users.email AS email,
               COUNT(*) AS requests,
               COALESCE(SUM(usage_events.input_tokens), 0) AS input_tokens,
               COALESCE(SUM(usage_events.output_tokens), 0) AS output_tokens,
               COALESCE(SUM(usage_events.estimated_cost), 0) AS estimated_cost,
               COALESCE(SUM(CASE WHEN usage_events.http_status >= 400 THEN 1 ELSE 0 END), 0) AS errors
          FROM usage_events
          JOIN users ON users.id = usage_events.user_id
         WHERE usage_events.created_at >= ?
         GROUP BY usage_events.user_id, users.email
         ORDER BY requests DESC
         LIMIT ?
        """,
        (start_ts, limit)
    ).fetchall()

    return [
        {
            "user_id": int(row["user_id"]),
            "email": row["email"],
            "requests": int(row["requests"] or 0),
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "estimated_cost": float(row["estimated_cost"] or 0),
            "errors": int(row["errors"] or 0)
        }
        for row in rows
    ]


@app.route("/auth/register", methods=["POST"])
def auth_register():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email"))
    password = validate_password_strength(payload.get("password"))
    enforce_rate_limit(
        "auth_register",
        RATE_LIMIT_AUTH_REGISTER_PER_MIN,
        window_seconds=60,
        subject=email
    )

    password_hash = hash_password(password)
    created_at = now_ts()

    db = get_db()
    try:
        cursor = db.execute(
            """
            INSERT INTO users (email, password_hash, created_at, status)
            VALUES (?, ?, ?, 'active')
            """,
            (email, password_hash, created_at)
        )
        db.commit()
    except sqlite3.IntegrityError as error:
        raise ApiError(400, "Este email ja esta cadastrado.", "email_already_exists") from error

    user_id = int(cursor.lastrowid)
    token = issue_auth_token(user_id, email)

    return json_success({
        "token": token,
        "user": {"id": user_id, "email": email}
    })


@app.route("/auth/login", methods=["POST"])
def auth_login():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email"))
    password = str(payload.get("password", ""))
    enforce_rate_limit(
        "auth_login",
        RATE_LIMIT_AUTH_LOGIN_PER_MIN,
        window_seconds=60,
        subject=email
    )

    user = get_user_by_email(email)
    if user is None:
        raise ApiError(401, "Email ou senha invalidos.", "invalid_credentials")
    if user["status"] != "active":
        raise ApiError(403, "Conta sem permissao para login.", "forbidden")

    password_ok, used_legacy_without_pepper = verify_password(password, user["password_hash"])
    if not password_ok:
        raise ApiError(401, "Email ou senha invalidos.", "invalid_credentials")

    if used_legacy_without_pepper or password_hash_needs_upgrade(user["password_hash"]):
        db = get_db()
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), int(user["id"]))
        )
        db.commit()

    token = issue_auth_token(int(user["id"]), user["email"])
    return json_success({
        "token": token,
        "user": {"id": int(user["id"]), "email": user["email"]}
    })


@app.route("/auth/me", methods=["GET"])
@require_auth
def auth_me():
    user = g.current_user
    key_row = get_active_openai_key_row(user["id"])
    return json_success({
        "user": user,
        "has_openai_key": key_row is not None
    })


@app.route("/auth/password-policy", methods=["GET"])
def auth_password_policy():
    return json_success({
        "min_length": PASSWORD_MIN_LENGTH,
        "max_length": 128,
        "requires_lowercase": True,
        "requires_uppercase": True,
        "requires_number": True,
        "requires_symbol": True,
        "no_whitespace": True
    })


@app.route("/health", methods=["GET"])
def health_check():
    db_ok = True
    db_error = ""
    try:
        db = get_db()
        db.execute("SELECT 1").fetchone()
    except Exception as error:
        db_ok = False
        db_error = str(error)

    redis_ok = REDIS_CLIENT is not None

    max_file_size_mb = current_max_file_size_mb()
    max_video_duration_seconds = current_max_video_duration_seconds()

    status_code = 200 if db_ok else 500
    response = jsonify({
        "success": db_ok,
        "status": "ok" if db_ok else "degraded",
        "max_file_size_mb": max_file_size_mb,
        "max_video_duration_seconds": max_video_duration_seconds,
        "serve_frontend_from_flask": SERVE_FRONTEND_FROM_FLASK,
        "services": {
            "database": "ok" if db_ok else "error",
            "redis": "ok" if redis_ok else "disabled_or_unavailable"
        },
        "database_error": db_error or None
    })
    response.headers["Cache-Control"] = "no-store"
    return response, status_code


@app.route("/integrations/openai-key", methods=["POST"])
@require_auth
def upsert_openai_key():
    payload = request.get_json(silent=True) or {}
    api_key = str(payload.get("api_key", "")).strip()
    if not api_key:
        raise ApiError(400, "api_key e obrigatoria.", "missing_api_key")

    validate_openai_api_key(api_key)
    set_active_openai_key_for_user(g.current_user["id"], api_key)

    return json_success({
        "provider": "openai",
        "is_active": True,
        "masked_key": mask_api_key(api_key)
    })


@app.route("/integrations/openai-key/status", methods=["GET"])
@require_auth
def openai_key_status():
    row = get_active_openai_key_row(g.current_user["id"])
    if row is None:
        return json_success({
            "provider": "openai",
            "is_active": False,
            "masked_key": None,
            "created_at": None,
            "rotated_at": None
        })

    return json_success({
        "provider": "openai",
        "is_active": True,
        "masked_key": f"sk-...{row['key_last4']}",
        "created_at": int(row["created_at"]),
        "rotated_at": int(row["rotated_at"] or row["created_at"])
    })


@app.route("/integrations/openai-key", methods=["DELETE"])
@require_auth
def delete_openai_key():
    revoke_openai_key(g.current_user["id"])
    return json_success({"provider": "openai", "is_active": False})


@app.route("/upload", methods=["POST"])
@require_auth
def upload_video():
    enforce_rate_limit("upload", RATE_LIMIT_UPLOAD_PER_MIN)

    if "file" not in request.files:
        raise ApiError(400, "Nenhum arquivo enviado.", "missing_file")

    file = request.files["file"]
    if file.filename == "":
        raise ApiError(400, "Nome de arquivo vazio.", "empty_filename")

    original_name = file.filename
    safe_original_name = secure_filename(original_name)
    if not safe_original_name:
        raise ApiError(400, "Nome de arquivo invalido.", "invalid_filename")
    if not allowed_file_extension(safe_original_name):
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ApiError(400, f"Extensao nao permitida. Use: {allowed}.", "invalid_extension")

    extension = safe_original_name.rsplit(".", 1)[1].lower()
    stored_filename = f"{uuid.uuid4().hex}.{extension}"

    destination_dir = user_upload_dir(g.current_user["id"])
    file_path = os.path.join(destination_dir, stored_filename)
    file.save(file_path)

    max_file_size_bytes = current_max_file_size_bytes()
    max_file_size_mb = current_max_file_size_mb()
    if os.path.getsize(file_path) > max_file_size_bytes:
        os.remove(file_path)
        raise ApiError(413, f"Arquivo excede o limite de {max_file_size_mb}MB.", "file_too_large")

    try:
        validate_uploaded_video(file_path)
    except ApiError:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

    return json_success({
        "filename": safe_original_name,
        "stored_filename": stored_filename
    })


@app.route("/video/<path:filename>", methods=["GET"])
@require_auth(allow_query_token=True)
def serve_video(filename):
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename:
        raise ApiError(400, "Arquivo solicitado invalido.", "invalid_file_reference")

    user_dir = user_upload_dir(g.current_user["id"])
    absolute_path = os.path.abspath(os.path.join(user_dir, safe_filename))

    if os.path.commonpath([absolute_path, user_dir]) != user_dir:
        raise ApiError(403, "Sem permissao para acessar esse arquivo.", "forbidden")
    if not os.path.exists(absolute_path):
        raise ApiError(404, "Video nao encontrado.", "file_not_found")

    return send_from_directory(user_dir, safe_filename)


@app.route("/analyze", methods=["POST"])
@require_auth
def analyze():
    enforce_rate_limit("analyze", RATE_LIMIT_ANALYZE_PER_MIN)

    payload = request.get_json(silent=True) or {}
    stored_filename = str(payload.get("stored_filename", "")).strip()
    file_path = str(payload.get("file_path", "")).strip()
    question = str(payload.get("question", "")).strip()

    video_path = resolve_user_video_path(g.current_user["id"], stored_filename=stored_filename, file_path=file_path)

    initial_prompt = question or (
        "Resuma os principais pontos da reuniao em bullets e destaque decisoes, riscos e proximos passos. "
        "Inclua timestamps relevantes."
    )

    api_key = get_active_openai_key_or_error(g.current_user["id"])

    try:
        segments = transcribe_large_video(api_key, video_path)
        chat_result = ask_openai(api_key, segments, initial_prompt)
        insights = chat_result["answer"]
        initial_history = [
            {"role": "user", "content": initial_prompt},
            {"role": "assistant", "content": insights}
        ]
        analysis_id = create_analysis_session(
            g.current_user["id"],
            video_path,
            segments,
            initial_history
        )

        safe_log_usage_event(
            user_id=g.current_user["id"],
            session_id=analysis_id,
            endpoint="/analyze",
            model=chat_result["model"],
            usage=chat_result["usage"],
            http_status=200
        )
    except ApiError as error:
        safe_log_usage_event(
            user_id=g.current_user["id"],
            session_id=None,
            endpoint="/analyze",
            model=OPENAI_CHAT_MODEL,
            usage={},
            http_status=error.status_code
        )
        raise
    except requests.RequestException as error:
        safe_log_usage_event(
            user_id=g.current_user["id"],
            session_id=None,
            endpoint="/analyze",
            model=OPENAI_CHAT_MODEL,
            usage={},
            http_status=502
        )
        raise ApiError(502, f"Falha ao comunicar com OpenAI: {error}", "provider_failure") from error

    return json_success({
        "analysis_id": analysis_id,
        "insights": insights,
        "transcription": segments,
        "timestamps": []
    })


@app.route("/followup", methods=["POST"])
@require_auth
def followup():
    enforce_rate_limit("followup", RATE_LIMIT_FOLLOWUP_PER_MIN)

    payload = request.get_json(silent=True) or {}
    analysis_id = str(payload.get("analysis_id", "")).strip()
    question = str(payload.get("question", "")).strip()

    if not analysis_id:
        raise ApiError(400, "analysis_id e obrigatorio.", "missing_analysis_id")
    if not question:
        raise ApiError(400, "A pergunta nao pode ser vazia.", "empty_question")

    session = get_analysis_session(g.current_user["id"], analysis_id)
    if session is None:
        raise ApiError(
            404,
            "Sessao de analise nao encontrada ou expirada. Rode a analise novamente.",
            "analysis_session_not_found"
        )

    api_key = get_active_openai_key_or_error(g.current_user["id"])

    try:
        chat_result = ask_openai(api_key, session["segments"], question, history=session.get("history", []))
        answer = chat_result["answer"]
    except ApiError as error:
        safe_log_usage_event(
            user_id=g.current_user["id"],
            session_id=analysis_id,
            endpoint="/followup",
            model=OPENAI_CHAT_MODEL,
            usage={},
            http_status=error.status_code
        )
        raise
    except requests.RequestException as error:
        safe_log_usage_event(
            user_id=g.current_user["id"],
            session_id=analysis_id,
            endpoint="/followup",
            model=OPENAI_CHAT_MODEL,
            usage={},
            http_status=502
        )
        raise ApiError(502, f"Falha ao comunicar com OpenAI: {error}", "provider_failure") from error

    history = list(session.get("history", []))
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})
    update_analysis_history(analysis_id, history, session.get("expires_at", now_ts() + ANALYSIS_SESSION_TTL_SECONDS))

    safe_log_usage_event(
        user_id=g.current_user["id"],
        session_id=analysis_id,
        endpoint="/followup",
        model=chat_result["model"],
        usage=chat_result["usage"],
        http_status=200
    )

    return json_success({"answer": answer})


@app.route("/usage/dashboard", methods=["GET"])
@require_auth
def usage_dashboard():
    days = parse_dashboard_days(request.args.get("days"))
    start_ts = now_ts() - (days * 86400)
    user_id = g.current_user["id"]

    usage_payload = get_usage_summary_for_user(user_id, start_ts)
    response_payload = {
        "range_days": days,
        "start_ts": start_ts,
        "end_ts": now_ts(),
        "user_id": user_id,
        **usage_payload
    }

    admin_key = request.headers.get("X-Internal-Admin-Key", "").strip()
    if INTERNAL_ADMIN_KEY and admin_key and admin_key == INTERNAL_ADMIN_KEY:
        response_payload["global_top_users"] = get_global_usage_top_users(start_ts, limit=20)

    return json_success(response_payload)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def frontend_assets(path):
    if not SERVE_FRONTEND_FROM_FLASK:
        return error_response(
            404,
            "Frontend no Flask desativado. Em desenvolvimento, use http://localhost:5173.",
            "frontend_disabled"
        )

    if path and is_api_like_path(path):
        raise ApiError(404, "Recurso nao encontrado.", "not_found")

    target = os.path.join(FRONTEND_DIST, path)
    if path and os.path.exists(target):
        return send_from_directory(FRONTEND_DIST, path)

    index_path = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(FRONTEND_DIST, "index.html")

    return error_response(
        404,
        'Frontend build nao encontrado. Rode "npm run build" em frontend/.',
        "frontend_build_missing"
    )


@app.errorhandler(ApiError)
def handle_api_error(error):
    return error_response(error.status_code, error.message, error.code)


@app.errorhandler(413)
def handle_file_too_large(_error):
    return error_response(
        413,
        f"Arquivo excede o limite de {current_max_file_size_mb()}MB.",
        "file_too_large"
    )


@app.errorhandler(500)
def handle_internal_error(_error):
    return error_response(500, "Erro interno.", "internal_error")


init_db()


if __name__ == "__main__":
    debug_mode = parse_bool(os.getenv("FLASK_DEBUG", "0"))
    app.run(debug=debug_mode)
