# Analise de Reuniao

Aplicacao de analise de reunioes com Flask + React no modelo BYOK: cada usuario cadastra a propria chave OpenAI, o backend faz as chamadas ao provedor sem expor segredo no frontend, e os videos sao processados temporariamente durante o upload e descartados ao final da analise.

## Status

Projeto pronto para portfolio tecnico e publicacao como servico unico.

O que este build ja demonstra:

- autenticacao com hash de senha e politica minima
- integracao OpenAI em modo BYOK
- upload, transcricao, analise inicial e follow-up
- persistencia em MongoDB com trilha de uso
- testes automatizados cobrindo o backend
- frontend buildado e servivel pelo proprio Flask

## Stack

- Backend: Flask
- Frontend: React + Vite
- Banco: MongoDB
- Cache opcional: Redis
- Video/transcricao: `ffmpeg` + OpenAI

## Como funciona

- em desenvolvimento, o frontend roda em `http://localhost:5173`
- a API Flask roda em `http://localhost:5000`
- em producao, o Flask pode servir o build do frontend na mesma aplicacao
- o upload ja dispara a analise automaticamente
- o backend nao persiste os videos enviados
- login, cadastro, chave OpenAI, upload, analise e follow-up passam pelo backend

## Publicacao Em Servico Unico

Se a ideia e publicar sem separar frontend e backend em dois servicos, este repositorio ja suporta esse modo.

Fluxo recomendado de deploy:

1. gerar o build do frontend
2. manter `SERVE_FRONTEND_FROM_FLASK=1`
3. publicar apenas o servico Flask

Build local para esse modo:

```bash
npm --prefix frontend run build
python app.py
```

Nesse modo:

- o Flask serve `frontend/dist`
- o frontend usa mesma origem da API
- voce nao precisa publicar um servico separado para o Vite

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
- `MONGODB_URI`
- `MAX_FILE_SIZE_MB`
- `SERVE_FRONTEND_FROM_FLASK`

Se quiser separar segredos locais sem mexer no `.env`, voce tambem pode criar `.env.local`. O backend carrega `.env` primeiro e depois `.env.local`, sobrescrevendo o que estiver duplicado.

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

1. Abra `http://localhost:5173` em dev ou a raiz do Flask em deploy unico
2. Crie uma conta ou faca login
3. Cadastre a chave OpenAI em `Conta e integracao`
4. Envie um video
5. A analise comeca automaticamente
6. O video e descartado do servidor ao fim do processamento
7. Use o assistente para perguntas adicionais sem reenviar o video

## Testes

Instale as dependencias de teste no mesmo ambiente virtual:

```bash
pip install -r requirements-dev.txt
```

Rode a suite com cobertura:

```bash
python -m pytest
```

O `pytest.ini` ja esta configurado para gerar cobertura sobre `app.py` com `term-missing`.

## Endpoints principais

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`
- `GET /auth/password-policy`
- `POST /integrations/openai-key`
- `GET /integrations/openai-key/status`
- `DELETE /integrations/openai-key`
- `POST /upload`
- `POST /followup`
- `GET /usage/dashboard`
- `GET /health`

## Seguranca de credenciais

- nunca commite `.env`, chaves reais da OpenAI ou segredos de deploy
- use sempre `.env.example` como template
- rotacione `SECRET_KEY`, `KEY_ENCRYPTION_MASTER_KEY` e chaves OpenAI se houver exposicao
- em producao, prefira armazenar segredos no painel do provedor e nao no repositorio
- o frontend nao deve exibir a chave completa depois do cadastro

## Custos e limites

- este projeto usa modelo BYOK: o custo da OpenAI fica na conta do usuario final
- transcricao e chat dependem de credito, limite e status da chave cadastrada
- `OPENAI_TRANSCRIPTION_PROVIDER_MAX_FILE_SIZE_MB` nao deve ultrapassar `25`
- uploads longos e modelos maiores podem aumentar latencia e custo

## Limitacoes conhecidas

- depende de `ffmpeg` e `ffprobe` instalados no ambiente
- depende de um MongoDB acessivel pela `MONGODB_URI`
- Redis e opcional; sem ele, parte do comportamento usa fallback local em memoria
- o preview do video em `/app` e local do navegador, nao servidor
- a suite automatizada cobre o backend; o frontend ainda nao tem suite dedicada

## Roadmap curto

- adicionar testes de frontend com Vitest + React Testing Library
- adicionar colecoes TTL/observabilidade mais avancadas no MongoDB
- adicionar storage S3-compatible para anexos e artefatos futuros
- expandir observabilidade com metricas e alertas de uso por usuario

## CI

Existe pipeline simples em GitHub Actions para:

- rodar `pytest`
- validar o build do frontend

Arquivo: `.github/workflows/ci.yml`

## Licenca

Este projeto esta licenciado sob a licenca MIT. Consulte [LICENSE](LICENSE).

## Observacoes

- Redis e opcional; sem ele o sistema usa fallback local em memoria para parte do comportamento
- se o `upload` ou a validacao de video falharem, confira se `ffmpeg` e `ffprobe` estao instalados corretamente
- se a analise retornar `502`, normalmente o problema esta na chave OpenAI, credito, limite ou falha de rede com o provedor
