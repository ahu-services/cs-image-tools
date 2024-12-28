import os
import shutil
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
import psutil  # New import for memory detection

def str_to_bool(value):
    """
    Convert a string to a boolean.
    Returns True for 'true', '1', 't', 'y', 'yes' (case insensitive).
    Returns False for 'false', '0', 'f', 'n', 'no' (case insensitive).
    Defaults to False for any other value.
    """
    return value.lower() in ['true', '1', 't', 'y', 'yes']

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
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print("Download complete. Unpacking...")
        with tarfile.open(output_path) as tar:
            tar.extractall(path="/opt/corpus/")
        print("Unpacking complete.")
        subprocess.run(['chown', '-R', 'corpus:corpus', '/opt/corpus/'], check=True)
    else:
        print("Failed to download the file.")
        sys.exit(1)

def configure_xml(svc_host, svc_user):
    """
    Updates XML configuration for the service client based on environment variables.

    Args:
    svc_host (str): Hostname of the service.
    svc_user (str): Username for service authentication.
    """
    # General service configuration
    svc_instances = os.getenv('SVC_INSTANCES', '4')  # Number of concurrent magick processes
    office_url = os.getenv('OFFICE_URL', '')

    # Connection details
    client_map_host_from = os.getenv('CLIENT_MAP_HOST_FROM', '')
    client_map_host_to = os.getenv('CLIENT_MAP_HOST_TO', '')
    client_map_port_from = os.getenv('CLIENT_MAP_PORT_FROM', '0')
    client_map_port_to = os.getenv('CLIENT_MAP_PORT_TO', '0')

    # XML file path
    path = f"/opt/corpus/censhare/censhare-Service-Client/config/.hosts/{svc_host}/serviceclient-preferences-{svc_user}.xml"
    tree = ET.parse(path)
    root = tree.getroot()

    # Update connection settings
    connection = root.find('.//connection[@type="standard"]')
    if connection is not None:
        connection.set('client-map-host-from', client_map_host_from)
        connection.set('client-map-host-to', client_map_host_to)
        connection.set('client-map-port-from', client_map_port_from)
        connection.set('client-map-port-to', client_map_port_to)

    # Update facilities instances
    facilities = root.find(".//facilities")
    facilities.attrib['instances'] = svc_instances

    # Update paths, timeout, and other settings for each facility
    for facility in facilities.findall('.//facility'):
        key = facility.attrib['key']
        update_facility_paths(facility, key, office_url)
        update_facility_timeout(facility, key)

    update_volumes_configuration("/opt/corpus/censhare/censhare-Service-Client/config/hosts.xml")

    tree.write(path)
    print("XML configuration updated.")

def get_path_map():
    return {
        'imagemagick': ('@@CONVERT@@', '/usr/local/bin/magick', '@@COMPOSITE@@', '/usr/local/bin/composite'),
        'exiftool': ('@@EXIFTOOL@@', '/usr/local/bin/exiftool'),
        'ghostscript': ('@@GS@@', '/usr/local/bin/gs'),
        'wkhtmltoimage': ('@@HTML2IMG@@', '/usr/bin/wkhtmltoimage'),
        'pngquant': ('@@PNGQUANT@@', '/usr/bin/pngquant'),
        'ffmpeg': ('@@FFMPEG-PATH@@', '/usr/local/bin/ffmpeg'),
    }

def update_facility_timeout(facility, key):
    """
    Updates the timeout attribute of a facility based on an environment variable.

    Args:
    facility (ET.Element): XML element that contains facility configuration.
    key (str): Facility key to determine which timeout to update.
    """
    # Define the environment variable name based on the facility key
    env_var = f"TIMEOUT_{key.upper()}"

    # Get the timeout value from the environment variable, if set
    timeout_value = os.getenv(env_var)

    if timeout_value:
        try:
            # Convert the timeout value to an integer
            timeout_int = int(timeout_value)
            if timeout_int < 0:
                raise ValueError("Timeout must be a non-negative integer.")
            # Update the 'timeout' attribute in the XML
            facility.set('timeout', str(timeout_int))
            print(f"Timeout for facility '{key}' set to {timeout_int} seconds via environment variable '{env_var}'.")
        except ValueError as ve:
            print(f"Invalid timeout value for '{env_var}': {ve}. Using default value '{facility.get('timeout')}'.")
    else:
        print(f"No environment variable '{env_var}' set. Using default timeout for facility '{key}'.")

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
        for i in range(0, len(paths), 2):
            path_element = facility.find(f".//path[@key='{paths[i]}']")
            if path_element is not None:
                path_element.set('path', paths[i + 1])
            else:
                # If the path element doesn't exist, create it
                ET.SubElement(facility, 'path', {'key': paths[i], 'path': paths[i + 1]})
        print(f"Updated paths for facility '{key}'.")

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

