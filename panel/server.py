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
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
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
# Automationens senaste ord. n8n skriver den, panelen visar den -- och den ligger i
# användarens egen state-katalog, inte i repot.
AUTOMATION_FILE = Path.home() / ".local/state/omarchy/godjira-automation.json"
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
    # Fyra gh-anrop i rad tog ~4 s av panelens tio. De frågar olika saker, så de får
    # gå samtidigt; "@me" betyder samma som inloggningsnamnet (mätt: samma svar).
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {
            "who": pool.submit(login),
            # url: raden i Repon-vyn skall gå att klicka på, inte bara läsas.
            "repos": pool.submit(seam, "gh", "repo", "list", "--limit", "100", "--json",
                                 "name,description,visibility,isPrivate,updatedAt,primaryLanguage,stargazerCount,url"),
            "prs": pool.submit(seam, "gh", "search", "prs", "--owner=@me", "--state=open", "--limit", "50", "--json",
                               "number,title,repository,updatedAt,isDraft,url"),
            "issues": pool.submit(seam, "gh", "search", "issues", "--owner=@me", "--state=open", "--limit", "50", "--json",
                                  "number,title,repository,updatedAt,url"),
        }
    who = jobs["who"].result() or ""
    repos, prs, issues = jobs["repos"].result(), jobs["prs"].result(), jobs["issues"].result()
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


# -------------------------------------------------------------- flödesgrafen
#
# n8n:s egen tavla går att öppna (länken finns kvar), men hubben skall visa flödet
# där man står. Filen n8n/workflows/*.workflow.ts ÄR källan och instansen i n8n är
# en kopia (n8n/README.md) -- därför ritas filen. Varje nod bär sin egen position,
# så ritningen får n8n:s egen layout i stället för en påhittad.

FLOW_DIR = ROOT / "n8n" / "workflows"
FLOW_NODE = re.compile(r"@node\(")
FLOW_EDGE = re.compile(r"this\.(\w+)\.out\((\d+)\)\.to\(this\.(\w+)\.in\((\d+)\)\)")
FLOW_NAME = re.compile(r"\bname:\s*'([^']*)'")
FLOW_TYPE = re.compile(r"\btype:\s*'([^']*)'")
FLOW_SPOT = re.compile(r"\bposition:\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]")


def decorator_body(text: str, start: int) -> str:
    """Argumenten inuti ett dekoratoranrop, alltså innehållet i @node({ ... }).

    Hängslena räknas: nodens egen konfiguration har nästlade objekt (Config bär en
    lista av { name, value }), och ett naivt sök efter `name:` hade läst dem som
    nodens namn.
    """
    depth, opening = 0, text.find("{", start)
    if opening < 0:
        return ""
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1:index]
    return ""


def flow_graph(path: Path) -> dict:
    """Noderna och vägarna ur en flödesfil."""
    text = path.read_text(errors="replace")
    head = decorator_body(text, text.find("@workflow")) if "@workflow" in text else ""
    nodes = []
    for match in FLOW_NODE.finditer(text):
        body = decorator_body(text, match.start())
        name, kind = FLOW_NAME.search(body), FLOW_TYPE.search(body)
        spot = FLOW_SPOT.search(body)
        if not (name and kind):
            continue
        # Egennamnet efter dekoratorn (EveryHour = { ... }) är det kanterna pekar på:
        # visningsnamnet är "Every hour" och egenskapen är EveryHour, så utan den här
        # nyckeln pekade varje kant i tomma luften (mätt: 0 av 7 vägar ritades).
        # Slutet på dekoratorns egna argument är `})`, och först därefter står
        # egenskapens namn. Ett ankrat sök direkt efter @node( träffar argumentens
        # egen { och ger visningsnamnet i stället.
        after = text[match.end():]
        prop = re.search(r"\}\s*\)\s*(?:export\s+)?([A-Za-z_]\w*)\s*=", after)
        nodes.append({"key": prop.group(1) if prop else name.group(1),
                      "name": name.group(1),
                      "type": kind.group(1).split(".")[-1],
                      "x": int(spot.group(1)) if spot else 0,
                      "y": int(spot.group(2)) if spot else 0})
    return {"file": path.name,
            "id": (re.search(r"\bid:\s*'([^']*)'", head) or [None, ""])[1] if head else "",
            "name": FLOW_NAME.search(head).group(1) if head and FLOW_NAME.search(head) else path.stem,
            "active": bool(re.search(r"active:\s*true", head)),
            "nodes": nodes,
            "edges": [{"from": m.group(1), "to": m.group(3)} for m in FLOW_EDGE.finditer(text)]}


