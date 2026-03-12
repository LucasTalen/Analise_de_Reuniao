import { useMemo, useRef, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE_URL?.trim() || '';

const QUICK_ACTIONS = [
  {
    label: 'Checklist',
    prompt: 'Gere um checklist objetivo com os itens mais importantes da reunião e cite timestamps.'
  },
  {
    label: 'Lista de tarefas',
    prompt:
      'Transforme a reunião em lista de tarefas com: tarefa, responsável sugerido, prioridade e prazo sugerido, citando timestamps.'
  },
  {
    label: 'Decisões',
    prompt: 'Liste as decisões tomadas e as pendências abertas com timestamps.'
  },
  {
    label: 'Plano de ação',
    prompt: 'Monte um plano de ação para os próximos 7 dias com etapas práticas e timestamps.'
  }
];

function endpoint(path) {
  if (!API_BASE) {
    return path;
  }
  return `${API_BASE}${path}`;
}

function formatTime(time) {
  return Number.parseFloat(time).toFixed(2);
}

function parseLineWithTimestamps(line) {
  const matches = [...line.matchAll(/\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]/g)];

  if (matches.length === 0) {
    return [{ type: 'text', value: line }];
  }

  const tokens = [];
  let lastIndex = 0;

  for (const match of matches) {
    const [raw, start, end] = match;
    const index = match.index ?? 0;

    if (index > lastIndex) {
      tokens.push({ type: 'text', value: line.slice(lastIndex, index) });
    }

    tokens.push({
      type: 'timestamp',
      value: raw,
      start: Number.parseFloat(start),
      end: Number.parseFloat(end)
    });

    lastIndex = index + raw.length;
  }

  if (lastIndex < line.length) {
    tokens.push({ type: 'text', value: line.slice(lastIndex) });
  }

  return tokens;
}

function renderTextWithTimestamps(text, keyPrefix, jumpToTime) {
  const lines = String(text || '')
    .split(/\n+/)
    .filter((line) => line.trim().length > 0);

  if (lines.length === 0) {
    return <p>Sem conteúdo.</p>;
  }

  return lines.map((line, lineIndex) => (
    <p key={`${keyPrefix}-${lineIndex}`}>
      {parseLineWithTimestamps(line).map((token, tokenIndex) => {
        if (token.type === 'timestamp') {
          return (
            <button
              key={`${keyPrefix}-${lineIndex}-${token.value}-${tokenIndex}`}
              className="timestamp"
              type="button"
              onClick={() => jumpToTime(token.start)}
              title={`Ir para ${token.value}`}
            >
              {token.value}
            </button>
          );
        }
        return <span key={`${keyPrefix}-${lineIndex}-text-${tokenIndex}`}>{token.value}</span>;
      })}
    </p>
  ));
}

