#!/usr/bin/env python3
"""Inspect installed CLIs and optionally run a harmless reply through LoudTalk.

Does not install software, log in, modify agent settings, or print credentials.
Use --smoke to make model calls with each agent's already configured account.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = {
    "codex": {"help": ["exec", "--help"], "flags": ["exec", "[PROMPT]"]},
    "claude": {"help": ["--help"], "flags": ["--print", "--output-format"]},
    "gemini": {"help": ["--help"], "flags": ["--prompt", "--output-format"]},
    "opencode": {"help": ["run", "--help"], "flags": ["--format", "message"]},
    "muse": {"help": ["exec", "--help"], "flags": ["exec", "--prompt-file"]},
}
EXPECTED = "LOUDTALK_AGENT_OK"
PROMPT = (
    "Odpowiedz dokładnie LOUDTALK_AGENT_OK. Nie używaj żadnych narzędzi, nie otwieraj plików, "
    "nie uruchamiaj poleceń, nie wysyłaj wiadomości i niczego nie zmieniaj."
)


def diagnostic_category(value: str) -> str:
    """Return fixed categories, never raw CLI diagnostics or account identifiers."""
    value = value.lower()
    markers = (
        ("filesystem_permission", ("eacces", "eperm", "operation not permitted", "permission denied")),
        ("unsupported_client", ("unsupported_client", "ineligibletiererror", "client is no longer supported")),
        ("authentication_required", (
            "not logged in", "oauth session expired", "authentication_failed", "token expired",
            "please run /login", "sesja agenta wygasła", "loggedin\":false",
        )),
        ("model_unavailable", ("model not found", "model is not available", "modelnotfound")),
        ("repository_required", ("not inside a trusted directory", "not a git repository")),
        ("provider_not_configured", ("no credentials", "no provider", "api key is missing")),
    )
    for category, terms in markers:
        if any(term in value for term in terms):
            return category
    return "command_failed"


def invoke(argv: list[str], *, cwd: Path, timeout: float) -> dict:
    """Bound lifetime/output and kill the whole child process group on timeout."""
    # A temporary file prevents a misbehaving CLI from exhausting parent memory.
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                argv, cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
            )
        except OSError:
            return {"status": "launch_failed", "returncode": None, "output": ""}
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if os.fstat(output.fileno()).st_size > 262144:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    output.seek(0)
                    return {"status": "output_limit", "returncode": None, "output": ""}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    process.wait(timeout=min(remaining, 0.05))
                except subprocess.TimeoutExpired:
                    continue
            code = process.returncode
            status = "ok" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            code, status = None, "timeout"
        output.seek(0)
        if os.fstat(output.fileno()).st_size > 262144:
            return {"status": "output_limit", "returncode": code, "output": ""}
        raw = output.read(262144)
        return {"status": status, "returncode": code, "output": raw.decode(errors="replace")}


def version_string(output: str) -> str | None:
    """Extract only a semantic version, excluding banners, paths, and identities."""
    for line in output.splitlines():
        match = re.fullmatch(
            r"\s*(?:codex-cli\s+)?v?(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)"
            r"(?:\s+\([^\n]+\))?\s*", line,
        )
        if match:
            return match.group(1)
    return None


def auth_check(name: str, executable: str, cwd: Path, timeout: float) -> dict:
    commands = {
        "codex": ["login", "status"],
        "claude": ["auth", "status", "--json"],
        "opencode": ["providers", "list"],
    }
    if name not in commands:
        return {"status": "not_inspected"}
    result = invoke([executable, *commands[name]], cwd=cwd, timeout=timeout)
    if name == "claude":
        try:
            payload = json.loads(result["output"])
            if payload.get("loggedIn") is False:
                return {"status": "authentication_required"}
            if payload.get("loggedIn") is True:
                return {"status": "configured"}
        except (ValueError, AttributeError):
            pass
    if result["status"] != "ok":
        return {
            "status": result["status"] if result["status"] != "failed"
            else diagnostic_category(result["output"]),
            "returncode": result["returncode"],
        }
    if name == "codex" and "logged in" in result["output"].lower():
        return {"status": "configured"}
    if name == "opencode":
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result["output"])
        if re.search(r"\b[1-9]\d* credentials?\b", plain, re.IGNORECASE):
            return {"status": "configured"}
        if re.search(r"\b0 credentials?\b", plain, re.IGNORECASE):
            return {"status": "authentication_required"}
    return {"status": "not_established"}


async def smoke_reply(name: str, directory: Path, timeout: float) -> dict:
    sys.path.insert(0, str(ROOT / "src"))
    from loudtalk import dispatch

    previous_timeout = dispatch.COMMAND_TIMEOUT_SECONDS
    dispatch.COMMAND_TIMEOUT_SECONDS = timeout
    try:
        reply = await dispatch.generate_reply(
            {"kind": "command", "command_id": name, "working_directory": str(directory)},
            [{"role": "user", "content": PROMPT}],
            f"compatibility-{name}",
        )
        matched = reply.strip() == EXPECTED
        return {
            "status": "passed" if matched else "unexpected_reply",
            "expected_reply": EXPECTED,
            "matched": matched,
            "reply_sha256": hashlib.sha256(reply.encode()).hexdigest(),
            "through_loudtalk_dispatch": True,
        }
    except ValueError as error:
        category = diagnostic_category(str(error))
        if "na czas" in str(error):
            category = "timeout"
        return {"status": category, "through_loudtalk_dispatch": True}
    finally:
        dispatch.COMMAND_TIMEOUT_SECONDS = previous_timeout


def inspect_agent(name: str, directory: Path, timeout: float, smoke: bool) -> dict:
    executable = shutil.which(name)
    record: dict = {"agent_id": name, "installed": bool(executable)}
    if not executable:
        record.update({"status": "not_installed", "smoke": {"status": "not_installed"}})
        return record
    version = invoke([executable, "--version"], cwd=directory, timeout=timeout)
    record["version"] = version_string(version["output"])
    record["version_probe"] = {"status": version["status"], "returncode": version["returncode"]}
    if version["status"] != "ok":
        record["status"] = "version_probe_failed"
        record["diagnostic"] = diagnostic_category(version["output"])
        record["smoke"] = {"status": "skipped_unhealthy_executable"}
        return record
    spec = AGENTS[name]
    help_result = invoke([executable, *spec["help"]], cwd=directory, timeout=timeout)
    found = {flag: flag in help_result["output"] for flag in spec["flags"]}
    record["interface"] = {
        "status": "documented_flags_present" if help_result["status"] == "ok" and all(found.values())
        else "not_verified",
        "probe_argv": [name, *spec["help"]],
        "required_flags_present": found,
        "returncode": help_result["returncode"],
    }
    record["auth"] = auth_check(name, executable, directory, timeout)
    record["status"] = "interface_checked"
    if not smoke:
        record["smoke"] = {"status": "not_requested"}
    elif record["interface"]["status"] != "documented_flags_present":
        record["smoke"] = {"status": "skipped_unverified_interface"}
    elif record["auth"]["status"] == "authentication_required":
        record["smoke"] = {"status": "authentication_required"}
    elif name in {"codex", "claude", "opencode"} and record["auth"]["status"] != "configured":
        record["smoke"] = {"status": "skipped_auth_not_established"}
    else:
        record["smoke"] = asyncio.run(smoke_reply(name, directory, max(30, timeout)))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", nargs="+", choices=AGENTS, default=list(AGENTS))
    parser.add_argument("--smoke", action="store_true", help="Run one no-tools model reply per ready agent")
    parser.add_argument("--timeout", type=float, default=45, help="Seconds per bounded CLI command")
    parser.add_argument("--output", type=Path, help="Write sanitized JSON evidence here")
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 180:
        parser.error("--timeout must be between 0 and 180 seconds")
    if shutil.disk_usage(ROOT).free < 20 * 1024**3:
        parser.error("Free disk is below 20 GB; stop and free space before retrying.")
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "mode": "reply_smoke" if args.smoke else "interface_only",
        "scope": "Installed CLI interface/auth and optional text reply; no messenger accounts or audio",
        "agents": [],
    }
    with tempfile.TemporaryDirectory(prefix="loudtalk-agent-check-") as temporary:
        directory = Path(temporary)
        # Codex normally requires a Git work folder. This only creates an empty
        # temporary repository and preserves the real adapter's standard flags.
        subprocess.run(["git", "init", "--quiet", str(directory)], check=True)
        for name in args.agents:
            record = inspect_agent(name, directory, args.timeout, args.smoke)
            report["agents"].append(record)
            print(f"{name}: {record['smoke']['status']}", file=sys.stderr, flush=True)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
