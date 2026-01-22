# TODO build gslib
# TODO build dmr https://github.com/ImageMagick/MagickCache

### Create base image for others (Debian 13.3)
FROM debian:13.3-slim AS debian-builder

RUN apt-get update && apt-get install -y wget build-essential libtool pkg-config \
    libfreetype-dev libfontconfig-dev libbz2-dev libzip-dev libzstd-dev \
    libdjvulibre-dev libfftw3-dev libgraphviz-dev libheif-dev libwmf-dev \
    liblzma-dev libopenexr-dev libopenjp2-7-dev libpango1.0-dev libraqm-dev \
    libraw-dev librsvg2-dev libtiff-dev libwebp-dev libxml2-dev \
    yasm xz-utils perl python3 git \
    libx264-dev libx265-dev libnuma-dev nasm libvpx-dev libopus-dev libdav1d-dev \
    libjxl-dev libx11-dev libxt-dev libpng-dev curl liblcms2-dev
RUN apt-get install ca-certificates

### Build Ghostscript
FROM debian-builder AS ghostscript-builder
ARG GHOSTSCRIPT_VERSION=10.06.0

# Download and build Ghostscript
WORKDIR /tmp
SHELL ["/bin/bash", "-c"]
RUN wget https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs${GHOSTSCRIPT_VERSION//.}/ghostscript-${GHOSTSCRIPT_VERSION}.tar.gz \
    && tar -xzf ghostscript-${GHOSTSCRIPT_VERSION}.tar.gz \
    && cd ghostscript-${GHOSTSCRIPT_VERSION} \
    && ./configure && make && make install DESTDIR=/ghostscript-build

### Build FPX libs for ImageMagick
FROM debian-builder AS fpx-builder
WORKDIR /tmp
RUN git clone https://github.com/ImageMagick/libfpx.git \
    && cd libfpx \
    && ./configure --prefix=/usr \
    && make -j$(nproc) \
    && make install DESTDIR=/fpx-build

### Build Ultra HDR (UHDR) libs for ImageMagick
FROM debian-builder AS uhdr-builder
WORKDIR /tmp
RUN apt-get update && apt-get install -y cmake ninja-build clang libjpeg-dev \
    && git clone https://github.com/google/libultrahdr.git \
    && cd libultrahdr \
    && mkdir build && cd build \
    && cmake -G Ninja -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ -DUHDR_BUILD_TESTS=OFF .. \
    && ninja \
    && DESTDIR=/uhdr-build ninja install

### Build ImageMagick
FROM debian-builder AS im-builder
ARG IMAGEMAGICK_VERSION=7.1.2-13

# Download ImageMagick
WORKDIR /tmp
RUN wget https://download.imagemagick.org/archive/releases/ImageMagick-${IMAGEMAGICK_VERSION}.tar.gz

COPY --from=ghostscript-builder /ghostscript-build/ /
COPY --from=fpx-builder /fpx-build/ /
COPY --from=uhdr-builder /uhdr-build/ /

# Unpack and compile ImageMagick
RUN tar -xzvf ImageMagick-${IMAGEMAGICK_VERSION}.tar.gz \
    && cd ImageMagick-${IMAGEMAGICK_VERSION} \
    && CFLAGS=" -DIMPNG_SETJMP_IS_THREAD_SAFE" \
    ./configure --enable-static --enable-openmp --enable-opencl --with-threads --with-jbig \
    --enable-hdri --with-xml --without-x --with-quantum-depth=16 --disable-dependency-tracking \
    --with-modules --without-dps --with-freetype=yes --with-jpeg=yes --with-tiff=yes \
    --with-png=yes --with-openjp2=yes --with-fpx=yes --with-lcms=yes --with-webp=yes \
    --with-wmf=yes --with-rsvg --with-bzlib --without-magick-plus-plus -with-heic=yes \
    --without-perl --without-apple-font-dir --without-dejavu-font-dir --disable-docs \
    --without-windows-font-dir --with-gslib=yes --with-security-policy=secure \
    --with-openjp2=yes --with-fftw=yes --with-rsvg=yes --with-jxl=yes --with-uhdr \
    --with-gs-font-dir=/usr/share/ghostscript/fonts \
    --with-fontpath=/usr/share/ghostscript/fonts \
    && make -j$(nproc) \
    && make install DESTDIR=/IM-build

COPY imagemagick-policy.xml /IM-build/usr/local/etc/ImageMagick-7/policy.xml

### Build ffmpeg
FROM debian-builder AS ffmpeg-builder
ARG FFMPEG_VERSION=8.0.1

# Download and build ffmpeg
WORKDIR /tmp
RUN wget https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz \
    && unxz ffmpeg-${FFMPEG_VERSION}.tar.xz \
    && tar -xf ffmpeg-${FFMPEG_VERSION}.tar \
    && cd ffmpeg-${FFMPEG_VERSION} \
    && ./configure --enable-gpl --enable-libx264 --enable-libx265 --enable-libvpx \
    --enable-libopus --enable-libdav1d \
    && make && make install DESTDIR=/ffmpeg-build

### Build ExifTool
FROM debian-builder AS exif-builder
ARG EXIF_VERSION=13.44

# Download and build ExifTool
WORKDIR /tmp
RUN set -eux; \
    curl -L "https://sourceforge.net/projects/exiftool/files/Image-ExifTool-${EXIF_VERSION}.tar.gz/download" -o exiftool.tar.gz; \
    mkdir exiftool-src; \
    tar -xzf exiftool.tar.gz -C exiftool-src --strip-components=1; \
    cd exiftool-src; \
    perl Makefile.PL; \
    make install DESTDIR=/exif-build

### Build latest pngquant
FROM debian-builder AS pngquant-builder
ARG PNGQUANT_VERSION=3.0.3
WORKDIR /tmp
RUN curl https://sh.rustup.rs -sSf | bash -s -- -y \
    && . "$HOME/.cargo/env" \
    && git clone --branch ${PNGQUANT_VERSION} --recursive https://github.com/kornelski/pngquant.git \
    && cd pngquant \
    && cargo build --release --features=lcms2 \
    && install -Dm755 target/release/pngquant /pngquant-build/usr/local/bin/pngquant

### Image to combine all tools
FROM debian-builder AS image-tools-combined

## Parameters for optional pre-download of censhare-Service-Client
ARG REPO_USER
ARG REPO_PASS
ARG VERSION

# Copy docker build directory, in case  additional resources are provided, such as iccprofiles
COPY . /app

## Optionally download and install censhare-Service-Client during build
RUN if [ -n "$REPO_USER" ] && [ -n "$REPO_PASS" ] && [ -n "$VERSION" ]; then \
        groupadd -g 861 corpus; \
        useradd -d /opt/corpus -u 861 -g 861 -m corpus; \
        wget --user=$REPO_USER --password=$REPO_PASS https://rpm.censhare.com/censhare-release/censhare-Service/v$VERSION/Shell/censhare-Service-Client-v$VERSION.tar.gz -O /tmp/censhare-client.tar.gz && \
        tar -xzf /tmp/censhare-client.tar.gz -C /opt/corpus && \
        chown -R corpus:corpus /opt/corpus && \
        mkdir -p /opt/corpus/censhare && \
        echo "$VERSION" > /opt/corpus/censhare/client-version.txt && \
        rm -f /tmp/censhare-client.tar.gz; \
    fi

# Create the directory to ensure it always exists
RUN mkdir -p /opt/corpus

# Prepare optional Corretto JDK download
RUN set -eux; \
    mkdir -p /TOOLS; \
    if [ -n "${VERSION:-}" ]; then \
        RELEASE_KEY=$(printf "%s" "$VERSION" | awk -F. 'NF>=2 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ {printf "%d%02d", $1, $2}'); \
        if [ -z "$RELEASE_KEY" ]; then \
            JDK_RESOLVED=17; \
        elif [ "$RELEASE_KEY" -le 202201 ]; then \
            JDK_RESOLVED=11; \
        elif [ "$RELEASE_KEY" -le 202403 ]; then \
            JDK_RESOLVED=17; \
        else \
            JDK_RESOLVED=21; \
        fi; \
        ARCH=$(uname -m); \
        case "$ARCH" in \
            x86_64) ARCH_ALIAS="x64" ;; \
            aarch64) ARCH_ALIAS="aarch64" ;; \
            *) echo "Unsupported architecture: $ARCH"; exit 1 ;; \
        esac; \
        echo "Using Corretto JDK ${JDK_RESOLVED} for Service-Client ${VERSION}"; \
        wget -O "/TOOLS/amazon-corretto-${JDK_RESOLVED}-${ARCH_ALIAS}-linux-jdk.deb" \
            "https://corretto.aws/downloads/latest/amazon-corretto-${JDK_RESOLVED}-${ARCH_ALIAS}-linux-jdk.deb"; \
    fi; \
    if [ -d /app/iccprofiles ]; then cp -r /app/iccprofiles /TOOLS/build_iccprofiles; fi

