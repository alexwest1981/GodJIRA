#!/usr/bin/env python3
"""jira_flow -- take the next critical Jira item, and keep commits linked to it.

One small CLI instead of one plugin per editor: VS Code and the Antigravity IDE
run it as a task, IntelliJ as an external tool, and git calls it from a
prepare-commit-msg hook. `jira_flow install <repo>` writes all four.

Commands
    login | logout
        Keep the token in the machine's own store: DPAPI on Windows, the login
        keychain on macOS, read from stdin so it stays out of the shell history.
        On Linux this declines -- the Omarchy bridge or the 0600 config file
        already owns it there.
    next [--project KEY] [--status "In Progress"] [--dry-run] [--json] [--expect KEY]
        Take the most critical not-started item (shared tasks first), assign it to
        you, move it to In Progress. With nothing of your own it proposes the most
        critical item that is someone else's and takes it only when the caller
        presses again with --expect KEY, naming that exact issue.
    plan [--text TEXT | --file PATH] [--create] [--json] [--project KEY]
        Hands the customer's wish to the agent you have chosen (JIRA_FLOW_AGENT,
        "claude -p" by default -- any CLI that reads a prompt on stdin and answers
        with JSON works) and gets issue proposals back. GodJIRA never calls a model
        itself: no key, no model list, no bill. Nothing is written until --create,
        and then exactly the list you just read.
    current
        The key of the item you are on right now (the commit hook reads this).
    install [repo] [--project KEY]
        Write the editor shims and the commit hook into a repository.
    --selftest
        Offline checks of the pure logic (no network, no credential).

Credentials, two ways, picked automatically:
    * the Omarchy Jira bridge, if it is installed (keyring token, action log),
    * otherwise JIRA_SITE, JIRA_EMAIL, JIRA_TOKEN in the environment or in
      ~/.config/jira-flow/config.json (chmod 600).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

SELF = Path(__file__).resolve()
BRIDGE_DIR = Path(
    os.environ.get("JIRA_BRIDGE_DIR", str(Path.home() / ".config/omarchy/plugins/custom.jira/bin"))
)
CONFIG_FILE = Path.home() / ".config/jira-flow/config.json"
LOG_FILE = Path.home() / ".local/state/jira-flow/actions.log"
DEFAULT_PROJECT = os.environ.get("JIRA_FLOW_PROJECT", "SCRUM")
DEFAULT_STATUS = os.environ.get("JIRA_FLOW_STATUS", "In Progress")

# Kundönskemålet blir ärenden genom *din* agent, inte genom en modell som GodJIRA
# ringer. Ingen nyckel, ingen modellista, ingen räkning att hålla reda på: den CLI
# du redan valt läser prompten på stdin och svarar med ärendena som JSON.
AGENT = os.environ.get("JIRA_FLOW_AGENT", "claude -p")
AGENT_TIMEOUT = int(os.environ.get("JIRA_FLOW_AGENT_TIMEOUT", "600"))
PLAN_MAX = int(os.environ.get("JIRA_FLOW_PLAN_MAX", "10"))

PLAN_PROMPT = """\
You are a scrum master. Split the customer's wish below into Jira issues for project {project}.

Only what the wish actually asks for. Do not invent scope, do not add epics, at most {limit} issues.
Issue types that exist in this project: {types}.

Answer with ONLY a JSON array -- no prose, no code fences, no explanation:
[{{"summary": "<short imperative, at most 80 characters>", "type": "<one of the types above>", \
"description": "<what to build, and how to know it is done>", "priority": "<Highest|High|Medium|Low>"}}]

