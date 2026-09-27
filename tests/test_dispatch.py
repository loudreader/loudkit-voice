import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from loudtalk import dispatch

MESSAGES = [
    {"role": "user", "content": "Mam na imię Ola.", "id": "ignored"},
    {"role": "assistant", "content": "Cześć, Olu!"},
    {"role": "user", "content": "Jak mam na imię?"},
]


@pytest.fixture
def http_mock(monkeypatch):
    original = httpx.AsyncClient
    calls = []

    def install(handler):
        def receive(request):
            calls.append(request)
            return handler(request)

        def make_client(**kwargs):
            assert kwargs["timeout"] == 120
            assert kwargs["follow_redirects"] is False
            return original(transport=httpx.MockTransport(receive), **kwargs)

        monkeypatch.setattr(dispatch.httpx, "AsyncClient", make_client)
        return calls

    return install


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("https://agent.test", "/v1/chat/completions"),
        ("https://agent.test/v1/", "/v1/chat/completions"),
        ("https://agent.test/v1/chat/completions", "/v1/chat/completions"),
        ("https://agent.test/openai/v1", "/openai/v1/chat/completions"),
    ],
)
async def test_openai_preserves_conversation_and_normalizes_endpoint(http_mock, endpoint, expected):
    calls = http_mock(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": " Masz na imię Ola. "}}]}
        )
    )
    reply = await dispatch.generate_reply(
        {"kind": "openai", "endpoint": endpoint, "model": "local-model", "api_key": "secret-token"},
        MESSAGES,
        "conversation-1",
    )
    assert reply == "Masz na imię Ola."
    assert len(calls) == 1
    assert calls[0].url.path == expected
    assert calls[0].headers["Authorization"] == "Bearer secret-token"
    payload = json.loads(calls[0].content)
    assert payload["model"] == "local-model"
    assert payload["stream"] is False
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1:] == [
        {"role": item["role"], "content": item["content"]} for item in MESSAGES
    ]


async def test_webhook_contract_and_no_automatic_retry(http_mock):
    calls = http_mock(lambda request: httpx.Response(200, json={"text": "Ola."}))
    assert (
        await dispatch.generate_reply(
            {"id": "my-agent", "kind": "webhook", "endpoint": "http://localhost:9000/reply"},
            MESSAGES,
            "conversation-2",
        )
        == "Ola."
    )
    assert len(calls) == 1
    assert "Authorization" not in calls[0].headers
    assert json.loads(calls[0].content) == {
        "conversation_id": "conversation-2",
        "agent_id": "my-agent",
        "messages": [{"role": item["role"], "content": item["content"]} for item in MESSAGES],
        "text": "Jak mam na imię?",
    }


@pytest.mark.parametrize("status", [301, 307, 401, 403, 429, 500])
async def test_http_errors_are_safe_and_redirects_not_followed(http_mock, status):
    secret = "SECRET-must-not-leak"
    calls = http_mock(
        lambda request: httpx.Response(
            status, text=secret, headers={"Location": "https://other.test/" + secret}
        )
    )
    with pytest.raises(ValueError) as error:
        await dispatch.generate_reply(
            {"kind": "webhook", "endpoint": "https://agent.test/" + secret, "api_key": secret},
            MESSAGES,
            "conversation-1",
        )
    assert secret not in str(error.value)
    assert "https://" not in str(error.value)
    assert len(calls) == 1


async def test_network_exception_is_safe(http_mock):
    def timeout(request):
        raise httpx.ReadTimeout("secret-token https://private.test", request=request)

    http_mock(timeout)
    with pytest.raises(ValueError, match="nie odpowiedział na czas") as error:
        await dispatch.generate_reply(
            {"kind": "webhook", "endpoint": "https://private.test"}, MESSAGES, "c"
        )
    assert "private" not in str(error.value)
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"text": " \n "}),
        httpx.Response(200, json={"text": "x" * 12_001}),
    ],
)
async def test_webhook_rejects_invalid_or_unusable_replies(http_mock, response):
    http_mock(lambda request: response)
    with pytest.raises(ValueError):
        await dispatch.generate_reply(
            {"kind": "webhook", "endpoint": "https://agent.test"}, MESSAGES, "c"
        )


async def test_http_response_is_bounded_before_json_decoding(http_mock, monkeypatch):
    monkeypatch.setattr(dispatch, "MAX_OUTPUT_BYTES", 128)
    http_mock(lambda request: httpx.Response(200, content=b"x" * 129))
    with pytest.raises(ValueError, match="zbyt dużą"):
        await dispatch.generate_reply(
            {"kind": "webhook", "endpoint": "https://agent.test"}, MESSAGES, "c"
        )


