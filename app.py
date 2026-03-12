import math
import os
import time
import uuid
from flask import Flask, request, jsonify, send_from_directory
import requests
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import ffmpeg

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'segredo')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

UPLOAD_FOLDER = 'uploads'
FRONTEND_DIST = os.path.join(app.root_path, 'frontend', 'dist')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

MAX_FILE_SIZE_MB = int(os.getenv('MAX_FILE_SIZE_MB', '25'))
CHAT_MODEL = os.getenv('OPENAI_CHAT_MODEL', 'gpt-4.1-nano')
TRANSCRIPTION_MODEL = os.getenv('OPENAI_TRANSCRIPTION_MODEL', 'whisper-1')
ANALYSIS_SESSION_TTL_SECONDS = int(os.getenv('ANALYSIS_SESSION_TTL_MINUTES', '180')) * 60
ANALYSIS_SESSION_MAX = int(os.getenv('ANALYSIS_SESSION_MAX', '20'))
MAX_HISTORY_MESSAGES = int(os.getenv('MAX_HISTORY_MESSAGES', '12'))

analysis_sessions = {}


def cleanup_analysis_sessions():
    now = time.time()
    expired_ids = [
        analysis_id
        for analysis_id, session in analysis_sessions.items()
        if now - session.get('updated_at', now) > ANALYSIS_SESSION_TTL_SECONDS
    ]
    for analysis_id in expired_ids:
        analysis_sessions.pop(analysis_id, None)

    overflow = len(analysis_sessions) - ANALYSIS_SESSION_MAX
    if overflow > 0:
        sorted_by_recent = sorted(
            analysis_sessions.items(),
            key=lambda item: item[1].get('updated_at', 0)
        )
        for analysis_id, _ in sorted_by_recent[:overflow]:
            analysis_sessions.pop(analysis_id, None)


def create_analysis_session(segments, initial_history):
    cleanup_analysis_sessions()
    analysis_id = str(uuid.uuid4())
    now = time.time()
    analysis_sessions[analysis_id] = {
        'segments': segments,
        'history': initial_history,
        'created_at': now,
        'updated_at': now
    }
    return analysis_id


def get_analysis_session(analysis_id):
    cleanup_analysis_sessions()
    session = analysis_sessions.get(analysis_id)
    if session:
        session['updated_at'] = time.time()
    return session


def build_transcript_with_times(segments):
    return "\n".join(
        f"[{seg['start']:.2f}-{seg['end']:.2f}] {seg['text']}" for seg in segments
    )


def ask_openai(segments, user_prompt, history=None):
    if not OPENAI_API_KEY:
        raise ValueError('OPENAI_API_KEY não configurada no .env.')

    transcript_with_times = build_transcript_with_times(segments)
    messages = [
        {
            "role": "system",
            "content": (
                "Você é um assistente que responde perguntas sobre a transcrição de um vídeo. "
                "Sempre cite timestamps relevantes no formato [inicio-fim] quando possível. "
                "Se o usuário pedir checklist, lista de tarefas, próximos passos ou plano de ação, "
                "responda de forma estruturada e prática."
            )
        },
        {
            "role": "system",
            "content": (
                "Use somente a transcrição abaixo como fonte de verdade. "
                "Se algo não estiver na transcrição, sinalize a limitação.\n\n"
                f"Transcrição:\n{transcript_with_times}"
            )
        }
    ]

    if history:
        for item in history[-MAX_HISTORY_MESSAGES:]:
            role = item.get('role')
            content = str(item.get('content', '')).strip()
            if role in {'user', 'assistant'} and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_prompt})

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": CHAT_MODEL,
            "messages": messages,
            "temperature": 0.2
        },
        timeout=90
    )
    response.raise_for_status()
    payload = response.json()
    return payload["choices"][0]["message"]["content"]


def split_video_ffmpeg(input_path, max_size_mb=None):
    max_size_mb = max_size_mb or MAX_FILE_SIZE_MB
    file_size = os.path.getsize(input_path)
    max_size = max_size_mb * 1024 * 1024
    if file_size <= max_size:
        return [input_path]

    probe = ffmpeg.probe(input_path)
    duration = float(probe['format']['duration'])
    num_parts = math.ceil(file_size / max_size)
    part_duration = duration / num_parts
    part_paths = []

    for i in range(num_parts):
        start = i * part_duration
        output_path = f"{input_path}_part{i + 1}.mp4"
        (
            ffmpeg
            .input(input_path, ss=start, t=part_duration)
            .output(output_path, c='copy')
            .run(overwrite_output=True, quiet=True)
        )
        part_paths.append(output_path)
    return part_paths


