import sys
# Add the directory containing entrypoint.py to the Python path
sys.path.append('/usr/local/bin')
import subprocess
import os
import tempfile
import entrypoint
from unittest import mock, TestCase
from unittest.mock import patch, mock_open
from xml.dom import minidom
import xml.etree.ElementTree as ET
import json

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
    assert '10.03.1' in result.stdout

def test_exiftool_installed():
    exiftool_path = path_map['exiftool'][1]
    result = subprocess.run([exiftool_path, '-ver'], capture_output=True, text=True)
    assert '12.85' in result.stdout

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

@patch('urllib3.PoolManager.request')
def test_handle_office_facility(mock_request):
    mock_facility = mock.Mock()
    mock_facility.find.return_value = mock.Mock()
    mock_response = mock.Mock()
    mock_response.status = 200
    mock_request.return_value = mock_response

    office_url = 'http://office.url'
    file_content = 'This is a test file content'

    entrypoint.handle_office_facility(mock_facility, office_url, file_content)

    mock_request.assert_called_once_with(
        'POST',
        office_url,
        body=mock.ANY,
        headers={'Content-Type': mock.ANY},
        timeout=10,
        retries=False
    )

class TestUpdateVolumesConfiguration(TestCase):
    @patch('builtins.open', new_callable=mock_open, read_data='''
<root>
  <hosts>
    <host name="localhost" compressionlevel="0" authentication-method="" url="rmi://localhost/corpus.RMIServer">
      <proxy use="0"/>
      <censhare-vfs use="0"/>
      <volumes>
        <volume filesystemname="assets" physicalurl="file:///opt/corpus/work/assets/" filestreaming="false"/>
        <volume filesystemname="assets-temp" physicalurl="file:///opt/corpus/work/assets-temp/" filestreaming="false"/>
        <volume filesystemname="temp" physicalurl="file:///opt/corpus/work/temp/" filestreaming="false"/>
      </volumes>
    </host>
    <host name="192.168.123.45" compressionlevel="0" authentication-method="" url="rmi://192.168.123.45/corpus.RMIServer">
      <proxy use="0"/>
      <volumes>
        <volume filesystemname="assets" physicalurl="file:///opt/corpus/work/assets/" filestreaming="true"/>
        <volume filesystemname="assets-temp" physicalurl="file:///opt/corpus/work/assets-temp/" filestreaming="true"/>
        <volume filesystemname="temp" physicalurl="file:///opt/corpus/work/temp/" filestreaming="true"/>
      </volumes>
    </host>
  </hosts>
</root>
    ''')
    @patch('entrypoint.ET.parse')
    def test_update_volumes_configuration(self, mock_parse, mock_file):
        # Setup the environment variable
        volumes_info = {
            "filesystem1": {"attr1": "value1", "attr2": True},
            "filesystem2": {"attr3": "value3", "attr4": False}
        }
        os.environ['VOLUMES_INFO'] = json.dumps(volumes_info)

        # Mock the XML structure
        mock_tree = ET.ElementTree(ET.fromstring('''
        <root>
          <hosts>
            <host name="localhost" compressionlevel="0" authentication-method="" url="rmi://localhost/corpus.RMIServer">
              <proxy use="0"/>
              <censhare-vfs use="0"/>
              <volumes>
                <volume filesystemname="assets" physicalurl="file:///opt/corpus/work/assets/" filestreaming="false"/>
                <volume filesystemname="assets-temp" physicalurl="file:///opt/corpus/work/assets-temp/" filestreaming="false"/>
                <volume filesystemname="temp" physicalurl="file:///opt/corpus/work/temp/" filestreaming="false"/>
              </volumes>
            </host>
            <host name="192.168.123.45" compressionlevel="0" authentication-method="" url="rmi://192.168.123.45/corpus.RMIServer">
              <proxy use="0"/>
              <volumes>
                <volume filesystemname="assets" physicalurl="file:///opt/corpus/work/assets/" filestreaming="true"/>
                <volume filesystemname="assets-temp" physicalurl="file:///opt/corpus/work/assets-temp/" filestreaming="true"/>
                <volume filesystemname="temp" physicalurl="file:///opt/corpus/work/temp/" filestreaming="true"/>
              </volumes>
            </host>
          </hosts>
        </root>
        '''))
        mock_parse.return_value = mock_tree

        # Call the function
        entrypoint.update_volumes_configuration('/mock/path/to/hosts.xml')

        # Verify the function's behavior
        mock_parse.assert_called_once_with('/mock/path/to/hosts.xml')

        # Verify the changes in the XML tree
        root_element = mock_tree.getroot()
        hosts_element = root_element.find('hosts')
        host1_volumes = hosts_element.find('.//host[@name="localhost"]/volumes')
        host2_volumes = hosts_element.find('.//host[@name="192.168.123.45"]/volumes')

        # Check that the new volumes elements are added correctly
        self.assertEqual(len(host1_volumes.findall('volume')), 2)
        self.assertEqual(len(host2_volumes.findall('volume')), 2)

        # Verify the attributes of the volumes in the first host
        volume1 = host1_volumes.find('volume[@filesystemname="filesystem1"]')
        self.assertIsNotNone(volume1)
        self.assertEqual(volume1.attrib['attr1'], 'value1')
        self.assertEqual(volume1.attrib['attr2'], 'true')

        volume2 = host1_volumes.find('volume[@filesystemname="filesystem2"]')
        self.assertIsNotNone(volume2)
        self.assertEqual(volume2.attrib['attr3'], 'value3')
        self.assertEqual(volume2.attrib['attr4'], 'false')

        # Verify the attributes of the volumes in the second host
        volume1 = host2_volumes.find('volume[@filesystemname="filesystem1"]')
        self.assertIsNotNone(volume1)
        self.assertEqual(volume1.attrib['attr1'], 'value1')
        self.assertEqual(volume1.attrib['attr2'], 'true')

        volume2 = host2_volumes.find('volume[@filesystemname="filesystem2"]')
        self.assertIsNotNone(volume2)
        self.assertEqual(volume2.attrib['attr3'], 'value3')
        self.assertEqual(volume2.attrib['attr4'], 'false')

        # Clean up the environment variable
        del os.environ['VOLUMES_INFO']
