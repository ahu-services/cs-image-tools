"""Unit tests for the small, pure and easily-mocked helpers in entrypoint.py.

These complement test_entrypoint.py (which focuses on the larger XML/policy
configuration flows) and exist primarily to exercise the many branch and
error-handling paths that otherwise go uncovered.
"""

import importlib.util
import os
import re
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


@pytest.fixture(autouse=True)
def _isolated_imagemagick_env(monkeypatch, tmp_path):
    """Keep policy tests away from the host: temp dir, env overrides, ECS."""
    for name in list(entrypoint.os.environ):
        if name.startswith("IMAGEMAGICK_"):
            monkeypatch.delenv(name, raising=False)
    for name in ("ECS_CONTAINER_METADATA_URI_V4", "CONTAINER_MEMORY_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("MAGICK_TEMPORARY_PATH", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))


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
        ("2021.1.11", 11),  # 202101 <= 202201, pre-installed 2021.1 images
        ("2022.1", 11),  # 202201 <= 202201
        ("2024.3", 17),  # 202403 <= 202403
        ("2025.1", 21),  # 202501 <= 202599
        ("2025.3", 21),  # all 2025.x releases need Java 21
        ("2026.1", 25),  # censhare 2026.1 requires Java 25
        ("2026.1", entrypoint.JAVA_DEFAULT),  # newer than all windows
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


# --------------------------------------------------------------------------- #
# Java matrix: Dockerfile (build-time pre-install) must match entrypoint.py
# --------------------------------------------------------------------------- #
def _dockerfile_java_matrix():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    windows = [
        (int(bound), int(jdk))
        for bound, jdk in re.findall(
            r'"\$RELEASE_KEY" -le (\d+) \]; then \\\s*JDK_RESOLVED=(\d+)',
            dockerfile,
        )
    ]
    default = re.search(r"else \\\s*JDK_RESOLVED=(\d+)", dockerfile)
    return windows, int(default.group(1)) if default else None


def test_dockerfile_java_matrix_matches_entrypoint():
    windows, default = _dockerfile_java_matrix()
    assert windows == entrypoint.JAVA_WINDOWS
    assert default == entrypoint.JAVA_DEFAULT


# --------------------------------------------------------------------------- #
# Credential handling
# --------------------------------------------------------------------------- #
def test_dockerfile_uses_build_secrets_not_credential_args():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*ARG\s+REPO_(USER|PASS)\b", dockerfile, re.MULTILINE)
    assert "--mount=type=secret,id=repo_user" in dockerfile
    assert "--mount=type=secret,id=repo_pass" in dockerfile
    assert re.search(r"^ARG VERSION$", dockerfile, re.MULTILINE)


def test_client_download_url_contains_no_credentials():
    url = entrypoint.client_download_url("2021.1.11")
    assert url == (
        "https://rpm.censhare.com/censhare-release/censhare-Service/v2021.1.11"
        "/Shell/censhare-Service-Client-v2021.1.11.tar.gz"
    )
    assert "@" not in url


def test_download_unpack_sends_credentials_as_basic_auth(monkeypatch, tmp_path):
    calls = {}

    class DummyResponse:
        status_code = 401

    def fake_get(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return DummyResponse()

    monkeypatch.setattr(entrypoint.requests, "get", fake_get)
    url = entrypoint.client_download_url("2025.2.0")
    with pytest.raises(SystemExit):
        entrypoint.download_unpack(
            url, str(tmp_path / "out.tar.gz"), auth=("user", "p@ss:w/rd#1")
        )
    assert calls["url"] == url
    assert calls["kwargs"]["auth"] == ("user", "p@ss:w/rd#1")


def test_scrub_environment_hides_secrets_from_child_processes(monkeypatch):
    monkeypatch.setenv("REPO_USER", "repo-user")
    monkeypatch.setenv("REPO_PASS", "repo-secret")
    monkeypatch.setenv("SVC_PASS", "svc-secret")
    monkeypatch.setenv("SVC_HOST", "host.example.com")

    entrypoint.scrub_environment(entrypoint.STARTUP_SECRET_ENV_VARS)

    for name in entrypoint.STARTUP_SECRET_ENV_VARS:
        assert name not in os.environ
    child_env = subprocess.run(
        [sys.executable, "-c", "import os; print(sorted(os.environ))"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "REPO_PASS" not in child_env
    assert "SVC_PASS" not in child_env
    assert "SVC_HOST" in child_env


def test_scrub_environment_ignores_missing_variables(monkeypatch):
    monkeypatch.delenv("VOLUMES_INFO", raising=False)
    entrypoint.scrub_environment(entrypoint.CONFIG_SECRET_ENV_VARS)
    assert "VOLUMES_INFO" not in os.environ


# --------------------------------------------------------------------------- #
# ImageMagick policy sizing, overrides and diagnostics
# --------------------------------------------------------------------------- #
GIB = entrypoint.GIB
MIB = entrypoint.MIB

BUNDLED_POLICY = """<?xml version="1.0" encoding="UTF-8"?>
<policymap>
  <policy domain="resource" name="thread" value="2"/>
  <policy domain="resource" name="time" value="120"/>
  <policy domain="resource" name="memory" value="2GiB"/>
  <policy domain="resource" name="map" value="4GiB"/>
  <policy domain="resource" name="area" value="400MP"/>
  <policy domain="resource" name="disk" value="10GiB"/>
  <policy domain="resource" name="list-length" value="256"/>
  <policy domain="resource" name="width" value="32KP"/>
  <policy domain="resource" name="height" value="32KP"/>
  <policy domain="cache" name="memory-map" value="anonymous"/>
  <policy domain="system" name="shred" value="1"/>
  <policy domain="system" name="max-memory-request" value="1536MiB"/>
</policymap>
"""


def _policy(tmp_path, content=BUNDLED_POLICY):
    path = tmp_path / "policy.xml"
    path.write_text(content)
    return path


def _value(path, name, domain="resource"):
    node = (
        ET.parse(path).getroot().find(f"./policy[@domain='{domain}'][@name='{name}']")
    )
    return node.get("value") if node is not None else None


def _fake_host(monkeypatch, memory=None, cpu=(1.0, "cgroup"), free=45 * GIB):
    monkeypatch.setattr(
        entrypoint, "detect_container_memory_limit_bytes", lambda: memory
    )
    monkeypatch.setattr(entrypoint, "detect_cpu_limit", lambda ecs_limits=None: cpu)
    monkeypatch.setattr(
        entrypoint.shutil,
        "disk_usage",
        lambda path: entrypoint.shutil._ntuple_diskusage(0, 0, free),
    )


@pytest.mark.parametrize(
    ("memory", "instances", "cpus", "expected"),
    [
        # Fargate 1 vCPU / 8 GB / 4 instances (current production size)
        (8 * GIB, 4, 1.0, ("1", "512MiB", "1GiB", "9GiB")),
        # Fargate 2 vCPU / 16 GB / 8 instances (planned for busy servers)
        (16 * GIB, 8, 2.0, ("1", "512MiB", "1GiB", "4608MiB")),
        # Small single-instance container
        (4 * GIB, 1, 1.0, ("1", "512MiB", "1GiB", "36GiB")),
        # Large container: pixel cache budget grows, capped by the bundled values
        (32 * GIB, 4, 4.0, ("1", "1664MiB", "3328MiB", "9GiB")),
    ],
)
def test_recommend_policy_sizes(memory, instances, cpus, expected):
    result = entrypoint.recommend_imagemagick_policy(memory, instances, cpus, 45 * GIB)
    thread, memory_limit, map_limit, disk = expected
    assert result == {
        "thread": thread,
        "memory": memory_limit,
        "map": map_limit,
        "disk": disk,
        "max-memory-request": "1536MiB",
    }


def test_max_memory_request_is_independent_of_container_size():
    small = entrypoint.recommend_imagemagick_policy(2 * GIB, 8)
    large = entrypoint.recommend_imagemagick_policy(64 * GIB, 1)
    assert small["max-memory-request"] == large["max-memory-request"] == "1536MiB"


@pytest.mark.parametrize(
    ("memory", "fits"), [(4 * GIB, 1), (8 * GIB, 2), (16 * GIB, 4)]
)
def test_large_jobs_that_fit(memory, fits):
    assert entrypoint.large_jobs_that_fit(memory) == fits


def test_threads_follow_cpu_quota_per_instance():
    assert entrypoint.recommend_imagemagick_policy(8 * GIB, 2, 4.0)["thread"] == "2"
    assert entrypoint.recommend_imagemagick_policy(8 * GIB, 1, 16.0)["thread"] == "4"
    assert entrypoint.recommend_imagemagick_policy(8 * GIB, 4, 0.5)["thread"] == "1"


def test_disk_without_temp_information_uses_memory_based_fallback():
    assert entrypoint.recommend_imagemagick_policy(8 * GIB, 4)["disk"] == "9728MiB"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1536MiB", 1536 * MIB),
        ("2GiB", 2 * GIB),
        ("1.5GiB", int(1.5 * GIB)),
        ("1GB", 1000**3),
        ("1048576", 1048576),
        ("lots", None),
        (None, None),
    ],
)
def test_parse_policy_size(value, expected):
    assert entrypoint.parse_policy_size(value) == expected


def test_read_policy_overrides_maps_names_and_validates():
    overrides, ignored = entrypoint.read_policy_overrides(
        {
            "IMAGEMAGICK_POLICY_TIME": "300",
            "IMAGEMAGICK_POLICY_LIST_LENGTH": "64",
            "IMAGEMAGICK_POLICY_WIDTH": "40KP",
            "IMAGEMAGICK_POLICY_AREA": "1GP",
            "IMAGEMAGICK_POLICY_MAX_MEMORY_REQUEST": "2GiB",
            "IMAGEMAGICK_POLICY_TEMPORARY_PATH": "/data/magick",
            "IMAGEMAGICK_POLICY_THREAD": "0",
            "IMAGEMAGICK_POLICY_HEIGHT": "tall",
            "IMAGEMAGICK_POLICY_SHRED": "0",
            "IMAGEMAGICK_POLICY_AUTOCONFIG": "true",
            "IMAGEMAGICK_POLICY_MEMORY": "  ",
            "OTHER": "1",
        }
    )
    assert overrides == {
        "time": "300",
        "list-length": "64",
        "width": "40KP",
        "area": "1GP",
        "max-memory-request": "2GiB",
        "temporary-path": "/data/magick",
    }
    assert ignored == [
        "IMAGEMAGICK_POLICY_HEIGHT=tall (invalid value)",
        "IMAGEMAGICK_POLICY_SHRED (unknown policy setting)",
        "IMAGEMAGICK_POLICY_THREAD=0 (invalid value)",
    ]


def test_configure_policy_logs_values_with_sources(monkeypatch, tmp_path, capsys):
    path = _policy(tmp_path)
    _fake_host(monkeypatch, memory=8 * GIB)
    monkeypatch.setenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "true")
    monkeypatch.setenv("SVC_INSTANCES", "4")
    monkeypatch.setenv("IMAGEMAGICK_POLICY_TIME", "300")
    monkeypatch.setenv("IMAGEMAGICK_POLICY_WIDTH", "bogus")
    monkeypatch.setenv("IMAGEMAGICK_TIMEOUT", "600")

    entrypoint.configure_imagemagick_policy(str(path))
    out = capsys.readouterr().out

    assert "detected: memory limit 8.0GiB (cgroup), SVC_INSTANCES=4" in out
    assert "cpu 1 (cgroup)" in out
    assert "memory=512MiB (autoconfig)" in out
    assert "disk=9GiB (autoconfig)" in out
    assert "max-memory-request=1536MiB (autoconfig)" in out
    assert "time=300 (env IMAGEMAGICK_POLICY_TIME)" in out
    assert "width=32KP (bundled)" in out
    assert "ignored: IMAGEMAGICK_POLICY_WIDTH=bogus (invalid value)" in out
    assert "imagemagick facility timeout: 600s (IMAGEMAGICK_TIMEOUT)" in out
    assert "WARNING" not in out
    assert _value(path, "time") == "300"
    assert _value(path, "width") == "32KP"
    assert _value(path, "shred", "system") == "1"
    assert f"temp {tmp_path} free 45.0GiB" in out


def test_autoconfig_never_lowers_bundled_max_memory_request(monkeypatch, tmp_path):
    path = _policy(tmp_path, BUNDLED_POLICY.replace('"1536MiB"', '"3GiB"'))
    _fake_host(monkeypatch, memory=8 * GIB)
    monkeypatch.setenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "true")

    entrypoint.configure_imagemagick_policy(str(path))

    assert _value(path, "max-memory-request", "system") == "3GiB"
    assert _value(path, "memory") == "512MiB"


