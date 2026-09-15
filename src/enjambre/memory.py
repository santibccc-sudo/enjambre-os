"""Memory graph: a folder of markdown notes becomes an explorable, queryable graph.

Every node is a real note and every edge a real link, `[[wikilink]]` or
`[text](other.md)`. Links to notes nobody wrote yet appear as `missing` nodes,
because a gap you can see is a gap you can fill. Notes with
`status: archived|deprecated` in their front matter move to the `archived` group.

    ---
    name: Lease rescue
    description: Silent workers lose their task
    type: decision
    tags: [kernel]
    ---
    Related to [[queue-design]] and [[incidents/2026-08-lease-tie]].
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

import yaml

WIKILINK_RX = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
MDLINK_RX = re.compile(r"\]\(([^)\s]+?\.md)(?:#[^)]*)?\)")
HEADING_RX = re.compile(r"^#\s+(.+?)\s*$", re.M)
WORD_RX = re.compile(r"[^\W_]+", re.UNICODE)

MISSING = "missing"
ARCHIVED = "archived"
FIXED_COLORS = {MISSING: "#ff5b6e", ARCHIVED: "#f0bd63"}
PALETTE = ["#62edb2", "#b88bff", "#5cb6ff", "#ff9f5a", "#67e6ff", "#c6e36b", "#d6a8ff", "#7b98d1", "#ffd166", "#ff8fb1"]
STOPWORDS = {"the", "and", "for", "with", "from", "that", "this", "los", "las", "del", "con", "por", "para", "una", "que"}
MAX_FILES = 20_000


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _color(group: str) -> str:
    if group in FIXED_COLORS:
        return FIXED_COLORS[group]
    return PALETTE[int(hashlib.sha1(group.encode()).hexdigest(), 16) % len(PALETTE)]


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        meta = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        meta = {}
    body = text[end + 4:].lstrip("\n")
    return (meta if isinstance(meta, dict) else {}), body


def _summary(body: str) -> str:
    for block in re.split(r"\n\s*\n", body):
        line = block.strip()
        if line and not line.startswith(("#", "---", "```")):
            return re.sub(r"\s+", " ", line)
    return ""


def _terms(q: str) -> list[str]:
    return [t for t in (slug(w) for w in WORD_RX.findall(q)) if len(t) > 2 and t not in STOPWORDS]


class MemoryGraph:
    def __init__(self, root: str | Path, *, cache_s: float = 10.0) -> None:
        self.root = Path(root).resolve()
        self.cache_s = cache_s
        self._lock = threading.Lock()
        self._cache: tuple[float, dict, dict] | None = None

    # ------------------------------------------------------------------ build
    def _files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        files = []
        for path in self.root.rglob("*.md"):
            rel = path.relative_to(self.root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            files.append(path)
            if len(files) >= MAX_FILES:
                break
        return sorted(files)

    def _build(self) -> tuple[dict, dict]:
        notes: dict[str, dict] = {}
        bodies: dict[str, str] = {}
        alias: dict[str, str] = {}
        for path in self._files():
            rel = path.relative_to(self.root).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, body = _frontmatter(text)
            nid = slug(rel[:-3])
            if not nid or nid in notes:
                continue
            heading = HEADING_RX.search(body)
            label = str(meta.get("name") or (heading.group(1) if heading else path.stem))
            group = str(meta.get("type") or (rel.split("/", 1)[0] if "/" in rel else "note"))
            if str(meta.get("status") or "").lower() in ("archived", "deprecated", "obsolete"):
                group = ARCHIVED
            tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
            notes[nid] = {
                "id": nid, "label": label[:120], "group": group,
                "description": str(meta.get("description") or _summary(body))[:280],
                "src": rel, "tags": [str(t) for t in tags][:12],
            }
            bodies[nid] = body
            for key in (slug(path.stem), nid, slug(label), slug(str(meta.get("name") or ""))):
                if key:
                    alias.setdefault(key, nid)

        edges: set[tuple[str, str]] = set()
        missing: dict[str, dict] = {}
        for nid, body in bodies.items():
            base = (self.root / notes[nid]["src"]).parent
            for raw in WIKILINK_RX.findall(body):
                raw = raw.strip().removesuffix(".md")
                key = slug(raw)
                if not key:
                    continue
                target = alias.get(key) or alias.get(slug(raw.rsplit("/", 1)[-1]))
                if target is None:
                    target = f"{MISSING}:{key}"
                    missing.setdefault(target, {"id": target, "label": raw[:120], "group": MISSING,
                                                "description": "linked, but nobody wrote this note yet",
                                                "src": "", "tags": []})
                if target != nid:
                    edges.add((min(nid, target), max(nid, target)))
            for raw in MDLINK_RX.findall(body):
                if "://" in raw:
                    continue
                try:
                    rel = (base / raw).resolve().relative_to(self.root).as_posix()
                except ValueError:
                    continue  # links that leave the memory folder are ignored
                target = alias.get(slug(rel[:-3]))
                if target and target != nid:
                    edges.add((min(nid, target), max(nid, target)))

        nodes = {**notes, **missing}
        degree = {nid: 0 for nid in nodes}
        for a, b in edges:
            degree[a] += 1
            degree[b] += 1
        groups: dict[str, int] = {}
        for node in nodes.values():
            node["degree"] = degree[node["id"]]
            groups[node["group"]] = groups.get(node["group"], 0) + 1
        ordered = sorted(groups.items(), key=lambda x: (-x[1], x[0]))
        # Colours by rank, not by hash: two groups never share a colour while the palette lasts.
        palette = iter(PALETTE)
        colors = {g: FIXED_COLORS.get(g) or next(palette, None) or _color(g)
                  for g, _ in sorted(ordered, key=lambda x: x[0] in FIXED_COLORS)}
        for node in nodes.values():
            node["color"] = colors[node["group"]]
        graph = {
            "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "links": [{"source": a, "target": b} for a, b in sorted(edges)],
            "groups": [{"name": g, "color": colors[g], "count": n} for g, n in ordered],
            "stats": {"nodes": len(nodes), "notes": len(notes), "missing": len(missing), "links": len(edges)},
        }
        return graph, bodies

    def _get(self) -> tuple[dict, dict]:
        with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < self.cache_s:
                return self._cache[1], self._cache[2]
            graph, bodies = self._build()
            self._cache = (time.monotonic(), graph, bodies)
            return graph, bodies

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    # ---------------------------------------------------------------- queries
    def graph(self) -> dict:
        return self._get()[0]

    def _neighbors(self, graph: dict) -> dict[str, list[str]]:
        adj: dict[str, list[str]] = {}
        for link in graph["links"]:
            adj.setdefault(link["source"], []).append(link["target"])
            adj.setdefault(link["target"], []).append(link["source"])
        return adj

    def node(self, node_id: str, neighbors: int = 30) -> dict | None:
        graph = self.graph()
        by_id = {n["id"]: n for n in graph["nodes"]}
        node = by_id.get(node_id)
        if node is None:
            return None
        adj = self._neighbors(graph)
        return {**node, "neighbors": [by_id[v]["label"] for v in adj.get(node_id, [])[:neighbors]],
                "neighbor_ids": adj.get(node_id, [])[:neighbors]}

    def query(self, q: str, *, limit: int = 10, neighbors: int = 6) -> dict:
        """Search notes by label, description and body; well-connected notes rank higher."""
        terms = _terms(q)
        graph, bodies = self._get()
        if not terms:
            return {"query": q, "results": [], "found": 0}
        adj = self._neighbors(graph)
        by_id = {n["id"]: n for n in graph["nodes"]}
        scored = []
        for node in graph["nodes"]:
            label = slug(node["label"])
            desc = slug(node["description"])
            body = slug(bodies.get(node["id"], ""))
            score = 0.0
            for t in terms:
                if label == t:
                    score += 100
                elif label.startswith(t):
                    score += 55
                elif t in label:
                    score += 38
                if t in desc:
                    score += 16
                if t in body:
                    score += 6
            if not score:
                continue
            score += min(node["degree"], 20) * 0.6
            if node["group"] in (MISSING, ARCHIVED):
                score *= 0.5
            scored.append((score, node))
        scored.sort(key=lambda x: (-x[0], x[1]["label"]))
        results = [{**n, "score": round(s, 1),
                    "neighbors": [by_id[v]["label"] for v in adj.get(n["id"], [])[:neighbors]]}
                   for s, n in scored[:max(1, min(limit, 50))]]
        return {"query": q, "results": results, "found": len(scored)}

    def pulse(self, minutes: int = 120) -> dict:
        """Notes whose content really changed in the window. Uses git when the folder is
        a repository (sync tools rewrite mtimes without changing content); mtime otherwise."""
        graph = self.graph()
        by_src = {n["src"]: n["id"] for n in graph["nodes"] if n["src"]}
        since = time.time() - minutes * 60
        alive: dict[str, int] = {}
        changed = self._git_changes(minutes)
        if changed is None:
            for src, nid in by_src.items():
                try:
                    mtime = (self.root / src).stat().st_mtime
                except OSError:
                    continue
                if mtime >= since:
                    alive[nid] = int(mtime)
        else:
            for src, ts in changed.items():
                if src in by_src:
                    alive[by_src[src]] = int(ts)
        return {"window_min": minutes, "alive": alive, "source": "mtime" if changed is None else "git"}

    def _git_changes(self, minutes: int) -> dict[str, float] | None:
        try:
            prefix = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--show-prefix"],
                                    capture_output=True, text=True, timeout=5)
            if prefix.returncode != 0:
                return None
            log = subprocess.run(
                ["git", "-C", str(self.root), "log", f"--since={int(minutes)} minutes ago",
                 "--pretty=format:@%ct", "--name-only", "--relative", "--", "."],
                capture_output=True, text=True, timeout=12)
        except (OSError, subprocess.SubprocessError):
            return None
        changed: dict[str, float] = {}
        ts = 0.0
        for line in log.stdout.splitlines():
            line = line.strip()
            if line.startswith("@"):
                ts = float(line[1:] or 0)
            elif line and ts and line not in changed:
                changed[line] = ts
        return changed
