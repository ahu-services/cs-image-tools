# Security Policy

## Supported Versions
The `main` branch is the only actively supported line at the moment.

## Reporting a Vulnerability
Please do not open public issues for security vulnerabilities.

Report privately to the maintainer and include:
- affected component (Dockerfile, entrypoint, bundled tool)
- impact and attack scenario
- reproduction steps
- suggested mitigation if available

You will receive an acknowledgement as soon as possible, and we will coordinate remediation and disclosure timing.

## Hardening Notes
- Never bake repository or service credentials into images or commit them to the repo.
- Prefer passing secrets at runtime via environment variables or a secrets manager, not build args, for published images.
- Run the container behind appropriate network controls; host networking exposes RMI callback ports.
- Keep the base image and bundled media tools (ImageMagick, FFmpeg, Ghostscript, etc.) up to date.