def run_as_corpus(command):
    """
    Executes a given shell command as 'corpus' user and captures the output.

    Args:
    command (str): Shell command to execute.

    Returns:
    subprocess.CompletedProcess: The result object including stdout, stderr, and exit status.
    """
    # Prepend the 'su - corpus -c' to run the command as the 'corpus' user
    corpus_command = f"su - corpus -c \"{command}\""
    try:
        result = subprocess.run(corpus_command, shell=True, executable='/bin/bash', text=True, capture_output=True, check=True)
        print(result.stdout)
        return result
    except subprocess.CalledProcessError as e:
        # Output the stderr and stdout from the subprocess if it fails
        print(f"Command failed with exit status {e.returncode}", file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        return e

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

def get_container_memory_limit():
    """
    Retrieves the memory limit set for the container in bytes.
    Works with both cgroup v1 and v2, and handles ECS Fargate environments.
    """
    import os

    # First, try ECS/Fargate environment variables
    ecs_metadata_uri = os.getenv('ECS_CONTAINER_METADATA_URI_V4')
    if ecs_metadata_uri:
        try:
            import requests
            response = requests.get(f"{ecs_metadata_uri}/task", timeout=2)
            if response.status_code == 200:
                task_metadata = response.json()
                mem_limit = int(task_metadata['Limits']['Memory']) * 1024 * 1024
                print("Memory limit obtained from ECS metadata endpoint.")
                return mem_limit
            else:
                print(f"Received non-200 response from ECS metadata endpoint: {response.status_code}")
        except Exception as e:
            print(f"Error fetching ECS task metadata: {e}")
    else:
        print("ECS_CONTAINER_METADATA_URI_V4 not set; skipping ECS metadata endpoint.")

    # Try cgroup v2
    try:
        with open('/sys/fs/cgroup/memory.max', 'r') as f:
            mem_limit_str = f.read().strip()
            if mem_limit_str.isdigit():
                mem_limit = int(mem_limit_str)
                print("Memory limit obtained from cgroup v2.")
                return mem_limit
            elif mem_limit_str == 'max':
                # No limit is set
                mem_limit = psutil.virtual_memory().total
                print("No memory limit set in cgroup v2; using total system memory.")
                return mem_limit
            else:
                raise ValueError(f"Unexpected memory.max value: {mem_limit_str}")
    except FileNotFoundError:
        print("cgroup v2 memory.max not found; trying cgroup v1.")

    # Try cgroup v1
    try:
        with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
            mem_limit = int(f.read().strip())
            if mem_limit >= 9223372036854771712 or mem_limit == 0:
                mem_limit = psutil.virtual_memory().total
                print("No memory limit set in cgroup v1; using total system memory.")
            else:
                print("Memory limit obtained from cgroup v1.")
            return mem_limit
    except FileNotFoundError:
        print("cgroup v1 memory.limit_in_bytes not found; unable to determine memory limit from cgroups.")

    # As a last resort, use psutil
    mem_limit = psutil.virtual_memory().total
    print("Memory limit obtained from psutil.virtual_memory().total.")
    return mem_limit

def update_imagemagick_policy_xml(container_memory, svc_instances):
    # Calculate buffer and available memory
    buffer_percentage = float(os.getenv('IMAGEMAGICK_BUFFER_PERCENTAGE', '0.1'))  # Reduced to 10%
    buffer_memory = int(container_memory * buffer_percentage)
    available_memory = container_memory - buffer_memory

    # Reduce the number of instances if necessary
    svc_instances = int(os.getenv('SVC_INSTANCES', '4'))

    # Calculate memory per process
    memory_per_process = int(available_memory / svc_instances)

    # Ensure minimum memory per process
    min_memory_per_process = 1 * 1024 * 1024 * 1024  # 1GB
    if memory_per_process < min_memory_per_process:
        memory_per_process = min_memory_per_process

    # Set resource limits
    memory_limit_mb = memory_per_process // (1024 * 1024)
    map_limit_mb = memory_limit_mb * 2  # Map is double the memory
    area_limit = f"{memory_limit_mb * 1024}KP"  # width*height <= memory_in_MB * 1024
    disk_limit = "40GiB"  # Set higher disk limit matching increased ephemeral storage
    thread_limit = 1  # Limit threads to reduce resource contention

    memory_limit = f"{memory_limit_mb}MB"
    map_limit = f"{map_limit_mb}MB"

    # Path to policy.xml
    policy_xml_path = "/usr/local/etc/ImageMagick-7/policy.xml"

    # Parse the existing policy.xml
    try:
        tree = ET.parse(policy_xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"Error parsing policy.xml: {e}")
        sys.exit(1)

    # Define the policies to update
    policies_to_update = {
        "memory": memory_limit,
        "map": map_limit,
        "area": area_limit,
        "disk": disk_limit,
        "thread": str(thread_limit)
    }

    # Update or add the resource policies
    for name, value in policies_to_update.items():
        policy = root.find(f".//policy[@domain='resource'][@name='{name}']")
        if policy is not None:
            policy.set('value', value)
            print(f"Updated policy: {name} = {value}")
        else:
            # If the policy doesn't exist, add it
            ET.SubElement(root, 'policy', {
                'domain': 'resource',
                'name': name,
                'value': value
            })
            print(f"Added policy: {name} = {value}")

    # Write back the updated policy.xml with pretty formatting
    xml_str = ET.tostring(root, encoding='utf-8')
    parsed = minidom.parseString(xml_str)
    pretty_xml_str = parsed.toprettyxml(indent="  ")

    with open(policy_xml_path, 'w') as f:
        f.write(pretty_xml_str)

    # Run 'identify -list resource' to display the current resource limits
    try:
        result = subprocess.run(['/usr/local/bin/identify', '-list', 'resource'], capture_output=True, text=True, check=True)
        print("Current ImageMagick resource limits:")
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running 'identify -list resource': {e}")
        print(f"Standard Output: {e.stdout}")
        print(f"Standard Error: {e.stderr}")

    print(f"ImageMagick policy.xml updated with memory limits based on container memory and {svc_instances} concurrent instances.")

def main():
    # Stop censhare Client on SIGTERM
    signal.signal(signal.SIGTERM, signal_handler)

    # Environment variables
    svc_user = os.getenv("SVC_USER")
    svc_pass = os.getenv("SVC_PASS")
    svc_host = os.getenv("SVC_HOST")
    if not all([svc_user, svc_pass, svc_host]):
        print("Required variables (SVC_USER, SVC_PASS, SVC_HOST) are not set.")
        sys.exit(1)    

    # Retrieve the number of service instances from environment variable
    svc_instances_env = os.getenv('SVC_INSTANCES', '4')
    try:
        svc_instances = int(svc_instances_env)
        if svc_instances < 1:
            raise ValueError
    except ValueError:
        print(f"Invalid SVC_INSTANCES value: '{svc_instances_env}'. It must be a positive integer.")
        sys.exit(1)

    # Update ImageMagick's policy.xml based on container memory and svc_instances
    container_memory = get_container_memory_limit()
    print(f"Container memory limit detected: {container_memory / (1024**3):.2f} GB")
    update_imagemagick_policy_xml(container_memory, svc_instances)

    # Check if service client is pre-installed
    client_installed = os.path.exists("/opt/corpus/censhare/censhare-Service-Client")
    if not client_installed:
        repo_user = os.getenv("REPO_USER")
        repo_pass = os.getenv("REPO_PASS")
        version = os.getenv("VERSION")
        if not all([repo_user, repo_pass, version]):
            print("Service client not pre-installed and required variables (REPO_USER, REPO_PASS, VERSION) are not set.")
            sys.exit(1)
        
        download_url = f"https://{repo_user}:{repo_pass}@rpm.censhare.com/censhare-release/censhare-Service/v{version}/Shell/censhare-Service-Client-v{version}.tar.gz"
        download_unpack(download_url, "/tmp/censhare-client.tar.gz")

    # Install custom iccprofiles if provided in build
    icc_source = "/build_iccprofiles"
    icc_target = "/opt/corpus/censhare/censhare-Service-Client/iccprofiles"
    setup_icc_profiles(icc_source, icc_target)       
    # Install custom iccprofiles if mounted
    icc_source = "/iccprofiles"
    icc_target = "/opt/corpus/censhare/censhare-Service-Client/iccprofiles"
    setup_icc_profiles(icc_source, icc_target) 

    # Run setup and start commands
    setup_command = f"yes Y | /opt/corpus/censhare/censhare-Service-Client/serviceclient.sh setup -m frmis://{svc_host}:30546/corpus.RMIServerSSL -n {svc_host} -u {svc_user} -p {svc_pass}"
    start_command = "/opt/corpus/censhare/censhare-Service-Client/serviceclient.sh start"
    run_as_corpus(setup_command)
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

if __name__ == "__main__":
    main()