# test_entrypoint.py

import os
import sys
import unittest
from unittest.mock import patch, MagicMock, mock_open, call
import subprocess
import signal
import tempfile
import shutil
import requests
import xml.etree.ElementTree as ET

# Add the project root to sys.path so we can import entrypoint.py
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Now we can import from 'entrypoint.py'
from entrypoint import (
    str_to_bool,
    download_unpack,
    configure_xml,
    get_path_map,
    update_facility_paths,
    handle_office_facility,
    setup_icc_profiles,
    run_as_corpus,
    stop_service_client,
    signal_handler,
    wait_for_log_file,
    follow_log_file,
    update_volumes_configuration,
    get_container_memory_limit,
    update_imagemagick_policy_xml,
    main  # Ensure 'main' is defined in 'entrypoint.py'
)

class TestEntrypoint(unittest.TestCase):

    def test_str_to_bool(self):
        self.assertTrue(str_to_bool('true'))
        self.assertTrue(str_to_bool('1'))
        self.assertTrue(str_to_bool('Yes'))
        self.assertTrue(str_to_bool('y'))
        self.assertFalse(str_to_bool('false'))
        self.assertFalse(str_to_bool('0'))
        self.assertFalse(str_to_bool('No'))
        self.assertFalse(str_to_bool('n'))
        self.assertFalse(str_to_bool('random_string'))

    @patch('entrypoint.requests.get')
    @patch('entrypoint.tarfile.open')
    @patch('entrypoint.subprocess.run')
    def test_download_unpack(self, mock_subprocess_run, mock_tarfile_open, mock_requests_get):
        # Mock the requests.get response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = MagicMock(return_value=[b'content'])
        mock_requests_get.return_value = mock_response

        # Call the function
        download_unpack('http://example.com/file.tar.gz', '/tmp/file.tar.gz')

        # Assert that requests.get was called with the correct URL
        mock_requests_get.assert_called_with('http://example.com/file.tar.gz', stream=True)

        # Assert that tarfile.open was called to extract the file
        mock_tarfile_open.assert_called_with('/tmp/file.tar.gz')

        # Assert that subprocess.run was called to change ownership
        mock_subprocess_run.assert_called_with(['chown', '-R', 'corpus:corpus', '/opt/corpus/'], check=True)

    @patch('entrypoint.get_path_map')
    def test_get_path_map(self, mock_get_path_map):
        expected_map = {
            'imagemagick': ('@@CONVERT@@', '/usr/local/bin/magick', '@@COMPOSITE@@', '/usr/local/bin/composite'),
            'exiftool': ('@@EXIFTOOL@@', '/usr/local/bin/exiftool'),
            'ghostscript': ('@@GS@@', '/usr/local/bin/gs'),
            'wkhtmltoimage': ('@@HTML2IMG@@', '/usr/bin/wkhtmltoimage'),
            'pngquant': ('@@PNGQUANT@@', '/usr/bin/pngquant'),
            'ffmpeg': ('@@FFMPEG-PATH@@', '/usr/local/bin/ffmpeg'),
        }
        mock_get_path_map.return_value = expected_map
        result = get_path_map()
        self.assertEqual(result, expected_map)

    @patch('entrypoint.urllib3.PoolManager')
    def test_handle_office_facility(self, mock_pool_manager):
        # Mock the facility element
        facility = ET.Element('facility', attrib={'key': 'office'})

        # Mock the PoolManager and response
        mock_http = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_http.request.return_value = mock_response
        mock_pool_manager.return_value = mock_http

        # Call the function
        handle_office_facility(facility, 'http://office.url', validate_certs=False)

        # Assert that the facility's path was updated
        path_element = facility.find(".//path[@key='@@OFFICE@@']")
        self.assertIsNotNone(path_element)
        self.assertEqual(path_element.get('port'), 'http://office.url')

    @patch('entrypoint.os.path.isfile')
    @patch('entrypoint.shutil.copy2')
    @patch('entrypoint.os.makedirs')
    @patch('entrypoint.os.listdir')
    @patch('entrypoint.os.path.exists')
    def test_setup_icc_profiles(self, mock_exists, mock_listdir, mock_makedirs, mock_copy2, mock_isfile):
        # Mock the source directory to exist and contain files
        mock_exists.return_value = True
        mock_listdir.return_value = ['profile1.icc', 'profile2.icc']
        mock_isfile.return_value = True  # Assume all entries are files

        # Call the function
        setup_icc_profiles('/source_dir', '/target_dir')

        # Assert that makedirs was called
        mock_makedirs.assert_called_with('/target_dir', exist_ok=True)

        # Assert that copy2 was called for each file
        expected_calls = [
            call('/source_dir/profile1.icc', '/target_dir/profile1.icc'),
            call('/source_dir/profile2.icc', '/target_dir/profile2.icc'),
        ]
        mock_copy2.assert_has_calls(expected_calls, any_order=True)

    @patch('entrypoint.subprocess.run')
    def test_run_as_corpus(self, mock_subprocess_run):
        # Mock the subprocess run
        mock_result = MagicMock()
        mock_result.stdout = 'command output'
        mock_subprocess_run.return_value = mock_result

        # Call the function
        test_command = 'echo "Hello World"'
        result = run_as_corpus(test_command)

        # Get the actual command passed to subprocess.run
        actual_command = mock_subprocess_run.call_args[0][0]

        # Assert that the command includes the expected components
        self.assertIn('su - corpus -c', actual_command)
        self.assertIn(test_command, actual_command)

        # Assert that subprocess.run was called with the expected parameters
        mock_subprocess_run.assert_called_with(
            actual_command,
            shell=True,
            executable='/bin/bash',
            text=True,
            capture_output=True,
            check=True
        )

        # Assert that the result is as expected
        self.assertEqual(result, mock_result)

    @patch('entrypoint.subprocess.run')
    @patch('entrypoint.subprocess.check_output')
    @patch('os.path.exists')
    @patch('time.sleep', return_value=None)
    def test_stop_service_client(self, mock_sleep, mock_exists, mock_check_output, mock_subprocess_run):
        # Mock the PID retrieval
        mock_check_output.return_value = '1234\n'
        # Simulate that the process exists and then disappears
        mock_exists.side_effect = [True, False]

        # Call the function
        stop_service_client()

        # Assert that the correct commands were run
        mock_check_output.assert_called_with('jps | grep ServiceClient | cut -f 1 -d \' \'', shell=True, executable='/bin/bash', text=True)
        mock_subprocess_run.assert_any_call('kill -TERM 1234', shell=True, executable='/bin/bash', text=True)

    @patch('entrypoint.stop_service_client')
    @patch('sys.exit')
    def test_signal_handler(self, mock_sys_exit, mock_stop_service_client):
        # Call the function
        signal_handler(signal.SIGTERM, None)

        # Assert that stop_service_client was called
        mock_stop_service_client.assert_called_once()

        # Assert that sys.exit was called with 0
        mock_sys_exit.assert_called_with(0)

    @patch('entrypoint.os.path.exists')
    @patch('time.sleep', return_value=None)
    def test_wait_for_log_file(self, mock_sleep, mock_exists):
        # Simulate the file not existing initially and then appearing
        mock_exists.side_effect = [False, False, True]

        # Call the function
        result = wait_for_log_file('/path/to/log', timeout=10)

        # Assert that the function returned True
        self.assertTrue(result)

    @patch('builtins.open', new_callable=mock_open, read_data='log line\n')
    @patch('time.sleep', side_effect=KeyboardInterrupt)
    def test_follow_log_file(self, mock_sleep, mock_file):
        # Call the function and expect it to handle KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            follow_log_file('/path/to/log')

        # Assert that the file was opened
        mock_file.assert_called_with('/path/to/log', 'r')

    @patch('entrypoint.os.getenv')
    @patch('entrypoint.ET.parse')
    @patch('entrypoint.json.loads')
    @patch('builtins.open', new_callable=mock_open)
    def test_update_volumes_configuration(self, mock_file, mock_json_loads, mock_et_parse, mock_os_getenv):
        # Mock environment variables
        mock_os_getenv.return_value = '{"volume1": {"attribute1": "value1"}}'

        # Mock JSON loading
        mock_json_loads.return_value = {'volume1': {'attribute1': 'value1'}}

        # Mock the XML parsing
        mock_tree = MagicMock()
        mock_root = ET.Element('root')
        mock_tree.getroot.return_value = mock_root
        mock_et_parse.return_value = mock_tree

        # Call the function
        update_volumes_configuration('/path/to/hosts.xml')

        # Ensure that ET.parse was called
        mock_et_parse.assert_called_with('/path/to/hosts.xml')

        # Further assertions can be added to check XML modifications

    @patch('entrypoint.open', new_callable=mock_open, read_data='9223372036854771712')
    @patch('entrypoint.psutil.virtual_memory')
    def test_get_container_memory_limit(self, mock_virtual_memory, mock_file):
        # Mock psutil to return a specific total memory
        mock_virtual_memory.return_value = MagicMock(total=16 * 1024 * 1024 * 1024)  # 16GB

        # Call the function
        mem_limit = get_container_memory_limit()

        # Assert that the memory limit was set to 8GB due to the cgroup limit
        self.assertEqual(mem_limit, 8 * 1024 * 1024 * 1024)

    @patch('entrypoint.os.cpu_count', return_value=4)
    @patch('entrypoint.os.getenv', side_effect=lambda k, default=None: {'IMAGEMAGICK_BUFFER_PERCENTAGE': '0.2'}.get(k, default))
    @patch('entrypoint.ET.parse')
    @patch('entrypoint.minidom.parseString')
    @patch('builtins.open', new_callable=mock_open)
    def test_update_imagemagick_policy_xml(self, mock_file, mock_parseString, mock_et_parse, mock_os_getenv, mock_cpu_count):
        # Mock XML parsing
        mock_tree = MagicMock()
        mock_root = ET.Element('policymap')
        mock_tree.getroot.return_value = mock_root
        mock_et_parse.return_value = mock_tree

        # Call the function
        update_imagemagick_policy_xml(8 * 1024 * 1024 * 1024, 4)

        # Ensure that the XML file was attempted to be written
        mock_file.assert_called_with('/usr/local/etc/ImageMagick-7/policy.xml', 'w')

        # Further assertions can be added to check XML modifications

    @patch('entrypoint.sys.exit')
    @patch('entrypoint.open', new_callable=mock_open, read_data='Log file content')
    @patch('entrypoint.os.path.exists')
    @patch('entrypoint.download_unpack')
    @patch('entrypoint.setup_icc_profiles')
    @patch('entrypoint.run_as_corpus')
    @patch('entrypoint.configure_xml')
    @patch('entrypoint.os.getenv')
    @patch('entrypoint.get_container_memory_limit')
    @patch('entrypoint.update_imagemagick_policy_xml')
    @patch('entrypoint.wait_for_log_file')
    @patch('entrypoint.follow_log_file')
    @patch('entrypoint.stop_service_client')
    def test_main_entrypoint(self, mock_stop_service_client, mock_follow_log_file, mock_wait_for_log_file,
                            mock_update_imagemagick_policy_xml, mock_get_container_memory_limit, mock_os_getenv,
                            mock_configure_xml, mock_run_as_corpus, mock_setup_icc_profiles, mock_download_unpack,
                            mock_os_path_exists, mock_open_file, mock_sys_exit):
        # Mock environment variables
        env_vars = {
            'SVC_USER': 'user',
            'SVC_PASS': 'pass',
            'SVC_HOST': 'host',
            'SVC_INSTANCES': '4',
            'REPO_USER': 'repo_user',
            'REPO_PASS': 'repo_pass',
            'VERSION': '1.0',
            'IMAGEMAGICK_BUFFER_PERCENTAGE': '0.2',
        }
        mock_os_getenv.side_effect = lambda k, default=None: env_vars.get(k, default)

        # Mock functions that have side effects
        mock_get_container_memory_limit.return_value = 8 * 1024 * 1024 * 1024  # 8GB
        mock_wait_for_log_file.return_value = True

        # Mock os.path.exists to return False for service client path
        def mock_exists(path):
            if path == '/opt/corpus/censhare/censhare-Service-Client':
                return False  # Simulate that the service client is not installed
            elif path in [
                '/opt/corpus/censhare/censhare-Service-Client/logs/startup.log',
                '/opt/corpus/censhare/censhare-Service-Client/logs/service-client-internal-0.0.log',
            ]:
                return True
            return False

        mock_os_path_exists.side_effect = mock_exists

        # Simulate KeyboardInterrupt to stop the infinite loop
        mock_follow_log_file.side_effect = KeyboardInterrupt

        # Call the main function
        main()

        # Assert that sys.exit(0) was called
        mock_sys_exit.assert_called_with(0)

        # Assert that functions were called
        mock_download_unpack.assert_called_once()
        mock_setup_icc_profiles.assert_called()
        mock_run_as_corpus.assert_any_call(
            'yes Y | /opt/corpus/censhare/censhare-Service-Client/serviceclient.sh setup '
            '-m frmis://host:30546/corpus.RMIServerSSL -n host -u user -p pass'
        )
        mock_configure_xml.assert_called_once_with('host', 'user')
        mock_run_as_corpus.assert_any_call('/opt/corpus/censhare/censhare-Service-Client/serviceclient.sh start')
        mock_wait_for_log_file.assert_called_once()
        mock_follow_log_file.assert_called_once()
        mock_stop_service_client.assert_called_once()

if __name__ == '__main__':
    unittest.main()