The customer's wish:
{wish}
"""


# -------------------------------------------------------------- pure logic

SHARED_PREFIX = "GEMENSAMT"


def pick_jql(project: str, scope: str = "mine") -> str:
    """Not-started work, most critical first.

    ``statusCategory = "To Do"`` rather than a status name: the workflow's names
    are the site's business, the category is not. ``scope="mine"`` keeps the
    work that is nobody's or already mine; ``scope="any"`` drops that clause, and
    is the pool you may take from with a second press (see ``--expect``).
    """
    claim = "AND (assignee IS EMPTY OR assignee = currentUser()) " if scope == "mine" else ""
    return (
        'project = {} AND statusCategory = "To Do" {}'
        "ORDER BY priority DESC, created ASC".format(project, claim)
    )


def is_shared(issue) -> bool:
    """A shared task: the summary is marked GEMENSAMT (how the group marks them)."""
    summary = ((issue.get("fields") or {}).get("summary") or "").strip()
    return summary.upper().startswith(SHARED_PREFIX)


def shared_first(issues):
    """Shared tasks before personal ones, each group keeping the query's order.

    ``sorted`` is stable, so the priority-then-oldest order Jira returned stands
    within a group -- no second comparison to get wrong.
    """
    return sorted(issues or [], key=lambda issue: 0 if is_shared(issue) else 1)


def current_jql(project: str) -> str:
    return (
        'project = {} AND assignee = currentUser() AND statusCategory = "In Progress" '
        "ORDER BY updated DESC".format(project)
    )


def key_in(text: str) -> str:
    """The first Jira key in a string, e.g. a branch name or a commit subject."""
    found = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", text or "")
    return found.group(1) if found else ""


def prefix_subject(message: str, key: str) -> str:
    """A commit subject that names its issue, or the message unchanged.

    Jira's GitHub integration links on the key being present in the message, so
    naming it in the subject is enough -- no trailer, no marker syntax.
    """
    if not key or not message.strip():
        return message
    if key in message:
        return message
    lines = message.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    lines[0] = "{}: {}".format(key, lines[0].strip())
    body = "\n".join(lines)
    return body + ("\n" if message.endswith("\n") and not body.endswith("\n") else "")


def row(issue) -> dict:
    """The fields that matter, for a human line or for an agent's JSON."""
    fields = issue.get("fields") or {}
    return {
        "key": issue.get("key"),
        "summary": fields.get("summary") or "",
        "priority": (fields.get("priority") or {}).get("name") or "",
        "status": (fields.get("status") or {}).get("name") or "",
        "assignee": (fields.get("assignee") or {}).get("displayName") or "",
    }


def describe(issue) -> str:
    it = row(issue)
    return "{}  {}  [{}]  {}".format(it["key"], it["priority"] or "-", it["status"] or "-",
                                    it["summary"][:70])


# ---------------------------------------------------------------- clients

class Http:
    """Plain Jira Cloud REST v3 over stdlib urllib. This is what a clone uses."""

    def __init__(self, site: str, email: str, token: str):
        self.base = site.rstrip("/")
        if not self.base.startswith("http"):
            self.base = "https://" + self.base
        self.auth = b64encode("{}:{}".format(email, token).encode()).decode()

    def request(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", "Basic " + self.auth)
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise SystemExit("jira_flow: {} {} -> {} {}\n{}".format(
                method, path.split("?")[0], exc.code, exc.reason, exc.read().decode()[:400]))

    def get(self, path: str):
        return self.request("GET", path)

    def myself(self) -> dict:
        me = self.get("/rest/api/3/myself") or {}
        return {"accountId": me.get("accountId"), "displayName": me.get("displayName")}

    def assign(self, key: str, account_id: str) -> None:
        self.request("PUT", "/rest/api/3/issue/{}".format(key),
                     {"fields": {"assignee": {"accountId": account_id}}})

    def move(self, key: str, status: str) -> None:
        found = self.get("/rest/api/3/issue/{}/transitions".format(key)) or {}
        transitions = found.get("transitions", [])
        for tr in transitions:
            if (tr.get("to") or {}).get("name") == status or str(tr.get("id")) == str(status):
                self.request("POST", "/rest/api/3/issue/{}/transitions".format(key),
                             {"transition": {"id": tr["id"]}})
                return
        offered = ", ".join((t.get("to") or {}).get("name", "?") for t in transitions)
        raise SystemExit("jira_flow: {} cannot move to {!r} (offered: {})".format(
            key, status, offered or "none"))

    def board(self, project: str) -> dict:
        found = self.get("/rest/agile/1.0/board?projectKeyOrId={}".format(project)) or {}
        boards = found.get("values") or []
        return boards[0] if boards else {}

    def types(self, project: str):
        try:
            found = self.get("/rest/api/3/issue/createmeta/{}/issuetypes".format(project)) or {}
        except SystemExit:
            return []
        return [str(t.get("name", "")) for t in (found.get("issueTypes") or found.get("values") or [])]

    def create(self, board: dict, item: dict) -> dict:
        fields = {"project": {"key": (board or {}).get("projectKey") or DEFAULT_PROJECT},
                  "summary": item["summary"], "issuetype": {"name": item.get("type") or "Task"}}
        if item.get("description"):
            fields["description"] = {"type": "doc", "version": 1, "content": [
                {"type": "paragraph",
                 "content": [{"type": "text", "text": item["description"]}]}]}
        return self.request("POST", "/rest/api/3/issue", {"fields": fields})

    def log(self, action: str, key: str, detail: str = "", ok: bool = True) -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.parent.chmod(0o700)
        with LOG_FILE.open("a") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "action": action, "key": key, "detail": detail, "ok": ok},
                                ensure_ascii=False) + "\n")
        LOG_FILE.chmod(0o600)


