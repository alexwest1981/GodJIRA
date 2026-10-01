#!/usr/bin/env python3
"""GodJIRA över MCP: backloggen, tavlan och skrivningarna, åt en agent.

Inte en andra Jira-klient. Varje verktyg kör jira_bridge.py som underprocess —
bryggan äger inloggningen (nyckelringen), kvittensen efter varje skrivning och
näten (journal, papperskorg, kvotvakt). Därför finns det fortfarande exakt en
plats där en skrivning kan ske, och MCP-ytan kan inte glida ifrån pluginens eget
beteende.

Medvetet inte utlagda: delete, restore, configure, login, logout. En agent får
ingen nyckel till den förstörande delen eller till inloggningen.

Anslut i Antigravity IDE (~/.gemini/config/mcp_config.json) eller valfri annan
MCP-klient:

  {"mcpServers": {"godjira": {"command": "python3",
      "args": ["<path to this repo>/bin/jira_mcp.py"]}}}

Självkontroll: python3 bin/jira_mcp.py --selftest
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "jira_bridge.py")
# Flödet (välj det kritiska, sätt mig, In Progress) bor i jira_flow, som i sin tur
# använder bryggan för token när den finns. Ett verktyg till, ingen andra klient.
FLOW = os.environ.get("JIRA_FLOW", os.path.join(HERE, "jira_flow.py"))
PROTOCOL = "2024-11-05"
SERVER = {"name": "godjira", "version": "0.1.0"}
READ_TIMEOUT = 120
WRITE_TIMEOUT = 300

# Låset: en agent som kopplas mot GodJIRA för ett projekt ska inte kunna läsa eller
# röra ett annat. Projektet kommer från --project, eller från GODJIRA_MCP_PROJECT --
# den miljövariabeln sätter flödet när den startar en agent, så en MCP-koppling som
# redan finns i agentens egen config blir låst utan att någon behöver ändra den.
# Vakten sitter i handle(): varje verktygsanrop går genom den, så ingen enskild
# verktygsfunktion kan glömma bort den. Tomt lås = öppet, som förut.
LOCK = ""
KEY_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,9}-\d+\b", re.IGNORECASE)
# Fält som är prosa. En ärendenyckel som *nämns* i en kommentar är text, inte
# åtkomst; allt annat genomsöks. Hellre en vägran för mycket (ett okänt fält som
# råkar innehålla "UTF-8") än en för litet -- felet går att läsa och formulera om.
PROSE = ("text", "comment", "body", "summary", "description", "note")


def lock_from(argv):
    for index, item in enumerate(argv):
        if item in ("--project", "--lock") and index + 1 < len(argv):
            return argv[index + 1].strip().upper()
        if item.startswith("--project="):
            return item.split("=", 1)[1].strip().upper()
    return (os.environ.get("GODJIRA_MCP_PROJECT") or "").strip().upper()


def guard(wanted, args):
    """Släpp igenom, eller säg varför inte. Fyller i projektet när låset är satt.

    Två saker kontrolleras: projektargumentet, och varje ärendenyckel som råkar stå
    i ett annat projekt. Den andra är den som betyder något -- en agent kan mycket
    väl hitta på att fråga om en nyckel den sett någon annanstans.
    """
    if not LOCK:
        return None
    if "project" in ((wanted.get("schema") or {}).get("properties") or {}):
        asked = str(args.get("project") or "").strip().upper()
        if asked and asked != LOCK:
            return {"code": -32000, "message": "låst till {}: {} får inte fråga om {}".format(
                LOCK, wanted["name"], asked)}
        args["project"] = LOCK
    for field, value in args.items():
        if field in PROSE or not isinstance(value, str):
            continue
        for found in KEY_RE.findall(value):
            if found.split("-")[0].upper() != LOCK:
                return {"code": -32000, "message": "låst till {}: {} får inte röra {}".format(
                    LOCK, wanted["name"], found)}
    return None

# Fälten som betyder något för en agent. Ikon-URL:er och millisekunder kostar
# bara kontext. Ingenting hittas på: raderna är bryggans egna.
KEEP = ("key", "summary", "typeName", "statusName", "statusCategory",
        "assigneeName", "priorityName", "storyPoints", "sprintId", "sprintName",
        "parentKey", "parentSummary", "projectKey", "url")


def trim(rows):
    return [{k: row.get(k) for k in KEEP if k in row} for row in rows or []]


def run_json(argv, timeout=READ_TIMEOUT):
    """Returnerar (ok, payload) ur en underprocess som svarar JSON. Programmets eget
    svar går före returkoden: det sätter ok:false med ett skäl i klartext, och det
    skälet är hela svaret."""
    try:
        done = subprocess.run([sys.executable] + [str(a) for a in argv],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {"error": "bryggan svarade inte inom {}s".format(timeout)}
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return False, {"error": (done.stderr or done.stdout or "tomt svar").strip()[:2000]}
    if isinstance(data, dict) and data.get("ok") is False:
        return False, data
    return True, data


def run_bridge(argv, timeout=READ_TIMEOUT):
    return run_json([BRIDGE] + list(argv), timeout)


def board_of(snap, want):
    """Tavlan på id, projektnyckel eller namn; skiftläget spelar ingen roll."""
    boards = (snap or {}).get("boards") or []
    if not want:
        return boards[0] if boards else None
    wanted = str(want).strip().lower()
    for board in boards:
        if wanted in (str(board.get("id", "")).lower(),
                      str(board.get("projectKey", "")).lower(),
                      str(board.get("name", "")).lower()):
            return board
    for board in boards:
        if wanted and wanted in str(board.get("name", "")).lower():
            return board
    return None


def board_or_error(args):
    ok, snap = run_bridge(["snapshot"])
    if not ok:
        return None, snap
    board = board_of(snap, args.get("project"))
    if board is None:
        return None, {"error": "ingen tavla matchar {!r}".format(args.get("project")),
                      "boards": [b.get("name") for b in snap.get("boards") or []]}
    return board, None


# ---------------------------------------------------------------- verktygen

def t_status(args):
    return run_bridge(["status"])


def t_backlog(args):
    board, err = board_or_error(args)
    if err:
        return False, err
    rows = board.get("backlog") or []
    return True, {"board": board.get("name"), "project": board.get("projectKey"),
                  "count": len(rows), "backlog": trim(rows)}


def t_next(args):
    """Ta nästa kritiska ärende. skrivningen kvitteras och journalförs av bryggan."""
    if not os.path.exists(FLOW):
        return False, {"error": "jira_flow saknas: {}".format(FLOW),
                       "hint": "filen ligger i samma bin/ som bryggan; sätt JIRA_FLOW om den flyttats"}
    argv = [FLOW, "next", "--json"]
    if args.get("project"):
        argv += ["--project", args["project"]]
    if args.get("status"):
        argv += ["--status", args["status"]]
    if args.get("dryRun"):
        argv += ["--dry-run"]
    return run_json(argv)


def t_board(args):
    board, err = board_or_error(args)
    if err:
        return False, err
    return True, {"board": board.get("name"), "project": board.get("projectKey"),
                  "columns": board.get("columns") or [],
                  "sprints": [{k: s.get(k) for k in ("id", "name", "state")}
                              for s in board.get("sprints") or []],
                  "backlog": trim(board.get("backlog")),
                  "issues": trim(board.get("issues"))}


def t_transitions(args):
    return run_bridge(["transitions", args["key"]])


def t_move(args):
    return run_bridge(["move", args["key"], args["status"]], WRITE_TIMEOUT)


def t_comments(args):
    return run_bridge(["comments", args["key"]])


def t_comment(args):
    return run_bridge(["comment", args["key"], args["text"]], WRITE_TIMEOUT)


def t_create(args):
    board, err = board_or_error(args)
    if err:
        return False, err
    payload = {"summary": args["summary"], "typeName": args.get("type") or "Task"}
    if args.get("status"):
        payload["statusName"] = args["status"]
    ok, out = run_bridge(["create", board.get("id"), json.dumps(payload, ensure_ascii=False)],
                         WRITE_TIMEOUT)
    if ok:
        out = out if isinstance(out, dict) else {"result": out}
    return ok, out


def t_update(args):
    payload = {k: v for k, v in args.items() if k != "key" and v is not None}
    if not payload:
        return False, {"error": "inget fält att uppdatera"}
    return run_bridge(["update", args["key"], json.dumps(payload, ensure_ascii=False)],
                      WRITE_TIMEOUT)


def t_activity(args):
    return run_bridge(["activity", args["project"], args.get("limit") or 25])


def t_report(args):
    return run_bridge(["report", str(args["board"]), str(args["sprint"])])


def t_dev(args):
    return run_bridge(["dev", args["key"]])


def t_options(args):
    return run_bridge(["options", args["project"]])


def t_journal(args):
    return run_bridge(["journal", args.get("limit") or 50])


def t_attachments(args):
    key = args["key"]
    if args.get("download"):
        argv = ["download", key, str(args["download"])]
        if args.get("dir"):
            argv.append(str(args["dir"]))
        if args.get("force"):
            argv.append("--force")
        return run_bridge(argv, WRITE_TIMEOUT)
    return run_bridge(["attachments", key])


def t_attach(args):
    return run_bridge(["attach", args["key"], args["file"]], WRITE_TIMEOUT)


def t_links(args):
    return run_bridge(["links", args["key"]])


def t_link(args):
    return run_bridge(["link", args["key"], args["type"], args["other"]], WRITE_TIMEOUT)


def t_worklogs(args):
    return run_bridge(["worklogs", args["key"]])


def t_worklog_add(args):
    argv = ["log-work", args["key"], args["time"]]
    if args.get("comment"):
        argv += ["--comment", str(args["comment"])]
    if args.get("started"):
        argv += ["--started", str(args["started"])]
    return run_bridge(argv, WRITE_TIMEOUT)


def t_sprints(args):
    board, err = board_or_error(args)
    if err:
        return False, err
    return run_bridge(["sprints", str(board.get("id"))])


def t_sprint(args):
    action = (args.get("action") or "").strip().lower()
    if action == "create":
        board, err = board_or_error(args)
        if err:
            return False, err
        argv = ["sprint-create", str(board.get("id")), args["name"]]
        for flag, name in (("--start", "start"), ("--end", "end"), ("--goal", "goal")):
            if args.get(name):
                argv += [flag, str(args[name])]
        return run_bridge(argv, WRITE_TIMEOUT)
    if action == "add":
        keys = [str(k) for k in args.get("keys") or []]
        if not args.get("sprint") or not keys:
            return False, {"error": "add needs sprint and at least one key"}
        return run_bridge(["sprint-add", str(args["sprint"])] + keys, WRITE_TIMEOUT)
    if action in ("start", "close"):
        sprint = str(args.get("sprint") or "")
        if not sprint:
            return False, {"error": "{} needs sprint".format(action)}
        # Enkelriktat och på teamets tavla: agenten upprepar sprintens id, samma
        # tvåtrycks-regel som panelen och flödets övertagande använder.
        if str(args.get("confirm") or "") != sprint:
            return False, {"error": "confirm must repeat the sprint id '{}' — {} is one-way "
                                    "and the board is shared".format(sprint, action),
                           "hint": "read the sprint with jira_sprints first"}
        return run_bridge(["sprint-" + action, sprint, "--yes"], WRITE_TIMEOUT)
    return False, {"error": "action must be one of create, add, start, close"}


def t_versions(args):
    return run_bridge(["versions", args["project"]])


def t_version_create(args):
    return run_bridge(["version-create", args["project"], args["name"]], WRITE_TIMEOUT)


GRAPH_LIMIT = 12
GRAPH_TIMEOUT = 180


def graph_view(graph, node="", limit=GRAPH_LIMIT):
    """Grafen som ett svar en agent kan läsa: översikten först, en nod i taget sedan.

    Hela grafen är ~120 kB för AutoCore, så verktyget svarar med ingången i stället för
    filen: vad repot består av, vad allt lutar sig mot, och vad som ligger utanför kartan.
    Listor kapas men **talen är alltid de sanna** -- ett tak som inte syns är samma sak som
    ingen information. Ren funktion: den rör ingen fil, så selftestet kan prova den.
    """
    entities = list(graph.get("entities") or [])
    relations = list(graph.get("relations") or [])
    by_id = {e.get("id"): e for e in entities}
    if not entities:
        return {"ok": False, "error": "the graph is empty -- run `jira_flow scan` first"}

    if node:
        want = str(node).strip()
        hit = by_id.get(want)
        if hit is None:
            # Ett namn räcker om det är entydigt: en agent känner sällan till id-formen.
            same = [e for e in entities if want in (e.get("name"), e.get("full"))]
            if len(same) > 1:
                return {"ok": False, "error": "{} matches {} entities in the graph".format(
                    want, len(same)),
                    "candidates": [e.get("id") for e in sorted(same, key=lambda e: e.get("id"))[:limit]]}
            hit = same[0] if same else None
        if hit is None:
            return {"ok": False, "error": "no entity {!r} in the graph".format(want),
                    "hint": "call without node for the overview; ids look like fil:src/Foo.java"}

        def side(rows, key, other):
            groups = {}
            for row in rows:
                groups.setdefault(row.get("kind") or "?", []).append(
                    by_id.get(row.get(other), {}).get("name") or row.get(other))
            return {kind: {"count": len(names), "names": sorted(names)[:limit]}
                    for kind, names in sorted(groups.items())}

        entity = {k: hit.get(k) for k in ("id", "type", "name", "full", "package", "path",
                                          "summary", "status", "pool", "unclear")
                  if hit.get(k) is not None}
        entity["pointsAt"] = side([r for r in relations if r.get("from") == hit.get("id")], "from", "to")
        entity["pointedAtBy"] = side([r for r in relations if r.get("to") == hit.get("id")], "to", "from")
        return {"ok": True, "repo": graph.get("repo"), "project": graph.get("project"),
                "at": graph.get("at"), "entity": entity}

    files_in = {}
    for row in relations:
        if row.get("kind") == "ligger-i":
            files_in[row.get("to")] = files_in.get(row.get("to"), 0) + 1
    packages = sorted(({"id": e.get("id"), "name": e.get("name"),
                        "files": files_in.get(e.get("id"), 0)}
                       for e in entities if e.get("type") == "package"),
                      key=lambda p: (-p["files"], p["name"]))
    # Motorn svarar med TAL här ("74 filer ingen uppgift nämner"), inte med listor: namnen
    # bor i scanningen och panelens Karta-vy visar dem. Tål båda formerna -- grafen har bytt
    # form förut, och ett antagande om typen kastade det här verktyget första gången.
    def tally(value):
        return value if isinstance(value, int) else len(value or [])

    return {"ok": True, "repo": graph.get("repo"), "project": graph.get("project"),
            "at": graph.get("at"), "counts": graph.get("counts") or {},
            "hubs": (graph.get("hubs") or [])[:limit],
            "packages": packages[:limit], "packagesTotal": len(packages),
            "filesWithoutIssue": tally(graph.get("filesWithoutIssue")),
            "issuesWithoutFile": tally(graph.get("issuesWithoutFile")),
            "hint": "call again with node=<id> for one entity: fil:src/Foo.java, "
                    "paket:com.x.y, or the name of an issue"}


def t_graph(args):
    """Kodbasen som en graf: paket, filer och ärenden med sina relationer.

    Frågan går till motorn (samma token, samma länk, samma scanning som resten): `graph`
    bygger ur repot + scan-filen på ~0,4 s och skriver filen panelen läser.
    """
    argv = [FLOW, "graph", "--json"]
    if (args.get("repo") or "").strip():
        argv += ["--repo-name", str(args["repo"]).strip()]
    ok, data = run_json(argv, GRAPH_TIMEOUT)
    if not ok:
        return False, data
    shaped = graph_view(data, args.get("node") or "", int(args.get("limit") or GRAPH_LIMIT))
    if shaped.get("ok"):
        shaped["root"] = data.get("root")
        shaped["savedTo"] = data.get("savedTo")
    # Samma regel som run_json: motorns eget svar går före. Ett okänt nodnamn är ett nej
    # med kandidater -- inte ett tyst ja med en tom kropp.
    return bool(shaped.get("ok")), shaped


def tool(name, description, properties=None, required=(), run=None):
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return {"name": name, "description": description, "schema": schema, "run": run}


PROJECT = {"project": {"type": "string",
                       "description": "Tavlans id, projektnyckel (SCRUM) eller namn. Tomt = första tavlan."}}
KEY = {"key": {"type": "string", "description": "Ärendenyckel, t.ex. SCRUM-65"}}

TOOLS = [
    tool("jira_status", "Anslutningen: konto, sajt, läge (real/mock) och språk.",
         run=t_status),
    tool("jira_backlog", "Backloggen för en tavla: ärenden som inte ligger i en sprint. "
                         "Detta är varje ärende utan sprintId.", PROJECT, run=t_backlog),
    tool("jira_next", "Ta nästa kritiska ärende: högsta prioritet bland det som inte "
                      "påbörjats och är oassignerat eller mitt, till mig och till In Progress. "
                      "Flödet ägs av jira_flow (samma token via bryggan); svaret namnger ärendet "
                      "och de överhoppade. Använd dryRun för att bara se valet.",
         dict(PROJECT, status={"type": "string", "description": "Målstatus, t.ex. 'In Progress'"},
              dryRun={"type": "boolean", "description": "Visa valet, skriv inget"}),
         run=t_next),
    tool("jira_board", "Hela tavlan: kolumner, sprintar, backloggen och ärendena i sprint.",
         PROJECT, run=t_board),
    tool("jira_transitions", "Vilka statusbyten ärendet tillåter just nu.", KEY,
         ("key",), t_transitions),
    tool("jira_move", "Flytta ett ärende till en annan status. Kräver statusnamnet som "
                      "jira_transitions visar. Skrivningen kvitteras och journalförs.",
         dict(KEY, status={"type": "string", "description": "Statusnamn, t.ex. 'In Review'"}),
         ("key", "status"), t_move),
    tool("jira_comments", "Kommentarerna i ett ärende, äldst först.", KEY, ("key",), t_comments),
    tool("jira_comment", "Skriv en kommentar i ett ärende. Text, ingen formatering.",
         dict(KEY, text={"type": "string"}), ("key", "text"), t_comment),
    tool("jira_create", "Skapa ett ärende på tavlans projekt och sammanfattning krävs. "
                        "Beskrivning sätts i ett andra steg med jira_update (Jiras create-API "
                        "tar den inte här).",
         dict(PROJECT, summary={"type": "string"},
              type={"type": "string", "description": "Ärendetyp, t.ex. Task eller Subtask"},
              status={"type": "string", "description": "Kolumn att landa i, om bytet tillåts"}),
         ("project", "summary"), t_create),
    tool("jira_update", "Ändra fält på ett ärende. Bara de fält du skickar rörs; den gamla "
                        "texten läggs i papperskorgen först.",
         dict(KEY, summary={"type": "string"}, description={"type": "string"},
              priorityName={"type": "string"}, storyPoints={"type": "number"},
              dueDate={"type": "string", "description": "YYYY-MM-DD"},
              assigneeAccountId={"type": "string"}),
         ("key",), t_update),
    tool("jira_activity", "Senast uppdaterade ärenden i projektet, nyast först.",
         {"project": {"type": "string"}, "limit": {"type": "integer"}},
         ("project",), t_activity),
    tool("jira_report", "Sprintrapport för en tavla och en sprint.",
         {"board": {"type": "string"}, "sprint": {"type": "string"}},
         ("board", "sprint"), t_report),
    tool("jira_dev", "Utvecklingsstatus för ett ärende (grenar, commits, PR:er).",
         KEY, ("key",), t_dev),
    tool("jira_options", "Vad redigeringsformuläret kan erbjuda: personer, prioriteringar, "
                         "ärendetyper.", {"project": {"type": "string"}}, ("project",), t_options),
    tool("jira_attachments", "Bilagorna på ett ärende. Med download (bilagans id eller "
                             "filnamn) sparas filen i stället och sökvägen svaras.",
         dict(KEY, download={"type": "string", "description": "Bilagans id eller filnamn"},
              dir={"type": "string", "description": "Mapp att spara i (annars ~/Downloads)"},
              force={"type": "boolean", "description": "Skriv över en fil som redan finns"}),
         ("key",), t_attachments),
    tool("jira_attach", "Ladda upp en lokal fil till ett ärende. Filen läses tillbaka med "
                        "sin storlek efter skrivningen.",
         dict(KEY, file={"type": "string", "description": "Sökväg till filen"}),
         ("key", "file"), t_attach),
    tool("jira_links", "Ärendets länkar, plus länktyperna sajten erbjuder.",
         KEY, ("key",), t_links),
    tool("jira_link", "Länka två ärenden; key blir den utgående sidan. Typen är en av dem "
                      "jira_links visar.",
         dict(KEY, type={"type": "string", "description": "Länktyp, t.ex. Relates"},
              other={"type": "string", "description": "Det andra ärendets nyckel"}),
         ("key", "type", "other"), t_link),
    tool("jira_worklogs", "Tiden som loggats på ett ärende.", KEY, ("key",), t_worklogs),
    tool("jira_worklog_add", "Logga tid på ett ärende. Tiden skrivs som Jira skriver den "
                             "(\"10m\", \"1h 30m\", \"2d\").",
         dict(KEY, time={"type": "string"}, comment={"type": "string"},
              started={"type": "string", "description": "YYYY-MM-DD eller Jiras tidsstämpel"}),
         ("key", "time"), t_worklog_add),
    tool("jira_sprints", "Tavlans sprintar och deras läge (future/active/closed).",
         PROJECT, run=t_sprints),
    tool("jira_sprint", "Skapa, fylla, starta eller avsluta en sprint. action är create, "
                        "add, start eller close. start och close är enkelriktade: sätt "
                        "confirm till sprintens id så som jira_sprints visar det.",
         dict(PROJECT, action={"type": "string"}, name={"type": "string"},
              sprint={"type": "string"}, confirm={"type": "string"},
              keys={"type": "array", "items": {"type": "string"}},
              start={"type": "string"}, end={"type": "string"}, goal={"type": "string"}),
         ("action",), t_sprint),
    tool("jira_versions", "Projektets versioner.", {"project": {"type": "string"}},
         ("project",), t_versions),
    tool("jira_version_create", "Skapa en version på projektet.",
         {"project": {"type": "string"}, "name": {"type": "string"}},
         ("project", "name"), t_version_create),
    tool("jira_journal", "Pluginens egen journal: varje skrivning med tid, nyckel och utfall. "
                         "Här ser du vad agenten (eller panelen) gjort.",
         {"limit": {"type": "integer"}}, run=t_journal),
    tool("code_graph", "Kodbasen som en graf: paket, filer och ärenden med sina relationer. "
                       "Utan node: översikten -- vad repot består av, vad allt lutar sig mot "
                       "(hubs), och vad som ligger utanför kartan (filer ingen uppgift nämner, "
                       "uppgifter som inte nämner någon fil). Med node: en enhet i taget och "
                       "vad den pekar på respektive pekas på av. Byggd ur repot + scanningen, "
                       "ingen Jira-trafik: använd den för att förstå koden innan du ändrar den.",
         {"repo": {"type": "string",
                   "description": "Repots namn (t.ex. alexwest1981/AutoCore). Tomt = länkens repo."},
          "node": {"type": "string",
                   "description": "En enhet: fil:src/Foo.java, paket:com.x.y, eller namnet på "
                                  "en fil/en uppgift. Tomt = översikten."},
          "limit": {"type": "integer", "description": "Hur många namn per lista (12)."}},
         run=t_graph),
]


# ------------------------------------------------------------------ servern

def reply(mid, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(message):
    method = message.get("method") or ""
    mid = message.get("id")
    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion") or PROTOCOL
        reply(mid, {"protocolVersion": asked, "capabilities": {"tools": {}},
                    "serverInfo": SERVER})
    elif method.startswith("notifications/"):
        return
    elif method == "ping":
        reply(mid, {})
    elif method == "tools/list":
        reply(mid, {"tools": [{"name": t["name"], "description": t["description"],
                               "inputSchema": t["schema"]} for t in TOOLS]})
    elif method == "tools/call":
        params = message.get("params") or {}
        wanted = next((t for t in TOOLS if t["name"] == params.get("name")), None)
        if wanted is None:
            reply(mid, error={"code": -32602,
                              "message": "okänt verktyg: {}".format(params.get("name"))})
            return
        arguments = params.get("arguments") or {}
        blocked = guard(wanted, arguments)
        if blocked:
            reply(mid, error=blocked)
            return
        ok, data = wanted["run"](arguments)
        reply(mid, {"content": [{"type": "text",
                                 "text": json.dumps(data, ensure_ascii=False, indent=1)}],
                    "isError": not ok})
    elif mid is not None:
        reply(mid, error={"code": -32601, "message": "okänd metod: {}".format(method)})


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        handle(message)


# --------------------------------------------------------------- självkontroll

def selftest():
    assert board_of({"boards": [{"id": "1", "projectKey": "SCRUM", "name": "SCRUM board"}]},
                    "scrum")["id"] == "1"
    assert board_of({"boards": [{"id": "7", "projectKey": "WEB", "name": "Webbtavlan"}]},
                    None)["id"] == "7"
    assert board_of({"boards": []}, "scrum") is None
    assert trim([{"key": "SCRUM-1", "summary": "x", "typeIcon": "http://stor"}]) == \
        [{"key": "SCRUM-1", "summary": "x"}], "ikon-URL ska bort"

    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def rpc(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": PROTOCOL}})["result"]
    assert init["serverInfo"]["name"] == "godjira", init
    assert init["capabilities"]["tools"] == {}
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()

    names = {t["name"] for t in rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
             ["result"]["tools"]}
    assert "jira_backlog" in names and "jira_move" in names, names
    for gone in ("jira_delete", "jira_restore", "jira_login", "jira_logout"):
        assert gone not in names, "{} ska inte ligga ute".format(gone)

    assert "jira_next" in names, names
    for added in ("jira_attachments", "jira_attach", "jira_links", "jira_link",
                  "jira_worklogs", "jira_worklog_add", "jira_sprints", "jira_sprint",
                  "jira_versions", "jira_version_create"):
        assert added in names, "{} saknas".format(added)
    assert "code_graph" in names, names
    peek = rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "jira_next", "arguments": {"dryRun": True}}})["result"]
    assert peek["isError"] is False, peek
    seen = json.loads(peek["content"][0]["text"])
    assert seen.get("dryRun") is True and seen.get("wouldTake", {}).get("key"), seen

    st = rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": "jira_status", "arguments": {}}})["result"]
    mode = json.loads(st["content"][0]["text"]).get("mode")
    if mode == "real":
        sp = rpc({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                  "params": {"name": "jira_sprints", "arguments": {}}})["result"]
        assert sp["isError"] is False, sp
        board = json.loads(sp["content"][0]["text"])
        assert board["sprints"] and board["sprints"][0]["state"], board
    else:
        print("mock-läge: sprint-provet hoppas över")

    # Kodgrafen: dörren agenten går genom. Översikten får kosta ett riktigt anrop, men
    # den skall svara med siffror -- eller med motorns eget skäl (ingen scanning än).
    cg = rpc({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "code_graph", "arguments": {}}})["result"]
    assert cg["isError"] is False, cg
    seen = json.loads(cg["content"][0]["text"])
    assert seen.get("ok") is True or seen.get("error"), seen
    if seen.get("ok"):
        assert seen["counts"] and seen["packages"], seen
    else:
        print("kodgrafen: {}".format(seen["error"]))

    # Formningen prövas ren, på en handgjord graf: det är logiken som skall hålla, och
    # den får inte hänga på att maskinen råkar ha en scanning för tillfället.
    liten = {
        "repo": "x/y", "project": "P", "counts": {"paket": 2, "filer": 2, "ärenden": 1},
        "hubs": [{"package": "b", "usedBy": 1}],
        "filesWithoutIssue": 1,
        "issuesWithoutFile": 1,
        "entities": [
            {"id": "paket:b", "type": "package", "name": "b"},
            {"id": "paket:a", "type": "package", "name": "a"},
            {"id": "fil:a/Foo.java", "type": "file", "name": "Foo.java", "package": "a"},
            {"id": "fil:a/Enslig.java", "type": "file", "name": "Enslig.java", "package": "a"},
            {"id": "fil:b/Foo.java", "type": "file", "name": "Foo.java", "package": "b"},
            {"id": "ärende:P-1", "type": "issue", "name": "P-1", "summary": "något"},
        ],
        "relations": [
            {"from": "fil:a/Foo.java", "to": "paket:a", "kind": "ligger-i"},
            {"from": "fil:a/Enslig.java", "to": "paket:a", "kind": "ligger-i"},
            {"from": "ärende:P-1", "to": "fil:a/Foo.java", "kind": "nämner"},
            {"from": "paket:a", "to": "paket:b", "kind": "använder"},
        ],
    }
    over = graph_view(liten)
    assert over["ok"] and over["packagesTotal"] == 2, over
    assert over["packages"][0] == {"id": "paket:a", "name": "a", "files": 2}, over["packages"]
    assert over["filesWithoutIssue"] == 1 and over["issuesWithoutFile"] == 1, over
    # Tål den gamla formen (lista) också -- det var den som kastade verktyget.
    assert graph_view(dict(liten, filesWithoutIssue=["a/Enslig.java"]))["filesWithoutIssue"] == 1
    en = graph_view(liten, "fil:a/Foo.java")
    assert en["entity"]["pointsAt"]["ligger-i"]["names"] == ["a"], en
    assert en["entity"]["pointedAtBy"]["nämner"]["names"] == ["P-1"], en
    # Ett namn räcker om det är entydigt, och två träffar ger kandidater i stället för ett val.
    assert graph_view(liten, "Enslig.java")["entity"]["id"] == "fil:a/Enslig.java"
    # Två filer med samma namn är vanligt: namnet räcker inte, kandidaterna skall med.
    dubbel = graph_view(liten, "Foo.java")
    assert dubbel["ok"] is False and sorted(dubbel["candidates"]) == [
        "fil:a/Foo.java", "fil:b/Foo.java"], dubbel
    assert graph_view(liten, "finns-inte")["ok"] is False
    assert graph_view({"entities": [], "relations": []})["ok"] is False

    err = rpc({"jsonrpc": "2.0", "id": 3, "method": "does/not/exist"})
    assert err["error"]["code"] == -32601, err

    call = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "jira_backlog", "arguments": {}}})["result"]
    assert call["isError"] is False, call
    body = json.loads(call["content"][0]["text"])
    assert body["count"] == len(body["backlog"]) > 0, body
    proc.stdin.close()

    # Låset: vakten prövas direkt, utan nät, och en gång över protokollet -- att
    # argv når fram till den körande servern är det som annars går sönder tyst.
    global LOCK
    os.environ.pop("GODJIRA_MCP_PROJECT", None)   # annars ärver servern ovan ett lås
    assert lock_from(["--project", "scrum"]) == "SCRUM"
    assert lock_from(["--project=web"]) == "WEB"
    assert lock_from(["--lock", "OTHER"]) == "OTHER"
    assert lock_from([]) == ""
    kept, LOCK = LOCK, "SCRUM"
    try:
        args = {"dryRun": True}
        assert guard({"name": "jira_next", "schema": {"properties": PROJECT}}, args) is None
        assert args["project"] == "SCRUM", "projektet fylls i när låset är satt"
        blocked = guard({"name": "jira_next", "schema": {"properties": PROJECT}},
                        {"project": "WEB"})
        assert blocked and "WEB" in blocked["message"], blocked
        blocked = guard({"name": "jira_comments", "schema": {"properties": KEY}},
                        {"key": "WEB-7"})
        assert blocked and "WEB-7" in blocked["message"], blocked
        assert guard({"name": "jira_comments", "schema": {"properties": KEY}},
                     {"key": "WEB-7".lower()}) is not None, "gemener ska inte slinka igenom"
        assert guard({"name": "jira_comment", "schema": {"properties": KEY}},
                     {"key": "SCRUM-7", "text": "handlar om WEB-12 och UTF-8"}) is None, \
            "en nyckel i ett fritextfält är text, inte åtkomst"
        assert guard({"name": "jira_comments", "schema": {"properties": KEY}},
                     {"key": "SCRUM-7", "text": "om SCRUM-7"}) is None
        assert guard({"name": "jira_status", "schema": {"properties": {}}}, {}) is None
    finally:
        LOCK = kept

    locked = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--project", "OTHER"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def lrpc(obj):
        locked.stdin.write(json.dumps(obj) + "\n")
        locked.stdin.flush()
        return json.loads(locked.stdout.readline())

    lrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL}})
    locked.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    locked.stdin.flush()
    denied = lrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "jira_comments", "arguments": {"key": "SCRUM-1"}}})
    assert denied.get("error") and "SCRUM-1" in denied["error"]["message"], denied
    allowed = lrpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "jira_status", "arguments": {}}})
    assert "isError" in allowed.get("result", {}), allowed
    locked.stdin.close()
    locked.wait(timeout=10)
    proc.wait(timeout=10)
    print("OK: {} verktyg; backlog {} ärenden ur '{}' (projekt {})."
          .format(len(names), body["count"], body["board"], body["project"]))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        LOCK = lock_from(sys.argv)
        serve()
