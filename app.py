import os
import math
from flask import Flask, request, jsonify, render_template, send_from_directory
import requests
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
import ffmpeg

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'segredo')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

MAX_FILE_SIZE_MB = 25

@app.route('/')
def home():
    return render_template('index.html')

def split_video_ffmpeg(input_path, max_size_mb=25):
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
        output_path = f"{input_path}_part{i+1}.mp4"
        (
            ffmpeg
            .input(input_path, ss=start, t=part_duration)
            .output(output_path, c='copy')
            .run(overwrite_output=True, quiet=True)
        )
        part_paths.append(output_path)
    return part_paths

def transcribe_with_openai(video_path):
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    files = {"file": open(video_path, "rb")}
    data = {
        "model": "whisper-1",
        "response_format": "verbose_json"
    }
    response = requests.post(url, headers=headers, files=files, data=data, timeout=120)
    response.raise_for_status()
    return response.json()

def transcribe_large_video(video_path):
    part_paths = split_video_ffmpeg(video_path)
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
        if part_path != video_path:
            os.remove(part_path)
    return all_segments

def ask_openai(segments, question):
    transcript_with_times = "\n".join(
        f"[{seg['start']:.2f}-{seg['end']:.2f}] {seg['text']}" for seg in segments
    )
    if question:
        prompt = (
            "Abaixo está a transcrição de um vídeo, com timestamps. "
            "Ao responder, sempre que possível, cite o(s) timestamp(s) relevante(s) entre colchetes. "
            f"\n\nTranscrição:\n{transcript_with_times}\n\nPergunta: {question}"
        )
    else:
        prompt = (
            "Abaixo está a transcrição de um vídeo, com timestamps. "
            "Resuma os principais pontos do vídeo, citando os timestamps relevantes."
            f"\n\nTranscrição:\n{transcript_with_times}"
        )
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "gpt-4.1-nano",
        "messages": [
            {"role": "system", "content": "Você é um assistente que responde perguntas sobre a transcrição de um vídeo, sempre citando os timestamps relevantes."},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("Erro ao chamar OpenAI:", e)
        return "Erro ao processar a pergunta ou gerar insights."

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
    data = request.get_json()
    file_path = data.get('file_path')
    question = data.get('question', '').strip()
    if not file_path or not os.path.exists(file_path):
        return jsonify({'success': False, 'error': 'Arquivo de vídeo não encontrado.'}), 400
    segments = transcribe_large_video(file_path)
    insights = ask_openai(segments, question)
    return jsonify({
        'success': True,
        'insights': insights,
        'transcription': segments,
        'timestamps': []
    })

if __name__ == '__main__':
    app.run(debug=True)