def test_explicit_env_may_lower_max_memory_request(monkeypatch, tmp_path):
    path = _policy(tmp_path)
    _fake_host(monkeypatch, memory=8 * GIB)
    monkeypatch.setenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "true")
    monkeypatch.setenv("IMAGEMAGICK_POLICY_MAX_MEMORY_REQUEST", "256MiB")

    entrypoint.configure_imagemagick_policy(str(path))

    assert _value(path, "max-memory-request", "system") == "256MiB"


def test_removed_override_is_reverted_on_restart(monkeypatch, tmp_path):
    path = _policy(tmp_path)
    _fake_host(monkeypatch)
    monkeypatch.setenv("IMAGEMAGICK_POLICY_MEMORY", "768MiB")
    entrypoint.configure_imagemagick_policy(str(path))
    assert _value(path, "memory") == "768MiB"

    monkeypatch.delenv("IMAGEMAGICK_POLICY_MEMORY")
    entrypoint.configure_imagemagick_policy(str(path))

    assert _value(path, "memory") == "2GiB"
    assert (tmp_path / "policy.xml.dist").exists()


def test_autoconfig_without_memory_limit_warns_and_keeps_bundled(
    monkeypatch, tmp_path, capsys
):
    path = _policy(tmp_path)
    _fake_host(monkeypatch, memory=None)
    monkeypatch.setenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "true")

    entrypoint.configure_imagemagick_policy(str(path))
    out = capsys.readouterr().out

    assert "no memory limit was detected" in out
    assert _value(path, "memory") == "2GiB"
    assert _value(path, "max-memory-request", "system") == "1536MiB"


