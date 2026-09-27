'use strict';

const $ = (id) => document.getElementById(id);
const state = { channels: [], catalog: [], agents: [], voices: [], engine: {}, pairings: [], events: [], nativeAgents: [], presets: [], directory: '', mcpConfig: null, createdInbox: null, channelId: null, platform: null, connectionId: null, nativeResult: null, copyValues: [], renderKeys: {}, polling: false, loaded: false, toastTimer: null };
const platformSymbols = { telegram: '↗', discord: '⌘', slack: '#', whatsapp: '◔', imessage: '•••', bluebubbles: '•••' };
const eventNames = { queued: 'Queued', processing: 'Agent is replying', sent: 'Reply sent', error: 'Needs attention', uncertain: 'Check delivery', ignored: 'Skipped', pairing: 'Awaiting confirmation', awaiting_agent: 'Waiting for your agent' };
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const pathId = (id) => encodeURIComponent(id);
const platformFor = (id) => state.catalog.find((item) => item.id === id);
const agentFor = (id) => state.agents.find((item) => item.id === id);
const channelFor = (id) => state.channels.find((item) => item.id === id);
function safeURL(value) { try { const url = new URL(value, location.origin); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; } catch (_) { return ''; } }
function platformIcon(platform) { return `<span class="platform-icon ${esc(platform)}" aria-hidden="true">${esc(platformSymbols[platform] || '↗')}</span>`; }
function asList(value) { return Array.isArray(value) ? value : []; }
function errorText(error) { return error?.message || 'This action failed. Try again.'; }
function showError(id, error) { $(id).textContent = errorText(error); $(id).hidden = false; }
function hideError(id) { $(id).textContent = ''; $(id).hidden = true; }

