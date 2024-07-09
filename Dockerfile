# TODO build libjxl https://github.com/libjxl/libjxl
# TODO build gslib
# TODO build dmr https://github.com/ImageMagick/MagickCache
### TODO Build latest https://github.com/kornelski/pngquant

### Create base image for others
FROM debian:trixie-slim AS debian-builder

RUN apt-get update && apt-get install -y wget build-essential libtool pkg-config \
    libfreetype-dev libfontconfig-dev libbz2-dev libzip-dev libzstd-dev \
    libdjvulibre-dev libfftw3-dev libgraphviz-dev libheif-dev libwmf-dev \
    liblzma-dev libopenexr-dev libopenjp2-7-dev libpango1.0-dev libraqm-dev \
    libraw-dev librsvg2-dev libtiff-dev libwebp-dev libxml2-dev \
    yasm xz-utils perl python3
RUN apt-get install ca-certificates

### Build Ghostscript
FROM debian-builder AS ghostscript-builder
ARG GHOSTSCRIPT_VERSION=10.03.1

# Download and build Ghostscript
WORKDIR /tmp
SHELL ["/bin/bash", "-c"]
RUN wget https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs${GHOSTSCRIPT_VERSION//.}/ghostscript-${GHOSTSCRIPT_VERSION}.tar.gz \
    && tar -xzf ghostscript-${GHOSTSCRIPT_VERSION}.tar.gz \
    && cd ghostscript-${GHOSTSCRIPT_VERSION} \
    && ./configure && make && make install DESTDIR=/ghostscript-build


### Build ImageMagick
FROM debian-builder AS im-builder
ARG IMAGEMAGICK_VERSION=7.1.1-34

# Download ImageMagick
WORKDIR /tmp
RUN wget https://download.imagemagick.org/archive/releases/ImageMagick-${IMAGEMAGICK_VERSION}.tar.gz

COPY --from=ghostscript-builder /ghostscript-build/ /

# Unpack and compile ImageMagick
RUN tar -xzvf ImageMagick-${IMAGEMAGICK_VERSION}.tar.gz \
    && cd ImageMagick-${IMAGEMAGICK_VERSION} \
    && ./configure --enable-static --enable-openmp --enable-opencl --disable-hdri \
    --with-xml --without-x --with-quantum-depth=16 --disable-dependency-tracking \
    --with-modules --without-dps --with-freetype=yes --with-jpeg=yes --with-tiff=yes \
    --with-png=yes --with-openjp2=no --with-fpx=no --with-lcms=yes --with-webp=yes \
    --with-wmf=yes --without-rsvg --without-bzlib --without-magick-plus-plus \
    --without-perl --without-apple-font-dir --without-dejavu-font-dir \
    --without-windows-font-dir --with-gslib=yes --with-bzlib=yes --enable-hdri \
    --with-openjp2=yes --with-fftw=yes --with-rsvg=yes \
    --with-gs-font-dir=/usr/share/ghostscript/fonts \
    --with-fontpath=/usr/share/ghostscript/fonts \
    && make -j$(nproc) \
    && make install DESTDIR=/IM-build

### Build ffmpeg
FROM debian-builder AS ffmpeg-builder
ARG FFMPEG_VERSION=7.0.1

# Download and build ffmpeg
WORKDIR /tmp
RUN wget https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz \
    && unxz ffmpeg-${FFMPEG_VERSION}.tar.xz \
    && tar -xf ffmpeg-${FFMPEG_VERSION}.tar \
    && cd ffmpeg-${FFMPEG_VERSION} \
    && ./configure && make && make install DESTDIR=/ffmpeg-build

### Build ExifTool
FROM debian-builder AS exif-builder
ARG EXIF_VERSION=12.87

# Download and build ExifTool
WORKDIR /tmp
RUN wget https://exiftool.org/Image-ExifTool-${EXIF_VERSION}.tar.gz \
    && tar -xzf Image-ExifTool-${EXIF_VERSION}.tar.gz \
    && cd Image-ExifTool-${EXIF_VERSION} \
    && perl Makefile.PL \
    && make install DESTDIR=/exif-build

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
        useradd -d /opt/corpus -u 861 -g 861 -m  corpus; \        
        wget --user=$REPO_USER --password=$REPO_PASS https://rpm.censhare.com/censhare-release/censhare-Service/v$VERSION/Shell/censhare-Service-Client-v$VERSION.tar.gz -O /tmp/censhare-client.tar.gz && \
        tar -xzf /tmp/censhare-client.tar.gz -C /opt/corpus && \
        chown -R corpus:corpus /opt/corpus && \
        rm -f /tmp/censhare-client.tar.gz; \
    fi

# Create the directory to ensure it always exists
RUN mkdir -p /opt/corpus

# Install Corretto JDK
ARG JDK_VERSION=17
RUN ARCH=$(uname -m); \
    if [ "$ARCH" = "x86_64" ]; then \
    ARCH="x64"; \
    elif [ "$ARCH" = "aarch64" ]; then \
    ARCH="aarch64"; \
    else \
    echo "Unsupported architecture"; \
    exit 1; \
    fi; \
    mkdir /TOOLS/; cd /TOOLS; wget https://corretto.aws/downloads/latest/amazon-corretto-${JDK_VERSION}-${ARCH}-linux-jdk.deb; \
    if [ -d /app/iccprofiles ]; then cp -r /app/iccprofiles /TOOLS/build_iccprofiles; fi

# Add 3rd party license information
COPY LICENSE third-party-licenses.txt /TOOLS/

# Copy binaries from builder stages
COPY --from=im-builder /IM-build/ /TOOLS/
COPY --from=ghostscript-builder /ghostscript-build/ /TOOLS/
COPY --from=ffmpeg-builder /ffmpeg-build/ /TOOLS/

### Final image
FROM debian:trixie-slim as final
RUN apt-get update && apt-get remove -y wpasupplicant && apt-get upgrade -y && \
    apt-get install -y iproute2 wget pkg-config wkhtmltopdf pngquant \
    libraqm-dev libfftw3-dev libtool python3 python3-pip ca-certificates

# Copy binaries from builder stages
COPY --from=image-tools-combined /TOOLS/ /
COPY --from=exif-builder /exif-build/usr/local/bin/ /usr/local/bin/
COPY --from=exif-builder /exif-build/usr/local/man/ //usr/local/man/
COPY --from=exif-builder /exif-build/usr/local/lib/ //usr/local/lib/
COPY --from=exif-builder /exif-build/usr/local/share/ //usr/local/share/

# Install Corretto JDK and refresh libraries
ARG JDK_VERSION=17
RUN ldconfig; \
    groupadd -g 861 corpus; \
    useradd -d /opt/corpus -u 861 -g 861 -m  corpus; \
    pip install requests --break-system-packages; \
    apt-get install -y /amazon-corretto-*-linux-jdk.deb && \
    rm -f /amazon-corretto-*-linux-jdk.deb && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Add Service-Client if included in build
COPY --from=image-tools-combined /opt/corpus/ /opt/corpus/
# Add entrypoint script
COPY entrypoint.py /usr/local/bin/entrypoint.py
# Add health check script
COPY health_check.py /usr/local/bin/health_check.py


### Test Stage
FROM final as test
RUN pip3 install pytest --break-system-packages
COPY tests/test_installation.py /test_installation.py
COPY tests/test_health_check.py /test_health_check.py

CMD ["pytest", "-v", "/test_installation.py", "/test_health_check.py"]

### Release
FROM final
# Expose Service-Client Ports
EXPOSE 30543 30544 30545
# Define health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python3 /usr/local/bin/health_check.py

# Define entrypoint to configure and start Service-Client
ENTRYPOINT ["python3", "/usr/local/bin/entrypoint.py"]
