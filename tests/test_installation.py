# test_installation.py

import pytest
import subprocess
import os
import pwd

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
    expected_version = '10.04.0'
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
    expected_version = '7.1.1-41'
    expected_features = 'Features: Cipher DPC HDRI Modules OpenMP(4.5)'
    expected_delegagtes = 'Delegates (built-in): bzlib cairo djvu fftw fontconfig freetype gvc heic jbig jng jp2 jpeg lcms ltdl lzma openexr pangocairo png raqm raw rsvg tiff webp wmf xml zip zlib zstd'
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
    expected_version = '13.00'
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
    expected_version = '7.1'
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

def test_third_party_licenses_installed():
    """Test that the third-party-licenses.txt file exists."""
    if not os.path.exists('/third-party-licenses.txt'):
        pytest.fail("third-party-licenses.txt is not installed.")

def test_jdk17_corretto_installed():
    """Test that Amazon Corretto JDK 17 is installed."""
    try:
        result = subprocess.run(['java', '-version'], capture_output=True, text=True, check=True)
        output = result.stderr  # Java version info is sent to stderr
        assert 'Corretto' in output, "Amazon Corretto JDK not installed."
        assert '17' in output, "JDK version is not 17."
    except subprocess.CalledProcessError:
        pytest.fail("Java is not installed or cannot be executed.")
    except FileNotFoundError:
        pytest.fail("'java' command not found.")
