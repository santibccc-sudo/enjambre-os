import http.client
import json
import threading
import urllib.error
import urllib.request

import pytest

from enjambre import Kernel
from enjambre.memory import MemoryGraph
from enjambre.router import Router
from enjambre.server import MAX_BODY, App, serve, static_file

from .helpers import make_genome

TOKEN = "s3cret-token"


@pytest.fixture
def swarm(tmp_path):
    genome = make_genome(tmp_path / "swarm", "writer:\n  adapter: scripted\n  options: {delay_s: 0}",
                         policy="operations:\n  publish: [editor]\n", extra="memory:\n  dir: memory")
    (genome.root / "memory").mkdir()
    (genome.root / "memory" / "leases.md").write_text("# Leases\n\nEvery lock expires. See [[kernel]].\n")
    kernel = Kernel(genome.db_path)
    app = App(kernel, genome, memory=MemoryGraph(genome.memory_dir), router=Router(genome, kernel), token=TOKEN)
    server = serve(app, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", kernel
    server.shutdown()
    server.server_close()


def call(base, method, path, body=None, token=TOKEN, headers=None, data=None):
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=payload, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def test_auth(swarm):
    base, _ = swarm
    assert call(base, "GET", "/api/health", token="")[0] == 200
    assert call(base, "GET", "/api/overview", token="")[0] == 401
    assert call(base, "GET", "/api/overview", token="wrong")[0] == 401
    assert call(base, "GET", "/api/overview")[0] == 200
    assert call(base, "GET", "/api/overview", token="", headers={"X-Enjambre-Token": TOKEN})[0] == 200


def test_task_lifecycle_over_http(swarm, tmp_path):
    base, _ = swarm
    status, created = call(base, "POST", "/api/tasks", {"title": "Write notes", "agent": "writer", "priority": 2})
    assert status == 200 and created["ok"]
    status, claimed = call(base, "POST", "/api/tasks/claim", {"worker": "remote-agent", "agents": ["writer", ""]})
    assert claimed["task"]["id"] == created["id"]
    assert call(base, "POST", f"/api/tasks/{created['id']}/renew", {"worker": "remote-agent", "minutes": 5})[1]["ok"]
    artifact = tmp_path / "notes.md"
    artifact.write_text("notes")
    status, closed = call(base, "POST", f"/api/tasks/{created['id']}/complete", {
        "worker": "remote-agent", "result": {"status": "completed", "result": "done", "artifacts": [{"path": str(artifact)}]}})
    assert status == 200 and closed["verification"] == "verified"
    status, task = call(base, "GET", f"/api/tasks/{created['id']}")
    assert task["status"] == "done" and [t["event"] for t in task["traces"]] == ["enqueued", "claimed", "done"]
    assert call(base, "GET", "/api/tasks?status=done")[1][0]["id"] == created["id"]


def test_errors(swarm):
    base, _ = swarm
    tid = call(base, "POST", "/api/tasks", {"title": "t"})[1]["id"]
    assert call(base, "POST", f"/api/tasks/{tid}/complete", {"worker": "nobody", "result": "x"})[0] == 409
    assert call(base, "GET", "/api/tasks/doesnotexist")[0] == 404
    assert call(base, "GET", "/api/leases/acquire")[0] == 405
    assert call(base, "POST", "/api/tasks", {"detail": "no title"})[0] == 400
    assert call(base, "POST", "/api/tasks", data=b"{not json")[0] == 400
    assert call(base, "POST", "/api/tasks", data=b"[1, 2]")[0] == 400
    assert call(base, "GET", "/api/nowhere")[0] == 404
    assert call(base, "POST", "/api/heartbeat", {"id": "../../x"})[0] == 400
    assert call(base, "POST", "/api/tasks", data=b"x" * (MAX_BODY + 1))[0] == 413


def test_hostile_content_lengths_never_hang_the_server(swarm):
    base, _ = swarm
    host, port = base.removeprefix("http://").split(":")
    for claimed, expected in ((50 * MAX_BODY, 413), (-5, 400)):
        conn = http.client.HTTPConnection(host, int(port), timeout=5)
        conn.putrequest("POST", "/api/tasks")
        conn.putheader("Authorization", f"Bearer {TOKEN}")
        conn.putheader("Content-Length", str(claimed))
        conn.endheaders()
        assert conn.getresponse().status == expected
        conn.close()


def test_policy_router_and_memory(swarm):
    base, _ = swarm
    assert call(base, "POST", "/api/policy/check", {"agent": "writer", "operation": "publish"})[1]["allowed"] is False
    assert call(base, "GET", "/api/router")[1]["agent"] == "writer"
    graph = call(base, "GET", "/api/memory/graph")[1]
    assert graph["stats"] == {"nodes": 2, "notes": 1, "missing": 1, "links": 1}
    assert call(base, "GET", "/api/memory/query?q=lock")[1]["results"][0]["label"] == "Leases"
    assert call(base, "GET", "/api/memory/node?id=nope")[0] == 404
    assert "alive" in call(base, "GET", "/api/memory/pulse?minutes=10")[1]


def test_dashboard_is_served_with_security_headers(swarm):
    base, _ = swarm
    host, port = base.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.request("GET", "/")
    resp = conn.getresponse()
    assert resp.status == 200 and b"<html" in resp.read()
    assert "default-src 'self'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    for sneaky in ("/../pyproject.toml", "/%2e%2e/%2e%2e/etc/passwd", "/vendor/../../server.py"):
        conn.request("GET", sneaky)
        r = conn.getresponse()
        r.read()
        assert r.status == 404, sneaky


def test_static_file_never_leaves_the_web_folder():
    assert static_file("/../server.py")[0] == 404
    assert static_file("/../../../../etc/passwd")[0] == 404
    assert static_file("/")[0] == 200


def test_refuses_to_expose_itself_without_a_token(tmp_path):
    app = App(Kernel(tmp_path / "k.db"))
    with pytest.raises(ValueError, match="without a token"):
        serve(app, "0.0.0.0", 0)
    serve(app, "0.0.0.0", 0, insecure=True).server_close()
