#!/usr/bin/env python3
"""Scan public-facing repo artifacts for accidental leakage.

The guard is intentionally focused on publishable text artifacts such as
README/docs files, GitHub workflow/config files, and other human-readable
release material. It also inspects PR/release/commit text when running in
GitHub Actions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".json",
    ".toml",
}
SPECIAL_BASENAMES = {
    ".pre-commit-config.yaml",
    "README",
    "README.md",
    "README.rst",
    "CHANGELOG",
    "CHANGELOG.md",
    "SECURITY",
    "SECURITY.md",
    "CONTRIBUTING",
    "CONTRIBUTING.md",
}

SAFE_SECRET_HINTS = (
    "example",
    "placeholder",
    "replace",
    "replace-me",
    "dummy",
    "changeme",
    "change-me",
    "your-",
    "your_",
    "foobar",
    "password",
    "secret",
    "token",
)


@dataclass(frozen=True)
class Rule:
    rule_id: str
    pattern: re.Pattern[str]
    message: str


RULES: tuple[Rule, ...] = (
    Rule(
        "escaped-newline",
        re.compile(r"\\n"),
        "literal escaped \\n appears in public text; use real line breaks instead",
    ),
    Rule(
        "prompt-board-leak",
        re.compile(
            r"(?i)\b(?:"
            r"openclaw|planka|AGENTS\.md|HEARTBEAT\.md|"
            r"boardId|cardId|sessionId|notifyChannel|notifyTarget|"
            r"notifyReplyTo|notifyThreadId|reply_to|reply-to|subagent|heartbeat"
            r")\b"
        ),
        "prompt/board/session metadata should not appear in public artifacts",
    ),
    Rule(
        "local-path",
        re.compile(
            r"(?<!\w)(?:"
            r"/home/[^\s'\"`]+|"
            r"/Users/[^\s'\"`]+|"
            r"/tmp/[^\s'\"`]+|"
            r"/private/var/[^\s'\"`]+|"
            r"/var/folders/[^\s'\"`]+|"
            r"~/(?:[^\s'\"`]+)?|"
            r"C:\\[^\s'\"`]+|"
            r"\\\\[^\s'\"`]+"
            r")"
        ),
        "local or machine-specific paths should be replaced with neutral placeholders",
    ),
    Rule(
        "internal-host",
        re.compile(
            r"(?i)\b(?:"
            r"localhost|127\.0\.0\.1|10\.(?:\d{1,3}\.){2}\d{1,3}|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|"
            r"(?:[A-Za-z0-9-]+\.)+(?:corp|internal|lan|local)"
            r")\b"
        ),
        "internal hostnames/IPs should not be published in public artifacts",
    ),
    Rule(
        "internal-email",
        re.compile(r"(?i)\b[\w.+-]+@(?:ahu\.services|openclaw\.[\w.-]+)\b"),
        "internal email identities should not be published in public artifacts",
    ),
    Rule(
        "secret-like",
        re.compile(
            r"(?i)\b(?:"
            r"ghp_[A-Za-z0-9]{20,}|"
            r"github_pat_[A-Za-z0-9_]{20,}|"
            r"AKIA[0-9A-Z]{16}|"
            r"(?:xox[baprs]-[A-Za-z0-9-]{10,})|"
            r"(?:sk-[A-Za-z0-9]{16,})|"
            r"(?:password|secret|token|api[_-]?key|client_secret|private_key)\b\s*[:=]\s*[^\s'\"`]{8,}"
            r")",
        ),
        "secret-like values should be removed or replaced with placeholders",
    ),
)


@dataclass
class Finding:
    source: str
    line_no: int
    rule_id: str
    message: str
    excerpt: str


def is_public_artifact_path(path: str) -> bool:
    candidate = Path(path)
    name = candidate.name
    if name in SPECIAL_BASENAMES:
        return True
    if path.startswith("docs/"):
        return True
    if name.startswith("README") or name.startswith("CHANGELOG") or name.startswith("SECURITY") or name.startswith("CONTRIBUTING"):
        return True
    if candidate.suffix.lower() in TEXT_SUFFIXES:
        return True
    return False


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def scan_text(text: str, source: str) -> list[Finding]:
    findings: list[Finding] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        value_part = line
        if ":" in line or "=" in line:
            for separator in (":", "="):
                if separator in line:
                    value_part = line.split(separator, 1)[1].strip()
                    break
        value_lowered = value_part.lower()
        for rule in RULES:
            match = rule.pattern.search(line)
            if not match:
                continue
            if rule.rule_id == "secret-like" and any(hint in value_lowered for hint in SAFE_SECRET_HINTS):
                continue
            findings.append(
                Finding(
                    source=source,
                    line_no=idx,
                    rule_id=rule.rule_id,
                    message=rule.message,
                    excerpt=line.strip(),
                )
            )
    return findings


def git_output(args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout


def scan_files(paths: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    for raw_path in paths:
        if raw_path == "-":
            continue
        if not is_public_artifact_path(raw_path):
            continue
        text = read_text(Path(raw_path))
        if text is None:
            continue
        findings.extend(scan_text(text, raw_path))
    return findings


def github_metadata_texts() -> list[tuple[str, str]]:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        return []
    try:
        with open(event_path, "r", encoding="utf-8") as handle:
            event = json.load(handle)
    except OSError:
        return []
    except json.JSONDecodeError:
        return []

    texts: list[tuple[str, str]] = []
    event_name = os.getenv("GITHUB_EVENT_NAME", "")

    if event_name == "pull_request":
        pr = event.get("pull_request") or {}
        title = pr.get("title")
        body = pr.get("body")
        if title:
            texts.append(("github.pull_request.title", str(title)))
        if body:
            texts.append(("github.pull_request.body", str(body)))
        base_sha = (pr.get("base") or {}).get("sha")
        head_sha = (pr.get("head") or {}).get("sha")
        if base_sha and head_sha:
            commit_text = git_output(["log", "--format=%B", f"{base_sha}..{head_sha}"])
            if commit_text:
                texts.append(("github.pull_request.commits", commit_text))
            else:
                commit_text = git_output(["log", "-n", "20", "--format=%B", "HEAD"])
                if commit_text:
                    texts.append(("github.pull_request.commits", commit_text))
    elif event_name == "push":
        for commit in event.get("commits") or []:
            message = commit.get("message")
            if message:
                texts.append((f"github.push.commit.{commit.get('id', 'unknown')[:7]}", str(message)))
    elif event_name == "release":
        release = event.get("release") or {}
        name = release.get("name")
        body = release.get("body")
        tag = release.get("tag_name")
        if name:
            texts.append(("github.release.name", str(name)))
        if tag:
            texts.append(("github.release.tag", str(tag)))
        if body:
            texts.append(("github.release.body", str(body)))

    return texts


def scan_github_metadata() -> list[Finding]:
    findings: list[Finding] = []
    for source, text in github_metadata_texts():
        findings.extend(scan_text(text, source))
    return findings


def staged_files() -> list[str]:
    output = git_output(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    return [line for line in (output or "").splitlines() if line]


def tracked_files() -> list[str]:
    output = git_output(["ls-files"])
    return [line for line in (output or "").splitlines() if line]


def emit(findings: list[Finding]) -> int:
    if not findings:
        print("public-artifact-guard: ok")
        return 0

    print(f"public-artifact-guard: {len(findings)} issue(s) found", file=sys.stderr)
    for finding in findings:
        print(
            f"{finding.source}:{finding.line_no}: [{finding.rule_id}] {finding.message}\n"
            f"  {finding.excerpt}",
            file=sys.stderr,
        )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Files to scan. Use '-' to scan stdin.")
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Scan all tracked files in the repository.",
    )
    parser.add_argument(
        "--stdin-label",
        default="stdin",
        help="Label to use when scanning data from stdin.",
    )
    args = parser.parse_args()

    findings: list[Finding] = []

    if args.all_files:
        findings.extend(scan_files(tracked_files()))
    elif args.paths:
        if "-" in args.paths:
            stdin_text = sys.stdin.read()
            findings.extend(scan_text(stdin_text, args.stdin_label))
        findings.extend(scan_files([path for path in args.paths if path != "-"]))
    else:
        findings.extend(scan_files(staged_files()))

    findings.extend(scan_github_metadata())
    return emit(findings)


if __name__ == "__main__":
    raise SystemExit(main())
