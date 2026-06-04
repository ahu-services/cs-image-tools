# Contributing

Thanks for contributing to cs-image-tools.

## Requirements
- Docker
- Python 3.x (for the entrypoint and local checks)
- `pre-commit`

## Development Setup
```bash
pip install pre-commit
pre-commit install --install-hooks
pre-commit run --all-files
docker build -t cs-image-tools:dev .
```

The `hadolint` hook runs in a container. Without a local Docker daemon, skip it:

```bash
SKIP=hadolint-docker pre-commit run --all-files
```

## Branch and Commit Guidelines
- Create a feature branch from `main`.
- Use Conventional Commits, e.g.:
  - `feat: add ICC profile auto-copy`
  - `fix: correct RMI callback host detection`
  - `docs: document VOLUMES_INFO usage`

## Pull Request Checklist
- Keep changes focused and minimal.
- Update docs (env vars, tool versions) when behavior changes.
- Run `pre-commit run --all-files` before opening a PR.
- Ensure the image builds and CI is green.

## Security and Secrets
- Never commit repository credentials (`REPO_USER`/`REPO_PASS`), service credentials, or ICC/licensing material.
- For vulnerabilities, follow `SECURITY.md`.

## Review Policy
- All PRs require human review before merge.
- AI-assisted changes are welcome, but maintainers are responsible for final correctness.
