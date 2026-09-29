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

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
SEAM = ROOT / "n8n" / "bin" / "flow-call.sh"
UI = Path(__file__).resolve().parent / "index.html"
LOGO = Path(__file__).resolve().parent.parent / "assets" / "godjira.svg"
PORT = int(os.environ.get("PANEL_PORT", "8788"))
BIND = os.environ.get("PANEL_BIND", "0.0.0.0")
TTL = int(os.environ.get("PANEL_TTL", "60"))
PROJECT = os.environ.get("JIRA_FLOW_PROJECT", "SCRUM")
MAX_BODY = int(os.environ.get("PANEL_MAX_BODY", str(40 * 1024 * 1024)))
MAX_FILE = int(os.environ.get("PANEL_MAX_FILE", str(25 * 1024 * 1024)))
DEFAULT_WISH = "Skapa ärenden för det som står i de bifogade dokumenten."

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()
# A proposal waiting for a human. The imported documents are gone by the time it
# exists: what is kept is the list the panel showed, and the client may only name
# which of *those* items to write -- it can never send issue text of its own.
_imports: dict[str, dict] = {}
IMPORT_TTL = 3600


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
    """Felet så en människa läser det: budskapet, inte toppen av en stacktrace.

    En krasch skriver "Traceback (most recent call last):" först och orsaken sist,
    så sista raden tas -- och en rad JSON hoppas över (det är själva svaret).
    """
    payload = env.get("payload")
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"]).strip().split("\n")[0]
    for line in reversed((env.get("raw") or "").strip().split("\n")):
        stripped = line.strip()
        if stripped and not stripped.startswith(("{", "[")):
            return stripped
    return "no answer"


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



def links_state() -> dict:
    """Vilket repo som hör till vilket Jira-projekt. Registret bor i CLI:t."""
    env = seam("flow", "link", "--json", timeout=60)
    data = env.get("payload") or {}
    links = data.get("links")
    if env.get("exitCode") != 0 or not isinstance(links, list):
        return {"ok": False, "links": [], "error": data.get("error") or first_line(env)}
    return {"ok": True, "links": links, "error": None}


def repo_detail(name: str) -> dict:
    """Ett repo: vad det är, vad som är öppet, lokala kopian och Jira-länken.

    Ingen egen åsikt här: svaret är `jira_flow repo --json`, samma kommando en
    människa kör i terminalen -- och därför kan panelen visa repot även när Jira
    inte svarar (kommandot rör aldrig Jira).
    """
    if not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?", (name or "").strip()):
        return {"ok": False, "error": "repot såg inte ut som ett namn"}
    env = seam("flow", "repo", name.strip(), "--json", timeout=180)
    data = env.get("payload") or {}
    if env.get("exitCode") != 0 or not isinstance(data, dict) or not data.get("ok"):
        return {"ok": False, "repo": name, "error": data.get("error") or first_line(env)}
    return data


def link_set(payload: dict) -> tuple[int, dict]:
    """Koppla ett repo till ett Jira-projekt, och till ärendet man jobbar mot.

    Länken är det som gör att ett uppladdat önskemål hamnar rätt: importen skickar
    repot, projektet kommer ur länken. Projectnyckeln kan aldrig skrivas av klienten
    utan att se ut som en nyckel.
    """
    repo = str(payload.get("repo") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?", repo):
        return 400, {"ok": False, "error": "repot såg inte ut som ett namn"}
    if payload.get("rm"):
        env = seam("flow", "link", "rm", repo, "--json", timeout=60)
    else:
        project = str(payload.get("project") or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,9}", project):
            return 400, {"ok": False, "error": "projektnyckeln såg inte ut som en nyckel"}
        args = ["flow", "link", "set", repo, "--project", project, "--json"]
        issue = str(payload.get("issue") or "").strip().upper()
        if issue:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,9}-\d+", issue):
                return 400, {"ok": False, "error": "ärendenyckeln såg inte ut som SCRUM-123"}
            args += ["--issue", issue]
        note = " ".join(str(payload.get("note") or "").split())[:200]
        if note:
            args += ["--note", note]
        env = seam(*args, timeout=60)
    data = env.get("payload") or {}
    if env.get("exitCode") != 0 or not data.get("ok"):
        return 200, {"ok": False, "error": data.get("error") or first_line(env)}
    with _lock:
        _cache.clear()      # länken ändrar vad nästa läsning ska säga
    return 200, {"ok": True, "repo": repo, "link": data.get("link") or {},
                 "removed": data.get("removed") or []}


def state() -> dict:
    return dict(cached("state", lambda: {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "jira": jira_state(),
        "github": github_state(),
        "links": links_state(),
        "flow": flow_state(),
        "journal": journal(10),
    }))


