"""The kernel: processes, resource leases and a durable task queue on one SQLite file.

Design rules, each one paid for by a real incident in the swarm this came from:

* A process never declares itself alive. Its state is derived from the age of
  its last heartbeat, so a dead process cannot lie: it simply stops beating.
* Every lock is a lease. A holder that dies without releasing loses the resource
  when the lease expires; nobody has to clean up after it.
* Claiming work is atomic. Two workers never get the same task.
* A running task is a lease too. Silent workers get their task taken back and
  retried; when attempts run out it goes to the dead-letter state with a reason.
* Dependencies form a DAG. A task only runs when everything upstream is done,
  and when an upstream task fails for good its dependents are marked dead
  instead of waiting forever.
* "Done" is checked, not believed: tasks can carry a proof of delivery that the
  kernel verifies itself.
"""
from __future__ import annotations

import re
import statistics
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

from . import db, proof
from .contract import AgentResult

FRESH_S = 180
STALE_S = 600
PURGE_S = 3600
LEASE_MAX_MIN = 240
TASK_LEASE_MIN = 30
DEFAULT_MAX_ATTEMPTS = 2
MAX_RESULT_CHARS = 8_000

TERMINAL = ("done", "failed", "dead", "cancelled")
NON_RETRYABLE = frozenset({"permission_denied", "upstream_failed", "invalid_task", "no_agent", "adapter_config"})

_ID_RX = re.compile(r"^[A-Za-z0-9][\w.:@/+-]{0,63}$")


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID_RX.match(value):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def _clamp(value: Any, lo: int, hi: int) -> int:
    return max(lo, min(int(value), hi))


def _fresh_after(period_s: int) -> float:
    return max(FRESH_S, period_s * 1.2)


def _stale_after(period_s: int) -> float:
    return max(STALE_S, period_s * 2)


