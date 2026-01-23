import os
import platform
import re
import shutil
import shlex
import time
import requests
import tarfile
import sys
import subprocess
import signal
import xml.etree.ElementTree as ET
from xml.dom import minidom
import urllib3
from urllib3.exceptions import HTTPError
import json
import hashlib
import socket

JAVA_WINDOWS = [
    (202201, 11),
    (202403, 17),
]
JAVA_DEFAULT = 21

CLIENT_VERSION_FILE = "/opt/corpus/censhare/client-version.txt"
SERVICECLIENT_SCRIPT = "/opt/corpus/censhare/censhare-Service-Client/serviceclient.sh"
DEFAULT_RMI_PORT = "30550"
RMI_HOST_OPTION_PATTERN = re.compile(r"-Djava\.rmi\.server\.hostname=([^\s]+)")

def _determine_serviceclient_version(script_path=SERVICECLIENT_SCRIPT):
    """
    Extract the service client version marker from the serviceclient.sh script.
    """
    version_pattern = re.compile(r'-Dcenshare\.serviceclient\.version=([\w\.\-]+)')
    try:
        with open(script_path, 'r', encoding='utf-8') as handle:
            for line in handle:
                match = version_pattern.search(line)
                if match:
                    return match.group(1)
    except OSError as exc:
        print(f"Warning: Unable to read {script_path} for version detection: {exc}")
    return None

def str_to_bool(value):
    """
    Convert a string to a boolean.
    Returns True for 'true', '1', 't', 'y', 'yes' (case insensitive).
    Returns False for 'false', '0', 'f', 'n', 'no' (case insensitive).
    Defaults to False for any other value.
    """
    return value.lower() in ['true', '1', 't', 'y', 'yes']

def detect_rmi_host_ip():
    """
    Best-effort detection of the IP the JVM will embed in RMI stubs.
    Tries hostname resolution (InetAddress.getLocalHost equivalent) first,
    then falls back to the default route source address.
    """
    candidates = []
    try:
        hostname = socket.gethostname()
        _, _, addrs = socket.gethostbyname_ex(hostname)
        candidates.extend(addrs)
    except socket.gaierror:
        pass

    try:
        route_cmd = "ip -4 route get 1 2>/dev/null | awk '{print $7; exit}'"
        route_ip = subprocess.check_output(
            route_cmd,
            shell=True,
            text=True,
            executable='/bin/bash',
        ).strip()
        if route_ip:
            candidates.append(route_ip)
    except subprocess.SubprocessError:
        pass

    for ip in candidates:
        if ip and not ip.startswith("127."):
            return ip
    return None

def apply_rmi_callback_host(callback_host):
    """
    Ensure SERVICECLIENT_JAVA_OPTIONS contains the desired RMI hostname.
    Priority: explicit SERVICECLIENT_CALLBACK_HOST, existing JAVA options,
    then autodetected host IP.
    """
    java_opts = os.getenv('SERVICECLIENT_JAVA_OPTIONS', '').strip()
    existing_match = RMI_HOST_OPTION_PATTERN.search(java_opts)
    existing_host = existing_match.group(1) if existing_match else None

    desired_host = callback_host or existing_host or detect_rmi_host_ip()
    if not desired_host:
        print("Warning: Unable to determine callback host for SERVICECLIENT_JAVA_OPTIONS.")
        return

    cleaned_opts = RMI_HOST_OPTION_PATTERN.sub('', java_opts).strip()
    rmi_option = f"-Djava.rmi.server.hostname={desired_host}"
    combined_opts = " ".join(part for part in [cleaned_opts, rmi_option] if part)
    os.environ['SERVICECLIENT_JAVA_OPTIONS'] = combined_opts

    if callback_host:
        source = "SERVICECLIENT_CALLBACK_HOST"
    elif existing_host:
        source = "existing SERVICECLIENT_JAVA_OPTIONS"
    else:
        source = "detected host IP"
    print(f"Configured SERVICECLIENT_JAVA_OPTIONS ({source}): {combined_opts}")

