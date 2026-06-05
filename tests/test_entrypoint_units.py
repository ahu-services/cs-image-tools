"""Unit tests for the small, pure and easily-mocked helpers in entrypoint.py.

These complement test_entrypoint.py (which focuses on the larger XML/policy
configuration flows) and exist primarily to exercise the many branch and
error-handling paths that otherwise go uncovered.
"""

import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT_PATH = REPO_ROOT / "entrypoint.py"

spec = importlib.util.spec_from_file_location(
    "entrypoint_units_module", ENTRYPOINT_PATH
)
entrypoint = importlib.util.module_from_spec(spec)
sys.modules["entrypoint_units_module"] = entrypoint
spec.loader.exec_module(entrypoint)


# --------------------------------------------------------------------------- #
# str_to_bool
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    ["true", "TRUE", " True ", "1", "t", "y", "yes", "YES"],
)
def test_str_to_bool_truthy(value):
    assert entrypoint.str_to_bool(value) is True


@pytest.mark.parametrize(
    "value",
    ["false", "0", "n", "no", "maybe", "", "   ", None],
)
def test_str_to_bool_falsy(value):
    assert entrypoint.str_to_bool(value) is False


# --------------------------------------------------------------------------- #
# _read_first_line
# --------------------------------------------------------------------------- #
def test_read_first_line_returns_stripped_first_line(tmp_path):
    target = tmp_path / "value.txt"
    target.write_text("  first line  \nsecond line\n")
    assert entrypoint._read_first_line(str(target)) == "first line"


def test_read_first_line_missing_file_returns_none(tmp_path):
    assert entrypoint._read_first_line(str(tmp_path / "nope.txt")) is None


# --------------------------------------------------------------------------- #
# _parse_positive_int
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,default,expected",
    [
        ("5", 4, 5),
        (7, 4, 7),
        ("0", 4, 4),
        ("-3", 4, 4),
        ("abc", 9, 9),
        (None, 2, 2),
    ],
)
def test_parse_positive_int(value, default, expected):
    assert entrypoint._parse_positive_int(value, default) == expected


# --------------------------------------------------------------------------- #
# _clamp / _round_down / _format_binary_size
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,minimum,maximum,expected",
    [(5, 0, 10, 5), (-1, 0, 10, 0), (15, 0, 10, 10)],
)
def test_clamp(value, minimum, maximum, expected):
    assert entrypoint._clamp(value, minimum, maximum) == expected


def test_round_down_floors_to_step():
    assert entrypoint._round_down(130, 64) == 128


def test_round_down_never_below_step():
    assert entrypoint._round_down(10, 64) == 64


def test_format_binary_size_uses_gib_for_exact_multiples():
    assert entrypoint._format_binary_size(2 * entrypoint.GIB) == "2GiB"


def test_format_binary_size_uses_mib_otherwise():
    assert entrypoint._format_binary_size(100 * entrypoint.MIB) == "100MiB"


