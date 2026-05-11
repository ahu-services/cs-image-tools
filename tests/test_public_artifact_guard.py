from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "public_artifact_guard.py"
spec = importlib.util.spec_from_file_location("public_artifact_guard", MODULE_PATH)
assert spec and spec.loader
public_artifact_guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = public_artifact_guard
spec.loader.exec_module(public_artifact_guard)


def rule_ids(findings):
    return [finding.rule_id for finding in findings]


def test_scan_text_flags_public_artifact_leaks():
    findings = public_artifact_guard.scan_text(
        "boardId=123\npath=/home/alice/.ssh/id_rsa\nvalue=foo\\nbar",
        "draft.txt",
    )

    assert "prompt-board-leak" in rule_ids(findings)
    assert "local-path" in rule_ids(findings)
    assert "escaped-newline" in rule_ids(findings)


def test_scan_text_allows_placeholder_values():
    findings = public_artifact_guard.scan_text(
        "SVC_HOST=host.example.com\nsecret=foobar\npassword=password",
        "README.md",
    )

    assert findings == []


def test_scan_text_flags_secret_like_token():
    findings = public_artifact_guard.scan_text(
        "token=ghp_123456789012345678901234567890123456",
        "release-note.md",
    )

    assert "secret-like" in rule_ids(findings)