class Bridge:
    """The Omarchy Jira bridge: the same token in the keyring, plus its action log."""

    def __init__(self, module, cfg):
        self.jb, self.cfg = module, cfg

    def get(self, path: str):
        return self.jb.jira_get(self.cfg, path)

    def myself(self) -> dict:
        me = self.jb.jira_get(self.cfg, "/rest/api/3/myself") or {}
        return {"accountId": me.get("accountId"), "displayName": me.get("displayName")}

    def assign(self, key: str, account_id: str) -> None:
        self.jb.real_update(self.cfg, key, {"assigneeAccountId": account_id})

    def move(self, key: str, status: str) -> None:
        self.jb.real_move(self.cfg, key, status)

    def log(self, action: str, key: str, detail: str = "", ok: bool = True) -> None:
        self.jb.log_action(self.cfg, action, key, detail=detail, ok=ok)

    def board(self, project: str) -> dict:
        """Tavlan på projektnyckel, samma väg som panelen och MCP:n går."""
        snap = self.jb.real_snapshot(self.cfg) or {}
        for board in snap.get("boards") or []:
            if str(board.get("projectKey", "")).upper() == str(project).upper():
                return board
        boards = snap.get("boards") or []
        return boards[0] if boards else {}

    def types(self, project: str):
        return [str(r.get("name", "")) for r in
                (self.jb.real_issue_types(self.cfg, project) or []) if r.get("name")]

    def create(self, board: dict, item: dict) -> dict:
        return self.jb.real_create(self.cfg, board, dict(item, typeName=item.get("type", "Task")))


def client():
    """The bridge when it is installed, otherwise a plain HTTP client."""
    if BRIDGE_DIR.is_dir() and str(BRIDGE_DIR) not in sys.path:
        sys.path.insert(0, str(BRIDGE_DIR))
    try:
        import jira_bridge  # noqa: PLC0415 -- the local import is the point

        return Bridge(jira_bridge, jira_bridge.load_config())
    except ImportError:
        pass

    import jira_secrets  # bara den här vägen behöver den; bryggan sköter token annars

    config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    site = os.environ.get("JIRA_SITE") or config.get("site") or ""
    email = os.environ.get("JIRA_EMAIL") or config.get("email") or ""
    try:
        stored = jira_secrets.store_for().read()
    except RuntimeError as exc:
        raise SystemExit("jira_flow: {}".format(exc))
    token = os.environ.get("JIRA_TOKEN") or stored or config.get("token") or ""
    missing = [name for name, value in
               (("JIRA_SITE", site), ("JIRA_EMAIL", email), ("JIRA_TOKEN", token)) if not value]
    if missing:
        raise SystemExit(
            "jira_flow: no credentials. Install the Omarchy Jira bridge, or set {} "
            "(environment, or {}).\n"
            "Create a token at https://id.atlassian.com/manage-profile/security/api-tokens".format(
                ", ".join(missing), CONFIG_FILE))
    return Http(site, email, token)


# --------------------------------------------------------------- commands

def find(client_, jql: str, limit: int = 4):
    path = "/rest/api/3/search/jql?jql={}&maxResults={}&fields=summary,status,assignee,priority".format(
        urllib.parse.quote(jql), limit)
    return (client_.get(path) or {}).get("issues", [])


def fetch_one(client_, key: str):
    """One not-started item by key, or None if it moved on. The second press takes
    exactly what was named, never whatever happens to be on top now."""
    issue = client_.get(
        "/rest/api/3/issue/{}?fields=summary,status,assignee,priority".format(key)) or {}
    category = ((issue.get("fields") or {}).get("status") or {}).get("statusCategory") or {}
    if category.get("key") != "new":
        return None
    return issue


def say(args, payload: dict, lines) -> None:
    """One payload, two faces: an agent reads the JSON, a human reads the lines."""
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in lines:
            print(line)


