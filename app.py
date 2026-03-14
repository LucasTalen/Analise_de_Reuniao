import base64
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from functools import wraps
from urllib.parse import urlparse

import ffmpeg
import redis
import requests
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_FILE_PATH = os.path.join(BASE_DIR, ".env")
ENV_LOCAL_FILE_PATH = os.path.join(BASE_DIR, ".env.local")


def load_project_env():
    load_dotenv(dotenv_path=ENV_FILE_PATH, override=False)
    if os.path.exists(ENV_LOCAL_FILE_PATH):
        load_dotenv(dotenv_path=ENV_LOCAL_FILE_PATH, override=True)


load_project_env()


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

MONGODB_URI = require_env("MONGODB_URI")
MONGODB_SERVER_SELECTION_TIMEOUT_MS = env_int("MONGODB_SERVER_SELECTION_TIMEOUT_MS", 10000)


def resolve_mongodb_db_name():
    configured_name = os.getenv("MONGODB_DB_NAME", "").strip()
    if configured_name:
        return configured_name

    parsed = urlparse(MONGODB_URI)
    path_name = parsed.path.lstrip("/").strip()
    if path_name:
        return path_name

    return "analise_reuniao"


MONGODB_DB_NAME = resolve_mongodb_db_name()

DEFAULT_MAX_FILE_SIZE_MB = 100
DEFAULT_MAX_VIDEO_DURATION_SECONDS = 7200
TRANSIENT_ANALYSIS_VIDEO_PATH = "[transient-upload]"
DEFAULT_ANALYSIS_PROMPT = (
    "Resuma os principais pontos da reuniao em bullets e destaque decisoes, riscos e proximos passos. "
    "Inclua timestamps relevantes."
)
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


def build_mongo_client():
    return MongoClient(
        MONGODB_URI,
        serverSelectionTimeoutMS=MONGODB_SERVER_SELECTION_TIMEOUT_MS,
    )


MONGO_CLIENT = build_mongo_client()
MONGO_DB = MONGO_CLIENT[MONGODB_DB_NAME]


def now_ts():
    return int(time.time())


def error_response(status_code, message, code):
    return jsonify({"success": False, "error": message, "code": code}), status_code


@app.before_request
def refresh_runtime_limits():
    # Permite refletir ajustes de .env sem reiniciar o processo em ambiente de dev.
    load_project_env()
    app.config["MAX_CONTENT_LENGTH"] = current_max_file_size_bytes()


def get_db():
    return MONGO_DB


@app.teardown_appcontext
def close_db(_error):
    return None


def init_db():
    db = get_db()
    db.users.create_index([("email", ASCENDING)], unique=True)
    db.user_api_keys.create_index(
        [("user_id", ASCENDING), ("provider", ASCENDING)],
        unique=True,
        partialFilterExpression={"is_active": True},
    )
    db.analysis_sessions.create_index(
        [("user_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)]
    )
    db.analysis_sessions.create_index([("expires_at", ASCENDING)])
    db.usage_events.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])


def users_collection():
    return get_db().users


def user_api_keys_collection():
    return get_db().user_api_keys


def analysis_sessions_collection():
    return get_db().analysis_sessions


def usage_events_collection():
    return get_db().usage_events


def normalize_user_document(document):
    if document is None:
        return None

    return {
        "id": str(document["_id"]),
        "email": document["email"],
        "password_hash": document.get("password_hash"),
        "status": document.get("status", "active"),
        "created_at": int(document.get("created_at", 0)),
    }


def normalize_api_key_document(document):
    if document is None:
        return None

    return {
        "id": str(document["_id"]),
        "user_id": str(document["user_id"]),
        "provider": document["provider"],
        "key_ciphertext": document["key_ciphertext"],
        "key_last4": document["key_last4"],
        "is_active": bool(document.get("is_active", False)),
        "created_at": int(document.get("created_at", 0)),
        "rotated_at": int(document.get("rotated_at") or document.get("created_at", 0)),
    }


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

    return str(user_id)


def get_user_by_id(user_id):
    document = users_collection().find_one({"_id": str(user_id)})
    user = normalize_user_document(document)
    if user is None:
        return None
    return {
        "id": user["id"],
        "email": user["email"],
        "status": user["status"],
        "created_at": user["created_at"],
    }