def test_autoconfig_warns_when_large_jobs_do_not_fit(monkeypatch, tmp_path, capsys):
    path = _policy(tmp_path)
    _fake_host(monkeypatch, memory=6 * GIB)
    monkeypatch.setenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "true")
    monkeypatch.setenv("SVC_INSTANCES", "4")

    entrypoint.configure_imagemagick_policy(str(path))

    assert "memory for 1 concurrent large layered-TIFF" in capsys.readouterr().out


def test_autoconfig_falls_back_to_ecs_task_metadata(monkeypatch, tmp_path, capsys):
    path = _policy(tmp_path)
    monkeypatch.setattr(entrypoint, "detect_container_memory_limit_bytes", lambda: None)
    monkeypatch.setattr(entrypoint, "_read_first_line", lambda path: None)
    monkeypatch.setattr(
        entrypoint.shutil,
        "disk_usage",
        lambda path: entrypoint.shutil._ntuple_diskusage(0, 0, 45 * GIB),
    )
    monkeypatch.setenv("IMAGEMAGICK_POLICY_AUTOCONFIG", "true")
    monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4/x")
    requested = []

    class Response:
        status_code = 200

        def json(self):
            return {"Limits": {"CPU": 1, "Memory": 8192}}

    def fake_get(url, timeout):
        requested.append((url, timeout))
        return Response()

    monkeypatch.setattr(entrypoint.requests, "get", fake_get)

    entrypoint.configure_imagemagick_policy(str(path))
    out = capsys.readouterr().out

    assert requested == [("http://169.254.170.2/v4/x/task", 1.0)]
    assert "memory limit 8.0GiB (ECS task metadata)" in out
    assert "cpu 1 (ECS task metadata)" in out
    assert _value(path, "disk") == "9GiB"