def cmd_next(client_, args) -> int:
    me = client_.myself()
    if not me.get("accountId"):
        say(args, {"ok": False, "error": "could not read your account from the site"},
            ["jira_flow: could not read your account from the site."])
        return 2

    if args.expect:
        # Andra trycket: exakt det ärende som knappen namngav. Är det redan påbörjat
        # eller borta är svaret nej, inte "här är en annan".
        chosen = fetch_one(client_, args.expect)
        if chosen is None:
            message = "{} is no longer not-started; nothing was taken.".format(args.expect)
            say(args, {"ok": False, "error": message, "expect": args.expect}, [message])
            return 2
        rest = shared_first(find(client_, pick_jql(args.project)))
        pool = [chosen] + [i for i in rest if i["key"] != chosen["key"]]
        chosen, rest = pool[0], pool[1:]
    else:
        pool = shared_first(find(client_, pick_jql(args.project), limit=10))
        if pool:
            chosen, rest = pool[0], pool[1:]
        else:
            # Inget att ta: föreslå den mest kritiska uppgiften som är någon annans,
            # och gör ingenting förrän ett andra tryck bekräftar just den nyckeln.
            others = shared_first(find(client_, pick_jql(args.project, "any"), limit=10))
            if not others:
                message = "Nothing to take: nothing not-started in {}.".format(args.project)
                say(args, {"ok": False, "error": message, "project": args.project}, [message])
                return 1
            top = others[0]
            message = "Nothing of your own; {} is the most critical. Press again to take it over.".format(
                top["key"])
            say(args, {"ok": False, "error": message, "proposal": row(top),
                       "requiresConfirmation": True},
                [message,
                 "  {}".format(describe(top))])
            return 3
    key = chosen["key"]
    skipped = [row(issue) for issue in rest]

    if args.dry_run:
        say(args,
            {"ok": True, "dryRun": True, "wouldTake": row(chosen), "status": args.status,
             "assignTo": me.get("displayName"), "skipped": skipped},
            ["would take: {}".format(describe(chosen)),
             "            assign to {} and move to {}".format(me.get("displayName"), args.status)])
    else:
        client_.assign(key, me["accountId"])
        try:
            client_.move(key, args.status)
        except SystemExit as exc:
            client_.log("flow-next", key, "assigned only: {}".format(exc), ok=False)
            say(args, {"ok": False, "error": str(exc), "took": row(chosen), "moved": False},
                ["jira_flow: assigned {} but could not move it: {}".format(key, exc)])
            return 2

        # Read it back: a write is done when the site says it is, never when the
        # call returned. A mismatch is an error, not a success.
        after = client_.get(
            "/rest/api/3/issue/{}?fields=summary,status,assignee,priority".format(key)) or {}
        fields = after.get("fields") or {}
        ok = ((fields.get("assignee") or {}).get("accountId") == me["accountId"]
              and (fields.get("status") or {}).get("name") == args.status)
        client_.log("flow-next", key, "assign + {}".format(args.status), ok=ok)
        if not ok:
            say(args, {"ok": False, "error": "the site did not keep the write", "took": row(after)},
                ["jira_flow: wrote {}, the site reads back:".format(key),
                 "          " + describe(after)])
            return 2
        say(args, {"ok": True, "took": row(after), "skipped": skipped},
            ["took: {}".format(describe(after))])

    if not args.json:
        for issue in rest:
            print("skipped: {}".format(describe(issue)))
    return 0