def get_user_by_email(email):
    return normalize_user_document(users_collection().find_one({"email": email}))


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
                "id": user["id"],
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
    current_time = now_ts()
    ciphertext = encrypt_api_key(api_key)
    key_last4 = api_key[-4:]
    user_api_keys_collection().update_many(
        {
            "user_id": str(user_id),
            "provider": "openai",
            "is_active": True,
        },
        {
            "$set": {
                "is_active": False,
                "rotated_at": current_time,
            }
        },
    )
    user_api_keys_collection().insert_one(
        {
            "_id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "provider": "openai",
            "key_ciphertext": ciphertext,
            "key_last4": key_last4,
            "is_active": True,
            "created_at": current_time,
            "rotated_at": current_time,
        }
    )


def revoke_openai_key(user_id):
    current_time = now_ts()
    user_api_keys_collection().update_many(
        {
            "user_id": str(user_id),
            "provider": "openai",
            "is_active": True,
        },
        {
            "$set": {
                "is_active": False,
                "rotated_at": current_time,
            }
        },
    )


def get_active_openai_key_row(user_id):
    document = user_api_keys_collection().find_one(
        {
            "user_id": str(user_id),
            "provider": "openai",
            "is_active": True,
        },
        sort=[("created_at", DESCENDING)],
    )
    return normalize_api_key_document(document)


def get_active_openai_key_or_error(user_id):
    row = get_active_openai_key_row(user_id)
    if row is None:
        raise ApiError(403, "Cadastre sua chave para usar IA.", "missing_openai_key")
    return decrypt_api_key(row["key_ciphertext"])


def cleanup_expired_analysis_sessions():
    current_time = now_ts()
    expired_ids = []
    sessions = analysis_sessions_collection()

    if REDIS_CLIENT is not None:
        expired_ids = [
            str(document["_id"])
            for document in sessions.find(
                {"status": "active", "expires_at": {"$lte": current_time}},
                {"_id": 1},
            )
        ]

    sessions.update_many(
        {"status": "active", "expires_at": {"$lte": current_time}},
        {"$set": {"status": "expired", "updated_at": current_time}},
    )

    for analysis_id in expired_ids:
        invalidate_cached_analysis_session(analysis_id)


def create_analysis_session(user_id, source_label, segments, history):
    cleanup_expired_analysis_sessions()

    analysis_id = str(uuid.uuid4())
    current_time = now_ts()
    expires_at = current_time + ANALYSIS_SESSION_TTL_SECONDS

    analysis_sessions_collection().insert_one(
        {
            "_id": analysis_id,
            "user_id": str(user_id),
            "source_label": source_label,
            "segments": segments,
            "history": history,
            "status": "active",
            "created_at": current_time,
            "updated_at": current_time,
            "expires_at": expires_at,
        }
    )

    cache_analysis_session(
        analysis_id,
        {
            "id": analysis_id,
            "user_id": str(user_id),
            "source_label": source_label,
            "segments": segments,
            "history": history,
            "expires_at": int(expires_at),
        },
        expires_at
    )

    return analysis_id


def get_analysis_session(user_id, analysis_id):
    cleanup_expired_analysis_sessions()

    cached = get_cached_analysis_session(analysis_id)
    if cached is not None:
        if str(cached.get("user_id", "")) != str(user_id):
            raise ApiError(403, "Sem permissao para acessar essa sessao.", "forbidden")
        return cached

    row = analysis_sessions_collection().find_one(
        {
            "_id": analysis_id,
            "user_id": str(user_id),
            "status": "active",
        }
    )

    if row is None:
        return None

    current_time = now_ts()
    analysis_sessions_collection().update_one(
        {"_id": analysis_id},
        {"$set": {"updated_at": current_time}},
    )

    session = {
        "id": str(row["_id"]),
        "user_id": str(row["user_id"]),
        "source_label": row["source_label"],
        "segments": list(row.get("segments", [])),
        "history": list(row.get("history", [])),
        "expires_at": int(row["expires_at"]),
    }
    cache_analysis_session(session["id"], session, row["expires_at"])
    return session


def update_analysis_history(analysis_id, history, expires_at):
    clipped_history = history[-(MAX_HISTORY_MESSAGES * 2):]
    analysis_sessions_collection().update_one(
        {"_id": analysis_id},
        {
            "$set": {
                "history": clipped_history,
                "updated_at": now_ts(),
            }
        },
    )

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
    usage_events_collection().insert_one(
        {
            "_id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "session_id": session_id,
            "endpoint": endpoint,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost": estimated_cost,
            "http_status": http_status,
            "created_at": now_ts(),
        }
    )


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