def test_read_ecs_task_limits_handles_errors(monkeypatch, capsys):
    assert entrypoint.read_ecs_task_limits() is None
    monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4/x")

    def boom(url, timeout):
        raise entrypoint.requests.ConnectionError("down")

    monkeypatch.setattr(entrypoint.requests, "get", boom)
    assert entrypoint.read_ecs_task_limits() is None
    assert "Unable to read ECS task metadata" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("files", "ecs", "expected"),
    [
        ({"/sys/fs/cgroup/cpu.max": "200000 100000"}, None, (2.0, "cgroup")),
        (
            {
                "/sys/fs/cgroup/cpu.max": "max 100000",
                "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "50000",
                "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000",
            },
            None,
            (0.5, "cgroup"),
        ),
        (
            {"/sys/fs/cgroup/cpu.max": "max 100000"},
            {"CPU": 2},
            (2.0, "ECS task metadata"),
        ),
    ],
)
def test_detect_cpu_limit(monkeypatch, files, ecs, expected):
    monkeypatch.setattr(entrypoint, "_read_first_line", lambda path: files.get(path))
    assert entrypoint.detect_cpu_limit(ecs) == expected


def test_detect_cpu_limit_falls_back_to_online_cpus(monkeypatch):
    monkeypatch.setattr(entrypoint, "_read_first_line", lambda path: None)
    monkeypatch.setattr(entrypoint.os, "cpu_count", lambda: 3)
    assert entrypoint.detect_cpu_limit() == (3.0, "online CPUs")