# Add 3rd party license information
COPY LICENSE third-party-licenses.txt /TOOLS/

# Copy binaries from builder stages
COPY --from=im-builder /IM-build/ /TOOLS/
COPY --from=uhdr-builder /uhdr-build/ /TOOLS/
COPY --from=ghostscript-builder /ghostscript-build/ /TOOLS/
COPY --from=ffmpeg-builder /ffmpeg-build/ /TOOLS/
COPY --from=pngquant-builder /pngquant-build/ /TOOLS/

### Final image
FROM debian:13.3-slim AS final

RUN set -eux; \
    apt-get update; \
    apt-get remove -y wpasupplicant; \
    apt-get upgrade -y; \
    apt-get install -y --no-install-recommends \
        iproute2 wget pkg-config libimage-exiftool-perl webp liblcms2-dev libxt-dev librsvg2-bin potrace \
        libopus-dev libdav1d-dev libraqm-dev libfftw3-dev libtool python3 ca-certificates java-common \
        libvpx-dev libx264-dev libx265-dev fontconfig libjpeg62-turbo libssl-dev xfonts-75dpi xfonts-base rawtherapee; \
    apt-get purge -y samba samba-libs smbclient libsmbclient winbind libwbclient0 cifs-utils || true; \
    apt-get upgrade -y; \
    apt-get autoremove -y

