import http.server
import os
import threading
import time

import pytest

from enjambre import proof


def test_files(tmp_path):
    f = tmp_path / "a.txt"
    assert proof.check(str(f)) == (False, "does not exist")
    f.write_text("")
    assert proof.check(str(f)) == (False, "empty file")
    f.write_text("x")
    assert proof.check(str(f))[0]
    assert proof.check("relative/path.txt") == (False, "not an absolute path")


def test_freshness(tmp_path):
    f = tmp_path / "old.txt"
    f.write_text("x")
    old = time.time() - 3_600
    os.utime(f, (old, old))
    assert not proof.check(str(f), since=time.time() - 60)[0]
    assert proof.check(str(f), since=old - 1)[0]


def test_directories_need_a_fresh_entry(tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    old = time.time() - 3_600
    os.utime(d, (old, old))
    assert not proof.check(str(d), since=time.time() - 60)[0]
    (d / "index.html").write_text("<h1>hi</h1>")
    assert proof.check(str(d), since=time.time() - 60)[0]


def test_verify_needs_one_passing_target(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("x")
    passed, detail = proof.verify(["/nope", str(f)])
    assert passed and "does not exist" in detail
    assert proof.verify([]) == (False, "no proof criteria")


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200 if self.path == "/ok" else 404)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_urls(server):
    assert proof.check(f"{server}/ok")[0]
    assert proof.check(f"{server}/missing") == (False, "HTTP 404")
    assert proof.check(f"{server}/ok", allow_urls=False) == (False, "url checks disabled")
