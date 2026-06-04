# AGENTS

This repository accepts AI-assisted contributions.

## Guardrails
- Keep changes small and reviewable.
- Be careful with the entrypoint and Dockerfile; they control runtime configuration and credential handling.
- Do not commit secrets, repository credentials, or service credentials.
- Run `pre-commit run --all-files` (ruff, hadolint, shellcheck, yamllint) before publishing PR text, commits, or docs.

## Required Human Checks
- Review every AI-generated change before merge.
- Validate networking/RMI callback logic and ImageMagick policy handling.
- Ensure docs (env vars, tool versions, Java matrix) match the implementation.

## Attribution
A concise note such as "AI-assisted" in the PR description is recommended for transparency.
