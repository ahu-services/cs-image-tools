import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import xml.etree.ElementTree as ET
from xml.dom import minidom

import requests
import urllib3
from urllib3.exceptions import HTTPError

JAVA_WINDOWS = [
    (202201, 11),
    (202403, 17),
    (202599, 21),
]
JAVA_DEFAULT = 25

CLIENT_VERSION_FILE = "/opt/corpus/censhare/client-version.txt"
SERVICECLIENT_SCRIPT = "/opt/corpus/censhare/censhare-Service-Client/serviceclient.sh"
DEFAULT_RMI_PORT = "30550"
DEFAULT_IMAGEMAGICK_POLICY_PATH = "/usr/local/etc/ImageMagick-7/policy.xml"
MIB = 1024 * 1024
GIB = 1024 * MIB

# ImageMagick policy rendering
POLICY_TEMPLATE_SUFFIX = ".dist"
POLICY_ENV_PREFIX = "IMAGEMAGICK_POLICY_"
POLICY_CONTROL_ENV_VARS = {"IMAGEMAGICK_POLICY_AUTOCONFIG"}
# Largest single allocation ImageMagick may request. Layered Photoshop TIFF
# masters keep their layers in TIFF tag 37724, which is read as one block that
# cannot spill to disk; blocks close to 1 GB occur in practice.
DEFAULT_MAX_MEMORY_REQUEST = 1536 * MIB
# Reading such a file needs about three times the block in anonymous memory
# (measured: ~2.6 GiB for a 0.86 GiB block). Sizing assumes 1 GiB blocks.
LARGE_JOB_BYTES = 3 * GIB
_SIZE = r"\d+(\.\d+)?([KMGTPE]i?)?B?"
_PIXELS = r"\d+(\.\d+)?([KMGTPE]P)?"
_INT = r"\d+"
# Policy settings that may be overridden with IMAGEMAGICK_POLICY_<NAME>
# (name upper-cased, "-" as "_"): name -> (domain, value pattern).
POLICY_SETTINGS = {
    "memory": ("resource", _SIZE),
    "map": ("resource", _SIZE),
    "disk": ("resource", _SIZE),
    "area": ("resource", f"({_PIXELS}|{_SIZE})"),
    "width": ("resource", _PIXELS),
    "height": ("resource", _PIXELS),
    "list-length": ("resource", _INT),
    "file": ("resource", _INT),
    "thread": ("resource", _INT),
    "throttle": ("resource", _INT),
    "time": ("resource", _INT),
    "temporary-path": ("resource", r"/[A-Za-z0-9._/-]*"),
    "max-memory-request": ("system", _SIZE),
}
CGROUP_MEMORY_LIMIT_FILES = (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
)
CGROUP_MEMORY_PEAK_FILES = (
    "/sys/fs/cgroup/memory.peak",
    "/sys/fs/cgroup/memory/memory.max_usage_in_bytes",
)
CGROUP_CPU_MAX_FILE = "/sys/fs/cgroup/cpu.max"
CGROUP_V1_CPU_QUOTA_FILE = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
CGROUP_V1_CPU_PERIOD_FILE = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"
CGROUP_OOM_EVENT_FILES = (
    "/sys/fs/cgroup/memory.events",
    "/sys/fs/cgroup/memory/memory.oom_control",
)
CGROUP_V1_MEMORY_FAILCNT_FILE = "/sys/fs/cgroup/memory/memory.failcnt"
MEMORY_MONITOR_INTERVAL_SECONDS = 60
RMI_HOST_OPTION_PATTERN = re.compile(r"-Djava\.rmi\.server\.hostname=([^\s]+)")
CLIENT_DOWNLOAD_URL = (
    "https://rpm.censhare.com/censhare-release/censhare-Service/v{version}"
    "/Shell/censhare-Service-Client-v{version}.tar.gz"
)
# Credentials read at start. They are removed from the environment before any
# child process runs, so the Service-Client JVM and the media tools never
# inherit them (they would otherwise be readable in /proc/<pid>/environ).
STARTUP_SECRET_ENV_VARS = ("REPO_USER", "REPO_PASS", "SVC_PASS")
# VOLUMES_INFO may carry storage secrets; it is needed until hosts.xml is written.
CONFIG_SECRET_ENV_VARS = ("VOLUMES_INFO",)


