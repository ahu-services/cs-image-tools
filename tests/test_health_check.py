import os
import sys
# Add the directory containing health_check.py to the Python path
sys.path.append('/usr/local/bin')
import re
import subprocess
import unittest
from unittest import mock
from unittest.mock import patch, mock_open

import health_check

class TestHealthCheck(unittest.TestCase):

    @patch('health_check.os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="INFO   : LoginAction: ServiceClientLoginAction: client token:\nINFO   : LoginAction: RMIProcessClient: created new RMIProcessClient 'ClientCLIService'")
    def test_check_log_file_successful(self, mock_file, mock_exists):
        mock_exists.return_value = True
        self.assertTrue(health_check.check_log_file(health_check.service_log_path, health_check.login_pattern))
        self.assertTrue(health_check.check_log_file(health_check.service_log_path, health_check.service_registration_pattern))

    @patch('health_check.os.path.exists')
    def test_check_log_file_not_exists(self, mock_exists):
        mock_exists.return_value = False
        self.assertFalse(health_check.check_log_file(health_check.service_log_path, health_check.login_pattern))
        self.assertFalse(health_check.check_log_file(health_check.service_log_path, health_check.service_registration_pattern))

    @patch('subprocess.run')
    def test_check_java_process_running(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 0
        self.assertTrue(health_check.check_java_process())

    @patch('subprocess.run')
    def test_check_java_process_not_running(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 1
        self.assertFalse(health_check.check_java_process())

    @patch('subprocess.run')
    def test_check_tcp_connection(self, mock_subprocess):
        mock_subprocess.return_value.stdout = b"ESTAB 0      0          192.168.1.2:30545       192.168.1.1:12345\n"
        self.assertTrue(health_check.check_tcp_connection())

    @patch('subprocess.run')
    def test_check_tcp_no_connection(self, mock_subprocess):
        mock_subprocess.return_value.stdout = b""
        self.assertFalse(health_check.check_tcp_connection())

    @patch('health_check.check_java_process')
    @patch('health_check.check_log_file')
    @patch('health_check.check_tcp_connection')
    def test_health_check_successful(self, mock_port, mock_log_file, mock_java_process):
        mock_java_process.return_value = True
        mock_log_file.side_effect = [True, True]
        mock_port.return_value = True

        self.assertEqual(health_check.health_check(), 0)

    @patch('health_check.check_java_process')
    @patch('health_check.check_log_file')
    @patch('health_check.check_tcp_connection')
    def test_health_check_failure(self, mock_port, mock_log_file, mock_java_process):
        mock_java_process.return_value = False
        mock_log_file.side_effect = [False, False]
        mock_port.return_value = False

        self.assertEqual(health_check.health_check(), 1)

if __name__ == '__main__':
    unittest.main()
