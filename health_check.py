import os
import re
import socket
import subprocess

# Paths to log files
service_log_path = "/opt/corpus/censhare/censhare-Service-Client/logs/service-client-internal-0.0.log"
DEFAULT_RMI_PORT = "30550"

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

def resolve_rmi_port():
    raw = os.getenv("SERVICECLIENT_RMI_PORT", DEFAULT_RMI_PORT)
    if str(raw).isdigit():
        return int(raw)
    print(f"Warning: SERVICECLIENT_RMI_PORT '{raw}' is not numeric; falling back to {DEFAULT_RMI_PORT}.")
    return int(DEFAULT_RMI_PORT)

def check_rmi_port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError as exc:
        print(f"RMI port {port} not reachable: {exc}")
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

    rmi_port = resolve_rmi_port()
    if not check_rmi_port_open(rmi_port):
        print(f"RMI port {rmi_port} not open.")
        return 1  # Indicate failure

    print("Service is healthy.")
    return 0  # Indicate success

if __name__ == "__main__":
    exit(health_check())