def _determine_serviceclient_version(script_path=SERVICECLIENT_SCRIPT):
    """
    Extract the service client version marker from the serviceclient.sh script.
    """
    version_pattern = re.compile(r"-Dcenshare\.serviceclient\.version=([\w\.\-]+)")
    try:
        with open(script_path, encoding="utf-8") as handle:
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
    Defaults to False for any other value (including None).
    """
    if not value:
        return False
    return value.strip().lower() in {"true", "1", "t", "y", "yes"}


def _read_first_line(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return None


def _parse_positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


def _round_down(value, step):
    return max(step, (value // step) * step)


def _format_binary_size(value):
    if value % GIB == 0:
        return f"{value // GIB}GiB"
    return f"{max(1, value // MIB)}MiB"


def _format_human_size(value):
    """Readable size for log lines, e.g. 58.9GiB."""
    if value >= GIB:
        return f"{value / GIB:.1f}GiB"
    return f"{value / MIB:.0f}MiB"


def detect_container_memory_limit_bytes():
    """
    Reads the effective container memory limit from cgroup v2/v1.
    Returns None when the container is effectively unlimited.
    """
    for path in CGROUP_MEMORY_LIMIT_FILES:
        raw_value = _read_first_line(path)
        if not raw_value or raw_value == "max":
            continue
        try:
            limit = int(raw_value)
        except ValueError:
            continue
        if 0 < limit < (1 << 60):
            return limit
    return None


def read_ecs_task_limits(timeout=1.0):
    """
    Returns the task-level "Limits" of the ECS task metadata endpoint (v4), e.g.
    {"CPU": 1, "Memory": 8192}, or None outside ECS. On Fargate the memory
    limit is often set on the task only, so the container cgroup reads "max".
    """
    base_url = os.getenv("ECS_CONTAINER_METADATA_URI_V4")
    if not base_url:
        return None
    try:
        response = requests.get(f"{base_url}/task", timeout=timeout)
        limits = response.json().get("Limits") if response.status_code == 200 else None
    except (requests.RequestException, ValueError, AttributeError) as exc:
        print(f"Warning: Unable to read ECS task metadata: {exc}")
        return None
    return limits if isinstance(limits, dict) else None


def detect_memory_limit():
    """
    Returns (bytes, source, ecs_limits). Order: cgroup v2/v1 (Docker,
    Kubernetes and most container platforms), CONTAINER_MEMORY_LIMIT, ECS task
    metadata. bytes and source are None when no limit is known.
    """
    limit = detect_container_memory_limit_bytes()
    if limit is not None:
        return limit, "cgroup", None
    configured = os.getenv("CONTAINER_MEMORY_LIMIT", "").strip()
    if configured:
        limit = parse_policy_size(configured)
        if limit:
            return limit, "CONTAINER_MEMORY_LIMIT", None
        print(f"Warning: Ignoring invalid CONTAINER_MEMORY_LIMIT={configured}")
    ecs_limits = read_ecs_task_limits()
    try:
        memory_mib = int((ecs_limits or {}).get("Memory") or 0)
    except (TypeError, ValueError):
        memory_mib = 0
    if memory_mib > 0:
        return memory_mib * MIB, "ECS task metadata", ecs_limits
    return None, None, ecs_limits


def detect_cpu_limit(ecs_limits=None):
    """
    Returns (cpus, source): the cgroup CPU quota, the ECS task vCPU limit or
    the number of online CPUs, in that order.
    """
    raw_v2 = _read_first_line(CGROUP_CPU_MAX_FILE)
    if raw_v2:
        parts = raw_v2.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                return int(parts[0]) / int(parts[1]), "cgroup"
            except (ValueError, ZeroDivisionError):
                pass
    quota = _read_first_line(CGROUP_V1_CPU_QUOTA_FILE)
    period = _read_first_line(CGROUP_V1_CPU_PERIOD_FILE)
    try:
        if quota and period and int(quota) > 0:
            return int(quota) / int(period), "cgroup"
    except (ValueError, ZeroDivisionError):
        pass
    try:
        ecs_cpu = float((ecs_limits or {}).get("CPU") or 0)
    except (TypeError, ValueError):
        ecs_cpu = 0
    if ecs_cpu > 0:
        return ecs_cpu, "ECS task metadata"
    return float(os.cpu_count() or 1), "online CPUs"


def _usable_memory(memory_limit_bytes):
    """Container memory minus a reserve for the JVM, the OS and other tools."""
    reserve = min(
        max(int(memory_limit_bytes * 0.20), 768 * MIB), int(memory_limit_bytes * 0.35)
    )
    return max(memory_limit_bytes - reserve, 512 * MIB)


def large_jobs_that_fit(memory_limit_bytes):
    """How many large layered-TIFF conversions fit into memory at the same time."""
    return _usable_memory(memory_limit_bytes) // LARGE_JOB_BYTES


def recommend_imagemagick_policy(
    memory_limit_bytes, svc_instances, cpu_count=None, temp_free_bytes=None
):
    """
    Derive ImageMagick limits for one worker from the container size.

    memory/map are the per-worker pixel-cache budget. They are sized after
    reserving room for about half of the workers to read a large layered TIFF
    at the same time (LARGE_JOB_BYTES each), because those allocations are not
    covered by memory/map and cannot spill to disk. max-memory-request is the
    largest single allocation (e.g. an embedded Photoshop layer block) and is
    deliberately not derived from the per-worker budget.
    """
    workers = max(1, svc_instances)
    usable = _usable_memory(memory_limit_bytes)
    concurrent_large_jobs = math.ceil(workers / 2)
    cache_budget = max(usable - concurrent_large_jobs * LARGE_JOB_BYTES, 0) // workers

    memory_limit = _round_down(_clamp(cache_budget // 3, 512 * MIB, 2 * GIB), 64 * MIB)
    map_limit = _round_down(_clamp(cache_budget * 2 // 3, 1 * GIB, 4 * GIB), 64 * MIB)
    if temp_free_bytes:
        disk_limit = _round_down(
            _clamp(int(temp_free_bytes * 0.8) // workers, 2 * GIB, 64 * GIB),
            256 * MIB,
        )
    else:
        disk_limit = _round_down(
            _clamp(max(int(usable * 1.5), 2 * GIB), 2 * GIB, 10 * GIB), 256 * MIB
        )
    if cpu_count:
        thread_limit = str(_clamp(int(cpu_count // workers), 1, 4))
    else:
        thread_limit = "1" if workers > 1 else "2"

    return {
        "thread": thread_limit,
        "memory": _format_binary_size(memory_limit),
        "map": _format_binary_size(max(map_limit, memory_limit)),
        "disk": _format_binary_size(disk_limit),
        "max-memory-request": _format_binary_size(DEFAULT_MAX_MEMORY_REQUEST),
    }


def parse_policy_size(value):
    """
    Parses an ImageMagick size value such as "1536MiB", "2GiB", "1.5GB" or a
    plain number of bytes. Returns bytes or None.
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGTPE]i?)?B?\s*", str(value or ""))
    if not match:
        return None
    number, prefix = match.groups()
    if not prefix:
        return int(float(number))
    exponent = "KMGTPE".index(prefix[0]) + 1
    base = 1024 if prefix.endswith("i") else 1000
    return int(float(number) * base**exponent)


