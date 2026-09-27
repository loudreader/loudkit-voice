# Connect your agent

LoudTalk exposes one local MCP server: `loudtalk mcp`. Agents receive the text
transcription of a voice message and send an answer that LoudTalk turns into
speech. The same five tools work across MCP clients:

| Tool | Purpose |
| --- | --- |
| `list_conversations` | Find conversation IDs. |
| `receive_voice_messages(agent_id, after_id)` | Read messages addressed to an agent. |
| `send_voice_message(agent_id, text, conversation_id, reply_to_message_id)` | Reply to the exact incoming voice note in its original messenger. |
| `transcribe_audio(path)` | Transcribe a local audio file. |
| `synthesize_speech(text, voice)` | Create an audio file without sending it. |

MCP adds tools to an agent; it does not start a stopped agent. For immediate
answers in a connected messenger, use a configured local CLI or HTTP agent connection.
Use MCP when the agent should check its inbox and send voice notes during its own
work. Add the [voice instructions](agent-instructions.md) to that agent's rules.

## Quick setup

1. Install and start LoudTalk using the [project instructions](../README.md).
2. Create an agent in LoudTalk and keep its ID for the voice instructions.
3. Merge the matching example below into the agent's existing configuration.
   Do not overwrite unrelated settings. If a desktop agent cannot resolve
   `loudtalk` on its PATH, use the absolute path to `.venv/bin/loudtalk` from your
   LoudTalk installation instead of the command name.
4. Restart or reconnect the MCP server in the agent. Confirm its tool list
   includes the five tools above, then ask it to check its LoudTalk inbox.

These files are examples, not installers. They do not edit personal settings,
enable blanket tool approval, sign in, or install agent software. First speech
inference may take longer while a model loads; clients with a short default tool
timeout may need it raised to 180 seconds.

## Agent matrix

Documentation checked on 2026-09-22. “Documented” means the vendor documents the
integration surface; it does not claim an end-to-end test with that agent and
your account. This is an extensible compatibility list, not a claim to cover every
agent product or a ranking by popularity.