def download_unpack(url, output_path):
    """
    Downloads and unpacks a tar.gz file from a given URL to /opt/corpus directory.
    Change owner and group for the unpacked files to "corpus".

    Args:
    url (str): URL to fetch the tar.gz from.
    output_path (str): Local path to save the tar.gz file.

    Raises:
    SystemExit: If the download fails or the HTTP status is not 200.
    """
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        hashers = {
            'md5': hashlib.md5(),
            'sha256': hashlib.sha256(),
        }
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                for hasher in hashers.values():
                    hasher.update(chunk)
        print("Download complete.")
        print("Archive checksums:")
        for name, hasher in hashers.items():
            print(f"  {name.upper()}: {hasher.hexdigest()}")
        print("Unpacking...")
        with tarfile.open(output_path) as tar:
            try:
                tar.extractall(path="/opt/corpus/", filter="data")
            except TypeError:
                # Fallback for older Python versions without the filter argument.
                tar.extractall(path="/opt/corpus/")
        print("Unpacking complete.")
        subprocess.run(['chown', '-R', 'corpus:corpus', '/opt/corpus/'], check=True)
        detected_version = _determine_serviceclient_version()
        if detected_version:
            print(f"Installed service client version: {detected_version}")
        else:
            print("Warning: Could not determine installed service client version from serviceclient.sh.")
    else:
        print("Failed to download the file.")
        sys.exit(1)

def select_jdk_major(client_version):
    """
    Chooses the JDK major version for the given client version.
    """
    if not client_version:
        print(f"Warning: censhare Service-Client version unknown, defaulting to JDK {JAVA_DEFAULT}.")
        return JAVA_DEFAULT
    parts = client_version.split('.')
    if len(parts) < 2:
        print(f"Warning: Unexpected version format '{client_version}', defaulting to JDK {JAVA_DEFAULT}.")
        return JAVA_DEFAULT
    try:
        major = int(parts[0])
        minor = int(parts[1])
    except ValueError:
        print(f"Warning: Unable to parse version '{client_version}', defaulting to JDK {JAVA_DEFAULT}.")
        return JAVA_DEFAULT

    release_key = major * 100 + minor
    for upper_bound, jdk in JAVA_WINDOWS:
        if release_key <= upper_bound:
            return jdk
    return JAVA_DEFAULT

def store_client_version(version):
    if not version:
        return
    try:
        os.makedirs(os.path.dirname(CLIENT_VERSION_FILE), exist_ok=True)
        with open(CLIENT_VERSION_FILE, 'w') as handle:
            handle.write(version)
    except OSError as exc:
        print(f"Warning: Unable to persist client version to {CLIENT_VERSION_FILE}: {exc}")

def ensure_corretto(jdk_major):
    """
    Installs (or reuses) the Corretto release that matches the requested major version.
    """
    def configure_java_environment(java_binary):
        java_home = os.path.dirname(os.path.dirname(os.path.realpath(java_binary)))
        os.environ['JAVA_HOME'] = java_home
        os.environ['JDK_HOME'] = java_home
        os.environ['PATH'] = f"{os.path.join(java_home, 'bin')}:{os.environ.get('PATH', '')}"
        try:
            subprocess.run(['update-alternatives', '--set', 'java', java_binary], check=False)
            javac_binary = os.path.join(java_home, 'bin', 'javac')
            if os.path.exists(javac_binary):
                subprocess.run(['update-alternatives', '--set', 'javac', javac_binary], check=False)
        except FileNotFoundError:
            print("update-alternatives not available; skipping alternative configuration.")

    java_binary = shutil.which('java')
    current_major = None
    if java_binary:
        try:
            result = subprocess.run([java_binary, '-version'], capture_output=True, text=True, check=True)
            match = re.search(r'version\s+"(\d+)', result.stderr + result.stdout)
            if match:
                current_major = int(match.group(1))
        except (subprocess.CalledProcessError, ValueError):
            current_major = None

    if current_major == jdk_major:
        print(f"Using existing Corretto JDK {jdk_major}.")
        configure_java_environment(java_binary)
        return

    arch_lookup = {
        "x86_64": "x64",
        "aarch64": "aarch64",
    }
    machine = platform.machine()
    try:
        arch = arch_lookup[machine]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported architecture for Corretto JDK: {machine}") from exc

    url = f"https://corretto.aws/downloads/latest/amazon-corretto-{jdk_major}-{arch}-linux-jdk.deb"
    deb_path = f"/tmp/amazon-corretto-{jdk_major}.deb"
    print(f"Installing Corretto JDK {jdk_major} from {url}...")
    subprocess.run(['wget', '-q', '-O', deb_path, url], check=True)
    try:
        subprocess.run(['dpkg', '-i', deb_path], check=True)
    except subprocess.CalledProcessError:
        subprocess.run(['apt-get', 'update'], check=True)
        subprocess.run(['apt-get', '-y', '-f', 'install'], check=True)
        subprocess.run(['dpkg', '-i', deb_path], check=True)
    finally:
        if os.path.exists(deb_path):
            os.remove(deb_path)

    java_binary = shutil.which('java')
    if java_binary:
        configure_java_environment(java_binary)
    else:
        print("Warning: Java binary not found after installation.")

