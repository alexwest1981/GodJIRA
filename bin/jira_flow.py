#!/usr/bin/env python3
"""jira_flow -- take the next critical Jira item, and keep commits linked to it.

One small CLI instead of one plugin per editor: VS Code and the Antigravity IDE
run it as a task, IntelliJ as an external tool, and git calls it from a
prepare-commit-msg hook. `jira_flow install <repo>` writes all four.

Commands
    next [--project KEY] [--status "In Progress"] [--dry-run] [--json] [--expect KEY]
        Take the most critical not-started item (shared tasks first), assign it to
        you, move it to In Progress. With nothing of your own it proposes the most
        critical item that is someone else's and takes it only when the caller
        presses again with --expect KEY, naming that exact issue.
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


def client():
    """The bridge when it is installed, otherwise a plain HTTP client."""
    if BRIDGE_DIR.is_dir() and str(BRIDGE_DIR) not in sys.path:
        sys.path.insert(0, str(BRIDGE_DIR))
    try:
        import jira_bridge  # noqa: PLC0415 -- the local import is the point

        return Bridge(jira_bridge, jira_bridge.load_config())
    except ImportError:
        pass

    config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    site = os.environ.get("JIRA_SITE") or config.get("site") or ""
    email = os.environ.get("JIRA_EMAIL") or config.get("email") or ""
    token = os.environ.get("JIRA_TOKEN") or config.get("token") or ""
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

IDEA_TOOL = """<tool name="Jira: take next critical" description="Assign the most critical Jira item to me and start it" showInMainMenu="true" showInEditor="true" showInProject="true" showInSearchPopup="true" disabled="false" useConsole="true" showConsoleOnStdOut="true" showConsoleOnStdErr="true" synchronizeAfterRun="true">
  <exec>
    <option name="COMMAND" value="python3" />
    <option name="PARAMETERS" value="{self} next" />
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
  key=$(python3 {self} current 2>/dev/null | grep -oE '[A-Z][A-Z0-9]+-[0-9]+' | head -1)
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
    path.write_text(text)
    if path.name == "prepare-commit-msg":
        path.chmod(0o755)
    print("{}: {}".format("updated" if existed else "wrote", path))


def cmd_install(args) -> int:
    repo = Path(args.repo or ".").resolve()
    if not (repo / ".git").is_dir():
        print("jira_flow: {} is not a git repository.".format(repo), file=sys.stderr)
        return 2

    print("editor shims in {}".format(repo))
    write_once(repo / ".vscode/tasks.json", VSCODE_TASK.replace("{self}", str(SELF)), args.dry_run)
    write_once(repo / ".idea/tools/jira-flow.xml", IDEA_TOOL.replace("{self}", str(SELF)), args.dry_run)
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
    for template in (VSCODE_TASK, IDEA_TOOL, AGY_RULE, HOOK):
        filled = template.replace("{self}", "/x/y.py").replace("{project}", "SCRUM")
        assert "{self}" not in filled and "{project}" not in filled, "template placeholder unfilled"
    json.loads(VSCODE_TASK.replace("{self}", "/x/y.py"))  # the task template stays valid JSON
    checks += 1
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
    for p in (p_next, p_cur):
        p.add_argument("--project", default=DEFAULT_PROJECT)
    p_next.add_argument("--status", default=DEFAULT_STATUS)
    p_next.add_argument("--dry-run", action="store_true")
    p_next.add_argument("--json", action="store_true", help="machine-readable result (for an agent)")
    p_next.add_argument("--expect", metavar="KEY", default="",
                        help="take exactly KEY, which a human confirmed (the second press)")
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
    jira = client()
    return cmd_next(jira, args) if args.cmd == "next" else cmd_current(jira, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