def _policy_value_is_valid(name, value):
    pattern = POLICY_SETTINGS[name][1]
    if not re.fullmatch(pattern, value):
        return False
    if name == "thread":
        return int(value) > 0
    return True


def _policy_env_name(policy_name):
    return f"{POLICY_ENV_PREFIX}{policy_name.upper().replace('-', '_')}"


def read_policy_overrides(environ=None):
    """
    Collects IMAGEMAGICK_POLICY_<NAME> overrides (e.g. IMAGEMAGICK_POLICY_LIST_LENGTH
    -> list-length). Returns (overrides, ignored) where ignored lists
    human-readable reasons for unknown names or invalid values.
    """
    environ = os.environ if environ is None else environ
    known = {_policy_env_name(name): name for name in POLICY_SETTINGS}
    overrides, ignored = {}, []
    for env_name in sorted(environ):
        if not env_name.startswith(POLICY_ENV_PREFIX):
            continue
        if env_name in POLICY_CONTROL_ENV_VARS:
            continue
        value = environ[env_name].strip()
        if not value:
            continue
        policy_name = known.get(env_name)
        if policy_name is None:
            ignored.append(f"{env_name} (unknown policy setting)")
        elif not _policy_value_is_valid(policy_name, value):
            ignored.append(f"{env_name}={value} (invalid value)")
        else:
            overrides[policy_name] = value
    return overrides, ignored


def _set_policy_value(root, domain, name, value):
    policy = root.find(f"./policy[@domain='{domain}'][@name='{name}']")
    if policy is None:
        policy = ET.SubElement(root, "policy", {"domain": domain, "name": name})
    policy.set("value", str(value))


def _get_policy_value(root, domain, name):
    policy = root.find(f"./policy[@domain='{domain}'][@name='{name}']")
    return policy.get("value") if policy is not None else None


def _load_policy_template(policy_path):
    """
    Returns the parsed bundled policy. The first start keeps a pristine copy
    next to the policy; later starts render from it, so a removed override is
    not carried over by a restarted container.
    """
    template_path = policy_path + POLICY_TEMPLATE_SUFFIX
    if not os.path.exists(template_path):
        try:
            shutil.copy2(policy_path, template_path)
        except OSError as exc:
            print(f"Warning: Unable to keep a copy of the bundled policy: {exc}")
            return ET.parse(policy_path)
    return ET.parse(template_path)


def imagemagick_temp_dir(temporary_path=None):
    """The directory ImageMagick writes its temp files to."""
    return (
        temporary_path
        or os.getenv("MAGICK_TEMPORARY_PATH")
        or os.getenv("TMPDIR")
        or "/tmp"
    )


