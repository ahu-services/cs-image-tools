import contextlib
import re
import socketserver
import subprocess
import threading
import time

import health_check


class _NoopHandler(socketserver.BaseRequestHandler):
    def handle(self):
        with contextlib.suppress(Exception):
            self.request.recv(1)


def test_check_log_file_matches_pattern(tmp_path):
    log = tmp_path / "service.log"
    log.write_text("noise\nINFO line that matches\nmore noise\n")
    assert health_check.check_log_file(str(log), re.compile("matches")) is True


def test_check_log_file_no_match(tmp_path):
    log = tmp_path / "service.log"
    log.write_text("nothing relevant here\n")
    assert health_check.check_log_file(str(log), re.compile("matches")) is False


def test_check_log_file_missing_file(tmp_path):
    assert (
        health_check.check_log_file(str(tmp_path / "absent.log"), re.compile("x"))
        is False
    )


def test_check_java_process_true(monkeypatch):
    monkeypatch.setattr(
        health_check.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=b"42"),
    )
    assert health_check.check_java_process() is True


def test_check_java_process_handles_error(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise OSError("pgrep missing")

    monkeypatch.setattr(health_check.subprocess, "run", boom)
    assert health_check.check_java_process() is False
    assert "Error checking Java process" in capsys.readouterr().out


def test_check_tcp_connection_detects_established(monkeypatch):
    monkeypatch.setattr(
        health_check.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=b"State ESTAB ...\n"),
    )
    assert health_check.check_tcp_connection() is True


def test_check_tcp_connection_handles_error(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise OSError("ss missing")

    monkeypatch.setattr(health_check.subprocess, "run", boom)
    assert health_check.check_tcp_connection() is False
    assert "Error checking TCP connections" in capsys.readouterr().out


def test_health_check_fails_when_java_down(monkeypatch, capsys):
    monkeypatch.setattr(health_check, "check_java_process", lambda: False)
    assert health_check.health_check() == 1
    assert "Java process not running." in capsys.readouterr().out


def test_health_check_fails_without_login(monkeypatch):
    monkeypatch.setattr(health_check, "check_java_process", lambda: True)
    monkeypatch.setattr(health_check, "check_log_file", lambda *a, **k: False)
    assert health_check.health_check() == 1


def test_health_check_fails_without_registration(monkeypatch):
    monkeypatch.setattr(health_check, "check_java_process", lambda: True)
    # login pattern matches, registration pattern does not
    calls = {"n": 0}

    def fake_log(_path, _pattern):
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(health_check, "check_log_file", fake_log)
    assert health_check.health_check() == 1


def test_health_check_fails_without_tcp(monkeypatch):
    monkeypatch.setattr(health_check, "check_java_process", lambda: True)
    monkeypatch.setattr(health_check, "check_log_file", lambda *a, **k: True)
    monkeypatch.setattr(health_check, "check_tcp_connection", lambda: False)
    assert health_check.health_check() == 1


def test_resolve_rmi_port_defaults(monkeypatch):
    monkeypatch.delenv("SERVICECLIENT_RMI_PORT", raising=False)
    assert health_check.resolve_rmi_port() == int(health_check.DEFAULT_RMI_PORT)

    monkeypatch.setenv("SERVICECLIENT_RMI_PORT", "not-a-number")
    assert health_check.resolve_rmi_port() == int(health_check.DEFAULT_RMI_PORT)


def test_check_rmi_port_open(monkeypatch):
    server = socketserver.TCPServer(("127.0.0.1", 0), _NoopHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        assert health_check.check_rmi_port_open(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    time.sleep(0.05)
    assert not health_check.check_rmi_port_open(port)


def test_health_check_includes_rmi_port(monkeypatch):
    monkeypatch.setattr(health_check, "check_java_process", lambda: True)
    monkeypatch.setattr(health_check, "check_log_file", lambda *args, **kwargs: True)
    monkeypatch.setattr(health_check, "check_tcp_connection", lambda: True)
    monkeypatch.setattr(health_check, "resolve_rmi_port", lambda: 12345)
    monkeypatch.setattr(health_check, "check_rmi_port_open", lambda port: port == 12345)

    assert health_check.health_check() == 0

    monkeypatch.setattr(health_check, "check_rmi_port_open", lambda port: False)
    assert health_check.health_check() == 1
