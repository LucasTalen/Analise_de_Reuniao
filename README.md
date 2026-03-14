# Analise de Reuniao

Aplicacao de analise de reunioes em modelo BYOK: cada usuario cadastra a propria chave OpenAI, o backend faz as chamadas ao provedor sem expor segredo no cliente, e os videos sao processados temporariamente durante o upload e descartados ao final da analise.

## Status

Projeto pronto para publicacao.

Principais recursos:

- autenticacao com hash de senha, salt e politica minima
- integracao OpenAI em modo BYOK
- upload, transcricao, analise inicial e follow-up
- persistencia em MongoDB com trilha de uso
- testes automatizados cobrindo o backend

## Stack

- Backend: Flask
- Frontend: Jinja templates + JavaScript estatico
- Banco: MongoDB
- Cache opcional: Redis
- Video/transcricao: `ffmpeg` + OpenAI

## Como funciona

- a landing publica fica em `http://localhost:5000/`
- a aplicacao real fica em `http://localhost:5000/app`
- o upload ja dispara a analise automaticamente
- o backend nao persiste os videos enviados
- login, cadastro, chave OpenAI, upload, analise e follow-up passam pelo backend

## Publicacao

Fluxo de deploy:

1. configurar variaveis de ambiente
2. subir apenas o servico Flask
3. apontar dominio para a aplicacao Flask

## Pre-requisitos

- Python 3.11+ ou compativel com as dependencias do projeto
- `ffmpeg` e `ffprobe` instalados e disponiveis no `PATH`
- MongoDB acessivel pela `MONGODB_URI`
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

Se quiser separar segredos locais sem mexer no `.env`, voce tambem pode criar `.env.local`. O backend carrega `.env` primeiro e depois `.env.local`, sobrescrevendo o que estiver duplicado.

Para gerar `KEY_ENCRYPTION_MASTER_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Exemplo de `.env.local`:

```env
MONGODB_URI="mongodb+srv://usuario:senha@cluster.mongodb.net/?retryWrites=true&w=majority"
MONGODB_DB_NAME=analise_reuniao
```

Se a URI nao trouxer o nome do banco no path, o app usa `analise_reuniao` por padrao.

## Como rodar

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

3. Suba a aplicacao:

```bash
python app.py
```

A aplicacao ficara em `http://localhost:5000`.

## Fluxo de uso

1. Abra `http://localhost:5000/`
2. Entre em `/app`
3. Crie uma conta ou faca login
4. Cadastre a chave OpenAI em `Conta e integracao`
5. Envie um video
6. A analise comeca automaticamente
7. O video e descartado do servidor ao fim do processamento
8. Use o assistente para perguntas adicionais sem reenviar o video

## Estrutura relevante

- `app.py`: API, auth, integracoes, upload, analise e renderizacao das paginas HTML
- `templates/`: landing e aplicacao em Jinja
- `static/css/app.css`: estilo do frontend
- `static/js/app.js`: comportamento do frontend
- `tests/`: suite automatizada

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

- `GET /`
- `GET /app`
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

- nunca commite `.env`, `.env.local`, chaves reais da OpenAI ou segredos de deploy
- use sempre `.env.example` como template
- rotacione `SECRET_KEY`, `KEY_ENCRYPTION_MASTER_KEY`, `MONGODB_URI` e chaves OpenAI se houver exposicao
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
- o preview do video em `/app` é local do navegador, nao servidor
- a suite automatizada cobre o backend; o frontend ainda nao tem suite dedicada de DOM/browser

## Roadmap curto

- adicionar testes de navegador para o frontend em JavaScript puro
- adicionar colecoes TTL/observabilidade mais avancadas no MongoDB
- adicionar storage S3-compatible para anexos e artefatos futuros
- expandir observabilidade com metricas e alertas de uso por usuario

## CI

Existe pipeline simples em GitHub Actions para:

- rodar `pytest`
- validar a integridade do backend Flask

Arquivo: `.github/workflows/ci.yml`

## Licenca

Este projeto esta licenciado sob a licenca MIT. Consulte [LICENSE](LICENSE).

## Observacoes

- Redis e opcional; sem ele o sistema usa fallback local em memoria para parte do comportamento
- se o `upload` ou a validacao de video falharem, confira se `ffmpeg` e `ffprobe` estao instalados corretamente
- se a analise retornar `502`, normalmente o problema esta na chave OpenAI, credito, limite ou falha de rede com o provedor