def configure_imagemagick_policy(policy_path=DEFAULT_IMAGEMAGICK_POLICY_PATH):
    """
    Renders the ImageMagick policy for this container from the bundled policy,
    optional auto-configuration and optional IMAGEMAGICK_POLICY_<NAME>
    overrides, and logs every effective value with its source. Without any of
    these variables the bundled policy is used unchanged. Only reads local
    files (and, on ECS without a container memory limit, the local metadata
    endpoint), so it does not delay the Service-Client start.
    """
    if not os.path.exists(policy_path):
        print(f"Warning: ImageMagick policy file not found at {policy_path}")
        return

    try:
        tree = _load_policy_template(policy_path)
    except ET.ParseError as exc:
        print(f"Warning: Unable to parse ImageMagick policy {policy_path}: {exc}")
        return

    root = tree.getroot()
    svc_instances = _parse_positive_int(os.getenv("SVC_INSTANCES", "4"), 4)
    auto_config = str_to_bool(os.getenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "false"))
    overrides, ignored = read_policy_overrides()

    effective = {}
    for name, (domain, _pattern) in POLICY_SETTINGS.items():
        value = _get_policy_value(root, domain, name)
        if value is not None:
            effective[name] = (value, "bundled")

    temporary_path = overrides.get("temporary-path")
    if temporary_path:
        try:
            os.makedirs(temporary_path, exist_ok=True)
        except OSError as exc:
            ignored.append(f"IMAGEMAGICK_POLICY_TEMPORARY_PATH ({exc})")
            overrides.pop("temporary-path")
            temporary_path = None
    temp_dir = imagemagick_temp_dir(
        temporary_path or effective.get("temporary-path", (None,))[0]
    )
    try:
        temp_free = shutil.disk_usage(temp_dir).free
    except OSError:
        temp_free = None

    memory_limit, memory_source, ecs_limits = detect_memory_limit()
    detected = [
        "memory limit "
        + (
            f"{_format_human_size(memory_limit)} ({memory_source})"
            if memory_limit
            else "none detected"
        ),
        f"SVC_INSTANCES={svc_instances}",
    ]
    if temp_free is not None:
        detected.append(f"temp {temp_dir} free {_format_human_size(temp_free)}")
    notes = []
    if auto_config and memory_limit is None:
        notes.append(
            "WARNING: IMAGEMAGICK_POLICY_AUTOCONFIG=true but no memory limit was "
            "detected (cgroup, CONTAINER_MEMORY_LIMIT, ECS task metadata); "
            "auto-configuration skipped, bundled values are used."
        )
    elif auto_config:
        cpu_count, cpu_source = detect_cpu_limit(ecs_limits)
        detected.append(f"cpu {cpu_count:g} ({cpu_source})")
        recommended = recommend_imagemagick_policy(
            memory_limit, svc_instances, cpu_count, temp_free
        )
        bundled_mmr = parse_policy_size(effective.get("max-memory-request", ("",))[0])
        if bundled_mmr and bundled_mmr > parse_policy_size(
            recommended["max-memory-request"]
        ):
            del recommended["max-memory-request"]
        for name, value in recommended.items():
            effective[name] = (value, "autoconfig")
        fits = large_jobs_that_fit(memory_limit)
        wanted = math.ceil(svc_instances / 2)
        if fits < wanted:
            notes.append(
                f"WARNING: memory for {fits} concurrent large layered-TIFF "
                f"conversion(s) (~{_format_binary_size(LARGE_JOB_BYTES)} each), "
                f"{wanted} wanted for SVC_INSTANCES={svc_instances}; use a "
                "larger container or fewer instances."
            )
    for name, value in overrides.items():
        effective[name] = (value, f"env {_policy_env_name(name)}")

    disk_bytes = parse_policy_size(effective.get("disk", ("",))[0])
    if disk_bytes and temp_free is not None and temp_free < disk_bytes * svc_instances:
        notes.append(
            f"WARNING: {temp_dir} has {_format_human_size(temp_free)} free, less than "
            f"disk={effective['disk'][0]} x SVC_INSTANCES={svc_instances}; large "
            "conversions may fail with 'cache resources exhausted'."
        )

    for name, (value, _source) in effective.items():
        _set_policy_value(root, POLICY_SETTINGS[name][0], name, value)
    tree.write(policy_path, encoding="utf-8", xml_declaration=True)

    facility_timeout = os.getenv("IMAGEMAGICK_TIMEOUT", "").strip()
    lines = [
        f"ImageMagick policy {policy_path} "
        f"(auto-configuration {'enabled' if auto_config else 'disabled'}):",
        "  detected: " + ", ".join(detected),
    ]
    lines += [
        f"  {name}={value} ({source})" for name, (value, source) in effective.items()
    ]
    lines.append(
        "  imagemagick facility timeout: "
        + (
            f"{facility_timeout}s (IMAGEMAGICK_TIMEOUT)"
            if facility_timeout
            else "Service-Client default (IMAGEMAGICK_TIMEOUT not set)"
        )
    )
    lines += [f"  ignored: {item}" for item in ignored]
    lines += [f"  {note}" for note in notes]
    print("\n".join(lines))


def _read_cgroup_memory_peak():
    for path in CGROUP_MEMORY_PEAK_FILES:
        raw_value = _read_first_line(path)
        if raw_value and raw_value.isdigit():
            return int(raw_value)
    return None


