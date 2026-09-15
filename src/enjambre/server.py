"""HTTP API and dashboard, standard library only.

The same `App.handle()` serves HTTP clients and, in-process, the MCP server, so
every client of the swarm goes through exactly the same code.
"""
from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from . import __version__

log = logging.getLogger("enjambre.server")

WEB_DIR = Path(__file__).parent / "web"
MAX_BODY = 1_048_576
DRAIN_LIMIT = 4 * MAX_BODY
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _required(body: dict, key: str) -> Any:
    value = body.get(key)
    if value is None or value == "":
        raise ApiError(400, f"'{key}' is required")
    return value


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ApiError(400, "expected a list of strings")


class App:
    def __init__(self, kernel, genome=None, *, memory=None, router=None, token: str = "") -> None:
        self.kernel = kernel
        self.genome = genome
        self.memory = memory
        self.router = router
        self.token = token or ""
        self.routes: list[tuple[str, re.Pattern, Callable]] = []
        tid = r"(?P<task_id>[A-Za-z0-9_-]{1,64})"
        for method, pattern, handler in (
            ("GET", "/api/health", self.health),
            ("GET", "/api/overview", self.overview),
            ("GET", "/api/processes", lambda q, b: self.kernel.processes()),
            ("POST", "/api/heartbeat", self.heartbeat),
            ("GET", "/api/leases", lambda q, b: self.kernel.leases()),
            ("POST", "/api/leases/acquire", self.acquire),
            ("POST", "/api/leases/release", self.release),
            ("GET", "/api/tasks", self.list_tasks),
            ("POST", "/api/tasks", self.create_task),
            ("POST", "/api/tasks/claim", self.claim),
            ("GET", f"/api/tasks/{tid}", self.get_task),
            ("POST", f"/api/tasks/{tid}/complete", self.complete),
            ("POST", f"/api/tasks/{tid}/renew", self.renew),
            ("POST", f"/api/tasks/{tid}/progress", self.progress),
            ("POST", f"/api/tasks/{tid}/cancel", lambda q, b, task_id: self.kernel.cancel(task_id, str(b.get("actor", "api")))),
            ("POST", f"/api/tasks/{tid}/retry", lambda q, b, task_id: self.kernel.retry(task_id, str(b.get("actor", "api")))),
            ("GET", "/api/traces", lambda q, b: self.kernel.traces(limit=int(q.get("limit", 200)))),
            ("GET", "/api/policy", self.policy),
            ("POST", "/api/policy/check", self.check_permission),
            ("GET", "/api/router", self.route),
            ("GET", "/api/memory/graph", lambda q, b: self._memory().graph()),
            ("GET", "/api/memory/query", lambda q, b: self._memory().query(q.get("q", ""), limit=int(q.get("limit", 10)))),
            ("GET", "/api/memory/node", self.memory_node),
            ("GET", "/api/memory/pulse", lambda q, b: self._memory().pulse(int(q.get("minutes", 120)))),
        ):
            self.routes.append((method, re.compile(pattern), handler))

    # -------------------------------------------------------------- dispatch
    def authorized(self, headers: Any) -> bool:
        if not self.token:
            return True
        supplied = headers.get("X-Enjambre-Token") or ""
        auth = headers.get("Authorization") or ""
        if not supplied and auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        return hmac.compare_digest(supplied.encode(), self.token.encode())

    def handle(self, method: str, path: str, query: dict, body: dict | None, headers: Any = None) -> tuple[int, Any]:
        """`headers=None` means a trusted in-process call (the local MCP server)."""
        if headers is not None and path != "/api/health" and not self.authorized(headers):
            return 401, {"ok": False, "error": "missing or invalid token"}
        matched = False
        for route_method, pattern, handler in self.routes:
            match = pattern.fullmatch(path)
            if not match:
                continue
            matched = True
            if route_method != method:
                continue
            try:
                result = handler(query, body or {}, **match.groupdict())
            except ApiError as exc:
                return exc.status, {"ok": False, "error": str(exc)}
            except (ValueError, TypeError) as exc:
                return 400, {"ok": False, "error": f"bad request: {exc}"}
            refused = isinstance(result, dict) and result.get("ok") is False
            return (409 if refused else 200), result
        if matched:
            return 405, {"ok": False, "error": "method not allowed"}
        return 404, {"ok": False, "error": "not found"}

    # -------------------------------------------------------------- handlers
    def health(self, q: dict, b: dict) -> dict:
        return {"ok": True, "version": __version__}

    def overview(self, q: dict, b: dict) -> dict:
        g = self.genome
        return {
            "ok": True,
            "name": g.name if g else "enjambre",
            "version": __version__,
            "stats": self.kernel.stats(),
            "processes": self.kernel.processes(),
            "leases": self.kernel.leases(),
            "router": self.router.choose() if self.router else None,
            "agents": [
                {"id": a.id, "name": a.name, "host": a.host, "adapter": a.adapter, "energy": a.energy,
                 "cost": a.cost, "route": a.route}
                for a in (g.agents.values() if g else [])
            ],
            "fitness": self.kernel.fitness(),
            "policy_error": g.policy.error if g else None,
            "memory": bool(self.memory),
        }

    def heartbeat(self, q: dict, b: dict) -> dict:
        period = b.get("period_s")
        return self.kernel.heartbeat(
            str(_required(b, "id")), name=str(b.get("name", "")), host=str(b.get("host", "")),
            kind=str(b.get("kind", "agent")), task=str(b.get("task", "")), detail=str(b.get("detail", "")),
            period_s=None if period is None else int(period))

    def acquire(self, q: dict, b: dict) -> dict:
        return self.kernel.acquire(str(_required(b, "resource")), str(_required(b, "holder")),
                                   purpose=str(b.get("purpose", "")), minutes=int(b.get("minutes", 60)))

    def release(self, q: dict, b: dict) -> dict:
        return self.kernel.release(str(_required(b, "resource")), str(_required(b, "holder")))

    def list_tasks(self, q: dict, b: dict) -> list:
        return self.kernel.tasks(status=q.get("status") or None, limit=int(q.get("limit", 100)))

    def create_task(self, q: dict, b: dict) -> dict:
        return self.kernel.enqueue(
            str(_required(b, "title")), detail=str(b.get("detail", "")), priority=int(b.get("priority", 5)),
            creator=str(b.get("creator", "api")), agent=str(b.get("agent", "")),
            depends_on=_str_list(b.get("depends_on")), idempotency_key=str(b.get("idempotency_key", "")),
            proof=str(b.get("proof", "")), max_attempts=int(b.get("max_attempts", 2)),
            operation=str(b.get("operation", "")), project=str(b.get("project", "")))

    def claim(self, q: dict, b: dict) -> dict:
        agents = b.get("agents")
        task = self.kernel.claim(str(_required(b, "worker")),
                                 agents=None if agents is None else _str_list(agents),
                                 lease_minutes=int(b.get("lease_minutes", 30)))
        return {"ok": True, "task": task}

    def get_task(self, q: dict, b: dict, task_id: str) -> dict:
        task = self.kernel.get(task_id)
        if task is None:
            raise ApiError(404, "unknown task")
        task["traces"] = self.kernel.traces(task_id)
        return task

    def complete(self, q: dict, b: dict, task_id: str) -> dict:
        return self.kernel.complete(task_id, str(_required(b, "worker")), b.get("result"))

    def renew(self, q: dict, b: dict, task_id: str) -> dict:
        return self.kernel.renew(task_id, str(_required(b, "worker")), minutes=int(b.get("minutes", 30)))

    def progress(self, q: dict, b: dict, task_id: str) -> dict:
        return self.kernel.progress(task_id, str(_required(b, "worker")), str(b.get("text", "")))

    def policy(self, q: dict, b: dict) -> dict:
        if not self.genome:
            raise ApiError(404, "no genome loaded")
        return self.genome.policy.rules_for(q.get("agent", ""))

    def check_permission(self, q: dict, b: dict) -> dict:
        if not self.genome:
            raise ApiError(404, "no genome loaded")
        reason = self.genome.policy.check(str(_required(b, "agent")), str(b.get("operation", "")), str(b.get("project", "")))
        return {"allowed": reason is None, "reason": reason}

    def route(self, q: dict, b: dict) -> dict:
        if not self.router:
            raise ApiError(404, "no router configured")
        return self.router.choose()

    def _memory(self):
        if not self.memory:
            raise ApiError(404, "no memory folder configured")
        return self.memory

    def memory_node(self, q: dict, b: dict) -> dict:
        node = self._memory().node(q.get("id", ""))
        if node is None:
            raise ApiError(404, "unknown node")
        return node


