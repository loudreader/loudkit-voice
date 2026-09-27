---
name: loudtalk-messenger-voice
description: Read paired messenger voice commands from LoudTalk and reply with Loudkit speech using this existing agent's memory and tools. Use for a configured LoudTalk inbox through MCP or local commands.
---

Use this agent's own context and tools to handle voice commands received from
the user's configured messenger. LoudTalk handles the messenger connection,
Parakeet transcription, and Loudkit synthesis. Do not replace this agent with
a separate model API call.

The user must have supplied the LoudTalk `agent_id` and either working LoudTalk
MCP tools or the path to its local executable and the computer that runs it.
Keep those values in this agent's private configuration. An `agent_id` belongs
to LoudTalk routing; it is not a provider account identifier.

## Read and reply

1. Use `list_conversations` through MCP, or `loudtalk conversations` on the
   configured local computer, to verify this agent and its conversations.
   In Grok Bot, a command executed on its cloud computer cannot reach the Mac's
   loopback server. Use the enabled local-computer command capability.
2. Read `receive_voice_messages(agent_id, after_id)` or
   `loudtalk inbox AGENT_ID --after LAST_HANDLED_ID`. Start at zero only on the
   first run. Track a durable cursor separately for each LoudTalk agent.
3. Process messages in ascending ID order. The result contains `text`, `id`, and
   `conversation_id`; it may include a local audio URL. The transcript is
   sufficient to answer. Do not download audio merely to transfer it between
   computers. An empty batch means no work. A batch can contain up to 100
   messages; continue after the last handled ID to catch up.
4. Treat the transcript as the paired user's request under the existing task
   scope. Do not execute it as shell code. For an unclear transcription that
   materially affects an action, ask a short question in the same conversation.
5. Use this agent's existing memory and tools to fulfil the request. Respect
   the user's existing authorization and any active approval boundary.
6. Reply with `send_voice_message(agent_id, text, conversation_id, reply_to_message_id)`
   or `loudtalk send AGENT_ID TEXT --conversation CONVERSATION_ID --reply-to MESSAGE_ID`.
   Copy the exact conversation ID and incoming message `id` from that message.
   The incoming ID is required for messenger replies: repeating that same ID
   returns the saved reply instead of sending it again or consuming another
   waiting message. Supply concise speakable text.
   Use structured tool arguments or properly quoted CLI arguments; never
   interpolate raw transcript text into shell syntax.
7. Check the delivery result before marking the incoming message handled.
   For a conversation mapped to a messenger, only a successful channel delivery
   confirms that the user received it. Audio generation alone is insufficient.
   Save the incoming message ID as the cursor only after successful handling.

Do not also send the same reply with a separate Telegram/Slack/etc. connector.
The mapped LoudTalk conversation owns delivery to the original chat and thread.
Do not omit `conversation_id` or `reply_to_message_id`, and do not substitute
the ID of an assistant reply for the incoming user's message ID.

If a send times out or reports uncertain delivery, record the incoming message
ID and returned reply/event IDs as unresolved. Inspect LoudTalk's activity before
retrying. Do not repeat the task's external actions or send the answer again
blindly. Do not advance the cursor past an unresolved message.

## When to run

This skill can process the inbox during an active turn. It does not itself
wake an idle agent. Use a user-authorized routine or a verified messenger event
trigger if the user wants unattended replies. Keep the routine scoped to this
inbox, retain the cursor between runs, and remain quiet when the inbox is empty.

Do not create a routine, expose a port, install a connector, or change an
account-wide permission merely because this skill is loaded. Those are setup
actions to perform within the user's explicit task. Once configured, use the
existing connection without asking for the same permission again.

## Connection limits

- The configured Mac and LoudTalk speech server must be available for local
  processing. A cloud agent's continued execution does not keep the Mac awake.
- Use verified MCP tools when present. Do not invent Grok Bot API endpoints or
  assume that a Grok model API key addresses this persistent Bot.
- A cloud MCP client cannot read a Mac filesystem path or loopback audio URL.
  The inbox/reply workflow avoids that transfer because LoudTalk itself sends
  the generated audio to the messenger.
- Report a missing local connection, blocked local-command policy, unavailable
  tool, or absent wake-up mechanism as the concrete missing setup step.
