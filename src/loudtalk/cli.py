"""Launch the app, expose MCP, or exchange voice messages from a terminal."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import webbrowser
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(prog="loudtalk", description="Głosówki dla Twoich agentów")
    parser.add_argument("--url", default=os.environ.get("LOUDTALK_URL", "http://127.0.0.1:8765"))
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Uruchom lokalny interfejs")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true")
    serve.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("LOUDTALK_DATA_DIR", Path.home() / ".loudtalk")),
    )
    sub.add_parser("mcp", help="Serwer narzędzi MCP przez stdio")
    mcp_http = sub.add_parser("mcp-http", help="MCP HTTP dla zewnętrznego proxy HTTPS")
    mcp_http.add_argument("--port", type=int, default=8766)
    sub.add_parser("doctor", help="Sprawdź instalację i dostępne aplikacje agentów")
    sub.add_parser("prepare", help="Pobierz i uruchom modele w działającej aplikacji")
    sub.add_parser("conversations", help="Lista agentów i rozmów")
    inbox = sub.add_parser("inbox", help="Odbierz wiadomości agenta")
    inbox.add_argument("agent_id")
    inbox.add_argument("--after", type=int, default=0)
    send = sub.add_parser("send", help="Wyślij użytkownikowi głosówkę od agenta")
    send.add_argument("agent_id")
    send.add_argument("text")
    send.add_argument("--conversation")
    send.add_argument(
        "--reply-to", type=int, help="ID odebranej głosówki (wymagane dla komunikatora)"
    )
    transcribe = sub.add_parser("transcribe", help="Rozpoznaj plik audio")
    transcribe.add_argument("file", type=Path)
    speak = sub.add_parser("speak", help="Wygeneruj plik mowy")
    speak.add_argument("text")
    speak.add_argument("--voice", default="gosia")
    args = parser.parse_args()
    if args.command == "serve":
        if (
            shutil.disk_usage(args.data_dir if args.data_dir.exists() else Path.home()).free
            < 20 * 1024**3
        ):
            parser.error(
                "Pozostało mniej niż 20 GB wolnego miejsca na dysku. Zwolnij miejsce przed uruchomieniem."
            )
        args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.environ.setdefault("HF_HOME", str(args.data_dir / "models"))
        import uvicorn

        from .server import create_app

        if args.open:
            threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
        # BlueBubbles authenticates its webhook with a query token. Do not log URLs.
        uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port, access_log=False)
        return
    if args.command == "mcp":
        from .mcp import run_stdio

        run_stdio(args.url)
        return
    if args.command == "mcp-http":
        from .mcp import run_http

        token = os.environ.get("LOUDTALK_MCP_TOKEN", "")
        if len(token) < 32:
            parser.error(
                "Ustaw LOUDTALK_MCP_TOKEN na losowy sekret o długości co najmniej 32 znaków."
            )
        run_http(args.url, port=args.port, token=token)
        return
    if args.command == "doctor":
        import platform

        from .dispatch import command_presets

        checks = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "apple_silicon": platform.system() == "Darwin" and platform.machine() == "arm64",
            "ffmpeg": shutil.which("ffmpeg"),
            "free_gb": round(shutil.disk_usage(Path.home()).free / 1024**3, 1),
            "agents": command_presets(),
        }
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return
    try:
        with httpx.Client(base_url=args.url.rstrip("/"), timeout=300) as client:
            if args.command == "prepare":
                response = client.post("/api/engines/prepare")
            elif args.command == "conversations":
                response = client.get("/api/bootstrap")
            elif args.command == "inbox":
                from urllib.parse import quote

                response = client.get(
                    f"/api/agents/{quote(args.agent_id, safe='')}/inbox",
                    params={"after_id": args.after},
                )
            elif args.command == "send":
                message = {
                    "agent_id": args.agent_id,
                    "text": args.text,
                    "conversation_id": args.conversation,
                }
                if args.reply_to is not None:
                    message["reply_to_message_id"] = args.reply_to
                response = client.post(
                    "/api/agent-messages",
                    json=message,
                )
            elif args.command == "transcribe":
                with args.file.open("rb") as file:
                    response = client.post(
                        "/api/transcribe", files={"file": (args.file.name, file)}
                    )
            else:
                response = client.post(
                    "/api/voice-preview", json={"text": args.text, "voice": args.voice}
                )
            response.raise_for_status()
            result = response.json()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if isinstance(result, dict) and result.get("status") == "error":
                raise SystemExit(1)
            if isinstance(result, dict) and result.get("delivery") is not None:
                delivery = result["delivery"]
                if not isinstance(delivery, dict) or delivery.get("status") != "sent":
                    print(
                        "Nie potwierdzono dostarczenia głosówki do komunikatora. "
                        "Sprawdź czat i aktywność LoudTalk przed ponowieniem; "
                        "odpowiedź mogła już dotrzeć.",
                        file=sys.stderr,
                    )
                    raise SystemExit(1)
    except httpx.ConnectError:
        parser.exit(1, "LoudTalk nie działa. Najpierw uruchom ./start.command.\n")
    except httpx.HTTPStatusError as exc:
        try:
            error = exc.response.json().get("detail", "Błąd serwera")
        except ValueError:
            error = "Błąd serwera"
        parser.exit(1, f"{error}\n")
    except (OSError, httpx.TimeoutException) as exc:
        parser.exit(1, f"Nie udało się wykonać polecenia: {exc}\n")


if __name__ == "__main__":
    main()
