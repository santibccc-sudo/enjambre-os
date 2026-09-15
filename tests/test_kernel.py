import sqlite3
import threading

import pytest

from enjambre import AgentResult, Kernel
from enjambre.kernel import MAX_RESULT_CHARS


# ------------------------------------------------------------------ processes
def test_state_is_derived_from_heartbeat_age(kernel, clock):
    kernel.heartbeat("writer", host="laptop")
    assert kernel.processes()[0]["state"] == "alive"
    clock.advance(300)
    assert kernel.processes()[0]["state"] == "stale"
    clock.advance(400)
    assert kernel.processes()[0]["state"] == "offline"
    clock.advance(3_600)
    assert kernel.processes() == []


def test_declared_period_keeps_slow_jobs_alive(kernel, clock):
    kernel.heartbeat("nightly", period_s=1_800)
    clock.advance(1_700)
    assert kernel.processes()[0]["state"] == "alive"


def test_a_process_that_comes_back_starts_a_new_life(kernel, clock):
    kernel.heartbeat("writer")
    first = kernel.processes()[0]["started_at"]
    clock.advance(30)
    assert kernel.heartbeat("writer")["reborn"] is False
    clock.advance(900)
    assert kernel.heartbeat("writer")["reborn"] is True
    assert kernel.processes()[0]["started_at"] > first


def test_invalid_ids_are_rejected(kernel):
    with pytest.raises(ValueError):
        kernel.heartbeat("../etc/passwd")
    with pytest.raises(ValueError):
        kernel.heartbeat("")


# --------------------------------------------------------------------- leases
def test_lease_is_exclusive_until_it_expires(kernel, clock):
    assert kernel.acquire("gpu0", "a", minutes=10)["ok"]
    busy = kernel.acquire("gpu0", "b")
    assert not busy["ok"] and busy["holder"] == "a"
    clock.advance(601)
    assert kernel.acquire("gpu0", "b")["ok"]


def test_renewal_keeps_the_original_start(kernel, clock):
    kernel.acquire("gpu0", "a", minutes=10)
    since = kernel.leases()[0]["since"]
    clock.advance(60)
    again = kernel.acquire("gpu0", "a", minutes=10)
    assert again["renewed"] and kernel.leases()[0]["since"] == since


def test_only_the_holder_releases(kernel):
    kernel.acquire("gpu0", "a")
    assert not kernel.release("gpu0", "b")["ok"]
    assert kernel.release("gpu0", "a")["ok"]
    assert kernel.leases() == []


def test_declared_resources_are_listed_and_enforced(tmp_path, clock):
    k = Kernel(tmp_path / "k.db", clock=clock, resources=["gpu0", "gpu1"])
    assert not k.acquire("gpu9", "a")["ok"]
    k.acquire("gpu1", "a")
    assert [(r["resource"], r["free"]) for r in k.leases()] == [("gpu0", True), ("gpu1", False)]


# ---------------------------------------------------------------------- queue
def test_enqueue_validates_and_is_idempotent(kernel):
    assert not kernel.enqueue("  ")["ok"]
    first = kernel.enqueue("build", idempotency_key="req-1")
    again = kernel.enqueue("build", idempotency_key="req-1")
    assert again["duplicate"] and again["id"] == first["id"]
    assert not kernel.enqueue("x", depends_on=["nope"])["ok"]


def test_claim_order_is_priority_then_age(kernel, clock):
    low = kernel.enqueue("low", priority=7)["id"]
    clock.advance(1)
    urgent_old = kernel.enqueue("urgent old", priority=1)["id"]
    clock.advance(1)
    kernel.enqueue("urgent new", priority=1)
    assert kernel.claim("w")["id"] == urgent_old
    kernel.claim("w")
    assert kernel.claim("w")["id"] == low
    assert kernel.claim("w") is None


def test_dag_runs_only_when_upstream_is_done(kernel):
    a = kernel.enqueue("a")["id"]
    b = kernel.enqueue("b")["id"]
    c = kernel.enqueue("c", depends_on=[a, b], priority=1)["id"]
    assert kernel.get(c)["blocked_by"] == [a, b]
    for _ in range(2):
        t = kernel.claim("w")
        assert t["id"] != c
        kernel.complete(t["id"], "w", AgentResult.completed("ok"))
    assert kernel.claim("w")["id"] == c


def test_agent_filter(kernel):
    kernel.enqueue("for bob", agent="bob")
    kernel.enqueue("anyone")
    assert kernel.claim("alice", agents=["alice", ""])["title"] == "anyone"
    assert kernel.claim("alice", agents=["alice", ""]) is None
    assert kernel.claim("bob", agents=["bob"])["title"] == "for bob"
    assert kernel.claim("x", agents=[]) is None