def build_transcript_with_times(segments):
    return "\n".join(
        f"[{segment.get('start', 0):.2f}-{segment.get('end', 0):.2f}] {segment.get('text', '').strip()}"
        for segment in segments
        if str(segment.get("text", "")).strip()
    )


def build_initial_prompt(question):
    prompt = str(question or "").strip()
    return prompt or DEFAULT_ANALYSIS_PROMPT


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


def run_video_analysis(user_id, api_key, video_path, question="", session_source_label=TRANSIENT_ANALYSIS_VIDEO_PATH):
    initial_prompt = build_initial_prompt(question)
    segments = transcribe_large_video(api_key, video_path)
    chat_result = ask_openai(api_key, segments, initial_prompt)
    insights = chat_result["answer"]
    initial_history = [
        {"role": "user", "content": initial_prompt},
        {"role": "assistant", "content": insights}
    ]
    analysis_id = create_analysis_session(
        user_id,
        session_source_label,
        segments,
        initial_history
    )

    return {
        "analysis_id": analysis_id,
        "insights": insights,
        "transcription": segments,
        "history": initial_history,
        "usage": chat_result["usage"],
        "model": chat_result["model"]
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
    docs = list(
        usage_events_collection().find(
            {
                "user_id": str(user_id),
                "created_at": {"$gte": start_ts},
            }
        )
    )

    summary = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": 0.0,
        "errors": 0,
    }
    by_endpoint_map = defaultdict(
        lambda: {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": 0.0,
            "errors": 0,
        }
    )
    timeline_map = defaultdict(
        lambda: {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": 0.0,
            "errors": 0,
        }
    )

    for doc in docs:
        input_tokens = int(doc.get("input_tokens") or 0)
        output_tokens = int(doc.get("output_tokens") or 0)
        estimated_cost = float(doc.get("estimated_cost") or 0)
        is_error = int((doc.get("http_status") or 0) >= 400)
        endpoint = str(doc.get("endpoint") or "unknown")
        day = time.strftime("%Y-%m-%d", time.gmtime(int(doc.get("created_at") or 0)))

        summary["requests"] += 1
        summary["input_tokens"] += input_tokens
        summary["output_tokens"] += output_tokens
        summary["estimated_cost"] += estimated_cost
        summary["errors"] += is_error

        endpoint_bucket = by_endpoint_map[endpoint]
        endpoint_bucket["requests"] += 1
        endpoint_bucket["input_tokens"] += input_tokens
        endpoint_bucket["output_tokens"] += output_tokens
        endpoint_bucket["estimated_cost"] += estimated_cost
        endpoint_bucket["errors"] += is_error

        timeline_bucket = timeline_map[day]
        timeline_bucket["requests"] += 1
        timeline_bucket["input_tokens"] += input_tokens
        timeline_bucket["output_tokens"] += output_tokens
        timeline_bucket["estimated_cost"] += estimated_cost
        timeline_bucket["errors"] += is_error

    return {
        "summary": {
            "requests": int(summary["requests"]),
            "input_tokens": int(summary["input_tokens"]),
            "output_tokens": int(summary["output_tokens"]),
            "estimated_cost": round(float(summary["estimated_cost"]), 6),
            "errors": int(summary["errors"]),
        },
        "by_endpoint": sorted(
            [
                {
                    "endpoint": endpoint,
                    "requests": int(values["requests"]),
                    "input_tokens": int(values["input_tokens"]),
                    "output_tokens": int(values["output_tokens"]),
                    "estimated_cost": round(float(values["estimated_cost"]), 6),
                    "errors": int(values["errors"]),
                }
                for endpoint, values in by_endpoint_map.items()
            ],
            key=lambda item: (-item["requests"], item["endpoint"]),
        ),
        "timeline": sorted(
            [
                {
                    "day": day,
                    "requests": int(values["requests"]),
                    "input_tokens": int(values["input_tokens"]),
                    "output_tokens": int(values["output_tokens"]),
                    "estimated_cost": round(float(values["estimated_cost"]), 6),
                    "errors": int(values["errors"]),
                }
                for day, values in timeline_map.items()
            ],
            key=lambda item: item["day"],
        ),
    }


