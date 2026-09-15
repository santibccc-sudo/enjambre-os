import http.server
import json
import sys
import threading

import pytest

from enjambre.adapters.cli import CliAdapter, batch_launcher
from enjambre.adapters.openai import OpenAIAdapter
from enjambre.genome import AgentSpec

TASK = {"id": "t1", "title": "hello"}


def cli(tmp_path, script, **options):
    spec = AgentSpec(id="py", adapter="cli", options={"command": [sys.executable, "-c", script], **options})
    return CliAdapter(spec, tmp_path)


def run(adapter, prompt="hi", cancel=None):
    return adapter.run(TASK, prompt, cancel or threading.Event())


def test_cli_json_contract(tmp_path):
    script = "import json,sys; print(json.dumps({'status':'completed','result':sys.stdin.read().upper()}))"
    res = run(cli(tmp_path, script), "shout")
    assert res.ok and res.result == "SHOUT"


def test_cli_text_mode_and_exit_codes(tmp_path):
    assert run(cli(tmp_path, "print('plain')", contract="text")).result == "plain"
    res = run(cli(tmp_path, "import sys; print('partial'); sys.exit(3)"))
    assert res.error_code == "nonzero_exit" and "code 3" in res.error and res.result.strip() == "partial"


def test_cli_prompt_as_argument_without_a_shell(tmp_path):
    spec = AgentSpec(id="py", adapter="cli", options={
        "command": [sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}"],
        "stdin": False, "contract": "text"})
    res = run(CliAdapter(spec, tmp_path), "$(rm -rf /); echo pwned")
    assert res.result == "$(rm -rf /); echo pwned"


def test_cli_timeout_and_cancel(tmp_path):
    slow = "import time; time.sleep(30)"
    assert run(cli(tmp_path, slow, timeout_s=0.5)).error_code == "timeout"
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    assert run(cli(tmp_path, slow), cancel=cancel).status == "cancelled"


def test_cli_environment_is_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "s3cr3t")
    script = "import os; print(os.environ.get('SECRET_TOKEN', 'absent'))"
    assert run(cli(tmp_path, script, contract="text")).result == "absent"
    assert run(cli(tmp_path, script, contract="text", env=["SECRET_TOKEN"])).result == "s3cr3t"


def test_cli_resolves_programs_on_path_and_relative_to_cwd(tmp_path):
    spec = AgentSpec(id="py", adapter="cli", options={"command": [sys.executable, "-c", "print('ok')"], "contract": "text"})
    adapter = CliAdapter(spec, tmp_path)
    assert adapter._resolve("python3-that-does-not-exist-4242", {"PATH": ""}) is None
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "agent.sh").write_text("#!/bin/sh\n")
    assert adapter._resolve("tools/agent.sh", {}) == str(tmp_path / "tools" / "agent.sh")
    assert run(adapter).result == "ok"


def test_windows_batch_launchers_never_receive_the_prompt_as_an_argument(tmp_path, monkeypatch):
    assert batch_launcher(r"C:\Users\me\AppData\Roaming\npm\claude.CMD", "win32")
    assert not batch_launcher("/usr/local/bin/claude", "linux")
    assert not batch_launcher(r"C:\tools\agent.exe", "win32")
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off\n")
    monkeypatch.setattr("enjambre.adapters.cli.batch_launcher", lambda path, platform="win32": path.endswith(".cmd"))
    unsafe = CliAdapter(AgentSpec(id="c", adapter="cli", options={"command": [str(shim), "-p", "{prompt}"], "stdin": False}), tmp_path)
    res = run(unsafe, "& del important.txt")
    assert res.error_code == "adapter_config" and "stdin: true" in res.error


def test_cli_configuration_errors(tmp_path):
    with pytest.raises(ValueError, match="command"):
        CliAdapter(AgentSpec(id="x", adapter="cli", options={"command": "claude -p"}), tmp_path)
    with pytest.raises(ValueError, match="contract"):
        CliAdapter(AgentSpec(id="x", adapter="cli", options={"command": ["x"], "contract": "yaml"}), tmp_path)
    missing = CliAdapter(AgentSpec(id="x", adapter="cli", options={"command": ["definitely-not-a-binary-4242"]}), tmp_path)
    assert run(missing).error_code == "adapter_start_failed"


class FakeProvider(http.server.BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeProvider.seen.append((self.headers.get("Authorization"), body))
        if FakeProvider.status != 200:
            self.send_response(FakeProvider.status)
            self.end_headers()
            self.wfile.write(b'{"error": "slow down"}')
            return
        reply = {"model": body["model"], "usage": {"total_tokens": 7},
                 "choices": [{"message": {"content": '{"status": "completed", "result": "hola"}'}}]}
        payload = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def provider():
    FakeProvider.seen, FakeProvider.status = [], 200
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_openai_compatible(provider, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "abc123")
    spec = AgentSpec(id="w", adapter="openai",
                     options={"base_url": provider, "model": "tiny", "api_key_env": "TEST_PROVIDER_KEY", "temperature": 0.2})
    res = run(OpenAIAdapter(spec), "say hola")
    assert res.ok and res.result == "hola"
    assert res.meta["usage"]["total_tokens"] == 7
    auth, body = FakeProvider.seen[0]
    assert auth == "Bearer abc123"
    assert body["messages"][0]["content"] == "say hola" and body["temperature"] == 0.2


def test_openai_errors(provider):
    spec = AgentSpec(id="w", adapter="openai", options={"base_url": provider, "model": "tiny"})
    FakeProvider.status = 429
    assert run(OpenAIAdapter(spec)).error_code == "rate_limited"
    dead = AgentSpec(id="w", adapter="openai", options={"base_url": "http://127.0.0.1:9/v1", "model": "tiny", "timeout_s": 2})
    assert run(OpenAIAdapter(dead)).error_code == "provider_unreachable"


def test_openai_refuses_keys_in_config():
    with pytest.raises(ValueError, match="never put API keys"):
        OpenAIAdapter(AgentSpec(id="w", adapter="openai", options={"model": "m", "api_key": "sk-live"}))
    with pytest.raises(ValueError, match="model"):
        OpenAIAdapter(AgentSpec(id="w", adapter="openai", options={}))