N8N_DB = Path.home() / ".n8n" / "database.sqlite"


def n8n_workflows() -> dict:
    """n8n:s egen tavla, läst skrivskyddat ur dess databas.

    Panelen ritade förut ur flödesfilen i repot -- rätt data, men den egna ritningen
    blev en tolkning av filen. Här är det samma noder, samma namn och samma
    positioner som n8n själv visar, så det man flyttar i n8n syns direkt. Filen är
    fortfarande det som deployas, och den används när n8n inte svarar.

    ponytail: läser databasen i stället för n8n:s REST-API -- ingen nyckel behövs och
    den ligger på samma maskin; API:t om panelen någon gång kör mot en n8n på annat håll.
    """
    try:
        con = sqlite3.connect("file:{}?mode=ro".format(N8N_DB), uri=True, timeout=5)
        try:
            rows = con.execute("select id, name, active, nodes, connections "
                               "from workflow_entity").fetchall()
        finally:
            con.close()
    except Exception:                                  # noqa: BLE001 -- n8n är frivilligt
        return {}
    out = {}
    for wid, name, active, nodes, conns in rows:
        try:
            nodes = json.loads(nodes) if isinstance(nodes, str) else nodes
            conns = json.loads(conns) if isinstance(conns, str) else conns
        except Exception:                              # noqa: BLE001
            continue
        out[wid] = {
            "id": wid or "", "name": name or "", "active": bool(active),
            "nodes": [{"key": n.get("name") or "", "name": n.get("name") or "",
                       "type": (n.get("type") or "").split(".")[-1],
                       "x": int((n.get("position") or [0, 0])[0]),
                       "y": int((n.get("position") or [0, 0])[1])}
                      for n in (nodes or [])],
            # n8n:s kanter: {"Nod": {"main": [[{node: "Nästa", ...}], ...]}} -- en väg
            # per mål. En nod med två utgångar ger två vägar, vilket är hela poängen.
            "edges": [{"from": src, "to": c.get("node")}
                      for src, outs in (conns or {}).items()
                      for group in (outs or {}).get("main", []) or []
                      for c in (group or []) if c.get("node")]}
    return out


def flow_graphs() -> list:
    """Varje flöde i repot. En fil som inte går att läsa namnges i stället för att
    sänka hela vyn -- samma hållning som jira_state() har mot ett tyst svar."""
    live = n8n_workflows()
    out = []
    for path in sorted(FLOW_DIR.glob("*.workflow.ts")):
        try:
            flow = flow_graph(path)
            # Flödesfilen bär samma id som n8n:s arbetsflöde; finns det i n8n är det
            # n8n:s noder och positioner som gäller (och n8n:s kanter, som pekar på
            # visningsnamn i stället för på filens egenskapsnamn).
            one = live.get(flow.get("id") or "")
            if one:
                flow = {**flow, **{k: one[k] for k in ("name", "active", "nodes", "edges")},
                        "source": "n8n"}
            else:
                flow["source"] = "file"
            out.append(flow)
        except Exception as exc:  # noqa: BLE001 -- vilket fel som helst är samma svar
            out.append({"file": path.name, "nodes": [], "edges": [],
                        "error": "{}: {}".format(type(exc).__name__, exc)})
    return out