def configure_xml(svc_host, svc_user, base_dir="/opt/corpus/censhare/censhare-Service-Client"):
    """
    Updates XML configuration for the service client based on environment variables.

    Args:
    svc_host (str): Hostname of the service.
    svc_user (str): Username for service authentication.
    base_dir (str): Base path of the Service-Client installation.

    Note:
    Facility-specific timeouts can be set via environment variables, e.g.:
      FFMPEG_TIMEOUT=1800
      VIDEO_TIMEOUT=1800
    """
    # General service configuration
    svc_instances = os.getenv('SVC_INSTANCES', '4')
    office_url = os.getenv('OFFICE_URL', '')
    callback_host = os.getenv('SERVICECLIENT_CALLBACK_HOST', '').strip()
    if callback_host:
        print(f"Callback host requested via SERVICECLIENT_CALLBACK_HOST: {callback_host}")
    apply_rmi_callback_host(callback_host)

    rmi_port_raw = os.getenv('SERVICECLIENT_RMI_PORT', DEFAULT_RMI_PORT)
    if str(rmi_port_raw).isdigit():
        rmi_port = str(rmi_port_raw)
    else:
        print(f"Warning: SERVICECLIENT_RMI_PORT '{rmi_port_raw}' is not numeric. Falling back to {DEFAULT_RMI_PORT}.")
        rmi_port = DEFAULT_RMI_PORT

    rmi_port_to_raw = os.getenv('SERVICECLIENT_RMI_PORT_TO', '').strip()
    if rmi_port_to_raw:
        if rmi_port_to_raw.isdigit():
            rmi_port_to = rmi_port_to_raw
        else:
            print(f"Warning: SERVICECLIENT_RMI_PORT_TO '{rmi_port_to_raw}' is not numeric. Falling back to {rmi_port}.")
            rmi_port_to = rmi_port
    else:
        rmi_port_to = rmi_port
    print(f"Configuring Service-Client server port range {rmi_port}-{rmi_port_to}.")

    # Connection details
    client_map_host_from = os.getenv('CLIENT_MAP_HOST_FROM', '').strip()
    client_map_host_to = os.getenv('CLIENT_MAP_HOST_TO', '').strip()
    client_map_port_from = os.getenv('CLIENT_MAP_PORT_FROM', '').strip()
    client_map_port_to = os.getenv('CLIENT_MAP_PORT_TO', '').strip()

    if client_map_port_from and not client_map_port_to:
        client_map_port_to = client_map_port_from
    if client_map_port_to and not client_map_port_from:
        client_map_port_from = client_map_port_to

    # XML file path
    path = f"{base_dir}/config/.hosts/{svc_host}/serviceclient-preferences-{svc_user}.xml"
    tree = ET.parse(path)
    root = tree.getroot()

    # Update connection settings (force port-range to keep listeners in a fixed window)
    connection = root.find('.//connection[@type="port-range"]') or root.find('.//connection[@type="standard"]')
    if connection is not None:
        connection.set('type', 'port-range')
        connection.set('client-map-host-from', client_map_host_from)
        connection.set('client-map-host-to', client_map_host_to)
        connection.set('client-map-port-from', client_map_port_from)
        connection.set('client-map-port-to', client_map_port_to)
        connection.set('server-port-range-from', rmi_port)
        connection.set('server-port-range-to', rmi_port_to)
    else:
        print("Warning: No <connection> element found in serviceclient preferences.")

    # Update facilities instances
    facilities = root.find(".//facilities")
    facilities.attrib['instances'] = svc_instances

    # Optional: Override timeouts via environment variables
    for facility in facilities.findall('.//facility'):
        key = facility.attrib['key']
        timeout_env_var = os.getenv(f'{key.upper()}_TIMEOUT')
        if timeout_env_var:
            facility.set('timeout', timeout_env_var)

    # Update paths and other settings for each facility
    for facility in facilities.findall('.//facility'):
        key = facility.attrib['key']
        update_facility_paths(facility, key, office_url)

    update_volumes_configuration(f"{base_dir}/config/hosts.xml")

    tree.write(path)
    print("XML configuration updated.")

