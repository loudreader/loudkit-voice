# Loudkit Voice

**Your agent. Your everyday buddy.**

Give your existing agent a local voice with [Loudkit](https://github.com/loudreader/loudkit)
text-to-speech and Parakeet speech recognition. Send a voice note, let your agent
use its own memory and tools, and hear its reply in the same conversation.

**Open source, Apache-2.0, free to self-host. No access request or Loudkit account.**
Your chosen agent/model and messenger providers may have their own costs.

[Product page](https://loudkit.loudreader.io/agents/) ·
[Agent setup instructions](https://loudkit.loudreader.io/agents/llms.txt) ·
[Integration reference](integrations/README.md)

## Install on a Mac

This developer preview runs speech locally on **macOS with Apple Silicon**.
You need Python 3.12–3.13, `uv`, `ffmpeg`, and at least **20 GB free disk space**.
The first model preparation downloads about 3 GB. The source does not include
recordings, credentials, or model weights.

```sh
brew install uv ffmpeg
git clone https://github.com/loudreader/loudkit-voice.git
cd loudkit-voice
./start.command
```

The launcher installs pinned public dependencies and opens
**http://127.0.0.1:8765**. It does not need a checkout of Loudkit next to it.
The runtime command and Python package retain the name **`loudtalk`** for
compatibility. The setup panel and some detailed guides currently use Polish;
this README and the agent instructions are in English.

Keep the server running. In another terminal, from the same directory:

```sh
.venv/bin/loudtalk doctor
.venv/bin/loudtalk prepare
```

`prepare` loads the real speech models. Readiness is reported by
`GET http://127.0.0.1:8765/api/bootstrap`: both speech engines must report
`state: ready` under `engine.tts` and `engine.stt`. An open page alone does not mean the models are ready.

Try a local reply without connecting any messenger:

```sh
curl --fail http://127.0.0.1:8765/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"loudkit","input":"Hello. Your agent now has a voice.","voice":"sophie","response_format":"wav"}' \
  --output hello.wav
afplay hello.wav
```

There are 28 voices across 10 languages. Speech runs on your Mac; your agent's
model and messenger connections may still use online services.

## Keep your existing Hermes or OpenClaw bot

When the agent already owns its messenger connection, configure its STT/TTS
provider to use **`http://127.0.0.1:8765/v1`**. Keep the existing bot, memory,
permissions, and model provider. Do not start a second Telegram receiver with
the same bot token.

- [Hermes provider configuration](integrations/native/hermes.yaml): merge the
  STT/TTS sections into the active profile; use `/voice on` in Telegram or Discord.
- [OpenClaw provider configuration](integrations/native/openclaw.json): merge
  with the existing configuration and use `tts.auto: inbound`. Audio uses its own
  `openai:loudtalk` authentication profile; retain any OpenAI chat credentials.
- [Detailed provider setup and test evidence](integrations/native/README.md).

Set the voice to `sophie`, `oscar`, or another included voice. The example local
key `loudtalk-local` is a placeholder required by clients, not an OpenAI API key.

`127.0.0.1` always means the machine running the agent. A remote agent needs a
private connection or tunnel to the Mac; it cannot use its own localhost to
reach this service. Do not expose the unauthenticated UI or audio API publicly.

## Connect another agent or messenger

The setup panel supports installed agent commands, an existing agent API or
webhook, or an MCP inbox. MCP adds tools; it does **not** wake a stopped agent.
For automatic replies, use a running agent integration or its own scheduling.

The existing messenger adapters are:

| Messenger | Required connection | Reply |
| --- | --- | --- |
| Telegram | BotFather bot | OGG/Opus voice note |
| Discord | Bot and Gateway | OGG/Opus voice note |
| WhatsApp | Business Cloud API and HTTPS webhook | OGG/Opus voice note |
| Slack | Bot app and HTTPS webhook | MP3 in the original thread |
| iMessage | BlueBubbles on a Mac with the agent account | M4A; native voice note needs Private API |

Pair the intended sender and chat in the panel before sending a new command.
Unapproved senders cannot trigger agent work. Private WhatsApp QR sessions are
not supported by this adapter.

To connect an MCP client, use the **absolute path** to this checkout's
`.venv/bin/loudtalk` with the argument `mcp`. See
[integration examples](integrations/README.md) and
[voice reply rules](integrations/agent-instructions.md). The MCP tools are
`list_conversations`, `receive_voice_messages`, `send_voice_message`,
`transcribe_audio`, and `synthesize_speech`.

```json
{
  "mcpServers": {
    "loudkit-voice": {
      "command": "/absolute/path/to/loudkit-voice/.venv/bin/loudtalk",
      "args": ["mcp"]
    }
  }
}
```

Install or adapt the [Loudkit Voice skill](skills/loudkit-voice/SKILL.md) in your
agent's skills directory. It explains how to select the native provider route
or MCP route and verify the first round trip. The skill does not bypass agent
approvals or configure accounts on its own.

## What has been tested

The standalone release passed 338 automated protocol and routing tests with
public PyPI dependencies. A fresh install of Loudkit 0.1.1 generated real Sophie
audio, and Parakeet transcribed it back to the exact English input; see the
[public dependency smoke result](docs/evidence/public-install-smoke.json).
Real Loudkit and Parakeet audio were checked through the pinned upstream Hermes
and OpenClaw provider code. A real Codex run also completed the voice pipeline
with a **mocked Telegram network transport**.

These checks do not prove delivery on every live messenger account. Grok Bot
and Muse integrations need validation in those products. We do not claim every
agent is connected because it supports MCP.

[Verification scope](docs/verification.md) ·
[Agent compatibility](docs/agent-compatibility.md) ·
[API reference](docs/api.md)

## Local data and permissions

The launcher keeps recordings, models, and credentials in `.loudtalk/`, which
is excluded from Git. The service binds to loopback. Credentials are not sent
back to the browser, but are not additionally encrypted in the local database.
Use the agent's existing permission policy; never disable it to enable voice.

For messenger webhooks, expose only `/hooks/` through your own HTTPS proxy and
set the upstream Host to `127.0.0.1:8765`. Retain webhook verification. Do not
publish the setup panel, local audio API, recordings, or connection secrets.

## Development

```sh
uv sync --locked --extra dev --extra discord
.venv/bin/pytest -q tests integrations/grokbot/test_transport.py
.venv/bin/ruff check src tests integrations
```

Tests use controlled messenger transports. Real speech tests download models
unless they are already cached; see `tests/smoke_speech.py` and `scripts/`.

## License

Code is licensed under [Apache-2.0](LICENSE). Dependencies and model weights are
separately distributed under their own licenses; see [NOTICE](NOTICE).
