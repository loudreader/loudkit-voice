#!/usr/bin/env node
/**
 * Run pinned OpenClaw speech-provider source against a real local LoudTalk API.
 * Node 26+ and ffprobe/ffmpeg must already be installed.
 *
 * node --experimental-vm-modules scripts/verify_openclaw_voice.mjs \
 *   --base-url http://127.0.0.1:18765/v1 \
 *   --source-dir /tmp/loudtalk-native-research --output-dir /tmp/openclaw-live-proof
 *
 * The upstream provider, normalizer, validators and request construction execute
 * unchanged after Node strips TypeScript types. SDK helpers are explicit test
 * adapters. Real HTTP response bytes are saved, probed and fully decoded. This
 * does not start an OpenClaw gateway or send messages to any messaging account.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import vm from 'node:vm';

const REVISION = 'b068eab40767fc6796899cdc85d08102e8035b2b';
const SOURCES = [
  { identifier: './tts.js', filename: 'openclaw-extensions_openai_tts.ts', upstream: 'extensions/openai/tts.ts', sha256: '87bd798376da60aeda790413f7fc5f40357cdaec17262c017dcf794512594891' },
  { identifier: './speech-provider.js', filename: 'openclaw-extensions_openai_speech-provider.ts', upstream: 'extensions/openai/speech-provider.ts', sha256: 'f68963068c723eb325d8159da98733b47b95f236e5de6fbe092d2d6fd35712fd' },
  { identifier: './realtime-provider-shared.js', filename: 'openclaw-extensions_openai_realtime-provider-shared.ts', upstream: 'extensions/openai/realtime-provider-shared.ts', sha256: '345c4c4656bebf9718542237f7a2a137e25a2b5ae18cdbd1fc02cc1aa5060a47' },
];
const STT_SOURCES = [
  { identifier: './audio-transcription.js', filename: 'openclaw-extensions_openai_audio-transcription.ts', upstream: 'extensions/openai/audio-transcription.ts', sha256: 'eae0ad8b79a018f7cb57d92319a326958657204398daa11eb719f4430c7f1597' },
  { identifier: './openai-compatible-audio.js', filename: 'openclaw-src_media-understanding_openai-compatible-audio.ts', upstream: 'src/media-understanding/openai-compatible-audio.ts', sha256: 'bf610529d620fd628e6564727695a9007848f1f179eaf3c53b6b574e54702e61' },
  { identifier: './openai-audio-api.js', filename: 'openclaw-src_media-understanding_openai-audio-api.ts', upstream: 'src/media-understanding/openai-audio-api.ts', sha256: '3789dd7e964ae3a989112375c3f1df689a51c80f33aeb0fde8ef4670daaf5cee' },
];
const HELP = 'Usage: node --experimental-vm-modules scripts/verify_openclaw_voice.mjs --base-url http://127.0.0.1:18765/v1 --source-dir PATH --output-dir PATH [--timeout-ms 180000] [--voice gosia] [--text "Dzień dobry."] [--audio-file PATH] [--expect-text "Expected transcription"]';
const hash = (value) => createHash('sha256').update(value).digest('hex');

function parseArgs(argv) {
  const allowed = new Set(['--base-url', '--source-dir', '--output-dir', '--timeout-ms', '--voice', '--text', '--audio-file', '--expect-text']);
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    if (!allowed.has(key) || argv[i + 1] === undefined || argv[i + 1].startsWith('--')) throw new Error(`Invalid argument ${key}. ${HELP}`);
    if (args[key] !== undefined) throw new Error(`Repeated argument ${key}`);
    args[key] = argv[i + 1];
  }
  for (const key of ['--base-url', '--source-dir', '--output-dir']) if (!args[key]) throw new Error(`Missing ${key}. ${HELP}`);
  const url = new URL(args['--base-url']);
  if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.username || url.password || url.search || url.hash) {
    throw new Error('--base-url must be an explicit loopback HTTP(S) URL without credentials, query or fragment');
  }
  if (url.pathname.replace(/\/+$/, '') !== '/v1') throw new Error('--base-url must include /v1');
  const timeoutMs = Number(args['--timeout-ms'] ?? 180000);
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 600000) throw new Error('--timeout-ms must be an integer from 1000 to 600000');
  const text = (args['--text'] ?? 'Dzień dobry.').trim();
  if (!text || text.length > 500) throw new Error('--text must contain 1 to 500 characters');
  const voice = (args['--voice'] ?? 'gosia').trim();
  if (!voice) throw new Error('--voice cannot be empty');
  const audioFile = args['--audio-file'] ? path.resolve(args['--audio-file']) : undefined;
  if (audioFile) {
    const stat = statSync(audioFile);
    if (!stat.isFile() || stat.size < 1 || stat.size > 25 * 1024 * 1024) throw new Error('--audio-file must be a nonempty file under 25 MiB');
  }
  const expectText = args['--expect-text']?.trim();
  if (args['--expect-text'] !== undefined && (!audioFile || !expectText)) throw new Error('--expect-text requires --audio-file and nonempty expected text');
  return { baseUrl: url.href.replace(/\/+$/, ''), sourceDir: path.resolve(args['--source-dir']), outputDir: path.resolve(args['--output-dir']), timeoutMs, voice, text, audioFile, expectText };
}

function runBinary(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(`${command} failed: ${result.error?.message ?? result.stderr?.trim() ?? result.status}`);
  return result.stdout;
}

async function readBinaryResponse(response, limit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.byteLength;
    if (size > limit) throw new Error(`Audio exceeds ${limit} bytes`);
    chunks.push(Buffer.from(chunk));
  }
  assert.ok(size > 0, 'Endpoint returned empty audio');
  return Buffer.concat(chunks);
}

async function main(options) {
  if (typeof vm.SourceTextModule !== 'function') throw new Error('Start Node with --experimental-vm-modules');
  runBinary('ffprobe', ['-version']);
  runBinary('ffmpeg', ['-version']);
  mkdirSync(options.sourceDir, { recursive: true });
  mkdirSync(options.outputDir, { recursive: true });
  const sources = [];
  for (const spec of [...SOURCES, ...(options.audioFile ? STT_SOURCES : [])]) {
    const sourcePath = path.join(options.sourceDir, spec.filename);
    const upstreamUrl = `https://raw.githubusercontent.com/openclaw/openclaw/${REVISION}/${spec.upstream}`;
    if (!existsSync(sourcePath)) {
      const response = await fetch(upstreamUrl, { redirect: 'error', signal: AbortSignal.timeout(30000) });
      if (!response.ok) throw new Error(`Source download failed: ${response.status} ${upstreamUrl}`);
      const downloaded = Buffer.from(await response.arrayBuffer());
      assert.equal(hash(downloaded), spec.sha256, `Pinned source digest mismatch: ${spec.filename}`);
      writeFileSync(sourcePath, downloaded, { flag: 'wx' });
    }
    const bytes = readFileSync(sourcePath);
    assert.equal(hash(bytes), spec.sha256, `Pinned source digest mismatch: ${sourcePath}`);
    sources.push({ ...spec, upstreamUrl, sourcePath, source: bytes.toString('utf8') });
  }

  const calls = [];
  const forbidden = () => { throw new Error('Unexpected direct network or unimplemented SDK helper'); };
  const context = vm.createContext({ Buffer, URL, process: { env: {} }, fetch: forbidden });
  const modules = new Map();
  const asOptionalRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value) ? value : undefined;
  const normalizeOptionalString = (value) => typeof value === 'string' && value.trim() ? value.trim() : undefined;
  const sdkAdapters = {
    'openclaw/plugin-sdk/secret-input': { normalizeResolvedSecretInputString: ({ value }) => normalizeOptionalString(value) },
    'openclaw/plugin-sdk/speech-provider': { parseSpeechDirectiveNumberOverride: forbidden },
    'openclaw/plugin-sdk/string-coerce-runtime': {
      asOptionalRecord,
      asFiniteNumber: (value) => typeof value === 'number' && Number.isFinite(value) ? value : undefined,
      normalizeOptionalString,
      normalizeOptionalLowercaseString: (value) => normalizeOptionalString(value)?.toLowerCase(),
      normalizeLowercaseStringOrEmpty: (value) => normalizeOptionalString(value)?.toLowerCase() ?? '',
    },
    'openclaw/plugin-sdk/number-runtime': { resolveExpiresAtMsFromEpochSeconds: forbidden },
    'openclaw/plugin-sdk/provider-http': {
      providerOperationRetryConfig: forbidden,
      assertOkOrThrowProviderError: async (response) => {
        if (!response.ok) throw new Error(`LoudTalk HTTP ${response.status}: ${(await response.text()).slice(0, 2000)}`);
      },
      readProviderBinaryResponse: async (response, _errorLabel, _kind, { maxBytes }) => readBinaryResponse(response, maxBytes),
      resolveProviderRequestHeaders: ({ defaultHeaders }) => defaultHeaders,
    },
    'openclaw/plugin-sdk/proxy-capture': {
      captureHttpExchange: () => {},
      isDebugProxyGlobalFetchPatchInstalled: () => false,
    },
    'openclaw/plugin-sdk/ssrf-runtime': {
      // Test-only transport: exact URL allowlist. Does not execute OpenClaw SSRF helpers.
      ssrfPolicyFromHttpBaseUrlAllowedHostname: (baseUrl) => ({ testHostname: new URL(baseUrl).hostname }),
      fetchWithSsrFGuard: async (request) => {
        assert.equal(request.url, `${options.baseUrl}/audio/speech`, 'Unexpected endpoint');
        const call = { url: request.url, method: request.init.method, body: JSON.parse(request.init.body), timeoutMs: request.timeoutMs, released: false };
        calls.push(call);
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(new Error('TTS request timed out')), request.timeoutMs);
        try {
          const startedAt = performance.now();
          const response = await fetch(request.url, { ...request.init, redirect: 'error', signal: controller.signal });
          call.httpStatus = response.status;
          call.contentType = response.headers.get('content-type');
          return { response, release: async () => { clearTimeout(timer); call.released = true; call.elapsedMs = Math.round(performance.now() - startedAt); } };
        } catch (error) {
          clearTimeout(timer);
          throw error;
        }
      },
    },
    'openclaw/plugin-sdk/media-generation-runtime': { resolveGeneratedMediaMaxBytes: () => 16 * 1024 * 1024 },
    'openclaw/plugin-sdk/media-runtime': { isVoiceMessageCompatibleAudio: ({ fileName }) => fileName.endsWith('.opus') },
  };
  if (options.audioFile) {
    Object.assign(sdkAdapters, {
      'openclaw/plugin-sdk/provider-auth': {
        findNormalizedProviderValue: forbidden,
        hasConfiguredSecretInput: forbidden,
      },
      'openclaw/plugin-sdk/provider-auth-runtime': {
        collectProviderApiKeysForExecution: forbidden,
        executeWithApiKeyRotation: forbidden,
        isProviderAuthError: forbidden,
        requireApiKey: forbidden,
        resolveApiKeyForProvider: forbidden,
      },
      './base-url.js': { classifyOpenAIBaseUrl: forbidden, OPENAI_API_BASE_URL: 'https://api.openai.com/v1' },
      './default-models.js': { OPENAI_DEFAULT_AUDIO_TRANSCRIPTION_MODEL: 'gpt-4o-mini-transcribe' },
      './shared.js': {
        buildOpenAiCompatibleAuthHeaders: (params) => {
          assert.equal(params.apiKey, 'loudtalk-local');
          return { Authorization: `Bearer ${params.apiKey}` };
        },
        resolveProviderHttpRequestConfig: (params) => {
          assert.equal(params.baseUrl, options.baseUrl);
          assert.equal(params.provider, 'openai');
          assert.equal(params.api, 'openai-audio-transcriptions');
          return { baseUrl: params.baseUrl, allowPrivateNetwork: true, headers: params.defaultHeaders };
        },
        buildAudioTranscriptionFormData: ({ buffer, fileName, mime, fields }) => {
          const form = new FormData();
          form.append('file', new Blob([buffer], { type: mime }), fileName);
          for (const [key, value] of Object.entries(fields)) if (value !== undefined) form.append(key, String(value));
          return form;
        },
        postTranscriptionRequest: async (request) => {
          assert.equal(request.url, `${options.baseUrl}/audio/transcriptions`);
          assert.equal(request.fetchFn, fetch);
          const audio = request.body.get('file');
          const call = {
            url: request.url, method: 'POST', timeoutMs: request.timeoutMs, released: false,
            multipart: { model: request.body.get('model'), fileName: audio.name, mime: audio.type, bytes: audio.size },
          };
          calls.push(call);
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(new Error('STT request timed out')), request.timeoutMs);
          try {
            const startedAt = performance.now();
            const response = await request.fetchFn(request.url, {
              method: 'POST', headers: request.headers, body: request.body, redirect: 'error', signal: controller.signal,
            });
            call.httpStatus = response.status;
            call.contentType = response.headers.get('content-type');
            return { response, release: async () => { clearTimeout(timer); call.released = true; call.elapsedMs = Math.round(performance.now() - startedAt); } };
          } catch (error) {
            clearTimeout(timer);
            throw error;
          }
        },
        assertOkOrThrowHttpError: async (response) => {
          if (!response.ok) throw new Error(`LoudTalk STT HTTP ${response.status}: ${(await response.text()).slice(0, 2000)}`);
        },
        readProviderJsonObjectResponse: async (response) => {
          const payload = JSON.parse((await readBinaryResponse(response, 1024 * 1024)).toString('utf8'));
          if (!asOptionalRecord(payload)) throw new Error('Expected transcription JSON object');
          return payload;
        },
        requireTranscriptionText: (value, errorMessage) => {
          const text = normalizeOptionalString(value);
          if (!text) throw new Error(errorMessage);
          return text;
        },
      },
    });
  }
  for (const [identifier, exports] of Object.entries(sdkAdapters)) {
    modules.set(identifier, new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
    }, { context, identifier }));
  }
  async function linker(specifier) {
    if (specifier === 'openclaw/plugin-sdk/media-understanding') return modules.get('./openai-compatible-audio.js');
    assert.ok(modules.has(specifier), `Unexpected import: ${specifier}`);
    return modules.get(specifier);
  }
  for (const spec of sources) {
    modules.set(spec.identifier, new vm.SourceTextModule(stripTypeScriptTypes(spec.source), {
      context,
      identifier: spec.identifier,
      importModuleDynamically: async (specifier) => {
        const dependency = await linker(specifier);
        if (dependency.status === 'unlinked') await dependency.link(linker);
        if (dependency.status === 'linked') await dependency.evaluate();
        return dependency;
      },
    }));
  }
  const root = modules.get('./speech-provider.js');
  await root.link(linker);
  await root.evaluate();
  const provider = root.namespace.buildOpenAISpeechProvider();
  const config = provider.resolveConfig({ rawConfig: { providers: { openai: {
    apiKey: 'loudtalk-local', baseUrl: `${options.baseUrl}/`, model: 'loudkit', voice: options.voice, speed: 1,
  } } } });
  assert.equal(config.baseUrl, options.baseUrl);
  assert.equal(config.model, 'loudkit');
  assert.equal(config.voice, options.voice);
  assert.equal(provider.isConfigured({ providerConfig: config }), true);
  const tts = modules.get('./tts.js').namespace;
  assert.equal(tts.isValidOpenAIModel('loudkit', options.baseUrl), true);
  assert.equal(tts.isValidOpenAIVoice(options.voice, options.baseUrl), true);
  const sttModule = options.audioFile ? modules.get('./audio-transcription.js') : undefined;
  if (sttModule) {
    await sttModule.link(linker);
    await sttModule.evaluate();
  }

  const artifacts = [];
  for (const [target, codec, extension] of [['voice-note', 'opus', '.opus'], ['audio-file', 'mp3', '.mp3']]) {
    process.stderr.write(`OpenClaw ${target}: request real ${codec} from ${options.baseUrl}\n`);
    const result = await provider.synthesize({ providerConfig: config, target, text: options.text, timeoutMs: options.timeoutMs, cfg: {} });
    const call = calls.at(-1);
    assert.equal(call.httpStatus, 200);
    assert.equal(call.method, 'POST');
    assert.deepEqual(call.body, { model: 'loudkit', input: options.text, voice: options.voice, response_format: codec, speed: 1 });
    assert.equal(call.released, true);
    assert.equal(result.outputFormat, codec);
    assert.equal(result.fileExtension, extension);
    const outputPath = path.join(options.outputDir, `openclaw-${target}${extension}`);
    writeFileSync(outputPath, result.audioBuffer);
    const probe = JSON.parse(runBinary('ffprobe', ['-v', 'error', '-show_entries', 'format=duration:stream=codec_name,codec_type,sample_rate,channels,duration', '-of', 'json', outputPath]));
    const audioStreams = probe.streams.filter((stream) => stream.codec_type === 'audio');
    assert.equal(audioStreams.length, 1, 'Expected one audio stream');
    assert.equal(audioStreams[0].codec_name, codec);
    const durationSeconds = Number(probe.format?.duration ?? audioStreams[0].duration);
    assert.ok(Number.isFinite(durationSeconds) && durationSeconds > 0, 'Expected positive audio duration');
    runBinary('ffmpeg', ['-v', 'error', '-i', outputPath, '-f', 'null', '-']);
    artifacts.push({ target, path: outputPath, sha256: hash(result.audioBuffer), bytes: result.audioBuffer.length, codec, durationSeconds, sampleRate: Number(audioStreams[0].sample_rate), channels: audioStreams[0].channels, fullyDecoded: true });
  }
  let transcription;
  if (sttModule) {
    process.stderr.write(`OpenClaw transcription: upload ${options.audioFile} to local Parakeet\n`);
    const buffer = readFileSync(options.audioFile);
    const mimeByExtension = { '.ogg': 'audio/ogg', '.opus': 'audio/ogg', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4', '.mp4': 'audio/mp4', '.webm': 'audio/webm', '.flac': 'audio/flac' };
    const result = await sttModule.namespace.transcribeOpenAiAudio({
      buffer,
      fileName: path.basename(options.audioFile),
      mime: mimeByExtension[path.extname(options.audioFile).toLowerCase()] ?? 'application/octet-stream',
      apiKey: 'loudtalk-local',
      auth: { kind: 'api-key', apiKey: 'loudtalk-local' },
      baseUrl: options.baseUrl,
      model: 'parakeet',
      timeoutMs: options.timeoutMs,
      fetchFn: fetch,
    });
    const call = calls.at(-1);
    assert.equal(call.httpStatus, 200);
    assert.equal(call.multipart.model, 'parakeet');
    assert.equal(call.multipart.bytes, buffer.length);
    assert.equal(call.released, true);
    assert.equal(result.model, 'parakeet');
    assert.ok(typeof result.text === 'string' && result.text.trim());
    const normalizeTranscript = (text) => text.normalize('NFKC').toLocaleLowerCase('pl').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
    if (options.expectText) assert.equal(normalizeTranscript(result.text), normalizeTranscript(options.expectText), 'Transcription differs from expected words');
    const transcriptPath = path.join(options.outputDir, 'openclaw-transcript.txt');
    writeFileSync(transcriptPath, `${result.text}\n`);
    transcription = {
      inputPath: options.audioFile, inputSha256: hash(buffer), inputBytes: buffer.length,
      model: result.model, text: result.text, transcriptPath,
      expectedText: options.expectText ?? null,
      expectedWordsMatch: options.expectText ? true : null,
    };
  }
  const report = {
    passed: true,
    verifiedAt: new Date().toISOString(),
    upstreamRevision: REVISION,
    scope: 'Pinned upstream OpenClaw speech-provider, config normalizer and TTS request construction executed against real loopback LoudTalk HTTP; actual returned Opus/MP3 probed and fully decoded.' + (transcription ? ' Unchanged upstream transcribeOpenAiAudio and transcribeOpenAiCompatibleAudio called real local Parakeet via multipart HTTP and returned nonempty transcript.' : ''),
    exclusions: ['OpenClaw gateway/global configuration loading', 'OpenClaw SDK secret, header, SSRF and media helper implementations (test adapters)', ...(transcription ? ['STT shared multipart/auth/HTTP/JSON helper implementations and context-owned credential resolution (test adapters)'] : ['Speech transcription']), 'Messaging account/channel delivery', 'Human listening evaluation'],
    baseUrl: options.baseUrl,
    text: options.text,
    voice: options.voice,
    sourceDigests: sources.map(({ filename, sha256, upstreamUrl }) => ({ filename, sha256, upstreamUrl })),
    calls,
    artifacts,
    ...(transcription ? { transcription } : {}),
  };
  writeFileSync(path.join(options.outputDir, 'openclaw-verification.json'), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
}

if (process.argv.slice(2).includes('--help')) {
  console.log(HELP);
} else {
  try {
    await main(parseArgs(process.argv.slice(2)));
  } catch (error) {
    console.error(JSON.stringify({ passed: false, error: error.message }, null, 2));
    process.exitCode = 1;
  }
}