def get_path_map():
    return {
        'imagemagick': ('@@CONVERT@@', '/usr/local/bin/magick', '@@COMPOSITE@@', '/usr/local/bin/composite'),
        'exiftool': ('@@EXIFTOOL@@', '/usr/local/bin/exiftool'),
        'ghostscript': ('@@GS@@', '/usr/local/bin/gs'),
        'wkhtmltoimage': ('@@HTML2IMG@@', '/usr/local/bin/wkhtmltoimage'),
        'pngquant': ('@@PNGQUANT@@', '/usr/local/bin/pngquant'),
        'ffmpeg': ('@@FFMPEG-PATH@@', '/usr/local/bin/ffmpeg'),
    }

def update_facility_paths(facility, key, office_url):
    """
    Helper function to update path and enabled attributes in XML for specific facilities.
    This function uses environment variables to adjust settings dynamically.

    Args:
    facility (ET.Element): XML element that contains facility configuration.
    key (str): Facility key to determine which paths to update.
    """
    path_map = get_path_map()

    # Update paths based on facility key
    if key in path_map:
        paths = path_map[key]
        target_paths = []
        for i in range(0, len(paths), 2):
            path_element = facility.find(f".//path[@key='{paths[i]}']")
            if path_element is not None:
                path_element.set('path', paths[i + 1])
            else:
                # If the path element doesn't exist, create it
                ET.SubElement(facility, 'path', {'key': paths[i], 'path': paths[i + 1]})
            target_paths.append(paths[i + 1])
        print(f"Updated paths for facility '{key}'.")

        if key != "office" and target_paths:
            binaries_exist = all(os.path.exists(path) and os.access(path, os.X_OK) for path in target_paths)
            if binaries_exist and facility.get('enabled') != 'true':
                facility.set('enabled', 'true')
                print(f"Enabled facility '{key}' (binaries present).")

    # Handle specific facilities like 'office'
    if key == "office":
        office_validate_certs = str_to_bool(os.getenv('OFFICE_VALIDATE_CERTS', 'true'))
        handle_office_facility(facility, office_url, validate_certs=office_validate_certs)

def handle_office_facility(facility, office_url, validate_certs=True):
    """
    Configures the office facility based on the availability of a given URL.
    Args:
    facility (ET.Element): XML element that contains office facility configuration.
    office_url (str): URL to test for office service availability.
    validate_certs (bool): Whether to validate SSL certificates.
    """
    if office_url:
        try:
            http = urllib3.PoolManager(cert_reqs='CERT_REQUIRED' if validate_certs else 'CERT_NONE')
            # Create a Multipart Encoder
            fields = {
                'file': ('test.txt', "foobar", 'text/plain')
            }

            # Encode the fields
            encoded_fields = urllib3.filepost.encode_multipart_formdata(fields)
            body, content_type = encoded_fields

            headers = {
                'Content-Type': content_type
            }

            response = http.request(
                'POST',
                office_url,
                body=body,
                headers=headers,
                timeout=10,
                retries=False
            )

            if response.status == 200:
                path_element = facility.find(".//path[@key='@@OFFICE@@']")
                if path_element is not None:
                    path_element.set('port', office_url)
                else:
                    ET.SubElement(facility, 'path', {'key': '@@OFFICE@@', 'port': office_url})
                print(f"Successfully tested {office_url}, facility enabled.")
            else:
                raise Exception(f"Non-200 status code received: {response.status}")
        except HTTPError as e:
            print(f"Failed to connect to OFFICE_URL: {e}")
            facility.set('enabled', 'false')
        except Exception as e:
            print(f"Unexpected error: {e}")
            facility.set('enabled', 'false')
    else:
        facility.set('enabled', 'false')

def setup_icc_profiles(source_dir, target_dir):
    """
    Checks if a custom ICC profiles directory exists, and if it does,
    copies all ICC profiles from the source directory to the target directory.

    Args:
    source_dir (str): The source directory where ICC profiles are stored.
    target_dir (str): The target directory within the application where ICC profiles should be copied.
    """
    # Check if the source directory exists and has files
    if os.path.exists(source_dir) and os.listdir(source_dir):
        # Ensure the target directory exists; create if it doesn't
        os.makedirs(target_dir, exist_ok=True)
        
        # Copy each file from the source to the target directory
        for filename in os.listdir(source_dir):
            source_file = os.path.join(source_dir, filename)
            target_file = os.path.join(target_dir, filename)
            if os.path.isfile(source_file):
                shutil.copy2(source_file, target_file)
        print(f"Copied ICC profiles from {source_dir} to {target_dir}")
    else:
        print(f"No ICC profiles found in {source_dir} or directory does not exist.")