def _read_counter(path, key):
    """Reads "<key> <number>" from a cgroup stat file such as memory.events."""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) == 2 and parts[0] == key and parts[1].isdigit():
                    return int(parts[1])
    except OSError:
        return None
    return None


def read_oom_kill_count():
    """
    Returns (count, source) of OOM kills in this container: cgroup v2
    memory.events, cgroup v1 memory.oom_control, else the v1 memory.failcnt
    (times the limit was hit). (None, None) when none is readable.
    """
    for path in CGROUP_OOM_EVENT_FILES:
        count = _read_counter(path, "oom_kill")
        if count is not None:
            return count, os.path.basename(path)
    failcnt = _read_first_line(CGROUP_V1_MEMORY_FAILCNT_FILE)
    if failcnt and failcnt.isdigit():
        return int(failcnt), os.path.basename(CGROUP_V1_MEMORY_FAILCNT_FILE)
    return None, None


def container_memory_monitor_loop(interval=MEMORY_MONITOR_INTERVAL_SECONDS):
    """
    Runs in a daemon thread after the Service-Client started. Logs only
    changes: a container memory peak that grew by more than 5 % and every rise
    of the OOM kill counter (e.g. an ImageMagick process killed by the kernel).
    """
    last_peak = _read_cgroup_memory_peak()
    last_oom, oom_source = read_oom_kill_count()
    if last_oom:
        print(f"Container OOM kills before monitoring: {last_oom} ({oom_source}).")
    while True:
        time.sleep(interval)
        peak = _read_cgroup_memory_peak()
        if peak and last_peak and peak > last_peak * 1.05:
            print(f"Container memory peak: {_format_human_size(peak)}.")
            last_peak = peak
        elif peak and not last_peak:
            last_peak = peak
        count, oom_source = read_oom_kill_count()
        if count is not None and last_oom is not None and count > last_oom:
            print(
                f"WARNING: {count - last_oom} process(es) in this container were "
                f"killed for lack of memory ({oom_source} now {count}); check the "
                "container size, SVC_INSTANCES and the ImageMagick policy."
            )
        if count is not None:
            last_oom = count


def start_container_memory_monitor():
    """Starts the memory/OOM monitor without blocking the caller."""
    thread = threading.Thread(
        target=container_memory_monitor_loop,
        name="container-memory-monitor",
        daemon=True,
    )
    thread.start()
    return thread


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
            executable="/bin/bash",
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
    java_opts = os.getenv("SERVICECLIENT_JAVA_OPTIONS", "").strip()
    existing_match = RMI_HOST_OPTION_PATTERN.search(java_opts)
    existing_host = existing_match.group(1) if existing_match else None

    desired_host = callback_host or existing_host or detect_rmi_host_ip()
    if not desired_host:
        print(
            "Warning: Unable to determine callback host for SERVICECLIENT_JAVA_OPTIONS."
        )
        return

    cleaned_opts = RMI_HOST_OPTION_PATTERN.sub("", java_opts).strip()
    rmi_option = f"-Djava.rmi.server.hostname={desired_host}"
    combined_opts = " ".join(part for part in [cleaned_opts, rmi_option] if part)
    os.environ["SERVICECLIENT_JAVA_OPTIONS"] = combined_opts

    if callback_host:
        source = "SERVICECLIENT_CALLBACK_HOST"
    elif existing_host:
        source = "existing SERVICECLIENT_JAVA_OPTIONS"
    else:
        source = "detected host IP"
    print(f"Configured SERVICECLIENT_JAVA_OPTIONS ({source}): {combined_opts}")


def client_download_url(version):
    """
    Returns the repository URL of the Service-Client archive for a version.
    The URL never contains credentials; pass them to download_unpack as auth.
    """
    return CLIENT_DOWNLOAD_URL.format(version=version)


def scrub_environment(names):
    """
    Removes the given variables from this process's environment so that child
    processes started afterwards do not inherit them.
    """
    for name in names:
        os.environ.pop(name, None)


def download_unpack(url, output_path, auth=None):
    """
    Downloads and unpacks a tar.gz file from a given URL to /opt/corpus directory.
    Change owner and group for the unpacked files to "corpus".

    Args:
    url (str): URL to fetch the tar.gz from. Must not contain credentials.
    output_path (str): Local path to save the tar.gz file.
    auth (tuple): Optional (user, password) for HTTP basic authentication.

    Raises:
    SystemExit: If the download fails or the HTTP status is not 200.
    """
    # (connect timeout, read timeout) so a stalled connection fails fast instead
    # of hanging the container start indefinitely.
    response = requests.get(url, stream=True, timeout=(10, 60), auth=auth)
    if response.status_code != 200:
        print(f"Failed to download the file (HTTP {response.status_code}).")
        sys.exit(1)

    hashers = {
        # usedforsecurity=False: MD5 here is only an integrity/identity checksum.
        "md5": hashlib.md5(usedforsecurity=False),
        "sha256": hashlib.sha256(),
    }
    with open(output_path, "wb") as f:
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
    subprocess.run(["chown", "-R", "corpus:corpus", "/opt/corpus/"], check=True)
    detected_version = _determine_serviceclient_version()
    if detected_version:
        print(f"Installed service client version: {detected_version}")
    else:
        print(
            "Warning: Could not determine installed service client version from serviceclient.sh."
        )


