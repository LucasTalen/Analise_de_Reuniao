# Analise de Reuniao

Aplicacao para upload de videos de reunioes, transcricao com OpenAI e follow-up em formato de chat. O projeto usa modelo BYOK: cada usuario cadastra a propria chave OpenAI, e o backend faz as chamadas ao provedor sem expor o segredo no frontend.

## Stack

- Backend: Flask
- Frontend: React + Vite
- Banco local: SQLite
- Cache opcional: Redis
- Video/transcricao: `ffmpeg` + OpenAI

## Como funciona

- `frontend` roda em `http://localhost:5173`
- `backend` roda em `http://localhost:5000`
- Em desenvolvimento, o Flask fica como API apenas
- O upload ja dispara a analise automaticamente
- Login, cadastro, chave OpenAI, upload, analise e follow-up passam pelo backend

## Pre-requisitos

- Python 3.11+ ou compativel com as dependencias do projeto
- Node.js 18+
- `ffmpeg` e `ffprobe` instalados e disponiveis no `PATH`
- Redis opcional

## Configuracao do ambiente

1. Crie o arquivo `.env` a partir do exemplo:

```bash
cp .env.example .env
```

No Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

2. Preencha pelo menos estas variaveis:

- `SECRET_KEY`
- `KEY_ENCRYPTION_MASTER_KEY`
- `SERVE_FRONTEND_FROM_FLASK=0` para desenvolvimento
- `MAX_FILE_SIZE_MB` conforme o limite desejado

Para gerar `KEY_ENCRYPTION_MASTER_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Como rodar em desenvolvimento

### Backend

1. Crie e ative um ambiente virtual:

```bash
python -m venv venv
source venv/bin/activate
```

No Windows PowerShell:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

2. Instale as dependencias:

```bash
pip install -r requirements.txt
```

3. Suba a API:

```bash
python app.py
```

O backend ficara em `http://localhost:5000`.

### Frontend

1. Instale as dependencias:

```bash
cd frontend
npm install
```

2. Rode o Vite:

```bash
npm run dev
```

O frontend ficara em `http://localhost:5173`.

## Fluxo de uso

1. Abra `http://localhost:5173`
2. Crie uma conta ou faca login
3. Cadastre a chave OpenAI em `Conta e integracao`
4. Envie um video
5. A analise comeca automaticamente
6. Use o assistente para perguntas adicionais sem reenviar o video

## Variaveis importantes

- `SERVE_FRONTEND_FROM_FLASK`
  - `0`: modo desenvolvimento recomendado, com frontend separado no Vite
  - `1`: Flask tambem serve o frontend buildado
- `MAX_FILE_SIZE_MB`: limite de upload por arquivo
- `OPENAI_TRANSCRIPTION_PROVIDER_MAX_FILE_SIZE_MB`: limite por parte enviado para transcricao na OpenAI. O provedor aceita no maximo `25`
- `MAX_VIDEO_DURATION_SECONDS`: duracao maxima permitida
- `PASSWORD_MIN_LENGTH`: politica minima de senha
- `REDIS_URL`: habilita cache/sessoes em Redis quando configurado
- `OPENAI_CHAT_MODEL`
- `OPENAI_TRANSCRIPTION_MODEL`
- `OPENAI_API_BASE_URL`

## Endpoints principais

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`
- `GET /auth/password-policy`
- `POST /integrations/openai-key`
- `GET /integrations/openai-key/status`
- `DELETE /integrations/openai-key`
- `POST /upload`
- `POST /analyze`
- `POST /followup`
- `GET /video/<filename>`
- `GET /usage/dashboard`
- `GET /health`

## Producao simples

Se quiser servir o frontend pelo Flask:

1. Gere o build:

```bash
cd frontend
npm run build
```

2. No `.env`, defina:

```env
SERVE_FRONTEND_FROM_FLASK=1
```

3. Rode o backend:

```bash
python app.py
```

Nesse modo, o Flask tenta servir os arquivos de `frontend/dist`.

## Observacoes

- O projeto usa SQLite por padrao em `app.db`
- Redis e opcional; sem ele o sistema usa fallback local em memoria para parte do comportamento
- Se o `upload` ou a validacao de video falharem, confira se `ffmpeg` e `ffprobe` estao instalados corretamente
- Se a analise retornar `502`, normalmente o problema esta na chave OpenAI, credito, limite ou falha de rede com o provedor
