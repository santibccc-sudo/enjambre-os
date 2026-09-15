import io
import json
import subprocess
import sys
import threading

import pytest

from enjambre import Kernel
from enjambre.cli import build_app
from enjambre.genome import Genome
from enjambre.mcp import TOOLS, HttpTransport, LocalTransport, McpServer
from enjambre.server import serve

from .helpers import make_genome

AGENTS = "writer:\n  adapter: scripted\n  options: {delay_s: 0}"


@pytest.fixture
def mcp(tmp_path):
    genome = make_genome(tmp_path / "swarm", AGENTS, policy="operations:\n  publish: [editor]\n")
    kernel = Kernel(genome.db_path)
    return McpServer(LocalTransport(build_app(genome, kernel))), kernel, tmp_path


def rpc(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def tool(server, name, **args):
    result = rpc(server, "tools/call", {"name": name, "arguments": args})["result"]
    return json.loads(result["content"][0]["text"]), result["isError"]


def test_handshake_and_tool_list(mcp):
    server, _, _ = mcp
    init = rpc(server, "initialize", {"protocolVersion": "2024-11-05"})["result"]
    assert init["protocolVersion"] == "2024-11-05" and init["serverInfo"]["name"] == "enjambre"
    assert rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-06-18"
    names = {t["name"] for t in rpc(server, "tools/list")["result"]["tools"]}
    assert {"claim_task", "complete_task", "memory_query", "check_permission"} <= names
    assert all(t["inputSchema"]["type"] == "object" for t in TOOLS)
    assert rpc(server, "ping")["result"] == {}


def test_an_mcp_agent_does_real_work(mcp):
    server, kernel, tmp = mcp
    created, err = tool(server, "enqueue_task", title="Write the changelog", agent="writer", proof=str(tmp / "CHANGELOG.md"))
    assert not err and created["ok"]
    tool(server, "heartbeat", id="claude-code", task="changelog")
    claimed, _ = tool(server, "claim_task", worker="claude-code", agents=["writer"])
    assert claimed["task"]["title"] == "Write the changelog"
    (tmp / "CHANGELOG.md").write_text("## 0.1.0\n")
    closed, err = tool(server, "complete_task", task_id=created["id"], worker="claude-code", status="completed",
                       result="written", artifacts=[{"path": str(tmp / "CHANGELOG.md")}])
    assert not err and closed["verification"] == "verified"
    task, _ = tool(server, "get_task", task_id=created["id"])
    assert task["status"] == "done"
    denied, _ = tool(server, "check_permission", agent="claude-code", operation="publish")
    assert denied["allowed"] is False
    status, _ = tool(server, "swarm_status")
    assert status["stats"]["tasks"]["done"] == 1


def test_bad_calls_are_tool_errors_not_crashes(mcp):
    server, _, _ = mcp
    assert tool(server, "claim_task")[1] is True
    assert "unknown: sneaky" in tool(server, "claim_task", worker="w", sneaky=1)[0]["error"]
    assert tool(server, "get_task", task_id="../../api/overview")[1] is True
    assert tool(server, "no_such_tool")[1] is True
    assert tool(server, "complete_task", task_id="abc", worker="w", status="completed")[1] is True
    assert rpc(server, "resources/list")["error"]["code"] == -32601
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"id": 3, "method": "tools/list"})["error"]["code"] == -32600


def test_stdio_loop_handles_garbage_and_batches(mcp):
    server, _, _ = mcp
    server.stdin = io.StringIO(
        "not json\n\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "ping"}]) + "\n")
    server.stdout = io.StringIO()
    server.serve()
    lines = [json.loads(line) for line in server.stdout.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700
    assert [r["id"] for r in lines[1]] == [1, 2]
    assert len(lines) == 2


def test_real_stdio_process(tmp_path):
    make_genome(tmp_path / "swarm", AGENTS)
    proc = subprocess.Popen([sys.executable, "-m", "enjambre", "mcp", "--dir", str(tmp_path / "swarm")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "enqueue_task", "arguments": {"title": "hi"}}},
    ]
    out, err = proc.communicate("\n".join(json.dumps(m) for m in messages) + "\n", timeout=30)
    replies = [json.loads(line) for line in out.splitlines()]
    assert [r["id"] for r in replies] == [1, 2], err
    assert json.loads(replies[1]["result"]["content"][0]["text"])["ok"]


def test_remote_mode_over_http(tmp_path):
    genome = make_genome(tmp_path / "swarm", AGENTS)
    kernel = Kernel(genome.db_path)
    http = serve(build_app(genome, kernel, token="tok"), "127.0.0.1", 0)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{http.server_address[1]}"
        good = McpServer(HttpTransport(url, "tok"))
        assert tool(good, "enqueue_task", title="remote")[0]["ok"]
        bad = McpServer(HttpTransport(url, "wrong"))
        assert tool(bad, "list_tasks")[1] is True
        gone = McpServer(HttpTransport("http://127.0.0.1:9", "tok", timeout=2))
        assert "unreachable" in tool(gone, "list_tasks")[0]["error"]
    finally:
        http.shutdown()
        http.server_close()