def _clip_result(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    cut = len(text) - MAX_RESULT_CHARS
    # Say that it was cut: a truncated result must never read as a complete one.
    return text[:MAX_RESULT_CHARS] + f"\n\n[... {cut} characters truncated]"


class Kernel:
    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] | None = None,
        proof_mode: str = "record",
        allow_url_proof: bool = True,
        resources: Iterable[str] | None = None,
    ) -> None:
        if proof_mode not in ("record", "enforce"):
            raise ValueError("proof_mode must be 'record' or 'enforce'")
        self.path = Path(path)
        self.proof_mode = proof_mode
        self.allow_url_proof = allow_url_proof
        self.resources = tuple(resources) if resources else None
        self._clock = clock or time.time
        db.init(self.path)

    def now(self) -> float:
        return self._clock()

    @contextmanager
    def _read(self) -> Iterator[Any]:
        conn = db.connect(self.path)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        conn = db.connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @staticmethod
    def _trace(conn: Any, task_id: str, event: str, actor: str, detail: str, at: float) -> None:
        conn.execute(
            "INSERT INTO traces (task_id, event, actor, detail, at) VALUES (?,?,?,?,?)",
            (task_id, event, actor or "", (detail or "")[:1_000], at),
        )

    # ---------------------------------------------------------------- processes
    def heartbeat(
        self,
        pid: str,
        *,
        name: str = "",
        host: str = "",
        kind: str = "agent",
        task: str = "",
        detail: str = "",
        period_s: int | None = None,
    ) -> dict:
        """Report that a process is alive. `period_s` is the declared cadence:
        a job that beats every 30 minutes must not look offline after 10."""
        _check_id(pid, "process id")
        now = self.now()
        period = None if period_s is None else max(0, int(period_s))
        with self._tx() as c:
            row = c.execute("SELECT last_beat, period_s FROM processes WHERE id=?", (pid,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO processes (id, name, host, kind, task, detail, period_s, started_at, last_beat) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (pid, name or pid, host, kind, task[:500], detail[:2_000], period or 0, now, now),
                )
                return {"ok": True, "id": pid, "reborn": True}
            reborn = now - row["last_beat"] > _stale_after(row["period_s"])
            c.execute(
                "UPDATE processes SET name=COALESCE(NULLIF(?, ''), name), host=COALESCE(NULLIF(?, ''), host), "
                "kind=?, task=?, detail=?, last_beat=?, period_s=COALESCE(?, period_s), "
                "started_at=CASE WHEN ? THEN ? ELSE started_at END WHERE id=?",
                (name, host, kind, task[:500], detail[:2_000], now, period, int(reborn), now, pid),
            )
        return {"ok": True, "id": pid, "reborn": reborn}

    def processes(self) -> list[dict]:
        """All known processes with a derived state: alive, stale or offline.
        Processes silent for more than an hour (or four periods) are purged."""
        now = self.now()
        with self._tx() as c:
            rows = c.execute("SELECT * FROM processes").fetchall()
            purged = {r["id"] for r in rows if now - r["last_beat"] > max(PURGE_S, r["period_s"] * 4)}
            c.executemany("DELETE FROM processes WHERE id=?", [(pid,) for pid in purged])
        out = []
        for r in rows:
            if r["id"] in purged:
                continue
            age = now - r["last_beat"]
            if age < _fresh_after(r["period_s"]):
                state = "alive"
            elif age < _stale_after(r["period_s"]):
                state = "stale"
            else:
                state = "offline"
            out.append({**dict(r), "age_s": round(age, 1), "state": state})
        return sorted(out, key=lambda p: (p["host"], p["id"]))

    # ------------------------------------------------------------------- leases
    def acquire(self, resource: str, holder: str, *, purpose: str = "", minutes: int = 60) -> dict:
        """Lease a resource (a GPU, an API quota, a browser...). Granted when it is
        free, expired, or already yours (then it is renewed)."""
        _check_id(resource, "resource")
        _check_id(holder, "holder")
        if self.resources is not None and resource not in self.resources:
            return {"ok": False, "reason": f"unknown resource (known: {', '.join(self.resources)})"}
        minutes = _clamp(minutes, 1, LEASE_MAX_MIN)
        now = self.now()
        expires = now + minutes * 60
        with self._tx() as c:
            row = c.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
            live = row is not None and row["expires_at"] > now
            if live and row["holder"] != holder:
                return {
                    "ok": False,
                    "reason": f"held by {row['holder']}" + (f" ({row['purpose']})" if row["purpose"] else ""),
                    "holder": row["holder"],
                    "expires_at": row["expires_at"],
                }
            renewed = live and row["holder"] == holder
            c.execute(
                "INSERT INTO leases (resource, holder, purpose, since, expires_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(resource) DO UPDATE SET holder=excluded.holder, purpose=excluded.purpose, "
                "since=excluded.since, expires_at=excluded.expires_at",
                (resource, holder, purpose[:300], row["since"] if renewed else now, expires),
            )
        return {"ok": True, "resource": resource, "holder": holder, "renewed": renewed, "expires_at": expires}

    def release(self, resource: str, holder: str) -> dict:
        """Only the holder can release. Someone else's lease simply expires."""
        now = self.now()
        with self._tx() as c:
            row = c.execute("SELECT holder, expires_at FROM leases WHERE resource=?", (resource,)).fetchone()
            if row is None or row["expires_at"] <= now:
                c.execute("DELETE FROM leases WHERE resource=?", (resource,))
                return {"ok": True, "resource": resource, "note": "already free"}
            if row["holder"] != holder:
                return {"ok": False, "reason": f"held by {row['holder']}, not {holder}"}
            c.execute("DELETE FROM leases WHERE resource=?", (resource,))
        return {"ok": True, "resource": resource}

    def leases(self) -> list[dict]:
        now = self.now()
        with self._read() as c:
            rows = {r["resource"]: dict(r) for r in c.execute("SELECT * FROM leases")}
        names = list(self.resources or []) + sorted(set(rows) - set(self.resources or []))
        out = []
        for name in names:
            row = rows.get(name)
            if row is None or row["expires_at"] <= now:
                out.append({"resource": name, "free": True})
            else:
                out.append({**row, "free": False, "remaining_s": round(row["expires_at"] - now)})
        return out

    # -------------------------------------------------------------------- queue
    def enqueue(
        self,
        title: str,
        *,
        detail: str = "",
        priority: int = 5,
        creator: str = "",
        agent: str = "",
        depends_on: Iterable[str] | str = (),
        idempotency_key: str = "",
        proof: str = "",
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        operation: str = "",
        project: str = "",
    ) -> dict:
        """Queue work. `agent` pins an executor ('' lets the router choose).
        `idempotency_key` makes retries of the same request safe.
        `proof` is what must exist when the task is done (path or URL).
        `operation` and `project` are what the policy gate checks."""
        title = (title or "").strip()
        if not title:
            return {"ok": False, "reason": "title is required"}
        if isinstance(depends_on, str):
            depends_on = depends_on.split(",")
        deps = list(dict.fromkeys(d.strip() for d in depends_on if d and d.strip()))
        key = (idempotency_key or "").strip()[:200] or None
        agent = (agent or "").strip()
        if agent:
            _check_id(agent, "agent")
        now = self.now()
        tid = uuid.uuid4().hex[:12]
        with self._tx() as c:
            if key:
                prev = c.execute("SELECT id, status FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
                if prev:
                    return {"ok": True, "id": prev["id"], "status": prev["status"], "duplicate": True}
            upstream_failed = []
            if deps:
                marks = ",".join("?" * len(deps))
                known = {r["id"]: r["status"] for r in c.execute(f"SELECT id, status FROM tasks WHERE id IN ({marks})", deps)}
                missing = [d for d in deps if d not in known]
                if missing:
                    return {"ok": False, "reason": f"unknown dependencies: {', '.join(missing)}"}
                upstream_failed = [d for d in deps if known[d] in ("failed", "dead", "cancelled")]
            c.execute(
                "INSERT INTO tasks (id, title, detail, priority, status, creator, agent, max_attempts, "
                "idempotency_key, proof, operation, project, created_at, updated_at) "
                "VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?,?,?)",
                (tid, title[:300], (detail or "")[:20_000], _clamp(priority, 1, 9), (creator or "")[:64],
                 agent, _clamp(max_attempts, 1, 10), key, (proof or "").strip()[:500],
                 (operation or "").strip()[:64], (project or "").strip()[:64], now, now),
            )
            c.executemany("INSERT INTO task_deps (task_id, depends_on) VALUES (?,?)", [(tid, d) for d in deps])
            note = f"priority {_clamp(priority, 1, 9)}" + (f" · after {', '.join(deps)}" if deps else "")
            self._trace(c, tid, "enqueued", creator, note, now)
            if upstream_failed:
                self._kill(c, tid, "upstream_failed", f"upstream task {upstream_failed[0]} already ended without success", now)
                self._cascade(c, tid, now)
                return {"ok": True, "id": tid, "duplicate": False, "status": "dead"}
        return {"ok": True, "id": tid, "duplicate": False, "status": "pending"}

    def claim(self, worker: str, *, agents: Iterable[str] | None = None, lease_minutes: int = TASK_LEASE_MIN) -> dict | None:
        """Take the most urgent runnable task (oldest first on ties). Atomic.
        `agents` restricts which requested executors this worker accepts."""
        _check_id(worker, "worker")
        sql = (
            "SELECT t.id FROM tasks t WHERE t.status='pending' AND t.cancel_requested=0 "
            "AND t.attempts < t.max_attempts AND NOT EXISTS ("
            "  SELECT 1 FROM task_deps d JOIN tasks up ON up.id=d.depends_on "
            "  WHERE d.task_id=t.id AND up.status != 'done')"
        )
        params: list[Any] = []
        if agents is not None:
            accepted = list(agents)
            if not accepted:
                return None
            sql += f" AND t.agent IN ({','.join('?' * len(accepted))})"
            params += accepted
        sql += " ORDER BY t.priority, t.created_at, t.rowid LIMIT 1"
        now = self.now()
        with self._tx() as c:
            row = c.execute(sql, params).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE tasks SET status='running', assignee=?, attempts=attempts+1, lease_expires=?, "
                "started_at=?, updated_at=? WHERE id=?",
                (worker, now + max(1, int(lease_minutes)) * 60, now, now, row["id"]),
            )
            task = self._task(c, row["id"])
            self._trace(c, row["id"], "claimed", worker, f"attempt {task['attempts']}/{task['max_attempts']}", now)
        return task

    def reassign(self, task_id: str, from_worker: str, to_worker: str, detail: str = "") -> dict:
        _check_id(to_worker, "worker")
        now = self.now()
        with self._tx() as c:
            cur = c.execute(
                "UPDATE tasks SET assignee=?, updated_at=? WHERE id=? AND status='running' AND assignee=?",
                (to_worker, now, task_id, from_worker),
            )
            if cur.rowcount:
                self._trace(c, task_id, "assigned", to_worker, detail or f"from {from_worker}", now)
        return {"ok": bool(cur.rowcount)}

    def renew(self, task_id: str, worker: str, *, minutes: int = TASK_LEASE_MIN) -> dict:
        """Heartbeat of a running task. Long jobs must renew or they get taken back."""
        now = self.now()
        expires = now + max(1, int(minutes)) * 60
        with self._tx() as c:
            cur = c.execute(
                "UPDATE tasks SET lease_expires=?, updated_at=? WHERE id=? AND status='running' AND assignee=?",
                (expires, now, task_id, worker),
            )
        return {"ok": bool(cur.rowcount), "lease_expires": expires if cur.rowcount else None}

    def progress(self, task_id: str, worker: str, text: str) -> dict:
        now = self.now()
        with self._tx() as c:
            cur = c.execute(
                "UPDATE tasks SET result=?, updated_at=? WHERE id=? AND status='running' AND assignee=?",
                (_clip_result(text or ""), now, task_id, worker),
            )
        return {"ok": bool(cur.rowcount)}

    def complete(self, task_id: str, worker: str, result: Any) -> dict:
        """Close a running task with an AgentResult (or a dict/str that converts to one)."""
        res = AgentResult.from_any(result)
        with self._read() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        refusal = self._refuse_close(row, worker)
        if refusal:
            return refusal

        verification, vdetail = "n/a", ""
        if res.ok:
            # Outside the transaction on purpose: disk and network checks can be slow.
            verification, vdetail = self._verify(row, res)
            if verification == "rejected" and self.proof_mode == "enforce":
                res = AgentResult(
                    status="failed", result=res.result, error=f"proof of delivery not satisfied ({vdetail})",
                    error_code="proof_missing", tools=res.tools, artifacts=res.artifacts, meta=res.meta,
                )

        now = self.now()
        with self._tx() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            refusal = self._refuse_close(row, worker)
            if refusal:
                return refusal
            if row["cancel_requested"] and res.status != "cancelled":
                res = AgentResult(status="cancelled", result=res.result, error="cancellation was requested while running",
                                  error_code="cancelled", artifacts=res.artifacts, meta=res.meta)
            if res.status == "completed":
                status = "done"
            elif res.status == "cancelled":
                status = "cancelled"
            elif res.error_code not in NON_RETRYABLE and row["attempts"] < row["max_attempts"]:
                status = "pending"
            else:
                status = "failed"
            duration_ms = int(max(0.0, now - (row["started_at"] or now)) * 1000)
            c.execute(
                "INSERT INTO runs (task_id, worker, status, error_code, duration_ms, at) VALUES (?,?,?,?,?,?)",
                (task_id, worker, res.status, res.error_code, duration_ms, now),
            )
            finished = now if status in TERMINAL else None
            c.execute(
                "UPDATE tasks SET status=?, result=?, error_code=?, assignee=CASE WHEN ?='pending' THEN '' ELSE assignee END, "
                "lease_expires=NULL, verification=?, verification_detail=?, updated_at=?, finished_at=? WHERE id=?",
                (status, _clip_result(res.visible_text()), res.error_code if not res.ok else "", status,
                 verification, vdetail[:400], now, finished, task_id),
            )
            note = f"{res.status}" + (f" · {res.error_code}" if res.error_code else "") + f" · proof {verification}"
            self._trace(c, task_id, "retry" if status == "pending" else status, worker, note, now)
            cascaded = self._cascade(c, task_id, now) if status in ("failed", "cancelled") else []
        return {"ok": True, "id": task_id, "status": status, "verification": verification,
                "verification_detail": vdetail, "cascaded": cascaded}

    @staticmethod
    def _refuse_close(row: Any, worker: str) -> dict | None:
        if row is None:
            return {"ok": False, "reason": "unknown task"}
        if row["status"] != "running":
            return {"ok": False, "reason": f"task is {row['status']}, not running"}
        if row["assignee"] != worker:
            return {"ok": False, "reason": f"task is assigned to {row['assignee']}, not {worker}"}
        return None

    def _verify(self, row: Any, res: AgentResult) -> tuple[str, str]:
        targets = [row["proof"]] if row["proof"] else []
        targets += [a.get("path", "") for a in res.artifacts if a.get("path")]
        if not targets:
            return "no_criteria", ""
        passed, detail = proof.verify(targets, since=row["created_at"], allow_urls=self.allow_url_proof)
        return ("verified" if passed else "rejected"), detail

    def cancel(self, task_id: str, actor: str = "") -> dict:
        now = self.now()
        with self._tx() as c:
            row = c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return {"ok": False, "reason": "unknown task"}
            if row["status"] == "pending":
                cascaded = self._cancel_now(c, task_id, actor, "cancelled before it started", now)
                return {"ok": True, "id": task_id, "status": "cancelled", "cascaded": cascaded}
            if row["status"] == "running":
                c.execute("UPDATE tasks SET cancel_requested=1, updated_at=? WHERE id=?", (now, task_id))
                self._trace(c, task_id, "cancel_requested", actor, "the worker will see it when it closes", now)
                return {"ok": True, "id": task_id, "status": "running", "cancel_requested": True}
        return {"ok": False, "reason": f"task already {row['status']}"}

    def retry(self, task_id: str, actor: str = "") -> dict:
        """Second life for a failed, dead or cancelled task. Dependents that died
        only because of it come back too."""
        now = self.now()
        with self._tx() as c:
            row = c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return {"ok": False, "reason": "unknown task"}
            if row["status"] not in ("failed", "dead", "cancelled"):
                return {"ok": False, "reason": f"a {row['status']} task cannot be retried"}
            self._requeue(c, task_id, actor, "manual retry", now)
            revived, frontier = [], [task_id]
            while frontier:
                upstream = frontier.pop()
                for dep in c.execute(
                    "SELECT t.id FROM task_deps d JOIN tasks t ON t.id=d.task_id "
                    "WHERE d.depends_on=? AND t.status='dead' AND t.error_code='upstream_failed' "
                    "AND NOT EXISTS (SELECT 1 FROM task_deps d2 JOIN tasks up ON up.id=d2.depends_on "
                    "  WHERE d2.task_id=t.id AND up.status IN ('failed', 'dead', 'cancelled'))", (upstream,)
                ).fetchall():
                    self._requeue(c, dep["id"], actor, f"upstream {task_id} was retried", now)
                    revived.append(dep["id"])
                    frontier.append(dep["id"])
        return {"ok": True, "id": task_id, "status": "pending", "revived": revived}

    def _requeue(self, c: Any, task_id: str, actor: str, why: str, now: float) -> None:
        c.execute(
            "UPDATE tasks SET status='pending', attempts=0, assignee='', lease_expires=NULL, cancel_requested=0, "
            "error_code='', finished_at=NULL, updated_at=? WHERE id=?", (now, task_id))
        self._trace(c, task_id, "requeued", actor, why, now)

    def rescue(self) -> dict:
        """Watchdog. Running tasks whose lease expired go back to pending while they
        have attempts left; otherwise they die with a reason. Pending tasks that can
        never be claimed (attempts exhausted) die too, so nothing hides in limbo."""
        now = self.now()
        retried, dead = [], []
        with self._tx() as c:
            for r in c.execute(
                "SELECT id, attempts, max_attempts, assignee, started_at, cancel_requested FROM tasks "
                "WHERE status='running' AND lease_expires IS NOT NULL AND lease_expires < ?", (now,)
            ).fetchall():
                c.execute(
                    "INSERT INTO runs (task_id, worker, status, error_code, duration_ms, at) VALUES (?,?,?,?,?,?)",
                    (r["id"], r["assignee"] or "?", "failed", "lease_expired",
                     int(max(0.0, now - (r["started_at"] or now)) * 1000), now),
                )
                if r["cancel_requested"]:
                    dead += self._cancel_now(c, r["id"], r["assignee"], "worker went silent after cancellation was requested", now)
                elif r["attempts"] < r["max_attempts"]:
                    c.execute(
                        "UPDATE tasks SET status='pending', assignee='', lease_expires=NULL, updated_at=? WHERE id=?",
                        (now, r["id"]))
                    self._trace(c, r["id"], "lease_expired", r["assignee"], "worker went silent · back to pending", now)
                    retried.append(r["id"])
                else:
                    why = f"lease expired after {r['attempts']} attempt(s), last worker {r['assignee'] or '?'}"
                    self._kill(c, r["id"], "lease_expired", why, now)
                    dead.append(r["id"])
                    dead += self._cascade(c, r["id"], now)
            for r in c.execute(
                "SELECT id, max_attempts, cancel_requested FROM tasks "
                "WHERE status='pending' AND (attempts >= max_attempts OR cancel_requested=1)"
            ).fetchall():
                if r["cancel_requested"]:
                    dead += self._cancel_now(c, r["id"], "", "cancellation was requested", now)
                    continue
                self._kill(c, r["id"], "attempts_exhausted", f"pending with all {r['max_attempts']} attempts used", now)
                dead.append(r["id"])
                dead += self._cascade(c, r["id"], now)
            # A pending task waiting on something that will never be done can never run.
            seen = set(dead)
            for r in c.execute(
                "SELECT t.id, up.id AS upstream FROM tasks t JOIN task_deps d ON d.task_id=t.id "
                "JOIN tasks up ON up.id=d.depends_on "
                "WHERE t.status='pending' AND up.status IN ('failed', 'dead', 'cancelled')"
            ).fetchall():
                if r["id"] in seen:
                    continue
                self._kill(c, r["id"], "upstream_failed", f"upstream task {r['upstream']} ended without success", now)
                killed = [r["id"], *self._cascade(c, r["id"], now)]
                seen.update(killed)
                dead += killed
        return {"retried": retried, "dead": dead}

    def recover(self, worker_prefix: str, *, exclude: Iterable[str] = ()) -> list[str]:
        """After a scheduler restart, its in-flight tasks go back to pending, or to
        cancelled if someone asked to cancel them meanwhile. `exclude` protects the
        tasks the caller is still running."""
        skip = set(exclude)
        now = self.now()
        recovered = []
        with self._tx() as c:
            rows = c.execute(
                "SELECT id, cancel_requested FROM tasks WHERE status='running' "
                "AND (assignee=? OR substr(assignee, 1, ?)=?)",
                (worker_prefix, len(worker_prefix) + 1, worker_prefix + "/")).fetchall()
            for r in rows:
                if r["id"] in skip:
                    continue
                if r["cancel_requested"]:
                    self._cancel_now(c, r["id"], worker_prefix, "cancelled while its scheduler was down", now)
                else:
                    c.execute("UPDATE tasks SET status='pending', assignee='', lease_expires=NULL, updated_at=? "
                              "WHERE id=?", (now, r["id"]))
                    self._trace(c, r["id"], "recovered", worker_prefix, "scheduler restarted while it was running", now)
                recovered.append(r["id"])
        return recovered

    def _cancel_now(self, c: Any, task_id: str, actor: str, why: str, now: float) -> list[str]:
        c.execute(
            "UPDATE tasks SET status='cancelled', error_code='cancelled', result=?, assignee='', lease_expires=NULL, "
            "updated_at=?, finished_at=? WHERE id=?", (f"cancelled: {why}", now, now, task_id))
        self._trace(c, task_id, "cancelled", actor, why, now)
        return self._cascade(c, task_id, now)

    def _kill(self, c: Any, task_id: str, code: str, why: str, now: float) -> None:
        c.execute(
            "UPDATE tasks SET status='dead', error_code=?, result=?, assignee='', lease_expires=NULL, "
            "updated_at=?, finished_at=? WHERE id=?", (code, f"dead: {why}", now, now, task_id))
        self._trace(c, task_id, "dead", "", why, now)

    def _cascade(self, c: Any, task_id: str, now: float) -> list[str]:
        """Dependents of a task that will never be done cannot run: mark them dead."""
        killed, frontier = [], [task_id]
        while frontier:
            upstream = frontier.pop()
            for dep in c.execute(
                "SELECT t.id FROM task_deps d JOIN tasks t ON t.id=d.task_id "
                "WHERE d.depends_on=? AND t.status='pending'", (upstream,)
            ).fetchall():
                self._kill(c, dep["id"], "upstream_failed", f"upstream task {upstream} ended without success", now)
                killed.append(dep["id"])
                frontier.append(dep["id"])
        return killed

    # ------------------------------------------------------------------ queries
    def _task(self, c: Any, task_id: str) -> dict | None:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        deps = c.execute(
            "SELECT d.depends_on AS id, t.status FROM task_deps d JOIN tasks t ON t.id=d.depends_on "
            "WHERE d.task_id=? ORDER BY d.rowid",
            (task_id,)).fetchall()
        out = dict(row)
        out["depends_on"] = [d["id"] for d in deps]
        out["blocked_by"] = [d["id"] for d in deps if d["status"] != "done"]
        return out

    def get(self, task_id: str) -> dict | None:
        with self._read() as c:
            return self._task(c, task_id)

    def tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict]:
        limit = _clamp(limit, 1, 1_000)
        with self._read() as c:
            if status:
                rows = c.execute("SELECT id FROM tasks WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                                 (status, limit)).fetchall()
            else:
                rows = c.execute("SELECT id FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            return [self._task(c, r["id"]) for r in rows]

    def traces(self, task_id: str | None = None, *, limit: int = 200) -> list[dict]:
        limit = _clamp(limit, 1, 2_000)
        with self._read() as c:
            if task_id:
                rows = c.execute("SELECT * FROM traces WHERE task_id=? ORDER BY id LIMIT ?", (task_id, limit))
            else:
                rows = c.execute("SELECT * FROM (SELECT * FROM traces ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._read() as c:
            counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
        return {"tasks": {s: counts.get(s, 0) for s in ("pending", "running", "done", "failed", "dead", "cancelled")}}

    def fitness(self, *, window_s: float = 7 * 86_400) -> dict[str, dict]:
        """Measured, never invented: success rate and median latency per worker.
        A worker without runs has no numbers (None), not zeros."""
        since = self.now() - window_s
        per: dict[str, dict] = {}
        with self._read() as c:
            for r in c.execute("SELECT worker, status, duration_ms FROM runs WHERE at >= ?", (since,)):
                s = per.setdefault(r["worker"], {"runs": 0, "completed": 0, "durations": []})
                s["runs"] += 1
                if r["status"] == "completed":
                    s["completed"] += 1
                    s["durations"].append(r["duration_ms"])
        return {
            w: {
                "runs": s["runs"],
                "success_rate": round(s["completed"] / s["runs"], 3) if s["runs"] else None,
                "median_ms": int(statistics.median(s["durations"])) if s["durations"] else None,
            }
            for w, s in per.items()
        }


__all__ = ["Kernel", "TASK_LEASE_MIN", "LEASE_MAX_MIN", "DEFAULT_MAX_ATTEMPTS"]
