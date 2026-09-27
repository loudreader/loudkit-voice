# Voice messages with LoudTalk

Use these instructions in an agent's existing rules or instructions file. Replace
`YOUR_AGENT_ID` with the identifier shown for that agent in LoudTalk.

You can exchange voice messages with the user through LoudTalk. Your LoudTalk
agent ID is `YOUR_AGENT_ID`.

1. Call `list_conversations` to discover conversations and their identifiers.
2. Call `receive_voice_messages` with your agent ID. Pass the last successfully
   handled message ID as `after_id` on subsequent reads. Keep this cursor across
   turns so you do not answer the same message twice.
3. Treat each returned user transcript as a message from the user. An unclear
   transcription needs a short clarification, especially before consequential work.
   Respect the user's existing permissions and your normal approval policy.
4. Answer in the user's language. Use concise spoken sentences, without reading
   Markdown formatting, long URLs, or code aloud.
5. Call `send_voice_message` with your agent ID, answer `text`, and the exact
   `conversation_id` returned by the inbox. Also pass the incoming message's `id`
   as `reply_to_message_id`; it selects the correct voice note and prevents
   duplicate delivery. For a messenger reply, only `delivery.status: sent`
   confirms delivery. An error does not mean the reply was delivered.
6. Advance the cursor only after handling the message. If sending times out with
   an unknown outcome, inspect the conversation before retrying. Keep the same
   `reply_to_message_id` on any retry; do not create a new unrelated reply.

For a separate audio file, use `transcribe_audio(path)`. To create speech without
sending a conversation reply, use `synthesize_speech(text, voice)`.

Receiving a message does not itself launch an agent. Use these tools during an
active agent turn or a user-authorized worker/routine. When LoudTalk launches your
CLI and says it will speak the final answer, return final answer text and do not
also call `send_voice_message`: the host handles delivery for that run.
