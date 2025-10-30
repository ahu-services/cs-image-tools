# censhare-Service-Client Docker Configuration

This repository provides a Docker image and entrypoint to run the censhare-Service-Client with a consistent, containerized setup. It supports dynamic configuration at runtime or pre-configuration during image build.

## Overview

- Runs censhare-Service-Client in a container with common media tooling
- Dynamic or pre-installed Service-Client (via `REPO_USER`, `REPO_PASS`, `VERSION`)
- Flexible configuration via environment variables and JSON volume config
- Graceful shutdown handling for data integrity
- Automatic Java runtime selection (Corretto 11/17/21) matching Service-Client version

## Prerequisites

- Docker installed: see https://docs.docker.com/get-docker/

## Quick Start

Build the base image:

```bash
docker build -t cs-image-tools:v1.0 .
```

Run and download Service-Client at startup:

```bash
docker run -d --name csclient1 \
  -e REPO_USER=repo_user -e REPO_PASS=repo_password -e VERSION=2023.1.1 \
  -e SVC_USER=user -e SVC_PASS=password -e SVC_HOST=host.example.com \
  cs-image-tools:v1.0
```

Pre-install the Service-Client at build time and run:

```bash
# Build with Service-Client baked in
docker build \
  --build-arg REPO_USER=youruser \
  --build-arg REPO_PASS=yourpass \
  --build-arg VERSION=2023.1.1 \
  -t cs-service-client:2023.1.1 .

# Run the pre-installed image (pass only connection settings)
docker run -d --name csclient1 \
  -e SVC_USER=user -e SVC_PASS=password -e SVC_HOST=host.example.com \
  cs-service-client:2023.1.1
```

Note: If an `iccprofiles` folder exists in the build context, it is copied into the image. On container start its contents are copied to `/opt/corpus/censhare/censhare-Service-Client/config/iccprofile`.

## Configuration

### Build-time variables

- `REPO_USER`: Username for the repository hosting the Service-Client. Required when pre-installing during build.
- `REPO_PASS`: Password for the repository. Used with `REPO_USER`.
- `VERSION`: Version of the Service-Client to download and install.

### Runtime variables

- `SVC_USER`: Username for the Service-Client connection/config.
- `SVC_PASS`: Password for the Service-Client.
- `SVC_HOST`: Hostname or IP of the censhare server to connect to.
- `SVC_INSTANCES`: Number of parallel worker instances. Default `4`.
- `OFFICE_URL`: URL of an office conversion service. If unset or unreachable, office previews are disabled.
- `OFFICE_VALIDATE_CERTS`: Validate SSL certificates for `OFFICE_URL`. Set to `false` to disable validation.
- `VOLUMES_INFO`: JSON string to fully replace the `volumes` section in `hosts.xml`.
- `REPO_USER`, `REPO_PASS`, `VERSION`: May also be provided at runtime to download/configure the Service-Client if not pre-installed.

### Tool-specific timeouts

Override processing timeouts with `<TOOLNAME>_TIMEOUT` (seconds). If not set, defaults from `serviceclient-preferences-service-client.xml` are used.

| Tool        | Default Timeout | Example               |
|-------------|------------------|-----------------------|
| ffmpeg      | 600              | `FFMPEG_TIMEOUT=1800` |
| video       | 600              | `VIDEO_TIMEOUT=1800`  |
| imagemagick | 300              | `IMAGEMAGICK_TIMEOUT=600` |
| exiftool    | 120              | `EXIFTOOL_TIMEOUT=90` |
| pngquant    | 120              | `PNGQUANT_TIMEOUT=60` |

Example (set longer ffmpeg/video timeouts):

```bash
docker run -d --name csclient1 \
  -e REPO_USER=repo_user -e REPO_PASS=repo_password -e VERSION=2023.1.1 \
  -e SVC_USER=user -e SVC_PASS=password -e SVC_HOST=host.example.com \
  -e FFMPEG_TIMEOUT=1800 -e VIDEO_TIMEOUT=1800 \
  cs-image-tools:v1.0
```

## Storage and ICC Profiles

### Custom ICC profiles

Mount ICC profiles at `/iccprofiles`. All files are copied to the Service-Client ICC profile directory on start.