def run_as_corpus(command, input_data=None):
    """
    Executes a given command as 'corpus' user and captures the output.

    Args:
    command (List[str]): Command arguments to execute.
    input_data (str): Optional stdin data to pass to the process.

    Returns:
    subprocess.CompletedProcess: The result object including stdout, stderr, and exit status.
    """
    def _run(cmd):
        return subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=True,
            input=input_data,
        )

    try:
        result = _run(['runuser', '-u', 'corpus', '--', *command])
    except FileNotFoundError:
        try:
            quoted_command = shlex.join(command)
            result = _run(['su', '-s', '/bin/bash', 'corpus', '-c', quoted_command])
        except subprocess.CalledProcessError as e:
            print(f"Command failed with exit status {e.returncode}", file=sys.stderr)
            print(e.stderr, file=sys.stderr)
            return e
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit status {e.returncode}", file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        return e

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result

def stop_service_client():
    """
    Stops the censhare service client by gracefully terminating the Java process.
    """
    print("Stopping the censhare service client...")

    # Find the process ID of the ServiceClient
    pid_command = "jps | grep ServiceClient | cut -f 1 -d ' '"
    try:
        pid = subprocess.check_output(pid_command, shell=True, executable='/bin/bash', text=True).strip()
    except subprocess.CalledProcessError:
        pid = ''

    if pid:
        # Send SIGTERM to the process
        stop_command = f"kill -TERM {pid}"
        subprocess.run(stop_command, shell=True, executable='/bin/bash', text=True)

        # Wait for the process to terminate
        timeout = 120
        while timeout > 0:
            if not os.path.exists(f"/proc/{pid}"):
                print("Service client stopped.")
                break
            time.sleep(1)
            timeout -= 1

        if timeout == 0:
            print("Timeout reached. Forcefully terminating the service client...")
            subprocess.run(f"kill -9 {pid}", shell=True, executable='/bin/bash', text=True)
            print("Service client forcefully stopped.")
    else:
        print("No ServiceClient process found.")

def signal_handler(sig, frame):
    """
    Handles incoming signals, specifically SIGTERM, to stop services gracefully.

    Args:
    sig (int): The signal number.
    frame (frame object): The current stack frame.
    """
    print("SIGTERM received, stopping services...")
    stop_service_client()
    sys.exit(0)

def wait_for_log_file(log_file_path, timeout=60):
    """
    Waits for a log file to become available within a specified timeout.

    Args:
    log_file_path (str): Path to the log file.
    timeout (int): Maximum time to wait for the log file in seconds.

    Returns:
    bool: True if the log file is found, False if not.
    """
    start_time = time.time()
    while not os.path.exists(log_file_path):
        if (time.time() - start_time) > timeout:
            print(f"Timeout waiting for log file {log_file_path}")
            return False
        print(f"Waiting for log file {log_file_path} to appear...")
        time.sleep(5)  # wait for 5 seconds before checking again
    print(f"Log file {log_file_path} found.")
    return True

def follow_log_file(log_file_path):
    """
    Continuously reads and prints lines from a log file, similar to 'tail -f'.

    Args:
    log_file_path (str): Path to the log file to follow.
    """
    with open(log_file_path, 'r') as log_file:
        while True:
            line = log_file.readline()
            if not line:
                time.sleep(0.1)  # Sleep briefly to avoid busy loop
                continue
            print(line.strip(), flush=True)