def static_file(path: str) -> tuple[int, bytes, str]:
    rel = "index.html" if path in ("", "/") else path.lstrip("/")
    root = WEB_DIR.resolve()
    target = (root / rel).resolve()
    if root not in target.parents or not target.is_file():
        return 404, b"not found", "text/plain; charset=utf-8"
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
        ctype += "; charset=utf-8"
    return 200, target.read_bytes(), ctype


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"enjambre/{__version__}"
        sys_version = ""
        timeout = 60  # a silent or slow client cannot hold a thread forever

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, status: int, payload: Any, ctype: str = "application/json; charset=utf-8") -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("application/json"):
                self.send_header("Cache-Control", "no-store")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _dispatch(self, method: str) -> None:
            parts = urlsplit(self.path)
            if parts.path.startswith("/api/"):
                body = None
                if method == "POST":
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                    except ValueError:
                        return self._send(400, {"ok": False, "error": "bad Content-Length"})
                    if length < 0:
                        return self._send(400, {"ok": False, "error": "bad Content-Length"})
                    if length > MAX_BODY:
                        self.close_connection = True
                        if length <= DRAIN_LIMIT:
                            # Read what the client is already sending, so it sees the 413 instead of a reset.
                            remaining = length
                            try:
                                while remaining > 0:
                                    chunk = self.rfile.read(min(65_536, remaining))
                                    if not chunk:
                                        break
                                    remaining -= len(chunk)
                            except OSError:
                                return None
                        return self._send(413, {"ok": False, "error": "body too large"})
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                    except ValueError:
                        return self._send(400, {"ok": False, "error": "invalid JSON"})
                    if not isinstance(body, dict):
                        return self._send(400, {"ok": False, "error": "the body must be a JSON object"})
                query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
                try:
                    status, payload = app.handle(method, parts.path, query, body, self.headers)
                except Exception:  # noqa: BLE001 - never leak a traceback to a client
                    log.exception("unhandled error on %s %s", method, parts.path)
                    status, payload = 500, {"ok": False, "error": "internal error"}
                return self._send(status, payload)
            if method not in ("GET", "HEAD"):
                return self._send(405, {"ok": False, "error": "method not allowed"})
            status, data, ctype = static_file(parts.path)
            self._send(status, data, ctype)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_HEAD(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

    return Handler


def serve(app: App, host: str = "127.0.0.1", port: int = 8765, *, insecure: bool = False) -> ThreadingHTTPServer:
    if host not in LOOPBACK and not app.token and not insecure:
        raise ValueError(
            f"refusing to listen on {host} without a token: set ENJAMBRE_TOKEN, or pass --insecure if you really mean it")
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server