def project_of_the_link(flow: dict) -> dict:
    """Projektet man är kopplad till.

    Nyckeln och källan kommer ur CLI:ts eget svar (flödets nästa räknar ut dem med
    samma regel som terminalen -- panelen har ingen egen uppfattning). Repot och
    ärendet kommer ur registret, för det är de som är kopplade.
    """
    env = seam("flow", "link", "--json", timeout=60)
    links = [l for l in (((env.get("payload") or {}).get("links")) or []) if (l or {}).get("project")]
    one = links[0] if len(links) == 1 else {}
    return {"key": (flow or {}).get("project") or PROJECT,
            "source": (flow or {}).get("projectSource") or "standarden",
            "repo": one.get("repo") or "", "issue": one.get("issue") or ""}


def automation_state() -> dict:
    """Vad automaten såg senast. Tom när n8n inte har kört än."""
    try:
        data = json.loads(AUTOMATION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def automation_report(payload: dict) -> tuple[int, dict]:
    """n8n:s flöde lämnar sin sammanfattning här, så panelen kan visa den.

    Ingen logik flyttar hit: fälten är det flödet redan räknade ut (samma kommandon
    som panelen kör). Bara korta strängar och kända nycklar sparas.
    """
    def text(name: str, cap: int = 300) -> str:
        return " ".join(str(payload.get(name) or "").split())[:cap]

    project = text("project", 16).upper()
    if project and not re.fullmatch(r"[A-Z][A-Z0-9_]{1,9}", project):
        return 400, {"ok": False, "error": "projektnyckeln såg inte ut som en nyckel"}
    pick = payload.get("theFlowsPick")
    if isinstance(pick, dict):
        pick = {"key": str(pick.get("key") or "")[:24], "summary": str(pick.get("summary") or "")[:160],
                "priority": str(pick.get("priority") or "")[:16]}
    else:
        pick = None
    report = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "checkedAt": text("checkedAt", 40),
        "project": project or PROJECT,
        "projectSource": text("projectSource", 80),
        "ok": bool(payload.get("ok", True)),
        "note": text("note", 300),
        "runner": text("runner", 40) or "n8n",
        "amIOn": text("amIOn", 24),
        "theFlowsPick": pick,
        "runnersUp": [str(x)[:160] for x in (payload.get("runnersUp") or [])][:5]
        if isinstance(payload.get("runnersUp"), list) else [],
        "workflowId": text("workflowId", 40),
    }
    AUTOMATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTOMATION_FILE.parent.chmod(0o700)
    AUTOMATION_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    AUTOMATION_FILE.chmod(0o600)
    with _lock:
        _cache.clear()          # automaten har sagt sitt: nästa läsning ska visa det
    return 200, {"ok": True, "automation": report}


def flow_state() -> dict:
    # Utan --project: CLI:t tar projektet ur länken (flaggan vinner om den finns).
    env = seam("flow", "next", "--dry-run", "--json")
    payload = env.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "ok": env.get("exitCode") == 0,
        "exitCode": env.get("exitCode"),
        "pick": payload.get("wouldTake") or payload.get("proposal"),
        "someoneElses": None if payload.get("wouldTake") else (payload.get("proposal") or None),
        "runnersUp": (payload.get("skipped") or [])[:5],
        # Var projektnyckeln kom ifrån står i CLI:ts eget svar: panelen gissar inte.
        "project": payload.get("project") or "",
        "projectSource": payload.get("projectSource") or "",
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



SCAN_DIR = Path.home() / ".config/jira-flow"
_scan_running: dict = {}
_scan_lock = threading.Lock()


