"""Run any command-line agent: Claude Code, Codex, aider, your own script.

    agents:
      coder:
        adapter: cli
        options:
          command: ["claude", "-p", "--output-format", "text"]
          stdin: true              # the prompt goes through stdin (default)
          contract: json           # json: parse the output contract · text: exit code decides
          timeout_s: 1800
          env: [ANTHROPIC_API_KEY] # only these variables are passed through

The command is never run through a shell, and the child only sees PATH, HOME,
locale variables and the ones listed in `env`: no secret leaks by default.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..contract import AgentResult

_BASE_ENV = ("PATH", "PATHEXT", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL", "TMPDIR", "TEMP", "SYSTEMROOT")


def batch_launcher(executable: str, platform: str = sys.platform) -> bool:
    """On Windows, .bat/.cmd files re-parse their arguments through cmd.exe, so
    untrusted text passed as an argument could run commands."""
    return platform == "win32" and executable.lower().endswith((".bat", ".cmd"))


class CliAdapter:
    def __init__(self, spec, root: Path) -> None:
        opts = spec.options
        command = opts.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError(f"agents.{spec.id}.options.command must be a non-empty list of strings")
        contract = opts.get("contract", "json")
        if contract not in ("json", "text"):
            raise ValueError(f"agents.{spec.id}.options.contract must be 'json' or 'text'")
        self.spec = spec
        self.command = command
        self.stdin = bool(opts.get("stdin", True))
        self.contract = contract
        self.timeout_s = float(opts.get("timeout_s", 1800))
        self.cwd = (Path(root) / str(opts["cwd"])).resolve() if opts.get("cwd") else Path(root)
        self.env_names = [str(n) for n in opts.get("env") or []]

    def run(self, task: dict, prompt: str, cancel: threading.Event) -> AgentResult:
        args = self.command if self.stdin else [a.replace("{prompt}", prompt) for a in self.command]
        env = {k: os.environ[k] for k in (*_BASE_ENV, *self.env_names) if k in os.environ}
        executable = self._resolve(args[0], env)
        if executable is None:
            return AgentResult.failed(f"cannot find {args[0]} (is it installed and on PATH?)", "adapter_start_failed")
        if batch_launcher(executable) and not self.stdin:
            return AgentResult.failed(
                f"{Path(executable).name} is a Windows batch launcher: the prompt cannot be passed safely as an "
                "argument. Set options.stdin: true for this agent.", "adapter_config")
        args = [executable, *args[1:]]
        try:
            proc = subprocess.Popen(
                args, cwd=self.cwd, env=env, text=True,
                stdin=subprocess.PIPE if self.stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except OSError as exc:
            return AgentResult.failed(f"cannot start {args[0]}: {exc.strerror or exc}", "adapter_start_failed")

        out: dict[str, str] = {}

        def pump() -> None:
            out["stdout"], out["stderr"] = proc.communicate(prompt if self.stdin else None)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        deadline = time.monotonic() + self.timeout_s
        while reader.is_alive():
            reader.join(0.2)
            if reader.is_alive() and cancel.is_set():
                _stop(proc, reader)
                return AgentResult(status="cancelled", error="cancelled while running", error_code="cancelled")
            if reader.is_alive() and time.monotonic() > deadline:
                _stop(proc, reader)
                return AgentResult.failed(f"timed out after {self.timeout_s:.0f}s", "timeout")

        stdout, stderr = out.get("stdout") or "", out.get("stderr") or ""
        return self._interpret(args, proc.returncode, stdout, stderr)

    def _resolve(self, command: str, env: dict) -> str | None:
        """Full path of the program. On Windows this also finds npm's .cmd shims (PATHEXT)."""
        if os.path.dirname(command):
            candidate = command if os.path.isabs(command) else str(self.cwd / command)
            return candidate if os.path.exists(candidate) else None
        return shutil.which(command, path=env.get("PATH"))

    def _interpret(self, args: list[str], returncode: int, stdout: str, stderr: str) -> AgentResult:
        if returncode != 0:
            return AgentResult.failed(
                f"{Path(args[0]).name} exited with code {returncode}: {stderr.strip()[-500:]}",
                "nonzero_exit", result=stdout[-2_000:])
        if self.contract == "text":
            return AgentResult.completed(stdout.strip())
        return AgentResult.parse(stdout)


def _stop(proc: subprocess.Popen, reader: threading.Thread) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    reader.join(5)
