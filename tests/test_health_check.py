import socketserver
import threading
import time

import health_check


class _NoopHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.recv(1)
        except Exception:
            pass


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