def select_jdk_major(client_version):
    """
    Chooses the JDK major version for the given client version.
    """
    if not client_version:
        print(
            f"Warning: censhare Service-Client version unknown, defaulting to JDK {JAVA_DEFAULT}."
        )
        return JAVA_DEFAULT
    parts = client_version.split(".")
    if len(parts) < 2:
        print(
            f"Warning: Unexpected version format '{client_version}', defaulting to JDK {JAVA_DEFAULT}."
        )
        return JAVA_DEFAULT
    try:
        major = int(parts[0])
        minor = int(parts[1])
    except ValueError:
        print(
            f"Warning: Unable to parse version '{client_version}', defaulting to JDK {JAVA_DEFAULT}."
        )
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
        with open(CLIENT_VERSION_FILE, "w") as handle:
            handle.write(version)
    except OSError as exc:
        print(
            f"Warning: Unable to persist client version to {CLIENT_VERSION_FILE}: {exc}"
        )


def ensure_corretto(jdk_major):
    """
    Installs (or reuses) the Corretto release that matches the requested major version.
    """

    def configure_java_environment(java_binary):
        java_home = os.path.dirname(os.path.dirname(os.path.realpath(java_binary)))
        os.environ["JAVA_HOME"] = java_home
        os.environ["JDK_HOME"] = java_home
        os.environ["PATH"] = (
            f"{os.path.join(java_home, 'bin')}:{os.environ.get('PATH', '')}"
        )
        try:
            subprocess.run(
                ["update-alternatives", "--set", "java", java_binary], check=False
            )
            javac_binary = os.path.join(java_home, "bin", "javac")
            if os.path.exists(javac_binary):
                subprocess.run(
                    ["update-alternatives", "--set", "javac", javac_binary], check=False
                )
        except FileNotFoundError:
            print(
                "update-alternatives not available; skipping alternative configuration."
            )

    java_binary = shutil.which("java")
    current_major = None
    if java_binary:
        try:
            result = subprocess.run(
                [java_binary, "-version"], capture_output=True, text=True, check=True
            )
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
        raise RuntimeError(
            f"Unsupported architecture for Corretto JDK: {machine}"
        ) from exc

    url = f"https://corretto.aws/downloads/latest/amazon-corretto-{jdk_major}-{arch}-linux-jdk.deb"
    deb_path = f"/tmp/amazon-corretto-{jdk_major}.deb"
    print(f"Installing Corretto JDK {jdk_major} from {url}...")
    subprocess.run(["wget", "-q", "-O", deb_path, url], check=True)
    try:
        subprocess.run(["dpkg", "-i", deb_path], check=True)
    except subprocess.CalledProcessError:
        subprocess.run(["apt-get", "update"], check=True)
        subprocess.run(["apt-get", "-y", "-f", "install"], check=True)
        subprocess.run(["dpkg", "-i", deb_path], check=True)
    finally:
        if os.path.exists(deb_path):
            os.remove(deb_path)

    java_binary = shutil.which("java")
    if java_binary:
        configure_java_environment(java_binary)
    else:
        print("Warning: Java binary not found after installation.")