def transcribe_with_openai(video_path):
    if not OPENAI_API_KEY:
        raise ValueError('OPENAI_API_KEY não configurada no .env.')

    with open(video_path, "rb") as video_file:
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": video_file},
            data={
                "model": TRANSCRIPTION_MODEL,
                "response_format": "verbose_json"
            },
            timeout=120
        )
    response.raise_for_status()
    return response.json()


def transcribe_large_video(video_path):
    part_paths = split_video_ffmpeg(video_path, max_size_mb=MAX_FILE_SIZE_MB)
    all_segments = []
    time_offset = 0.0

    for part_path in part_paths:
        result = transcribe_with_openai(part_path)
        segments = result.get("segments", [])

        for seg in segments:
            seg_copy = seg.copy()
            seg_copy["start"] += time_offset
            seg_copy["end"] += time_offset
            all_segments.append(seg_copy)

        if segments:
            time_offset = all_segments[-1]["end"]

        if part_path != video_path and os.path.exists(part_path):
            os.remove(part_path)

    return all_segments


@app.route('/')
def home():
    index_path = os.path.join(FRONTEND_DIST, 'index.html')
    if os.path.exists(index_path):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return jsonify({
        'success': False,
        'error': 'Frontend build não encontrado. Rode "npm run build" em frontend/.'
    }), 404


@app.route('/<path:path>')
def frontend_assets(path):
    asset_path = os.path.join(FRONTEND_DIST, path)
    if os.path.exists(asset_path):
        return send_from_directory(FRONTEND_DIST, path)
    return home()


@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Nenhum arquivo enviado.'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Nome de arquivo vazio.'}), 400

    filename = secure_filename(file.filename)
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(file_path)

    return jsonify({'success': True, 'filename': filename, 'file_path': file_path})


@app.route('/video/<filename>')
def serve_video(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route('/analyze', methods=['POST'])
def analyze():
    payload = request.get_json(silent=True) or {}
    file_path = str(payload.get('file_path', '')).strip()
    question = str(payload.get('question', '')).strip()

    if not file_path or not os.path.exists(file_path):
        return jsonify({'success': False, 'error': 'Arquivo de vídeo não encontrado.'}), 400
    upload_root = os.path.abspath(UPLOAD_FOLDER)
    absolute_file_path = os.path.abspath(file_path)
    if os.path.commonpath([absolute_file_path, upload_root]) != upload_root:
        return jsonify({'success': False, 'error': 'Caminho de arquivo inválido.'}), 400

    initial_prompt = question or (
        "Resuma os principais pontos da reunião em bullets e destaque decisões, riscos e próximos passos. "
        "Inclua timestamps relevantes."
    )

    try:
        segments = transcribe_large_video(absolute_file_path)
        insights = ask_openai(segments, initial_prompt)
        initial_history = [
            {'role': 'user', 'content': initial_prompt},
            {'role': 'assistant', 'content': insights}
        ]
        analysis_id = create_analysis_session(segments, initial_history)
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 500
    except requests.RequestException as error:
        return jsonify({'success': False, 'error': f'Falha ao comunicar com OpenAI: {error}'}), 502
    except Exception as error:
        return jsonify({'success': False, 'error': f'Erro ao analisar vídeo: {error}'}), 500

    return jsonify({
        'success': True,
        'analysis_id': analysis_id,
        'insights': insights,
        'transcription': segments,
        'timestamps': []
    })


@app.route('/followup', methods=['POST'])
def followup():
    payload = request.get_json(silent=True) or {}
    analysis_id = str(payload.get('analysis_id', '')).strip()
    question = str(payload.get('question', '')).strip()

    if not analysis_id:
        return jsonify({'success': False, 'error': 'analysis_id é obrigatório.'}), 400
    if not question:
        return jsonify({'success': False, 'error': 'A pergunta não pode ser vazia.'}), 400

    session = get_analysis_session(analysis_id)
    if not session:
        return jsonify({
            'success': False,
            'error': 'Sessão de análise não encontrada ou expirada. Rode a análise novamente.'
        }), 404

    try:
        answer = ask_openai(session['segments'], question, history=session.get('history', []))
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 500
    except requests.RequestException as error:
        return jsonify({'success': False, 'error': f'Falha ao comunicar com OpenAI: {error}'}), 502
    except Exception as error:
        return jsonify({'success': False, 'error': f'Erro ao gerar resposta: {error}'}), 500

    session['history'].append({'role': 'user', 'content': question})
    session['history'].append({'role': 'assistant', 'content': answer})
    session['updated_at'] = time.time()

    return jsonify({
        'success': True,
        'answer': answer
    })


if __name__ == '__main__':
    app.run(debug=True)
