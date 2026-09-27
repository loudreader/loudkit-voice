'use strict';

const $ = (id) => document.getElementById(id);
const paths = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  mic: '<rect x="8" y="2" width="8" height="13" rx="4"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
  volume: '<path d="m11 4-5 4H2v8h4l5 4V4ZM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 17v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  keyboard: '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 15h10"/>',
  'arrow-up': '<path d="M12 19V5m-6 6 6-6 6 6"/>',
  'arrow-right': '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  sliders: '<path d="M4 7h9m4 0h3M4 17h3m4 0h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  sparkles: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/>',
  link: '<path d="m10 13 4-4m-6 6-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 2 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 1)"/>',
  inbox: '<path d="m5 4-3 9v7h20v-7l-3-9H5ZM2 13h6l2 3h4l2-3h6"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3m6 0h4"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  chat: '<path d="M21 11a8 8 0 0 1-8 8H7l-5 3 1-7a8 8 0 1 1 18-4Z"/>'
};
function icon(name) { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.chat}</svg>`; }
document.querySelectorAll('[data-icon]').forEach((node) => { node.innerHTML = icon(node.dataset.icon); });
const state = {
  agents: [], conversations: [], voices: [], presets: [], engine: {}, mcpCommand: '', mcpConfig: null, directory: '',
  agentId: null, conversationId: null, messages: [], polling: false, initialized: false,
  recording: false, micWaiting: false, mediaRecorder: null, stream: null, audioContext: null, analyser: null,
  chunks: [], recordingStart: 0, frame: null, draft: null, transcribing: false, sending: false,
  editingAgent: null, bootstrapKey: '', messageKey: '', engineDismissed: false, toastTimer: null
};
try { state.agentId = localStorage.getItem('loudtalk.agent'); state.conversationId = localStorage.getItem('loudtalk.conversation'); } catch (_) { /* Storage is optional. */ }
const kindNames = { inbox: 'Skrzynka MCP', openai: 'API czatu', webhook: 'Webhook', command: 'Aplikacja lokalna' };
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function currentAgent() { return state.agents.find((agent) => agent.id === state.agentId); }
function saveSelection() { try { localStorage.setItem('loudtalk.agent', state.agentId || ''); localStorage.setItem('loudtalk.conversation', state.conversationId || ''); } catch (_) { /* Storage is optional. */ } }
function escapeHTML(value) { return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])); }
function safeColor(value) { return /^#[0-9a-f]{3,8}$/i.test(value || '') ? value : '#e4e9d7'; }
function initials(name) { return (name || 'A').trim().slice(0, 1).toUpperCase(); }
function audioURL(value) {
  if (!value) return '';
  try { const url = new URL(value, location.origin); return url.origin === location.origin && ['http:', 'https:'].includes(url.protocol) ? url.href : ''; } catch (_) { return ''; }
}
async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.headers || {}) } };
  if (init.body && !(init.body instanceof FormData)) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(init.body); }
  let response;
  try { response = await fetch(path, init); } catch (_) { throw new Error('Brak połączenia z LoudTalk. Sprawdź, czy serwer nadal działa.'); }
  let data;
  try { data = await response.json(); } catch (_) { if (response.ok && response.status === 204) return null; throw new Error(`Serwer zwrócił nieczytelną odpowiedź (${response.status}).`); }
  if (!response.ok) { const detail = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join(' · ') : null; throw new Error(detail || `Nie udało się wykonać operacji (${response.status}).`); }
  return data;
}
function toast(message, isError = false) {
  clearTimeout(state.toastTimer);
  const node = document.createElement('div'); node.className = `toast${isError ? ' error' : ''}`;
  const text = document.createElement('span'); text.textContent = message;
  const close = document.createElement('button'); close.innerHTML = icon('close'); close.setAttribute('aria-label', 'Zamknij powiadomienie'); close.onclick = () => node.remove();
  node.append(text, close); $('toast-region').replaceChildren(node);
  state.toastTimer = setTimeout(() => node.remove(), isError ? 14000 : 6500);
}
function closeSidebar() { $('sidebar').classList.remove('open'); $('sidebar-shade').hidden = true; $('menu-button').setAttribute('aria-expanded', 'false'); }
function renderSidebar() {
  const key = JSON.stringify([state.agents, state.conversations, state.agentId, state.conversationId]);
  if (key === state.bootstrapKey) return; state.bootstrapKey = key;
  const agentNodes = state.agents.map((agent) => {
    const button = document.createElement('button'); button.className = `agent-item${agent.id === state.agentId ? ' active' : ''}`;
    button.setAttribute('aria-current', agent.id === state.agentId ? 'true' : 'false');
    button.innerHTML = `<span class="agent-avatar" style="background:${safeColor(agent.color)}">${escapeHTML(initials(agent.name))}</span><span class="agent-info"><span class="agent-name">${escapeHTML(agent.name)}</span><span class="agent-detail">${escapeHTML(kindNames[agent.kind] || agent.kind)}</span></span>${agent.id === state.agentId ? '<span class="agent-active-dot" aria-hidden="true"></span>' : ''}`;
    button.onclick = () => selectAgent(agent.id); return button;
  });
  if (!agentNodes.length) { const button = document.createElement('button'); button.className = 'agent-add-empty'; button.textContent = '+ Dodaj swojego pierwszego agenta'; button.onclick = () => openAgentDialog(); agentNodes.push(button); }
  $('agent-list').replaceChildren(...agentNodes);
  const conversations = state.conversations.filter((conversation) => conversation.agent_id === state.agentId);
  $('conversation-count').textContent = conversations.length;
  const conversationNodes = conversations.map((conversation) => {
    const button = document.createElement('button'); button.className = `conversation-item${conversation.id === state.conversationId ? ' active' : ''}`;
    button.setAttribute('aria-current', conversation.id === state.conversationId ? 'true' : 'false');
    button.innerHTML = `${icon('chat')}<span>${escapeHTML(conversation.title || 'Nowa rozmowa')}</span>`; button.title = conversation.title || 'Nowa rozmowa';
    button.onclick = () => selectConversation(conversation.id); return button;
  });
  if (!conversationNodes.length) { const empty = document.createElement('p'); empty.className = 'sidebar-empty'; empty.textContent = 'Pierwsza głosówka zaczyna rozmowę.'; conversationNodes.push(empty); }
  $('conversation-list').replaceChildren(...conversationNodes);
  const agent = currentAgent(); $('current-agent-name').textContent = agent?.name || 'Wybierz agenta'; $('current-agent-kind').textContent = kindNames[agent?.kind] || '';
  $('current-avatar').textContent = initials(agent?.name); $('current-avatar').style.background = safeColor(agent?.color);
  $('conversation-menu').hidden = !state.conversationId;
  const transportCopy = agent?.kind === 'inbox' ? 'Głos przetwarzany lokalnie. Agent MCP może odczytać treść i nagranie.' : 'Głos przetwarzany lokalnie. Do agenta trafia treść wiadomości.';
  $('privacy-caption').innerHTML = `${icon('shield')}${escapeHTML(transportCopy)}`;
}
function clearMessages() { state.messages = []; state.messageKey = ''; $('messages').replaceChildren(); renderConversation(); }
function renderConversation() {
  const hasMessages = state.messages.length > 0;
  $('main').classList.toggle('has-messages', hasMessages); $('welcome').hidden = hasMessages; $('messages').hidden = !hasMessages;
  const agent = currentAgent(); const last = state.messages[state.messages.length - 1];
  $('inbox-connection-note').hidden = !(agent?.kind === 'inbox' && !hasMessages);
  $('inbox-note').hidden = !(agent?.kind === 'inbox' && last?.role === 'user');
  $('welcome-description').innerHTML = agent?.kind === 'inbox' ? 'Złap myśl, zanim ucieknie.<br>Nagraj wiadomość do skrzynki swojego agenta.' : 'Złap myśl, zanim ucieknie.<br>Nagraj wiadomość. Twój agent odpowie głosem.';
}
function preserveDraftForSelection() {
  if (!state.draft && !$('message-text').value.trim() && !state.recording) return true;
  toast('Twoja niewysłana wiadomość pozostaje w edytorze. Wyślesz ją do wybranego agenta.'); return true;
}
function selectAgent(id) {
  if (state.sending) { toast('Poczekaj na wysłanie bieżącej wiadomości.'); return; }
  if (state.agentId === id) { closeSidebar(); return; }
  preserveDraftForSelection(); state.agentId = id;
  state.conversationId = state.conversations.find((conversation) => conversation.agent_id === id)?.id || null;
  saveSelection(); clearMessages(); renderSidebar(); closeSidebar(); loadMessages().catch((error) => toast(error.message, true));
}
function selectConversation(id) {
  if (state.sending) { toast('Poczekaj na wysłanie bieżącej wiadomości.'); return; }
  if (state.conversationId === id) { closeSidebar(); return; }
  preserveDraftForSelection(); const conversation = state.conversations.find((item) => item.id === id); if (!conversation) return;
  state.agentId = conversation.agent_id; state.conversationId = id;
  saveSelection(); clearMessages(); renderSidebar(); closeSidebar(); loadMessages().catch((error) => toast(error.message, true));
}
function renderEngine() {
  const engine = state.engine || {}; const tts = engine.tts || {}; const stt = engine.stt || {};
  const ready = tts.state === 'ready' && stt.state === 'ready';
  const loading = engine.preparing || tts.state === 'loading' || stt.state === 'loading';
  const error = tts.state === 'error' || stt.state === 'error';
  $('engine-status').className = `engine-status ${ready ? 'ready' : loading ? 'loading' : error ? 'error' : ''}`;
  $('engine-status-label').textContent = ready ? 'Głos gotowy' : loading ? 'Przygotowuję głos…' : error ? 'Modele: ponów próbę' : 'Przygotuj głos';
  $('engine-title').textContent = ready ? 'Możemy rozmawiać' : loading ? 'Twój głos nabiera kształtu' : error ? 'Przygotowanie wymaga ponownej próby' : 'Jeszcze chwila i możemy rozmawiać';
  $('engine-description').textContent = ready ? 'Parakeet i LoudKit są gotowe. Nagrywaj wiadomości i odbieraj odpowiedzi głosowe.' : loading ? 'Pobieram i wczytuję modele na tym komputerze. Pierwsze uruchomienie może potrwać kilka minut. Możesz już skonfigurować agenta.' : error ? [stt.error, tts.error].filter(Boolean).join(' · ') || 'Model nie uruchomił się poprawnie. Sprawdź połączenie i spróbuj ponownie.' : 'Pobierz lokalne modele głosu: Parakeet rozpozna Twoje słowa, a LoudKit przeczyta odpowiedź. To jednorazowy krok.';
  const labels = { idle: 'oczekuje', loading: 'wczytywanie…', ready: 'gotowy', error: 'błąd' };
  $('stt-chip').textContent = `Parakeet · ${labels[stt.state] || 'oczekuje'}`;
  $('tts-chip').textContent = `LoudKit · ${labels[tts.state] || 'oczekuje'}`;
  $('prepare-engines').disabled = loading || ready;
  $('prepare-engines').innerHTML = `${loading ? 'Przygotowuję…' : ready ? 'Gotowe' : error ? 'Spróbuj ponownie' : 'Przygotuj głos'}${icon(ready ? 'check' : 'download')}`;
  if (!state.engineDismissed) $('engine-panel').hidden = ready;
}
async function bootstrap(initial = false) {
  const data = await api('/api/bootstrap');
  const previousAgent = state.agentId; const previousConversation = state.conversationId;
  state.agents = data.agents || []; state.conversations = data.conversations || []; state.voices = data.voices || [];
  state.presets = data.command_presets || []; state.directory = data.working_directory || ''; state.mcpCommand = data.mcp_command || ''; state.mcpConfig = data.mcp_config || null; state.engine = data.engine || {};
  if (!state.agents.some((agent) => agent.id === state.agentId)) state.agentId = state.agents[0]?.id || null;
  if (!state.conversations.some((conversation) => conversation.id === state.conversationId && conversation.agent_id === state.agentId)) state.conversationId = null;
  if (initial && !state.conversationId) state.conversationId = state.conversations.find((conversation) => conversation.agent_id === state.agentId)?.id || null;
  if (previousAgent !== state.agentId || previousConversation !== state.conversationId) clearMessages();
  state.initialized = true; saveSelection(); renderSidebar(); renderEngine(); renderConversation();
  document.querySelector('.boot-error')?.remove();
}
function createMessageNode(message) {
  const agent = currentAgent(); const node = document.createElement('article'); node.className = `message ${message.role} ${message.status || 'ready'}`; node.dataset.id = message.id;
  const date = new Date(message.created_at); const validDate = !Number.isNaN(date.getTime());
  const time = validDate ? date.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' }) : '';
  const name = message.role === 'user' ? 'Ty' : agent?.name || 'Agent';
  const avatar = document.createElement('div'); avatar.className = 'message-avatar'; avatar.textContent = initials(name); avatar.setAttribute('aria-hidden', 'true');
  const body = document.createElement('div'); body.className = 'message-body';
  const meta = document.createElement('div'); meta.className = 'message-meta'; meta.innerHTML = `<strong>${escapeHTML(name)}</strong><time${validDate ? ` datetime="${escapeHTML(date.toISOString())}"` : ''}>${escapeHTML(time)}</time>`;
  const bubble = document.createElement('div'); bubble.className = 'message-bubble';
  if (message.text) { const paragraph = document.createElement('p'); paragraph.className = 'message-text'; paragraph.textContent = message.text; bubble.append(paragraph); }
  const audio = audioURL(message.audio_url);
  if (audio) { const player = document.createElement('audio'); player.src = audio; player.controls = true; player.preload = 'metadata'; player.setAttribute('aria-label', `Głosówka: ${name}`); bubble.append(player); }
  if (message.status === 'pending') { const pending = document.createElement('div'); pending.className = 'message-pending'; pending.innerHTML = '<span class="pending-dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Agent przygotowuje odpowiedź…</span>'; bubble.append(pending); }
  if (message.status === 'error') {
    const error = document.createElement('p'); error.className = 'message-error'; error.textContent = message.error || 'Nie udało się przygotować odpowiedzi.';
    const retry = document.createElement('button'); retry.className = 'message-retry'; retry.innerHTML = `${icon('refresh')}Spróbuj ponownie`;
    retry.onclick = async () => { retry.disabled = true; try { await api(`/api/messages/${encodeURIComponent(message.id)}/retry`, { method: 'POST' }); await loadMessages(); } catch (issue) { toast(issue.message, true); retry.disabled = false; } };
    bubble.append(error, retry);
  }
  body.append(meta, bubble); node.append(avatar, body); return node;
}
async function loadMessages() {
  const id = state.conversationId; if (!id) return;
  const messages = await api(`/api/conversations/${encodeURIComponent(id)}/messages`); if (id !== state.conversationId) return;
  const key = JSON.stringify(messages); if (key === state.messageKey) return;
  const previousCount = state.messages.length; const wasNearBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 240;
  state.messageKey = key; state.messages = messages;
  const container = $('messages'); const existing = new Map([...container.children].map((node) => [String(node.dataset.id), node]));
  const activeIds = new Set(messages.map((message) => String(message.id)));
  for (const [messageId, node] of existing) { if (!activeIds.has(messageId)) node.remove(); }
  for (const message of messages) {
    const signature = JSON.stringify(message); let node = existing.get(String(message.id));
    if (!node || node.dataset.signature !== signature) {
      const replacement = createMessageNode(message); replacement.dataset.signature = signature;
      if (node) {
        const previousAudio = node.querySelector('audio'); const nextAudio = replacement.querySelector('audio');
        if (previousAudio && nextAudio && previousAudio.src === nextAudio.src) nextAudio.replaceWith(previousAudio);
        node.replaceWith(replacement);
      } else container.append(replacement);
      node = replacement;
    }
  }
  renderConversation();
  if (messages.length > previousCount && wasNearBottom && previousCount > 0) container.lastElementChild?.scrollIntoView({ behavior: reducedMotion ? 'instant' : 'smooth', block: 'nearest' });
}
async function poll() {
  if (state.polling || document.hidden) return; state.polling = true;
  try { await bootstrap(); await loadMessages(); } catch (_) { $('engine-status-label').textContent = 'Brak połączenia'; $('engine-status').className = 'engine-status error'; } finally { state.polling = false; }
}
async function newConversation() {
  if (state.sending) { toast('Poczekaj na wysłanie bieżącej wiadomości.'); return; }
  if (!currentAgent()) { openAgentDialog(); return; }
  preserveDraftForSelection(); state.conversationId = null; saveSelection(); clearMessages(); renderSidebar(); closeSidebar();
  window.scrollTo({ top: 0, behavior: reducedMotion ? 'instant' : 'smooth' });
}
async function ensureConversation(agentId) {
  if (state.conversationId) return state.conversationId;
  const conversation = await api('/api/conversations', { method: 'POST', body: { agent_id: agentId } });
  state.conversations.unshift(conversation); state.conversationId = conversation.id; saveSelection(); renderSidebar(); return conversation.id;
}
async function sendMessage(text, audioId, source) {
  if (state.sending || !text.trim()) return false;
  const agent = currentAgent(); if (!agent) { openAgentDialog(); return false; }
  state.sending = true; $('send-draft').disabled = true; $('send-text').disabled = true;
  if (source === 'draft') $('send-draft').innerHTML = `Wysyłam…${icon('arrow-up')}`;
  try {
    const id = await ensureConversation(agent.id);
    await api(`/api/conversations/${encodeURIComponent(id)}/messages`, { method: 'POST', body: { text: text.trim(), ...(audioId ? { audio_id: audioId } : {}) } });
    if (source === 'draft') discardDraft(); else { $('message-text').value = ''; $('text-composer').hidden = true; $('text-toggle').setAttribute('aria-expanded', 'false'); }
    await bootstrap(); await loadMessages(); toast(audioId ? 'Głosówka wysłana.' : 'Wiadomość wysłana.'); return true;
  } catch (error) { toast(error.message, true); return false; } finally { state.sending = false; $('send-text').disabled = false; $('send-draft').innerHTML = `Wyślij głosówkę${icon('arrow-up')}`; updateDraftSend(); }
}
function updateDraftSend() {
  $('send-draft').disabled = state.sending || state.transcribing || !state.draft || !$('draft-text').value.trim();
  if (!state.sending) $('send-draft').innerHTML = `${state.draft && !state.draft.audioId && !state.transcribing ? 'Wyślij sam tekst' : 'Wyślij głosówkę'}${icon('arrow-up')}`;
}
function setDraftStatus(message, error = false) { $('draft-status').textContent = message; $('draft-status').className = `inline-status${error ? ' error' : ''}`; }
function discardDraft() {
  if (state.draft?.url) URL.revokeObjectURL(state.draft.url);
  state.draft = null; state.transcribing = false; $('draft-audio').pause(); $('draft-audio').removeAttribute('src'); $('draft-audio').load();
  $('preview-panel').hidden = true; $('record-stage').hidden = false; $('draft-text').value = ''; $('retry-transcribe').hidden = true;
  setDraftStatus(''); updateDraftSend();
}
async function receiveAudio(file) {
  if (state.recording || state.micWaiting || state.sending) { toast('Najpierw zakończ bieżące nagrywanie lub wysyłanie.'); return; }
  if (!file || !file.size) { toast('Nagranie jest puste. Spróbuj nagrać je jeszcze raz.', true); return; }
  if (file.size > 25 * 1024 * 1024) { toast('To nagranie ma ponad 25 MB. Wybierz krótszy plik — do 3 minut.', true); return; }
  if (state.draft?.url) URL.revokeObjectURL(state.draft.url);
  state.draft = { file, url: URL.createObjectURL(file), audioId: null, editVersion: 0 };
  $('draft-audio').src = state.draft.url; $('preview-panel').hidden = false; $('record-stage').hidden = true; $('text-composer').hidden = true; $('text-toggle').setAttribute('aria-expanded', 'false'); $('draft-text').value = '';
  $('preview-panel').scrollIntoView({ behavior: reducedMotion ? 'instant' : 'smooth', block: 'nearest' });
  await transcribeDraft();
}
async function transcribeDraft() {
  if (!state.draft || state.transcribing) return;
  const draft = state.draft; const editVersion = draft.editVersion; state.transcribing = true; $('retry-transcribe').hidden = true;
  $('draft-text').disabled = true; updateDraftSend(); setDraftStatus('Parakeet rozpoznaje Twoje słowa. Przy pierwszym nagraniu model może potrzebować chwili.');
  const form = new FormData(); form.append('file', draft.file, draft.file.name || 'nagranie.webm');
  try {
    const result = await api('/api/transcribe', { method: 'POST', body: form }); if (state.draft !== draft) return;
    draft.audioId = result.audio_id; draft.duration = result.duration;
    if (draft.editVersion === editVersion) $('draft-text').value = result.text || '';
    setDraftStatus(result.text?.trim() ? 'Gotowe. Odsłuchaj, popraw tekst i wyślij, gdy chcesz.' : 'Nie udało się rozpoznać słów. Wpisz treść poniżej lub nagraj wiadomość ponownie.');
  } catch (error) { if (state.draft !== draft) return; setDraftStatus(`${error.message} Nagranie czeka tutaj. Ponów próbę albo wpisz i wyślij sam tekst.`, true); $('retry-transcribe').hidden = false; }
  finally { if (state.draft === draft) { state.transcribing = false; $('draft-text').disabled = false; updateDraftSend(); } }
}
function resetRecordUI() {
  $('main').classList.remove('recording'); $('record-button').setAttribute('aria-label', 'Nagraj wiadomość'); $('record-icon').innerHTML = icon('mic'); $('record-label').textContent = 'Nagraj wiadomość'; $('record-hint').innerHTML = 'Kliknij lub naciśnij <kbd>spację</kbd>';
  $('record-button').disabled = false; $('upload-button').disabled = false; $('text-toggle').disabled = false;
}
function cleanupMic() {
  cancelAnimationFrame(state.frame); state.stream?.getTracks().forEach((track) => track.stop()); state.stream = null;
  if (state.audioContext && state.audioContext.state !== 'closed') state.audioContext.close().catch(() => {});
  state.audioContext = null; state.analyser = null; state.mediaRecorder = null;
}
function drawWaveform() {
  if (!state.recording) return;
  const elapsed = Math.floor((performance.now() - state.recordingStart) / 1000); $('record-label').textContent = `Nagrywanie · ${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')}`;
  if (elapsed >= 179) { stopRecording(); toast('Nagranie zbliża się do limitu 3 minut — czas je sprawdzić.'); return; }
  const canvas = $('waveform'); const context = canvas.getContext('2d'); const analyser = state.analyser;
  if (context && analyser) {
    const values = new Uint8Array(analyser.frequencyBinCount); analyser.getByteFrequencyData(values);
    context.clearRect(0, 0, canvas.width, canvas.height); const barCount = 60; const step = canvas.width / barCount;
    context.fillStyle = '#bdcc9b';
    for (let index = 0; index < barCount; index++) {
      const distance = Math.abs(index - barCount / 2); if (distance < 8) continue;
      const sample = values[Math.floor((index / barCount) * values.length * .65)];
      const height = 3 + (sample / 255) * 90; context.beginPath(); context.roundRect(index * step, (canvas.height - height) / 2, 3.5, height, 2); context.fill();
    }
  }
  state.frame = requestAnimationFrame(drawWaveform);
}
async function startRecording() {
  if (state.recording || state.micWaiting || state.sending) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { toast('Ta przeglądarka nie udostępnia mikrofonu. Otwórz LoudTalk pod localhost lub dodaj gotowe nagranie.', true); return; }
  state.micWaiting = true; $('record-button').disabled = true; $('record-label').textContent = 'Udostępnij mikrofon';
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
    state.stream = stream; const options = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm', 'audio/ogg;codecs=opus'].find((type) => MediaRecorder.isTypeSupported(type));
    const recorder = new MediaRecorder(stream, options ? { mimeType: options } : {}); state.mediaRecorder = recorder; state.chunks = [];
    recorder.addEventListener('dataavailable', (event) => { if (event.data.size) state.chunks.push(event.data); });
    recorder.addEventListener('stop', () => {
      const type = recorder.mimeType || state.chunks[0]?.type || 'audio/webm'; const blob = new Blob(state.chunks, { type }); const extension = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
      state.recording = false; state.micWaiting = false; cleanupMic(); resetRecordUI(); receiveAudio(new File([blob], `glosowka-${Date.now()}.${extension}`, { type }));
    }, { once: true });
    recorder.addEventListener('error', () => { state.recording = false; cleanupMic(); resetRecordUI(); toast('Nagrywanie zostało przerwane. Sprawdź mikrofon i spróbuj ponownie.', true); });
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (AudioContext) { try { state.audioContext = new AudioContext(); await state.audioContext.resume(); state.analyser = state.audioContext.createAnalyser(); state.analyser.fftSize = 256; state.audioContext.createMediaStreamSource(stream).connect(state.analyser); } catch (_) { state.analyser = null; } }
    recorder.start(500); state.recording = true; state.recordingStart = performance.now();
    $('main').classList.add('recording'); $('record-button').setAttribute('aria-label', 'Zatrzymaj nagranie'); $('record-icon').innerHTML = icon('stop'); $('record-hint').innerHTML = 'Kliknij lub naciśnij <kbd>spację</kbd>, żeby zakończyć';
    $('upload-button').disabled = true; $('text-toggle').disabled = true; drawWaveform();
  } catch (error) {
    cleanupMic(); resetRecordUI(); const message = error.name === 'NotAllowedError' ? 'Mikrofon jest zablokowany. Zezwól na niego w ustawieniach witryny albo dodaj gotowe nagranie.' : error.name === 'NotFoundError' ? 'Nie widzę mikrofonu. Podłącz go albo dodaj gotowe nagranie.' : 'Nie udało się uruchomić mikrofonu. Sprawdź, czy jest dostępny.'; toast(message, true);
  } finally { state.micWaiting = false; $('record-button').disabled = false; }
}
function stopRecording() { if (!state.recording || state.mediaRecorder?.state !== 'recording') return; $('record-button').disabled = true; $('record-label').textContent = 'Zapisuję nagranie…'; state.mediaRecorder.stop(); }
function toggleRecording() { if (state.recording) stopRecording(); else startRecording(); }
function chosenKind() { return $('agent-form').querySelector('input[name="kind"]:checked')?.value || 'inbox'; }
function updateKindFields() {
  const kind = chosenKind(); const endpoint = kind === 'openai' || kind === 'webhook';
  $('endpoint-fields').hidden = !endpoint; $('command-fields').hidden = kind !== 'command'; $('model-field').hidden = kind !== 'openai'; $('mcp-setup').hidden = kind !== 'inbox';
  $('agent-endpoint').required = endpoint; $('agent-model').required = kind === 'openai'; $('agent-command').required = kind === 'command'; $('agent-directory').required = kind === 'command';
  $('agent-endpoint').placeholder = kind === 'webhook' ? 'http://localhost:8080/voice-message' : 'http://localhost:8000/v1/chat/completions';
  $('endpoint-label').textContent = kind === 'webhook' ? 'Adres webhooka' : 'Adres API';
  $('endpoint-help').textContent = kind === 'webhook' ? 'LoudTalk wysyła JSON z wiadomością. Webhook powinien zwrócić odpowiedź agenta — zgodnie z dokumentacją integracji.' : 'Pełny adres endpointu Chat Completions, np. kończący się na /v1/chat/completions.';
}
function openAgentDialog(agent = null) {
  state.editingAgent = agent?.id || null; $('agent-form').reset(); $('agent-form-error').hidden = true; $('voice-preview-audio').pause(); $('voice-preview-audio').hidden = true;
  $('agent-dialog-title').textContent = agent ? `Głos dla ${agent.name}.` : 'Daj głos swojemu agentowi.';
  $('save-agent').innerHTML = `${agent ? 'Zapisz zmiany' : 'Dodaj agenta'}${icon('arrow-right')}`;
  $('agent-form').querySelector(`input[name="kind"][value="${['inbox', 'openai', 'webhook', 'command'].includes(agent?.kind) ? agent.kind : 'inbox'}"]`).checked = true;
  $('agent-name').value = agent?.name || ''; $('agent-endpoint').value = agent?.endpoint || ''; $('agent-model').value = agent?.model || '';
  $('agent-api-key').placeholder = agent?.has_api_key ? 'Klucz zapisany — zostaw puste, aby zachować' : 'Klucz zostaje na tym komputerze';
  $('agent-voice').replaceChildren(...state.voices.map((voice) => { const option = document.createElement('option'); option.value = voice.id; option.textContent = `${voice.name}${voice.language ? ` · ${voice.language}` : ''}`; return option; }));
  if (!state.voices.length) { const option = document.createElement('option'); option.value = 'gosia'; option.textContent = 'Gosia · polski'; $('agent-voice').append(option); }
  $('agent-voice').value = agent?.voice || (state.voices.some((voice) => voice.id === 'gosia') ? 'gosia' : $('agent-voice').options[0].value);
  $('agent-command').replaceChildren(...state.presets.map((preset) => { const option = document.createElement('option'); option.value = preset.id; option.textContent = `${preset.name} · ${preset.installed ? 'zainstalowany' : 'brak programu'}`; return option; }));
  if (agent?.command_id) $('agent-command').value = agent.command_id; else if (state.presets.some((preset) => preset.installed)) $('agent-command').value = state.presets.find((preset) => preset.installed).id;
  $('agent-directory').value = agent?.working_directory || state.directory;
  $('mcp-command').textContent = state.mcpConfig ? JSON.stringify(state.mcpConfig, null, 2) : typeof state.mcpCommand === 'string' ? state.mcpCommand : JSON.stringify(state.mcpCommand, null, 2);
  $('agent-id-note').hidden = !agent; $('agent-id-value').textContent = agent?.id || '';
  updateKindFields(); closeSidebar(); $('agent-dialog').showModal();
}
async function saveAgent(event) {
  event.preventDefault(); const form = $('agent-form'); if (!form.reportValidity()) return;
  const kind = chosenKind(); const data = { name: $('agent-name').value.trim(), kind, endpoint: kind === 'openai' || kind === 'webhook' ? $('agent-endpoint').value.trim() : '', model: kind === 'openai' ? $('agent-model').value.trim() : '', voice: $('agent-voice').value, color: currentAgent()?.color || '#e4e9d7' };
  if (!data.name) { $('agent-name').focus(); return; }
  if (kind === 'command') { data.command_id = $('agent-command').value; data.working_directory = $('agent-directory').value.trim(); }
  const key = $('agent-api-key').value.trim(); if (key) data.api_key = key;
  $('save-agent').disabled = true; $('agent-form-error').hidden = true;
  try {
    const editing = state.editingAgent; const agent = await api(editing ? `/api/agents/${encodeURIComponent(editing)}` : '/api/agents', { method: editing ? 'PATCH' : 'POST', body: data });
    await bootstrap(); state.agentId = agent.id; state.conversationId = state.conversations.find((conversation) => conversation.agent_id === agent.id)?.id || null;
    saveSelection(); clearMessages(); renderSidebar(); await loadMessages();
    if (!editing && kind === 'inbox') { state.editingAgent = agent.id; $('agent-dialog-title').textContent = `${agent.name} ma już skrzynkę.`; $('save-agent').innerHTML = `Zapisz zmiany${icon('check')}`; $('agent-id-note').hidden = false; $('agent-id-value').textContent = agent.id; toast('Skrzynka gotowa. Skopiuj konfigurację MCP, aby połączyć agenta.'); }
    else { $('agent-dialog').close(); toast(editing ? 'Połączenie zapisane.' : 'Agent dodany. Możesz rozpocząć rozmowę.'); }
  } catch (error) { $('agent-form-error').textContent = error.message; $('agent-form-error').hidden = false; }
  finally { $('save-agent').disabled = false; }
}

