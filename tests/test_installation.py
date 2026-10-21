# test_installation.py

import os
import pwd
import re
import shutil
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
    expected_version = "7.1.2-34"
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
    expected_version = "13.55"
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
        }, "JDK major version is not within the supported set (11, 17, 21)."
    except subprocess.CalledProcessError:
        pytest.fail("Java is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'java' command not found.")
