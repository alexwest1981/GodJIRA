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
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
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
# Panelen visar din Jira-data med din nyckel i ryggen: den lyssnar på den egna maskinen
# om ingen sagt något annat. PANEL_BIND=0.0.0.0 öppnar den i nätet, medvetet.
BIND = os.environ.get("PANEL_BIND", "127.0.0.1")

# Väktaren: bara den här maskinen får svar. Utan den kan en sida du råkar besöka skicka
# en POST hit (webbläsaren gör det utan förvarning -- en så kallad simple request) och
# till exempel skriva om adminlistan, eller läsa allt genom ett värdnamn som pekar på
# 127.0.0.1. Panelen är ett lokalt verktyg.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}
IP4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def host_ok(value: str) -> bool:
    """Sant om Host/Origin pekar på den här maskinen.

    Ett *värdnamn* nekas -- det är precis så en rebinding-attack ser ut. En ren
    IP-adress släpps igenom, så en panel som medvetet bundits till nätet fungerar
    (en sida någon annanstans kan ändå inte sätta Host själv, och skrivningar har
    dessutom ursprungskontrollen nedan). PANEL_HOST=<namn> tillåter ett eget namn.
    """
    text = (value or "").strip().lower()
    if not text or text == "null":
        return False
    if "://" in text:                      # Origin skickar med sitt scheme
        text = text.split("://", 1)[1]
    text = text.split("/")[0]
    if text.startswith("["):               # IPv6 skrivs [::1]:8788
        text = text.split("]")[0] + "]"
    else:
        text = text.split(":")[0]
    if text in LOCAL_HOSTS or IP4.match(text):
        return True
    extra = os.environ.get("PANEL_HOST", "").strip().lower()
    return bool(extra) and text == extra
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


def seam(*args: str, timeout: int = 180, stdin_text: str = "") -> dict:
    """One CLI call through the envelope n8n reads: {exitCode, payload, raw}.

    `stdin_text` finns för nycklarna: en token får inte stå i argv, där syns den i
    processlistan för varje användare på maskinen.
    """
    try:
        done = subprocess.run([str(SEAM), *args], capture_output=True, text=True, timeout=timeout,
                              input=stdin_text or None)
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


def github_read() -> dict:
    """Vem gh ar inloggad som, och var man skaffar en ny nyckel."""
    env = seam("gh", "api", "user", "--jq", ".login", timeout=60)
    name = (env.get("raw") or "").strip()
    return {"ok": bool(name) and env.get("exitCode") == 0, "login": name,
            "tokenPage": "https://github.com/settings/tokens",
            "error": "" if name else first_line(env)}


def github_save(payload: dict) -> tuple[int, dict]:
    """En GitHub-nyckel, genom gh:s egen inloggning.

    Nyckeln går på stdin (`--with-token`) -- aldrig i argv. gh prövar den mot
    GitHub innan den sparas, och svarar med sitt eget fel om den inte duger.
    """
    if payload.get("rm"):
        env = seam("gh", "auth", "logout", "--hostname", "github.com", timeout=60)
        return (200 if env.get("exitCode") == 0 else 400), {"ok": env.get("exitCode") == 0,
                                                           "error": None if env.get("exitCode") == 0
                                                           else first_line(env)}
    token = str(payload.get("token") or "").strip()
    if not token:
        return 400, {"ok": False, "error": "ingen nyckel i förfrågan"}
    if len(token) < 20 or any(ch.isspace() for ch in token):
        return 400, {"ok": False, "error": "det såg inte ut som en GitHub-nyckel"}
    env = seam("gh", "auth", "login", "--with-token", timeout=120, stdin_text=token + "\n")
    who = github_read() if env.get("exitCode") == 0 else {}
    good = bool(who.get("ok"))
    return (200 if good else 400), {"ok": good, "login": who.get("login", ""),
                                    "error": None if good else (first_line(env) or "GitHub nekade nyckeln")}


def admin_read() -> dict:
    """Admin-ytan: roller med folk, fälten, och vad kontot får. Ren läsning.

    Vad som får synas avgörs av Jiras eget svar (`can`), inte av vad sidan tror:
    gränssnittet är artighet, Jira säger nej på riktigt.
    """
    env = seam("flow", "admin", "all", "--json", timeout=180)
    data = env.get("payload") or {}
    if env.get("exitCode") != 0 or not data:
        return {"ok": False, "error": first_line(env) or "admin-svaret gick inte att läsa"}
    data["ok"] = True
    return data


def admin_people(payload: dict) -> tuple[int, dict]:
    """Personer på sajten -- det man behöver för att kunna tilldela något."""
    query = " ".join(str(payload.get("query") or "").split())[:60]
    if len(query) < 2:
        return 400, {"ok": False, "error": "skriv minst två tecken att söka på"}
    env = seam("flow", "admin", "people", query, "--json", timeout=120)
    data = env.get("payload") or {}
    if env.get("exitCode") != 0:
        return 400, {"ok": False, "error": first_line(env) or "sökningen gick inte att läsa"}
    return 200, {"ok": True, "people": data.get("people") or []}


