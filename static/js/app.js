(function () {
  const root = document.getElementById('app-page');
  if (!root) {
    return;
  }

  const AUTH_TOKEN_STORAGE_KEY = 'meeting_analysis_auth_token';
  const QUICK_ACTIONS = [
    {
      label: 'Checklist',
      prompt: 'Gere um checklist objetivo com os itens mais importantes da reunião e cite timestamps.'
    },
    {
      label: 'Lista de tarefas',
      prompt: 'Transforme a reunião em lista de tarefas com: tarefa, responsável sugerido, prioridade e prazo sugerido, citando timestamps.'
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

  const state = {
    token: getStoredToken(),
    currentUser: null,
    authMode: 'login',
    passwordPolicy: {
      min_length: 10,
      max_length: 128,
      requires_lowercase: true,
      requires_uppercase: true,
      requires_number: true,
      requires_symbol: true,
      no_whitespace: true
    },
    apiKeyStatus: {
      is_active: false,
      masked_key: null,
      rotated_at: null
    },
    usageDays: 7,
    usageDashboard: null,
    uploaded: null,
    analysisId: '',
    file: null,
    localVideoUrl: '',
    insights: '',
    transcription: [],
    chatMessages: [],
    activeTab: 'assistant',
    error: '',
    progressText: 'Aguardando envio...',
    backendReachable: true,
    maxUploadMb: 100,
    isUploading: false,
    isAsking: false,
    isAuthLoading: false,
    isBootstrapping: false,
    isSavingApiKey: false,
    isRevokingApiKey: false,
    isUsageLoading: false
  };

  const elements = {
    guestSection: document.getElementById('guest-section'),
    userSection: document.getElementById('user-section'),
    authForm: document.getElementById('auth-form'),
    authEmail: document.getElementById('auth-email'),
    authPassword: document.getElementById('auth-password'),
    authPasswordConfirmGroup: document.getElementById('auth-password-confirm-group'),
    authPasswordConfirm: document.getElementById('auth-password-confirm'),
    authSubmitBtn: document.getElementById('auth-submit-btn'),
    authToggleBtn: document.getElementById('auth-toggle-btn'),
    passwordPolicyHelp: document.getElementById('password-policy-help'),
    currentUserEmail: document.getElementById('current-user-email'),
    logoutBtn: document.getElementById('logout-btn'),
    apiKeyForm: document.getElementById('api-key-form'),
    apiKeyInput: document.getElementById('openai-key'),
    apiKeyHelp: document.getElementById('api-key-help'),
    saveApiKeyBtn: document.getElementById('save-api-key-btn'),
    revokeApiKeyBtn: document.getElementById('revoke-api-key-btn'),
    usageDaysSelect: document.getElementById('usage-days-select'),
    usageRefreshBtn: document.getElementById('usage-refresh-btn'),
    usageSummaryGrid: document.getElementById('usage-summary-grid'),
    usageRequests: document.getElementById('usage-requests'),
    usageInputTokens: document.getElementById('usage-input-tokens'),
    usageOutputTokens: document.getElementById('usage-output-tokens'),
    usageEstimatedCost: document.getElementById('usage-estimated-cost'),
    usageByEndpoint: document.getElementById('usage-by-endpoint'),
    usageTimeline: document.getElementById('usage-timeline'),
    usageEmpty: document.getElementById('usage-empty'),
    uploadForm: document.getElementById('upload-form'),
    fileInput: document.getElementById('video-file'),
    questionInput: document.getElementById('question'),
    uploadLimitHelp: document.getElementById('upload-limit-help'),
    uploadSubmitBtn: document.getElementById('upload-submit-btn'),
    statusDot: document.getElementById('status-dot'),
    progressText: document.getElementById('progress-text'),
    progressFill: document.getElementById('progress-fill'),
    videoCard: document.getElementById('video-card'),
    videoLabel: document.getElementById('video-label'),
    videoPlayer: document.getElementById('video-player'),
    videoHelp: document.getElementById('video-help'),
    errorBox: document.getElementById('error-box'),
    heroAccount: document.getElementById('hero-account'),
    heroKey: document.getElementById('hero-key'),
    heroFile: document.getElementById('hero-file'),
    heroStatus: document.getElementById('hero-status'),
    stepLogin: document.getElementById('step-login'),
    stepKey: document.getElementById('step-key'),
    stepUpload: document.getElementById('step-upload'),
    stepAnalysis: document.getElementById('step-analysis'),
    stepChat: document.getElementById('step-chat'),
    emptyState: document.getElementById('empty-state'),
    resultsShell: document.getElementById('results-shell'),
    quickActions: document.getElementById('quick-actions'),
    chatFeed: document.getElementById('chat-feed'),
    followupForm: document.getElementById('followup-form'),
    followupQuestion: document.getElementById('followup-question'),
    followupSubmitBtn: document.getElementById('followup-submit-btn'),
    insightsContent: document.getElementById('insights-content'),
    transcriptionContent: document.getElementById('transcription-content'),
    tabAssistantCount: document.getElementById('tab-assistant-count'),
    tabInsightsCount: document.getElementById('tab-insights-count'),
    tabTranscriptionCount: document.getElementById('tab-transcription-count')
  };

  function getStoredToken() {
    return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || '';
  }

  function endpoint(path) {
    return path;
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

  function formatTime(value) {
    return Number.parseFloat(value || 0).toFixed(2);
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
    const matches = [...String(line || '').matchAll(/\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]/g)];
    if (matches.length === 0) {
      return [{ type: 'text', value: String(line || '') }];
    }

    const tokens = [];
    let lastIndex = 0;

    for (const match of matches) {
      const raw = match[0];
      const start = match[1];
      const index = match.index || 0;

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

  function makeTimestampButton(label, start) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'timestamp';
    button.dataset.start = String(start);
    button.title = `Ir para ${label}`;
    button.textContent = label;
    return button;
  }

  function appendRichText(container, text) {
    const lines = String(text || '')
      .split(/\n+/)
      .filter((line) => line.trim().length > 0);

    if (lines.length === 0) {
      const paragraph = document.createElement('p');
      paragraph.textContent = 'Sem conteúdo.';
      container.appendChild(paragraph);
      return;
    }

    lines.forEach((line) => {
      const paragraph = document.createElement('p');
      parseLineWithTimestamps(line).forEach((token) => {
        if (token.type === 'timestamp') {
          paragraph.appendChild(makeTimestampButton(token.value, token.start));
        } else {
          paragraph.appendChild(document.createTextNode(token.value));
        }
      });
      container.appendChild(paragraph);
    });
  }

  function hasOpenAiKey() {
    return Boolean(state.apiKeyStatus.is_active);
  }

  function hasResult() {
    return state.insights.trim().length > 0 || state.transcription.length > 0;
  }

  function canUseAssistant() {
    return Boolean(state.analysisId) && hasOpenAiKey();
  }

  function isBusy() {
    return (
      state.isUploading ||
      state.isAsking ||
      state.isAuthLoading ||
      state.isBootstrapping ||
      state.isSavingApiKey ||
      state.isRevokingApiKey
    );
  }

  function workflowState() {
    if (!state.token) {
      return 'Login pendente';
    }
    if (state.isBootstrapping) {
      return 'Carregando sessão';
    }
    if (!hasOpenAiKey()) {
      return 'Cadastre sua chave OpenAI';
    }
    if (state.isUploading) {
      return 'Enviando e analisando';
    }
    if (state.isAsking) {
      return 'Respondendo nova pergunta';
    }
    if (state.uploaded && state.uploaded.filename) {
      return 'Análise concluída';
    }
    if (state.file && state.file.name) {
      return 'Arquivo selecionado';
    }
    return 'Aguardando arquivo';
  }

  function persistToken(nextToken) {
    if (nextToken) {
      localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, nextToken);
    } else {
      localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    }
    state.token = nextToken || '';
  }

  function buildAuthHeaders(extra, authToken) {
    const headers = Object.assign({}, extra || {});
    const token = authToken || state.token;
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    return headers;
  }

  function revokeLocalVideoUrl() {
    if (state.localVideoUrl) {
      URL.revokeObjectURL(state.localVideoUrl);
      state.localVideoUrl = '';
    }
  }

  function resetAnalysisOutput() {
    state.uploaded = null;
    state.analysisId = '';
    state.insights = '';
    state.transcription = [];
    state.chatMessages = [];
    state.activeTab = 'insights';
  }

  function resetAnalysisState(clearSelectedFile) {
    resetAnalysisOutput();
    state.question = '';
    elements.questionInput.value = '';
    elements.followupQuestion.value = '';
    if (clearSelectedFile) {
      state.file = null;
      elements.fileInput.value = '';
      revokeLocalVideoUrl();
    }
  }

  function clearAuthSession(message) {
    persistToken('');
    state.currentUser = null;
    state.apiKeyStatus = { is_active: false, masked_key: null, rotated_at: null };
    state.usageDashboard = null;
    state.isUsageLoading = false;
    elements.apiKeyInput.value = '';
    resetAnalysisState(true);
    elements.authPassword.value = '';
    elements.authPasswordConfirm.value = '';
    setError(message || '');
    render();
  }

  function setError(message) {
    state.error = String(message || '').trim();
    elements.errorBox.hidden = state.error.length === 0;
    elements.errorBox.textContent = state.error;
  }

  function setProgressText(message) {
    state.progressText = message;
    elements.progressText.textContent = message;
  }

  function setSelectedFile(nextFile) {
    state.file = nextFile || null;
    state.uploaded = null;
    state.analysisId = '';
    state.insights = '';
    state.transcription = [];
    state.chatMessages = [];
    state.activeTab = 'insights';
    state.error = '';
    elements.followupQuestion.value = '';
    elements.questionInput.value = '';
    revokeLocalVideoUrl();

    if (nextFile) {
      state.localVideoUrl = URL.createObjectURL(nextFile);
      setProgressText('Arquivo selecionado. Pronto para enviar e analisar.');
    } else {
      setProgressText('Aguardando envio...');
    }
    setError('');
    render();
  }

  async function refreshBackendHealth(options) {
    const showError = !options || options.showError !== false;

    try {
      const response = await fetch(endpoint('/health'), { cache: 'no-store' });
      const data = await readApiPayload(response);
      const parsedLimit = Number(data.max_file_size_mb);
      const healthy = response.ok && data.success && Number.isFinite(parsedLimit) && parsedLimit > 0;

      if (!healthy) {
        state.backendReachable = false;
        if (showError) {
          setError('Backend indisponível ou com resposta inválida em /health. Verifique o servidor Flask.');
        }
        render();
        return null;
      }

      state.backendReachable = true;
      state.maxUploadMb = parsedLimit;
      render();
      return parsedLimit;
    } catch {
      state.backendReachable = false;
      if (showError) {
        setError('Backend indisponível. Inicie o Flask para usar a aplicação.');
      }
      render();
      return null;
    }
  }

  async function loadPasswordPolicy() {
    try {
      const response = await fetch(endpoint('/auth/password-policy'));
      const data = await readApiPayload(response);
      if (!response.ok || !data.success) {
        return;
      }

      state.passwordPolicy = {
        min_length: Number(data.min_length) || 10,
        max_length: Number(data.max_length) || 128,
        requires_lowercase: Boolean(data.requires_lowercase),
        requires_uppercase: Boolean(data.requires_uppercase),
        requires_number: Boolean(data.requires_number),
        requires_symbol: Boolean(data.requires_symbol),
        no_whitespace: Boolean(data.no_whitespace)
      };
      render();
    } catch {
      // Mantém fallback local.
    }
  }

  async function loadKeyStatus(authToken) {
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

    state.apiKeyStatus = {
      is_active: Boolean(data.is_active),
      masked_key: data.masked_key || null,
      rotated_at: data.rotated_at || null
    };
  }

  async function loadUsageDashboard(daysOverride, authToken, silent) {
    if (!authToken) {
      return;
    }

    if (!silent) {
      state.isUsageLoading = true;
      render();
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

      state.usageDashboard = data;
      state.usageDays = Number(daysOverride);
    } catch (error) {
      if (String(error && error.message || '').includes('Sessão expirada')) {
        clearAuthSession(error.message);
        return;
      }
      setError(error.message || 'Falha ao carregar dashboard de consumo.');
    } finally {
      state.isUsageLoading = false;
      render();
    }
  }

  async function bootstrapSession(authToken) {
    if (!authToken) {
      return;
    }

    state.isBootstrapping = true;
    render();

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

      state.currentUser = meData.user || null;
      await loadKeyStatus(authToken);
      await loadUsageDashboard(state.usageDays, authToken, true);
      setError('');
    } catch (error) {
      clearAuthSession(error.message);
      return;
    } finally {
      state.isBootstrapping = false;
      render();
    }
  }

  async function handleAuthSubmit(event) {
    event.preventDefault();

    if (!state.backendReachable) {
      setError('Backend indisponível. Inicie o Flask para continuar.');
      render();
      return;
    }

    const email = elements.authEmail.value.trim();
    const password = elements.authPassword.value;

    if (!email || !password) {
      setError('Preencha email e senha para continuar.');
      render();
      return;
    }
    if (state.authMode === 'register' && password !== elements.authPasswordConfirm.value) {
      setError('Confirmação de senha diferente da senha informada.');
      render();
      return;
    }
    if (state.authMode === 'register' && password.length < Number(state.passwordPolicy.min_length || 10)) {
      setError(`Senha deve ter pelo menos ${Number(state.passwordPolicy.min_length || 10)} caracteres.`);
      render();
      return;
    }

    setError('');
    state.isAuthLoading = true;
    render();

    const route = state.authMode === 'register' ? '/auth/register' : '/auth/login';

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
      state.currentUser = data.user || null;
      elements.authPassword.value = '';
      elements.authPasswordConfirm.value = '';
      setProgressText('Conta autenticada. Configure sua chave OpenAI para usar IA.');
      await bootstrapSession(state.token);
    } catch (error) {
      setError(error.message || 'Falha ao autenticar usuário.');
    } finally {
      state.isAuthLoading = false;
      render();
    }
  }

  async function handleSaveApiKey(event) {
    event.preventDefault();

    if (!state.token) {
      setError('Faça login para configurar a chave OpenAI.');
      render();
      return;
    }

    const apiKey = elements.apiKeyInput.value.trim();
    if (!apiKey) {
      setError('Cole sua chave OpenAI para salvar.');
      render();
      return;
    }

    setError('');
    state.isSavingApiKey = true;
    render();

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

      elements.apiKeyInput.value = '';
      state.apiKeyStatus = {
        is_active: true,
        masked_key: data.masked_key || null,
        rotated_at: Date.now()
      };
      setProgressText('Chave OpenAI validada e ativa.');
      await loadUsageDashboard(state.usageDays, state.token, true);
    } catch (error) {
      setError(error.message || 'Falha ao salvar chave OpenAI.');
    } finally {
      state.isSavingApiKey = false;
      render();
    }
  }

  async function handleRevokeApiKey() {
    if (!state.token) {
      setError('Faça login para revogar a chave OpenAI.');
      render();
      return;
    }

    setError('');
    state.isRevokingApiKey = true;
    render();

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

      state.apiKeyStatus = { is_active: false, masked_key: null, rotated_at: null };
      state.analysisId = '';
      setProgressText('Chave OpenAI revogada.');
    } catch (error) {
      setError(error.message || 'Falha ao revogar chave OpenAI.');
    } finally {
      state.isRevokingApiKey = false;
      render();
    }
  }

  async function handleUpload(event) {
    event.preventDefault();

    if (!state.token) {
      setError('Faça login para enviar vídeos.');
      render();
      return;
    }
    if (!hasOpenAiKey()) {
      setError('Cadastre sua chave OpenAI antes de enviar o vídeo.');
      render();
      return;
    }
    if (!state.file) {
      setError('Selecione um arquivo de vídeo antes de enviar.');
      render();
      return;
    }

    const latestMaxUploadMb = await refreshBackendHealth({ showError: false });
    if (!latestMaxUploadMb) {
      setError('Backend indisponível. Inicie o Flask para usar a aplicação.');
      render();
      return;
    }

    if (state.file.size > latestMaxUploadMb * 1024 * 1024) {
      setError(`Arquivo excede o limite de ${latestMaxUploadMb}MB. Ajuste MAX_FILE_SIZE_MB no backend ou use um arquivo menor.`);
      render();
      return;
    }

    setError('');
    state.isUploading = true;
    resetAnalysisOutput();
    setProgressText('Enviando vídeo para análise transitória...');
    render();

    try {
      const formData = new FormData();
      formData.append('file', state.file);
      formData.append('question', elements.questionInput.value.trim());

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
        if (response.status === 502 || data.code === 'provider_failure') {
          throw new Error('Falha na OpenAI (502). Verifique se a chave está válida, com créditos e sem bloqueio de rede.');
        }
        throw new Error(data.error || 'Erro ao enviar o vídeo.');
      }

      const normalizedInsights = data.insights || '';
      const initialMessages = [];
      const initialQuestion = elements.questionInput.value.trim();

      if (initialQuestion) {
        initialMessages.push({ role: 'user', content: initialQuestion });
      }
      if (normalizedInsights.trim()) {
        initialMessages.push({ role: 'assistant', content: normalizedInsights });
      }

      state.uploaded = {
        filename: data.filename || state.file.name,
        video_retained: Boolean(data.video_retained)
      };
      state.analysisId = data.analysis_id || '';
      state.insights = normalizedInsights;
      state.transcription = Array.isArray(data.transcription) ? data.transcription : [];
      state.chatMessages = initialMessages;
      state.activeTab = 'assistant';
      setProgressText('Análise concluída. O vídeo foi processado temporariamente e descartado do servidor.');
      await loadUsageDashboard(state.usageDays, state.token, true);
    } catch (error) {
      const rawMessage = String((error && error.message) || '');
      const isNetworkError =
        error instanceof TypeError ||
        rawMessage.includes('Failed to fetch') ||
        rawMessage.includes('NetworkError');

      if (isNetworkError) {
        setError(`Falha de conexão no envio. Verifique se o backend está rodando e se o arquivo está dentro de ${latestMaxUploadMb}MB.`);
      } else {
        setError(rawMessage || 'Erro ao enviar o vídeo.');
      }
      setProgressText('Falha no envio/análise.');
    } finally {
      state.isUploading = false;
      render();
    }
  }

  async function askFollowup(promptOverride) {
    const prompt = String(promptOverride != null ? promptOverride : elements.followupQuestion.value).trim();

    if (!prompt) {
      setError('Digite uma pergunta para o assistente.');
      render();
      return;
    }
    if (!state.analysisId) {
      setError('Faça a análise do vídeo antes de perguntar.');
      render();
      return;
    }
    if (!hasOpenAiKey()) {
      setError('Cadastre sua chave para usar IA.');
      render();
      return;
    }

    setError('');
    state.isAsking = true;
    state.activeTab = 'assistant';
    if (promptOverride == null) {
      elements.followupQuestion.value = '';
    }

    state.chatMessages.push({ role: 'user', content: prompt });
    render();

    try {
      const response = await fetch(endpoint('/followup'), {
        method: 'POST',
        headers: buildAuthHeaders({
          'Content-Type': 'application/json'
        }),
        body: JSON.stringify({
          analysis_id: state.analysisId,
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

      state.chatMessages.push({ role: 'assistant', content: data.answer || 'Sem resposta.' });
      setProgressText('Resposta gerada com sucesso.');
      await loadUsageDashboard(state.usageDays, state.token, true);
    } catch (error) {
      setError(error.message || 'Erro ao consultar o assistente.');
      setProgressText('Falha ao responder pergunta.');
    } finally {
      state.isAsking = false;
      render();
    }
  }

  function jumpToTime(seconds) {
    if (!elements.videoPlayer || Number.isNaN(seconds)) {
      return;
    }

    elements.videoPlayer.currentTime = seconds;
    elements.videoPlayer.play().catch(function () {});
  }

  function renderHero() {
    elements.heroAccount.textContent = state.currentUser && state.currentUser.email ? state.currentUser.email : 'Não autenticado';
    elements.heroKey.textContent = hasOpenAiKey() ? state.apiKeyStatus.masked_key || 'Ativa' : 'Não configurada';
    elements.heroFile.textContent = state.uploaded && state.uploaded.filename ? state.uploaded.filename : 'Nenhum enviado';
    elements.heroStatus.textContent = workflowState();
  }

  function renderSteps() {
    elements.stepLogin.classList.toggle('flow-step-done', Boolean(state.token));
    elements.stepKey.classList.toggle('flow-step-done', hasOpenAiKey());
    elements.stepUpload.classList.toggle('flow-step-done', Boolean(state.uploaded));
    elements.stepAnalysis.classList.toggle('flow-step-done', hasResult());
    elements.stepChat.classList.toggle('flow-step-done', state.chatMessages.length > 2);
  }

  function renderAuthSections() {
    const loggedIn = Boolean(state.token);
    elements.guestSection.hidden = loggedIn;
    elements.userSection.hidden = !loggedIn;

    if (!loggedIn) {
      const isRegister = state.authMode === 'register';
      elements.authPasswordConfirmGroup.hidden = !isRegister;
      elements.authPasswordConfirm.required = isRegister;
      elements.authSubmitBtn.textContent = state.isAuthLoading ? 'Processando...' : (isRegister ? 'Criar conta' : 'Entrar');
      elements.authToggleBtn.textContent = isRegister ? 'Já tem conta? Fazer login' : 'Não tem conta? Criar agora';
      elements.authToggleBtn.disabled = state.isAuthLoading;
      elements.authSubmitBtn.disabled = state.isAuthLoading;
      elements.authEmail.disabled = state.isAuthLoading;
      elements.authPassword.disabled = state.isAuthLoading;
      elements.authPasswordConfirm.disabled = state.isAuthLoading;
      elements.authPassword.placeholder = `Mínimo ${state.passwordPolicy.min_length} caracteres`;
      elements.passwordPolicyHelp.textContent = `Use no mínimo ${state.passwordPolicy.min_length} caracteres com maiúscula, minúscula, número e símbolo.`;
      return;
    }

    elements.currentUserEmail.textContent = state.currentUser && state.currentUser.email ? state.currentUser.email : 'Sessão carregada';
    elements.logoutBtn.disabled = isBusy();
  }

  function renderApiKeyCard() {
    const active = hasOpenAiKey();
    elements.apiKeyHelp.textContent = active
      ? `Chave ativa: ${state.apiKeyStatus.masked_key || 'sk-...'} (a chave completa nunca é exibida).`
      : 'Nenhuma chave ativa. Cadastre para liberar análise e follow-up.';
    elements.saveApiKeyBtn.textContent = state.isSavingApiKey
      ? 'Validando...'
      : (active ? 'Rotacionar chave' : 'Salvar chave');
    elements.saveApiKeyBtn.disabled = state.isSavingApiKey || state.isRevokingApiKey;
    elements.apiKeyInput.disabled = state.isSavingApiKey || state.isRevokingApiKey;
    elements.revokeApiKeyBtn.hidden = !active;
    elements.revokeApiKeyBtn.disabled = state.isSavingApiKey || state.isRevokingApiKey;
    elements.revokeApiKeyBtn.textContent = state.isRevokingApiKey ? 'Revogando...' : 'Revogar chave';
  }

  function renderUsageDashboard() {
    const dashboard = state.usageDashboard;
    const summary = dashboard && dashboard.summary ? dashboard.summary : null;
    const byEndpoint = dashboard && Array.isArray(dashboard.by_endpoint) ? dashboard.by_endpoint : [];
    const timeline = dashboard && Array.isArray(dashboard.timeline) ? dashboard.timeline : [];

    elements.usageDaysSelect.value = String(state.usageDays);
    elements.usageDaysSelect.disabled = state.isUsageLoading;
    elements.usageRefreshBtn.disabled = state.isUsageLoading || !state.token;
    elements.usageRefreshBtn.textContent = state.isUsageLoading ? 'Atualizando...' : 'Atualizar';

    elements.usageByEndpoint.innerHTML = '';
    elements.usageTimeline.innerHTML = '';

    if (!summary) {
      elements.usageSummaryGrid.hidden = true;
      elements.usageEmpty.hidden = false;
      elements.usageEmpty.textContent = 'Nenhum evento de consumo registrado ainda.';
      return;
    }

    elements.usageSummaryGrid.hidden = false;
    elements.usageEmpty.hidden = byEndpoint.length > 0 || timeline.length > 0;
    elements.usageRequests.textContent = formatInteger(summary.requests);
    elements.usageInputTokens.textContent = formatInteger(summary.input_tokens);
    elements.usageOutputTokens.textContent = formatInteger(summary.output_tokens);
    elements.usageEstimatedCost.textContent = formatUsd(summary.estimated_cost);

    if (byEndpoint.length === 0) {
      const span = document.createElement('span');
      span.textContent = 'Sem eventos.';
      elements.usageByEndpoint.appendChild(span);
    } else {
      byEndpoint.slice(0, 4).forEach((item) => {
        const span = document.createElement('span');
        span.textContent = `${item.endpoint}: ${formatInteger(item.requests)} req`;
        elements.usageByEndpoint.appendChild(span);
      });
    }

    if (timeline.length === 0) {
      const span = document.createElement('span');
      span.textContent = 'Sem eventos.';
      elements.usageTimeline.appendChild(span);
    } else {
      timeline.slice(-4).forEach((item) => {
        const span = document.createElement('span');
        span.textContent = `${item.day}: ${formatInteger(item.requests)} req`;
        elements.usageTimeline.appendChild(span);
      });
    }
  }

  function renderStatus() {
    elements.progressText.textContent = state.progressText;
    elements.statusDot.classList.toggle('dot-running', isBusy());

    elements.progressFill.className = 'progress-fill';
    if (isBusy()) {
      elements.progressFill.classList.add('progress-fill-running');
    }
    if (hasResult()) {
      elements.progressFill.classList.add('progress-fill-done');
    }
  }

  function renderVideoCard() {
    if (!state.localVideoUrl) {
      elements.videoCard.hidden = true;
      elements.videoPlayer.removeAttribute('src');
      elements.videoPlayer.load();
      return;
    }

    elements.videoCard.hidden = false;
    elements.videoLabel.textContent = `Preview local: ${state.uploaded && state.uploaded.filename ? state.uploaded.filename : state.file.name}`;
    if (elements.videoPlayer.getAttribute('src') !== state.localVideoUrl) {
      elements.videoPlayer.src = state.localVideoUrl;
    }

    if (!hasOpenAiKey()) {
      elements.videoHelp.textContent = 'Cadastre sua chave para liberar o processamento deste vídeo.';
    } else if (state.isUploading) {
      elements.videoHelp.textContent = 'O backend está processando o vídeo temporariamente e descartará o arquivo ao concluir.';
    } else if (hasResult()) {
      elements.videoHelp.textContent = 'O preview acima é local do navegador. O servidor não mantém cópia do vídeo após a análise.';
    } else {
      elements.videoHelp.textContent = 'Ao enviar, a análise acontece no próprio upload e o vídeo não é persistido no servidor.';
    }
  }

  function renderQuickActions() {
    elements.quickActions.innerHTML = '';
    QUICK_ACTIONS.forEach((action) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'chip-btn';
      button.dataset.prompt = action.prompt;
      button.disabled = !canUseAssistant() || state.isAsking;
      button.textContent = action.label;
      elements.quickActions.appendChild(button);
    });
  }

  function renderChatFeed() {
    elements.chatFeed.innerHTML = '';

    if (state.chatMessages.length === 0) {
      const placeholder = document.createElement('p');
      placeholder.className = 'chat-placeholder';
      placeholder.textContent = 'A primeira resposta aparecerá aqui após a análise.';
      elements.chatFeed.appendChild(placeholder);
    } else {
      state.chatMessages.forEach((message) => {
        const article = document.createElement('article');
        article.className = `chat-message chat-${message.role}`;

        const role = document.createElement('p');
        role.className = 'chat-role';
        role.textContent = message.role === 'assistant' ? 'Assistente' : 'Você';

        const content = document.createElement('div');
        content.className = 'chat-content';
        appendRichText(content, message.content || '');

        article.appendChild(role);
        article.appendChild(content);
        elements.chatFeed.appendChild(article);
      });
    }

    if (state.isAsking) {
      const loading = document.createElement('p');
      loading.className = 'chat-loading';
      loading.textContent = 'Gerando resposta...';
      elements.chatFeed.appendChild(loading);
    }
  }

  function renderInsights() {
    elements.insightsContent.innerHTML = '';
    appendRichText(elements.insightsContent, state.insights);
  }

  function renderTranscription() {
    elements.transcriptionContent.innerHTML = '';
    if (!Array.isArray(state.transcription) || state.transcription.length === 0) {
      const paragraph = document.createElement('p');
      paragraph.textContent = 'Sem conteúdo.';
      elements.transcriptionContent.appendChild(paragraph);
      return;
    }

    state.transcription.forEach((segment) => {
      const paragraph = document.createElement('p');
      paragraph.appendChild(
        makeTimestampButton(`[${formatTime(segment.start)}-${formatTime(segment.end)}]`, Number(segment.start || 0))
      );
      paragraph.appendChild(document.createTextNode(` ${segment.text || ''}`));
      elements.transcriptionContent.appendChild(paragraph);
    });
  }

  function renderActiveTab() {
    const buttons = root.querySelectorAll('.tab');
    buttons.forEach((button) => {
      const isActive = button.dataset.tab === state.activeTab;
      button.classList.toggle('tab-active', isActive);
    });

    ['assistant', 'insights', 'transcription'].forEach((tabName) => {
      const panel = document.getElementById(`tab-panel-${tabName}`);
      if (panel) {
        panel.hidden = tabName !== state.activeTab;
      }
    });
  }

  function renderEmptyState(title, lines, numberedLines) {
    elements.emptyState.innerHTML = '';

    const heading = document.createElement('h3');
    heading.textContent = title;
    elements.emptyState.appendChild(heading);

    (lines || []).forEach((line) => {
      const paragraph = document.createElement('p');
      paragraph.textContent = line;
      elements.emptyState.appendChild(paragraph);
    });

    if (Array.isArray(numberedLines) && numberedLines.length > 0) {
      const list = document.createElement('ol');
      numberedLines.forEach((line) => {
        const item = document.createElement('li');
        item.textContent = line;
        list.appendChild(item);
      });
      elements.emptyState.appendChild(list);
    }
  }

  function renderResults() {
    const resultAvailable = hasResult();

    elements.tabAssistantCount.textContent = String(state.chatMessages.length);
    elements.tabInsightsCount.textContent = String(
      state.insights
        .split(/\n+/)
        .filter((line) => line.trim().length > 0).length
    );
    elements.tabTranscriptionCount.textContent = String(state.transcription.length);

    if (!state.token) {
      elements.resultsShell.hidden = true;
      elements.emptyState.hidden = false;
      renderEmptyState('Faça login para começar', [
        '1. Entre ou crie sua conta.',
        '2. Cadastre sua chave OpenAI.',
        '3. Envie um vídeo para análise.'
      ]);
      return;
    }

    if (!hasOpenAiKey()) {
      elements.resultsShell.hidden = true;
      elements.emptyState.hidden = false;
      renderEmptyState('Chave OpenAI pendente', [
        'Cadastre uma chave em “Conta e integração”.',
        'Sem chave ativa, a análise e o assistente ficam bloqueados.'
      ]);
      return;
    }

    if (state.isUploading && !resultAvailable) {
      elements.resultsShell.hidden = true;
      elements.emptyState.hidden = false;
      renderEmptyState('Análise em andamento', [
        'O vídeo está sendo enviado e processado no mesmo fluxo.',
        'Quando a resposta chegar, ela aparecerá aqui sem retenção do arquivo no servidor.'
      ]);
      return;
    }

    if (!resultAvailable) {
      elements.resultsShell.hidden = true;
      elements.emptyState.hidden = false;
      renderEmptyState('Fluxo recomendado', [
        'A experiência foi desenhada para ser direta:'
      ], [
        'Envie o vídeo (a análise começa automaticamente).',
        'Acompanhe o resultado na aba Assistente.',
        'Use a aba Assistente para pedir checklist, tarefas e novas respostas.'
      ]);
      return;
    }

    elements.emptyState.hidden = true;
    elements.resultsShell.hidden = false;
    renderQuickActions();
    renderChatFeed();
    renderInsights();
    renderTranscription();
    renderActiveTab();
  }

  function renderFormStates() {
    const busy = isBusy();
    const uploadDisabled = state.isUploading;
    const followupDisabled = !canUseAssistant() || state.isAsking;

    elements.uploadLimitHelp.textContent = `Limite atual de upload: ${state.maxUploadMb}MB por arquivo. O vídeo é processado temporariamente e não fica salvo no servidor.`;
    elements.uploadSubmitBtn.textContent = state.isUploading ? 'Enviando e analisando...' : 'Enviar e analisar';
    elements.uploadSubmitBtn.disabled = uploadDisabled;
    elements.fileInput.disabled = uploadDisabled;
    elements.questionInput.disabled = uploadDisabled;

    elements.followupSubmitBtn.textContent = state.isAsking ? 'Enviando...' : 'Perguntar ao assistente';
    elements.followupSubmitBtn.disabled = followupDisabled;
    elements.followupQuestion.disabled = followupDisabled;
    elements.usageDaysSelect.disabled = state.isUsageLoading || !state.token;
    elements.logoutBtn.disabled = busy;
  }

  function render() {
    renderHero();
    renderSteps();
    renderAuthSections();
    renderApiKeyCard();
    renderUsageDashboard();
    renderStatus();
    renderVideoCard();
    renderResults();
    renderFormStates();
    setError(state.error);
  }

  elements.authForm.addEventListener('submit', handleAuthSubmit);
  elements.authToggleBtn.addEventListener('click', function () {
    state.authMode = state.authMode === 'login' ? 'register' : 'login';
    elements.authPassword.value = '';
    elements.authPasswordConfirm.value = '';
    setError('');
    render();
  });
  elements.apiKeyForm.addEventListener('submit', handleSaveApiKey);
  elements.revokeApiKeyBtn.addEventListener('click', handleRevokeApiKey);
  elements.logoutBtn.addEventListener('click', function () {
    clearAuthSession('Sessão encerrada.');
  });
  elements.fileInput.addEventListener('change', function (event) {
    const nextFile = event.target.files && event.target.files[0] ? event.target.files[0] : null;
    setSelectedFile(nextFile);
  });
  elements.uploadForm.addEventListener('submit', handleUpload);
  elements.usageDaysSelect.addEventListener('change', function (event) {
    state.usageDays = Number(event.target.value);
    if (state.token) {
      loadUsageDashboard(state.usageDays, state.token, true);
    } else {
      render();
    }
  });
  elements.usageRefreshBtn.addEventListener('click', function () {
    if (state.token) {
      loadUsageDashboard(state.usageDays, state.token, false);
    }
  });
  elements.followupForm.addEventListener('submit', function (event) {
    event.preventDefault();
    askFollowup();
  });
  elements.followupQuestion.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      askFollowup();
    }
  });
  root.addEventListener('click', function (event) {
    const quickActionButton = event.target.closest('.chip-btn');
    if (quickActionButton && quickActionButton.dataset.prompt) {
      askFollowup(quickActionButton.dataset.prompt);
      return;
    }

    const tabButton = event.target.closest('.tab');
    if (tabButton && tabButton.dataset.tab) {
      state.activeTab = tabButton.dataset.tab;
      render();
      return;
    }

    const timestampButton = event.target.closest('.timestamp');
    if (timestampButton && timestampButton.dataset.start) {
      const seconds = Number.parseFloat(timestampButton.dataset.start);
      if (!Number.isNaN(seconds)) {
        jumpToTime(seconds);
      }
    }
  });
  window.addEventListener('beforeunload', revokeLocalVideoUrl);

  render();
  refreshBackendHealth();
  loadPasswordPolicy();
  if (state.token) {
    bootstrapSession(state.token);
  }
})();
