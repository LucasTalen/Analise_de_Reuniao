import { useEffect, useMemo, useRef, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE_URL?.trim() || '';
const AUTH_TOKEN_STORAGE_KEY = 'meeting_analysis_auth_token';

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

function parseJsonSafe(text) {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

async function readApiPayload(response) {
  const text = await response.text();
  if (!text) {
    return {};
  }
  return parseJsonSafe(text);
}

function formatTime(time) {
  return Number.parseFloat(time).toFixed(2);
}

function formatInteger(value) {
  return Number(value || 0).toLocaleString('pt-BR');
}

function formatUsd(value) {
  return Number(value || 0).toLocaleString('pt-BR', {
    style: 'currency',
    currency: 'USD'
  });
}

function parseLineWithTimestamps(line) {
  const matches = [...line.matchAll(/\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]/g)];

  if (matches.length === 0) {
    return [{ type: 'text', value: line }];
  }

  const tokens = [];
  let lastIndex = 0;

  for (const match of matches) {
    const [raw, start] = match;
    const index = match.index ?? 0;

    if (index > lastIndex) {
      tokens.push({ type: 'text', value: line.slice(lastIndex, index) });
    }

    tokens.push({
      type: 'timestamp',
      value: raw,
      start: Number.parseFloat(start)
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

function getStoredToken() {
  return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || '';
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
  const [backendReachable, setBackendReachable] = useState(true);
  const [maxUploadMb, setMaxUploadMb] = useState(100);

  const [token, setToken] = useState(() => getStoredToken());
  const [currentUser, setCurrentUser] = useState(null);
  const [authMode, setAuthMode] = useState('login');
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authPasswordConfirm, setAuthPasswordConfirm] = useState('');
  const [isAuthLoading, setIsAuthLoading] = useState(false);
  const [isBootstrapping, setIsBootstrapping] = useState(false);
  const [passwordPolicy, setPasswordPolicy] = useState({
    min_length: 10,
    max_length: 128,
    requires_lowercase: true,
    requires_uppercase: true,
    requires_number: true,
    requires_symbol: true,
    no_whitespace: true
  });

  const [apiKeyInput, setApiKeyInput] = useState('');
  const [apiKeyStatus, setApiKeyStatus] = useState({
    is_active: false,
    masked_key: null,
    rotated_at: null
  });
  const [isSavingApiKey, setIsSavingApiKey] = useState(false);
  const [isRevokingApiKey, setIsRevokingApiKey] = useState(false);
  const [usageDays, setUsageDays] = useState(7);
  const [usageDashboard, setUsageDashboard] = useState(null);
  const [isUsageLoading, setIsUsageLoading] = useState(false);

  const videoRef = useRef(null);
  const autoAnalyzeAttemptRef = useRef('');

  const insightsLines = useMemo(() => {
    if (!insights) {
      return [];
    }
    return insights.split(/\n+/).filter((line) => line.trim().length > 0);
  }, [insights]);

  const hasResult = insights.length > 0 || transcription.length > 0;
  const hasOpenAiKey = Boolean(apiKeyStatus.is_active);
  const apiKeyRotationMarker = apiKeyStatus.rotated_at || 0;
  const canUseAssistant = Boolean(analysisId) && hasOpenAiKey;
  const isBusy =
    isUploading ||
    isAnalyzing ||
    isAsking ||
    isAuthLoading ||
    isBootstrapping ||
    isSavingApiKey ||
    isRevokingApiKey;

  const workflowState = !token
    ? 'Login pendente'
    : isBootstrapping
      ? 'Carregando sessão'
      : !hasOpenAiKey
        ? 'Cadastre sua chave OpenAI'
        : isUploading
          ? 'Enviando vídeo'
          : isAnalyzing
            ? 'Processando IA'
          : isAsking
              ? 'Respondendo nova pergunta'
              : uploaded?.filename
                ? 'Vídeo enviado'
                : 'Aguardando arquivo';
  const usageSummary = usageDashboard?.summary || null;
  const usageByEndpoint = Array.isArray(usageDashboard?.by_endpoint) ? usageDashboard.by_endpoint : [];
  const usageTimeline = Array.isArray(usageDashboard?.timeline) ? usageDashboard.timeline : [];

  const videoUrl = useMemo(() => {
    if (!uploaded?.stored_filename || !token) {
      return '';
    }
    return endpoint(`/video/${encodeURIComponent(uploaded.stored_filename)}?token=${encodeURIComponent(token)}`);
  }, [uploaded, token]);

  function persistToken(nextToken) {
    if (nextToken) {
      localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, nextToken);
    } else {
      localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    }
    setToken(nextToken);
  }

  function resetAnalysisState() {
    autoAnalyzeAttemptRef.current = '';
    setFile(null);
    setQuestion('');
    setUploaded(null);
    setAnalysisId('');
    setInsights('');
    setTranscription([]);
    setChatInput('');
    setChatMessages([]);
    setActiveTab('insights');
    setProgressText('Aguardando envio...');
  }

  function clearAuthSession(message) {
    persistToken('');
    setCurrentUser(null);
    setApiKeyStatus({ is_active: false, masked_key: null, rotated_at: null });
    setApiKeyInput('');
    setUsageDashboard(null);
    setIsUsageLoading(false);
    resetAnalysisState();
    if (message) {
      setError(message);
    }
  }

  function buildAuthHeaders(extra = {}, authToken = token) {
    if (!authToken) {
      return extra;
    }
    return {
      ...extra,
      Authorization: `Bearer ${authToken}`
    };
  }

  async function refreshBackendHealth({ showError = true } = {}) {
    try {
      const response = await fetch(endpoint('/health'), { cache: 'no-store' });
      const data = await readApiPayload(response);
      const parsedLimit = Number(data.max_file_size_mb);
      const healthy = response.ok && data.success && Number.isFinite(parsedLimit) && parsedLimit > 0;

      if (!healthy) {
        setBackendReachable(false);
        if (showError) {
          setError('Backend indisponível ou com resposta inválida em /health. Verifique o servidor Flask.');
        }
        return null;
      }

      setBackendReachable(true);
      setMaxUploadMb(parsedLimit);
      return parsedLimit;
    } catch {
      setBackendReachable(false);
      if (showError) {
        setError('Backend indisponível. Inicie o Flask em http://localhost:5000.');
      }
      return null;
    }
  }

  async function loadKeyStatus(authToken = token) {
    if (!authToken) {
      return;
    }

    const response = await fetch(endpoint('/integrations/openai-key/status'), {
      headers: buildAuthHeaders({}, authToken)
    });
    const data = await readApiPayload(response);

    if (response.status === 401) {
      throw new Error('Sessão expirada. Faça login novamente.');
    }
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Falha ao consultar status da integração OpenAI.');
    }

    setApiKeyStatus({
      is_active: Boolean(data.is_active),
      masked_key: data.masked_key || null,
      rotated_at: data.rotated_at || null
    });
  }

  async function loadUsageDashboard(daysOverride = usageDays, authToken = token, silent = false) {
    if (!authToken) {
      return;
    }

    if (!silent) {
      setIsUsageLoading(true);
    }

    try {
      const response = await fetch(endpoint(`/usage/dashboard?days=${encodeURIComponent(daysOverride)}`), {
        headers: buildAuthHeaders({}, authToken)
      });
      const data = await readApiPayload(response);

      if (response.status === 401) {
        throw new Error('Sessão expirada. Faça login novamente.');
      }
      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Falha ao carregar dashboard de consumo.');
      }

      setUsageDashboard(data);
    } catch (dashboardError) {
      if (dashboardError?.message?.includes('Sessão expirada')) {
        clearAuthSession(dashboardError.message);
        return;
      }
      setError(dashboardError.message || 'Falha ao carregar dashboard de consumo.');
    } finally {
      if (!silent) {
        setIsUsageLoading(false);
      }
    }
  }

  async function bootstrapSession(authToken) {
    if (!authToken) {
      return;
    }

    setIsBootstrapping(true);
    try {
      const meResponse = await fetch(endpoint('/auth/me'), {
        headers: buildAuthHeaders({}, authToken)
      });
      const meData = await readApiPayload(meResponse);

      if (meResponse.status === 401) {
        throw new Error('Sessão expirada. Faça login novamente.');
      }
      if (!meResponse.ok || !meData.success) {
        throw new Error(meData.error || 'Falha ao validar sessão atual.');
      }

      setCurrentUser(meData.user || null);
      await loadKeyStatus(authToken);
      await loadUsageDashboard(usageDays, authToken, true);
      setError('');
    } catch (sessionError) {
      clearAuthSession(sessionError.message);
    } finally {
      setIsBootstrapping(false);
    }
  }

  useEffect(() => {
    if (!token) {
      return;
    }

    bootstrapSession(token);
  }, [token]);

  useEffect(() => {
    refreshBackendHealth();
  }, []);

  useEffect(() => {
    async function loadPasswordPolicy() {
      try {
        const response = await fetch(endpoint('/auth/password-policy'));
        const data = await readApiPayload(response);
        if (!response.ok || !data.success) {
          return;
        }
        setPasswordPolicy({
          min_length: Number(data.min_length) || 10,
          max_length: Number(data.max_length) || 128,
          requires_lowercase: Boolean(data.requires_lowercase),
          requires_uppercase: Boolean(data.requires_uppercase),
          requires_number: Boolean(data.requires_number),
          requires_symbol: Boolean(data.requires_symbol),
          no_whitespace: Boolean(data.no_whitespace)
        });
      } catch {
        // Mantem fallback local se endpoint indisponivel.
      }
    }

    loadPasswordPolicy();
  }, []);

  useEffect(() => {
    if (!token) {
      return;
    }

    loadUsageDashboard(usageDays, token, true);
  }, [usageDays]);

  useEffect(() => {
    if (!token || !hasOpenAiKey || !uploaded?.stored_filename || analysisId || isAnalyzing) {
      return;
    }

    if (autoAnalyzeAttemptRef.current === uploaded.stored_filename) {
      return;
    }

    autoAnalyzeAttemptRef.current = uploaded.stored_filename;
    setProgressText('Vídeo enviado. Iniciando análise automática...');
    void analyzeVideo(uploaded.stored_filename);
  }, [analysisId, apiKeyRotationMarker, hasOpenAiKey, isAnalyzing, token, uploaded]);

  function jumpToTime(seconds) {
    if (!videoRef.current || Number.isNaN(seconds)) {
      return;
    }
    videoRef.current.currentTime = seconds;
    videoRef.current.play().catch(() => {});
  }

  async function handleAuthSubmit(event) {
    event.preventDefault();

    if (!backendReachable) {
      setError('Backend indisponível. Inicie o Flask em http://localhost:5000.');
      return;
    }

    const email = authEmail.trim();
    const password = authPassword;

    if (!email || !password) {
      setError('Preencha email e senha para continuar.');
      return;
    }
    if (authMode === 'register' && password !== authPasswordConfirm) {
      setError('Confirmação de senha diferente da senha informada.');
      return;
    }
    if (authMode === 'register' && password.length < Number(passwordPolicy.min_length || 10)) {
      setError(`Senha deve ter pelo menos ${Number(passwordPolicy.min_length || 10)} caracteres.`);
      return;
    }

    setError('');
    setIsAuthLoading(true);

    const route = authMode === 'register' ? '/auth/register' : '/auth/login';

    try {
      const response = await fetch(endpoint(route), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          email,
          password
        })
      });

      const data = await readApiPayload(response);
      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Falha ao autenticar usuário.');
      }

      persistToken(data.token || '');
      setCurrentUser(data.user || null);
      setAuthPassword('');
      setAuthPasswordConfirm('');
      setProgressText('Conta autenticada. Configure sua chave OpenAI para usar IA.');
    } catch (authError) {
      setError(authError.message);
    } finally {
      setIsAuthLoading(false);
    }
  }

  async function handleSaveApiKey(event) {
    event.preventDefault();

    if (!token) {
      setError('Faça login para configurar a chave OpenAI.');
      return;
    }

    const apiKey = apiKeyInput.trim();
    if (!apiKey) {
      setError('Cole sua chave OpenAI para salvar.');
      return;
    }

    setError('');
    setIsSavingApiKey(true);

    try {
      const response = await fetch(endpoint('/integrations/openai-key'), {
        method: 'POST',
        headers: buildAuthHeaders({
          'Content-Type': 'application/json'
        }),
        body: JSON.stringify({ api_key: apiKey })
      });

      const data = await readApiPayload(response);
      if (response.status === 401) {
        clearAuthSession('Sessão expirada. Faça login novamente.');
        return;
      }
      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Falha ao salvar chave OpenAI.');
      }

      setApiKeyInput('');
      autoAnalyzeAttemptRef.current = '';
      setApiKeyStatus({
        is_active: true,
        masked_key: data.masked_key || null,
        rotated_at: Date.now()
      });
      setProgressText('Chave OpenAI validada e ativa.');
    } catch (integrationError) {
      setError(integrationError.message);
    } finally {
      setIsSavingApiKey(false);
    }
  }

  async function handleRevokeApiKey() {
    if (!token) {
      setError('Faça login para revogar a chave OpenAI.');
      return;
    }

    setError('');
    setIsRevokingApiKey(true);

    try {
      const response = await fetch(endpoint('/integrations/openai-key'), {
        method: 'DELETE',
        headers: buildAuthHeaders()
      });
      const data = await readApiPayload(response);

      if (response.status === 401) {
        clearAuthSession('Sessão expirada. Faça login novamente.');
        return;
      }
      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Falha ao revogar chave OpenAI.');
      }

      setApiKeyStatus({ is_active: false, masked_key: null, rotated_at: null });
      autoAnalyzeAttemptRef.current = '';
      setAnalysisId('');
      setProgressText('Chave OpenAI revogada.');
    } catch (integrationError) {
      setError(integrationError.message);
    } finally {
      setIsRevokingApiKey(false);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();

    if (!token) {
      setError('Faça login para enviar vídeos.');
      return;
    }

    if (!file) {
      setError('Selecione um arquivo de vídeo antes de enviar.');
      return;
    }

    const latestMaxUploadMb = await refreshBackendHealth({ showError: false });
    if (!latestMaxUploadMb) {
      setError('Backend indisponível. Inicie o Flask em http://localhost:5000.');
      return;
    }

    if (file.size > latestMaxUploadMb * 1024 * 1024) {
      setError(
        `Arquivo excede o limite de ${latestMaxUploadMb}MB. Ajuste MAX_FILE_SIZE_MB no backend ou use um arquivo menor.`
      );
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
        headers: buildAuthHeaders(),
        body: formData
      });

      const data = await readApiPayload(response);
      if (response.status === 401) {
        clearAuthSession('Sessão expirada. Faça login novamente.');
        return;
      }
      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Erro ao enviar o vídeo.');
      }

      setUploaded(data);
      autoAnalyzeAttemptRef.current = '';
      if (!hasOpenAiKey) {
        setProgressText('Upload concluído. Cadastre sua chave para liberar a análise automática.');
        return;
      }
    } catch (uploadError) {
      const rawMessage = String(uploadError?.message || '');
      const isNetworkError =
        uploadError instanceof TypeError ||
        rawMessage.includes('Failed to fetch') ||
        rawMessage.includes('NetworkError');
      if (isNetworkError) {
        setError(
          `Falha de conexão no upload. Verifique se o backend está rodando em http://localhost:5000 e se o arquivo está dentro de ${latestMaxUploadMb}MB.`
        );
      } else {
        setError(uploadError.message);
      }
      setProgressText('Falha no upload.');
    } finally {
      setIsUploading(false);
    }
  }

  async function analyzeVideo(storedFilename) {
    if (!token) {
      setError('Faça login para analisar vídeos.');
      return false;
    }

    if (!hasOpenAiKey) {
      setError('Cadastre sua chave para usar IA.');
      return false;
    }

    if (!storedFilename) {
      setError('Faça o upload de um vídeo primeiro.');
      return false;
    }

    setError('');
    setIsAnalyzing(true);
    setProgressText('Upload concluído. Transcrevendo e analisando automaticamente...');

    try {
      const response = await fetch(endpoint('/analyze'), {
        method: 'POST',
        headers: buildAuthHeaders({
          'Content-Type': 'application/json'
        }),
        body: JSON.stringify({
          stored_filename: storedFilename,
          question
        })
      });

      const data = await readApiPayload(response);
      if (response.status === 401) {
        clearAuthSession('Sessão expirada. Faça login novamente.');
        return false;
      }
      if (!response.ok || !data.success) {
        if (response.status === 502 || data.code === 'provider_failure') {
          throw new Error(
            'Falha na OpenAI (502). Verifique se a chave está válida, com créditos e sem bloqueio de rede.'
          );
        }
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
      loadUsageDashboard(usageDays, token, true);
      return true;
    } catch (analysisError) {
      setError(analysisError.message);
      setProgressText('Falha durante a análise.');
      return false;
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

    if (!hasOpenAiKey) {
      setError('Cadastre sua chave para usar IA.');
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
        headers: buildAuthHeaders({
          'Content-Type': 'application/json'
        }),
        body: JSON.stringify({
          analysis_id: analysisId,
          question: prompt
        })
      });

      const data = await readApiPayload(response);
      if (response.status === 401) {
        clearAuthSession('Sessão expirada. Faça login novamente.');
        return;
      }
      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Erro ao consultar o assistente.');
      }

      setChatMessages((previous) => [...previous, { role: 'assistant', content: data.answer || 'Sem resposta.' }]);
      setProgressText('Resposta gerada com sucesso.');
      loadUsageDashboard(usageDays, token, true);
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

  function handleLogout() {
    clearAuthSession('Sessão encerrada.');
  }

  return (
    <div className="app-shell">
      <div className="bg-shape bg-shape-a" />
      <div className="bg-shape bg-shape-b" />
      <header className="hero">
        <p className="eyebrow">Analise de Reuniao</p>
        <h1>Análise de Vídeo com IA (BYOK)</h1>
        <p className="subtitle">
          Faça login, conecte sua chave OpenAI e analise reuniões sem expor segredos no front-end.
        </p>
        <div className="hero-metrics hero-metrics-wide">
          <article className="metric-card">
            <p>Conta</p>
            <strong>{currentUser?.email || 'Não autenticado'}</strong>
          </article>
          <article className="metric-card">
            <p>Integração OpenAI</p>
            <strong>{hasOpenAiKey ? apiKeyStatus.masked_key || 'Ativa' : 'Não configurada'}</strong>
          </article>
          <article className="metric-card">
            <p>Arquivo</p>
            <strong>{uploaded?.filename || 'Nenhum enviado'}</strong>
          </article>
          <article className="metric-card">
            <p>Status</p>
            <strong>{workflowState}</strong>
          </article>
        </div>
        <div className="flow-steps">
          <span className={`flow-step ${token ? 'flow-step-done' : ''}`}>1. Login</span>
          <span className={`flow-step ${hasOpenAiKey ? 'flow-step-done' : ''}`}>2. Chave OpenAI</span>
          <span className={`flow-step ${uploaded ? 'flow-step-done' : ''}`}>3. Upload</span>
          <span className={`flow-step ${hasResult ? 'flow-step-done' : ''}`}>4. Análise</span>
          <span className={`flow-step ${chatMessages.length > 2 ? 'flow-step-done' : ''}`}>5. Conversa contínua</span>
        </div>
      </header>

      <main className="layout">
        <section className="panel panel-upload">
          {!token ? (
            <>
              <h2>Acesse sua conta</h2>
              <form onSubmit={handleAuthSubmit} className="upload-form">
                <label htmlFor="auth-email">Email</label>
                <input
                  id="auth-email"
                  type="email"
                  value={authEmail}
                  onChange={(event) => setAuthEmail(event.target.value)}
                  placeholder="seu@email.com"
                  required
                />

                <label htmlFor="auth-password">Senha</label>
                <input
                  id="auth-password"
                  type="password"
                  value={authPassword}
                  onChange={(event) => setAuthPassword(event.target.value)}
                  placeholder={`Mínimo ${passwordPolicy.min_length} caracteres`}
                  required
                />

                {authMode === 'register' ? (
                  <>
                    <label htmlFor="auth-password-confirm">Confirmar senha</label>
                    <input
                      id="auth-password-confirm"
                      type="password"
                      value={authPasswordConfirm}
                      onChange={(event) => setAuthPasswordConfirm(event.target.value)}
                      placeholder="Repita a senha"
                      required
                    />
                    <p className="help-text">
                      Use no mínimo {passwordPolicy.min_length} caracteres com maiúscula, minúscula, número e símbolo.
                    </p>
                  </>
                ) : null}

                <button className="btn" type="submit" disabled={isAuthLoading}>
                  {isAuthLoading ? 'Processando...' : authMode === 'register' ? 'Criar conta' : 'Entrar'}
                </button>
                {!backendReachable ? (
                  <p className="help-text">Servidor backend offline. Inicie o Flask para autenticar.</p>
                ) : null}
                <button
                  className="text-btn"
                  type="button"
                  onClick={() => {
                    setAuthMode((mode) => (mode === 'login' ? 'register' : 'login'));
                    setAuthPassword('');
                    setAuthPasswordConfirm('');
                  }}
                  disabled={isAuthLoading}
                >
                  {authMode === 'login' ? 'Não tem conta? Criar agora' : 'Já tem conta? Fazer login'}
                </button>
              </form>
            </>
          ) : (
            <>
              <h2>Conta e integração</h2>
              <div className="account-box">
                <p className="account-title">Usuário autenticado</p>
                <p className="account-email">{currentUser?.email}</p>
                <button className="btn btn-ghost" type="button" onClick={handleLogout}>
                  Sair da conta
                </button>
              </div>

              <form onSubmit={handleSaveApiKey} className="upload-form integration-form">
                <label htmlFor="openai-key">Minha chave OpenAI</label>
                <input
                  id="openai-key"
                  type="password"
                  value={apiKeyInput}
                  onChange={(event) => setApiKeyInput(event.target.value)}
                  placeholder="sk-..."
                />
                <p className="help-text">
                  {hasOpenAiKey
                    ? `Chave ativa: ${apiKeyStatus.masked_key || 'sk-...'} (a chave completa nunca é exibida).`
                    : 'Nenhuma chave ativa. Cadastre para liberar análise e follow-up.'}
                </p>
                <div className="integration-actions">
                  <button className="btn" type="submit" disabled={isSavingApiKey || isRevokingApiKey}>
                    {isSavingApiKey ? 'Validando...' : hasOpenAiKey ? 'Rotacionar chave' : 'Salvar chave'}
                  </button>
                  {hasOpenAiKey ? (
                    <button
                      className="btn btn-danger"
                      type="button"
                      onClick={handleRevokeApiKey}
                      disabled={isSavingApiKey || isRevokingApiKey}
                    >
                      {isRevokingApiKey ? 'Revogando...' : 'Revogar chave'}
                    </button>
                  ) : null}
                </div>
              </form>

              <div className="usage-card">
                <div className="usage-card-header">
                  <p className="account-title">Dashboard interno de consumo</p>
                  <div className="usage-controls">
                    <select
                      value={usageDays}
                      onChange={(event) => setUsageDays(Number(event.target.value))}
                      disabled={isUsageLoading}
                    >
                      <option value={7}>7 dias</option>
                      <option value={30}>30 dias</option>
                      <option value={90}>90 dias</option>
                    </select>
                    <button
                      className="btn btn-ghost btn-small"
                      type="button"
                      onClick={() => loadUsageDashboard(usageDays)}
                      disabled={isUsageLoading}
                    >
                      {isUsageLoading ? 'Atualizando...' : 'Atualizar'}
                    </button>
                  </div>
                </div>

                {usageSummary ? (
                  <>
                    <div className="usage-summary-grid">
                      <article className="usage-mini">
                        <p>Requests</p>
                        <strong>{formatInteger(usageSummary.requests)}</strong>
                      </article>
                      <article className="usage-mini">
                        <p>Input tokens</p>
                        <strong>{formatInteger(usageSummary.input_tokens)}</strong>
                      </article>
                      <article className="usage-mini">
                        <p>Output tokens</p>
                        <strong>{formatInteger(usageSummary.output_tokens)}</strong>
                      </article>
                      <article className="usage-mini">
                        <p>Custo estimado</p>
                        <strong>{formatUsd(usageSummary.estimated_cost)}</strong>
                      </article>
                    </div>

                    <div className="usage-list">
                      <p>Top endpoints no período</p>
                      {usageByEndpoint.length === 0 ? (
                        <span>Sem eventos.</span>
                      ) : (
                        usageByEndpoint.slice(0, 4).map((item) => (
                          <span key={item.endpoint}>
                            {item.endpoint}: {formatInteger(item.requests)} req
                          </span>
                        ))
                      )}
                    </div>

                    <div className="usage-list">
                      <p>Timeline recente</p>
                      {usageTimeline.length === 0 ? (
                        <span>Sem eventos.</span>
                      ) : (
                        usageTimeline.slice(-4).map((item) => (
                          <span key={item.day}>
                            {item.day}: {formatInteger(item.requests)} req
                          </span>
                        ))
                      )}
                    </div>
                  </>
                ) : (
                  <p className="help-text">Nenhum evento de consumo registrado ainda.</p>
                )}
              </div>

              <div className="divider" />

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
                <p className="help-text">Limite atual de upload: {maxUploadMb}MB por arquivo.</p>

                <label htmlFor="question">Pergunta inicial (opcional)</label>
                <input
                  id="question"
                  type="text"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="Ex: Quais decisões foram tomadas?"
                />

                <button className="btn" type="submit" disabled={isUploading || isAnalyzing}>
                  {isUploading ? 'Enviando...' : isAnalyzing ? 'Analisando...' : 'Enviar e analisar'}
                </button>
              </form>
            </>
          )}

          <div className="status-row">
            <span className={`dot ${isBusy ? 'dot-running' : ''}`} />
            <p>{progressText}</p>
          </div>
          <div className="progress-track" aria-hidden="true">
            <span
              className={`progress-fill ${isBusy ? 'progress-fill-running' : ''} ${hasResult ? 'progress-fill-done' : ''}`}
            />
          </div>

          {token && uploaded?.stored_filename ? (
            <div className="video-card">
              <p className="video-label">Arquivo pronto: {uploaded.filename}</p>
              <video ref={videoRef} controls src={videoUrl}>
                Seu navegador não suporta reprodução de vídeo.
              </video>
              {!hasOpenAiKey ? (
                <p className="help-text">Cadastre sua chave para iniciar a análise automática deste vídeo.</p>
              ) : (
                <p className="help-text">A análise começa automaticamente após o upload.</p>
              )}
            </div>
          ) : null}

          {error ? <p className="error-box">{error}</p> : null}
        </section>

        <section className="panel panel-results">
          <div className="result-header">
            <h2>Resultados e assistente</h2>
            <p>Navegue pelos timestamps ou continue perguntando sem reprocessar o vídeo.</p>
          </div>

          {!token ? (
            <div className="empty-state">
              <h3>Faça login para começar</h3>
              <p>1. Crie conta ou entre.</p>
              <p>2. Cadastre sua chave OpenAI.</p>
              <p>3. Envie um vídeo para análise.</p>
            </div>
          ) : !hasOpenAiKey ? (
            <div className="empty-state">
              <h3>Chave OpenAI pendente</h3>
              <p>Cadastre uma chave em “Conta e integração”.</p>
              <p>Sem chave ativa, a análise e o assistente ficam bloqueados.</p>
            </div>
          ) : uploaded?.stored_filename && !hasResult ? (
            <div className="empty-state">
              <h3>Análise automática em andamento</h3>
              <p>O vídeo já foi enviado e a transcrição começa sem clique extra.</p>
              <p>Quando a resposta chegar, ela aparecerá aqui na aba Assistente.</p>
            </div>
          ) : !hasResult ? (
            <div className="empty-state">
              <h3>Fluxo recomendado</h3>
              <p>A experiência foi desenhada para ser direta:</p>
              <ol>
                <li>Envie o vídeo (a análise começa automaticamente).</li>
                <li>Acompanhe o resultado na aba Assistente.</li>
                <li>Use a aba Assistente para pedir checklist, tarefas e novas respostas.</li>
              </ol>
            </div>
          ) : (
            <>
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

              {activeTab === 'assistant' ? (
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

              {activeTab === 'insights' ? (
                <div className="content-card">
                  <p className="hint">Clique em um timestamp para pular para o trecho correspondente do vídeo.</p>
                  <div className="text-flow">{renderTextWithTimestamps(insights, 'insight', jumpToTime)}</div>
                </div>
              ) : null}

              {activeTab === 'transcription' ? (
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
            </>
          )}
        </section>
      </main>
    </div>
  );
}

export default App;