def parse_plan(text: str):
    """Ärendena ur agentens svar. Hel array eller inget: en halv lista blir aldrig
    några ärenden, och skräp ger fel i stället för halvskrivna tavlor."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*|```$", "", body).strip()
    match = re.search(r"\[\s*\{.*\}\s*\]", body, re.S)
    if not match:
        raise ValueError("the agent answered without a JSON array of issues")
    try:
        items = json.loads(match.group(0))
    except ValueError as exc:
        raise ValueError("the agent's JSON does not parse: {}".format(exc))
    out = []
    for item in items:
        summary = str((item or {}).get("summary") or "").strip()
        if not summary:
            raise ValueError("an issue came back without a summary")
        out.append({"summary": summary[:250],
                    "type": str(item.get("type") or "Task").strip() or "Task",
                    "description": str(item.get("description") or "").strip(),
                    "priority": str(item.get("priority") or "").strip()})
    if not out:
        raise ValueError("the agent proposed no issues at all")
    if len(out) > PLAN_MAX:
        raise ValueError("the agent proposed {} issues; the cap is {} "
                         "(JIRA_FLOW_PLAN_MAX)".format(len(out), PLAN_MAX))
    return out


def ask_agent(prompt: str) -> str:
    """Prompten in, svaret ut. Vilken CLI som helst som pratar stdin/stdout duger:
    JIRA_FLOW_AGENT="claude -p" i grunden, byt till codex, agy, opencode eller hermes."""
    argv = shlex.split(AGENT)
    if not argv:
        raise RuntimeError("JIRA_FLOW_AGENT is empty")
    try:
        done = subprocess.run(argv, input=prompt, capture_output=True, text=True,
                              timeout=AGENT_TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError("{} is not installed".format(argv[0]))
    except subprocess.TimeoutExpired:
        raise RuntimeError("{} gave no answer in {}s".format(argv[0], AGENT_TIMEOUT))
    if done.returncode != 0:
        raise RuntimeError("{} exited {}: {}".format(
            argv[0], done.returncode, (done.stderr or "").strip()[:200]))
    return done.stdout


def cmd_plan(client_, args) -> int:
    """Kundens önskemål in, ärendeförslag ut. Ingenting skrivs förrän --create."""
    if args.text:
        # Panelen har texten i ett fält, inte i en fil: argv är oshellat, så
        # inget kan citeras sönder på vägen.
        wish = args.text
    elif args.file:
        wish = Path(args.file).read_text()
    else:
        wish = "" if sys.stdin.isatty() else sys.stdin.read()
    if not wish.strip():
        message = "No wish to work from (stdin, or --file PATH)."
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2

    board = client_.board(args.project)
    types = client_.types(args.project)
    prompt = PLAN_PROMPT.format(project=args.project, limit=PLAN_MAX,
                                types=", ".join(types) or "Story, Task, Bug", wish=wish.strip())
    try:
        items = parse_plan(ask_agent(prompt))
    except (ValueError, RuntimeError) as exc:
        message = str(exc)
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2

    if args.json:
        print(json.dumps({"ok": True, "created": False, "proposal": items,
                          "project": args.project}, ensure_ascii=False))
    else:
        print("{} issue(s) proposed for {}:".format(len(items), args.project))
        for number, item in enumerate(items, 1):
            print("  {}. [{}] {}  ({})".format(number, item["type"], item["summary"],
                                               item["priority"] or "no priority"))
    if not args.create:
        if not args.json:
            print("nothing written. again with --create writes exactly this list.")
        return 0

    created = []
    for item in items:
        try:
            answer = client_.create(board, item) or {}
        except (SystemExit, RuntimeError) as exc:
            # Stanna på första felet: hellre halv tavla med besked än tyst halv tavla.
            message = "{} failed: {} ({} of {} written)".format(item["summary"][:40], exc,
                                                               len(created), len(items))
            say(args, {"ok": False, "error": message, "created": created},
                ["jira_flow: " + message,
                 "           already written: " + ", ".join(c["key"] for c in created)])
            return 2
        key = answer.get("key") or (answer.get("result") or {}).get("key") or ""
        created.append({"key": key, "summary": item["summary"]})
        if not args.json:
            print("created: {}  {}".format(key or "(no key back)", item["summary"]))
    for entry in created:
        client_.log("flow-plan", entry["key"], entry["summary"][:80])
    say(args, {"ok": True, "created": created, "project": args.project}, [])
    return 0


def cmd_current(client_, args) -> int:
    found = find(client_, current_jql(args.project), limit=5)
    if not found:
        return 1
    if len(found) > 1:
        print("# {} in progress; the most recently updated is {}".format(len(found), found[0]["key"]),
              file=sys.stderr)
    print(found[0]["key"])
    return 0


# ------------------------------------------------------------------ shims

VSCODE_TASK = """{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Jira: take next critical",
      "type": "shell",
      "command": "python3",
      "args": ["{self}", "next"],
      "windows": { "command": "py" },
      "options": { "cwd": "${workspaceFolder}" },
      "presentation": { "reveal": "always", "panel": "shared" },
      "problemMatcher": []
    },
    {
      "label": "Jira: what am I on",
      "type": "shell",
      "command": "python3",
      "args": ["{self}", "current"],
      "options": { "cwd": "${workspaceFolder}" },
      "presentation": { "reveal": "always", "panel": "shared" },
      "problemMatcher": []
    }
  ]
}
"""

# Windows har ingen python3: py är Launcher-skapelsen, python finns i PATH-varianten.
# if-satsen, inte &&-kedja: ett felaktigt körningsresultat får inte betyda "kör en gång till".
LAUNCHER = ("@echo off\r\n"
            "where py >nul 2>nul\r\n"
            "if %errorlevel%==0 (\r\n"
            "  py \"%~dp0jira_flow.py\" %*\r\n"
            ") else (\r\n"
            "  python \"%~dp0jira_flow.py\" %*\r\n"
            ")\r\n")

IDEA_TOOL = """<tool name="Jira: take next critical" description="Assign the most critical Jira item to me and start it" showInMainMenu="true" showInEditor="true" showInProject="true" showInSearchPopup="true" disabled="false" useConsole="true" showConsoleOnStdOut="true" showConsoleOnStdErr="true" synchronizeAfterRun="true">
  <exec>
    <option name="COMMAND" value="{command}" />
    <option name="PARAMETERS" value="{params}" />
    <option name="WORKING_DIRECTORY" value="$ProjectFileDir$" />
  </exec>
</tool>
"""

AGY_RULE = """# Jira

Work items live in Jira; the credential is never in this repository.

## Taking work

    python3 {self} next            # most critical not-started item -> me -> In Progress
    python3 {self} next --dry-run  # show the pick and the move, write nothing

The pick is `project = {project} AND statusCategory = "To Do" AND (assignee IS
EMPTY OR assignee = currentUser()) ORDER BY priority DESC, created ASC`. The
command prints the item it took and the runners-up it skipped; never guess which
item is most critical, run it.

