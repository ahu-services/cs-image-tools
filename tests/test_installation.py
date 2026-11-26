# test_installation.py

import os
import pwd
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

import pytest


def test_user_corpus_exists():
    """Test that the user 'corpus' exists in the system."""
    try:
        pwd.getpwnam("corpus")
    except KeyError:
        pytest.fail("User 'corpus' does not exist.")


def test_user_corpus_can_execute_commands():
    """Test that the user 'corpus' can execute commands."""
    try:
        subprocess.run(
            ["su", "-s", "/bin/sh", "-c", "echo hello", "corpus"], check=True
        )
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute commands.")
    except FileNotFoundError:
        pytest.fail("'su' command not found.")


def test_ghostscript_installed_and_version():
    """Test that Ghostscript is installed, executable, and the version is correct."""
    expected_version = "10.08.0"
    try:
        # Check if 'gs' command is available and get version
        result = subprocess.run(
            ["/usr/local/bin/gs", "-version"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert expected_version in result.stdout, (
            f"Ghostscript version does not match expected version: {expected_version}"
        )
    except subprocess.CalledProcessError:
        pytest.fail("Ghostscript is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'gs' command not found.")

    # Check that 'gs' can be executed by 'corpus'
    try:
        subprocess.run(
            ["su", "-s", "/bin/sh", "-c", "/usr/local/bin/gs -h > /dev/null", "corpus"],
            check=True,
        )
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute Ghostscript.")
    except FileNotFoundError:
        pytest.fail("'gs' command not found for user 'corpus'.")


def test_imagemagick_installed_and_version():
    """Test that ImageMagick is installed, executable, and the version is correct."""
    expected_version = "7.1.2-36"
    expected_features = "Features: Cipher DPC HDRI Modules OpenMP(4.5)"
    expected_delegagtes = "Delegates (built-in): bzlib cairo djvu fftw fontconfig fpx freetype gvc heic jbig jng jp2 jpeg jxl lcms ltdl lzma openexr pangocairo png raqm raw rsvg tiff uhdr webp wmf xml zip zlib zstd"

    try:
        # Check if 'magick' command is available and get version
        result = subprocess.run(
            ["/usr/local/bin/magick", "-version"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert expected_version in result.stdout, (
            f"ImageMagick version does not match expected version: {expected_version}"
        )
        assert expected_features in result.stdout, (
            f"ImageMagick version does not have expected features: {expected_features}"
        )
        assert expected_delegagtes in result.stdout, (
            f"ImageMagick version does not have expected delegates: {expected_delegagtes}"
        )
    except subprocess.CalledProcessError:
        pytest.fail("ImageMagick is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'magick' command not found.")

    # Check that 'magick' can be executed by 'corpus'
    try:
        subprocess.run(
            [
                "su",
                "-s",
                "/bin/sh",
                "-c",
                "/usr/local/bin/magick -version > /dev/null",
                "corpus",
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute ImageMagick.")
    except FileNotFoundError:
        pytest.fail("'magick' command not found for user 'corpus'.")


MAGICK = "/usr/local/bin/magick"
SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'
RED_RECT_SVG = f'<svg {SVG_NS} width="40" height="20"><rect width="40" height="20" fill="#ff0000"/></svg>'


RED = "255,0,0"
WHITE = "255,255,255"


def _render_svg_center_pixel(svg_path):
    """Render an SVG onto white and return the center pixel as "r,g,b", or None if rendering failed."""
    channel = "%[fx:int(255*p{{20,10}}.{}+0.5)]"
    result = subprocess.run(
        [
            MAGICK,
            str(svg_path),
            "-background",
            "#ffffff",
            "-flatten",
            "-format",
            ",".join(channel.format(c) for c in "rgb"),
            "info:",
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def test_imagemagick_reads_svg(tmp_path):
    """SVG assets must convert to a JPEG preview (censhare PreviewMaker command)."""
    svg = tmp_path / "asset.svg"
    svg.write_text(RED_RECT_SVG)
    out = tmp_path / "preview.jpg"
    result = subprocess.run(
        [
            MAGICK,
            str(svg),
            "-intent",
            "Perceptual",
            "-black-point-compensation",
            "-auto-orient",
            "-resize",
            "1000x1000>",
            "-quality",
            "80",
            "-density",
            "72x72",
            "-background",
            "#ffffff",
            "+repage",
            "-flatten",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"SVG preview failed: {result.stderr}"
    assert out.stat().st_size > 0


def test_imagemagick_blocks_msvg_and_mvg(tmp_path):
    """ImageMagick's internal MSVG renderer and MVG input stay disabled."""
    svg = tmp_path / "asset.svg"
    svg.write_text(RED_RECT_SVG)
    mvg = tmp_path / "asset.mvg"
    mvg.write_text("viewbox 0 0 10 10 fill red rectangle 0,0 10,10\n")
    for source in (f"msvg:{svg}", f"mvg:{mvg}"):
        result = subprocess.run(
            [MAGICK, source, str(tmp_path / "out.png")],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, f"{source} must be rejected"
        assert "security policy" in result.stderr


BLUE = "0,0,255"
BLUE_PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVQI12NgYPgPAAEDAQDZqt2zAAAAAElFTkSuQmCC"
)


@pytest.mark.parametrize(
    "reference",
    [
        '<image href="{href}" width="40" height="20" preserveAspectRatio="none"/>',
        '<xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="{href}"/>',
    ],
    ids=["image", "xinclude"],
)
def test_svg_cannot_include_files_from_elsewhere_on_disk(tmp_path, reference):
    """librsvg must not embed files outside the temp directory the SVG is rendered from."""
    outside_dir = Path(tempfile.mkdtemp(dir="/var/tmp"))
    try:
        subprocess.run(
            [MAGICK, "-size", "40x20", "xc:#0000ff", str(outside_dir / "blue.png")],
            check=True,
        )
        (outside_dir / "blue.xml").write_text(
            f'<rect {SVG_NS} width="40" height="20" fill="#0000ff"/>'
        )
        target = outside_dir / ("blue.png" if "<image" in reference else "blue.xml")

        def render(href):
            svg = tmp_path / "asset.svg"
            svg.write_text(
                f'<svg {SVG_NS} width="40" height="20">{reference.format(href=href)}</svg>'
            )
            return _render_svg_center_pixel(svg)

        if "<image" in reference:
            # Control: embedded image data renders, so a white result below means "blocked".
            assert render(BLUE_PNG_DATA_URI) == BLUE
        # Blocked references either render nothing or make librsvg reject the file.
        assert render(f"file://{target}") in (WHITE, None)
        assert render(os.path.relpath(target, tmp_path)) in (WHITE, None)
    finally:
        shutil.rmtree(outside_dir)


@pytest.mark.parametrize(
    ("name", "source", "output"),
    [("potrace", "png", "svg"), ("ffmpeg", "gif", "mp4")],
)
def test_imagemagick_external_delegates_run(tmp_path, name, source, output):
    """Delegates invoked by ImageMagick (raster-to-SVG trace, video) are permitted by policy."""
    src = tmp_path / f"in.{source}"
    # Video output needs an animation; PNG holds a single frame.
    frames = ["xc:red", "xc:blue"] if source == "gif" else ["xc:red"]
    subprocess.run([MAGICK, "-size", "40x20", *frames, str(src)], check=True)
    out = tmp_path / f"out.{output}"
    result = subprocess.run(
        [MAGICK, str(src), str(out)], capture_output=True, text=True
    )
    assert result.returncode == 0, f"{name} delegate failed: {result.stderr}"
    assert out.stat().st_size > 0


def test_exiftool_installed_and_version():
    """Test that ExifTool is installed, executable, and the version is correct."""
    expected_version = "13.64"
    try:
        # Check if 'exiftool' command is available and get version
        result = subprocess.run(
            ["/usr/local/bin/exiftool", "-ver"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert expected_version in result.stdout.strip(), (
            f"ExifTool version does not match expected version: {expected_version}"
        )
    except subprocess.CalledProcessError:
        pytest.fail("ExifTool is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'exiftool' command not found.")

    # Check that 'exiftool' can be executed by 'corpus'
    try:
        subprocess.run(
            [
                "su",
                "-s",
                "/bin/sh",
                "-c",
                "/usr/local/bin/exiftool -ver > /dev/null",
                "corpus",
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute ExifTool.")
    except FileNotFoundError:
        pytest.fail("'exiftool' command not found for user 'corpus'.")


def test_ffmpeg_installed_and_version():
    """Test that ffmpeg is installed, executable, and the version is correct."""
    expected_version = "9.0.2"
    try:
        # Check if 'ffmpeg' command is available and get version
        result = subprocess.run(
            ["/usr/local/bin/ffmpeg", "-version"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert expected_version in result.stdout, (
            f"ffmpeg version does not match expected version: {expected_version}"
        )
    except subprocess.CalledProcessError:
        pytest.fail("ffmpeg is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'ffmpeg' command not found.")

    # Check that 'ffmpeg' can be executed by 'corpus'
    try:
        subprocess.run(
            [
                "su",
                "-s",
                "/bin/sh",
                "-c",
                "/usr/local/bin/ffmpeg -h > /dev/null",
                "corpus",
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute ffmpeg.")
    except FileNotFoundError:
        pytest.fail("'ffmpeg' command not found for user 'corpus'.")


def test_ffmpeg_libs():
    """Test that ffmpeg includes libs."""
    ffmpeg_additional_libs = ["libvpx", "libopus", "libx264", "libx265", "libdav1d"]
    try:
        # Get 'ffmpeg' linked libaries
        result = subprocess.run(
            ["/usr/bin/ldd", "/usr/local/bin/ffmpeg"],
            capture_output=True,
            text=True,
            check=True,
        )
        for lib in ffmpeg_additional_libs:
            assert lib in result.stdout, f"ffmpeg version missing linked lib: {lib}"
    except subprocess.CalledProcessError:
        pytest.fail("ldd is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'ldd' command not found.")


def test_third_party_licenses_installed():
    """Test that the third-party-licenses.txt file exists."""
    if not os.path.exists("/third-party-licenses.txt"):
        pytest.fail("third-party-licenses.txt is not installed.")


def test_corretto_jdk_installed():
    """Test that an Amazon Corretto JDK from the supported set is installed when present."""
    java_path = shutil.which("java")
    if java_path is None:
        pytest.skip("Corretto JDK not pre-installed in this image variant.")
    try:
        result = subprocess.run(
            [java_path, "-version"], capture_output=True, text=True, check=True
        )
        combined_output = (
            result.stderr + result.stdout
        )  # Java version info is usually in stderr
        assert "Corretto" in combined_output, "Amazon Corretto JDK not installed."
        match = re.search(r'version\s+"(\d+)', combined_output)
        assert match, "Unable to detect Java version."
        assert match.group(1) in {
            "11",
            "17",
            "21",
            "25",
        }, "JDK major version is not within the supported set (11, 17, 21, 25)."
    except subprocess.CalledProcessError:
        pytest.fail("Java is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'java' command not found.")


# --------------------------------------------------------------------------- #
# ImageMagick policy: limits and large layered TIFF masters
# --------------------------------------------------------------------------- #
POLICY_PATH = Path("/usr/local/etc/ImageMagick-7/policy.xml")
# Large enough to exceed max-memory-request=256MiB and the old 512MiB autoconfig
# value; layered Photoshop TIFF masters carry blocks of 330-980 MB.
LARGE_LAYER_BLOCK_BYTES = int(os.getenv("LARGE_LAYER_BLOCK_BYTES", "750000000"))


def _write_layered_tiff(path, width=64, height=64, block_bytes=0):
    """
    Writes a synthetic big-endian RGB TIFF whose tag 37724 (ImageSourceData)
    holds a valid one-layer Photoshop layer section, padded to block_bytes.
    ImageMagick reads the composite from IFD0 and the layer as a second frame.
    """
    plane = bytes((x * 7 + y * 3) % 256 for y in range(height) for x in range(width))
    record = struct.pack(">iiiiH", 0, 0, height, width, 3)
    for channel_id in range(3):
        record += struct.pack(">hI", channel_id, 2 + len(plane))
    record += b"8BIMnorm" + bytes([255, 0, 0, 0])
    extra = struct.pack(">II", 0, 0) + b"\x05Layer\x00\x00"
    record += struct.pack(">I", len(extra)) + extra
    channel_data = b"".join(struct.pack(">H", 0) + plane for _ in range(3))
    layer_info = struct.pack(">h", 1) + record + channel_data
    layer_info += b"\0" * (len(layer_info) % 2)
    layer = (
        b"Adobe Photoshop Document Data Block\0"
        + b"8BIMLayr"
        + struct.pack(">I", len(layer_info))
        + layer_info
    )
    padding = max(0, block_bytes - len(layer))
    pixels = bytes(
        v
        for y in range(height)
        for x in range(width)
        for v in (x * 4 % 256, y * 4 % 256, 128)
    )
    strip_offset = 8 + 6
    block_offset = strip_offset + len(pixels)
    block_offset += block_offset % 2
    ifd_offset = block_offset + len(layer) + padding
    ifd_offset += ifd_offset % 2
    entries = [
        (256, 4, 1, width),
        (257, 4, 1, height),
        (258, 3, 3, 8),
        (259, 3, 1, 1),
        (262, 3, 1, 2),
        (273, 4, 1, strip_offset),
        (277, 3, 1, 3),
        (278, 4, 1, height),
        (279, 4, 1, len(pixels)),
        (284, 3, 1, 1),
        (37724, 7, len(layer) + padding, block_offset),
    ]
    with open(path, "wb") as handle:
        handle.write(b"MM\0*" + struct.pack(">I", ifd_offset))
        handle.write(struct.pack(">HHH", 8, 8, 8))
        handle.write(pixels)
        handle.write(b"\0" * (block_offset - handle.tell()))
        handle.write(layer)
        chunk = b"\0" * (8 << 20)
        while padding:
            size = min(padding, len(chunk))
            handle.write(chunk[:size])
            padding -= size
        handle.write(b"\0" * (ifd_offset - handle.tell()))
        handle.write(struct.pack(">H", len(entries)))
        for tag, kind, count, value in entries:
            if kind == 3 and count == 1:
                handle.write(struct.pack(">HHIHH", tag, kind, count, value, 0))
            else:
                handle.write(struct.pack(">HHII", tag, kind, count, value))
        handle.write(struct.pack(">I", 0))


def _convert_master(source, out):
    """The conversion censhare runs for layered TIFF masters."""
    return subprocess.run(
        [MAGICK, str(source), "-delete", "1--1", "-resize", "4000x4000>", str(out)],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def rendered_policy(monkeypatch):
    """Renders the policy with entrypoint.py and restores the bundled one afterwards."""
    import entrypoint

    backup = POLICY_PATH.read_bytes()
    template = Path(str(POLICY_PATH) + ".dist")
    had_template = template.exists()
    for name in list(os.environ):
        if name.startswith("IMAGEMAGICK_"):
            monkeypatch.delenv(name)

    def render(memory_bytes, instances, cpus=1.0, **env):
        monkeypatch.setenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "true")
        monkeypatch.setenv("SVC_INSTANCES", str(instances))
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            entrypoint, "detect_container_memory_limit_bytes", lambda: memory_bytes
        )
        monkeypatch.setattr(
            entrypoint, "detect_cpu_limit", lambda ecs_limits=None: (cpus, "test")
        )
        entrypoint.configure_imagemagick_policy(str(POLICY_PATH))

    yield render
    POLICY_PATH.write_bytes(backup)
    if not had_template and template.exists():
        template.unlink()


def _policy_listing():
    return subprocess.run(
        [MAGICK, "-list", "policy"], capture_output=True, text=True, check=True
    ).stdout


def test_bundled_policy_limits():
    listing = " ".join(_policy_listing().split())
    for name, value in (
        ("width", "32KP"),
        ("height", "32KP"),
        ("time", "120"),
        ("max-memory-request", "1536MiB"),
    ):
        assert f"name: {name} value: {value}" in listing, f"{name} != {value}"


def test_area_limit_caches_to_disk_instead_of_rejecting(tmp_path):
    """Images above the area limit are cached to disk, not rejected."""
    out = tmp_path / "out.jpg"
    result = subprocess.run(
        [MAGICK, "-size", "2000x2000", "xc:gray", "-resize", "50%", str(out)],
        capture_output=True,
        text=True,
        env={**os.environ, "MAGICK_AREA_LIMIT": "1MP"},
    )
    assert result.returncode == 0, result.stderr
    assert out.stat().st_size > 0


def test_width_limit_rejects_wider_images(tmp_path):
    result = subprocess.run(
        [MAGICK, "-size", "33000x1", "xc:gray", str(tmp_path / "out.png")],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "width or height exceeds limit" in result.stderr


def test_layered_tiff_layers_are_read_and_dropped(tmp_path):
    source = tmp_path / "master.tif"
    _write_layered_tiff(source)
    frames = subprocess.run(
        [MAGICK, "identify", "-format", "%s\\n", str(source)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert frames == ["0", "1"], "Photoshop layer block was not decoded"

    out = tmp_path / "preview.jpg"
    result = _convert_master(source, out)
    assert result.returncode == 0, result.stderr
    size = subprocess.run(
        [MAGICK, "identify", "-format", "%wx%h", str(out)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert size == "64x64"


def test_large_layer_block_converts_with_autoconfig_for_8gb_4_instances(
    tmp_path, rendered_policy
):
    rendered_policy(8 * 1024**3, 4)
    assert "max-memory-request value: 1536MiB" in " ".join(_policy_listing().split())
    source = tmp_path / "master.tif"
    _write_layered_tiff(source, block_bytes=LARGE_LAYER_BLOCK_BYTES)

    result = _convert_master(source, tmp_path / "preview.jpg")

    assert result.returncode == 0, result.stderr


def test_large_layer_block_fails_with_256mib_max_memory_request(
    tmp_path, rendered_policy
):
    rendered_policy(8 * 1024**3, 4, IMAGEMAGICK_POLICY_MAX_MEMORY_REQUEST="256MiB")
    source = tmp_path / "master.tif"
    _write_layered_tiff(source, block_bytes=LARGE_LAYER_BLOCK_BYTES)

    result = _convert_master(source, tmp_path / "preview.jpg")

    assert result.returncode != 0
    assert "memory allocation failed" in result.stderr.lower(), result.stderr