```bash
docker run -d --name csclient1 \
  -e REPO_USER=repo_user -e REPO_PASS=repo_password -e VERSION=2023.1.1 \
  -e SVC_USER=user -e SVC_PASS=password -e SVC_HOST=host.example.com \
  -v "${PWD}/custom_iccprofiles:/iccprofiles" \
  cs-image-tools:v1.0
```

### Volume configuration with `VOLUMES_INFO`

By default, the Service-Client uses RMI to transfer files to and from the censhare Server. Supplying `VOLUMES_INFO` and mounting the asset storage into the container lets the Service-Client access those paths locally (via the filesystem) instead. This reduces RMI traffic and can improve throughput and latency, provided the `physicalurl` values map to mounted paths and permissions are set correctly.

Provide a JSON string to replace the `volumes` section in `hosts.xml`.

Example definition (as YAML for readability):

```yaml
VOLUMES_INFO: >
  {
    "assets": {"physicalurl": "file:///opt/corpus/work/assets/", "filestreaming": true},
    "assets-temp": {"physicalurl": "file:///opt/corpus/work/assets-temp/", "filestreaming": true},
    "assets-s3": {"endpoint": "s3.amazon.com", "bucket-name": "assets-s3", "secret": "foobar"},
    "temp": {"physicalurl": "file:///opt/corpus/work/temp/", "filestreaming": false}
  }
```

Run example using `VOLUMES_INFO` and a mounted host path for assets:

```bash
docker run -d --name csclient1 \
  -e REPO_USER=repo_user -e REPO_PASS=repo_password -e VERSION=2023.1.1 \
  -e SVC_USER=user -e SVC_PASS=password -e SVC_HOST=host.example.com \
  -e VOLUMES_INFO='{"assets": {"physicalurl": "file:///assets/", "filestreaming": false}, "assets-temp": {"physicalurl": "file:///assets/assets-temp/", "filestreaming": false}, "assets-s3": {"endpoint": "s3.amazon.com", "bucket-name": "assets-s3", "secret": "foobar"}, "temp": {"physicalurl": "file:///opt/corpus/work/temp/", "filestreaming": true}}' \
  -v /opt/corpus/work/assets:/assets \
  cs-image-tools:v1.0
```

## Office Previews (Collabora)

Use Collabora Online to create previews for office documents. Example `docker-compose.yml`:

```yaml
services:
  censhare-service-client:
    image: cs-service-client:2023.1.1
    environment:
      SVC_USER: 'service-client'
      SVC_PASS: 'password'
      SVC_HOST: 'host.example.com'
      SVC_INSTANCES: '2'
      OFFICE_URL: 'http://collabora:9980/cool/convert-to/pdf'
    depends_on:
      - collabora

  collabora:
    image: collabora/code
    ports:
      - "9980:9980"
```

## Java Runtime Selection

The image keeps the Service-Client and Java runtime in sync automatically:

- `2019.2` — `2022.1` use Corretto 11
- `2022.2` — `2024.3` use Corretto 17
- `2025.1` and newer use Corretto 21

Only the major release component (first two numbers of `VERSION`, e.g. `2025.3`) is used to pick the JDK.

When you pass `VERSION` during image build, the Dockerfile fetches and installs the matching Corretto release so the image is ready to run out of the box. If you skip pre-installation, the base image stays slim and the entrypoint fetches the appropriate Corretto version on container start using the same compatibility matrix.

## Tools Included

| Tool         | Version   |
|--------------|-----------|
| ImageMagick  | 7.1.2-7   |
| Ghostscript  | 10.06.0   |
| ExifTool     | 13.36     |
| FFmpeg       | 8.0       |
| pngquant     | 3.0.3     |
| wkhtmltoimage| 0.12.6.1-2|

ImageMagick features and delegates:

```
Features: Cipher DPC HDRI Modules OpenMP(4.5)
Delegates (built-in): bzlib cairo djvu fftw fontconfig fpx freetype gvc heic jbig jng jp2 jpeg jxl lcms ltdl lzma openexr pangocairo png raqm raw rsvg tiff uhdr webp wmf xml zip zlib zstd
```

## Customization

You can adjust the behavior in `entrypoint.py` and the Dockerfile to fit your needs. The entrypoint handles environment variables for flexible runtime configuration.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