If this session has a Jira MCP server (for example the `godjira` one), prefer its
tools -- `jira_backlog`, `jira_update`, `jira_move` -- for anything this CLI does
not cover. Never delete or restore an issue.
"""

HOOK = """#!/bin/sh
# prepare-commit-msg: put the Jira key in the commit subject, so the issue and
# the commit are linked without anyone remembering a number.
# Written by `jira_flow install`. $1 = message file, $2 = source.
case "$2" in
  merge|squash|commit) exit 0 ;;   # git writes merge subjects; amend is deliberate
esac

[ -z "$1" ] && exit 0
grep -qE '[A-Z][A-Z0-9]+-[0-9]+' "$1" && exit 0

key=$(git branch --show-current 2>/dev/null | grep -oE '[A-Z][A-Z0-9]+-[0-9]+' | head -1)
if [ -z "$key" ]; then
  # Git for Windows kör hooks genom sin egen bash, så den här filen fungerar på alla
  # tre systemen. Tolken är det som skiljer: python3 på macOS/Linux, py eller python
  # på Windows. Slå upp den vid körning i stället för att skriva in en sökväg.
  py=""
  for kandidat in python3 py python; do
    command -v "$kandidat" >/dev/null 2>&1 && py="$kandidat" && break
  done
  if [ -n "$py" ]; then
    key=$("$py" {self} current 2>/dev/null | grep -oE '[A-Z][A-Z0-9]+-[0-9]+' | head -1)
  fi
fi
[ -z "$key" ] && exit 0