def setup_read() -> dict:
    """Det en ny maskin behöver veta: är Jira och GitHub påkopplade, och vems."""
    jira, hub = token_read(), github_read()
    return {"ok": True, "jira": jira, "github": hub,
            "ready": bool(jira.get("present") and jira.get("connected") and hub.get("ok"))}


def demo_mode() -> bool:
    """Sant när bryggan kör mot sin egen testdata, alltså när inget konto är kopplat.

    Panelen är då en provkörning: Jira-delen kommer ur bryggans mock, och GitHub och
    flödet får exempelsvar av sina egna funktioner nedan. Det är poängen -- man skall
    kunna se hela appen innan man kopplar något. Anropet cachas: det är en process.
    """
    try:
        return (seam("bridge", "status", timeout=60).get("payload") or {}).get("mode") == "mock"
    except Exception:  # ingen brygga alls: då finns inget provläge att tala om
        return False


def demo() -> bool:
    return bool(cached("demo", demo_mode, ttl=60))


DEMO_LOGIN = "demo-user"
DEMO_REPOS = [
    {"name": "web-platform", "description": "Storefront and account pages", "visibility": "PUBLIC",
     "isPrivate": False, "updatedAt": "2026-09-29T14:20:00Z", "primaryLanguage": {"name": "TypeScript"},
     "stargazerCount": 128, "url": "https://github.com/demo-user/web-platform"},
    {"name": "mobile-app", "description": "iOS and Android client", "visibility": "PUBLIC",
     "isPrivate": False, "updatedAt": "2026-09-28T08:05:00Z", "primaryLanguage": {"name": "Kotlin"},
     "stargazerCount": 54, "url": "https://github.com/demo-user/mobile-app"},
    {"name": "design-tokens", "description": "The colours, spacing and type scale", "visibility": "PUBLIC",
     "isPrivate": False, "updatedAt": "2026-09-26T19:41:00Z", "primaryLanguage": {"name": "CSS"},
     "stargazerCount": 12, "url": "https://github.com/demo-user/design-tokens"},
    {"name": "internal-tools", "description": "Scripts the team runs by hand", "visibility": "PRIVATE",
     "isPrivate": True, "updatedAt": "2026-09-25T07:12:00Z", "primaryLanguage": {"name": "Python"},
     "stargazerCount": 0, "url": "https://github.com/demo-user/internal-tools"},
]
DEMO_PRS = [
    {"number": 212, "title": "Checkout: keep the cart when the session expires", "isDraft": False,
     "updatedAt": "2026-09-29T16:02:00Z", "url": "https://github.com/demo-user/web-platform/pull/212",
     "repository": {"name": "web-platform", "nameWithOwner": "demo-user/web-platform"}},
    {"number": 87, "title": "Bump the payment SDK", "isDraft": True,
     "updatedAt": "2026-09-28T11:30:00Z", "url": "https://github.com/demo-user/mobile-app/pull/87",
     "repository": {"name": "mobile-app", "nameWithOwner": "demo-user/mobile-app"}},
]
DEMO_GITHUB_ISSUES = [
    {"number": 419, "title": "Search returns stale results after a rename",
     "updatedAt": "2026-09-27T09:15:00Z", "url": "https://github.com/demo-user/web-platform/issues/419",
     "repository": {"name": "web-platform", "nameWithOwner": "demo-user/web-platform"}},
]


# ------------------------------------------------------------------ panelens språk
#
# Panelens egna strängar ligger i panel/i18n/<kod>.json. Det är samma val som widgeten
# läser (bryggans config), så en inställning gäller båda -- och widgeten har redan
# nio språk; hit flyttas bara panelen till samma källa.

PANEL_I18N = Path(__file__).resolve().parent / "i18n"


def panel_languages() -> list:
    """Språkkoderna panelen har filer för."""
    return sorted(p.stem for p in PANEL_I18N.glob("*.json"))