@pytest.mark.parametrize(
    "agent,messages",
    [
        ({"kind": "inbox"}, MESSAGES),
        ({"kind": "unknown"}, MESSAGES),
        ({"kind": "openai", "endpoint": "https://agent.test"}, MESSAGES),
        ({"kind": "webhook", "endpoint": "file:///etc/passwd"}, MESSAGES),
        ({"kind": "webhook", "endpoint": "https://user:pass@agent.test"}, MESSAGES),
        ({"kind": "webhook", "endpoint": "https://agent.test"}, []),
        (
            {"kind": "webhook", "endpoint": "https://agent.test"},
            [{"role": "user", "content": "x" * 64_001}],
        ),
    ],
)
async def test_invalid_configuration_never_sends_request(http_mock, agent, messages):
    calls = http_mock(lambda request: pytest.fail("invalid configuration made a network call"))
    with pytest.raises(ValueError):
        await dispatch.generate_reply(agent, messages, "c")
    assert calls == []


@pytest.fixture
def command_fixture(tmp_path, monkeypatch):
    def install(source, parser="text"):
        script = tmp_path / "agent_fixture.py"
        script.write_text(source, encoding="utf-8")
        monkeypatch.setitem(
            dispatch._COMMAND_PRESETS,
            "fixture",
            dispatch._CommandPreset(
                "Fixture agent", sys.executable, (str(script), "{prompt}"), parser
            ),
        )
        return {"kind": "command", "command_id": "fixture", "working_directory": str(tmp_path)}

    return install


async def test_command_receives_literal_prompt_and_entire_conversation(command_fixture, tmp_path):
    agent = command_fixture(
        "import json, os, sys\nprint(json.dumps({'prompt': sys.argv[1], 'cwd': os.getcwd()}))\n"
    )
    marker = tmp_path / "must-not-exist"
    messages = [*MESSAGES, {"role": "user", "content": f"$(touch {marker}); `touch {marker}`"}]
    result = json.loads(await dispatch.generate_reply(agent, messages, "c"))
    assert Path(result["cwd"]).resolve() == tmp_path.resolve()
    for message in messages:
        assert message["content"] in result["prompt"]
    assert not marker.exists()


@pytest.mark.parametrize(
    "parser,payload,expected",
    [
        ("claude", {"result": "Odpowiedź Claude", "is_error": False}, "Odpowiedź Claude"),
        ("gemini", {"response": "Odpowiedź Gemini"}, "Odpowiedź Gemini"),
        ("openclaw", {"payloads": [{"text": "Odpowiedź"}, {"mediaUrl": "ignored"}]}, "Odpowiedź"),
        (
            "openclaw",
            {"result": {"payloads": [{"text": "Pierwsza"}, {"text": "Druga"}]}},
            "Pierwsza\n\nDruga",
        ),
    ],
)
async def test_structured_command_response(command_fixture, parser, payload, expected):
    agent = command_fixture(f"print({json.dumps(payload)!r})\n", parser)
    assert await dispatch.generate_reply(agent, MESSAGES, "c") == expected


async def test_claude_event_array_uses_last_result(command_fixture):
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": "Wcześniejszy tekst"}},
        {"type": "result", "result": "Wcześniejszy wynik", "is_error": False},
        {"type": "result", "result": "Końcowa odpowiedź", "is_error": False},
    ]
    agent = command_fixture(f"print({json.dumps(events)!r})\n", "claude")
    assert await dispatch.generate_reply(agent, MESSAGES, "c") == "Końcowa odpowiedź"


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "result", "result": "SECRET diagnostic", "is_error": True}],
        [{"type": "result", "result": "SECRET diagnostic", "error": "failed"}],
        [{"type": "assistant", "message": {"content": "SECRET partial reply"}}],
        [],
    ],
)
async def test_claude_event_array_rejects_errors_or_missing_result(command_fixture, events):
    agent = command_fixture(f"print({json.dumps(events)!r})\n", "claude")
    with pytest.raises(ValueError) as error:
        await dispatch.generate_reply(agent, MESSAGES, "c")
    assert "SECRET" not in str(error.value)