def scan_read(repo: str) -> dict:
    """Kartan som scannen skrev, för det projekt repot är kopplat till.

    Panelen läser filen i stället för att fråga Jira: samma arbetsdelning som länken,
    och en scanning pågår i tio-tjugo sekunder utan att vyn står och väntar.
    """
    links = (links_state().get("links") or [])
    hit = next((l for l in links if str((l or {}).get("repo") or "").lower() == repo.lower()), None)
    if not hit:
        hit = links[0] if len(links) == 1 else None
    if not hit:
        return {"ok": True, "map": None, "running": False,
                "note": "no Jira link here, so there is no project to scan"}
    project = str(hit.get("project") or "").upper()
    path = SCAN_DIR / "scan-{}.json".format(project)
    answer = {"ok": True, "project": project, "repo": hit.get("repo"),
              "running": bool(_scan_running.get(repo or project)), "map": None}
    try:
        answer["map"] = json.loads(path.read_text())
    except (OSError, ValueError):
        answer["note"] = "no scan has been run for {} yet".format(project)
    return answer


def scan_start(payload: dict) -> tuple[int, dict]:
    """Starta en scanning av hela projektet. Jobbet går i bakgrunden: en scanning tar
    tio-tjugo sekunder, och en panel som står och väntar ser trasig ut.

    ponytail: en scanning per repo i taget, och en ny nekas inom tre minuter. Räcker
    för en användare; en kö behövs först om flera trycker samtidigt.
    """
    repo = str(payload.get("repo") or "").strip()
    if repo and not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)?", repo):
        return 400, {"ok": False, "error": "repot såg inte ut som ett namn"}
    key = repo or "standard"
    with _scan_lock:
        started = float(_scan_running.get(key) or 0)
        if started and time.time() - started < 180:
            return 200, {"ok": True, "running": True,
                         "secondsAgo": round(time.time() - started)}
        _scan_running[key] = time.time()

    def work() -> None:
        args = ["flow", "scan"]
        if repo:
            args += ["--repo-name", repo]
        args += ["--json"]
        try:
            seam(*args, timeout=900)
        finally:
            _scan_running[key] = 0

    threading.Thread(target=work, daemon=True).start()
    return 200, {"ok": True, "started": True, "repo": repo or None}


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


def project_can() -> dict:
    """Får kontot skapa projekt? Bryggan frågar Jira -- panelen upprepar bara svaret.

    Att skapa ett projekt kräver den globala behörigheten "Administer Jira", som är
    en annan sak än "Administer Projects". Svaret bär därför sin egen förklaring, så
    ytan kan säga varför i stället för att visa en knapp som inte gör något.
    """
    env = seam("bridge", "project-can", timeout=60)
    data = env.get("payload") or {}
    if env.get("exitCode") != 0 or not isinstance(data, dict) or not data.get("ok"):
        return {"may": False, "error": data.get("error") or first_line(env)}
    return data.get("canCreate") or {"may": False}


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
    """En läsning: flödet räknar ut projektet, projektet läser ur registret, resten är Jira, GitHub och journalen."""
    def build() -> dict:
        # Åtta anrop i rad tog ~10 s, och skalet stod tomt under tiden -- rälen finns
        # först när svaret kommer, så Alex hade ingenting att klicka på. Delarna rör
        # olika system (Jira, GitHub, flödet, registret, journalen) och får gå samtidigt.
        flow = flow_state()
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {"jira": pool.submit(jira_state),
                    "github": pool.submit(github_state),
                    "project": pool.submit(project_of_the_link, flow),
                    "automation": pool.submit(automation_state),
                    "links": pool.submit(links_state),
                    "journal": pool.submit(journal, 10),
                    "flows": pool.submit(lambda: cached("flows", flow_graphs)),
                    # Bara frågan om behörigheten (ett anrop): projektlistan finns
                    # redan i jira-svaret. Utan den här raden vore "nytt projekt" en
                    # knapp som inte gör något på en sajt där kontot inte får skapa.
                    "projectCan": pool.submit(lambda: cached("projectCan", project_can))}
        return {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "flow": flow, **{name: job.result() for name, job in jobs.items()}}
    return dict(cached("state", build))


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
        if path == "/api/scan":
            query = parse_qs(urlparse(self.path).query)
            self._json(200, scan_read((query.get("repo") or [""])[0]))
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
                   "/api/link": link_set, "/api/automation": automation_report,
                   "/api/scan": scan_start}.get(self.path.split("?")[0])
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