def test_concurrent_claims_never_share_a_task(tmp_path):
    path = tmp_path / "race.db"
    Kernel(path)
    ids = {Kernel(path).enqueue(f"t{i}")["id"] for i in range(60)}
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        k = Kernel(path)
        while True:
            t = k.claim(f"w{n}")
            if t is None:
                return
            with lock:
                claimed.append(t["id"])

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(claimed) == sorted(ids)


def test_only_the_assignee_can_close(kernel):
    tid = kernel.enqueue("t")["id"]
    assert not kernel.complete(tid, "w", "done")["ok"]
    kernel.claim("w")
    assert "assigned to w" in kernel.complete(tid, "intruder", "done")["reason"]
    assert kernel.complete(tid, "w", "done")["status"] == "done"
    assert not kernel.complete(tid, "w", "done")["ok"]


def test_failures_retry_then_fail_and_cascade(kernel):
    up = kernel.enqueue("up", max_attempts=2)["id"]
    down = kernel.enqueue("down", depends_on=[up])["id"]
    downer = kernel.enqueue("downer", depends_on=[down])["id"]
    kernel.claim("w")
    assert kernel.complete(up, "w", AgentResult.failed("boom"))["status"] == "pending"
    kernel.claim("w")
    res = kernel.complete(up, "w", AgentResult.failed("boom again"))
    assert res["status"] == "failed"
    assert set(res["cascaded"]) == {down, downer}
    assert kernel.get(downer)["error_code"] == "upstream_failed"


def test_non_retryable_errors_fail_at_once(kernel):
    tid = kernel.enqueue("t", max_attempts=5)["id"]
    kernel.claim("w")
    res = kernel.complete(tid, "w", AgentResult.failed("nope", "permission_denied"))
    assert res["status"] == "failed"


def test_silent_workers_lose_their_task(kernel, clock):
    up = kernel.enqueue("slow", max_attempts=2)["id"]
    down = kernel.enqueue("after", depends_on=[up])["id"]
    kernel.claim("w1", lease_minutes=1)
    clock.advance(61)
    assert kernel.rescue()["retried"] == [up]
    assert kernel.get(up)["status"] == "pending"
    kernel.claim("w2", lease_minutes=1)
    clock.advance(61)
    rescued = kernel.rescue()
    assert rescued["dead"] == [up, down]
    assert kernel.get(up)["error_code"] == "lease_expired"


def test_renewing_keeps_long_work_alive(kernel, clock):
    tid = kernel.enqueue("long")["id"]
    kernel.claim("w", lease_minutes=1)
    for _ in range(5):
        clock.advance(50)
        assert kernel.renew(tid, "w", minutes=1)["ok"]
    assert kernel.rescue() == {"retried": [], "dead": []}
    assert not kernel.renew(tid, "someone-else")["ok"]


def test_retry_revives_dependents_killed_by_upstream(kernel):
    up = kernel.enqueue("up", max_attempts=1)["id"]
    down = kernel.enqueue("down", depends_on=[up])["id"]
    kernel.claim("w")
    kernel.complete(up, "w", AgentResult.failed("boom"))
    assert kernel.get(down)["status"] == "dead"
    res = kernel.retry(up)
    assert res["revived"] == [down]
    assert kernel.get(down)["status"] == "pending"
    assert not kernel.retry(down)["ok"]


def test_enqueue_after_a_failed_upstream_is_dead_on_arrival(kernel):
    up = kernel.enqueue("up", max_attempts=1)["id"]
    kernel.claim("w")
    kernel.complete(up, "w", AgentResult.failed("boom"))
    res = kernel.enqueue("late", depends_on=[up])
    assert res["status"] == "dead"


def test_cancel_pending_and_running(kernel):
    a = kernel.enqueue("a")["id"]
    child = kernel.enqueue("child", depends_on=[a])["id"]
    assert kernel.cancel(a)["cascaded"] == [child]
    b = kernel.enqueue("b")["id"]
    kernel.claim("w")
    assert kernel.cancel(b)["cancel_requested"]
    assert kernel.complete(b, "w", AgentResult.completed("finished anyway"))["status"] == "cancelled"
    assert not kernel.cancel(b)["ok"]


def test_limbo_tasks_are_not_hidden(kernel):
    tid = kernel.enqueue("t", max_attempts=1)["id"]
    with sqlite3.connect(kernel.path) as c:
        c.execute("UPDATE tasks SET attempts=1 WHERE id=?", (tid,))
    assert kernel.rescue()["dead"] == [tid]
    assert kernel.get(tid)["error_code"] == "attempts_exhausted"


def test_recover_after_scheduler_restart(kernel):
    tid = kernel.enqueue("t")["id"]
    kernel.claim("scheduler")
    kernel.reassign(tid, "scheduler", "scheduler/writer")
    assert kernel.recover("scheduler") == [tid]
    assert kernel.get(tid)["status"] == "pending"