def test_format_binary_size_floors_to_at_least_one_mib():
    assert entrypoint._format_binary_size(entrypoint.MIB // 2) == "1MiB"


# --------------------------------------------------------------------------- #
# detect_container_memory_limit_bytes
# --------------------------------------------------------------------------- #
def test_detect_memory_limit_skips_unlimited_and_invalid(monkeypatch):
    values = {
        "/sys/fs/cgroup/memory.max": "max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes": "not-an-int",
    }
    monkeypatch.setattr(entrypoint, "_read_first_line", lambda path: values.get(path))
    assert entrypoint.detect_container_memory_limit_bytes() is None


def test_detect_memory_limit_ignores_sentinel_huge_value(monkeypatch):
    # cgroup v1 reports a near-max sentinel for "unlimited"; it must be ignored.
    monkeypatch.setattr(entrypoint, "_read_first_line", lambda path: str(1 << 62))
    assert entrypoint.detect_container_memory_limit_bytes() is None


# --------------------------------------------------------------------------- #
# recommend_imagemagick_policy
# --------------------------------------------------------------------------- #
def test_recommend_policy_single_worker_uses_two_threads():
    result = entrypoint.recommend_imagemagick_policy(6 * entrypoint.GIB, 1)
    assert result["thread"] == "2"
    assert set(result) == {"thread", "memory", "map", "disk", "max-memory-request"}


def test_recommend_policy_multi_worker_uses_single_thread():
    result = entrypoint.recommend_imagemagick_policy(6 * entrypoint.GIB, 4)
    assert result["thread"] == "1"


# --------------------------------------------------------------------------- #
# _set_policy_value
# --------------------------------------------------------------------------- #
def test_set_policy_value_creates_missing_policy():
    root = ET.Element("policymap")
    entrypoint._set_policy_value(root, "resource", "memory", "512MiB")
    created = root.find("./policy[@domain='resource'][@name='memory']")
    assert created is not None
    assert created.get("value") == "512MiB"


def test_set_policy_value_updates_existing_policy():
    root = ET.Element("policymap")
    ET.SubElement(root, "policy", {"domain": "resource", "name": "map", "value": "1"})
    entrypoint._set_policy_value(root, "resource", "map", "2GiB")
    assert root.find("./policy[@name='map']").get("value") == "2GiB"


# --------------------------------------------------------------------------- #
# configure_imagemagick_policy — error branches
# --------------------------------------------------------------------------- #
def test_configure_policy_warns_when_file_missing(tmp_path, capsys):
    entrypoint.configure_imagemagick_policy(str(tmp_path / "absent.xml"))
    assert "not found" in capsys.readouterr().out


def test_configure_policy_warns_on_parse_error(tmp_path, capsys):
    bad = tmp_path / "policy.xml"
    bad.write_text("<not-closed>")
    entrypoint.configure_imagemagick_policy(str(bad))
    assert "Unable to parse" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# select_jdk_major
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "version,expected",
    [
        (None, entrypoint.JAVA_DEFAULT),
        ("2022", entrypoint.JAVA_DEFAULT),  # too few parts
        ("abc.def", entrypoint.JAVA_DEFAULT),  # unparseable
        ("2022.1", 11),  # 202201 <= 202201
        ("2024.3", 17),  # 202403 <= 202403
        ("2025.1", entrypoint.JAVA_DEFAULT),  # newer than all windows
    ],
)
def test_select_jdk_major(version, expected):
    assert entrypoint.select_jdk_major(version) == expected


