"""Proof of delivery: existing is not the same as having worked.

A task may carry a `proof` (a file, directory or URL that must exist when it is
done) and agents may declare artifacts. The kernel checks them itself instead
of trusting the agent's report.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

_WALK_LIMIT = 2_000


def check(target: str, *, since: float = 0.0, allow_urls: bool = True, timeout: float = 5.0) -> tuple[bool, str]:
    """(passes, reason) for one proof target. Never raises."""
    t = (target or "").strip()
    if not t:
        return False, "empty"
    if t.startswith(("http://", "https://")):
        if not allow_urls:
            return False, "url checks disabled"
        return _check_url(t, timeout)
    try:
        p = Path(t).expanduser()
        if not p.is_absolute():
            return False, "not an absolute path"
        if p.is_file():
            st = p.stat()
            if st.st_size == 0:
                return False, "empty file"
            if since and st.st_mtime < since:
                return False, "file was not modified during the task"
            return True, "file"
        if p.is_dir():
            if not since:
                return True, "directory"
            if _newest_mtime(p) >= since:
                return True, "directory"
            return False, "directory was not modified during the task"
        return False, "does not exist"
    except OSError as exc:
        return False, f"unreadable: {type(exc).__name__}"


def _display(target: str) -> str:
    """The informative end of a path (folder/file), not its long, machine-specific start."""
    text = str(target)
    if text.startswith(("http://", "https://")):
        return text[:80]
    path = Path(text)
    return (f"{path.parent.name}/{path.name}" if path.parent.name else path.name or text)[-80:]


def verify(targets: Iterable[str], **kw) -> tuple[bool, str]:
    """True if at least one target passes; the reason lists every check."""
    parts, passed = [], False
    for target in targets:
        ok, why = check(target, **kw)
        passed = passed or ok
        parts.append(f"{'ok' if ok else 'no'} {why}: {_display(target)}")
    if not parts:
        return False, "no proof criteria"
    return passed, "; ".join(parts[:6])


def _newest_mtime(root: Path) -> float:
    newest = root.stat().st_mtime
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames + dirnames:
            seen += 1
            if seen > _WALK_LIMIT:
                return newest
            try:
                newest = max(newest, os.stat(os.path.join(dirpath, name)).st_mtime)
            except OSError:
                continue
    return newest


def _check_url(url: str, timeout: float) -> tuple[bool, str]:
    headers = {"User-Agent": "enjambre-proof/0.1"}
    for method in ("HEAD", "GET"):
        req_headers = dict(headers)
        if method == "GET":
            req_headers["Range"] = "bytes=0-0"
        req = urllib.request.Request(url, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                return 200 <= status < 400, f"HTTP {status}"
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405, 501):
                continue
            return False, f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if method == "HEAD":
                continue
            return False, f"unreachable: {type(exc).__name__}"
    return False, "no HTTP response"