async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.headers || {}) } };
  if (init.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(init.body); }
  let response;
  try { response = await fetch(path, init); } catch (_) { throw new Error('Cannot connect to Loudkit Voice. Check that the app is running.'); }
  if (response.status === 204 && response.ok) return null;
  let data;
  try { data = await response.json(); } catch (_) { throw new Error(`Could not read the Loudkit Voice response (${response.status}).`); }
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join(' · ') : '';
    throw new Error(detail || `The action failed (${response.status}).`);
  }
  return data;
}
function toast(message, isError = false) {
  clearTimeout(state.toastTimer);
  const node = document.createElement('div'); node.className = `toast${isError ? ' error' : ''}`;
  const label = document.createElement('span'); label.textContent = message;
  const close = document.createElement('button'); close.textContent = '×'; close.setAttribute('aria-label', 'Dismiss notification'); close.onclick = () => node.remove();
  node.append(label, close); $('toast-region').replaceChildren(node);
  state.toastTimer = setTimeout(() => node.remove(), isError ? 14000 : 6500);
}
async function copyText(value, button) {
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
    else { const field = document.createElement('textarea'); field.value = value; field.style.position = 'fixed'; field.style.opacity = '0'; (document.querySelector('dialog[open]') || document.body).append(field); field.select(); const copied = document.execCommand('copy'); field.remove(); if (!copied) throw new Error(); }
    const label = button.textContent; button.textContent = 'Copied ✓'; setTimeout(() => { if (button.isConnected) button.textContent = label; }, 2200);
  } catch (_) { toast('Automatic copying failed. Select the text and copy it manually.', true); }
}
function openDialog(id) { const dialog = $(id); if (!dialog.open) dialog.showModal(); }
function setEnginePanel(open) { $('engine-panel').hidden = !open; $('engine-status').setAttribute('aria-expanded', String(open)); }
function renderEngine() {
  const engine = state.engine || {}, stt = engine.stt || {}, tts = engine.tts || {};
  const ready = stt.state === 'ready' && tts.state === 'ready';
  const loading = engine.preparing || stt.state === 'loading' || tts.state === 'loading';
  const failed = stt.state === 'error' || tts.state === 'error';
  $('engine-status').className = `engine-button ${ready ? 'ready' : loading ? 'loading' : failed ? 'error' : ''}`;
  $('engine-status-label').textContent = ready ? 'Voice ready' : loading ? 'Preparing voice…' : failed ? 'Voice: try again' : 'Set up voice';
  $('engine-title').textContent = ready ? 'Ready to talk.' : loading ? 'Getting your voice ready.' : failed ? 'Voice setup needs attention.' : 'Set up voice once.';
  $('engine-description').textContent = ready ? 'Parakeet transcribes your words and LoudKit reads the replies. Both models are ready on this computer.' : loading ? 'Downloading and loading local models. The first run may take a few minutes. You can connect your agent while you wait.' : failed ? [stt.error, tts.error].filter(Boolean).join(' · ') || 'Could not start the model. Try again.' : 'Parakeet transcribes your words and LoudKit reads the reply. This downloads the models to this computer once.';
  const labels = { idle: 'awaiting setup', loading: 'loading…', ready: 'ready', error: 'needs attention' };
  $('stt-chip').textContent = `Parakeet · ${labels[stt.state] || 'awaiting setup'}`;
  $('tts-chip').textContent = `LoudKit · ${labels[tts.state] || 'awaiting setup'}`;
  $('prepare-engines').disabled = Boolean(loading || ready);
  $('prepare-engines').textContent = ready ? 'Voice ready ✓' : loading ? 'Preparing…' : failed ? 'Try again' : 'Set up voice ↓';
}
function statusInfo(channel) {
  const status = channel.status || {};
  if (status.state === 'error') return { label: 'Needs attention', state: 'error' };
  if (status.state === 'running') return { label: 'Receiving messages', state: 'running' };
  if (status.state === 'configured') return { label: 'Configured', state: 'configured' };
  return { label: 'Disabled', state: 'disabled' };
}
function statusHTML(status) { return `<span class="channel-status ${esc(status.state)}"><span class="dot"></span>${esc(status.label)}</span>`; }
function renderChannels() {
  const key = JSON.stringify([state.channels, state.agents]); if (key === state.renderKeys.channels) return; state.renderKeys.channels = key;
  $('channel-count').textContent = state.channels.length;
  if (!state.channels.length) { $('channel-list').innerHTML = '<div class="empty-state"><span class="empty-symbol" aria-hidden="true">↗</span><div><h3>Your messengers will appear here.</h3><p>Choose a connection method above to get started.</p></div></div>'; return; }
  $('channel-list').innerHTML = state.channels.map((channel) => {
    const info = statusInfo(channel), platform = platformFor(channel.platform), agent = agentFor(channel.agent_id);
    return `<article class="channel-row ${esc(info.state)}">${platformIcon(channel.platform)}<div class="channel-main"><h3>${esc(channel.name || platform?.name || channel.platform)}</h3><p>${esc(platform?.name || channel.platform)} <span aria-hidden="true">↔</span> ${esc(agent?.name || 'Choose an agent')}</p>${channel.status?.error ? `<p class="channel-error-summary">${esc(channel.status.error)}</p>` : ''}</div>${statusHTML(info)}<button class="channel-open" data-connection="${esc(channel.id)}" aria-label="Open connection ${esc(channel.name)}">Open <span aria-hidden="true">↗</span></button></article>`;
  }).join('');
}
function renderPairings() {
  const pending = state.pairings.filter((item) => !item.approved && !['approved', 'rejected', 'expired'].includes(item.status));
  const key = JSON.stringify(pending); if (key === state.renderKeys.pairings) return; state.renderKeys.pairings = key;
  $('pairing-section').hidden = pending.length === 0;
  $('pairing-list').innerHTML = pending.map((pairing) => {
    const channel = channelFor(pairing.channel_id);
    return `<article class="pairing-card"><div class="pairing-main"><h3>${esc(channel?.name || 'New conversation')}</h3><p>Your bot received its first message. Approve only your own chat.</p><div class="pairing-ids">Chat: <code>${esc(pairing.chat_id)}</code><br>Sender: <code>${esc(pairing.sender_id)}</code></div></div><button class="button dark" data-pairing="${esc(pairing.id)}" data-channel="${esc(pairing.channel_id)}">Yes, this is my chat <span aria-hidden="true">✓</span></button></article>`;
  }).join('');
}
function formatTime(value) { if (!value) return ''; const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value); return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(date); }
function renderEvents() {
  const events = state.events.slice(0, 8), key = JSON.stringify(events); if (key === state.renderKeys.events) return; state.renderKeys.events = key;
  $('activity-section').hidden = events.length === 0;
  $('event-list').innerHTML = events.map((event) => `<article class="event-row"><span class="event-icon" aria-hidden="true">ııı</span><div class="event-main"><strong>${esc(channelFor(event.channel_id)?.name || 'Voice message')}</strong><p>${esc(event.error || event.transcript || (event.status === 'sent' ? 'Your reply was sent back to your messenger.' : 'A message from your messenger.'))}</p></div>${statusHTML({ state: event.status, label: eventNames[event.status] || 'Received' })}<time class="event-time" datetime="${esc(event.created_at || '')}">${esc(formatTime(event.created_at))}</time></article>`).join('');
}
async function bootstrap(force = false) {
  if (state.polling) { await state.polling; if (!force) return; }
  const task = fetchBootstrap(); state.polling = task;
  try { await task; } finally { if (state.polling === task) state.polling = null; }
}
async function fetchBootstrap() {
  try {
    const data = await api('/api/channels/bootstrap');
    state.channels = asList(data.channels); state.catalog = asList(data.catalog); state.agents = asList(data.agents); state.voices = asList(data.voices); state.pairings = asList(data.pairings); state.events = asList(data.events); state.engine = data.engine || {};
    if (Array.isArray(data.native_agents)) state.nativeAgents = data.native_agents;
    state.loaded = true; $('connection-alert').hidden = true;
    renderEngine(); renderChannels(); renderPairings(); renderEvents();
    if ($('connection-dialog').open && state.connectionId) updateConnectionStatus();
  } catch (error) { $('connection-alert').hidden = false; $('engine-status-label').textContent = 'Disconnected'; $('engine-status').className = 'engine-button error'; throw error; }
}
async function loadAgentOptions() {
  const data = await api('/api/bootstrap'); state.presets = asList(data.command_presets); state.directory = data.working_directory || ''; state.mcpConfig = data.mcp_config || null;
  if (!state.voices.length) state.voices = asList(data.voices); if (!state.agents.length) state.agents = asList(data.agents);
}
function fillVoices(id, value) {
  const select = $(id); select.replaceChildren();
  const available = state.voices.length ? state.voices : [{ id: 'sophie', name: 'Sophie', language: 'en' }];
  const voices = available;
  for (const voice of voices) { const option = document.createElement('option'); option.value = voice.id; option.textContent = `${voice.name || voice.id}${voice.language ? ` · ${voice.language}` : ''}`; select.append(option); }
  select.value = value || (voices.some((voice) => voice.id === 'sophie') ? 'sophie' : voices[0].id);
}
async function openNative() {
  hideError('native-error'); $('native-result').hidden = true; $('native-result').replaceChildren(); state.nativeResult = null; fillVoices('native-voice'); $('native-base-url').value = `${location.origin}/v1`;
  $('native-options').innerHTML = '<p class="loading-text">Loading connection options…</p>'; $('generate-native').disabled = true; openDialog('native-dialog');
  try {
    if (!state.nativeAgents.length) { const result = await api('/api/native-agents'); state.nativeAgents = Array.isArray(result) ? result : asList(result.agents || result.native_agents); }
    if (!state.nativeAgents.length) throw new Error('No preset is available. You can connect your agent through an API, webhook or local app.');
    const selection = state.nativeAgents[0].id; $('native-agent').value = selection;
    $('native-options').innerHTML = state.nativeAgents.map((agent) => `<button class="native-choice" type="button" data-native="${esc(agent.id)}" aria-pressed="${agent.id === selection}"><span aria-hidden="true">${agent.id === 'hermes' ? 'H' : agent.id === 'openclaw' ? '✳' : '↗'}</span>${esc(agent.name)}</button>`).join('');
    $('generate-native').disabled = false;
  } catch (error) { $('native-options').replaceChildren(); showError('native-error', error); }
}
function stepText(step) { if (typeof step === 'string') return step; return [step.title, step.description || step.text || step.instruction, step.command].filter(Boolean).join(' — '); }
function renderNativeResult(result) {
  state.copyValues = [];
  const files = asList(result.files);
  const fileHTML = files.map((file) => { const content = typeof file.content === 'string' ? file.content : JSON.stringify(file.content, null, 2); const index = state.copyValues.push(content) - 1; return `<div class="config-file"><div class="config-file-head"><code>${esc(file.path || file.name || 'Configuration')}</code><button type="button" class="copy-button" data-copy="${index}">Copy</button></div><pre>${esc(content)}</pre></div>`; }).join('');
  const steps = asList(result.steps), commands = asList(result.chat_commands);
  const channelRows = asList(result.channels).map((item) => {
    if (typeof item === 'string') return `<li>${esc(platformFor(item)?.name || item)}</li>`;
    const name = platformFor(item.id)?.name || item.name || item.id;
    const output = item.output === 'voice_note' ? 'voice message' : 'audio file';
    const automatic = item.automatic_reply === true ? 'automatic reply' : 'check support in your agent version';
    return `<li><strong>${esc(name)}</strong> · ${esc(output)}, ${esc(automatic)}${item.note ? `<span>${esc(item.note)}</span>` : ''}</li>`;
  }).join('');
  const selected = state.nativeAgents.find((agent) => agent.id === $('native-agent').value);
  $('native-result').innerHTML = `<h3>Voice for ${esc(selected?.name || 'your agent')}.</h3><p>Merge this snippet into your existing agent settings. Keep the rest of your configuration. Loudkit Voice must be running when you send voice messages.</p>${fileHTML}${steps.length ? `<ol class="setup-steps">${steps.map((step) => `<li>${esc(stepText(step))}</li>`).join('')}</ol>` : ''}${commands.length ? `<details class="native-capabilities"><summary>Commands to check or change voice mode</summary><p class="field-help">These are separate options. Use only the command you need.</p>${commands.map((command) => { const value = typeof command === 'string' ? command : command.command || command.text || stepText(command); const index = state.copyValues.push(value) - 1; const commandHint = /(?:^|\s)off$/.test(value) ? 'Disables voice replies' : /(?:^|\s)status$/.test(value) ? 'Shows current settings' : /(?:^|\s)on$/.test(value) ? 'Enables voice replies' : 'Changes reply mode'; return `<p class="field-help">${esc(commandHint)}</p><div class="config-file"><div class="config-file-head"><code>${esc(value)}</code><button type="button" class="copy-button" data-copy="${index}">Copy</button></div></div>`; }).join('')}</details>` : ''}${channelRows ? `<details class="native-capabilities"><summary>How will replies appear in your messenger?</summary><ul>${channelRows}</ul></details>` : ''}${asList(result.notes).length ? `<details class="native-capabilities"><summary>Notes for your agent settings</summary><ul>${result.notes.map((note) => `<li>${esc(note)}</li>`).join('')}</ul></details>` : ''}${asList(result.sources).length ? `<details class="native-capabilities"><summary>Connection documentation</summary><ul>${result.sources.map((source) => safeURL(source.url) ? `<li><a href="${esc(safeURL(source.url))}" target="_blank" rel="noopener noreferrer">${esc(source.title || 'Documentation')} ↗</a></li>` : '').join('')}</ul></details>` : ''}<div class="native-done">After saving, send a voice message in your existing agent chat and check the reply. Generating configuration does not verify the connection.</div>`;
  $('native-result').hidden = false; $('native-result').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function fillAgents(selected) {
  const select = $('channel-agent'); select.replaceChildren();
  const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = state.agents.length ? 'Choose your agent' : 'Add your agent first'; select.append(placeholder);
  for (const agent of state.agents) { const option = document.createElement('option'); option.value = agent.id; option.textContent = `${agent.name}${agent.kind === 'command' ? ' · local app' : agent.kind === 'webhook' ? ' · custom bot' : agent.kind === 'inbox' ? ' · MCP / tools' : ' · API'}`; select.append(option); }
  if (selected) select.value = selected; updateSelectedAgent();
}
function updateSelectedAgent() { $('configure-agent').hidden = agentFor($('channel-agent').value)?.kind !== 'inbox'; }
function platformDescription(platform) { return platform.description || (platform.mode === 'webhook' ? 'Connect through a webhook' : 'Connect directly to your bot'); }
function voiceFormat(platform) { return platform.voice_note ? 'Replies arrive as voice messages in the same chat.' : 'Replies arrive as playable audio files in the same chat.'; }
function renderPlatformPicker() {
  $('platform-picker').innerHTML = state.catalog.length ? state.catalog.map((platform) => `<button type="button" class="platform-option" data-platform="${esc(platform.id)}">${platformIcon(platform.id)}<span><strong>${esc(platform.name)}</strong><small>${esc(platformDescription(platform))}</small></span><span aria-hidden="true">↗</span></button>`).join('') : '<p class="loading-text">Could not load messengers. Close this window and try again when Loudkit Voice is available.</p>';
}
function showPlatformPicker() { $('platform-picker').hidden = false; $('channel-form').hidden = true; $('channel-step').textContent = '1 / 2 · CHOOSE A MESSENGER'; $('channel-dialog-title').textContent = 'Where do you want to chat?'; $('channel-dialog-intro').textContent = 'Connect your messenger to your agent. Voice messages and replies stay in the same chat.'; renderPlatformPicker(); }
function openChannel(channel = null) {
  state.channelId = channel?.id || null; state.platform = channel?.platform || null; $('channel-form').reset(); hideError('channel-error');
  if (channel) choosePlatform(channel.platform, channel); else showPlatformPicker();
  openDialog('channel-dialog');
}
function renderField(field, channel) {
  const id = `setting-${field.key}`; const secretSet = asList(channel?.secret_fields_set).includes(field.key); const value = field.secret ? '' : channel?.settings?.[field.key] ?? field.default ?? '';
  const required = field.required && !(field.secret && secretSet); const type = field.secret ? 'password' : field.type === 'number' ? 'number' : field.type === 'url' ? 'url' : 'text';
  const help = [field.help, secretSet ? 'A value is saved. Leave this blank to keep it.' : ''].filter(Boolean).join(' ');
  const attributes = `id="${esc(id)}" data-setting="${esc(field.key)}"${required ? ' required' : ''}${help ? ` aria-describedby="${esc(id)}-help"` : ''}`;
  let control;
  if (field.type === 'boolean' || field.type === 'checkbox') control = `<div class="form-checkbox"><input type="checkbox" ${attributes}${value === true || value === 'true' ? ' checked' : ''}><label for="${esc(id)}">${esc(field.label || field.key)}</label></div>`;
  else if (Array.isArray(field.options)) control = `<label for="${esc(id)}">${esc(field.label || field.key)}</label><select ${attributes}>${field.options.map((item) => { const key = typeof item === 'object' ? item.value ?? item.id : item; const label = typeof item === 'object' ? item.label ?? item.name : item; return `<option value="${esc(key)}"${String(value) === String(key) ? ' selected' : ''}>${esc(label)}</option>`; }).join('')}</select>`;
  else control = `<label for="${esc(id)}">${esc(field.label || field.key)}${!required ? '<span> · optional</span>' : ''}</label><input type="${type}" ${attributes} value="${esc(value)}" placeholder="${esc(secretSet ? 'Saved — enter only to change' : field.placeholder || '')}" autocomplete="${field.secret ? 'new-password' : 'off'}"${field.type === 'number' ? ' step="any"' : ''}>`;
  return `<div class="field">${control}${help ? `<p class="field-help" id="${esc(id)}-help">${esc(help)}</p>` : ''}</div>`;
}
function choosePlatform(id, channel = null) {
  const platform = platformFor(id); if (!platform) return;
  state.platform = id; $('platform-picker').hidden = true; $('channel-form').hidden = false; $('channel-step').textContent = channel ? 'CONNECTION SETTINGS' : '2 / 2 · CONNECT YOUR AGENT'; $('channel-dialog-title').textContent = `${platform.name} has a voice.`; $('channel-dialog-intro').textContent = 'Enter your bot details and choose the agent that will reply.'; $('change-platform').hidden = Boolean(channel);
  const docs = safeURL(platform.docs || platform.docs_url);
  $('platform-guide').innerHTML = `<p>${esc(platformDescription(platform))}</p>${docs ? `<a href="${esc(docs)}" target="_blank" rel="noopener noreferrer">Where do I find my connection details? ↗</a>` : ''}<p class="voice-format">${esc(voiceFormat(platform))}</p>`;
  $('channel-name').value = channel?.name || `My ${platform.name}`; fillAgents(channel?.agent_id); $('channel-fields').innerHTML = asList(platform.fields).map((field) => renderField(field, channel)).join('');
  $('channel-webhook-hint').hidden = platform.mode !== 'webhook'; $('channel-webhook-hint').textContent = id === 'imessage' ? 'BlueBubbles needs to forward new messages to Loudkit Voice. Save to see the webhook URL. A local address works on the same Mac.' : 'This messenger needs to send events to a public HTTPS address. Save the connection to see the webhook URL and next steps.'; $('save-channel').innerHTML = `${channel ? 'Save changes' : 'Save connection'} <span aria-hidden="true">→</span>`;
}
function updateConnectionStatus() {
  const channel = channelFor(state.connectionId); if (!channel || !$('connection-live-status')) return;
  $('connection-live-status').innerHTML = statusHTML(statusInfo(channel));
  const error = $('connection-runtime-error'); error.textContent = channel.status?.error || ''; error.hidden = !channel.status?.error; renderConnectionPairings();
}
function openConnection(id) {
  const channel = channelFor(id); if (!channel) return;
  state.connectionId = id; hideError('connection-error');
  const platform = platformFor(channel.platform) || {}, agent = agentFor(channel.agent_id); const docs = safeURL(platform.docs || platform.docs_url); const info = statusInfo(channel);
  const pairings = state.pairings.filter((pairing) => pairing.channel_id === id && !pairing.approved && !['approved', 'rejected', 'expired'].includes(pairing.status));
  const paired = asList(channel.allowed_senders).length > 0 || asList(channel.allowed_chats).length > 0;
  $('connection-content').innerHTML = `<div class="connection-heading">${platformIcon(channel.platform)}<div><h2 id="connection-title">${esc(channel.name)}</h2><p>${esc(platform.name || channel.platform)} <span aria-hidden="true">↔</span> ${esc(agent?.name || 'Choose an agent')}</p></div></div><div id="connection-live-status">${statusHTML(info)}</div><p id="connection-runtime-error" class="form-error" ${channel.status?.error ? '' : 'hidden'}>${esc(channel.status?.error)}</p><div id="connection-pairing-notice" hidden></div><ol class="connection-checklist"><li><span class="step-number">1</span><div><h3>Check connection details</h3><p>Check access to your messenger. This does not send a message.</p></div></li><li><span class="step-number">2</span><div><h3>Start receiving messages</h3><p>${platform.mode === 'webhook' ? 'Set the webhook URL below in your messenger, then enable the connection.' : 'Loudkit Voice must be running to pass voice messages to your agent.'}</p></div></li><li><span class="step-number">3</span><div><h3>${paired ? 'Your chat is approved' : pairings.length ? 'Confirm your chat' : 'Send your first voice message'}</h3><p>${paired ? 'Record a message in your messenger. Your agent will process the command and reply with audio.' : 'Record a message in your bot chat. Loudkit Voice will ask “Is this you?” — approve your chat, then send the command again.'}</p></div></li></ol>${platform.mode === 'webhook' ? `<details class="webhook-box"><summary>Webhook URL for ${esc(platform.name)}</summary><p>${channel.platform === 'imessage' ? 'Enter a Loudkit Voice address that BlueBubbles can reach. On the same Mac, use a local address. Another computer needs a reachable, secured connection.' : 'Your messenger needs a public HTTPS address for Loudkit Voice. Enter your tunnel or domain below. Expose only the /hooks/ receiver path.'}</p><label for="public-base-url">${channel.platform === 'imessage' ? 'Loudkit Voice address for BlueBubbles' : 'Public Loudkit Voice address'}</label><input type="url" id="public-base-url" placeholder="https://your-domain.example" value="${channel.platform === 'imessage' ? esc(location.origin) : ''}"><button type="button" id="get-webhook-info" class="button subtle">Show webhook URL <span aria-hidden="true">↗</span></button><div id="webhook-output" class="webhook-output" hidden></div>${docs ? `<p><a href="${esc(docs)}" target="_blank" rel="noopener noreferrer">Messenger guide ↗</a></p>` : ''}</details>` : ''}<div class="connection-actions"><button id="check-channel" class="button subtle">Check connection</button><button id="toggle-channel" class="button ${channel.enabled ? 'subtle' : 'coral'}">${channel.enabled ? 'Pause receiving' : 'Enable connection'}</button></div>${agent?.kind === 'inbox' ? '<button id="connection-mcp" class="text-button connection-edit">Show MCP tool configuration ↗</button><br>' : ''}<button id="edit-channel" class="text-button connection-edit">Edit connection details <span aria-hidden="true">↗</span></button>`;
  $('check-channel').onclick = () => checkChannel(id); $('toggle-channel').onclick = () => toggleChannel(id); $('edit-channel').onclick = () => { $('connection-dialog').close(); openChannel(channelFor(id)); };
  if ($('get-webhook-info')) $('get-webhook-info').onclick = () => getWebhookInfo(id);
  if ($('connection-mcp')) $('connection-mcp').onclick = () => openMcpSetup(agent);
  state.renderKeys.connectionPairings = ''; renderConnectionPairings();
  openDialog('connection-dialog');
}
async function checkChannel(id) {
  const button = $('check-channel'); button.disabled = true; button.textContent = 'Checking…'; hideError('connection-error');
  try { const result = await api(`/api/channels/${pathId(id)}/check`, { method: 'POST' }); if (result.ok === false) throw new Error(result.error || result.detail || 'Could not verify the connection. Check your bot details.'); button.textContent = 'Details verified ✓'; toast(result.name ? `Details verified: ${result.name}.` : 'Connection details are valid.'); await bootstrap(true); }
  catch (error) { showError('connection-error', error); button.textContent = 'Check again'; }
  finally { button.disabled = false; }
}
async function toggleChannel(id) {
  const channel = channelFor(id); if (!channel) return; const button = $('toggle-channel'); button.disabled = true; hideError('connection-error');
  try { await api(`/api/channels/${pathId(id)}/${channel.enabled ? 'disable' : 'enable'}`, { method: 'POST' }); await bootstrap(true); openConnection(id); toast(channel.enabled ? 'Receiving paused.' : 'Connection requested. Check its status in the panel.'); }
  catch (error) { showError('connection-error', error); button.disabled = false; }
}
async function getWebhookInfo(id) {
  const input = $('public-base-url'); if (!input.value.trim()) { input.setCustomValidity('Enter a public HTTPS address.'); input.reportValidity(); input.setCustomValidity(''); return; } if (!input.reportValidity()) return;
  const button = $('get-webhook-info'); button.disabled = true; hideError('connection-error');
  try {
    const result = await api(`/api/channels/${pathId(id)}/webhook-info`, { method: 'POST', body: { public_base_url: input.value.trim() } });
    const url = result.url || result.webhook_url; const target = $('webhook-output'); target.innerHTML = `<p>Paste this URL into your messenger event settings:</p><code>${esc(url || '')}</code><button type="button" class="copy-button" id="copy-webhook">Copy address</button>${asList(result.steps).length ? `<ol class="setup-steps">${result.steps.map((step) => `<li>${esc(stepText(step))}</li>`).join('')}</ol>` : ''}${result.verify_token ? `<p>Verification token</p><code>${esc(result.verify_token)}</code>` : ''}${result.note ? `<p>${esc(result.note)}</p>` : ''}${asList(result.events).length ? `<p>Enable these events: <strong>${esc(result.events.join(', '))}</strong>.</p>` : ''}`; target.hidden = false; $('copy-webhook').onclick = (event) => copyText(url || '', event.currentTarget);
  } catch (error) { showError('connection-error', error); }
  finally { button.disabled = false; }
}
function updateAgentKind() {
  const kind = $('agent-kind').value, command = kind === 'command', inbox = kind === 'inbox';
  $('agent-inbox-fields').hidden = !inbox; $('agent-command-fields').hidden = !command; $('agent-endpoint-fields').hidden = command || inbox; $('agent-model-field').hidden = kind !== 'openai';
  $('agent-command').required = command; $('agent-directory').required = command; $('agent-endpoint').required = !command && !inbox; $('agent-model').required = kind === 'openai';
  $('agent-endpoint-label').textContent = kind === 'webhook' ? 'Your bot or workflow address' : 'API address';
  $('agent-command-fields').querySelectorAll('input, select').forEach((input) => { input.disabled = !command; }); $('agent-endpoint-fields').querySelectorAll('input, select').forEach((input) => { input.disabled = command || inbox; }); $('agent-model').disabled = kind !== 'openai';
  $('agent-endpoint').placeholder = kind === 'webhook' ? 'http://localhost:8080/voice-message' : 'http://localhost:8000/v1/chat/completions';
  $('agent-endpoint-help').textContent = kind === 'webhook' ? 'Enter your agent webhook that accepts a message and returns a text reply in Loudkit Voice format. This is not your messenger webhook URL.' : 'Use an OpenAI Chat Completions-compatible endpoint. A model API connection does not provide access to your existing agent or its memory.';
}
function renderMcpBlock(targetId, agent, prefix) {
  const config = state.mcpConfig ? JSON.stringify(state.mcpConfig, null, 2) : '';
  const instruction = `Check the Loudkit Voice inbox for agent ${agent.id} using receive_voice_messages. Reply with send_voice_message, using the received message’s conversation_id and setting reply_to_message_id to its id. Do not retry a reply with uncertain delivery; check the chat first. Send the reply back to the same messenger.`;
  $(targetId).innerHTML = `<p>Add the MCP server in your agent settings, then give it the instruction below.</p>${config ? `<div class="config-file"><div class="config-file-head"><code>MCP configuration</code><button type="button" class="copy-button" id="${prefix}-mcp-copy">Copy</button></div><pre>${esc(config)}</pre></div>` : ''}<div class="config-file"><div class="config-file-head"><code>Instruction for your agent</code><button type="button" class="copy-button" id="${prefix}-instruction-copy">Copy</button></div><pre>${esc(instruction)}</pre></div><p class="inbox-id">Agent ID: ${esc(agent.id)}</p><p>Your agent needs to check messages using the MCP tool. Adding this configuration alone does not start an automatic listener.</p><p><a href="/docs/grokbot.md" target="_blank" rel="noopener noreferrer">Grok Bot guide ↗</a></p>`;
  $(targetId).hidden = false;
  if ($(`${prefix}-mcp-copy`)) $(`${prefix}-mcp-copy`).onclick = (event) => copyText(config, event.currentTarget);
  $(`${prefix}-instruction-copy`).onclick = (event) => copyText(instruction, event.currentTarget);
}
async function openMcpSetup(agent) {
  if (!agent || agent.kind !== 'inbox') return;
  $('mcp-title').textContent = `Tools for ${agent.name}.`; $('mcp-setup-content').innerHTML = '<p class="loading-text">Loading configuration…</p>'; openDialog('mcp-dialog');
  try { await loadAgentOptions(); renderMcpBlock('mcp-setup-content', agent, 'existing-agent'); } catch (error) { $('mcp-setup-content').innerHTML = `<p class="form-error">${esc(errorText(error))}</p>`; }
}
function renderConnectionPairings() {
  const target = $('connection-pairing-notice'); if (!target) return;
  const pairings = state.pairings.filter((item) => item.channel_id === state.connectionId && !item.approved && !['approved', 'rejected', 'expired'].includes(item.status));
  const key = JSON.stringify(pairings); if (key === state.renderKeys.connectionPairings) return; state.renderKeys.connectionPairings = key;
  target.hidden = !pairings.length;
  target.innerHTML = pairings.map((pairing) => `<article class="pairing-card"><div class="pairing-main"><h3>Is this you?</h3><p>Approve only your own chat.</p><div class="pairing-ids">Chat: <code>${esc(pairing.chat_id)}</code><br>Sender: <code>${esc(pairing.sender_id)}</code></div></div><button class="button dark" data-pairing="${esc(pairing.id)}" data-channel="${esc(pairing.channel_id)}">Yes, this is my chat ✓</button></article>`).join('');
  target.onclick = approvePairing;
}
async function approvePairing(event) {
  const button = event.target.closest('[data-pairing]'); if (!button) return; button.disabled = true;
  try { await api(`/api/channels/${pathId(button.dataset.channel)}/pairings/${pathId(button.dataset.pairing)}/approve`, { method: 'POST' }); await bootstrap(true); toast('Chat approved. Send a voice message with your command.'); }
  catch (error) { toast(errorText(error), true); button.disabled = false; }
}
async function openAgent() {
  $('agent-form').reset(); $('agent-form').querySelectorAll('input, select').forEach((input) => { input.disabled = false; }); state.createdInbox = null; $('agent-mcp-result').hidden = true; $('agent-mcp-result').replaceChildren(); $('save-agent').textContent = 'Add agent →'; hideError('agent-error'); fillVoices('agent-voice'); $('save-agent').disabled = true; openDialog('agent-dialog');
  try { await loadAgentOptions(); fillVoices('agent-voice'); $('agent-command').innerHTML = '<option value="">Choose your agent app</option>' + state.presets.map((preset) => `<option value="${esc(preset.id)}"${preset.installed ? '' : ' disabled'}>${esc(preset.name)} · ${preset.installed ? 'installed' : 'needs installation'}</option>`).join(''); $('agent-directory').value = state.directory; updateAgentKind(); }
  catch (error) { showError('agent-error', error); }
  finally { $('save-agent').disabled = false; }
}

$('engine-status').onclick = () => setEnginePanel($('engine-panel').hidden);
$('close-engine-panel').onclick = () => setEnginePanel(false);
$('prepare-engines').onclick = async () => { $('prepare-engines').disabled = true; try { state.engine = await api('/api/engines/prepare', { method: 'POST' }); renderEngine(); } catch (error) { toast(errorText(error), true); $('prepare-engines').disabled = false; } };
$('retry-load').onclick = () => bootstrap().catch(() => {});
$('open-native').onclick = openNative;
$('native-other-agent').onclick = () => { $('native-dialog').close(); openChannel(); };
$('open-channel').onclick = () => openChannel();
$('add-channel').onclick = () => openChannel();
$('change-platform').onclick = showPlatformPicker;
$('add-agent').onclick = openAgent;
$('channel-agent').onchange = updateSelectedAgent;
$('configure-agent').onclick = () => openMcpSetup(agentFor($('channel-agent').value));
$('agent-kind').onchange = updateAgentKind;
$('native-options').onclick = (event) => { const button = event.target.closest('[data-native]'); if (!button) return; $('native-agent').value = button.dataset.native; $('native-options').querySelectorAll('[data-native]').forEach((item) => item.setAttribute('aria-pressed', String(item === button))); $('native-result').hidden = true; };
$('native-form').onsubmit = async (event) => { event.preventDefault(); hideError('native-error'); const id = $('native-agent').value; if (!id) { showError('native-error', new Error('Choose your agent.')); return; } const button = $('generate-native'); button.disabled = true; button.textContent = 'Preparing configuration…'; try { const result = await api(`/api/native-agents/${pathId(id)}/setup`, { method: 'POST', body: { voice: $('native-voice').value, base_url: $('native-base-url').value.trim() } }); state.nativeResult = result; renderNativeResult(result); } catch (error) { showError('native-error', error); } finally { button.disabled = false; button.textContent = 'Show configuration →'; } };
$('native-result').onclick = (event) => { const button = event.target.closest('[data-copy]'); if (button) copyText(state.copyValues[Number(button.dataset.copy)], button); };
$('platform-picker').onclick = (event) => { const button = event.target.closest('[data-platform]'); if (button) choosePlatform(button.dataset.platform); };
$('channel-list').onclick = (event) => { const button = event.target.closest('[data-connection]'); if (button) openConnection(button.dataset.connection); };
$('pairing-list').onclick = approvePairing;
$('channel-form').onsubmit = async (event) => {
  event.preventDefault(); if (!$('channel-form').reportValidity()) return; hideError('channel-error'); const platform = platformFor(state.platform); if (!platform) return;
  const settings = {}, secrets = {};
  for (const field of asList(platform.fields)) { const input = $(`setting-${field.key}`); if (!input) continue; const value = field.type === 'boolean' || field.type === 'checkbox' ? input.checked : field.type === 'number' && input.value !== '' ? Number(input.value) : input.value.trim(); if (field.secret) { if (value) secrets[field.key] = value; } else settings[field.key] = value; }
  const payload = { name: $('channel-name').value.trim(), agent_id: $('channel-agent').value, settings, secrets };
  if (!state.channelId) { payload.platform = state.platform; payload.enabled = false; }
  const button = $('save-channel'); button.disabled = true;
  try { const result = await api(state.channelId ? `/api/channels/${pathId(state.channelId)}` : '/api/channels', { method: state.channelId ? 'PATCH' : 'POST', body: payload }); await bootstrap(true); $('channel-dialog').close(); openConnection(result.id || result.channel?.id || state.channelId); toast('Connection saved. Check the details, then enable receiving.'); }
  catch (error) { showError('channel-error', error); }
  finally { button.disabled = false; }
};
$('agent-form').onsubmit = async (event) => {
  event.preventDefault(); if (state.createdInbox) { $('agent-dialog').close(); state.createdInbox = null; return; } if (!$('agent-form').reportValidity()) return; hideError('agent-error');
  const kind = $('agent-kind').value, data = { name: $('agent-name').value.trim(), kind, voice: $('agent-voice').value, color: '#879b6d' };
  if (kind === 'command') { data.command_id = $('agent-command').value; data.working_directory = $('agent-directory').value.trim(); } else if (kind !== 'inbox') { data.endpoint = $('agent-endpoint').value.trim(); if (kind === 'openai') data.model = $('agent-model').value.trim(); const key = $('agent-api-key').value.trim(); if (key) data.api_key = key; }
  const button = $('save-agent'); button.disabled = true;
  try { const agent = await api('/api/agents', { method: 'POST', body: data }); await bootstrap(true); if (!state.agents.some((item) => item.id === agent.id)) state.agents.push(agent); fillAgents(agent.id); if (kind === 'inbox') {
      state.createdInbox = agent.id; $('agent-form').querySelectorAll('input, select').forEach((input) => { input.disabled = true; });
      renderMcpBlock('agent-mcp-result', agent, 'new-agent');
      button.textContent = 'Done, back to your messenger →'; $('agent-mcp-result').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      toast('Inbox ready. Add the Loudkit Voice tools to your agent.');
    } else { $('agent-dialog').close(); toast('Agent added. Now save your messenger connection.'); } }
  catch (error) { showError('agent-error', error); }
  finally { button.disabled = false; }
};
document.querySelectorAll('[data-close]').forEach((button) => { button.onclick = () => $(button.dataset.close).close(); });
document.querySelectorAll('dialog').forEach((dialog) => { dialog.addEventListener('click', (event) => { if (event.target !== dialog) return; const box = dialog.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close(); }); });
bootstrap().catch(() => {});
setInterval(() => { if (!document.hidden) bootstrap().catch(() => {}); }, 3000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) bootstrap().catch(() => {}); });