def import_parse(payload: dict) -> tuple[int, dict]:
    """A wish plus the papers it came with -> an issue proposal. Writes nothing."""
    wish = (payload.get("wish") or "").strip() or DEFAULT_WISH
    repo = str(payload.get("repo") or "").strip()
    if repo and not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?", repo):
        return 400, {"ok": False, "error": "repot såg inte ut som ett namn"}
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        return 400, {"ok": False, "error": "inga dokument bifogades"}
    now = time.time()
    for token in [t for t, e in _imports.items() if now - e["at"] > IMPORT_TTL]:
        _imports.pop(token, None)

    folder = tempfile.mkdtemp(prefix="godjira-import-")
    try:
        paths = []
        for index, entry in enumerate(files, 1):
            if not isinstance(entry, dict):
                return 400, {"ok": False, "error": "ett av dokumenten gick inte att läsa"}
            try:
                raw = base64.b64decode(str(entry.get("b64") or ""), validate=True)
            except Exception:  # noqa: BLE001 -- any bad base64 is the same answer
                return 400, {"ok": False, "error": "dokument {} kunde inte avkodas".format(index)}
            if not raw or len(raw) > MAX_FILE:
                return 400, {"ok": False, "error": "dokument {} är tomt eller större än {} MB".format(
                    index, MAX_FILE // 1024 // 1024)}
            # The client's name is never a path; only its extension is kept, and only
            # characters that cannot leave the folder. The core reads the type and
            # rejects what it cannot read, so there is no second whitelist here.
            suffix = re.sub(r"[^A-Za-z0-9.]", "", Path(str(entry.get("name") or "")).suffix)[:10]
            path = Path(folder) / "doc{}{}".format(index, suffix)
            path.write_bytes(raw)
            paths.append(path)

        args = ["flow", "plan", "--text", wish, "--json"]
        if repo:
            # Repot i stället för projektnyckeln: CLI:t slår upp länken och tar med
            # både projektet, ärendet man jobbar mot och den lokala kopian.
            args += ["--repo-name", repo]
        else:
            args += ["--project", PROJECT]
        for path in paths:
            args += ["--context", str(path)]
        env = seam(*args, timeout=600)
        data = env.get("payload") or {}
        if env.get("exitCode") != 0 or not isinstance(data.get("proposal"), list) or not data["proposal"]:
            return 200, {"ok": False, "error": data.get("error") or first_line(env)}
        token = secrets.token_urlsafe(9)
        # Repot följer med godkännandet: skrivningen ska landa där förhandsgranskningen sa.
        _imports[token] = {"proposal": data["proposal"], "at": time.time(), "repo": repo}
        return 200, {"ok": True, "token": token, "proposal": data["proposal"],
                     "project": data.get("project") or PROJECT,
                     "projectSource": data.get("projectSource") or "",
                     "repo": repo, "agent": data.get("agent"), "context": data.get("context")}
    finally:
        shutil.rmtree(folder, ignore_errors=True)   # the papers are not kept


def import_apply(payload: dict) -> tuple[int, dict]:
    """The ticked part of the proposal, and only that. One approval, used once."""
    entry = _imports.pop(str(payload.get("token") or ""), None)
    if not entry:
        return 404, {"ok": False, "error": "godkännandet gäller inte längre (använt, eller äldre än en timme)"}
    proposal = entry["proposal"]
    keep = payload.get("keep")
    if not isinstance(keep, list) or not keep:
        return 400, {"ok": False, "error": "ingenting var ibockat"}
    try:
        items = [proposal[int(i)] for i in keep if 0 <= int(i) < len(proposal)]
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "ibockningen såg inte ut som ärendenummer"}
    if not items:
        return 400, {"ok": False, "error": "de ibockade ärendena fanns inte i förslaget"}

    folder = tempfile.mkdtemp(prefix="godjira-approved-")
    path = Path(folder) / "approved.json"
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    try:
        repo = str(entry.get("repo") or "")
        args = ["flow", "plan", "--proposal", str(path), "--create", "--json"]
        args += ["--repo-name", repo] if repo else ["--project", PROJECT]
        env = seam(*args, timeout=600)
        data = env.get("payload") or {}
        if env.get("exitCode") != 0:
            # A crash mid-list says how many were written: never a silent half board.
            return 200, {"ok": False, "error": data.get("error") or first_line(env),
                         "created": data.get("created") or []}
        with _lock:
            _cache.clear()      # the board changed: the next read must not be the old one
        return 200, {"ok": True, "created": data.get("created") or [],
                     "project": data.get("project") or PROJECT}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


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
        if path in ("/godjira.svg", "/favicon.ico"):
            self._send(200, LOGO.read_bytes(), "image/svg+xml")
            return
        if path == "/api/state":
            self._send(200, json.dumps(state(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
            return
        if path == "/api/repo":
            query = parse_qs(urlparse(self.path).query)
            name = (query.get("name") or [""])[0]
            answer = cached("repo:" + name, lambda: repo_detail(name), ttl=120)
            self._json(200, answer)   # svaret bär sitt eget ok/fel
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

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802 -- http.server's own naming
        handler = {"/api/import": import_parse, "/api/import/apply": import_apply,
                   "/api/link": link_set}.get(self.path.split("?")[0])
        if not handler:
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(400, {"ok": False, "error": "ingen kropp, eller över {} MB".format(
                MAX_BODY // 1024 // 1024)})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            assert isinstance(payload, dict)
        except Exception:  # noqa: BLE001 -- malformed body is malformed
            self._json(400, {"ok": False, "error": "kroppen var inte ett JSON-objekt"})
            return
        try:
            code, answer = handler(payload)
        except Exception as exc:  # noqa: BLE001 -- a crash must not look like a dead panel
            code, answer = 500, {"ok": False, "error": "{}: {}".format(type(exc).__name__, exc)}
        self._json(code, answer)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 -- the base class names it
        print("[panel] " + format % args, flush=True)


if __name__ == "__main__":
    print("panel on http://{}:{}/ (cache {} s)".format(BIND, PORT, TTL), flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