async def test_opencode_events(command_fixture):
    events = [
        {"type": "step_start", "part": {}},
        {"type": "text", "part": {"text": "Odpowiedź"}},
        {"type": "step_finish", "part": {"reason": "stop"}},
    ]
    agent = command_fixture(
        "\n".join(f"print({json.dumps(event)!r})" for event in events), "opencode"
    )
    assert await dispatch.generate_reply(agent, MESSAGES, "c") == "Odpowiedź"


@pytest.mark.parametrize(
    "parser,output",
    [
        ("claude", '{"result":"SECRET diagnostic","is_error":true}'),
        ("gemini", '{"error":{"message":"SECRET diagnostic"}}'),
        (
            "opencode",
            '{"type":"text","part":{"text":"Partial reply"}}\n{"type":"error","error":"SECRET diagnostic"}',
        ),
        ("openclaw", '{"result":{}}'),
        ("text", ""),
    ],
)
async def test_command_errors_do_not_become_fake_replies(command_fixture, parser, output):
    agent = command_fixture(f"print({output!r})\n", parser)
    with pytest.raises(ValueError) as error:
        await dispatch.generate_reply(agent, MESSAGES, "c")
    assert "SECRET" not in str(error.value)


async def test_failed_command_diagnostics_are_private(command_fixture):
    agent = command_fixture(
        "import sys\nprint('SECRET', file=sys.stderr)\nprint('partial reply')\nsys.exit(1)\n"
    )
    with pytest.raises(ValueError, match="zakończył się błędem") as error:
        await dispatch.generate_reply(agent, MESSAGES, "c")
    assert "SECRET" not in str(error.value)
    assert "partial reply" not in str(error.value)


@pytest.mark.parametrize(
    "stream,diagnostic",
    [
        ("stdout", "OAuth session expired"),
        ("stderr", "authentication_failed"),
        ("stderr", "Token has expired"),
    ],
)
async def test_expired_command_auth_is_actionable_and_private(command_fixture, stream, diagnostic):
    output = f"{diagnostic}: SECRET-token https://private.test/login"
    agent = command_fixture(f"import sys\nprint({output!r}, file=sys.{stream})\nsys.exit(1)\n")
    with pytest.raises(ValueError, match="Sesja agenta wygasła") as error:
        await dispatch.generate_reply(agent, MESSAGES, "c")
    message = str(error.value)
    assert "Zaloguj się ponownie w Fixture agent" in message
    assert "SECRET" not in message
    assert "private.test" not in message
    assert diagnostic not in message


async def test_command_output_limit_does_not_deadlock(command_fixture, monkeypatch):
    monkeypatch.setattr(dispatch, "MAX_OUTPUT_BYTES", 8192)
    agent = command_fixture("import os\nwhile True: os.write(1, b'x' * 65536)\n")
    with pytest.raises(ValueError, match="zbyt dużo danych"):
        await asyncio.wait_for(dispatch.generate_reply(agent, MESSAGES, "c"), timeout=5)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name != "posix", reason="Process group assertions use POSIX signals")
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_stop_child_processes(
    command_fixture, tmp_path, monkeypatch, cancel
):
    pid_file = tmp_path / "pids.json"
    agent = command_fixture(
        "import json, os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(json.dumps([os.getpid(), child.pid]))\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(dispatch, "COMMAND_TIMEOUT_SECONDS", 1)
    task = asyncio.create_task(dispatch.generate_reply(agent, MESSAGES, "c"))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists(), "fixture process never started"
    pids = json.loads(pid_file.read_text())
    assert all(_alive(pid) for pid in pids)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
    else:
        with pytest.raises(ValueError, match="nie odpowiedział na czas"):
            await asyncio.wait_for(task, timeout=5)
    for _ in range(100):
        if not any(_alive(pid) for pid in pids):
            break
        await asyncio.sleep(0.01)
    assert not any(_alive(pid) for pid in pids)


async def test_command_rejects_relative_working_directory(command_fixture):
    agent = command_fixture("print('should not run')\n")
    agent["working_directory"] = "relative/path"
    with pytest.raises(ValueError, match="pełną ścieżkę"):
        await dispatch.generate_reply(agent, MESSAGES, "c")


def test_command_availability_is_checked_live(monkeypatch):
    monkeypatch.setattr(
        dispatch.shutil,
        "which",
        lambda executable: "/bin/claude" if executable == "claude" else None,
    )
    presets = {item["id"]: item for item in dispatch.command_presets()}
    assert presets["claude"]["installed"] is True
    assert presets["codex"]["installed"] is False
    assert {"hermes", "claude", "codex", "gemini", "muse", "opencode", "openclaw"} <= presets.keys()
