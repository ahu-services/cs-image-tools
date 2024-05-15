import sys
# Add the directory containing entrypoint.py to the Python path
sys.path.append('/usr/local/bin')
import subprocess
import os
import pytest
import tempfile
import entrypoint
from unittest import mock
from unittest.mock import patch, mock_open

# Integration Tests
path_map = entrypoint.get_path_map()

def test_imagemagick_installed():
    convert_path = path_map['imagemagick'][1]
    result = subprocess.run([convert_path, '--version'], capture_output=True, text=True)
    assert 'ImageMagick' in result.stdout
    assert '7.1.1-32' in result.stdout
    assert 'Features: Cipher DPC HDRI Modules OpenMP(4.5)' in result.stdout
    assert 'Delegates (built-in): bzlib cairo djvu fftw fontconfig freetype gvc heic jbig jng jp2 jpeg lcms ltdl lzma openexr pangocairo png raqm raw rsvg tiff webp wmf xml zip zlib zstd' in result.stdout

def test_ghostscript_installed():
    gs_path = path_map['ghostscript'][1]
    result = subprocess.run([gs_path, '--version'], capture_output=True, text=True)
    assert '10.03.0' in result.stdout

def test_exiftool_installed():
    exiftool_path = path_map['exiftool'][1]
    result = subprocess.run([exiftool_path, '-ver'], capture_output=True, text=True)
    assert '12.84' in result.stdout

def test_ffmpeg_installed():
    ffmpeg_path = path_map['ffmpeg'][1]
    result = subprocess.run([ffmpeg_path, '-version'], capture_output=True, text=True)
    assert 'ffmpeg version 7.0' in result.stdout

def test_wkhtmltoimage_installed():
    wkhtmltoimage_path = path_map['wkhtmltoimage'][1]
    result = subprocess.run([wkhtmltoimage_path, '--version'], capture_output=True, text=True)
    assert 'wkhtmltoimage 0.12.6' in result.stdout

def test_pngquant_installed():
    pngquant_path = path_map['pngquant'][1]
    result = subprocess.run([pngquant_path, '--version'], capture_output=True, text=True)
    assert '2.18.0' in result.stdout

# Unit Tests
def test_get_path_map():
    path_map = entrypoint.get_path_map()
    assert 'imagemagick' in path_map
    assert path_map['imagemagick'] == ('@@CONVERT@@', '/usr/local/bin/convert', '@@COMPOSITE@@', '/usr/local/bin/composite')

@patch('requests.get')
@patch('tarfile.open')
@patch('subprocess.run')
def test_download_unpack(mock_subprocess, mock_tarfile, mock_requests):
    # Setup mocks
    mock_response = mock.Mock()
    mock_response.status_code = 200
    mock_response.iter_content = lambda chunk_size: [b'content']
    mock_requests.return_value = mock_response
    mock_tarfile_open = mock.Mock()
    mock_tarfile.__enter__.return_value = mock_tarfile_open

    with tempfile.TemporaryDirectory() as tmpdirname:
        output_path = os.path.join(tmpdirname, 'file.tar.gz')
        entrypoint.download_unpack('http://example.com/file.tar.gz', output_path)
        
        mock_requests.assert_called_once_with('http://example.com/file.tar.gz', stream=True)
        mock_tarfile.assert_called_once_with(output_path)
        mock_subprocess.assert_called_once_with(['chown', '-R', 'corpus:corpus', '/opt/corpus/'], check=True)
        assert os.path.exists(output_path)

@patch('shutil.copy2')
def test_setup_icc_profiles(mock_copy2):
    with tempfile.TemporaryDirectory() as tmpdirname:
        source_dir = os.path.join(tmpdirname, 'source')
        target_dir = os.path.join(tmpdirname, 'target')
        
        # Create the source directory and a test file
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(source_dir, 'test.icc'), 'w') as f:
            f.write('icc content')
        
        # Ensure directories are created
        assert os.path.exists(source_dir)
        assert not os.path.exists(target_dir)

        # Run the function to be tested
        entrypoint.setup_icc_profiles(source_dir, target_dir)
        
        # Check that the target directory was created and file was copied
        assert os.path.exists(target_dir)
        mock_copy2.assert_called_once_with(os.path.join(source_dir, 'test.icc'), os.path.join(target_dir, 'test.icc'))

@patch('subprocess.run')
def test_run_as_corpus(mock_subprocess):
    mock_result = mock.Mock()
    mock_result.stdout = "output"
    mock_result.returncode = 0
    mock_subprocess.return_value = mock_result

    result = entrypoint.run_as_corpus('echo Hello')
    mock_subprocess.assert_called_once_with('su - corpus -c "echo Hello"', shell=True, executable='/bin/bash', text=True, capture_output=True, check=True)
    assert result.stdout == "output"

@patch('entrypoint.ET.parse')
def test_configure_xml(mock_et_parse):
    mock_tree = mock.Mock()
    mock_root = mock.Mock()
    mock_tree.getroot.return_value = mock_root
    mock_et_parse.return_value = mock_tree

    mock_facilities = mock.Mock()
    mock_facilities.attrib = {'instances': ''}  # Ensure attrib supports item assignment
    mock_facility = mock.Mock()
    mock_facility.attrib = {'key': 'test'}
    mock_facilities.findall.return_value = [mock_facility]  # Ensure findall supports iteration and subscripting
    mock_root.find.return_value = mock_facilities

    os.environ['SVC_INSTANCES'] = '4'
    os.environ['OFFICE_URL'] = 'http://office.url'
    entrypoint.configure_xml('svc_host', 'svc_user')

    mock_et_parse.assert_called_once_with('/opt/corpus/censhare/censhare-Service-Client/config/.hosts/svc_host/serviceclient-preferences-svc_user.xml')
    mock_tree.write.assert_called_once_with('/opt/corpus/censhare/censhare-Service-Client/config/.hosts/svc_host/serviceclient-preferences-svc_user.xml')
    assert mock_root.find.call_count > 0
    assert mock_facilities.attrib['instances'] == '4'

@patch('urllib.request.urlopen')
def test_handle_office_facility(mock_urlopen):
    mock_facility = mock.Mock()
    mock_facility.find.return_value = mock.Mock()
    mock_response = mock.Mock()
    mock_response.status = 200
    mock_urlopen.return_value = mock_response

    entrypoint.handle_office_facility(mock_facility, 'http://office.url')

    mock_urlopen.assert_called_once_with('http://office.url', timeout=10)
    mock_facility.find.return_value.set.assert_called_once_with('port', 'http://office.url')