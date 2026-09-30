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
        ok, data = wanted["run"](params.get("arguments") or {})
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

    err = rpc({"jsonrpc": "2.0", "id": 3, "method": "does/not/exist"})
    assert err["error"]["code"] == -32601, err

    call = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "jira_backlog", "arguments": {}}})["result"]
    assert call["isError"] is False, call
    body = json.loads(call["content"][0]["text"])
    assert body["count"] == len(body["backlog"]) > 0, body
    proc.stdin.close()
    proc.wait(timeout=10)
    print("OK: {} verktyg; backlog {} ärenden ur '{}' (projekt {})."
          .format(len(names), body["count"], body["board"], body["project"]))


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else serve()