def test_read_cgroup_memory_peak(monkeypatch):
    values = {"/sys/fs/cgroup/memory/memory.max_usage_in_bytes": "1024"}
    monkeypatch.setattr(entrypoint, "_read_first_line", lambda path: values.get(path))
    assert entrypoint._read_cgroup_memory_peak() == 1024


def test_configure_policy_without_variables_keeps_bundled_values(
    monkeypatch, tmp_path, capsys
):
    path = _policy(tmp_path)
    _fake_host(monkeypatch, memory=8 * GIB)

    entrypoint.configure_imagemagick_policy(str(path))
    out = capsys.readouterr().out

    assert "(auto-configuration disabled)" in out
    assert "memory limit 8.0GiB (cgroup)" in out
    assert "(autoconfig)" not in out
    assert "(env " not in out
    for name, value in (("memory", "2GiB"), ("map", "4GiB"), ("thread", "2")):
        assert _value(path, name) == value
    assert _value(path, "max-memory-request", "system") == "1536MiB"
    assert _value(path, "temporary-path") is None


def test_container_memory_limit_variable_is_used_before_ecs(monkeypatch):
    monkeypatch.setattr(entrypoint, "detect_container_memory_limit_bytes", lambda: None)
    monkeypatch.setenv("CONTAINER_MEMORY_LIMIT", "16GiB")
    monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4/x")

    def must_not_call(*args, **kwargs):
        raise AssertionError("ECS metadata must not be queried")

    monkeypatch.setattr(entrypoint.requests, "get", must_not_call)

    assert entrypoint.detect_memory_limit() == (
        16 * GIB,
        "CONTAINER_MEMORY_LIMIT",
        None,
    )


def test_invalid_container_memory_limit_is_ignored(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint, "detect_container_memory_limit_bytes", lambda: None)
    monkeypatch.setenv("CONTAINER_MEMORY_LIMIT", "lots")

    assert entrypoint.detect_memory_limit() == (None, None, None)
    assert "Ignoring invalid CONTAINER_MEMORY_LIMIT=lots" in capsys.readouterr().out


def test_cgroup_limit_wins_over_container_memory_limit(monkeypatch):
    monkeypatch.setattr(
        entrypoint, "detect_container_memory_limit_bytes", lambda: 4 * GIB
    )
    monkeypatch.setenv("CONTAINER_MEMORY_LIMIT", "16GiB")
    assert entrypoint.detect_memory_limit() == (4 * GIB, "cgroup", None)


def test_temporary_path_override_is_created(monkeypatch, tmp_path):
    path = _policy(tmp_path)
    _fake_host(monkeypatch)
    target = tmp_path / "custom-temp"
    monkeypatch.setenv("IMAGEMAGICK_POLICY_TEMPORARY_PATH", str(target))

    entrypoint.configure_imagemagick_policy(str(path))

    assert target.is_dir()
    assert _value(path, "temporary-path") == str(target)