def update_volumes_configuration(hosts_xml_path):
    """
    Updates the volumes configuration in the hosts.xml file based on provided environment variable.

    Args:
    hosts_xml_path (str): Path to the hosts.xml file.
    """
    volumes_info = os.getenv('VOLUMES_INFO')
    if not volumes_info:
        print("VOLUMES_INFO environment variable is not set.")
        return

    try:
        volumes_info = json.loads(volumes_info)
    except json.JSONDecodeError:
        print("VOLUMES_INFO environment variable is not valid JSON.")
        return

    tree = ET.parse(hosts_xml_path)
    root = tree.getroot()

    # Find and remove existing volumes elements
    for host in root.findall('.//host'):
        for volumes in host.findall('volumes'):
            host.remove(volumes)

        # Ensure <censhare-vfs use="0"/> element is present
        if host.find('censhare-vfs') is None:
            ET.SubElement(host, 'censhare-vfs', {'use': '0'})

        # Add new volumes element to the host
        volumes_element = ET.SubElement(host, 'volumes')
        for fs_name, attributes in volumes_info.items():
            volume_element = ET.SubElement(volumes_element, 'volume')
            volume_element.set('filesystemname', fs_name)
            for attr_key, attr_value in attributes.items():
                if isinstance(attr_value, bool):
                    attr_value = str(attr_value).lower()
                volume_element.set(attr_key, str(attr_value))

    # Pretty-print the XML
    xml_str = ET.tostring(root, encoding='utf-8')
    parsed = minidom.parseString(xml_str)
    pretty_xml_str = parsed.toprettyxml(indent="  ")

    with open(hosts_xml_path, 'w') as f:
        f.write(pretty_xml_str)
    
    print("Volumes configuration updated.")

if __name__ == "__main__":
    # Stop censhare Client on SIGTERM
    signal.signal(signal.SIGTERM, signal_handler)

    # Environment variables
    client_version_env = os.getenv("VERSION")
    svc_user = os.getenv("SVC_USER")
    svc_pass = os.getenv("SVC_PASS")
    svc_host = os.getenv("SVC_HOST")
    if not all([svc_user, svc_pass, svc_host]):
        print("Required variables (SVC_USER, SVC_PASS, SVC_HOST) are not set.")
        sys.exit(1)    

    # Check if service client is pre-installed
    client_installed = os.path.exists("/opt/corpus/censhare/censhare-Service-Client")
    client_version = None
    if not client_installed:
        repo_user = os.getenv("REPO_USER")
        repo_pass = os.getenv("REPO_PASS")
        version = client_version_env
        if not all([repo_user, repo_pass, version]):
            print("Service client not pre-installed and required variables (REPO_USER, REPO_PASS, VERSION) are not set.")
            sys.exit(1)

        download_url = f"https://{repo_user}:{repo_pass}@rpm.censhare.com/censhare-release/censhare-Service/v{version}/Shell/censhare-Service-Client-v{version}.tar.gz"
        download_unpack(download_url, "/tmp/censhare-client.tar.gz")
        store_client_version(version)
        client_version = version
    elif client_version_env:
        client_version = client_version_env
        store_client_version(client_version)
    elif os.path.exists(CLIENT_VERSION_FILE):
        try:
            with open(CLIENT_VERSION_FILE, 'r') as handle:
                client_version = handle.read().strip()
        except OSError:
            client_version = None

    required_jdk_major = select_jdk_major(client_version)
    ensure_corretto(required_jdk_major)

    # Install custom iccprofiles if provided in build
    icc_source = "/build_iccprofiles"
    icc_target = "/opt/corpus/censhare/censhare-Service-Client/iccprofiles"
    setup_icc_profiles(icc_source, icc_target)       
    # Install custom iccprofiles if mounted
    icc_source = "/iccprofiles"
    icc_target = "/opt/corpus/censhare/censhare-Service-Client/iccprofiles"
    setup_icc_profiles(icc_source, icc_target) 

    # Run setup and start commands
    setup_command = [
        "/opt/corpus/censhare/censhare-Service-Client/serviceclient.sh",
        "setup",
        "-m",
        f"frmis://{svc_host}:30546/corpus.RMIServerSSL",
        "-n",
        svc_host,
        "-u",
        svc_user,
        "-p",
        svc_pass,
    ]
    start_command = [
        "/opt/corpus/censhare/censhare-Service-Client/serviceclient.sh",
        "start",
    ]
    run_as_corpus(setup_command, input_data="Y\n" * 10)
    configure_xml(svc_host, svc_user)
    run_as_corpus(start_command)

    # Log output handling
    startup_log_path = "/opt/corpus/censhare/censhare-Service-Client/logs/startup.log"
    service_log_path = "/opt/corpus/censhare/censhare-Service-Client/logs/service-client-internal-0.0.log"
    with open(startup_log_path, "r") as file:
        print(file.read())
    if wait_for_log_file(service_log_path):
        # Log output handling
        try:
            # Continuous log file following or other long-running tasks here
            follow_log_file(service_log_path)
        except KeyboardInterrupt:
            print("Interrupted by user, stopping services...")
            stop_service_client()
            sys.exit(0)
