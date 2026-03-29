import hashlib
import importlib.util
import sys
from pathlib import Path
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT_PATH = REPO_ROOT / "entrypoint.py"

spec = importlib.util.spec_from_file_location("entrypoint_module", ENTRYPOINT_PATH)
entrypoint = importlib.util.module_from_spec(spec)
sys.modules["entrypoint_module"] = entrypoint
spec.loader.exec_module(entrypoint)


def test_determine_serviceclient_version(tmp_path):
    script = tmp_path / "serviceclient.sh"
    script.write_text('JAVA_PROPERTIES="$JAVA_PROPERTIES -Dcenshare.serviceclient.version=2024.2.0"\n')

    detected = entrypoint._determine_serviceclient_version(str(script))

    assert detected == "2024.2.0"


def test_download_unpack_logs_checksums(monkeypatch, tmp_path, capsys):
    # Prepare fake HTTP response that streams deterministic bytes
    chunk = b"offline-archive"

    class DummyResponse:
        status_code = 200

        def iter_content(self, chunk_size=8192):
            yield chunk

    monkeypatch.setattr(entrypoint.requests, "get", lambda url, stream=True: DummyResponse())

    # Stub tarfile extraction to materialize a serviceclient script
    script_path = tmp_path / "serviceclient.sh"

    class DummyTar:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extractall(self, path):
            script_path.write_text("placeholder")

    monkeypatch.setattr(entrypoint.tarfile, "open", lambda *args, **kwargs: DummyTar())
    monkeypatch.setattr(entrypoint.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(entrypoint, "_determine_serviceclient_version", lambda: "2024.2.0")

    archive_path = tmp_path / "download.tar.gz"
    entrypoint.download_unpack("https://example.com/archive.tar.gz", str(archive_path))

    out = capsys.readouterr().out
    expected_md5 = hashlib.md5(chunk).hexdigest()
    expected_sha = hashlib.sha256(chunk).hexdigest()

    assert f"MD5: {expected_md5}" in out
    assert f"SHA256: {expected_sha}" in out
    assert "Installed service client version: 2024.2.0" in out


def _write_minimal_preferences(base_dir: Path, host: str, user: str):
    prefs_dir = base_dir / "config" / ".hosts" / host
    prefs_dir.mkdir(parents=True, exist_ok=True)
    prefs_path = prefs_dir / f"serviceclient-preferences-{user}.xml"
    prefs_path.write_text(
        """<root>
  <connection type="standard"/>
  <facilities instances="1">
    <facility key="imagemagick"/>
  </facilities>
</root>
"""
    )
    hosts_xml = base_dir / "config" / "hosts.xml"
    hosts_xml.parent.mkdir(parents=True, exist_ok=True)
    hosts_xml.write_text("<root/>")
    return prefs_path


def test_configure_xml_sets_port_range_and_mapping(monkeypatch, tmp_path):
    prefs_path = _write_minimal_preferences(tmp_path, "host1", "user1")

    monkeypatch.setenv("SERVICECLIENT_CALLBACK_HOST", "198.51.100.10")
    monkeypatch.setenv("SERVICECLIENT_RMI_PORT", "40000")
    monkeypatch.setenv("SERVICECLIENT_RMI_PORT_TO", "40010")
    monkeypatch.setenv("SERVICECLIENT_JAVA_OPTIONS", "-Xmx512m")
    monkeypatch.delenv("CLIENT_MAP_HOST_FROM", raising=False)
    monkeypatch.delenv("CLIENT_MAP_HOST_TO", raising=False)
    monkeypatch.delenv("CLIENT_MAP_PORT_FROM", raising=False)
    monkeypatch.delenv("CLIENT_MAP_PORT_TO", raising=False)
    monkeypatch.setattr(entrypoint, "detect_rmi_host_ip", lambda: "10.0.0.5")

    entrypoint.configure_xml("host1", "user1", base_dir=str(tmp_path))

    tree = ET.parse(prefs_path)
    connection = tree.getroot().find(".//connection")

    assert connection.get("type") == "port-range"
    assert connection.get("server-port-range-from") == "40000"
    assert connection.get("server-port-range-to") == "40010"
    assert connection.get("client-map-port-from") == ""
    assert connection.get("client-map-port-to") == ""
    assert connection.get("client-map-host-to") == ""
    assert connection.get("client-map-host-from") == ""
    assert entrypoint.os.getenv("SERVICECLIENT_JAVA_OPTIONS") == "-Xmx512m -Djava.rmi.server.hostname=198.51.100.10"


def test_configure_xml_defaults_to_constant_port(monkeypatch, tmp_path):
    prefs_path = _write_minimal_preferences(tmp_path, "host2", "user2")

    monkeypatch.setenv("SERVICECLIENT_RMI_PORT", "not-a-number")
    monkeypatch.setenv("SERVICECLIENT_RMI_PORT_TO", "also-not-a-number")
    monkeypatch.delenv("SERVICECLIENT_JAVA_OPTIONS", raising=False)
    monkeypatch.delenv("SERVICECLIENT_CALLBACK_HOST", raising=False)
    monkeypatch.setattr(entrypoint, "detect_rmi_host_ip", lambda: "10.0.0.9")

    entrypoint.configure_xml("host2", "user2", base_dir=str(tmp_path))

    tree = ET.parse(prefs_path)
    connection = tree.getroot().find(".//connection")

    assert connection.get("server-port-range-from") == entrypoint.DEFAULT_RMI_PORT
    assert connection.get("server-port-range-to") == entrypoint.DEFAULT_RMI_PORT
    assert connection.get("client-map-port-from") == ""
    assert connection.get("client-map-port-to") == ""
    assert connection.get("client-map-host-from") == ""
    assert connection.get("client-map-host-to") == ""
    assert entrypoint.os.getenv("SERVICECLIENT_JAVA_OPTIONS") == "-Djava.rmi.server.hostname=10.0.0.9"


def test_configure_xml_respects_explicit_client_mapping(monkeypatch, tmp_path):
    prefs_path = _write_minimal_preferences(tmp_path, "host3", "user3")

    monkeypatch.setenv("CLIENT_MAP_HOST_FROM", "10.0.0.7")
    monkeypatch.setenv("CLIENT_MAP_HOST_TO", "203.0.113.77")
    monkeypatch.setenv("CLIENT_MAP_PORT_FROM", "32123")
    monkeypatch.delenv("CLIENT_MAP_PORT_TO", raising=False)
    monkeypatch.delenv("SERVICECLIENT_CALLBACK_HOST", raising=False)
    monkeypatch.delenv("SERVICECLIENT_JAVA_OPTIONS", raising=False)
    monkeypatch.setattr(entrypoint, "detect_rmi_host_ip", lambda: "10.0.0.15")

    entrypoint.configure_xml("host3", "user3", base_dir=str(tmp_path))

    tree = ET.parse(prefs_path)
    connection = tree.getroot().find(".//connection")

    assert connection.get("client-map-host-from") == "10.0.0.7"
    assert connection.get("client-map-host-to") == "203.0.113.77"
    assert connection.get("client-map-port-from") == "32123"
    assert connection.get("client-map-port-to") == "32123"
    assert entrypoint.os.getenv("SERVICECLIENT_JAVA_OPTIONS") == "-Djava.rmi.server.hostname=10.0.0.15"


def _write_minimal_policy(policy_path: Path):
    policy_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<policymap>
  <policy domain="resource" name="thread" value="2"/>
  <policy domain="resource" name="memory" value="2GiB"/>
  <policy domain="resource" name="map" value="4GiB"/>
  <policy domain="resource" name="disk" value="10GiB"/>
  <policy domain="system" name="max-memory-request" value="1GiB"/>
</policymap>
"""
    )


def test_detect_container_memory_limit_bytes_prefers_finite_values(monkeypatch):
    values = {
        "/sys/fs/cgroup/memory.max": "max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(6 * entrypoint.GIB),
    }
    monkeypatch.setattr(entrypoint, "_read_first_line", lambda path: values.get(path))

    assert entrypoint.detect_container_memory_limit_bytes() == 6 * entrypoint.GIB


def test_configure_imagemagick_policy_autoconfigures_from_memory_limit(monkeypatch, tmp_path):
    policy_path = tmp_path / "policy.xml"
    _write_minimal_policy(policy_path)
    monkeypatch.setenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "true")
    monkeypatch.setenv("SVC_INSTANCES", "4")
    monkeypatch.delenv("IMAGEMAGICK_POLICY_MEMORY", raising=False)
    monkeypatch.delenv("IMAGEMAGICK_POLICY_MAP", raising=False)
    monkeypatch.delenv("IMAGEMAGICK_POLICY_THREAD", raising=False)
    monkeypatch.delenv("IMAGEMAGICK_POLICY_MAX_MEMORY_REQUEST", raising=False)
    monkeypatch.setattr(entrypoint, "detect_container_memory_limit_bytes", lambda: 6 * entrypoint.GIB)

    entrypoint.configure_imagemagick_policy(str(policy_path))

    expected = entrypoint.recommend_imagemagick_policy(6 * entrypoint.GIB, 4)
    tree = ET.parse(policy_path)
    root = tree.getroot()

    assert root.find("./policy[@domain='resource'][@name='thread']").get("value") == expected["thread"]
    assert root.find("./policy[@domain='resource'][@name='memory']").get("value") == expected["memory"]
    assert root.find("./policy[@domain='resource'][@name='map']").get("value") == expected["map"]
    assert root.find("./policy[@domain='resource'][@name='disk']").get("value") == expected["disk"]
    assert root.find("./policy[@domain='system'][@name='max-memory-request']").get("value") == expected["max-memory-request"]


def test_configure_imagemagick_policy_keeps_bundled_defaults_by_default(monkeypatch, tmp_path):
    policy_path = tmp_path / "policy.xml"
    _write_minimal_policy(policy_path)
    monkeypatch.delenv("IMAGEMAGICK_POLICY_AUTOCONFIG", raising=False)
    monkeypatch.setenv("SVC_INSTANCES", "4")
    monkeypatch.setattr(entrypoint, "detect_container_memory_limit_bytes", lambda: 6 * entrypoint.GIB)

    entrypoint.configure_imagemagick_policy(str(policy_path))

    tree = ET.parse(policy_path)
    root = tree.getroot()

    assert root.find("./policy[@domain='resource'][@name='thread']").get("value") == "2"
    assert root.find("./policy[@domain='resource'][@name='memory']").get("value") == "2GiB"
    assert root.find("./policy[@domain='resource'][@name='map']").get("value") == "4GiB"
    assert root.find("./policy[@domain='resource'][@name='disk']").get("value") == "10GiB"
    assert root.find("./policy[@domain='system'][@name='max-memory-request']").get("value") == "1GiB"


def test_configure_imagemagick_policy_allows_explicit_overrides(monkeypatch, tmp_path):
    policy_path = tmp_path / "policy.xml"
    _write_minimal_policy(policy_path)
    monkeypatch.setenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "false")
    monkeypatch.setenv("IMAGEMAGICK_POLICY_THREAD", "2")
    monkeypatch.setenv("IMAGEMAGICK_POLICY_MEMORY", "768MiB")
    monkeypatch.setenv("IMAGEMAGICK_POLICY_MAP", "1536MiB")
    monkeypatch.setenv("IMAGEMAGICK_POLICY_MAX_MEMORY_REQUEST", "384MiB")

    entrypoint.configure_imagemagick_policy(str(policy_path))

    tree = ET.parse(policy_path)
    root = tree.getroot()

    assert root.find("./policy[@domain='resource'][@name='thread']").get("value") == "2"
    assert root.find("./policy[@domain='resource'][@name='memory']").get("value") == "768MiB"
    assert root.find("./policy[@domain='resource'][@name='map']").get("value") == "1536MiB"
    assert root.find("./policy[@domain='system'][@name='max-memory-request']").get("value") == "384MiB"
