"""Safety and evidence classification tests; these do not establish agent compatibility."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "verify_agent_commands.py"
spec = importlib.util.spec_from_file_location("verify_agent_commands", SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def test_probe_extracts_version_without_account_or_path():
    diagnostic = "token=private-credential\n/user/private/location\ncodex-cli 0.153.0\n"
    assert verify.version_string(diagnostic) == "0.153.0"
    assert verify.version_string("account person@example.com, key abcdef") is None
    assert verify.version_string("2.1.268 (Claude Code)") == "2.1.268"


def test_probe_bounds_process_lifetime_and_output(tmp_path):
    timed = verify.invoke(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, timeout=0.05
    )
    assert timed["status"] == "timeout"
    verbose = verify.invoke(
        [sys.executable, "-c", "print('x' * 300000)"], cwd=tmp_path, timeout=2
    )
    assert verbose["status"] == "output_limit"
    assert verbose["output"] == ""


def test_uninstalled_agent_is_never_claimed_tested(monkeypatch, tmp_path):
    monkeypatch.setattr(verify.shutil, "which", lambda executable: None)
    result = verify.inspect_agent("muse", tmp_path, 1, True)
    assert result["installed"] is False
    assert result["smoke"]["status"] == "not_installed"


def test_failed_auth_evidence_excludes_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "invoke", lambda *args, **kwargs: {
        "status": "failed", "returncode": 1,
        "output": '{"loggedIn":false,"email":"private@example.com","token":"private-secret"}',
    })
    result = verify.auth_check("claude", "claude", tmp_path, 1)
    assert result == {"status": "authentication_required"}
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("text, expected", [
    ("token=private-secret EPERM writing state", "filesystem_permission"),
    ("OAuth session expired; private@example.com", "authentication_required"),
    ("ProviderModelNotFoundError private-model", "model_unavailable"),
    ("IneligibleTierError reasonCode UNSUPPORTED_CLIENT", "unsupported_client"),
])
def test_diagnostics_keep_only_fixed_categories(text, expected):
    assert verify.diagnostic_category(text) == expected
