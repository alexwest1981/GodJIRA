#!/usr/bin/env python3
"""GodJIRA's panel -- the hub's front door.

Reads the same two CLIs as everything else (the bridge's `snapshot`, the flow's
`next`, and `gh`) through the same seam n8n uses (`n8n/bin/flow-call.sh`), so the
panel is a door and never a second opinion about the flow.

One screen, one JSON: /api/state. The UI is one file, no build step, no CDN.

    python3 panel/server.py            # 0.0.0.0:8788, the home network
    PANEL_PORT/PANEL_BIND/PANEL_TTL    # overrides

ponytail: stdlib http.server rather than a framework, and a 60 s cache because
Jira's snapshot is ~100 KB and a browser refresh should not cost an API call.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEAM = ROOT / "n8n" / "bin" / "flow-call.sh"
UI = Path(__file__).resolve().parent / "index.html"
PORT = int(os.environ.get("PANEL_PORT", "8788"))
BIND = os.environ.get("PANEL_BIND", "0.0.0.0")
TTL = int(os.environ.get("PANEL_TTL", "60"))

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def seam(*args: str, timeout: int = 180) -> dict:
    """One CLI call through the envelope n8n reads: {exitCode, payload, raw}."""
    try:
        done = subprocess.run([str(SEAM), *args], capture_output=True, text=True, timeout=timeout)
        return json.loads(done.stdout)
    except Exception as exc:  # a step that could not answer at all
        return {"exitCode": None, "payload": None, "raw": "{}: {}".format(type(exc).__name__, exc)}


def cached(key: str, build, ttl: int = TTL):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = build()
    with _lock:
        _cache[key] = (now, value)
    return value


def first_line(env: dict) -> str:
    return (env.get("raw") or "no answer").strip().split("\n")[0]


def jira_state() -> dict:
    env = seam("bridge", "snapshot")
    if env.get("exitCode") != 0 or not isinstance(env.get("payload"), dict):
        return {"ok": False, "error": first_line(env)}
    p = env["payload"]
    return {
        "ok": True,
        "mode": p.get("mode"),
        "account": p.get("account"),
        "projects": p.get("projects") or [],
        "boards": p.get("boards") or [],
        "generatedAt": p.get("generatedAt"),
    }


def login() -> str:
    env = seam("gh", "api", "user", "--jq", ".login", timeout=60)
    return (env.get("raw") or "").strip() or ""


def github_state() -> dict:
    who = login()
    repos = seam("gh", "repo", "list", "--limit", "100", "--json",
                 "name,description,visibility,isPrivate,updatedAt,primaryLanguage,stargazerCount")
    prs = seam("gh", "search", "prs", "--owner=" + (who or "@me"), "--state=open", "--limit", "50", "--json",
               "number,title,repository,updatedAt,isDraft,url")
    issues = seam("gh", "search", "issues", "--owner=" + (who or "@me"), "--state=open", "--limit", "50", "--json",
                  "number,title,repository,updatedAt,url")
    failed = [name for name, env in (("repos", repos), ("pull requests", prs), ("issues", issues))
              if not isinstance(env.get("payload"), list)]
    return {
        "ok": not failed,
        "error": ("github could not answer: " + ", ".join(failed)) if failed else None,
        "login": who,
        "repos": repos.get("payload") or [],
        "pullRequests": prs.get("payload") or [],
        "issues": issues.get("payload") or [],
    }


def flow_state() -> dict:
    env = seam("flow", "next", "--dry-run", "--json", "--project", os.environ.get("JIRA_FLOW_PROJECT", "SCRUM"))
    payload = env.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "ok": env.get("exitCode") == 0,
        "exitCode": env.get("exitCode"),
        "pick": payload.get("wouldTake") or payload.get("proposal"),
        "someoneElses": None if payload.get("wouldTake") else (payload.get("proposal") or None),
        "runnersUp": (payload.get("skipped") or [])[:5],
        "error": payload.get("error") or (first_line(env) if env.get("exitCode") not in (0, 1) else None),
    }


def journal(limit: int = 20) -> dict:
    """The flow's own writes. The command answers one JSON object with a `log`
    list (not a line per write), so the panel reads the list and not the text."""
    env = seam("bridge", "journal", str(limit))
    payload = env.get("payload") or {}
    log = payload.get("log") if isinstance(payload, dict) else None
    return {"ok": isinstance(log, list), "log": log or [],
            "error": None if isinstance(log, list) else first_line(env)}



def state() -> dict:
    return dict(cached("state", lambda: {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "jira": jira_state(),
        "github": github_state(),
        "flow": flow_state(),
        "journal": journal(10),
    }))


class Handler(BaseHTTPRequestHandler):
    server_version = "godjira-panel"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # a tab that closed mid-answer is normal, not a crash

    def do_GET(self) -> None:  # noqa: N802 -- http.server's own naming
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/state":
            self._send(200, json.dumps(state(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
            return
        if path == "/api/refresh":
            with _lock:
                _cache.clear()
            self._send(200, json.dumps(state(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
            return
        if path == "/healthz":
            self._send(200, b"ok", "text/plain")
            return
        self._send(404, b"not found", "text/plain")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 -- the base class names it
        print("[panel] " + format % args, flush=True)


if __name__ == "__main__":
    print("panel on http://{}:{}/ (cache {} s)".format(BIND, PORT, TTL), flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