def configure_xml(
    svc_host, svc_user, base_dir="/opt/corpus/censhare/censhare-Service-Client"
):
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
    svc_instances = os.getenv("SVC_INSTANCES", "4")
    office_url = os.getenv("OFFICE_URL", "")
    callback_host = os.getenv("SERVICECLIENT_CALLBACK_HOST", "").strip()
    if callback_host:
        print(
            f"Callback host requested via SERVICECLIENT_CALLBACK_HOST: {callback_host}"
        )
    apply_rmi_callback_host(callback_host)

    rmi_port_raw = os.getenv("SERVICECLIENT_RMI_PORT", DEFAULT_RMI_PORT)
    if str(rmi_port_raw).isdigit():
        rmi_port = str(rmi_port_raw)
    else:
        print(
            f"Warning: SERVICECLIENT_RMI_PORT '{rmi_port_raw}' is not numeric. Falling back to {DEFAULT_RMI_PORT}."
        )
        rmi_port = DEFAULT_RMI_PORT

    rmi_port_to_raw = os.getenv("SERVICECLIENT_RMI_PORT_TO", "").strip()
    if rmi_port_to_raw:
        if rmi_port_to_raw.isdigit():
            rmi_port_to = rmi_port_to_raw
        else:
            print(
                f"Warning: SERVICECLIENT_RMI_PORT_TO '{rmi_port_to_raw}' is not numeric. Falling back to {rmi_port}."
            )
            rmi_port_to = rmi_port
    else:
        rmi_port_to = rmi_port
    print(f"Configuring Service-Client server port range {rmi_port}-{rmi_port_to}.")

    # Connection details
    client_map_host_from = os.getenv("CLIENT_MAP_HOST_FROM", "").strip()
    client_map_host_to = os.getenv("CLIENT_MAP_HOST_TO", "").strip()
    client_map_port_from = os.getenv("CLIENT_MAP_PORT_FROM", "").strip()
    client_map_port_to = os.getenv("CLIENT_MAP_PORT_TO", "").strip()

    if client_map_port_from and not client_map_port_to:
        client_map_port_to = client_map_port_from
    if client_map_port_to and not client_map_port_from:
        client_map_port_from = client_map_port_to

    # XML file path
    path = (
        f"{base_dir}/config/.hosts/{svc_host}/serviceclient-preferences-{svc_user}.xml"
    )
    tree = ET.parse(path)
    root = tree.getroot()

    # Update connection settings (force port-range to keep listeners in a fixed window).
    # Use explicit "is not None" checks: attribute-only Elements are falsy, so an
    # "x or y" fallback would silently skip a present-but-childless <connection>.
    connection = root.find('.//connection[@type="port-range"]')
    if connection is None:
        connection = root.find('.//connection[@type="standard"]')
    if connection is not None:
        connection.set("type", "port-range")
        connection.set("client-map-host-from", client_map_host_from)
        connection.set("client-map-host-to", client_map_host_to)
        connection.set("client-map-port-from", client_map_port_from)
        connection.set("client-map-port-to", client_map_port_to)
        connection.set("server-port-range-from", rmi_port)
        connection.set("server-port-range-to", rmi_port_to)
    else:
        print("Warning: No <connection> element found in serviceclient preferences.")

    # Update facilities instances
    facilities = root.find(".//facilities")
    if facilities is None:
        print("Warning: No <facilities> element found in serviceclient preferences.")
    else:
        facilities.attrib["instances"] = svc_instances

        # Apply optional per-facility timeout overrides and resolve tool paths in
        # a single pass over the facilities.
        for facility in facilities.findall(".//facility"):
            key = facility.attrib["key"]
            timeout_env_var = os.getenv(f"{key.upper()}_TIMEOUT")
            if timeout_env_var:
                facility.set("timeout", timeout_env_var)
            update_facility_paths(facility, key, office_url)

    update_volumes_configuration(f"{base_dir}/config/hosts.xml")

    tree.write(path)
    print("XML configuration updated.")


# Maps each facility key to its placeholder/binary path pairs in the preferences XML.
FACILITY_PATH_MAP = {
    "imagemagick": (
        "@@CONVERT@@",
        "/usr/local/bin/magick",
        "@@COMPOSITE@@",
        "/usr/local/bin/composite",
    ),
    "exiftool": ("@@EXIFTOOL@@", "/usr/local/bin/exiftool"),
    "ghostscript": ("@@GS@@", "/usr/local/bin/gs"),
    "wkhtmltoimage": ("@@HTML2IMG@@", "/usr/local/bin/wkhtmltoimage"),
    "pngquant": ("@@PNGQUANT@@", "/usr/local/bin/pngquant"),
    "ffmpeg": ("@@FFMPEG-PATH@@", "/usr/local/bin/ffmpeg"),
}


def get_path_map():
    return FACILITY_PATH_MAP