| Agent | Documented integration | Configuration / qualification |
| --- | --- | --- |
| Hermes Agent | Local stdio MCP; headless CLI; HTTP API | Merge [hermes.yaml](hermes.yaml) into `~/.hermes/config.yaml`. [MCP docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp), [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server). |
| OpenClaw | MCP registry and Gateway CLI | Run `openclaw mcp add loudtalk --command loudtalk --arg mcp`, then `openclaw mcp probe loudtalk`. Tool availability depends on the configured runtime. [Registry docs](https://docs.openclaw.ai/cli/mcp/registry). Older Clawdbot/Moltbot installs must be checked against their installed version. |
| Claude Code | Local stdio MCP; headless CLI | `claude mcp add --transport stdio loudtalk -- loudtalk mcp`, or merge [mcp.json](mcp.json) into project `.mcp.json`. [MCP docs](https://code.claude.com/docs/en/mcp). |
| Codex | Local stdio MCP; CLI and App Server | `codex mcp add loudtalk -- loudtalk mcp`, or merge [codex.toml](codex.toml) into `~/.codex/config.toml`. [Official MCP docs](https://learn.chatgpt.com/docs/extend/mcp?surface=cli). |
| OpenCode | Local stdio MCP; headless CLI and HTTP server | Merge [opencode.json](opencode.json) into `opencode.json`. [MCP docs](https://opencode.ai/docs/mcp-servers/). |
| Gemini CLI | Local stdio MCP; headless CLI | `gemini mcp add loudtalk loudtalk mcp`, or merge [mcp.json](mcp.json) into `.gemini/settings.json`. [MCP docs](https://geminicli.com/docs/tools/mcp-server/). |
| Cursor | Local stdio MCP in editor and CLI | Merge [mcp.json](mcp.json) into `.cursor/mcp.json`. [Configuration](https://docs.cursor.com/context/model-context-protocol), [CLI MCP](https://cursor.com/docs/cli/mcp). |
| Cline | Local stdio MCP | Merge [mcp.json](mcp.json) using MCP Servers → Configure, or CLI `~/.cline/mcp.json`. [Official docs source](https://github.com/cline/cline/blob/main/docs/mcp/mcp-overview.mdx). |
| Roo Code | Local stdio MCP | Merge [mcp.json](mcp.json) into `.roo/mcp.json`. [MCP docs](https://roocodeinc.github.io/Roo-Code/features/mcp/using-mcp-in-roo/). |
| Goose | Local stdio MCP extensions | Merge [goose.yaml](goose.yaml) into `~/.config/goose/config.yaml`, or add a Standard IO custom extension with command `loudtalk mcp`. [Official extensions docs](https://github.com/aaif-goose/goose/blob/main/documentation/docs/getting-started/using-extensions.md). |
| Muse Code (Meta) | Local stdio MCP; headless CLI; SDK | Merge [muse-code.json](muse-code.json) into trusted project `.mcp.json`. This uses the current canonical schema. [MCP SDK docs](https://meta-models.github.io/muse-code-sdk/next/guides/extend/mcp-servers/). |
| GitHub Copilot CLI | Local stdio MCP | Merge [copilot.json](copilot.json) into `~/.copilot/mcp-config.json`, or run `copilot mcp add loudtalk -- loudtalk mcp`. [GitHub docs](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers). |
| Continue | MCP tools | Add a local MCP server with command `loudtalk`, args `["mcp"]` through its MCP configuration. [MCP docs](https://docs.continue.dev/customize/mcp-tools). |
| xAI Grok API | HTTP API; remote MCP | A local API bridge can send transcripts and synthesize returned text. xAI's remote MCP transport requires a reachable HTTP server; the local stdio example cannot be pasted into the API. [Remote MCP docs](https://docs.x.ai/developers/tools/remote-mcp). |
| Grok Bot | Skills, authorized local commands, own routines | Separate product from Grok API. No documented custom STT/TTS or public existing-Bot dispatch API was found. The [Grok Bot guide](../docs/grokbot.md) and [skill](grokbot/loudtalk-messenger-voice/SKILL.md) use its own context through a local inbox; full Bot execution requires account validation. [Official voice docs](https://docs.x.ai/grok-bot/chat-and-collaboration). |
| Muse personal agent (Meta) | WhatsApp; custom API connectors | Separate product from Muse Code. Meta documents creating Custom Connectors for a service API; a LoudTalk connection still requires account, authentication and network tests. No custom STT/TTS setting or existing-agent dispatch API was confirmed. [Official introduction](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/), [Custom Connectors](https://www.meta.com/help/artificial-intelligence/1687253048996149/). |
| Other agents | MCP, HTTP, or local command | Use the standard MCP example when the client supports local stdio. Otherwise use a documented HTTP/command adapter. Verify the exact product and transport before claiming it is connected. |

“Muse” also names unrelated products such as MuseHub. Select the exact product;
MCP server support in one product does not prove MCP client support in another.

## Headless command references

These are integration references for installed, authenticated agents. Pass the
prompt as one argument with a process API, or use the documented stdin/file
option. Do not interpolate transcripts into shell commands. Keep each agent's
existing permission policy; approval-required work may pause or fail in a
headless run and should remain visible to the user.

| Agent | Invocation | Read the reply from |
| --- | --- | --- |
| Hermes | `hermes chat --oneshot --quiet -q PROMPT` (the implemented local preset) | Final text on stdout; inspect the process exit code. [CLI reference](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md). |
| Claude Code | `claude -p --output-format json PROMPT` | JSON `result`, after checking process and result errors. [Headless docs](https://code.claude.com/docs/en/headless). |
| Codex | `codex exec PROMPT` | Final assistant text on stdout; progress is on stderr. [Non-interactive docs](https://learn.chatgpt.com/docs/non-interactive-mode). |
| OpenCode | `opencode run --format json PROMPT` | JSONL `type=text` → `part.text`; treat `type=error` as failure. [CLI docs](https://opencode.ai/docs/cli/), [output implementation](https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/opencode/src/cli/cmd/run.ts). |
| Gemini CLI | `gemini -p PROMPT --output-format json` | JSON `response`; inspect `error` and exit status. [Headless docs](https://geminicli.com/docs/cli/headless/). |
| OpenClaw | `openclaw agent --session-key loudtalk:CONVERSATION_ID --message PROMPT --json` | `payloads[].text` or Gateway `result.payloads[].text`; inspect status/errors. Uses configured Gateway and an explicit LoudTalk session. Omit `--deliver` so the host controls delivery. [Agent CLI docs](https://docs.openclaw.ai/cli/agent). |
| Muse Code | `muse exec PROMPT` (the implemented local preset) | Agent output on stdout; inspect exit status. Structured `--json` is available for a version-specific JSONL adapter. [Automation docs](https://dev.meta.ai/docs/muse-code/extending). |

The CLI runs above can execute the user's requested work with the tools already
authorized in that agent. They are not read-only chat model calls. For OpenClaw,
use the Gateway command shown rather than `agent exec`, whose documented default
execution policy differs. A transport timeout can have an unknown outcome; do
not automatically start the same task again.

## Verify one connection

Send a short voice message in LoudTalk. In the intended agent, ask it to read
its inbox and answer in the same conversation. Check all three outcomes:

1. The agent's tool inventory contains LoudTalk's tools.
2. The agent reads the intended transcript and conversation ID.
3. The reply appears in that conversation and its audio plays.

A green MCP connection indicator proves only the protocol connection. It does
not prove speech recognition, synthesis, correct routing, or automatic wake-up.
