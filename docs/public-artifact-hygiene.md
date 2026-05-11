# Public artifact hygiene guard

This repository ships a local pre-commit hook and a CI check that scan public-facing text for accidental leakage before it is published.

## What it checks

- escaped newline sequences where real line breaks were intended
- prompt / board / session metadata leakage
- local or machine-specific paths
- internal hostnames or private IPs
- internal email identities
- secret-like values and tokens

## Local setup

Install the hook once:

```bash
pre-commit install --install-hooks
```

Run it manually on the repo:

```bash
pre-commit run public-artifact-hygiene --all-files
```

## Before you publish text

Use the same guard on a draft PR body, comment, commit message, or release note:

```bash
printf '%s' "$DRAFT_TEXT" | python3 scripts/public_artifact_guard.py - --stdin-label "draft text"
```

## How to read failures

Each failure shows:

- the source file or text source
- the line number
- the rule that matched
- the offending line

Fix the text, then rerun the same command.

## CI behavior

GitHub Actions runs the same hook on tracked public artifacts and scans pull request, push, and release text when that metadata is available.