def update_facility_paths(facility, key, office_url):
    """
    Helper function to update path and enabled attributes in XML for specific facilities.
    This function uses environment variables to adjust settings dynamically.

    Args:
    facility (ET.Element): XML element that contains facility configuration.
    key (str): Facility key to determine which paths to update.
    """
    # Update paths based on facility key
    if key in FACILITY_PATH_MAP:
        paths = FACILITY_PATH_MAP[key]
        target_paths = []
        for i in range(0, len(paths), 2):
            path_element = facility.find(f".//path[@key='{paths[i]}']")
            if path_element is not None:
                path_element.set("path", paths[i + 1])
            else:
                # If the path element doesn't exist, create it
                ET.SubElement(facility, "path", {"key": paths[i], "path": paths[i + 1]})
            target_paths.append(paths[i + 1])
        print(f"Updated paths for facility '{key}'.")

        if key != "office" and target_paths:
            binaries_exist = all(
                os.path.exists(path) and os.access(path, os.X_OK)
                for path in target_paths
            )
            if binaries_exist and facility.get("enabled") != "true":
                facility.set("enabled", "true")
                print(f"Enabled facility '{key}' (binaries present).")

    # Handle specific facilities like 'office'
    if key == "office":
        office_validate_certs = str_to_bool(os.getenv("OFFICE_VALIDATE_CERTS", "true"))
        handle_office_facility(
            facility, office_url, validate_certs=office_validate_certs
        )


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
            http = urllib3.PoolManager(
                cert_reqs="CERT_REQUIRED" if validate_certs else "CERT_NONE"
            )
            # Create a Multipart Encoder
            fields = {"file": ("test.txt", "foobar", "text/plain")}

            # Encode the fields
            encoded_fields = urllib3.filepost.encode_multipart_formdata(fields)
            body, content_type = encoded_fields

            headers = {"Content-Type": content_type}

            response = http.request(
                "POST",
                office_url,
                body=body,
                headers=headers,
                timeout=10,
                retries=False,
            )

            if response.status == 200:
                path_element = facility.find(".//path[@key='@@OFFICE@@']")
                if path_element is not None:
                    path_element.set("port", office_url)
                else:
                    ET.SubElement(
                        facility, "path", {"key": "@@OFFICE@@", "port": office_url}
                    )
                print(f"Successfully tested {office_url}, facility enabled.")
            else:
                raise Exception(f"Non-200 status code received: {response.status}")
        except HTTPError as e:
            print(f"Failed to connect to OFFICE_URL: {e}")
            facility.set("enabled", "false")
        except Exception as e:
            print(f"Unexpected error: {e}")
            facility.set("enabled", "false")
    else:
        facility.set("enabled", "false")


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
        result = _run(["runuser", "-u", "corpus", "--", *command])
    except FileNotFoundError:
        try:
            quoted_command = shlex.join(command)
            result = _run(["su", "-s", "/bin/bash", "corpus", "-c", quoted_command])
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
        pid = subprocess.check_output(
            pid_command, shell=True, executable="/bin/bash", text=True
        ).strip()
    except subprocess.CalledProcessError:
        pid = ""

    if pid:
        # Send SIGTERM to the process
        stop_command = f"kill -TERM {pid}"
        subprocess.run(stop_command, shell=True, executable="/bin/bash", text=True)

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
            subprocess.run(
                f"kill -9 {pid}", shell=True, executable="/bin/bash", text=True
            )
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
    with open(log_file_path) as log_file:
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
    volumes_info = os.getenv("VOLUMES_INFO")
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
    for host in root.findall(".//host"):
        for volumes in host.findall("volumes"):
            host.remove(volumes)

        # Ensure <censhare-vfs use="0"/> element is present
        if host.find("censhare-vfs") is None:
            ET.SubElement(host, "censhare-vfs", {"use": "0"})

        # Add new volumes element to the host
        volumes_element = ET.SubElement(host, "volumes")
        for fs_name, attributes in volumes_info.items():
            volume_element = ET.SubElement(volumes_element, "volume")
            volume_element.set("filesystemname", fs_name)
            for attr_key, attr_value in attributes.items():
                if isinstance(attr_value, bool):
                    attr_value = str(attr_value).lower()
                volume_element.set(attr_key, str(attr_value))

    # Pretty-print the XML
    xml_str = ET.tostring(root, encoding="utf-8")
    parsed = minidom.parseString(xml_str)
    pretty_xml_str = parsed.toprettyxml(indent="  ")

    with open(hosts_xml_path, "w") as f:
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
    repo_user = os.getenv("REPO_USER")
    repo_pass = os.getenv("REPO_PASS")
    scrub_environment(STARTUP_SECRET_ENV_VARS)
    if not all([svc_user, svc_pass, svc_host]):
        print("Required variables (SVC_USER, SVC_PASS, SVC_HOST) are not set.")
        sys.exit(1)

    # Check if service client is pre-installed
    client_installed = os.path.exists("/opt/corpus/censhare/censhare-Service-Client")
    client_version = None
    if not client_installed:
        version = client_version_env
        if not all([repo_user, repo_pass, version]):
            print(
                "Service client not pre-installed and required variables (REPO_USER, REPO_PASS, VERSION) are not set."
            )
            sys.exit(1)

        download_unpack(
            client_download_url(version),
            "/tmp/censhare-client.tar.gz",
            auth=(repo_user, repo_pass),
        )
        store_client_version(version)
        client_version = version
    elif client_version_env:
        client_version = client_version_env
        store_client_version(client_version)
    elif os.path.exists(CLIENT_VERSION_FILE):
        try:
            with open(CLIENT_VERSION_FILE) as handle:
                client_version = handle.read().strip()
        except OSError:
            client_version = None

    required_jdk_major = select_jdk_major(client_version)
    ensure_corretto(required_jdk_major)
    try:
        configure_imagemagick_policy()
    except Exception as exc:  # noqa: BLE001 - the policy must never block the start
        print(f"Warning: ImageMagick policy not configured, using bundled file: {exc}")

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
    scrub_environment(CONFIG_SECRET_ENV_VARS)
    run_as_corpus(start_command)
    # Log memory peaks and OOM kills in the background, after the start.
    start_container_memory_monitor()

    # Log output handling
    startup_log_path = "/opt/corpus/censhare/censhare-Service-Client/logs/startup.log"
    service_log_path = "/opt/corpus/censhare/censhare-Service-Client/logs/service-client-internal-0.0.log"
    with open(startup_log_path) as file:
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
