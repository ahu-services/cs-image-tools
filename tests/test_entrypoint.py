import hashlib
import importlib.util
import sys
from pathlib import Path


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
