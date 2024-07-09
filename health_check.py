import os
import re
import subprocess

# Paths to log files
service_log_path = "/opt/corpus/censhare/censhare-Service-Client/logs/service-client-internal-0.0.log"

# Regex patterns for successful login and service registration
login_pattern = re.compile(r"INFO\s+: LoginAction: ServiceClientLoginAction: client token:")
service_registration_pattern = re.compile(r"INFO\s+: LoginAction: RMIProcessClient: created new RMIProcessClient 'ClientCLIService'")

def check_log_file(log_path, pattern):
    if os.path.exists(log_path):
        with open(log_path, 'r') as log_file:
            for line in log_file:
                if pattern.search(line):
                    return True
    return False

def check_java_process():
    try:
        result = subprocess.run(['pgrep', '-f', 'java'], stdout=subprocess.PIPE)
        return result.returncode == 0
    except Exception as e:
        print(f"Error checking Java process: {e}")
        return False

def check_tcp_connection():
    try:
        result = subprocess.run(['ss', '-tan'], stdout=subprocess.PIPE)
        # Check for established TCP connections
        return "ESTAB" in result.stdout.decode()
    except Exception as e:
        print(f"Error checking TCP connections: {e}")
        return False

def health_check():
    # Check if the Java process is running
    if not check_java_process():
        print("Java process not running.")
        return 1  # Indicate failure
    
    # Check for successful login
    if not check_log_file(service_log_path, login_pattern):
        print("No successful login found in logs.")
        return 1  # Indicate failure

    # Check for successful service registration
    if not check_log_file(service_log_path, service_registration_pattern):
        print("No successful service registration found in logs.")
        return 1  # Indicate failure

    # Check if there are established TCP connections
    if not check_tcp_connection():
        print("No established TCP connections found.")
        return 1  # Indicate failure

    print("Service is healthy.")
    return 0  # Indicate success

if __name__ == "__main__":
    exit(health_check())