def test_recover_matches_the_prefix_exactly_and_honours_exclusions(kernel):
    mine = kernel.enqueue("mine")["id"]
    other = kernel.enqueue("other")["id"]
    busy = kernel.enqueue("busy")["id"]
    kernel.claim("sched")
    kernel.reassign(mine, "sched", "sched/a")
    kernel.claim("schedX")
    kernel.reassign(other, "schedX", "schedX/a")
    kernel.claim("sched")
    kernel.reassign(busy, "sched", "sched/b")
    assert kernel.recover("sched", exclude=[busy]) == [mine]
    assert kernel.get(other)["status"] == "running"
    assert kernel.get(busy)["status"] == "running"


def test_cancelled_work_never_returns_to_limbo(kernel, clock):
    a = kernel.enqueue("recovered after cancel")["id"]
    kernel.claim("sched")
    kernel.cancel(a)
    kernel.recover("sched")
    assert kernel.get(a)["status"] == "cancelled"
    b = kernel.enqueue("lease expires after cancel")["id"]
    kernel.claim("w", lease_minutes=1)
    kernel.cancel(b)
    clock.advance(61)
    kernel.rescue()
    assert kernel.get(b)["status"] == "cancelled"
    c = kernel.enqueue("pending with a stale cancel flag")["id"]
    with sqlite3.connect(kernel.path) as conn:
        conn.execute("UPDATE tasks SET cancel_requested=1 WHERE id=?", (c,))
    kernel.rescue()
    assert kernel.get(c)["status"] == "cancelled"


# ---------------------------------------------------------------------- proof
def test_proof_is_recorded_but_not_enforced_by_default(kernel, tmp_path):
    tid = kernel.enqueue("write report", proof=str(tmp_path / "report.md"))["id"]
    kernel.claim("w")
    res = kernel.complete(tid, "w", AgentResult.completed("I wrote it, trust me"))
    assert res["status"] == "done" and res["verification"] == "rejected"


def test_enforced_proof_turns_a_lie_into_a_failure(tmp_path, clock):
    k = Kernel(tmp_path / "k.db", clock=clock, proof_mode="enforce")
    target = tmp_path / "report.md"
    tid = k.enqueue("write report", proof=str(target), max_attempts=2)["id"]
    k.claim("w")
    res = k.complete(tid, "w", AgentResult.completed("done"))
    assert res["status"] == "pending"
    assert k.get(tid)["error_code"] == "proof_missing"
    k.claim("w")
    target.write_text("# real report\n")
    assert k.complete(tid, "w", AgentResult.completed("done"))["verification"] == "verified"
    assert k.get(tid)["status"] == "done"


def test_declared_artifacts_count_as_proof(kernel, tmp_path):
    out = tmp_path / "out.txt"
    tid = kernel.enqueue("t")["id"]
    kernel.claim("w")
    out.write_text("data")
    res = kernel.complete(tid, "w", {"status": "completed", "artifacts": [{"path": str(out)}]})
    assert res["verification"] == "verified"


def test_no_criteria_is_said_out_loud(kernel):
    tid = kernel.enqueue("t")["id"]
    kernel.claim("w")
    assert kernel.complete(tid, "w", "ok")["verification"] == "no_criteria"


# ----------------------------------------------------------- queries & fitness
def test_traces_tell_the_whole_story(kernel):
    tid = kernel.enqueue("t", creator="me")["id"]
    kernel.claim("w")
    kernel.complete(tid, "w", "ok")
    assert [t["event"] for t in kernel.traces(tid)] == ["enqueued", "claimed", "done"]


def test_fitness_is_measured_not_invented(kernel, clock):
    for ok in (True, True, False):
        tid = kernel.enqueue("t", max_attempts=1)["id"]
        kernel.claim("writer")
        clock.advance(2)
        kernel.complete(tid, "writer", AgentResult.completed("x") if ok else AgentResult.failed("y"))
    fit = kernel.fitness()
    assert fit["writer"]["runs"] == 3
    assert fit["writer"]["success_rate"] == pytest.approx(0.667)
    assert fit["writer"]["median_ms"] == 2000
    assert "nobody" not in fit


def test_long_results_say_they_were_truncated(kernel):
    tid = kernel.enqueue("t")["id"]
    kernel.claim("w")
    kernel.complete(tid, "w", "x" * (MAX_RESULT_CHARS + 50))
    assert kernel.get(tid)["result"].endswith("[... 50 characters truncated]")


def test_stats_counts_every_status(kernel):
    kernel.enqueue("a")
    assert kernel.stats()["tasks"]["pending"] == 1
    assert set(kernel.stats()["tasks"]) == {"pending", "running", "done", "failed", "dead", "cancelled"}
