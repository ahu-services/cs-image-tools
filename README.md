# censhare-Service-Client Docker Configuration

This repository contains Docker configurations and scripts for setting up and running the censhare-Service-Client within a Docker container. This setup is designed to be flexible, supporting both dynamic configuration at runtime and pre-configuration during image building.

## Introduction

This Docker setup is tailored for deploying the censhare-Service-Client in a containerized environment, ensuring isolation and consistency across different deployment scenarios. The configuration allows for both on-the-fly setup during container startup and pre-setup during the Docker image build, based on environment variables and build arguments.

## Features

- **ImageMagick Installation**: Image processing tools included.
- **FFmpeg and ExifTool**: Media processing and metadata extraction tools.
- **Optional Pre-installation**: Support for building images with censhare-Service-Client already downloaded and unpacked, using build-time arguments.
- **Flexible Configuration**: Dynamic configuration changes based on environment variables.
- **Graceful Shutdown**: Proper shutdown process handling to ensure data integrity.

## Prerequisites

Before you begin, ensure you have Docker installed on your system. You can download and install Docker from [Docker's official site](https://docs.docker.com/get-docker/).

## Tools included in the image

|Tool         |Version |
|-------------|--------|
|ImageMagick  |7.1.1-32|
|Ghostscript  |10.03.1 |
|ExifTool     |12.85   |
|FFmpeg       |7.0     |
|pngquant     |2.18.0  |
|wkhtmltoimage|0.12.6  |

ImageMagick Features and Delegates:

```
Features: Cipher DPC HDRI Modules OpenMP(4.5)
Delegates (built-in): bzlib cairo djvu fftw fontconfig freetype gvc heic jbig jng jp2 jpeg lcms ltdl lzma openexr pangocairo png raqm raw rsvg tiff webp wmf xml zip zlib zstd
```

## Building the Docker Image

To build the Docker image, use the following command:

```bash
docker build -t cs-image-tools:v1.0 .
```

To pre-install censhare-Service-Client during build, provide the `REPO_USER`, `REPO_PASS`, and `VERSION` as build arguments:

```bash
docker build --build-arg REPO_USER=youruser --build-arg REPO_PASS=yourpass --build-arg VERSION=2023.1.1 -t cs-service-client:2023.1.1 .
```

If there is a `iccprofiles` folder within the build direcotires, it will also be copied to the docker image during build, and during container run, the content will be copied to the `/opt/corpus/censhare/censhare-Service-Client/config/iccprofile` folder.

## Running the Container

If censhare-Service-Client was not included in your build, run your Docker container using the following command, to download and install dynamically:

```bash
docker run -d --name csclient1 \
  -e REPO_USER=repo_user -e REPO_PASS=repo_password -e VERSION=2023.1.1 \
  -e SVC_USER=user -e SVC_PASS=password -e SVC_HOST=host.example.com \
  cs-image-tools:v1.0
```

If you got the censhare-Service-Client already installed in your build, just pass service-client username, password and host to connect to.

```bash
docker run -d --name csclient1 cs-service-client:2023.1.1 \
  -e SVC_USER=user -e SVC_PASS=password -e SVC_HOST=host.example.com
```

### Custom ICC profiles

If you have ICC profiles you want to add, you can mount them directly to the container in `/iccprofiles`. All files will be copied to the censhare-Service-Client iccprofiles directory.

```bash
docker run -d --name csclient1 \
  -e REPO_USER=repo_user -e REPO_PASS=repo_password -e VERSION=2023.1.1 \
  -e SVC_USER=user -e SVC_PASS=password -e SVC_HOST=host.example.com \
  -v ${PWD}/custom_iccprofiles:/iccprofiles
  cs-image-tools:v1.0
```

### Volume Configuration with VOLUMES_INFO

To dynamically configure volume information, use the VOLUMES_INFO environment variable to provide a JSON string with volume details. This will completely replace the existing volumes section in the hosts.xml file.

#### Example VOLUMES_INFO

```yaml
VOLUMES_INFO: >
  {
    "assets": {"physicalurl": "file:///opt/corpus/work/assets/", "filestreaming": true},
    "assets-temp": {"physicalurl": "file:///opt/corpus/work/assets-temp/", "filestreaming": true},
    "assets-s3": {"endpoint": "s3.amazon.com", "bucket-name": "assets-s3", "secret": "foobar"},
    "temp": {"physicalurl": "file:///opt/corpus/work/temp/", "filestreaming": false}
  }
```

#### Docker Run Command with VOLUMES_INFO

```bash
docker run -d --name csclient1 \
  -e REPO_USER=repo_user -e REPO_PASS=repo_password -e VERSION=2023.1.1 \
  -e SVC_USER=user -e SVC_PASS=password -e SVC_HOST=host.example.com \
  -e VOLUMES_INFO='{"assets": {"physicalurl": "file:///assets/", "filestreaming": false}, "assets-temp": {"physicalurl": "file:///assets/assets-temp/", "filestreaming": false}, "assets-s3": {"endpoint": "s3.amazon.com", "bucket-name": "assets-s3", "secret": "foobar"}, "temp": {"physicalurl": "file:///opt/corpus/work/temp/", "filestreaming": true}}' \
  -v /opt/corpus/work/assets:/assets
  cs-image-tools:v1.0
```

## Running the Container together with collabora office to create previews for office documents

To use a service for creating Office document previews, here is an example docker-compose:

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

## Environment Variables

This project uses several environment variables for configuration during the Docker build process and at runtime. Below is a description of each:

### Build-time Variables

These variables are used during the Docker image build process to pre-install the censhare-Service-Client:

- `REPO_USER`: Username for the repository from which the censhare-Service-Client is downloaded. This variable is mandatory if pre-installing the client during the build.
- `REPO_PASS`: Password for the repository. This must be provided along with `REPO_USER`.
- `VERSION`: Version of the censhare-Service-Client to download and install.

### Runtime Variables

These variables affect the runtime behavior of the Docker container:

- `SVC_USER`: Username required for the censhare service client configuration. It is used to set user-specific configurations in the service client's settings.
- `SVC_PASS`: Password for the censhare service client. Used in conjunction with `SVC_USER`.
- `SVC_HOST`: Hostname or IP address where the censhare service client connects. This setting is crucial for network communication setup.
- `SVC_INSTANCES`: Defines the number of instances for parallel processing within the service client. Default is `4`.
- `OFFICE_URL`: URL of the office service for document conversion services. If not set or the service is unreachable, the related functionality is disabled.
- `OFFICE_VALIDATE_CERTS`: If the OFFICE_URL uses SSL, the certificates are validated. To turn validation off, set the `OFFICE_VALIDATE_CERTS` to `false`.
- `VOLUMES_INFO`: A JSON string defining the volume configurations, including physicalurl and filestreaming status for each volume.
- `REPO_USER`, `REPO_PASS`, and `VERSION`: These can also be provided at runtime to download and configure the censhare-Service-Client if not done at build time.

## Customization

Modify the `entrypoint.py` and Dockerfile according to your specific needs. The entrypoint script is designed to handle environment variables for flexible runtime configuration.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