function App() {
  const [file, setFile] = useState(null);
  const [question, setQuestion] = useState('');
  const [uploaded, setUploaded] = useState(null);
  const [analysisId, setAnalysisId] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isAsking, setIsAsking] = useState(false);
  const [progressText, setProgressText] = useState('Aguardando envio...');
  const [insights, setInsights] = useState('');
  const [transcription, setTranscription] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [chatMessages, setChatMessages] = useState([]);
  const [activeTab, setActiveTab] = useState('insights');
  const [error, setError] = useState('');

  const videoRef = useRef(null);

  const insightsLines = useMemo(() => {
    if (!insights) {
      return [];
    }
    return insights.split(/\n+/).filter((line) => line.trim().length > 0);
  }, [insights]);

  const hasResult = insights.length > 0 || transcription.length > 0;
  const canUseAssistant = Boolean(analysisId);
  const isBusy = isUploading || isAnalyzing || isAsking;
  const modeLabel = question.trim() ? 'Pergunta guiada' : 'Resumo automático';
  const workflowState = isUploading
    ? 'Enviando vídeo'
    : isAnalyzing
      ? 'Processando IA'
      : isAsking
        ? 'Respondendo nova pergunta'
        : uploaded?.filename
          ? 'Pronto para analisar'
          : 'Aguardando arquivo';

  function jumpToTime(seconds) {
    if (!videoRef.current || Number.isNaN(seconds)) {
      return;
    }
    videoRef.current.currentTime = seconds;
    videoRef.current.play().catch(() => {});
  }

  async function handleUpload(event) {
    event.preventDefault();

    if (!file) {
      setError('Selecione um arquivo de vídeo antes de enviar.');
      return;
    }

    setError('');
    setIsUploading(true);
    setUploaded(null);
    setAnalysisId('');
    setInsights('');
    setTranscription([]);
    setChatInput('');
    setChatMessages([]);
    setActiveTab('insights');
    setProgressText('Enviando vídeo...');

    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch(endpoint('/upload'), {
        method: 'POST',
        body: formData
      });

      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Erro ao enviar o vídeo.');
      }

      setUploaded(data);
      setProgressText('Upload concluído. Clique em "Analisar vídeo".');
    } catch (uploadError) {
      setError(uploadError.message);
      setProgressText('Falha no upload.');
    } finally {
      setIsUploading(false);
    }
  }

  async function handleAnalyze() {
    if (!uploaded?.file_path) {
      setError('Faça o upload de um vídeo primeiro.');
      return;
    }

    setError('');
    setIsAnalyzing(true);
    setProgressText('Transcrevendo e analisando o conteúdo...');

    try {
      const response = await fetch(endpoint('/analyze'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          file_path: uploaded.file_path,
          question
        })
      });

      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Erro ao analisar o vídeo.');
      }

      const normalizedInsights = data.insights || '';
      const initialMessages = [];

      if (question.trim()) {
        initialMessages.push({ role: 'user', content: question.trim() });
      }
      if (normalizedInsights.trim()) {
        initialMessages.push({ role: 'assistant', content: normalizedInsights });
      }

      setAnalysisId(data.analysis_id || '');
      setInsights(normalizedInsights);
      setTranscription(Array.isArray(data.transcription) ? data.transcription : []);
      setChatMessages(initialMessages);
      setActiveTab('assistant');
      setProgressText('Análise concluída. Faça novas perguntas ou use as ações rápidas.');
    } catch (analysisError) {
      setError(analysisError.message);
      setProgressText('Falha durante a análise.');
    } finally {
      setIsAnalyzing(false);
    }
  }

  async function askFollowup(promptOverride) {
    const prompt = (promptOverride ?? chatInput).trim();

    if (!prompt) {
      setError('Digite uma pergunta para o assistente.');
      return;
    }
    if (!analysisId) {
      setError('Faça a análise do vídeo antes de perguntar.');
      return;
    }

    setError('');
    setIsAsking(true);
    setActiveTab('assistant');
    if (!promptOverride) {
      setChatInput('');
    }

    setChatMessages((previous) => [...previous, { role: 'user', content: prompt }]);

    try {
      const response = await fetch(endpoint('/followup'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          analysis_id: analysisId,
          question: prompt
        })
      });

      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Erro ao consultar o assistente.');
      }

      setChatMessages((previous) => [...previous, { role: 'assistant', content: data.answer || 'Sem resposta.' }]);
      setProgressText('Resposta gerada com sucesso.');
    } catch (followupError) {
      setError(followupError.message);
      setProgressText('Falha ao responder pergunta.');
    } finally {
      setIsAsking(false);
    }
  }

  function handleFollowupSubmit(event) {
    event.preventDefault();
    askFollowup();
  }

  function handleFollowupKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      askFollowup();
    }
  }

  return (
    <div className="app-shell">
      <div className="bg-shape bg-shape-a" />
      <div className="bg-shape bg-shape-b" />
      <header className="hero">
        <p className="eyebrow">Analise de Reuniao</p>
        <h1>Análise de Vídeo com IA</h1>
        <p className="subtitle">
          Faça upload uma vez, analise e continue conversando com a transcrição para gerar checklist, tarefas e planos de ação.
        </p>
        <div className="hero-metrics">
          <article className="metric-card">
            <p>Status</p>
            <strong>{workflowState}</strong>
          </article>
          <article className="metric-card">
            <p>Arquivo</p>
            <strong>{uploaded?.filename || 'Nenhum enviado'}</strong>
          </article>
          <article className="metric-card">
            <p>Modo</p>
            <strong>{modeLabel}</strong>
          </article>
        </div>
        <div className="flow-steps">
          <span className={`flow-step ${uploaded ? 'flow-step-done' : ''}`}>1. Upload</span>
          <span className={`flow-step ${hasResult ? 'flow-step-done' : ''}`}>2. Análise</span>
          <span className={`flow-step ${chatMessages.length > 2 ? 'flow-step-done' : ''}`}>3. Conversa contínua</span>
        </div>
      </header>

      <main className="layout">
        <section className="panel panel-upload">
          <h2>Enviar e analisar</h2>
          <form onSubmit={handleUpload} className="upload-form">
            <label htmlFor="video-file">Arquivo de vídeo</label>
            <input
              id="video-file"
              type="file"
              accept=".mp4,.avi,.mov,.mkv"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              required
            />

            <label htmlFor="question">Pergunta inicial (opcional)</label>
            <input
              id="question"
              type="text"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ex: Quais decisões foram tomadas?"
            />

            <button className="btn" type="submit" disabled={isUploading || isAnalyzing}>
              {isUploading ? 'Enviando...' : 'Enviar vídeo'}
            </button>
          </form>

          <div className="status-row">
            <span className={`dot ${isBusy ? 'dot-running' : ''}`} />
            <p>{progressText}</p>
          </div>
          <div className="progress-track" aria-hidden="true">
            <span
              className={`progress-fill ${isBusy ? 'progress-fill-running' : ''} ${hasResult ? 'progress-fill-done' : ''}`}
            />
          </div>

          {uploaded?.filename ? (
            <div className="video-card">
              <p className="video-label">Arquivo pronto: {uploaded.filename}</p>
              <video
                ref={videoRef}
                controls
                src={endpoint(`/video/${encodeURIComponent(uploaded.filename)}`)}
              >
                Seu navegador não suporta reprodução de vídeo.
              </video>
              <button className="btn btn-secondary" onClick={handleAnalyze} disabled={isAnalyzing || isAsking}>
                {isAnalyzing ? 'Analisando...' : 'Analisar vídeo'}
              </button>
            </div>
          ) : null}

          {error ? <p className="error-box">{error}</p> : null}
        </section>

        <section className="panel panel-results">
          <div className="result-header">
            <h2>Resultados e assistente</h2>
            <p>Navegue pelos timestamps ou continue perguntando sem reprocessar o vídeo.</p>
          </div>
          <div className="tabs">
            <button
              className={`tab ${activeTab === 'assistant' ? 'tab-active' : ''}`}
              onClick={() => setActiveTab('assistant')}
              type="button"
            >
              Assistente ({chatMessages.length})
            </button>
            <button
              className={`tab ${activeTab === 'insights' ? 'tab-active' : ''}`}
              onClick={() => setActiveTab('insights')}
              type="button"
            >
              Insights ({insightsLines.length})
            </button>
            <button
              className={`tab ${activeTab === 'transcription' ? 'tab-active' : ''}`}
              onClick={() => setActiveTab('transcription')}
              type="button"
            >
              Transcrição ({transcription.length})
            </button>
          </div>

          {!hasResult ? (
            <div className="empty-state">
              <h3>Fluxo recomendado</h3>
              <p>A experiência foi desenhada para ser direta:</p>
              <ol>
                <li>Envie o vídeo.</li>
                <li>Clique em analisar.</li>
                <li>Use a aba Assistente para pedir checklist, tarefas e novas respostas.</li>
              </ol>
            </div>
          ) : null}

          {hasResult && activeTab === 'assistant' ? (
            <div className="assistant-shell">
              <div className="assistant-toolbar">
                <p>Ações rápidas</p>
                <div className="quick-actions">
                  {QUICK_ACTIONS.map((action) => (
                    <button
                      key={action.label}
                      type="button"
                      className="chip-btn"
                      disabled={!canUseAssistant || isAsking}
                      onClick={() => askFollowup(action.prompt)}
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="chat-feed">
                {chatMessages.length === 0 ? (
                  <p className="chat-placeholder">A primeira resposta aparecerá aqui após a análise.</p>
                ) : null}

                {chatMessages.map((message, index) => (
                  <article className={`chat-message chat-${message.role}`} key={`${message.role}-${index}`}>
                    <p className="chat-role">{message.role === 'assistant' ? 'Assistente' : 'Você'}</p>
                    <div className="chat-content">
                      {renderTextWithTimestamps(message.content, `chat-${index}`, jumpToTime)}
                    </div>
                  </article>
                ))}

                {isAsking ? <p className="chat-loading">Gerando resposta...</p> : null}
              </div>

              <form className="followup-form" onSubmit={handleFollowupSubmit}>
                <label htmlFor="followup-question">Nova pergunta ou comando</label>
                <textarea
                  id="followup-question"
                  value={chatInput}
                  onChange={(event) => setChatInput(event.target.value)}
                  onKeyDown={handleFollowupKeyDown}
                  placeholder="Ex: Gere uma lista de tarefas com prioridade e prazo sugerido."
                  rows={3}
                  disabled={!canUseAssistant || isAsking}
                />
                <div className="followup-actions">
                  <p>Pressione Enter para enviar ou Shift+Enter para quebrar linha.</p>
                  <button className="btn btn-send" type="submit" disabled={!canUseAssistant || isAsking}>
                    {isAsking ? 'Enviando...' : 'Perguntar ao assistente'}
                  </button>
                </div>
              </form>
            </div>
          ) : null}

          {hasResult && activeTab === 'insights' ? (
            <div className="content-card">
              <p className="hint">Clique em um timestamp para pular para o trecho correspondente do vídeo.</p>
              <div className="text-flow">{renderTextWithTimestamps(insights, 'insight', jumpToTime)}</div>
            </div>
          ) : null}

          {hasResult && activeTab === 'transcription' ? (
            <div className="content-card transcription-list">
              {transcription.map((segment, index) => (
                <p key={`${segment.start}-${segment.end}-${index}`}>
                  <button
                    className="timestamp"
                    type="button"
                    onClick={() => jumpToTime(segment.start)}
                    title="Ir para este trecho"
                  >
                    [{formatTime(segment.start)}-{formatTime(segment.end)}]
                  </button>{' '}
                  {segment.text}
                </p>
              ))}
            </div>
          ) : null}
        </section>
      </main>
    </div>
  );
}

export default App;