printf '%s: %s' "$key" "$(cat "$1")" > "$1.jiraflow" && mv "$1.jiraflow" "$1"
echo "jira_flow: the subject names $key"
"""


def write_once(path: Path, text: str, dry_run: bool) -> None:
    # Our own files are rewritten -- they carry the tool's name -- so a moved or
    # updated jira_flow never leaves a shim pointing at the old path. Something
    # that is not ours is left alone.
    existed = path.exists()
    if existed and "jira_flow" not in path.read_text():
        print("kept (not ours): {}".format(path))
        return
    if existed and path.read_text() == text:
        print("kept (already right): {}".format(path))
        return
    if dry_run:
        print("{}: {}".format("would update" if existed else "would write", path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" med flit: Windows textläge skriver \r\n, och en sh-hook med CRLF
    # dör på "\r: command not found" i Git for Windows' bash.
    with path.open("w", newline="\n") as fh:
        fh.write(text)
    if path.name == "prepare-commit-msg":
        path.chmod(0o755)
    print("{}: {}".format("updated" if existed else "wrote", path))


def cmd_login(args) -> int:
    """Store the token in the machine's own store. Read from stdin, never as an
    argument: arguments end up in shell history and in process lists."""
    import jira_secrets

    store = jira_secrets.store_for()
    if isinstance(store, jira_secrets.NoStore):
        print("jira_flow: " + _no_store_reason(), file=sys.stderr)
        return 2
    token = sys.stdin.read().strip()
    if not token:
        print("jira_flow: no token on stdin.", file=sys.stderr)
        return 2
    try:
        store.write(token)
    except RuntimeError as exc:
        print("jira_flow: {}".format(exc), file=sys.stderr)
        return 2
    print("token stored in {} ({}).".format(type(store).__name__, jira_secrets.SECRET_FILE
                                            if isinstance(store, jira_secrets.WindowsStore)
                                            else "the login keychain"))
    return 0


def cmd_logout(args) -> int:
    import jira_secrets

    try:
        gone = jira_secrets.store_for().delete()
    except RuntimeError as exc:
        print("jira_flow: {}".format(exc), file=sys.stderr)
        return 2
    print("token removed." if gone else "nothing stored here.")
    return 0


def _no_store_reason() -> str:
    """Sagt en gång, på det system där det gäller."""
    return ("på Linux sköts token av bryggans nyckelring (`jira_bridge.py login`) "
            "eller av {} (chmod 600)".format(Path.home() / ".config/jira-flow/config.json"))


def cmd_install(args) -> int:
    repo = Path(args.repo or ".").resolve()
    if not (repo / ".git").is_dir():
        print("jira_flow: {} is not a git repository.".format(repo), file=sys.stderr)
        return 2

    print("editor shims in {}".format(repo))
    # Windows har inget python3 och IntelliJ har ingen per-OS-variant av ett externt
    # verktyg, så där läggs en startfil i repot som verktyget pekar på i stället.
    windows = os.name == "nt"
    write_once(repo / ".vscode/tasks.json", VSCODE_TASK.replace("{self}", str(SELF)), args.dry_run)
    if windows:
        write_once(repo / "jira-flow.cmd", LAUNCHER, args.dry_run)
    write_once(repo / ".idea/tools/jira-flow.xml",
               IDEA_TOOL.replace("{command}", str(repo / "jira-flow.cmd") if windows else "python3")
                        .replace("{params}", "next" if windows else "{} next".format(SELF)),
               args.dry_run)
    write_once(repo / ".agents/rules/jira.md",
               AGY_RULE.replace("{self}", str(SELF)).replace("{project}", args.project), args.dry_run)
    write_once(repo / ".git/hooks/prepare-commit-msg", HOOK.replace("{self}", str(SELF)), args.dry_run)

    if not args.dry_run:
        print("\nVS Code / Antigravity IDE: Run Task -> 'Jira: take next critical'")
        print("IntelliJ: Tools -> External Tools -> 'Jira: take next critical' (key: Settings -> Keymap)")
    return 0


# -------------------------------------------------------------- self-check

def selftest() -> int:
    checks = 0
    jql = pick_jql("SCRUM")
    assert "project = SCRUM" in jql and "assignee IS EMPTY" in jql and "priority DESC" in jql, jql
    assert "In Progress" in current_jql("SCRUM")
    assert "assignee IS EMPTY" in pick_jql("SCRUM", "mine")
    assert "assignee IS EMPTY" not in pick_jql("SCRUM", "any"), "the take-over pool is wider"
    checks += 1
    shared = {"key": "S-1", "fields": {"summary": "GEMENSAMT: testa flödet"}}
    mine_high = {"key": "S-2", "fields": {"summary": "A1 bokningen"}}
    mine_low = {"key": "S-3", "fields": {"summary": "A2 tabellen"}}
    order = [i["key"] for i in shared_first([mine_high, mine_low, shared])]
    assert order == ["S-1", "S-2", "S-3"], order          # gemensam först
    assert [i["key"] for i in shared_first([mine_low, mine_high])] == ["S-3", "S-2"], \
        "ordningen inom gruppen är frågans, inte sorterarens"   # stabil
    checks += 1
    assert key_in("feature/SCRUM-147-booking-list") == "SCRUM-147", key_in("feature/SCRUM-147-booking-list")
    assert key_in("no key here") == "", "a keyless string must give an empty key"
    assert key_in("fix AB-1 thing") == "AB-1"
    checks += 1
    assert prefix_subject("fix the list", "SCRUM-147") == "SCRUM-147: fix the list"
    assert prefix_subject("SCRUM-147: fix", "SCRUM-147") == "SCRUM-147: fix", "no double key"
    assert prefix_subject("", "SCRUM-147") == "" and prefix_subject("fix", "") == "fix"
    assert prefix_subject("\n\nfix the list\n", "SCRUM-147") == "SCRUM-147: fix the list\n", "leading blanks"
    checks += 1
    assert LAUNCHER.startswith("@echo off\r\n") and LAUNCHER.count("%~dp0") == 2, "cmd-filen"
    assert "&&" not in LAUNCHER, "en &&-kedja hade kört skriptet två gånger vid felkod"
    windows_idea = (IDEA_TOOL.replace("{command}", "C:/repo/jira-flow.cmd").replace("{params}", "next"))
    unix_idea = IDEA_TOOL.replace("{command}", "python3").replace("{params}", "/x/y.py next")
    assert "python3" not in windows_idea and "/x/y.py next" in unix_idea, "två system, två verktyg"
    checks += 1
    for template in (VSCODE_TASK, IDEA_TOOL, AGY_RULE, HOOK):
        filled = (template.replace("{self}", "/x/y.py").replace("{project}", "SCRUM")
                  .replace("{command}", "python3").replace("{params}", "/x/y.py next"))
        assert "{self}" not in filled and "{project}" not in filled, "template placeholder unfilled"
    json.loads(VSCODE_TASK.replace("{self}", "/x/y.py"))  # the task template stays valid JSON
    assert '\r' not in HOOK, "en hook med CRLF dör i Git for Windows' bash"
    assert "command -v" in HOOK, "hooken ska slå upp sin tolk (py på Windows, python3 annars)"
    assert '"windows"' in VSCODE_TASK, "VS Code-tasken behöver py på Windows"
    checks += 1
    # plan: agentens svar tolkas helt eller inte alls, och inget skrivs utan --create.
    fenced = "Här är förslagen:\n```json\n[{\"summary\": \"Boka tid\", \"type\": \"Story\", "\
             "\"description\": \"kunden kan boka\", \"priority\": \"High\"}, "\
             "{\"summary\": \"Bekräfta bokning\", \"type\": \"Task\"}]\n```\nHör av dig!"
    items = parse_plan(fenced)
    assert len(items) == 2 and items[0]["summary"] == "Boka tid", items
    assert items[1]["type"] == "Task" and items[0]["priority"] == "High"
    checks += 1
    for bad, why in (("inga ärenden här, bara prat", "utan array"),
                     ("[{\"type\": \"Task\"}]", "utan summary"),
                     ("[]", "tom lista"),
                     ("[{\"summary\": \"x\"}]" * (PLAN_MAX + 1), "över taket")):
        try:
            parse_plan(bad)
            raise AssertionError("skulle ha vägrat: " + why)
        except ValueError:
            pass
    checks += 1
    prompt = PLAN_PROMPT.format(project="SCRUM", limit=PLAN_MAX, types="Story, Task", wish="kunden vill boka")
    assert "SCRUM" in prompt and "kunden vill boka" in prompt, "prompten bär projekt och önskemål"
    assert "{project}" not in prompt and "{wish}" not in prompt and "{limit}" not in prompt, "ofylld platshållare"

    class FakeClient:
        def __init__(self):
            self.written = []
        def board(self, project):
            return {"id": "7", "projectKey": project}
        def types(self, project):
            return ["Story", "Task"]
        def create(self, board, item):
            self.written.append(item)
            return {"key": "SCRUM-{}".format(900 + len(self.written))}
        def log(self, *a, **k):
            pass

    fake = FakeClient()
    plan_argv = argparse.Namespace(file="", create=False, json=True, project="SCRUM")
    # Byt den globala agenten och önskemålet: ett självprov får aldrig starta en
    # riktig agent, och aldrig läsa på en riktig stdin (den kan vara en pipe som
    # aldrig tar slut). Båda anropen går mot samma fejkade agent.
    class Quiet:
        def write(self, *a):
            return None

        def flush(self):
            return None

    original_agent, original_stdin, original_stdout = ask_agent, sys.stdin, sys.stdout
    try:
        globals()["ask_agent"] = lambda prompt: '[{"summary": "Boka tid", "type": "Story"}]'
        sys.stdin = type("S", (), {"isatty": lambda self: False, "read": lambda self: "kunden vill boka"})()
        sys.stdout = Quiet()

        assert cmd_plan(fake, plan_argv) == 0, "förslaget ska gå igenom utan att skriva"
        assert fake.written == [], "utan --create får ingenting skrivas"

        plan_argv.create = True
        assert cmd_plan(fake, plan_argv) == 0, "med --create ska listan skrivas"
        assert len(fake.written) == 1 and fake.written[0]["summary"] == "Boka tid", fake.written
    finally:
        globals()["ask_agent"], sys.stdin, sys.stdout = original_agent, original_stdin, original_stdout
    checks += 2
    print("jira_flow self-check: {} checks, 0 failed".format(checks))
    return 0


# ------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jira_flow", description=__doc__.split("\n")[0])
    parser.add_argument("--selftest", action="store_true", help="offline checks of the pure logic")
    sub = parser.add_subparsers(dest="cmd")
    p_next = sub.add_parser("next", help="take the most critical item and start it")
    p_cur = sub.add_parser("current", help="the key you are on right now")
    p_ins = sub.add_parser("install", help="write the editor shims and the commit hook into a repo")
    sub.add_parser("login", help="store the Jira token in this machine's own store (reads stdin)")
    sub.add_parser("logout", help="remove it again")
    for p in (p_next, p_cur):
        p.add_argument("--project", default=DEFAULT_PROJECT)
    p_next.add_argument("--status", default=DEFAULT_STATUS)
    p_next.add_argument("--dry-run", action="store_true")
    p_next.add_argument("--json", action="store_true", help="machine-readable result (for an agent)")
    p_next.add_argument("--expect", metavar="KEY", default="",
                        help="take exactly KEY, which a human confirmed (the second press)")
    p_plan = sub.add_parser("plan", help="customer wish in, issue proposal out")
    p_plan.add_argument("--file", default="", help="read the wish from a file (default: stdin)")
    p_plan.add_argument("--text", default="", help="the wish as one argument (the panel sends it this way)")
    p_plan.add_argument("--create", action="store_true",
                        help="write exactly the proposed list (default: write nothing)")
    p_plan.add_argument("--json", action="store_true", help="machine-readable result")
    p_plan.add_argument("--project", default=DEFAULT_PROJECT)
    p_ins.add_argument("repo", nargs="?", help="repository root (default: here)")
    p_ins.add_argument("--project", default=DEFAULT_PROJECT)
    p_ins.add_argument("--dry-run", action="store_true")
    return parser


def main(argv) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.cmd:
        build_parser().print_help()
        return 1
    if args.cmd == "install":
        return cmd_install(args)
    if args.cmd == "login":
        return cmd_login(args)
    if args.cmd == "logout":
        return cmd_logout(args)
    jira = client()
    if args.cmd == "plan":
        return cmd_plan(jira, args)
    return cmd_next(jira, args) if args.cmd == "next" else cmd_current(jira, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