def test_unusable_temporary_path_override_is_ignored(monkeypatch, tmp_path, capsys):
    path = _policy(tmp_path)
    _fake_host(monkeypatch)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("IMAGEMAGICK_POLICY_TEMPORARY_PATH", str(blocker / "t"))

    entrypoint.configure_imagemagick_policy(str(path))

    assert "ignored: IMAGEMAGICK_POLICY_TEMPORARY_PATH" in capsys.readouterr().out
    assert _value(path, "temporary-path") is None


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "/tmp"),
        ({"TMPDIR": "/var/tmp"}, "/var/tmp"),
        ({"TMPDIR": "/var/tmp", "MAGICK_TEMPORARY_PATH": "/scratch"}, "/scratch"),
    ],
)
def test_imagemagick_temp_dir(monkeypatch, env, expected):
    monkeypatch.delenv("TMPDIR", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert entrypoint.imagemagick_temp_dir() == expected
    assert entrypoint.imagemagick_temp_dir("/policy/tmp") == "/policy/tmp"


def test_low_temp_space_warns_at_start(monkeypatch, tmp_path, capsys):
    path = _policy(tmp_path)
    _fake_host(monkeypatch, free=20 * GIB)
    monkeypatch.setenv("SVC_INSTANCES", "4")

    entrypoint.configure_imagemagick_policy(str(path))

    assert "less than disk=10GiB x SVC_INSTANCES=4" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("low 0\nhigh 0\nmax 3\noom 1\noom_kill 2\noom_group_kill 0\n", 2),
        ("oom_kill_disable 0\nunder_oom 0\noom_kill 5\n", 5),
        ("oom_kill_disable 0\nunder_oom 0\n", None),
    ],
)
def test_read_oom_kill_count_from_cgroup_files(
    monkeypatch, tmp_path, content, expected
):
    events = tmp_path / "memory.events"
    events.write_text(content)
    monkeypatch.setattr(entrypoint, "CGROUP_OOM_EVENT_FILES", (str(events),))
    monkeypatch.setattr(entrypoint, "_read_first_line", lambda path: None)

    count, source = entrypoint.read_oom_kill_count()

    assert count == expected
    assert source == ("memory.events" if expected is not None else None)


def test_read_oom_kill_count_falls_back_to_failcnt(monkeypatch, tmp_path):
    monkeypatch.setattr(
        entrypoint, "CGROUP_OOM_EVENT_FILES", (str(tmp_path / "missing"),)
    )
    monkeypatch.setattr(entrypoint, "_read_first_line", lambda path: "7")
    assert entrypoint.read_oom_kill_count() == (7, "memory.failcnt")


def test_memory_monitor_logs_only_rising_values(monkeypatch, capsys):
    peaks = iter([1 * GIB, 1 * GIB, 3 * GIB, 3 * GIB])
    ooms = iter([(0, "memory.events")] * 2 + [(2, "memory.events")] * 2)
    monkeypatch.setattr(entrypoint, "_read_cgroup_memory_peak", lambda: next(peaks))
    monkeypatch.setattr(entrypoint, "read_oom_kill_count", lambda: next(ooms))
    sleeps = []

    class Stop(Exception):
        pass

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 3:
            raise Stop

    monkeypatch.setattr(entrypoint.time, "sleep", fake_sleep)
    with pytest.raises(Stop):
        entrypoint.container_memory_monitor_loop(interval=60)
    out = capsys.readouterr().out

    assert sleeps == [60, 60, 60, 60]
    assert out.count("Container memory peak") == 1
    assert "Container memory peak: 3.0GiB." in out
    assert out.count("killed for lack of memory") == 1
    assert "2 process(es)" in out
    assert "before monitoring" not in out


def test_memory_monitor_reports_earlier_oom_kills(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint, "_read_cgroup_memory_peak", lambda: None)
    monkeypatch.setattr(entrypoint, "read_oom_kill_count", lambda: (4, "memory.events"))

    class Stop(Exception):
        pass

    def stop(seconds):
        raise Stop

    monkeypatch.setattr(entrypoint.time, "sleep", stop)
    with pytest.raises(Stop):
        entrypoint.container_memory_monitor_loop()

    assert "OOM kills before monitoring: 4 (memory.events)" in capsys.readouterr().out


def test_start_container_memory_monitor_runs_as_daemon(monkeypatch):
    started = []
    monkeypatch.setattr(
        entrypoint, "container_memory_monitor_loop", lambda: started.append(True)
    )
    thread = entrypoint.start_container_memory_monitor()
    thread.join(timeout=5)
    assert started == [True]
    assert thread.daemon
