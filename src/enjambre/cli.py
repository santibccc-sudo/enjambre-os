"""enjambre command line.

    enjambre demo                 a living swarm in your browser, no API keys
    enjambre init my-swarm        scaffold a genome you can edit
    enjambre up my-swarm          API + dashboard + scheduler
    enjambre run my-swarm         run the scheduler until the queue is idle (CI friendly)
    enjambre enqueue "Write the release notes" --dir my-swarm --agent writer
    enjambre tasks --dir my-swarm
    enjambre ps --dir my-swarm
    enjambre memory "leases" --dir my-swarm
    enjambre mcp --dir my-swarm   MCP server over stdio
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .genome import Genome, GenomeError
from .kernel import Kernel
from .memory import MemoryGraph
from .router import Router
from .scheduler import Scheduler
from .server import App, serve

TEMPLATES = Path(__file__).parent / "templates"


def load(directory: str | Path) -> tuple[Genome, Kernel]:
    genome = Genome.load(directory)
    kernel = Kernel(genome.db_path, proof_mode=str(genome.kernel.get("proof_mode", "record")),
                    allow_url_proof=bool(genome.kernel.get("allow_url_proof", True)),
                    resources=genome.kernel.get("resources"))
    return genome, kernel


def build_app(genome: Genome, kernel: Kernel, token: str = "") -> App:
    memory = MemoryGraph(genome.memory_dir) if genome.memory_dir else None
    return App(kernel, genome, memory=memory, router=Router(genome, kernel), token=token)


def seed_demo(kernel: Kernel, round_no: int = 1) -> list[str]:
    tag = f"demo-round-{round_no}"

    def add(title: str, **kw) -> str:
        return kernel.enqueue(title, creator="demo", idempotency_key=f"{tag}:{title}", **kw)["id"]

    research = add("Research why agent swarms fail in production", agent="scout", priority=2,
                   detail="Collect the three most common failure modes, with sources.")
    draft = add("Draft a one-page brief", agent="writer", depends_on=[research])
    review = add("Review the brief", agent="critic", depends_on=[draft],
                 detail="The critic stumbles on its first attempt, so you can watch a retry.")
    add("Publish the brief", agent="editor", depends_on=[review], operation="publish")
    add("Publish a teaser right now", agent="writer", operation="publish", priority=3,
        detail="The writer may not publish: the policy gate stops this before any agent runs.")
    add("Summarise the heartbeat design", priority=6)
    add("Summarise the lease design", priority=6)
    kernel.acquire("gpu0", "scout", purpose="embedding the sources", minutes=15)
    return [research, draft, review]


def _wait(stop: threading.Event) -> None:
    def handler(signum, frame):  # noqa: ARG001
        stop.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    while not stop.wait(0.5):
        pass


def _serve(genome: Genome, kernel: Kernel, args: argparse.Namespace, *, scheduler: bool) -> tuple:
    token = os.environ.get("ENJAMBRE_TOKEN", "")
    app = build_app(genome, kernel, token)
    server = serve(app, args.host, args.port, insecure=getattr(args, "insecure", False))
    sched = Scheduler(kernel, genome, router=app.router) if scheduler else None
    threading.Thread(target=server.serve_forever, name="enjambre-http", daemon=True).start()
    if sched:
        threading.Thread(target=sched.run_forever, name="enjambre-scheduler", daemon=True).start()
    host, port = server.server_address[:2]
    print(f"\n  enjambre {__version__} · {genome.name}")
    print(f"  dashboard   http://{host}:{port}/")
    print(f"  agents      {', '.join(genome.agents)}")
    print(f"  scheduler   {'on' if sched else 'off'}")
    print(f"  auth        {'token from $ENJAMBRE_TOKEN' if token else 'none (loopback only)'}")
    return server, sched


def _shutdown(server, sched) -> None:
    print("\n  stopping...")
    if sched:
        sched.stop(wait=False)
    server.shutdown()


# ------------------------------------------------------------------ commands
def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir).resolve()
    source = TEMPLATES / args.template
    if not source.is_dir():
        print(f"unknown template: {args.template}", file=sys.stderr)
        return 1
    if (target / "swarm.yaml").exists() and not args.force:
        print(f"{target} already has a swarm.yaml (use --force to overwrite)", file=sys.stderr)
        return 1
    shutil.copytree(source, target, dirs_exist_ok=True)
    print(f"created a swarm in {target}\n  next: enjambre up {args.dir}")
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    genome, kernel = load(args.dir)
    server, sched = _serve(genome, kernel, args, scheduler=not args.no_scheduler)
    print("  Ctrl+C to stop\n")
    stop = threading.Event()
    try:
        _wait(stop)
    finally:
        _shutdown(server, sched)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    base = Path(args.dir).resolve() if args.dir else Path(tempfile.mkdtemp(prefix="enjambre-demo-"))
    if not (base / "swarm.yaml").exists():
        shutil.copytree(TEMPLATES / "demo", base, dirs_exist_ok=True)
    genome, kernel = load(base)
    server, sched = _serve(genome, kernel, args, scheduler=True)
    print(f"  swarm dir   {base}")
    print("  Ctrl+C to stop\n")
    stop = threading.Event()
    seed_demo(kernel, 1)

    def keep_alive() -> None:
        round_no, idle_since = 1, None
        while not stop.wait(2):
            counts = kernel.stats()["tasks"]
            if counts["pending"] or counts["running"]:
                idle_since = None
                continue
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since > 15:
                round_no += 1
                seed_demo(kernel, round_no)
                idle_since = None

    if not args.once:
        threading.Thread(target=keep_alive, name="enjambre-demo", daemon=True).start()
    try:
        _wait(stop)
    finally:
        _shutdown(server, sched)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    genome, kernel = load(args.dir)
    sched = Scheduler(kernel, genome)
    idle = sched.run_until_idle(timeout_s=args.timeout)
    sched.stop()
    counts = kernel.stats()["tasks"]
    print("  ".join(f"{k}={v}" for k, v in counts.items()))
    return 0 if idle else 2


def cmd_enqueue(args: argparse.Namespace) -> int:
    _, kernel = load(args.dir)
    res = kernel.enqueue(args.title, detail=args.detail, priority=args.priority, creator="cli", agent=args.agent,
                         depends_on=args.after, proof=args.proof, operation=args.operation, project=args.project,
                         max_attempts=args.max_attempts)
    if not res["ok"]:
        print(res["reason"], file=sys.stderr)
        return 1
    print(res["id"])
    return 0


def _ago(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def cmd_tasks(args: argparse.Namespace) -> int:
    _, kernel = load(args.dir)
    rows = kernel.tasks(status=args.status, limit=args.limit)
    print(f"{'ID':<13}{'STATUS':<10}{'WHO':<22}{'TRY':<5}{'UPDATED':<10}TITLE")
    for t in rows:
        who = t["assignee"] or t["agent"] or "router"
        print(f"{t['id']:<13}{t['status']:<10}{who[:21]:<22}{t['attempts']}/{t['max_attempts']:<3}{_ago(t['updated_at']):<10}{t['title']}")
    return 0


def cmd_ps(args: argparse.Namespace) -> int:
    _, kernel = load(args.dir)
    print(f"{'ID':<24}{'STATE':<9}{'HOST':<14}{'AGE':>7}  TASK")
    for p in kernel.processes():
        print(f"{p['id']:<24}{p['state']:<9}{p['host'][:13]:<14}{p['age_s']:>6.0f}s  {p['task']}")
    for lease in kernel.leases():
        if not lease["free"]:
            print(f"lease {lease['resource']} -> {lease['holder']} ({lease['remaining_s']}s left)")
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    genome, _ = load(args.dir)
    if not genome.memory_dir:
        print("this swarm has no memory folder (set memory.dir in swarm.yaml)", file=sys.stderr)
        return 1
    res = MemoryGraph(genome.memory_dir).query(args.query, limit=args.limit)
    for r in res["results"]:
        print(f"{r['score']:>6}  {r['label']}  [{r['group']}]  {r['description']}")
        if r["neighbors"]:
            print(f"        -> {', '.join(r['neighbors'])}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp import HttpTransport, LocalTransport, McpServer

    if args.url:
        transport = HttpTransport(args.url, os.environ.get("ENJAMBRE_TOKEN", ""))
    else:
        genome, kernel = load(args.dir)
        transport = LocalTransport(build_app(genome, kernel))
    McpServer(transport).serve()
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="enjambre", description="A small, honest operating system for swarms of AI agents.")
    p.add_argument("--version", action="version", version=f"enjambre {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging to stderr")
    sub = p.add_subparsers(dest="command")

    def net(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--host", default="127.0.0.1")
        sp.add_argument("--port", type=int, default=8765)

    sp = sub.add_parser("demo", help="run a living demo swarm (no API keys)")
    sp.add_argument("--dir", help="where to create the demo swarm (default: a temporary folder)")
    sp.add_argument("--once", action="store_true", help="seed one round of tasks instead of looping")
    net(sp)
    sp.set_defaults(fn=cmd_demo)

    sp = sub.add_parser("init", help="scaffold a swarm from a template")
    sp.add_argument("dir")
    sp.add_argument("--template", default="demo")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("up", help="serve the API and dashboard and run the scheduler")
    sp.add_argument("dir", nargs="?", default=".")
    sp.add_argument("--no-scheduler", action="store_true")
    sp.add_argument("--insecure", action="store_true", help="allow a non-loopback host without a token")
    net(sp)
    sp.set_defaults(fn=cmd_up)

    sp = sub.add_parser("run", help="run the scheduler until the queue is idle")
    sp.add_argument("dir", nargs="?", default=".")
    sp.add_argument("--timeout", type=float, default=600)
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("enqueue", help="queue a task")
    sp.add_argument("title")
    sp.add_argument("--dir", default=".")
    sp.add_argument("--detail", default="")
    sp.add_argument("--priority", type=int, default=5)
    sp.add_argument("--agent", default="")
    sp.add_argument("--after", action="append", default=[], metavar="TASK_ID")
    sp.add_argument("--proof", default="")
    sp.add_argument("--operation", default="")
    sp.add_argument("--project", default="")
    sp.add_argument("--max-attempts", type=int, default=2)
    sp.set_defaults(fn=cmd_enqueue)

    sp = sub.add_parser("tasks", help="list tasks")
    sp.add_argument("--dir", default=".")
    sp.add_argument("--status")
    sp.add_argument("--limit", type=int, default=30)
    sp.set_defaults(fn=cmd_tasks)

    sp = sub.add_parser("ps", help="list processes and leases")
    sp.add_argument("--dir", default=".")
    sp.set_defaults(fn=cmd_ps)

    sp = sub.add_parser("memory", help="query the memory graph")
    sp.add_argument("query")
    sp.add_argument("--dir", default=".")
    sp.add_argument("--limit", type=int, default=8)
    sp.set_defaults(fn=cmd_memory)

    sp = sub.add_parser("mcp", help="MCP server over stdio")
    group = sp.add_mutually_exclusive_group()
    group.add_argument("--dir", default=".")
    group.add_argument("--url", help="talk to a running `enjambre up` instead (token from $ENJAMBRE_TOKEN)")
    sp.set_defaults(fn=cmd_mcp)
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not getattr(args, "fn", None):
        p.print_help()
        return 0
    try:
        return int(args.fn(args) or 0)
    except GenomeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