def read_panel_strings(code: str) -> dict:
    """En språkfil, eller tomt. Trasig json betyder tom, inte krasch."""
    try:
        data = json.loads((PANEL_I18N / "{}.json".format(code)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def panel_language(want: str = "") -> str:
    """Språket panelen skall visa: det som efterfrågas, annars bryggans val, annars engelska."""
    have = panel_languages()
    if not have:
        return "sv"
    code = (want or "").strip().lower()
    if code in have:
        return code
    try:
        chosen = ((seam("bridge", "status", timeout=60).get("payload") or {}).get("language") or "")
    except Exception:  # ingen brygga: engelska är ett tryggare val än svenska för en främling
        chosen = ""
    chosen = chosen.strip().lower()
    if chosen in have:
        return chosen
    return "en" if "en" in have else have[0]


def panel_strings(want: str = "") -> dict:
    """Panelens strängar på ett språk, med svenskan som fallback rad för rad."""
    have = panel_languages()
    code = panel_language(want)
    base = read_panel_strings("sv") if "sv" in have else {}
    table = read_panel_strings(code)
    merged = {key: (table.get(key) or value) for key, value in base.items()}
    for key, value in table.items():
        merged.setdefault(key, value)
    return {"ok": True, "language": code, "available": available_languages(have),
            "strings": merged, "keys": len(merged), "translated": len(table)}


def available_languages(have: list = None) -> list:
    """Kod och eget namn, till rullistan. Namnen kommer ur bryggans katalog."""
    have = have or panel_languages()
    try:
        catalog = (seam("bridge", "strings", "sv", timeout=60).get("payload") or {}).get("available") or []
    except Exception:
        catalog = []
    out = [{"code": item.get("code"), "native": item.get("native") or item.get("code")}
           for item in catalog if (item or {}).get("code") in have]
    return out or [{"code": c, "native": c} for c in have]


def language_save(payload: dict) -> tuple:
    """Spara språkvalet. Samma config som widgeten läser, så valet gäller båda."""
    code = str(payload.get("language") or "").strip().lower()
    if code not in panel_languages():
        return 400, {"ok": False, "error": "okänt språk: {}".format(code[:12])}
    env = seam("bridge", "configure", json.dumps({"language": code}), timeout=90)
    ok = env.get("exitCode") == 0 and bool((env.get("payload") or {}).get("ok", True))
    with _lock:
        _cache.clear()   # allt som svarar med text skall svara på det nya språket
    return (200 if ok else 500), {"ok": ok, "language": code,
                                  "error": None if ok else first_line(env)}


def github_state() -> dict:
    if demo():
        # Provläget: gh får inte ens köra. Den skulle svara med användarens *egna* repos
        # och inloggningsnamn, och en skärmdump av provläget skall inte bära någons konto.
        return {"ok": True, "error": None, "login": DEMO_LOGIN, "repos": DEMO_REPOS,
                "pullRequests": DEMO_PRS, "issues": DEMO_GITHUB_ISSUES, "demo": True}
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
            # Parametrarna följer med: de ÄR vad noden gör, och utan dem blir ett klick
            # på en nod i panelen ett klick i tomma luften (n8n visar dem i sin ruta).
            # Nycklar och hemligheter stannar i n8n -- autentiseringsuppgifter pekas
            # bara ut med namn, aldrig med innehåll.
            "nodes": [{"key": n.get("name") or "", "name": n.get("name") or "",
                       "type": (n.get("type") or "").split(".")[-1],
                       "typeFull": n.get("type") or "",
                       "version": n.get("typeVersion"),
                       "disabled": bool(n.get("disabled")),
                       "parameters": n.get("parameters") or {},
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


def n8n_runs() -> dict:
    """Senaste körningen per flöde, ur n8n:s egen databas.

    Status, start och slut -- det n8n visar i sin körningslista, och det som svarar på
    om flödet lever. Skrivskyddat och utan nyckel, samma väg som positionerna.

    ponytail: bara senaste körningen per flöde, en fråga. Per-nodstatus ligger i
    execution_data, men den är chunkad och versionsbunden (mätt: runData låg i en
    delpost med ett annat skal, och en delpost hade bara en nod). Historik och
    nodstatus läggs på när någon saknar dem -- inte innan.
    """
    try:
        con = sqlite3.connect("file:{}?mode=ro".format(N8N_DB), uri=True, timeout=5)
        try:
            rows = con.execute(
                "select e.workflowId, e.status, e.startedAt, e.stoppedAt, e.id "
                "from execution_entity e "
                "join (select workflowId, max(id) as id from execution_entity "
                "      group by workflowId) s on s.id = e.id").fetchall()
        finally:
            con.close()
    except Exception:                                  # noqa: BLE001 -- n8n är frivilligt
        return {}
    return {wid: {"status": status or "", "startedAt": started or "",
                  "stoppedAt": stopped or "", "id": eid or 0}
            for wid, status, started, stopped, eid in rows}


def flow_graphs() -> list:
    """Varje flöde i repot. En fil som inte går att läsa namnges i stället för att
    sänka hela vyn -- samma hållning som jira_state() har mot ett tyst svar."""
    live = n8n_workflows()
    runs = n8n_runs()
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
            # Körningen hör till flödets id, som filen bär -- så den syns även när n8n
            # inte svarar och noderna kommer ur filen.
            flow["run"] = runs.get(flow.get("id") or "") or {}
            # Flödeskartan: artefakten visas i stället för ritytan när Archify finns.
            # Språket är panelens, och en rad som saknar det faller till engelska.
            flow["map"] = ("/api/flowmap?flow={}&lang={}".format(flow["id"], panel_language(""))
                           if (flow.get("id") and flowmap_available()) else "")
            out.append(flow)
        except Exception as exc:  # noqa: BLE001 -- vilket fel som helst är samma svar
            out.append({"file": path.name, "nodes": [], "edges": [],
                        "error": "{}: {}".format(type(exc).__name__, exc)})
    return out


ENGINE = Path(__file__).resolve().parent.parent / "bin" / "jira_flow.py"


def flowmap_cache_dir() -> Path:
    """Artefakterna bor hos användaren, inte i repot."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "jira-flow" / "flowmap"


def flowmap_available() -> bool:
    """Finns Archify och node? Annars ritas flödet på ritytan, precis som förut."""
    root = Path.home() / ".local/share/godjira/vendor"
    candidates = [os.environ.get("ARCHIFY") or "",
                  str(root / "archify" / "archify" / "bin" / "archify.mjs"),
                  str(root / "archify" / "bin" / "archify.mjs")]
    return bool(shutil.which("node")) and any(p and Path(p).exists() for p in candidates)


def flowmap_engine(args: list, timeout: int = 300) -> dict:
    """Kör motorn och läser dess kvitto. Flödeslogiken har en ägare."""
    try:
        proc = subprocess.run([sys.executable, str(ENGINE)] + args,
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "why": "{}: {}".format(type(exc).__name__, exc)}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {"ok": False, "why": (proc.stderr or proc.stdout or "inget svar")[-200:]}


def flowmap_artifact(wid: str, lang: str) -> dict:
    """Flödets artefakt, ur cachen eller nyr itad.

    IR:en byggs varje gång -- den är en lokal databasläsning -- och jämförs mot
    stämpeln. HTML:en ritas bara om när noderna verkligen ändrats; Archify tar
    ett par sekunder och flödet står stilla för det mesta.
    """
    folder = flowmap_cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    html = folder / "{}-{}.html".format(wid, lang)
    stamp = folder / "{}-{}.stamp".format(wid, lang)
    with tempfile.TemporaryDirectory() as scratch:
        drawn = Path(scratch) / "ir.json"
        made = flowmap_engine(["flowmap", "--workflow", wid, "--lang", lang, "--ir", str(drawn)], timeout=60)
        if not made.get("ok"):
            return {"ok": False, "why": made.get("why") or "IR:en kunde inte byggas"}
        want = hashlib.sha256(drawn.read_bytes()).hexdigest()
        if html.exists() and stamp.exists() and stamp.read_text(encoding="utf-8").strip() == want:
            return {"ok": True, "html": html, "cached": True}
    done = flowmap_engine(["flowmap", "--workflow", wid, "--lang", lang, "--out", str(html), "--json"])
    if not done.get("ok"):
        item = (done.get("diagnostics") or [{}])[0]
        return {"ok": False, "why": item.get("message") or "renderingen föll"}
    stamp.write_text(want, encoding="utf-8")
    os.chmod(html, 0o600)
    os.chmod(stamp, 0o600)
    return {"ok": True, "html": html, "cached": False}


def project_of_the_link(flow: dict) -> dict:
    """Projektet man är kopplad till.

    Nyckeln och källan kommer ur CLI:ts eget svar (flödets nästa räknar ut dem med
    samma regel som terminalen -- panelen har ingen egen uppfattning). Repot och
    ärendet kommer ur registret, för det är de som är kopplade.
    """
    env = seam("flow", "link", "--json", timeout=60)
    links = [l for l in (((env.get("payload") or {}).get("links")) or []) if (l or {}).get("project")]
    one = links[0] if len(links) == 1 else {}
    if (flow or {}).get("project"):
        return {"key": flow["project"], "source": flow.get("projectSource") or "standarden",
                "repo": one.get("repo") or "", "issue": one.get("issue") or ""}
    # Ingen länk att peka på: ta det första projektet anslutningen ser. I provläget är det
    # mockens WEB, för en ny anslutning hens eget första projekt -- annars möttes en färsk
    # installation av en tom tavla och en projektnyckel hon aldrig hört talas om.
    return {"key": (first_project_of_connection() or PROJECT), "source": "anslutningens första",
            "repo": "", "issue": ""}


def first_project_of_connection() -> str:
    """Nyckeln till det första projektet anslutningen ser, annars tom sträng."""
    try:
        snapshot = (seam("bridge", "snapshot", timeout=90).get("payload") or {})
    except Exception:  # ingen anslutning alls: standarden får gälla
        return ""
    for project in snapshot.get("projects") or []:
        if (project or {}).get("key"):
            return str(project["key"])
    return ""


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
    # Kartan: n8n:s egen läsning av hela projektet. Den valideras som allt annat som
    # kommer utifrån -- den hamnar i kortet, så den får inte vara text eller negativ.
    raw_map = payload.get("theMap")
    if isinstance(raw_map, dict):
        def count(name: str) -> int:
            try:
                return max(0, int(raw_map.get(name) or 0))
            except (TypeError, ValueError):
                return 0
        raw_map = {"issues": count("issues"), "files": count("files"), "mapped": count("mapped"),
                   "missing": count("missing"), "silentFiles": count("silentFiles"),
                   "at": str(raw_map.get("at") or "")[:24]}
        raw_map = raw_map if raw_map["issues"] else None
    else:
        raw_map = None
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
        "theMap": raw_map,
    }
    AUTOMATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTOMATION_FILE.parent.chmod(0o700)
    AUTOMATION_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    AUTOMATION_FILE.chmod(0o600)
    with _lock:
        _cache.clear()          # automaten har sagt sitt: nästa läsning ska visa det
    return 200, {"ok": True, "automation": report}


def flow_state() -> dict:
    if demo():
        # Flödets nästa kräver en riktig anslutning, så i provläget svarar panelen själv
        # med ett exempel. Annars stod det ett tokenfel i kortet i stället för en uppgift.
        return {"ok": True, "exitCode": 0, "demo": True, "error": None,
                "project": "WEB", "projectSource": "provläget",
                "pick": {"key": "WEB-42", "summary": "Fix flaky CI for the e2e suite",
                         "priority": "Highest", "statusName": "In Progress"},
                "someoneElses": {"key": "MOB-24", "summary": "Crash on startup with empty account"},
                "runnersUp": [{"key": "WEB-44", "summary": "Migrate build pipeline to the new runner"}]}
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
TOKEN_META = SCAN_DIR / "token.json"        # nyckelns utgångsdatum, aldrig nyckeln
TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens"
WARN_DAYS = 14                              # så nära utgången larmar panelen


def days_left(expires: str, today: str = "") -> int | None:
    """Dagar kvar till utgångsdatumet, eller None när inget datum är satt.

    Datumet är användarens eget (Atlassian visar det när nyckeln skapas, men har
    inget API för det) -- panelen frågar en gång och minns. Ett datum i det förflutna
    ger ett negativt tal: nyckeln är redan ute, och det sägs rakt ut.
    """
    text = (expires or "").strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        end = datetime.date.fromisoformat(text)
    except ValueError:
        return None
    now = datetime.date.fromisoformat(today) if today else datetime.date.today()
    return (end - now).days


def token_read() -> dict:
    """Vad panelen vet om nyckeln: vems den är, om den svarar, och när den går ut.

    Statusen kommer ur bryggan (samma svar som CLI:t visar), utgångsdatumet ur
    panelens egen metadatafil. Nyckeln själv lämnar aldrig den här funktionen.
    """
    env = seam("bridge", "status", timeout=90)
    data = env.get("payload") or {}
    conn = data.get("connection") or {}
    account = data.get("account") or {}
    meta = {}
    try:
        meta = json.loads(TOKEN_META.read_text())
    except (OSError, ValueError):
        meta = {}
    left = days_left(meta.get("expires") or "")
    answer = {"ok": True, "present": bool(conn.get("hasToken")),
              "connected": bool(data.get("connected")),
              "email": conn.get("email") or account.get("email") or "",
              "siteUrl": conn.get("siteUrl") or account.get("siteUrl") or "",
              "displayName": account.get("displayName") or "",
              "expires": meta.get("expires") or "", "setAt": meta.get("at") or "",
              "daysLeft": left, "warnDays": WARN_DAYS, "tokenPage": TOKEN_PAGE,
              "error": data.get("error") or ""}
    if demo():
        answer.update(alert="", note="provläge: exempeldata, inget konto kopplat", demo=True)
        return answer
    if not answer["present"]:
        answer["alert"], answer["note"] = "missing", "ingen nyckel sparad på den här maskinen"
    elif data.get("error") and not answer["connected"]:
        answer["alert"], answer["note"] = "rejected", "Jira avvisade nyckeln — gör en ny"
    elif left is not None and left <= WARN_DAYS:
        answer["alert"] = "expiring"
        answer["note"] = ("nyckeln går ut om {} dagar".format(left) if left >= 0
                          else "nyckeln gick ut för {} dagar sedan".format(-left))
    else:
        answer["alert"], answer["note"] = "", ""
    return answer


def token_save(payload: dict) -> tuple[int, dict]:
    """En ny nyckel, genom bryggans egen login.

    Nyckeln skrivs till en 0600-fil som bryggan läser och raderar: den får aldrig
    stå i argv (den syns i processlistan) och aldrig i en logg. Bryggan prövar den
    mot Jira innan något skrivs, så en felaktig nyckel lämnar anslutningen orörd.
    """
    if payload.get("rm"):
        env = seam("bridge", "logout", "--yes", timeout=120)
        good = env.get("exitCode") == 0
        return (200 if good else 400), {"ok": good,
                                        "error": None if good else (first_line(env) or "kunde inte ta bort nyckeln")}
    token = str(payload.get("token") or "").strip()
    if not token:
        return 400, {"ok": False, "error": "ingen nyckel i förfrågan"}
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,}", token):
        return 400, {"ok": False, "error": "det såg inte ut som en Atlassian-nyckel"}
    handle, name = tempfile.mkstemp(prefix="godjira-token-")
    path = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(token)
        args = ["bridge", "login", "--token-file", str(path)]
        site = str(payload.get("site") or "").strip()
        email = str(payload.get("email") or "").strip()
        if site:
            if not re.fullmatch(r"https?://[A-Za-z0-9._-]+\.atlassian\.net/?", site):
                return 400, {"ok": False, "error": "adressen såg inte ut som en Atlassian-sajt"}
            args += ["--site", site]
        if email:
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                return 400, {"ok": False, "error": "e-postadressen såg inte ut som en adress"}
            args += ["--email", email]
        if payload.get("replace"):
            args.append("--replace")
        env = seam(*args, timeout=240)
    finally:
        path.unlink(missing_ok=True)      # bryggan tar den själv; här om den nekades
    data = env.get("payload") or {}
    good = env.get("exitCode") == 0 and bool(data.get("ok"))
    if not good:
        return 400, {"ok": False, "error": data.get("error") or first_line(env) or "inloggningen nekades"}
    # Utgångsdatumet är användarens eget ord; det sparas bara när nyckeln accepterats.
    expires = str(payload.get("expires") or "").strip()[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires):
        try:
            TOKEN_META.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_META.write_text(json.dumps({"expires": expires, "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                             ensure_ascii=False))
            os.chmod(TOKEN_META, 0o600)
        except OSError:
            pass
    return 200, {"ok": True, "daysLeft": days_left(expires), "status": data}
_scan_running: dict = {}
_scan_lock = threading.Lock()


def codemap_read(repo: str) -> dict:
    """Kodkartan för repot: paketen och deras beroenden.

    Motorn läser källfilerna -- den känner till var kopian ligger och äger parsningen --
    och panelen ritar. Samma arbetsdelning som kartan över ärendena: vyn väntar aldrig
    på att något skall räknas i webbläsaren.
    """
    name = (repo or "").strip()
    if not name:
        links = (links_state().get("links") or [])
        name = str((links[0] or {}).get("repo") or "") if len(links) == 1 else ""
    if not name:
        return {"ok": False, "error": "no repo to map"}
    env = seam("flow", "codemap", "--repo-name", name, "--json", timeout=120)
    data = env.get("payload") if isinstance(env.get("payload"), dict) else None
    if not data:
        return {"ok": False, "error": first_line(env) or "the code map could not be read"}
    data["ok"] = bool(data.get("nodes"))
    return data


def graph_read(repo: str) -> dict:
    """Kunskapsgrafen för repot: paket, filer och ärenden med sina relationer.

    Byggs av motorn ur scan-filen och källkoden -- inget Jira-anrop, alltså under en
    sekund, och kan läsas av en agent så ofta den vill. Ärendena kommer ur scanningen;
    finns ingen sådan säger svaret det i stället för att visa en halv graf.
    """
    name = (repo or "").strip()
    if not name:
        links = (links_state().get("links") or [])
        name = str((links[0] or {}).get("repo") or "") if len(links) == 1 else ""
    if not name:
        return {"ok": False, "error": "no repo to graph"}
    env = seam("flow", "graph", "--repo-name", name, "--json", timeout=120)
    data = env.get("payload") if isinstance(env.get("payload"), dict) else None
    if not data:
        return {"ok": False, "error": first_line(env) or "the graph could not be built"}
    return data


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


"""Insikterna: sammanfattningen, utvecklingen och tidslinjen -- räknade ur ärendena.

Jira har tre flikar GodJIRA saknade: Summary (KPI:er, statusdonut, aktivitet),
Development (DORA-siffror och kodverktyg) och Timeline (epics på en tidsaxel).
Siffrorna räknas här och inte i ritningen, av två skäl: de går att prova med kända
datum, och n8n kan läsa dem.

Det som faktiskt kan gå fel utan att någon märker det:
  * "klara senaste 7 dygn" räknas på updated -> varje rörelse i ett stängt ärende
                                räknas som att det blev klart (därför resolutionMs)
  * fönstret är öppet i fel ände  -> ärenden från framtiden räknas in
  * andelar summerar inte till 100 -> donuten och staplarna ljuger
  * epicens framdrift räknar fel barn -> framdriftsraden visar fel
  * stapel utanför 0-100 %        -> ritningen hamnar utanför rutan
"""
import time

DAY_MS = 86400000.0


def _now(now: float) -> float:
    return now or time.time() * 1000.0


def _share(count: int, total: int) -> float:
    return round(100.0 * count / total, 1) if total else 0.0


# Prioriteter har en ordning i sig: en fördelning skall läsas i samma ordning varje
# gång, så att staplarna går att jämföra. Typer har ingen, de sorteras på antal.
PRIORITY_ORDER = ("highest", "high", "medium", "low", "lowest")


def _bars(rows: list, total: int, order: tuple = ()) -> list:
    """Namn + antal + andel. Prioriteringar i sin egen ordning, resten störst först."""
    counts: dict = {}
    for row in rows:
        name = str(row or "").strip() or "utan"
        counts[name] = counts.get(name, 0) + 1
    if order:
        rank = {name: i for i, name in enumerate(order)}
        ordered = sorted(counts.items(),
                         key=lambda kv: (rank.get(kv[0].lower(), len(order)), kv[0].lower()))
    else:
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [{"name": name, "count": count, "share": _share(count, total)} for name, count in ordered]


def summary(issues: list, now: float = 0.0, days: int = 7) -> dict:
    """Siffrorna på Jiras Summary-flik, räknade ur ärendena själva."""
    now = _now(now)
    start = now - days * DAY_MS
    end = now + days * DAY_MS
    done = [i for i in issues if str(i.get("statusCategory") or "") == "done"]
    in_progress = [i for i in issues if str(i.get("statusCategory") or "") == "indeterminate"]
    todo = [i for i in issues if str(i.get("statusCategory") or "") not in ("done", "indeterminate")]
    recent = sorted((i for i in issues if i.get("updatedMs")),
                    key=lambda i: i.get("updatedMs") or 0, reverse=True)[:8]
    return {
        "days": days,
        "completed": sum(1 for i in issues if start <= (i.get("resolutionMs") or 0) <= now),
        "updated": sum(1 for i in issues if start <= (i.get("updatedMs") or 0) <= now),
        "created": sum(1 for i in issues if start <= (i.get("createdMs") or 0) <= now),
        "dueSoon": sum(1 for i in issues
                       if str(i.get("statusCategory") or "") != "done"
                       and now <= (i.get("dueMs") or 0) <= end),
        "status": {"total": len(issues), "done": len(done),
                   "inProgress": len(in_progress), "todo": len(todo),
                   "doneShare": _share(len(done), len(issues)),
                   "inProgressShare": _share(len(in_progress), len(issues)),
                   "todoShare": _share(len(todo), len(issues))},
        "priorities": _bars([i.get("priorityName") for i in issues], len(issues),
                            order=PRIORITY_ORDER),
        "types": _bars([i.get("typeName") for i in issues], len(issues)),
        "recent": [{"key": i.get("key") or "", "summary": i.get("summary") or "",
                    "who": i.get("assigneeName") or "", "updatedMs": i.get("updatedMs") or 0,
                    "statusName": i.get("statusName") or "",
                    "statusCategory": i.get("statusCategory") or "",
                    "url": i.get("url") or ""} for i in recent],
    }


def timeline(issues: list, sprints: list, now: float = 0.0) -> dict:
    """Gantt-läget: raderna, deras staplar i procent av spannet, och månaderna.

    En epic utan egna datum får sin stapel av barnens ytterkanter -- det är vad Jira
    gör, och annars står raden tom trots att arbetet har datum.
    """
    now = _now(now)
    children: dict = {}
    for issue in issues:
        parent = str(issue.get("parentKey") or "")
        if parent:
            children.setdefault(parent, []).append(issue)
    rows = []
    for issue in issues:
        key = str(issue.get("key") or "")
        kids = children.get(key) or []
        is_epic = str(issue.get("typeName") or "").strip().lower() == "epic"
        if issue.get("parentKey") and not is_epic:
            continue                      # ett barn ritas under sin epic, inte själv
        if not kids and not is_epic:
            continue                      # löst ärende utan barn hör inte på tidslinjen
        kid_done = sum(1 for k in kids if str(k.get("statusCategory") or "") == "done")
        starts = [issue.get("startMs") or 0] + [k.get("startMs") or 0 for k in kids]
        ends = [issue.get("dueMs") or 0] + [k.get("dueMs") or 0 for k in kids]
        start = min((s for s in starts if s), default=0)
        end = max((e for e in ends if e), default=0)
        rows.append({"key": key, "summary": issue.get("summary") or "",
                     "type": issue.get("typeName") or "", "statusName": issue.get("statusName") or "",
                     "done": str(issue.get("statusCategory") or "") == "done",
                     "children": len(kids), "childrenDone": kid_done,
                     "startMs": start, "endMs": end, "url": issue.get("url") or ""})
    sprints_out = [{"id": str(s.get("id") or ""), "name": s.get("name") or "",
                    "state": s.get("state") or "", "startMs": s.get("startMs") or 0,
                    "endMs": s.get("endMs") or 0}
                   for s in sorted(sprints, key=lambda s: s.get("startMs") or 0)
                   if s.get("startMs") and s.get("endMs")]
    edges = ([r["startMs"] for r in rows if r["startMs"]] + [r["endMs"] for r in rows if r["endMs"]]
             + [s["startMs"] for s in sprints_out] + [s["endMs"] for s in sprints_out] + [now])
    lo, hi = min(edges), max(edges)
    span = (hi - lo) or DAY_MS

    def place(value: float) -> float:
        return round(max(0.0, min(100.0, 100.0 * (value - lo) / span)), 2)

    for row in rows:
        if not (row["startMs"] or row["endMs"]):
            # Utan datum ritas ingen stapel: en punkt vid kanten ser ut som ett datum.
            row["left"], row["width"] = None, None
            continue
        row["left"] = place(row["startMs"] or row["endMs"] or lo)
        row["width"] = round(max(0.8, place(row["endMs"] or row["startMs"] or lo) - row["left"]), 2)
    for sprint in sprints_out:
        sprint["left"] = place(sprint["startMs"])
        sprint["width"] = round(max(0.8, place(sprint["endMs"]) - sprint["left"]), 2)
    months = []
    cursor = time.gmtime(lo / 1000.0)
    year, month = cursor.tm_year, cursor.tm_mon
    while True:
        first = time.mktime((year, month, 1, 0, 0, 0, 0, 0, -1)) * 1000.0
        month += 1
        if month > 12:
            year, month = year + 1, 1
        last = time.mktime((year, month, 1, 0, 0, 0, 0, 0, -1)) * 1000.0
        if first > hi:
            break
        months.append({"label": time.strftime("%b", time.gmtime(first / 1000.0)),
                       "left": place(first),
                       "width": round(max(0.5, place(min(last, hi)) - place(first)), 2)})
        if len(months) > 36:
            break
    dated = sum(1 for r in rows if r["startMs"] or r["endMs"])
    return {"rows": rows, "sprints": sprints_out, "months": months,
            "todayLeft": place(now), "spanDays": round(span / DAY_MS),
            "dated": dated,
            # Utan datum på ärendena finns bara sprintarna att rita. Det skall stå,
            # inte se ut som en tom ruta.
            "note": "" if dated else "Inga ärenden har start- eller slutdatum än — "
                                     "här syns därför bara sprintarna. Fyll i datumen i Jira "
                                     "(ärendet → fler fält) så hamnar de på tidslinjen."}


def development(jira: dict, github: dict, now: float = 0.0) -> dict:
    """Jiras Development-flik: vad koden och ärendena gör, och vad som inte går att se.

    Jira visar "Connect your code tools" tills GitHub kopplas dit. Här är kopplingen
    redan gjord, så repo-listan är verklig och PR:erna kommer ur GitHub -- men ledtid,
    cykeltid och driftsättningar kräver att Jira känner till kopplingen, och det gör
    den inte. Det står i notes i stället för att visas som en nolla.
    """
    now = _now(now)
    boards = (jira.get("boards") or [{}])
    issues = boards[0].get("issues") or []
    active_sprint = (boards[0].get("sprint") or {}).get("id") or ""
    bugs = [i for i in issues if str(i.get("typeName") or "").strip().lower() == "bug"]
    return {
        "workItems": sum(1 for i in issues if now - 7 * DAY_MS <= (i.get("resolutionMs") or 0) <= now),
        "openBugs": sum(1 for i in bugs if str(i.get("statusCategory") or "") != "done"),
        "overdue": sum(1 for i in issues if str(i.get("statusCategory") or "") != "done"
                       and (i.get("dueMs") or 0) and (i.get("dueMs") or 0) < now),
        "inSprint": sum(1 for i in issues if active_sprint and str(i.get("sprintId") or "") == active_sprint),
        "repos": [{"name": r.get("name") or "", "language": r.get("primaryLanguage") or "",
                   "stars": r.get("stargazerCount") or 0, "visibility": r.get("visibility") or "",
                   "updatedAt": r.get("updatedAt") or "", "url": r.get("url") or "",
                   "description": r.get("description") or ""}
                  for r in (github.get("repos") or [])],
        "pullRequests": [{"title": p.get("title") or "", "url": p.get("url") or "",
                          "repo": p.get("repo") or "", "author": p.get("author") or ""}
                         for p in (github.get("pullRequests") or [])],
        "notes": ["ledtid, cykeltid och driftsättningar kräver att GitHub kopplas till Jira "
                  "i Jiras eget gränssnitt",
                  "sårbarheter kräver en GitHub-nyckel med läsrätt på säkerhetsaviseringar"],
    }


def insights_read() -> dict:
    """De tre flikarna i ett svar. Räknat, inte ritat."""
    jira = jira_state()
    github = github_state()
    boards = jira.get("boards") or [{}]
    issues = boards[0].get("issues") or []
    sprints = boards[0].get("sprints") or []
    out = {"ok": True, "project": (boards[0].get("projectKey") or ""),
           "summary": summary(issues),
           "timeline": timeline(issues, sprints),
           "development": development(jira, github)}
    return out


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
                    "token": pool.submit(token_read),
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

    def _guard(self) -> bool:
        """Före varje svar: rätt värd, och rätt ursprung på skrivningar."""
        if not host_ok(self.headers.get("Host", "")):
            self._send(403, b"forbidden: wrong host", "text/plain; charset=utf-8")
            return False
        origin = self.headers.get("Origin")
        if origin and not host_ok(origin):
            self._send(403, b"forbidden: cross-site request", "text/plain; charset=utf-8")
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 -- http.server's own naming
        if not self._guard():
            return
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
        if path == "/api/insights":
            self._json(200, cached("insights", insights_read, ttl=180))
            return
        if path == "/api/admin":
            self._json(200, cached("admin", admin_read, ttl=180))
            return
        if path == "/api/setup":
            self._json(200, setup_read())
            return
        if path == "/api/token":
            self._json(200, token_read())
            return
        if path == "/api/strings":
            query = parse_qs(urlparse(self.path).query)
            want = (query.get("lang") or [""])[0][:8]
            self._json(200, cached("strings:" + want, lambda: panel_strings(want), ttl=300))
            return
        if path == "/api/scan":
            query = parse_qs(urlparse(self.path).query)
            self._json(200, scan_read((query.get("repo") or [""])[0]))
            return
        if path == "/api/flowmap":
            query = parse_qs(urlparse(self.path).query)
            wid = (query.get("flow") or [""])[0][:64]
            lang = (query.get("lang") or [""])[0][:8]
            found = flowmap_artifact(wid, lang) if (wid and flowmap_available()) else {"ok": False}
            if not found.get("ok"):
                self._send(404, b"flow map not available", "text/plain")
                return
            self._send(200, found["html"].read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/codemap":
            query = parse_qs(urlparse(self.path).query)
            name = (query.get("repo") or [""])[0]
            self._json(200, cached("codemap:" + name, lambda: codemap_read(name), ttl=120))
            return
        if path == "/api/graph":
            query = parse_qs(urlparse(self.path).query)
            name = (query.get("repo") or [""])[0]
            self._json(200, cached("graph:" + name, lambda: graph_read(name), ttl=120))
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
        if not self._guard():
            return
        handler = {"/api/import": import_parse, "/api/import/apply": import_apply,
                   "/api/link": link_set, "/api/automation": automation_report,
                   "/api/scan": scan_start, "/api/token": token_save,
                   "/api/github": github_save, "/api/language": language_save,
                   "/api/admin/people": admin_people}.get(self.path.split("?")[0])
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
    if BIND not in ("127.0.0.1", "::1", "localhost"):
        print("GodJIRA listens on {}:{} -- anyone who can reach that port reads the panel "
              "and can press its buttons. It is the same network your Jira data is on."
              .format(BIND, PORT), flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
