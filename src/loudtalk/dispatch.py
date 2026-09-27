"""Small, bounded adapters from a voice conversation to a real agent reply.

Credentials and provider diagnostics must never enter user-facing errors. Local
commands use fixed argument lists and inherit each agent's normal permissions.
"""

# ruff: noqa: TRY004 -- ValueError is the server's uniform, user-safe error contract.

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_REPLY_CHARS = 12_000
MAX_CONVERSATION_CHARS = 64_000
MAX_OUTPUT_BYTES = 1_048_576
HTTP_TIMEOUT_SECONDS = 120
COMMAND_TIMEOUT_SECONDS = 180

_VOICE_INSTRUCTION = (
    "To jest rozmowa głosowa. Odpowiadaj w języku użytkownika, zwięźle i naturalnie, "
    "tak żeby odpowiedź dobrze brzmiała po przeczytaniu na głos. "
    "Unikaj tabel i długich bloków kodu, chyba że użytkownik o nie poprosi. "
    "Odpowiedz na ostatnią wiadomość użytkownika, uwzględniając całą rozmowę poniżej."
)


@dataclass(frozen=True)
class _CommandPreset:
    name: str
    executable: str
    arguments: tuple[str, ...]
    output: str = "text"


_COMMAND_PRESETS = {
    "hermes": _CommandPreset(
        "Hermes", "hermes", ("chat", "--oneshot", "--quiet", "-q", "{prompt}")
    ),
    "claude": _CommandPreset(
        "Claude Code", "claude", ("-p", "--output-format", "json", "{prompt}"), "claude"
    ),
    "codex": _CommandPreset("Codex", "codex", ("exec", "{prompt}")),
    "gemini": _CommandPreset(
        "Gemini CLI", "gemini", ("-p", "{prompt}", "--output-format", "json"), "gemini"
    ),
    "muse": _CommandPreset("Muse Code", "muse", ("exec", "{prompt}")),
    "opencode": _CommandPreset(
        "OpenCode", "opencode", ("run", "--format", "json", "--", "{prompt}"), "opencode"
    ),
    "openclaw": _CommandPreset(
        "OpenClaw",
        "openclaw",
        ("agent", "--session-key", "{session_key}", "--message", "{prompt}", "--json"),
        "openclaw",
    ),
}


def command_presets() -> list[dict[str, Any]]:
    """Return only curated commands; executable availability is checked live."""
    return [
        {"id": key, "name": preset.name, "installed": shutil.which(preset.executable) is not None}
        for key, preset in _COMMAND_PRESETS.items()
    ]


def _messages(messages: list[dict]) -> list[dict[str, str]]:
    if not isinstance(messages, list) or not messages:
        raise ValueError("Rozmowa jest pusta. Nagraj lub napisz wiadomość.")
    normalized = []
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Nie udało się odczytać wiadomości w rozmowie.")
        role, content = message.get("role"), message.get("content")
        if role not in ("system", "user", "assistant") or not isinstance(content, str):
            raise ValueError("Nie udało się odczytać wiadomości w rozmowie.")
        if not content.strip():
            raise ValueError("Wiadomość jest pusta. Nagraj lub napisz ją ponownie.")
        total += len(content)
        if total > MAX_CONVERSATION_CHARS:
            raise ValueError("Ta rozmowa jest już za długa. Rozpocznij nową rozmowę z agentem.")
        normalized.append({"role": role, "content": content})
    if not any(message["role"] == "user" for message in normalized):
        raise ValueError("Rozmowa nie zawiera jeszcze wiadomości użytkownika.")
    return normalized