# Install wkhtmltopdf for both amd64 and arm64
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    if [ "$ARCH" = "amd64" ]; then \
        WKHTML_URL="https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb"; \
    elif [ "$ARCH" = "arm64" ]; then \
        WKHTML_URL="https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_arm64.deb"; \
    else \
        echo "Unsupported architecture: $ARCH"; exit 1; \
    fi; \
    wget -O /tmp/wkhtmltox.deb "$WKHTML_URL"; \
    dpkg -i /tmp/wkhtmltox.deb || (apt-get update && apt-get -y -f install && dpkg -i /tmp/wkhtmltox.deb); \
    rm /tmp/wkhtmltox.deb

# Copy binaries from builder stages
COPY --from=image-tools-combined /TOOLS/ /
COPY --from=exif-builder /exif-build/usr/local/bin/ /usr/local/bin/
COPY --from=exif-builder /exif-build/usr/local/man/ //usr/local/man/
COPY --from=exif-builder /exif-build/usr/local/lib/ //usr/local/lib/
COPY --from=exif-builder /exif-build/usr/local/share/ //usr/local/share/

# Install Corretto JDK (if provided) and refresh libraries
RUN ldconfig; \
    groupadd -g 861 corpus; \
    useradd -d /opt/corpus -u 861 -g 861 -m  corpus; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3-pip; \
    pip3 install --no-cache-dir --break-system-packages "requests==2.32.3" "urllib3==2.6.0"; \
    apt-get purge -y python3-pip; \
    apt-get autoremove -y; \
    if ls /amazon-corretto-*-linux-jdk.deb >/dev/null 2>&1; then \
        apt-get install -y /amazon-corretto-*-linux-jdk.deb; \
        rm -f /amazon-corretto-*-linux-jdk.deb; \
    fi; \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Add Service-Client if included in build
COPY --from=image-tools-combined /opt/corpus/ /opt/corpus/
# Add entrypoint script
COPY entrypoint.py /usr/local/bin/entrypoint.py
# Add health check script
COPY health_check.py /usr/local/bin/health_check.py


### Test Stage
FROM final AS test
ENV PYTHONPATH="/usr/local/bin"
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3-pytest; \
    rm -rf /var/lib/apt/lists/*
COPY tests/test_installation.py /test_installation.py
COPY tests/test_health_check.py /test_health_check.py

CMD ["pytest", "-v", "/test_installation.py", "/test_health_check.py"]

### Release
FROM final
# Define health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python3 /usr/local/bin/health_check.py

# Define entrypoint to configure and start Service-Client
ENTRYPOINT ["python3", "/usr/local/bin/entrypoint.py"]