$('add-agent').onclick = () => openAgentDialog(); $('open-settings').onclick = () => openAgentDialog(currentAgent()); $('connect-inbox').onclick = () => openAgentDialog(currentAgent());
$('connect-inbox-first').onclick = () => openAgentDialog(currentAgent());
$('new-conversation').onclick = newConversation; $('record-button').onclick = toggleRecording;
$('menu-button').onclick = () => { const open = !$('sidebar').classList.contains('open'); $('sidebar').classList.toggle('open', open); $('sidebar-shade').hidden = !open; $('menu-button').setAttribute('aria-expanded', String(open)); };
$('sidebar-shade').onclick = closeSidebar;
$('engine-status').onclick = () => { state.engineDismissed = true; $('engine-panel').hidden = !$('engine-panel').hidden; };
$('close-engine-panel').onclick = () => { state.engineDismissed = true; $('engine-panel').hidden = true; };
$('prepare-engines').onclick = async () => { $('prepare-engines').disabled = true; try { state.engine = await api('/api/engines/prepare', { method: 'POST' }); renderEngine(); } catch (error) { toast(error.message, true); $('prepare-engines').disabled = false; } };
$('upload-button').onclick = () => $('audio-file').click();
$('audio-file').onchange = () => { const file = $('audio-file').files[0]; $('audio-file').value = ''; if (file) receiveAudio(file); };
$('text-toggle').onclick = () => { $('text-composer').hidden = !$('text-composer').hidden; $('text-toggle').setAttribute('aria-expanded', String(!$('text-composer').hidden)); if (!$('text-composer').hidden) $('message-text').focus(); };
$('text-composer').onsubmit = (event) => { event.preventDefault(); sendMessage($('message-text').value, null, 'text'); };
$('message-text').onkeydown = (event) => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); sendMessage($('message-text').value, null, 'text'); } };
$('draft-text').oninput = () => { if (state.draft) state.draft.editVersion++; updateDraftSend(); };
$('send-draft').onclick = () => sendMessage($('draft-text').value, state.draft?.audioId, 'draft');
$('discard-draft').onclick = () => { discardDraft(); $('record-button').focus(); };
$('rerecord').onclick = () => { discardDraft(); startRecording(); };
$('retry-transcribe').onclick = transcribeDraft;
$('close-agent-dialog').onclick = () => $('agent-dialog').close();
$('agent-dialog').addEventListener('close', () => $('voice-preview-audio').pause());
$('preview-voice').onclick = async () => {
  const button = $('preview-voice'); button.disabled = true; button.innerHTML = `${icon('volume')}<span>Przygotowuję próbkę…</span>`;
  try {
    const voice = $('agent-voice').value; const language = state.voices.find((item) => item.id === voice)?.language;
    const result = await api('/api/voice-preview', { method: 'POST', body: { voice, text: language === 'pl' ? 'Cześć! Tak brzmi mój głos. Czekam na Twoją wiadomość.' : 'Hello! This is my voice. I am ready for your next message.' } });
    const player = $('voice-preview-audio'); player.src = audioURL(result.audio_url); player.hidden = false;
    if ($('agent-dialog').open) player.play().catch(() => {});
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.innerHTML = `${icon('volume')}<span>Posłuchaj głosu</span>`; }
};
$('agent-voice').onchange = () => { $('voice-preview-audio').pause(); $('voice-preview-audio').hidden = true; };
$('agent-form').onsubmit = saveAgent;
$('agent-form').querySelectorAll('input[name="kind"]').forEach((input) => { input.onchange = updateKindFields; });
$('agent-command').onchange = () => { if (!$('agent-name').value.trim()) $('agent-name').value = state.presets.find((preset) => preset.id === $('agent-command').value)?.name || ''; };
$('copy-mcp').onclick = async () => { try { await navigator.clipboard.writeText($('mcp-command').textContent); $('copy-mcp').innerHTML = icon('check'); toast('Konfiguracja MCP skopiowana.'); setTimeout(() => { $('copy-mcp').innerHTML = icon('copy'); }, 2200); } catch (_) { const range = document.createRange(); range.selectNodeContents($('mcp-command')); const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); toast('Zaznaczyłem konfigurację. Skopiuj ją skrótem ⌘ / Ctrl + C.'); } };
$('copy-agent-id').onclick = async () => { try { await navigator.clipboard.writeText($('agent-id-value').textContent); toast('Identyfikator skrzynki skopiowany.'); } catch (_) { toast(`Identyfikator skrzynki: ${$('agent-id-value').textContent}`); } };
$('conversation-menu').onclick = () => { if (state.conversationId) $('delete-dialog').showModal(); };
$('cancel-delete').onclick = () => $('delete-dialog').close();
$('confirm-delete').onclick = async () => { const id = state.conversationId; if (!id) return; $('confirm-delete').disabled = true; try { await api(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }); state.conversationId = null; state.conversations = state.conversations.filter((conversation) => conversation.id !== id); clearMessages(); saveSelection(); renderSidebar(); $('delete-dialog').close(); toast('Rozmowa usunięta.'); } catch (error) { toast(error.message, true); } finally { $('confirm-delete').disabled = false; } };
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { closeSidebar(); if (state.recording) stopRecording(); }
  if (event.code !== 'Space' || event.repeat || event.metaKey || event.ctrlKey || event.altKey || document.querySelector('dialog[open]')) return;
  const target = event.target; if (target instanceof Element && target.closest('input,textarea,select,button,a,[contenteditable="true"],audio')) return;
  if (state.draft) return; event.preventDefault(); toggleRecording();
});
let dragDepth = 0;
document.addEventListener('dragenter', (event) => { if (![...(event.dataTransfer?.types || [])].includes('Files')) return; event.preventDefault(); dragDepth++; $('drop-zone').hidden = false; });
document.addEventListener('dragover', (event) => { if ([...(event.dataTransfer?.types || [])].includes('Files')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } });
document.addEventListener('dragleave', (event) => { event.preventDefault(); dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('drop-zone').hidden = true; });
document.addEventListener('drop', (event) => { event.preventDefault(); dragDepth = 0; $('drop-zone').hidden = true; const file = event.dataTransfer?.files?.[0]; if (!file) return; if (file.type.startsWith('audio/') || /\.(wav|mp3|m4a|ogg|webm|flac|mp4|aac|aiff|aif)$/i.test(file.name)) receiveAudio(file); else toast('Wybierz plik z nagraniem audio, np. WAV, MP3 lub M4A.', true); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
window.addEventListener('beforeunload', (event) => { if (state.recording || state.draft || $('message-text').value.trim()) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('pagehide', () => { cleanupMic(); });
async function initialize() {
  try { await bootstrap(true); await loadMessages(); }
  catch (error) {
    const banner = document.createElement('div'); banner.className = 'boot-error'; banner.setAttribute('role', 'alert'); const message = document.createElement('p'); message.textContent = error.message;
    const retry = document.createElement('button'); retry.className = 'button button-subtle'; retry.textContent = 'Połącz ponownie'; retry.onclick = () => { banner.remove(); initialize(); }; banner.append(message, retry); $('conversation-space').prepend(banner);
    $('engine-status-label').textContent = 'Brak połączenia';
  }
}
initialize(); setInterval(poll, 3000);