def _reply(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Agent nie zwrócił odpowiedzi tekstowej. Spróbuj ponownie.")
    value = value.strip()
    if len(value) > MAX_REPLY_CHARS:
        raise ValueError("Odpowiedź agenta jest za długa. Poproś o krótszą odpowiedź.")
    return value


def _endpoint(value: Any, *, openai: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Uzupełnij adres połączenia z agentem.")
    try:
        url = urlsplit(value.strip())
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError
        # Accessing port also rejects malformed ports before httpx sees them.
        _ = url.port
        if url.fragment:
            raise ValueError
        path = url.path.rstrip("/")
        if openai and not path.endswith("/chat/completions"):
            path = (path or "/v1") + "/chat/completions"
        return urlunsplit((url.scheme, url.netloc, path or "/", url.query, ""))
    except (ValueError, TypeError):
        raise ValueError("Podaj poprawny adres HTTP lub HTTPS do połączenia z agentem.") from None


async def _request_json(endpoint: str, body: dict, headers: dict[str, str]) -> dict:
    try:
        async with (
            httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client,
            client.stream("POST", endpoint, json=body, headers=headers) as response,
        ):
            if response.status_code in (401, 403):
                raise ValueError("Agent odmówił dostępu. Sprawdź klucz i uprawnienia połączenia.")
            if response.status_code == 429:
                raise ValueError("Agent osiągnął limit zapytań. Spróbuj ponownie za chwilę.")
            if 300 <= response.status_code < 400:
                raise ValueError("Adres agenta przekierowuje żądanie. Ustaw bezpośredni adres API.")
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_OUTPUT_BYTES:
                    raise ValueError(
                        "Agent zwrócił zbyt dużą odpowiedź. Poproś o krótszą odpowiedź."
                    )
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Agent zwrócił odpowiedź w nieobsługiwanym formacie.")
        return payload
    except httpx.TimeoutException:
        raise ValueError("Agent nie odpowiedział na czas. Spróbuj ponownie.") from None
    except (httpx.HTTPError, httpx.InvalidURL):
        raise ValueError(
            "Nie udało się połączyć z agentem. Sprawdź adres i dostępność API."
        ) from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Agent zwrócił odpowiedź w nieobsługiwanym formacie.") from None


def _prompt(messages: list[dict[str, str]]) -> str:
    # Include actual conversation content; do not silently discard older turns.
    turns = "\n\n".join(f"--- {item['role']} ---\n{item['content']}" for item in messages)
    return f"{_VOICE_INSTRUCTION}\n\n{turns}\n\n--- assistant ---\n"


def _parse_command_output(output: str, parser: str) -> str:
    if parser == "text":
        return _reply(output)
    try:
        if parser == "opencode":
            parts = []
            for line in output.splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("type") == "error":
                    raise ValueError
                if event.get("type") == "text":
                    part = event.get("part")
                    if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                        raise ValueError
                    parts.append(part["text"])
            return _reply("\n\n".join(parts))
        payload = json.loads(output)
        if parser == "claude" and isinstance(payload, list):
            # Claude Code versions may return the whole event sequence instead
            # of a single result. Only the final result is the spoken answer.
            payload = next(
                (
                    event
                    for event in reversed(payload)
                    if isinstance(event, dict) and event.get("type") == "result"
                ),
                None,
            )
        if not isinstance(payload, dict) or payload.get("error") or payload.get("is_error"):
            raise ValueError
        if parser == "claude":
            return _reply(payload.get("result"))
        if parser == "gemini":
            return _reply(payload.get("response"))
        if parser == "openclaw":
            result = payload.get("result", payload)
            if not isinstance(result, dict):
                raise ValueError
            parts = result.get("payloads")
            if not isinstance(parts, list):
                raise ValueError
            return _reply(
                "\n\n".join(
                    part["text"]
                    for part in parts
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
            )
        raise ValueError
    except (ValueError, TypeError, KeyError):
        # JSON and native agent diagnostics may contain secrets or local paths.
        raise ValueError(
            "Nie udało się odczytać odpowiedzi agenta. Sprawdź jego konfigurację."
        ) from None


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    # Kill the group even when its original parent already exited: children may
    # still hold pipes open, and must not survive timeout/cancellation.
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    # Pipes can be paused at their buffer limit. Drain after cancelling the
    # readers so wait() cannot hang even after a noisy process has been killed.
    await process.communicate()


async def _run_command(agent: dict, messages: list[dict[str, str]], conversation_id: str) -> str:
    preset = _COMMAND_PRESETS.get(agent.get("command_id"))
    if preset is None:
        raise ValueError("Wybierz obsługiwany program agenta.")
    executable = shutil.which(preset.executable)
    if executable is None:
        raise ValueError("Program agenta nie jest zainstalowany lub nie jest dostępny w PATH.")
    raw_directory = agent.get("working_directory") or str(Path.home())
    try:
        directory = Path(raw_directory).expanduser()
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError
    except (OSError, TypeError, ValueError):
        raise ValueError("Wybierz istniejący folder roboczy, podając jego pełną ścieżkę.") from None
    if not isinstance(conversation_id, str) or not conversation_id or len(conversation_id) > 200:
        raise ValueError("Nie udało się rozpoznać rozmowy. Rozpocznij nową rozmowę.")
    substitutions = {"{prompt}": _prompt(messages), "{session_key}": f"loudtalk:{conversation_id}"}
    arguments = [substitutions.get(argument, argument) for argument in preset.arguments]
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            cwd=str(directory),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        raise ValueError(
            "Nie udało się uruchomić programu agenta. Sprawdź instalację i folder roboczy."
        ) from None

    output_size = 0

    async def read_output(stream: asyncio.StreamReader) -> bytes:
        nonlocal output_size
        data = bytearray()
        while chunk := await stream.read(65_536):
            output_size += len(chunk)
            if output_size > MAX_OUTPUT_BYTES:
                raise ValueError(
                    "Program agenta zwrócił zbyt dużo danych. Spróbuj z krótszą wiadomością."
                )
            data.extend(chunk)
        return bytes(data)

    stdout_task = asyncio.create_task(read_output(process.stdout))
    stderr_task = asyncio.create_task(read_output(process.stderr))
    wait_task = asyncio.create_task(process.wait())
    tasks = (stdout_task, stderr_task, wait_task)
    try:
        stdout, stderr, returncode = await asyncio.wait_for(
            asyncio.gather(*tasks), timeout=COMMAND_TIMEOUT_SECONDS
        )
    except BaseException as error:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _stop_process(process)
        if isinstance(error, TimeoutError):
            raise ValueError("Program agenta nie odpowiedział na czas. Spróbuj ponownie.") from None
        raise
    if returncode != 0:
        # Both streams are already bounded. Match known auth diagnostics without
        # returning any provider text, which can contain credentials or URLs.
        diagnostics = (stdout + b"\n" + stderr).lower()
        if any(
            marker in diagnostics
            for marker in (
                b"oauth session expired",
                b"authentication_failed",
                b"authentication failed",
                b"token has expired",
                b"token expired",
                b"refresh token expired",
                b"not logged in",
                b"please run /login",
            )
        ):
            raise ValueError(
                f"Sesja agenta wygasła. Zaloguj się ponownie w {preset.name} i spróbuj jeszcze raz."
            )
        raise ValueError(
            "Program agenta zakończył się błędem. Sprawdź logowanie, uprawnienia i folder roboczy "
            "w jego terminalu, a następnie spróbuj ponownie."
        )
    return _parse_command_output(stdout.decode("utf-8", errors="replace"), preset.output)


async def generate_reply(agent: dict, messages: list[dict], conversation_id: str) -> str:
    """Get a real text reply, or raise a safe Polish ``ValueError``.

    ``inbox`` is deliberately excluded: the server owns its asynchronous queue.
    No backend retries automatically, since requests may execute agent actions.
    """
    if not isinstance(agent, dict):
        raise ValueError("Wybierz agenta, z którym chcesz rozmawiać.")
    kind = agent.get("kind")
    if kind == "inbox":
        raise ValueError("Wiadomości skrzynki agenta obsługuje kolejka rozmowy.")
    if kind not in ("openai", "webhook", "command"):
        raise ValueError("Wybierz obsługiwany sposób połączenia z agentem.")
    normalized = _messages(messages)
    if kind == "command":
        return await _run_command(agent, normalized, conversation_id)

    endpoint = _endpoint(agent.get("endpoint"), openai=kind == "openai")
    headers = {"Accept": "application/json"}
    api_key = agent.get("api_key")
    if api_key:
        if not isinstance(api_key, str) or "\r" in api_key or "\n" in api_key:
            raise ValueError("Klucz dostępu ma niepoprawny format. Wklej go ponownie.")
        headers["Authorization"] = f"Bearer {api_key}"
    if kind == "webhook":
        payload = await _request_json(
            endpoint,
            {
                "conversation_id": conversation_id,
                "agent_id": agent.get("id"),
                "messages": normalized,
                "text": next(
                    item["content"] for item in reversed(normalized) if item["role"] == "user"
                ),
            },
            headers,
        )
        return _reply(payload.get("text"))

    model = agent.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Uzupełnij nazwę modelu dla tego połączenia.")
    payload = await _request_json(
        endpoint,
        {
            "model": model.strip(),
            "messages": [{"role": "system", "content": _VOICE_INSTRUCTION}, *normalized],
            "stream": False,
        },
        headers,
    )
    try:
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "\n".join(
                part["text"]
                for part in content
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            )
    except (KeyError, IndexError, TypeError):
        raise ValueError("Agent zwrócił odpowiedź w nieobsługiwanym formacie.") from None
    return _reply(content)
