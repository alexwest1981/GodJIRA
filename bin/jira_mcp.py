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
      "args": ["/home/alex/.config/omarchy/plugins/custom.jira/bin/jira_mcp.py"]}}}

Självkontroll: python3 bin/jira_mcp.py --selftest
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "jira_bridge.py")
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


def run_bridge(argv, timeout=READ_TIMEOUT):
    """Returnerar (ok, payload). Bryggans JSON går före returkoden: den sätter
    ok:false med ett skäl i klartext, och det skälet är hela svaret."""
    try:
        done = subprocess.run([sys.executable, BRIDGE] + [str(a) for a in argv],
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