def get_global_usage_top_users(start_ts, limit):
    docs = list(usage_events_collection().find({"created_at": {"$gte": start_ts}}))
    users_map = {
        str(document["_id"]): document.get("email", "")
        for document in users_collection().find({}, {"email": 1})
    }
    grouped = defaultdict(
        lambda: {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": 0.0,
            "errors": 0,
        }
    )

    for doc in docs:
        user_id = str(doc.get("user_id"))
        bucket = grouped[user_id]
        bucket["requests"] += 1
        bucket["input_tokens"] += int(doc.get("input_tokens") or 0)
        bucket["output_tokens"] += int(doc.get("output_tokens") or 0)
        bucket["estimated_cost"] += float(doc.get("estimated_cost") or 0)
        bucket["errors"] += int((doc.get("http_status") or 0) >= 400)

    ranked = sorted(
        [
            {
                "user_id": user_id,
                "email": users_map.get(user_id, ""),
                "requests": int(values["requests"]),
                "input_tokens": int(values["input_tokens"]),
                "output_tokens": int(values["output_tokens"]),
                "estimated_cost": round(float(values["estimated_cost"]), 6),
                "errors": int(values["errors"]),
            }
            for user_id, values in grouped.items()
        ],
        key=lambda item: (-item["requests"], item["email"]),
    )
    return ranked[:limit]


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

    try:
        user_id = str(uuid.uuid4())
        users_collection().insert_one(
            {
                "_id": user_id,
                "email": email,
                "password_hash": password_hash,
                "created_at": created_at,
                "status": "active",
            }
        )
    except DuplicateKeyError as error:
        raise ApiError(400, "Este email ja esta cadastrado.", "email_already_exists") from error

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
        users_collection().update_one(
            {"_id": user["id"]},
            {"$set": {"password_hash": hash_password(password)}},
        )

    token = issue_auth_token(user["id"], user["email"])
    return json_success({
        "token": token,
        "user": {"id": user["id"], "email": user["email"]}
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
        get_db().list_collection_names()
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
        "frontend_mode": "flask_templates",
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
    enforce_rate_limit("analyze", RATE_LIMIT_ANALYZE_PER_MIN)

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

    question = str(request.form.get("question", "")).strip()
    api_key = get_active_openai_key_or_error(g.current_user["id"])
    extension = safe_original_name.rsplit(".", 1)[1].lower()
    temp_dir = tempfile.mkdtemp(prefix=f"meeting_analysis_user_{g.current_user['id']}_")
    file_path = os.path.join(temp_dir, f"input.{extension}")

    try:
        file.save(file_path)

        max_file_size_bytes = current_max_file_size_bytes()
        max_file_size_mb = current_max_file_size_mb()
        if os.path.getsize(file_path) > max_file_size_bytes:
            raise ApiError(413, f"Arquivo excede o limite de {max_file_size_mb}MB.", "file_too_large")

        validate_uploaded_video(file_path)
        result = run_video_analysis(
            user_id=g.current_user["id"],
            api_key=api_key,
            video_path=file_path,
            question=question,
            session_source_label=f"{TRANSIENT_ANALYSIS_VIDEO_PATH}:{safe_original_name}"
        )

        safe_log_usage_event(
            user_id=g.current_user["id"],
            session_id=result["analysis_id"],
            endpoint="/upload",
            model=result["model"],
            usage=result["usage"],
            http_status=200
        )
    except ApiError as error:
        safe_log_usage_event(
            user_id=g.current_user["id"],
            session_id=None,
            endpoint="/upload",
            model=OPENAI_CHAT_MODEL,
            usage={},
            http_status=error.status_code
        )
        raise
    except requests.RequestException as error:
        safe_log_usage_event(
            user_id=g.current_user["id"],
            session_id=None,
            endpoint="/upload",
            model=OPENAI_CHAT_MODEL,
            usage={},
            http_status=502
        )
        raise ApiError(502, f"Falha ao comunicar com OpenAI: {error}", "provider_failure") from error
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return json_success({
        "filename": safe_original_name,
        "analysis_id": result["analysis_id"],
        "insights": result["insights"],
        "transcription": result["transcription"],
        "timestamps": [],
        "video_retained": False
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


@app.route("/", methods=["GET"])
def landing_page():
    return render_template("landing.html")


@app.route("/app", methods=["GET"])
@app.route("/app/", methods=["GET"])
def application_page():
    return render_template("app.html")


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
