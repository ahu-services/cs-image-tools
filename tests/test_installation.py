# test_installation.py

import pytest
import subprocess
import os
import pwd
import re
import shutil

def test_user_corpus_exists():
    """Test that the user 'corpus' exists in the system."""
    try:
        pwd.getpwnam('corpus')
    except KeyError:
        pytest.fail("User 'corpus' does not exist.")

def test_user_corpus_can_execute_commands():
    """Test that the user 'corpus' can execute commands."""
    try:
        subprocess.run(['su', '-s', '/bin/sh', '-c', 'echo hello', 'corpus'], check=True)
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute commands.")
    except FileNotFoundError:
        pytest.fail("'su' command not found.")

def test_ghostscript_installed_and_version():
    """Test that Ghostscript is installed, executable, and the version is correct."""
    expected_version = '10.06.0'
    try:
        # Check if 'gs' command is available and get version
        result = subprocess.run(['/usr/local/bin/gs', '-version'], capture_output=True, text=True, check=True)
        assert expected_version in result.stdout, f"Ghostscript version does not match expected version: {expected_version}"
    except subprocess.CalledProcessError:
        pytest.fail("Ghostscript is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'gs' command not found.")

    # Check that 'gs' can be executed by 'corpus'
    try:
        subprocess.run(['su', '-s', '/bin/sh', '-c', '/usr/local/bin/gs -h > /dev/null', 'corpus'], check=True)
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute Ghostscript.")
    except FileNotFoundError:
        pytest.fail("'gs' command not found for user 'corpus'.")

def test_imagemagick_installed_and_version():
    """Test that ImageMagick is installed, executable, and the version is correct."""
    expected_version = '7.1.2-10'
    expected_features = 'Features: Cipher DPC HDRI Modules OpenMP(4.5)'
    expected_delegagtes = 'Delegates (built-in): bzlib cairo djvu fftw fontconfig fpx freetype gvc heic jbig jng jp2 jpeg jxl lcms ltdl lzma openexr pangocairo png raqm raw rsvg tiff uhdr webp wmf xml zip zlib zstd'

    try:
        # Check if 'magick' command is available and get version
        result = subprocess.run(['/usr/local/bin/magick', '-version'], capture_output=True, text=True, check=True)
        assert expected_version in result.stdout, f"ImageMagick version does not match expected version: {expected_version}"
        assert expected_features in result.stdout, f"ImageMagick version does not have expected features: {expected_features}"
        assert expected_delegagtes in result.stdout, f"ImageMagick version does not have expected delegates: {expected_delegagtes}"
    except subprocess.CalledProcessError:
        pytest.fail("ImageMagick is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'magick' command not found.")

    # Check that 'magick' can be executed by 'corpus'
    try:
        subprocess.run(['su', '-s', '/bin/sh', '-c', '/usr/local/bin/magick -version > /dev/null', 'corpus'], check=True)
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute ImageMagick.")
    except FileNotFoundError:
        pytest.fail("'magick' command not found for user 'corpus'.")

def test_exiftool_installed_and_version():
    """Test that ExifTool is installed, executable, and the version is correct."""
    expected_version = '13.36'
    try:
        # Check if 'exiftool' command is available and get version
        result = subprocess.run(['/usr/local/bin/exiftool', '-ver'], capture_output=True, text=True, check=True)
        assert expected_version in result.stdout.strip(), f"ExifTool version does not match expected version: {expected_version}"
    except subprocess.CalledProcessError:
        pytest.fail("ExifTool is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'exiftool' command not found.")

    # Check that 'exiftool' can be executed by 'corpus'
    try:
        subprocess.run(['su', '-s', '/bin/sh', '-c', '/usr/local/bin/exiftool -ver > /dev/null', 'corpus'], check=True)
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute ExifTool.")
    except FileNotFoundError:
        pytest.fail("'exiftool' command not found for user 'corpus'.")

def test_ffmpeg_installed_and_version():
    """Test that ffmpeg is installed, executable, and the version is correct."""
    expected_version = '8.0.1'
    try:
        # Check if 'ffmpeg' command is available and get version
        result = subprocess.run(['/usr/local/bin/ffmpeg', '-version'], capture_output=True, text=True, check=True)
        assert expected_version in result.stdout, f"ffmpeg version does not match expected version: {expected_version}"
    except subprocess.CalledProcessError:
        pytest.fail("ffmpeg is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'ffmpeg' command not found.")

    # Check that 'ffmpeg' can be executed by 'corpus'
    try:
        subprocess.run(['su', '-s', '/bin/sh', '-c', '/usr/local/bin/ffmpeg -h > /dev/null', 'corpus'], check=True)
    except subprocess.CalledProcessError:
        pytest.fail("User 'corpus' cannot execute ffmpeg.")
    except FileNotFoundError:
        pytest.fail("'ffmpeg' command not found for user 'corpus'.")

def test_ffmpeg_libs():
    """Test that ffmpeg includes libs."""
    ffmpeg_additional_libs = [
        "libvpx",
        "libopus",
        "libx264",
        "libx265",
        "libdav1d"
    ]
    try:
        # Get 'ffmpeg' linked libaries
        result = subprocess.run(['/usr/bin/ldd', '/usr/local/bin/ffmpeg'], capture_output=True, text=True, check=True)
        for lib in ffmpeg_additional_libs:
            assert lib in result.stdout, f"ffmpeg version missing linked lib: {lib}"
    except subprocess.CalledProcessError:
        pytest.fail("ldd is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'ldd' command not found.")

def test_third_party_licenses_installed():
    """Test that the third-party-licenses.txt file exists."""
    if not os.path.exists('/third-party-licenses.txt'):
        pytest.fail("third-party-licenses.txt is not installed.")

import re

def test_corretto_jdk_installed():
    """Test that an Amazon Corretto JDK from the supported set is installed when present."""
    java_path = shutil.which('java')
    if java_path is None:
        pytest.skip("Corretto JDK not pre-installed in this image variant.")
    try:
        result = subprocess.run([java_path, '-version'], capture_output=True, text=True, check=True)
        combined_output = result.stderr + result.stdout  # Java version info is usually in stderr
        assert 'Corretto' in combined_output, "Amazon Corretto JDK not installed."
        match = re.search(r'version\s+"(\d+)', combined_output)
        assert match, "Unable to detect Java version."
        assert match.group(1) in {'11', '17', '21'}, "JDK major version is not within the supported set (11, 17, 21)."
    except subprocess.CalledProcessError:
        pytest.fail("Java is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'java' command not found.")