# --------------------------------------------------------------------------- #
# store_client_version
# --------------------------------------------------------------------------- #
def test_store_client_version_writes_file(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "client-version.txt"
    monkeypatch.setattr(entrypoint, "CLIENT_VERSION_FILE", str(target))
    entrypoint.store_client_version("2024.2.0")
    assert target.read_text() == "2024.2.0"


def test_store_client_version_noop_for_empty_value(monkeypatch, tmp_path):
    target = tmp_path / "client-version.txt"
    monkeypatch.setattr(entrypoint, "CLIENT_VERSION_FILE", str(target))
    entrypoint.store_client_version("")
    assert not target.exists()


def test_store_client_version_warns_on_oserror(monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(entrypoint.os, "makedirs", boom)
    entrypoint.store_client_version("2024.2.0")
    assert "Unable to persist client version" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# detect_rmi_host_ip
# --------------------------------------------------------------------------- #
def test_detect_rmi_host_ip_prefers_non_loopback_hostname(monkeypatch):
    monkeypatch.setattr(entrypoint.socket, "gethostname", lambda: "host")
    monkeypatch.setattr(
        entrypoint.socket,
        "gethostbyname_ex",
        lambda name: (name, [], ["10.0.0.42"]),
    )
    assert entrypoint.detect_rmi_host_ip() == "10.0.0.42"


def test_detect_rmi_host_ip_falls_back_to_route(monkeypatch):
    monkeypatch.setattr(entrypoint.socket, "gethostname", lambda: "host")
    monkeypatch.setattr(
        entrypoint.socket,
        "gethostbyname_ex",
        lambda name: (name, [], ["127.0.0.1"]),
    )
    monkeypatch.setattr(
        entrypoint.subprocess, "check_output", lambda *a, **k: "10.0.0.99\n"
    )
    assert entrypoint.detect_rmi_host_ip() == "10.0.0.99"


def test_detect_rmi_host_ip_returns_none_when_all_loopback(monkeypatch):
    def raise_gaierror(_name):
        raise entrypoint.socket.gaierror

    monkeypatch.setattr(entrypoint.socket, "gethostname", lambda: "host")
    monkeypatch.setattr(entrypoint.socket, "gethostbyname_ex", raise_gaierror)

    def raise_subprocess(*_a, **_k):
        raise subprocess.SubprocessError

    monkeypatch.setattr(entrypoint.subprocess, "check_output", raise_subprocess)
    assert entrypoint.detect_rmi_host_ip() is None


# --------------------------------------------------------------------------- #
# apply_rmi_callback_host
# --------------------------------------------------------------------------- #
def test_apply_rmi_callback_uses_explicit_host(monkeypatch):
    monkeypatch.delenv("SERVICECLIENT_JAVA_OPTIONS", raising=False)
    entrypoint.apply_rmi_callback_host("203.0.113.5")
    assert (
        entrypoint.os.environ["SERVICECLIENT_JAVA_OPTIONS"]
        == "-Djava.rmi.server.hostname=203.0.113.5"
    )


def test_apply_rmi_callback_preserves_existing_option(monkeypatch):
    monkeypatch.setenv(
        "SERVICECLIENT_JAVA_OPTIONS",
        "-Xmx1g -Djava.rmi.server.hostname=10.1.1.1",
    )
    monkeypatch.setattr(entrypoint, "detect_rmi_host_ip", lambda: "10.9.9.9")
    entrypoint.apply_rmi_callback_host("")
    opts = entrypoint.os.environ["SERVICECLIENT_JAVA_OPTIONS"]
    assert "-Djava.rmi.server.hostname=10.1.1.1" in opts
    assert "-Xmx1g" in opts
    assert "10.9.9.9" not in opts


def test_apply_rmi_callback_warns_when_unresolvable(monkeypatch, capsys):
    monkeypatch.delenv("SERVICECLIENT_JAVA_OPTIONS", raising=False)
    monkeypatch.setattr(entrypoint, "detect_rmi_host_ip", lambda: None)
    entrypoint.apply_rmi_callback_host("")
    assert "Unable to determine callback host" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# handle_office_facility
# --------------------------------------------------------------------------- #
def test_handle_office_facility_disabled_without_url():
    facility = ET.Element("facility", {"key": "office", "enabled": "true"})
    entrypoint.handle_office_facility(facility, office_url="")
    assert facility.get("enabled") == "false"


def test_handle_office_facility_enables_on_success(monkeypatch):
    facility = ET.Element("facility", {"key": "office"})

    class DummyResponse:
        status = 200

    class DummyPool:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            return DummyResponse()

    monkeypatch.setattr(entrypoint.urllib3, "PoolManager", DummyPool)
    entrypoint.handle_office_facility(facility, office_url="https://office:9980")

    path_element = facility.find(".//path[@key='@@OFFICE@@']")
    assert path_element is not None
    assert path_element.get("port") == "https://office:9980"


def test_handle_office_facility_disables_on_non_200(monkeypatch):
    facility = ET.Element("facility", {"key": "office", "enabled": "true"})

    class DummyResponse:
        status = 503

    class DummyPool:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            return DummyResponse()

    monkeypatch.setattr(entrypoint.urllib3, "PoolManager", DummyPool)
    entrypoint.handle_office_facility(facility, office_url="https://office:9980")
    assert facility.get("enabled") == "false"


def test_handle_office_facility_disables_on_http_error(monkeypatch):
    facility = ET.Element("facility", {"key": "office", "enabled": "true"})

    class DummyPool:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise entrypoint.HTTPError("boom")

    monkeypatch.setattr(entrypoint.urllib3, "PoolManager", DummyPool)
    entrypoint.handle_office_facility(facility, office_url="https://office:9980")
    assert facility.get("enabled") == "false"


# --------------------------------------------------------------------------- #
# setup_icc_profiles
# --------------------------------------------------------------------------- #
def test_setup_icc_profiles_copies_files(tmp_path, capsys):
    source = tmp_path / "src"
    source.mkdir()
    (source / "srgb.icc").write_text("profile-bytes")
    target = tmp_path / "dst"

    entrypoint.setup_icc_profiles(str(source), str(target))

    assert (target / "srgb.icc").read_text() == "profile-bytes"
    assert "Copied ICC profiles" in capsys.readouterr().out


def test_setup_icc_profiles_noop_when_source_missing(tmp_path, capsys):
    entrypoint.setup_icc_profiles(str(tmp_path / "absent"), str(tmp_path / "dst"))
    assert "No ICC profiles found" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# update_volumes_configuration
# --------------------------------------------------------------------------- #
def test_update_volumes_configuration_noop_when_unset(monkeypatch, capsys):
    monkeypatch.delenv("VOLUMES_INFO", raising=False)
    entrypoint.update_volumes_configuration("/nonexistent/hosts.xml")
    assert "not set" in capsys.readouterr().out


def test_update_volumes_configuration_noop_on_invalid_json(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("VOLUMES_INFO", "{not-json")
    hosts = tmp_path / "hosts.xml"
    hosts.write_text("<root/>")
    entrypoint.update_volumes_configuration(str(hosts))
    assert "not valid JSON" in capsys.readouterr().out


def test_update_volumes_configuration_rewrites_hosts(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "VOLUMES_INFO",
        '{"images": {"path": "/data/images", "writable": true}}',
    )
    hosts = tmp_path / "hosts.xml"
    hosts.write_text(
        """<root>
  <host>
    <volumes><volume filesystemname="stale"/></volumes>
  </host>
</root>
"""
    )
    entrypoint.update_volumes_configuration(str(hosts))

    root = ET.parse(hosts).getroot()
    volume = root.find(".//volume")
    assert volume.get("filesystemname") == "images"
    assert volume.get("path") == "/data/images"
    assert volume.get("writable") == "true"  # bool coerced to lowercase string
    assert root.find(".//censhare-vfs").get("use") == "0"


# --------------------------------------------------------------------------- #
# run_as_corpus
# --------------------------------------------------------------------------- #
def _completed(stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout=stdout, stderr=stderr
    )


def test_run_as_corpus_uses_runuser(monkeypatch, capsys):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _completed(stdout="ok\n")

    monkeypatch.setattr(entrypoint.subprocess, "run", fake_run)
    result = entrypoint.run_as_corpus(["echo", "hi"])
    assert captured["cmd"][:3] == ["runuser", "-u", "corpus"]
    assert result.stdout == "ok\n"
    assert "ok" in capsys.readouterr().out


def test_run_as_corpus_falls_back_to_su(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[0] == "runuser":
            raise FileNotFoundError
        return _completed(stdout="via-su\n")

    monkeypatch.setattr(entrypoint.subprocess, "run", fake_run)
    result = entrypoint.run_as_corpus(["echo", "hi"])
    assert result.stdout == "via-su\n"


def test_run_as_corpus_returns_error_on_failure(monkeypatch):
    error = subprocess.CalledProcessError(returncode=3, cmd="x", stderr="bad\n")

    def fake_run(cmd, **kwargs):
        raise error

    monkeypatch.setattr(entrypoint.subprocess, "run", fake_run)
    result = entrypoint.run_as_corpus(["echo", "hi"])
    assert result is error


# --------------------------------------------------------------------------- #
# wait_for_log_file
# --------------------------------------------------------------------------- #
def test_wait_for_log_file_found_immediately(monkeypatch, tmp_path):
    log = tmp_path / "service.log"
    log.write_text("ready")
    assert entrypoint.wait_for_log_file(str(log)) is True


def test_wait_for_log_file_times_out(monkeypatch):
    times = iter([0, 100])
    monkeypatch.setattr(entrypoint.time, "time", lambda: next(times))
    monkeypatch.setattr(entrypoint.os.path, "exists", lambda _p: False)
    monkeypatch.setattr(entrypoint.time, "sleep", lambda _s: None)
    assert entrypoint.wait_for_log_file("/tmp/never.log", timeout=60) is False


# --------------------------------------------------------------------------- #
# stop_service_client / signal_handler
# --------------------------------------------------------------------------- #
def test_stop_service_client_terminates_running_pid(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.subprocess, "check_output", lambda *a, **k: "123\n")
    monkeypatch.setattr(entrypoint.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(entrypoint.os.path, "exists", lambda _p: False)
    monkeypatch.setattr(entrypoint.time, "sleep", lambda _s: None)

    entrypoint.stop_service_client()
    assert "Service client stopped." in capsys.readouterr().out


def test_stop_service_client_handles_missing_process(monkeypatch, capsys):
    def raise_called(*_a, **_k):
        raise subprocess.CalledProcessError(returncode=1, cmd="jps")

    monkeypatch.setattr(entrypoint.subprocess, "check_output", raise_called)
    entrypoint.stop_service_client()
    assert "No ServiceClient process found." in capsys.readouterr().out


def test_signal_handler_stops_and_exits(monkeypatch):
    calls = []
    monkeypatch.setattr(entrypoint, "stop_service_client", lambda: calls.append(True))
    with pytest.raises(SystemExit) as excinfo:
        entrypoint.signal_handler(15, None)
    assert excinfo.value.code == 0
    assert calls == [True]


# --------------------------------------------------------------------------- #
# ensure_corretto
# --------------------------------------------------------------------------- #
def _version_run_factory(version_major):
    """subprocess.run stub: report a java -version, succeed for everything else."""

    def fake_run(cmd, **kwargs):
        if "-version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="", stderr=f'openjdk version "{version_major}.0.1"\n'
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return fake_run


def test_ensure_corretto_reuses_matching_runtime(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: "/usr/bin/java")
    monkeypatch.setattr(entrypoint.subprocess, "run", _version_run_factory(17))
    monkeypatch.setattr(entrypoint.os.path, "realpath", lambda p: p)
    monkeypatch.setattr(entrypoint.os.path, "exists", lambda _p: True)

    entrypoint.ensure_corretto(17)

    assert "Using existing Corretto JDK 17." in capsys.readouterr().out
    assert entrypoint.os.environ["JAVA_HOME"] == "/usr"


def test_ensure_corretto_installs_on_version_mismatch(monkeypatch, capsys):
    removed = []
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: "/usr/bin/java")
    monkeypatch.setattr(entrypoint.subprocess, "run", _version_run_factory(11))
    monkeypatch.setattr(entrypoint.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(entrypoint.os.path, "realpath", lambda p: p)
    monkeypatch.setattr(entrypoint.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(entrypoint.os, "remove", lambda p: removed.append(p))

    entrypoint.ensure_corretto(17)

    out = capsys.readouterr().out
    assert "Installing Corretto JDK 17" in out
    assert removed  # the downloaded .deb is cleaned up


def test_ensure_corretto_recovers_from_dpkg_failure(monkeypatch):
    state = {"dpkg_calls": 0}

    def fake_run(cmd, **kwargs):
        if "-version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="", stderr='version "11"\n'
            )
        if cmd[0] == "dpkg":
            state["dpkg_calls"] += 1
            if state["dpkg_calls"] == 1:
                raise subprocess.CalledProcessError(returncode=1, cmd="dpkg")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: "/usr/bin/java")
    monkeypatch.setattr(entrypoint.subprocess, "run", fake_run)
    monkeypatch.setattr(entrypoint.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(entrypoint.os.path, "realpath", lambda p: p)
    monkeypatch.setattr(entrypoint.os.path, "exists", lambda _p: False)

    entrypoint.ensure_corretto(21)

    # First dpkg fails, apt-get fixes deps, second dpkg succeeds.
    assert state["dpkg_calls"] == 2


def test_ensure_corretto_rejects_unsupported_arch(monkeypatch):
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)
    monkeypatch.setattr(entrypoint.platform, "machine", lambda: "riscv64")
    with pytest.raises(RuntimeError, match="Unsupported architecture"):
        entrypoint.ensure_corretto(21)


# --------------------------------------------------------------------------- #
# download_unpack — failure path
# --------------------------------------------------------------------------- #
def test_download_unpack_exits_on_http_error(monkeypatch, tmp_path):
    class DummyResponse:
        status_code = 404

    monkeypatch.setattr(
        entrypoint.requests, "get", lambda url, **kwargs: DummyResponse()
    )
    with pytest.raises(SystemExit) as excinfo:
        entrypoint.download_unpack(
            "https://example.com/x.tar.gz", str(tmp_path / "out.tar.gz")
        )
    assert excinfo.value.code == 1


# --------------------------------------------------------------------------- #
# misc
# --------------------------------------------------------------------------- #
def test_get_path_map_returns_facility_map():
    assert entrypoint.get_path_map() is entrypoint.FACILITY_PATH_MAP


def test_determine_serviceclient_version_warns_on_missing_file(tmp_path, capsys):
    missing = tmp_path / "serviceclient.sh"
    assert entrypoint._determine_serviceclient_version(str(missing)) is None
    assert "Unable to read" in capsys.readouterr().out
