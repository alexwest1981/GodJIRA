#!/usr/bin/env python3
"""Bridge between the custom.jira Omarchy plugin and a Jira site.

The QML shell never talks HTTP or stores credentials: it calls this helper and
reads one JSON document from stdout. The helper owns every API call and every
credential access.

Two data sources produce the SAME neutral snapshot model, so the UI has a
single render path:

  * mock  - a deterministic fake Jira with two boards, an active sprint, and
            moveable issues, persisted under ~/.local/state/omarchy/. Good for
            developing without credentials. `mock-reset` rebuilds it.
  * real  - Jira Cloud over REST v3 / Agile 1.0. The API token is stored in the
            system keyring (secret-tool), never in a config file.

Commands (all print one JSON document on stdout, exit 0 for every state the
user can encounter):

  status                          Config + who is connected (or why not)
  snapshot                        The whole neutral model for the UI
  transitions <issueKey>          Statuses the issue can move to
  move <issueKey> <target>        Move issue to a status/transition
  create <boardId> <json>         Add an issue to a board (admin/create perms)
  delete <issueKey> --yes         Delete an issue (admin perms). Requires --yes;
                                  a full copy is saved first, and more than 3
                                  deletions within 10 minutes are refused
                                  (--force overrides). See "Skrivskydd".
  journal [limit]                 What this tool has written, newest first
  trash [issueKey]                The local copies taken before deletions/changes
  restore <issueKey>              Recreate a deleted issue from its local copy
  assign <issueKey> <sprint|backlog>  Put an issue in a sprint, or on the backlog
  comments <issueKey>             Comments on an issue
  comment <issueKey> <text>       Add a comment to an issue
  update <issueKey> <json>        Edit summary/description/assignee/priority/points
  options <projectKey>            Assignable people, priorities, issue types
  activity <projectKey> [limit]   Recently changed issues, newest first
  dev <issueKey>                  Git/PR/build status + the issue's history
  report <boardId> <sprintId>     Burndown series + sprint report + velocity
  login [--site <url>] [--email <addr>] [--token-file <path>] [--replace]
                                  Validate the credentials against Jira FIRST,
                                  and only then store the token and write the
                                  address. An existing working connection is
                                  never overwritten without --replace.
  logout --yes                    Forget the stored credential (--yes required).
                                  The address is kept, the token is removed.
  configure <json> [--replace]    Store UI/config keys. The identity keys
                                  (mode, siteUrl, email) are refused while a
                                  working connection exists unless --replace.
  mock-reset                      Rebuild the mock dataset

Anything else (bad args, bad JSON, a crash) is a bug in the helper and exits 1.
"""

import base64
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = 1
SERVICE = "custom.jira"
LABEL = "Custom Jira"
TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens"

HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME, ".config", "omarchy")
CONFIG_PATH = os.path.join(CONFIG_DIR, "jira.json")
STATE_DIR = os.path.join(HOME, ".local", "state", "omarchy")
MOCK_STATE_PATH = os.path.join(STATE_DIR, "jira-mock.json")
WATCH_PATH = os.path.join(STATE_DIR, "jira-watch.json")
# Skrivskyddet: en journal över varje skrivning, en lokal papperskorg och en kvot
# som stoppar en skur av raderingar. Se "skrivskydd" längre ner.
ACTION_LOG_PATH = os.path.join(STATE_DIR, "jira-actions.log")
TRASH_DIR = os.path.join(STATE_DIR, "jira-trash")
DELETE_QUOTA = 3
DELETE_WINDOW_SECS = 600

DEFAULT_CONFIG = {
    "schema": 1,
    "mode": "mock",
    "siteUrl": "",
    "email": "",
    "selectedBoardId": "",
    "selectedProjectKey": "",
    # Which view the panel opens on ("summary", "board", "backlog",
    # "timeline", "reports", "dev", "activity"); empty means Board.
    "startView": "",
}

# ---------------------------------------------------------------- payloads

def payload(data):
    """Print a JSON document and exit 0."""
    print(json.dumps(data, ensure_ascii=False, default=str))
    sys.exit(0)


def ok_payload(**extra):
    out = {"schema": SCHEMA, "ok": True, "error": None, "generatedAt": utc_now()}
    out.update(extra)
    payload(out)


def err_payload(message, **extra):
    out = {"schema": SCHEMA, "ok": False, "error": message, "generatedAt": utc_now()}
    out.update(extra)
    payload(out)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- config

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if v is not None})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def account_for(cfg):
    return cfg.get("email") or "default"


# ---------------------------------------------------------------- keyring

def store_secret(token, account):
    subprocess.run(
        ["secret-tool", "store", "--label=" + LABEL,
         "service", SERVICE, "account", account],
        input=token.encode(), check=True)


def clear_secret(account):
    subprocess.run(
        ["secret-tool", "clear", "service", SERVICE, "account", account],
        check=True, capture_output=True)


def load_secret(account):
    proc = subprocess.run(
        ["secret-tool", "lookup", "service", SERVICE, "account", account],
        capture_output=True)
    if proc.returncode != 0:
        return None
    token = proc.stdout.decode("utf-8", "replace").strip()
    return token or None


# ---------------------------------------------------- anslutningslåset

# Nycklarna som beskriver VILKEN anslutning som används. UI-nycklar som
# startView eller vald tavla rör dem inte och ska fortsätta gå att spara.
IDENTITY_KEYS = ("mode", "siteUrl", "email")


def stored_token(cfg):
    """Token ur nyckelringen, eller None. Får aldrig kasta."""
    account = (cfg or {}).get("email") or ""
    if not account:
        return None
    try:
        return load_secret(account)
    except Exception:  # nyckelringen kan saknas helt
        return None


def connection_summary(cfg):
    """Den sparade anslutningen som UI:t får se den — aldrig token själv."""
    site = (cfg or {}).get("siteUrl") or ""
    email = (cfg or {}).get("email") or ""
    mode = (cfg or {}).get("mode") or ""
    token = stored_token(cfg) if email else None
    return {
        "siteUrl": site,
        "email": email,
        "mode": mode,
        "hasToken": bool(token),
        "locked": bool(site and email and token and mode == "real"),
    }


def forget_stored_token(email):
    """Tar bort token för ett konto. Saknad post är inget fel."""
    if not email:
        return False
    try:
        clear_secret(email)
        return True
    except Exception:
        return False


def flag_value(argv, name):
    if name in argv:
        i = argv.index(name) + 1
        if i < len(argv):
            return argv[i]
    return None


# ---------------------------------------------------------------- http

def jira_get(cfg, path, token=None):
    return jira_request(cfg, "GET", path, token=token)


def jira_post(cfg, path, body, token=None):
    return jira_request(cfg, "POST", path, body, token=token)


def jira_request(cfg, method, path, body=None, token=None):
    if token is None:
        token = load_secret(account_for(cfg))
    if not token:
        raise RuntimeError("Ingen API-token i nyckelringen. Kör login först.")
    site = cfg["siteUrl"].rstrip("/")
    url = site + path
    # Scoped personal access tokens (ATCTT...) authenticate as "Bearer <token>"
    # with no account. Classic API tokens (ATATT...) use Basic <email>:<token>.
    if token.startswith("ATCTT") or cfg.get("auth") == "bearer":
        auth = "Bearer " + token
    else:
        auth = "Basic " + base64.b64encode("{}:{}".format(cfg["email"], token).encode()).decode()
    data = None
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", auth)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8", "replace"))
            messages = parsed.get("errorMessages") or []
            if isinstance(parsed.get("errors"), dict):
                messages = messages + list(parsed["errors"].values())
            detail = " — " + "; ".join(str(m) for m in messages if m)
        except Exception:
            pass
        raise RuntimeError("Jira svarade {} {}{}".format(exc.code, exc.reason, detail))


def paginate(cfg, path):
    """Fetch all pages of an Agile/API list endpoint. Path should already
    carry a query string without startAt/maxResults."""
    results = []
    start = 0
    while True:
        sep = "&" if "?" in path else "?"
        page = jira_get(cfg, "{}{}startAt={}&maxResults=100".format(path, sep, start))
        values = page.get("values") or page.get("issues") or []
        results.extend(values)
        total = page.get("total")
        if total is None:
            total = len(values) + start
        start += len(values)
        if not values or start >= total:
            break
    return results


# ------------------------------------------------------- real (Jira Cloud)

# Custom field ids differ per Jira site; this site's are the defaults below.
# Keep them in one place so another site only has to change these three lines.
STORY_POINTS_FIELD = "customfield_10016"   # Story point estimate
SPRINT_FIELD = "customfield_10020"         # Sprint (array, empty on the backlog)
START_DATE_FIELD = "customfield_10015"     # Start date

FIELD_SUBSET = (
    "summary,status,assignee,issuetype,priority,updated,description,parent,"
    "subtasks,creator,duedate,{},{},{}".format(
        STORY_POINTS_FIELD, SPRINT_FIELD, START_DATE_FIELD)
)


def issue_fields(raw):
    """Normalize one Jira issue JSON into our neutral issue model."""
    fields = raw.get("fields") or {}
    status = fields.get("status") or {}
    category = (status.get("statusCategory") or {}).get("key", "indeterminate")
    assignee = fields.get("assignee") or {}
    itype = fields.get("issuetype") or {}
    priority = fields.get("priority") or {}
    updated = fields.get("updated") or ""
    updated_ms = 0
    try:
        dt = datetime.datetime.fromisoformat(updated.replace("Z", "+00:00"))
        updated_ms = int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        pass
    # The sprint field is an array (a team-managed issue can sit in several);
    # the last entry is the current one. Parent is how team-managed issues hang
    # off an epic or a subtask off its parent.
    sprints = fields.get(SPRINT_FIELD)
    sprint = {}
    if isinstance(sprints, list) and sprints:
        sprint = sprints[-1] if isinstance(sprints[-1], dict) else {}
    elif isinstance(sprints, dict):
        sprint = sprints
    parent = fields.get("parent") or {}
    return {
        "key": raw.get("key") or "",
        "summary": fields.get("summary") or "",
        "typeName": itype.get("name") or "",
        "typeIcon": itype.get("iconUrl") or "",
        "statusId": str(status.get("id") or ""),
        "statusName": status.get("name") or "",
        "statusCategory": category,
        "assigneeEmail": assignee.get("emailAddress") or "",
        "assigneeName": assignee.get("displayName") or "",
        "priorityName": priority.get("name") or "",
        "storyPoints": fields.get(STORY_POINTS_FIELD),
        "updatedMs": updated_ms,
        "description": (adf_to_text(fields.get("description")) or "")[:20000],
        "url": "",
        "projectKey": (raw.get("fields") or {}).get("project", {}).get("key", ""),
        "sprintId": str(sprint.get("id") or ""),
        "sprintName": sprint.get("name") or "",
        "startMs": parse_iso(fields.get(START_DATE_FIELD)),
        "dueMs": parse_iso(fields.get("duedate")),
        "parentKey": parent.get("key") or "",
        "parentSummary": ((parent.get("fields") or {}).get("summary") or ""),
    }


def real_snapshot(cfg):
    myself = jira_get(cfg, "/rest/api/3/myself")
    email = myself.get("emailAddress") or cfg.get("email") or ""
    account = {
        "email": email,
        "displayName": myself.get("displayName") or "",
        "siteUrl": cfg.get("siteUrl") or "",
        "connected": True,
    }

    projects = []
    project_rows = paginate(cfg, "/rest/api/3/project/search")
    for row in project_rows:
        projects.append({"key": row.get("key") or "", "name": row.get("name") or ""})

    boards = []
    perm_cache = {}
    for board in paginate(cfg, "/rest/agile/1.0/board"):
        bid = board.get("id")
        bname = board.get("name") or "Board {}".format(bid)
        btype = board.get("type") or "kanban"
        location = board.get("location") or {}
        pkey = (location.get("projectKey") or "").upper()
        perms = real_project_permissions(cfg, perm_cache, pkey)

        config = jira_get(cfg, "/rest/agile/1.0/board/{}/configuration".format(bid))
        columns = []
        for col in (config.get("columnConfig") or {}).get("columns") or []:
            for st in col.get("statuses") or []:
                columns.append({
                    "name": col.get("name") or st.get("name") or "",
                    "statusId": str(st.get("id") or ""),
                    "statusName": st.get("name") or "",
                })

        # Every sprint on the board - the timeline and the report view need the
        # whole list, not only the one that happens to be running right now.
        sprints = []
        sprint = None
        for row in paginate(cfg, "/rest/agile/1.0/board/{}/sprint".format(bid)):
            state = (row.get("state") or "").lower()
            entry = {
                "id": str(row.get("id") or ""),
                "name": row.get("name") or "",
                "state": state,
                "startMs": parse_iso(row.get("startDate")),
                "endMs": parse_iso(row.get("endDate")),
            }
            sprints.append(entry)
            if state == "active" and sprint is None:
                sprint = entry

        def load_issues(path):
            out = []
            for raw in paginate(cfg, path):
                issue = issue_fields(raw)
                issue["url"] = "{}/browse/{}".format(cfg["siteUrl"].rstrip("/"), issue["key"])
                if not issue["projectKey"]:
                    issue["projectKey"] = pkey
                out.append(issue)
            return out

        board_issues = load_issues(
            "/rest/agile/1.0/board/{}/issue?fields={}".format(bid, FIELD_SUBSET))

        # Jira's Agile API reports team-managed boards as type "simple"
        # (kanban-capable boards can also come back as "simple"), and those do
        # have a real backlog - only kanban boards have none, where the
        # endpoint 404s. Never let a board without a backlog kill the snapshot.
        #
        # `/board/{id}/issue` also returns the backlog issues, so whatever the
        # backlog endpoint gives us is removed from the board list: the two
        # lists stay disjoint, exactly like the mock model.
        backlog = []
        if btype != "kanban":
            try:
                backlog = load_issues(
                    "/rest/agile/1.0/board/{}/backlog?fields={}".format(bid, FIELD_SUBSET))
            except RuntimeError:
                backlog = []
        backlog_keys = set(issue["key"] for issue in backlog)
        if backlog_keys:
            board_issues = [i for i in board_issues if i["key"] not in backlog_keys]

        boards.append({
            "id": str(bid),
            "name": bname,
            "type": btype,
            "projectKey": pkey,
            "columns": columns,
            "sprint": sprint,
            "sprints": sprints,
            "issues": board_issues,
            "backlog": backlog,
            "canAdd": perms["canAdd"],
            "canDelete": perms["canDelete"],
        })

    return {
        "schema": SCHEMA,
        "ok": True,
        "error": None,
        "generatedAt": utc_now(),
        "mode": cfg.get("mode"),
        "account": account,
        "projects": projects,
        "boards": boards,
    }


def parse_iso(value):
    if not value:
        return 0
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return 0


def adf_to_text(node):
    """Flatten an Atlassian Document Format body into plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(x for x in (adf_to_text(n) for n in node) if x)
    if not isinstance(node, dict):
        return str(node)
    ntype = node.get("type")
    text = ""
    if ntype == "text":
        text = node.get("text") or ""
    content = node.get("content")
    if isinstance(content, list):
        inner = adf_to_text(content)
        if ntype == "paragraph":
            text = (text + inner) if not inner else inner
        elif ntype in ("bulletList", "orderedList"):
            text = inner
        elif ntype == "listItem":
            text = "- " + inner if inner else ""
        elif ntype in ("codeBlock", "blockquote", "heading"):
            text = inner
        elif ntype == "table":
            text = inner
        elif ntype == "tableRow":
            text = inner + " | "
        elif ntype == "tableCell":
            text = inner + ""
        elif ntype == "hardBreak":
            text = "\n"
        else:
            text = inner
    if ntype == "mediaSingle" or ntype == "media":
        return ""
    if text == "" and content:
        return ""
    return text


def real_transitions(cfg, key):
    data = jira_get(cfg, "/rest/api/3/issue/{}/transitions?expand=transitions.fields".format(key))
    out = []
    for tr in data.get("transitions") or []:
        to = tr.get("to") or {}
        out.append({
            "id": tr.get("id"),
            "name": tr.get("name") or to.get("name") or "",
            "toStatusId": str(to.get("id") or ""),
            "toStatusName": to.get("name") or "",
        })
    return out


def real_move(cfg, key, target):
    transitions = real_transitions(cfg, key)
    chosen = None
    for tr in transitions:
        if str(tr["id"]) == str(target) or tr["toStatusName"] == target \
                or str(tr["toStatusId"]) == str(target):
            chosen = tr
            break
    if not chosen:
        available = ", ".join(t["toStatusName"] for t in transitions)
        raise RuntimeError("Kan inte flytta {} till '{}'. Tillgängliga: {}".format(
            key, target, available or "inga"))
    jira_post(cfg, "/rest/api/3/issue/{}/transitions".format(key),
              {"transition": {"id": chosen["id"]}})
    return chosen


def real_project_permissions(cfg, cache, pkey):
    """True for project-level create/delete permissions (admin-ish rights)."""
    if pkey in cache:
        return cache[pkey]
    flags = {"canAdd": False, "canDelete": False}
    if not pkey:
        cache[pkey] = flags
        return flags
    try:
        data = jira_get(cfg,
            "/rest/api/3/mypermissions?projectKey={}&permissions=CREATE_ISSUES,DELETE_ISSUES"
            .format(pkey))
    except RuntimeError:
        cache[pkey] = flags
        return flags
    perms = data.get("permissions") or {}
    flags = {
        "canAdd": bool((perms.get("CREATE_ISSUES") or {}).get("havePermission")),
        "canDelete": bool((perms.get("DELETE_ISSUES") or {}).get("havePermission")),
    }
    cache[pkey] = flags
    return flags


def real_issue_types(cfg, pkey):
    """Creatable issue types for a project (subtasks excluded).

    The createmeta endpoint is deprecated on Jira Cloud and returns an empty
    list on some sites, which used to make `create` fail there; the project
    endpoint carries the same information and is the primary source now."""
    types = []
    try:
        project = jira_get(cfg, "/rest/api/3/project/{}".format(pkey))
        for row in project.get("issueTypes") or []:
            if not row.get("subtask"):
                types.append({"id": str(row.get("id") or ""),
                              "name": row.get("name") or ""})
    except RuntimeError:
        types = []
    if types:
        return types
    try:
        data = jira_get(cfg, "/rest/api/3/issue/createmeta/{}/issuetypes".format(pkey))
        for row in data.get("values") or []:
            if not row.get("subtask"):
                types.append({"id": str(row.get("id") or ""),
                              "name": row.get("name") or ""})
    except RuntimeError:
        pass
    return types


def real_create(cfg, board, payload):
    """Create an issue on the board's project. `board` is a neutral board
    dict (has projectKey). Returns nothing; caller re-snapshots."""
    pkey = (board or {}).get("projectKey")
    if not pkey:
        raise RuntimeError("Tavlan saknar projekt – kan inte skapa ärende.")
    summary = (payload.get("summary") or "").strip()
    if not summary:
        raise RuntimeError("Sammanfattning saknas.")
    target_status = (payload.get("statusName") or "").strip()
    type_name = (payload.get("typeName") or "Task").strip()

    types = real_issue_types(cfg, pkey)
    chosen = None
    for row in types:
        if row.get("name") == type_name:
            chosen = row
            break
        if chosen is None:
            chosen = row
    if chosen is None:
        raise RuntimeError("Kunde inte lista ärendetyper för projektet {}.".format(pkey))

    body = {
        "fields": {
            "project": {"key": pkey},
            "summary": summary,
            "issuetype": {"id": chosen["id"]},
        }
    }
    created = jira_post(cfg, "/rest/api/3/issue", body)
    key = created.get("key") or ""
    if not key:
        raise RuntimeError("Jira svarade utan ärendenyckel vid skapande.")

    if target_status:
        # New issues land in the project's default status; move on if the
        # requested column is a valid transition from there.
        try:
            real_move(cfg, key, target_status)
        except RuntimeError:
            pass
    return key


def real_delete(cfg, key):
    jira_request(cfg, "DELETE", "/rest/api/3/issue/{}".format(key))


def real_assign_sprint(cfg, key, sprint_id):
    """Put an issue in a sprint, or back on the backlog."""
    target = str(sprint_id or "").strip()
    if not target or target.lower() == "backlog":
        jira_post(cfg, "/rest/agile/1.0/backlog/issue", {"issues": [key]})
        return ""
    jira_post(cfg, "/rest/agile/1.0/sprint/{}/issue".format(target), {"issues": [key]})
    return target


def text_to_adf(text):
    """Wrap plain text in the minimal Atlassian Document Format document that
    the v3 API wants for descriptions and comment bodies."""
    paragraphs = []
    for block in str(text or "").split("\n"):
        paragraphs.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": block}] if block else [],
        })
    if not paragraphs:
        paragraphs = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": paragraphs}


def real_comments(cfg, key):
    data = jira_get(cfg,
        "/rest/api/3/issue/{}/comment?maxResults=50&orderBy=created".format(key))
    out = []
    for row in data.get("comments") or []:
        author = row.get("author") or {}
        body = row.get("body")
        out.append({
            "id": str(row.get("id") or ""),
            "authorName": author.get("displayName") or "",
            "authorEmail": author.get("emailAddress") or "",
            "createdMs": parse_iso(row.get("created")),
            "updatedMs": parse_iso(row.get("updated")),
            "body": body if isinstance(body, str) else adf_to_text(body),
        })
    return out


def real_add_comment(cfg, key, text):
    body = str(text or "").strip()
    if not body:
        raise RuntimeError("Kommentaren är tom.")
    jira_post(cfg, "/rest/api/3/issue/{}/comment".format(key), {"body": text_to_adf(body)})
    return real_comments(cfg, key)


def real_options(cfg, pkey):
    """What the edit form can offer: people the issue can be assigned to, plus
    the project's priorities and issue types. Each part is best-effort - a
    project may not expose any of them."""
    people = []
    try:
        rows = jira_get(cfg, "/rest/api/3/user/assignable/search?project={}&maxResults=50"
                             .format(pkey))
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            people.append({
                "accountId": row.get("accountId") or "",
                "displayName": row.get("displayName") or "",
                "email": row.get("emailAddress") or "",
                "active": bool(row.get("active", True)),
            })
    except RuntimeError:
        people = []
    priorities = []
    try:
        for row in jira_get(cfg, "/rest/api/3/priority") or []:
            if isinstance(row, dict) and row.get("name"):
                priorities.append(row["name"])
    except RuntimeError:
        priorities = []
    types = real_issue_types(cfg, pkey)
    return {"people": people, "priorities": priorities, "types": types}


def real_update(cfg, key, payload):
    """Edit the fields the UI can change. Only the keys present in the payload
    are touched, so a form that edits one field cannot wipe the rest."""
    fields = {}
    if "summary" in payload:
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            raise RuntimeError("Sammanfattningen får inte vara tom.")
        fields["summary"] = summary
    if "description" in payload:
        fields["description"] = text_to_adf(payload.get("description"))
    if "assigneeAccountId" in payload:
        account = str(payload.get("assigneeAccountId") or "").strip()
        fields["assignee"] = {"accountId": account} if account else None
    if "priorityName" in payload:
        name = str(payload.get("priorityName") or "").strip()
        fields["priority"] = {"name": name} if name else None
    if "storyPoints" in payload:
        raw = payload.get("storyPoints")
        if raw in (None, ""):
            fields[STORY_POINTS_FIELD] = None
        else:
            try:
                fields[STORY_POINTS_FIELD] = float(raw)
            except (TypeError, ValueError):
                raise RuntimeError("Story points måste vara ett tal.")
    if "dueDate" in payload:
        due = str(payload.get("dueDate") or "").strip()
        fields["duedate"] = due or None
    if not fields:
        raise RuntimeError("Inget att uppdatera.")
    jira_request(cfg, "PUT", "/rest/api/3/issue/{}".format(key), {"fields": fields})


def real_activity(cfg, pkey, limit=25):
    """The most recently updated issues in the project, newest first. The top
    few also carry their last real changelog entry, so the feed can say what
    changed and not just when."""
    jql = "project = {} ORDER BY updated DESC".format(pkey)
    data = jira_get(cfg, "/rest/api/3/search/jql?jql={}&maxResults={}&fields={}".format(
        urllib.parse.quote(jql), int(limit), FIELD_SUBSET))
    rows = []
    for raw in data.get("issues") or []:
        issue = issue_fields(raw)
        issue["url"] = "{}/browse/{}".format(cfg["siteUrl"].rstrip("/"), issue["key"])
        if not issue["projectKey"]:
            issue["projectKey"] = pkey
        rows.append(issue)
    for issue in rows[:5]:
        try:
            hist = jira_get(cfg, "/rest/api/3/issue/{}/changelog?maxResults=5"
                                 .format(issue["key"]))
        except RuntimeError:
            continue
        values = hist.get("values") or []
        if not values:
            continue
        last = values[-1]
        parts = []
        for item in last.get("items") or []:
            field = item.get("field") or ""
            if item.get("fromString") or item.get("toString"):
                parts.append("{}: {} -> {}".format(
                    field, item.get("fromString") or "–", item.get("toString") or "–"))
            elif field:
                parts.append(field)
        if parts:
            issue["lastChange"] = " · ".join(parts[:3])
            issue["lastChangeMs"] = parse_iso(last.get("created"))
    return rows


def real_history(cfg, key, limit=20):
    """The issue's changelog, oldest last, as {authorName, createdMs, items}."""
    out = []
    try:
        data = jira_get(cfg, "/rest/api/3/issue/{}/changelog?maxResults={}".format(key, limit))
    except RuntimeError:
        return out
    for row in data.get("values") or []:
        parts = []
        for item in row.get("items") or []:
            field = item.get("field") or ""
            if item.get("fromString") or item.get("toString"):
                parts.append("{}: {} -> {}".format(field, item.get("fromString") or "–",
                                                   item.get("toString") or "–"))
            elif field:
                parts.append(field)
        if not parts:
            continue
        out.append({
            "authorName": (row.get("author") or {}).get("displayName") or "",
            "createdMs": parse_iso(row.get("created")),
            "text": " · ".join(parts[:3]),
        })
    return out


def real_dev_status(cfg, key):
    """Git/CI panel for an issue, plus its history.

    The dev-status API only answers for applications the site has connected
    (GitHub, Bitbucket, GitLab...). The summary endpoint always answers, so it
    decides whether asking for details is worth anything - on a site with no
    integration the view says so instead of showing four empty lists."""
    out = {"configured": False, "counts": {}, "pullRequests": [], "branches": [],
           "commits": [], "builds": [], "history": [], "applications": []}
    issue_id = ""
    try:
        issue_id = jira_get(cfg, "/rest/api/3/issue/{}?fields=id".format(key)).get("id") or ""
    except RuntimeError:
        issue_id = ""
    if issue_id:
        summary = {}
        try:
            summary = jira_get(cfg, "/rest/dev-status/1.0/issue/summary?issueId={}"
                                    .format(issue_id))
        except RuntimeError:
            summary = {}
        counts = {}
        for data_type, row in (summary.get("summary") or {}).items():
            overall = (row or {}).get("overall") or {}
            counts[data_type] = int(overall.get("count") or 0)
            for app in (overall.get("byInstanceType") or {}).keys():
                if app not in out["applications"]:
                    out["applications"].append(app)
        out["counts"] = counts
        out["configured"] = any(counts.values())
        if out["configured"]:
            for app in (out["applications"] or ["GitHub", "Bitbucket", "GitLab"]):
                for data_type in ("pullrequest", "branch", "commit", "build"):
                    try:
                        detail = jira_get(cfg,
                            "/rest/dev-status/1.0/issue/detail?issueId={}&applicationType={}"
                            "&dataType={}".format(issue_id, urllib.parse.quote(app), data_type))
                    except RuntimeError:
                        continue
                    for block in detail.get("detail") or []:
                        for row in block.get("pullRequests") or []:
                            out["pullRequests"].append({
                                "id": str(row.get("id") or ""),
                                "title": row.get("title") or row.get("name") or "",
                                "status": row.get("status") or "",
                                "url": row.get("url") or "",
                                "author": row.get("author") or "",
                                "updatedMs": parse_iso(row.get("lastUpdate")),
                            })
                        for row in block.get("branches") or []:
                            out["branches"].append({
                                "name": row.get("name") or "",
                                "url": row.get("url") or "",
                                "lastCommit": (row.get("lastCommit") or {}).get("message") or "",
                            })
                        for row in block.get("commits") or []:
                            out["commits"].append({
                                "id": str(row.get("id") or ""),
                                "message": row.get("message") or "",
                                "url": row.get("url") or "",
                                "author": row.get("author") or "",
                                "updatedMs": parse_iso(row.get("timestamp")),
                            })
                        for row in block.get("builds") or []:
                            out["builds"].append({
                                "name": row.get("name") or row.get("displayName") or "",
                                "status": row.get("status") or "",
                                "url": row.get("url") or "",
                            })
    out["history"] = real_history(cfg, key)
    return out


def real_report(cfg, bid, sprint_id):
    """Burndown series + sprint report + velocity for one sprint.

    Jira's chart endpoint (greenhopper) is the only source for the day-by-day
    series; the sprint report supplies the end state. Both are best-effort, so
    a sprint without data still renders its summary."""
    sid = str(sprint_id or "").strip()
    out = {"sprintId": sid, "points": [], "summary": {}, "velocity": []}
    if not sid:
        return out

    series_start = 0
    series_end = 0
    try:
        chart = jira_get(cfg, "/rest/greenhopper/1.0/rapid/charts/scopechangeburndownchart"
                              "?rapidViewId={}&sprintId={}".format(bid, sid))
        series_start = int(chart.get("startTime") or 0)
        series_end = int(chart.get("endTime") or 0)
        changes = chart.get("changes") or {}
        state = {}
        for stamp in sorted(changes, key=lambda s: int(s)):
            for event in changes[stamp] or []:
                key = event.get("key") or ""
                if not key:
                    continue
                column = event.get("column") or {}
                state[key] = bool(column.get("notDone", True))
            remaining = 0
            for not_done in state.values():
                if not_done:
                    remaining += 1
            out["points"].append({"atMs": int(stamp), "remaining": remaining,
                                  "total": len(state)})
    except RuntimeError:
        out["points"] = []

    try:
        report = jira_get(cfg, "/rest/greenhopper/1.0/rapid/charts/sprintreport"
                               "?rapidViewId={}&sprintId={}".format(bid, sid))
        contents = report.get("contents") or {}
        meta = report.get("sprint") or {}
        out["summary"] = {
            "name": meta.get("name") or "",
            "state": (meta.get("state") or "").lower(),
            "startMs": series_start,
            "endMs": series_end,
            "completed": len(contents.get("completedIssues") or []),
            "notCompleted": len(contents.get("issuesNotCompletedInCurrentSprint") or []),
            "punted": len(contents.get("puntedIssues") or []),
            "added": len(contents.get("issueKeysAddedDuringSprint") or {}),
            "completedKeys": [i.get("key") for i in (contents.get("completedIssues") or [])],
            "notCompletedKeys": [i.get("key") for i in
                                 (contents.get("issuesNotCompletedInCurrentSprint") or [])],
        }
    except RuntimeError:
        pass

    try:
        vel = jira_get(cfg, "/rest/greenhopper/1.0/rapid/charts/velocity?rapidViewId={}"
                            .format(bid))
        entries = vel.get("velocityStatEntries") or {}
        for sprint in vel.get("sprints") or []:
            sid_key = str(sprint.get("id") or "")
            entry = entries.get(sid_key) or {}
            out["velocity"].append({
                "id": sid_key,
                "name": sprint.get("name") or "",
                "estimates": int((entry.get("estimated") or {}).get("value") or 0),
                "completed": int((entry.get("completed") or {}).get("value") or 0),
            })
    except RuntimeError:
        pass
    return out


# ----------------------------------------------------------------- mock

MOCK_PROJECTS = [
    {"key": "WEB", "name": "Web Platform"},
    {"key": "MOB", "name": "Mobile App"},
]

# statuses: id -> (name, category)
MOCK_PRIORITIES = ["Highest", "High", "Medium", "Low"]

MOCK_STATUSES = {
    "todo": ("To Do", "new"),
    "progress": ("In Progress", "indeterminate"),
    "review": ("In Review", "indeterminate"),
    "done": ("Done", "done"),
}

# Allowed moves per status id, in board order. (Jira would give transitions;
# the mock uses a simple linear-ish workflow.)
MOCK_TRANSITIONS = {
    "todo": ["progress", "done"],
    "progress": ["review", "done", "todo"],
    "review": ["done", "progress", "todo"],
    "done": [],
}

# key -> issue seed. status is the starting column id.
MOCK_ISSUES = [
    # WEB kanban
    ("WEB-41", "WEB", "todo", "Redesign the settings page navigation", "Story", "Alex Weström", "alex@westrom.dev", "High", 3),
    ("WEB-42", "WEB", "progress", "Fix flaky CI for the e2e suite", "Bug", "Alex Weström", "alex@westrom.dev", "Highest", 2),
    ("WEB-43", "WEB", "progress", "Add dark mode toggle to the header", "Story", "Maja Lind", "maja@westrom.dev", "Medium", 5),
    ("WEB-44", "WEB", "review", "Migrate build pipeline to the new runner", "Task", "Alex Weström", "alex@westrom.dev", "High", None),
    ("WEB-45", "WEB", "review", "Accessibility pass on forms", "Story", "Noah Berg", "noah@westrom.dev", "Medium", 8),
    ("WEB-46", "WEB", "todo", "Write onboarding docs for contributors", "Task", "Alex Weström", "alex@westrom.dev", "Low", None),
    ("WEB-47", "WEB", "done", "Ship cookie consent banner", "Story", "Alex Weström", "alex@westrom.dev", "Medium", 5),
    ("WEB-48", "WEB", "done", "Reduce bundle size below 200 kB", "Task", "Maja Lind", "maja@westrom.dev", "Medium", None),
    # MOB scrum - in the active sprint
    ("MOB-21", "MOB", "progress", "Pull-to-refresh on the feed", "Story", "Alex Weström", "alex@westrom.dev", "High", 5),
    ("MOB-22", "MOB", "todo", "Offline cache for search results", "Story", "Alex Weström", "alex@westrom.dev", "Medium", 3),
    ("MOB-23", "MOB", "review", "Deep links into the article view", "Story", "Elin Åkerman", "elin@westrom.dev", "High", 5),
    ("MOB-24", "MOB", "progress", "Crash on startup with empty account", "Bug", "Alex Weström", "alex@westrom.dev", "Highest", 2),
    ("MOB-25", "MOB", "done", "Biometric unlock", "Story", "Elin Åkerman", "elin@westrom.dev", "Medium", 8),
    ("MOB-26", "MOB", "todo", "Localize notifications", "Task", "Alex Weström", "alex@westrom.dev", "Low", None),
    # MOB scrum - backlog (not yet started)
    ("MOB-31", "MOB", "todo", "Widget for the home screen", "Story", "Alex Weström", "alex@westrom.dev", "Medium", 8),
    ("MOB-32", "MOB", "todo", "Support iPad layout", "Story", "Elin Åkerman", "elin@westrom.dev", "Low", 13),
    ("MOB-33", "MOB", "todo", "Analytics events audit", "Task", "Noah Berg", "noah@westrom.dev", "Medium", None),
    ("MOB-34", "MOB", "todo", "Empty states across the app", "Story", "Alex Weström", "alex@westrom.dev", "Medium", 3),
    ("MOB-35", "MOB", "todo", "Switch CDN provider", "Task", "Noah Berg", "noah@westrom.dev", "High", None),
]

MOCK_DESCRIPTIONS = {
    "WEB-41": "The settings page has outgrown a single scrolling list. Group entries under clear sections and keep the current focus in the sidebar.",
    "WEB-44": "The old runner is being retired at the end of the quarter. Move the build to the new one and keep the green checkmark.",
    "MOB-22": "Search results should survive going offline. Cache the last query and its results, and mark them stale when the network returns.",
}

# Sprint windows are cosmetic and relative to today, so the mock timeline and
# the mock report always have something to draw.
MOCK_BOARDS = [
    {
        "id": "1",
        "name": "Web Platform",
        "type": "kanban",
        "projectKey": "WEB",
        "statuses": ["todo", "progress", "review", "done"],
        "sprints": [],
        "backlogKeys": [],
    },
    {
        "id": "2",
        "name": "Mobile App",
        "type": "scrum",
        "projectKey": "MOB",
        "statuses": ["todo", "progress", "review", "done"],
        "sprints": [
            {"id": "7", "name": "Sprint 7", "state": "active",
             "startDaysAgo": 7, "lengthDays": 14},
            {"id": "8", "name": "Sprint 8", "state": "future",
             "startDaysAgo": -7, "lengthDays": 14},
        ],
        "sprintAssignments": {
            "7": ["MOB-21", "MOB-22", "MOB-23", "MOB-24", "MOB-25", "MOB-26"],
            "8": ["MOB-31", "MOB-32"],
        },
        "backlogKeys": ["MOB-33", "MOB-34", "MOB-35"],
    },
]

# Comments the mock starts with, so the detail page has something to show.
MOCK_COMMENTS = {
    "MOB-22": [
        ("Elin Åkerman", "elin@westrom.dev", "Ska cachen tömmas när kontot byter?"),
        ("Alex Weström", "alex@westrom.dev", "Ja - töm listan vid utloggning."),
    ],
    "MOB-24": [
        ("Noah Berg", "noah@westrom.dev", "Repro: tomt konto + flygplansläge."),
    ],
}

MOCK_STATE_VERSION = 2


def mock_sprint_row(sprint):
    """A sprint the way the UI sees it: absolute bounds derived from the
    cosmetic offsets, so the mock looks alive whatever the date is."""
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=sprint.get("startDaysAgo", 0))
    end = start + datetime.timedelta(days=sprint.get("lengthDays", 14))
    return {
        "id": str(sprint.get("id") or ""),
        "name": sprint.get("name") or "",
        "state": sprint.get("state") or "future",
        "startMs": int(start.timestamp() * 1000),
        "endMs": int(end.timestamp() * 1000),
    }


def mock_seed_sprint_map():
    """key -> sprint id for the seeded dataset ('' means the backlog)."""
    out = {}
    for board in MOCK_BOARDS:
        for sprint_id, keys in (board.get("sprintAssignments") or {}).items():
            for key in keys:
                out[key] = str(sprint_id)
        for key in board.get("backlogKeys") or []:
            out[key] = ""
    return out


def mock_seed_comments():
    now = datetime.datetime.now(datetime.timezone.utc)
    out = {}
    for key, rows in MOCK_COMMENTS.items():
        comments = []
        for idx, (name, email, body) in enumerate(rows):
            created = int((now - datetime.timedelta(hours=(len(rows) - idx) * 5)).timestamp() * 1000)
            comments.append({
                "id": "c-{}-{}".format(key, idx + 1),
                "authorName": name,
                "authorEmail": email,
                "body": body,
                "createdMs": created,
                "updatedMs": created,
            })
        out[key] = comments
    return out


def mock_migrate(state):
    """State from before sprints were per-issue has no sprintId; seed it from
    the board definitions instead of throwing the user's mock edits away."""
    seeds = mock_seed_sprint_map()
    for key, issue in state.get("issues", {}).items():
        if "sprintId" not in issue:
            issue["sprintId"] = seeds.get(key, "")
    state["version"] = MOCK_STATE_VERSION
    state.setdefault("comments", mock_seed_comments())
    save_mock_state(state)


def mock_state():
    """Load the persisted mock state, creating it from the seeds on first run.

    Older state has no per-issue sprint and no comments; it is migrated rather
    than discarded, so a mock board keeps whatever the user changed in it."""
    if os.path.exists(MOCK_STATE_PATH):
        try:
            with open(MOCK_STATE_PATH, encoding="utf-8") as fh:
                state = json.load(fh)
            if isinstance(state, dict) and isinstance(state.get("issues"), dict):
                if int(state.get("version") or 1) < MOCK_STATE_VERSION:
                    mock_migrate(state)
                state.setdefault("comments", {})
                return state
        except (OSError, ValueError):
            pass

    issues = {}
    for seed in MOCK_ISSUES:
        key, project, status_id, summary, type_name, name, email, priority, points = seed
        issues[key] = {
            "key": key,
            "projectKey": project,
            "statusId": status_id,
            "summary": summary,
            "typeName": type_name,
            "assigneeName": name,
            "assigneeEmail": email,
            "priorityName": priority,
            "storyPoints": points,
            "description": MOCK_DESCRIPTIONS.get(key, ""),
        }
    seeds = mock_seed_sprint_map()
    for key, issue in issues.items():
        issue["sprintId"] = seeds.get(key, "")
    state = {"version": MOCK_STATE_VERSION, "issues": issues,
             "comments": mock_seed_comments()}
    save_mock_state(state)
    return state


def save_mock_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(MOCK_STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def mock_reset():
    if os.path.exists(MOCK_STATE_PATH):
        os.remove(MOCK_STATE_PATH)
    # The watcher baseline is shared with real mode; only drop it when the mock
    # board is what is being watched, or a reset would re-notify the real one.
    if load_config().get("mode") != "real" and os.path.exists(WATCH_PATH):
        os.remove(WATCH_PATH)
    mock_state()


def mock_issue(key, state):
    raw = state["issues"][key]
    status_id = raw["statusId"]
    status_name, category = MOCK_STATUSES[status_id]
    sprint_id = str(raw.get("sprintId") or "")
    sprint_name = ""
    for board in MOCK_BOARDS:
        if board["projectKey"] != raw["projectKey"]:
            continue
        for sprint in board.get("sprints") or []:
            if str(sprint.get("id")) == sprint_id:
                sprint_name = sprint.get("name") or ""
    # Deterministic pseudo-timestamp per key: the mock activity feed needs an
    # order, and it must be the same on every run.
    age_hours = sum(ord(ch) for ch in key) % 36
    now = datetime.datetime.now(datetime.timezone.utc)
    updated_ms = int((now - datetime.timedelta(hours=age_hours)).timestamp() * 1000)
    return {
        "key": key,
        "summary": raw["summary"],
        "typeName": raw["typeName"],
        "typeIcon": "",
        "statusId": status_id,
        "statusName": status_name,
        "statusCategory": category,
        "assigneeEmail": raw["assigneeEmail"],
        "assigneeName": raw["assigneeName"],
        "priorityName": raw["priorityName"],
        "storyPoints": raw["storyPoints"],
        "updatedMs": updated_ms,
        "description": raw["description"],
        "url": "https://example.atlassian.net/browse/{}".format(key),
        "projectKey": raw["projectKey"],
        "sprintId": sprint_id,
        "sprintName": sprint_name,
        "startMs": 0,
        "dueMs": 0,
        "parentKey": "",
        "parentSummary": "",
    }


def mock_snapshot(cfg):
    state = mock_state()
    account = {
        "email": cfg.get("email") or "alex@westrom.dev",
        "displayName": "Alex Weström",
        "siteUrl": "mock",
        "connected": True,
    }
    boards = []
    for board in MOCK_BOARDS:
        sprints = [mock_sprint_row(s) for s in board.get("sprints") or []]
        sprint = None
        for row in sprints:
            if row["state"] == "active":
                sprint = row
                break
        columns = [{
            "name": MOCK_STATUSES[sid][0],
            "statusId": sid,
            "statusName": MOCK_STATUSES[sid][0],
        } for sid in board["statuses"]]

        # A scrum board splits the project's issues into sprint(s) and the
        # backlog; a kanban board has no sprints, so everything sits on the
        # board. mock_assign_sprint moves an issue between the two.
        issues = []
        backlog = []
        for key in state["issues"]:
            issue = state["issues"][key]
            if issue["projectKey"] != board["projectKey"]:
                continue
            row = mock_issue(key, state)
            if board["type"] == "kanban" or row["sprintId"]:
                issues.append(row)
            else:
                backlog.append(row)

        boards.append({
            "id": board["id"],
            "name": board["name"],
            "type": board["type"],
            "projectKey": board["projectKey"],
            "columns": columns,
            "sprint": sprint,
            "sprints": sprints,
            "issues": issues,
            "backlog": backlog,
            "canAdd": True,
            "canDelete": True,
        })

    return {
        "schema": SCHEMA,
        "ok": True,
        "error": None,
        "generatedAt": utc_now(),
        "mode": "mock",
        "account": account,
        "projects": MOCK_PROJECTS,
        "boards": boards,
    }


def mock_transitions(state, key):
    issue = state["issues"].get(key)
    if not issue:
        raise RuntimeError("Okänt ärende {}".format(key))
    out = []
    for target_id in MOCK_TRANSITIONS[issue["statusId"]]:
        status_name, category = MOCK_STATUSES[target_id]
        out.append({
            "id": "tr-{}-{}".format(key, target_id),
            "name": "Move to {}".format(status_name),
            "toStatusId": target_id,
            "toStatusName": status_name,
        })
    return out


def mock_move(state, key, target):
    if key not in state["issues"]:
        raise RuntimeError("Okänt ärende {}".format(key))
    issue = state["issues"][key]
    targets = [t["toStatusId"] for t in mock_transitions(state, key)]
    # Accept either a status id ("progress") or a status name ("In Progress").
    resolved = None
    if target in targets:
        resolved = target
    else:
        for sid, (sname, _cat) in MOCK_STATUSES.items():
            if sname.lower() == str(target).lower() and sid in targets:
                resolved = sid
                break
    if resolved is None:
        raise RuntimeError(
            "Kan inte flytta {} från {} vidare till '{}'. Tillgängliga: {}".format(
                key, MOCK_STATUSES[issue["statusId"]][0], target,
                ", ".join(MOCK_STATUSES[t][0] for t in targets) or "inga"))
    issue["statusId"] = resolved
    save_mock_state(state)
    return resolved


def mock_next_key(state, project):
    highest = 0
    prefix = str(project).upper() + "-"
    for key in state["issues"]:
        if key.startswith(prefix):
            try:
                highest = max(highest, int(key[len(prefix):]))
            except (ValueError, TypeError):
                pass
    return "{}{}".format(prefix, highest + 1)


def mock_create(state, board_id, payload):
    summary = (payload.get("summary") or "").strip()
    if not summary:
        raise RuntimeError("Sammanfattning saknas.")
    board = None
    for candidate in MOCK_BOARDS:
        if str(candidate["id"]) == str(board_id):
            board = candidate
            break
    if not board:
        raise RuntimeError("Okänd tavla {} i mock-läget.".format(board_id))
    project = board["projectKey"]
    statuses = board.get("statuses") or []
    status_id = str(payload.get("statusId") or "").strip() or statuses[0]
    if status_id not in statuses:
        raise RuntimeError(
            "Statusen '{}' finns inte på tavlan. Tillgängliga: {}".format(
                status_id, ", ".join(MOCK_STATUSES[s][0] for s in statuses)))

    key = mock_next_key(state, project)
    cfg = load_config()
    email = cfg.get("email") or "alex@westrom.dev"
    display = "Alex Weström"
    issue = {
        "key": key,
        "projectKey": project,
        "statusId": status_id,
        "summary": summary,
        "typeName": (payload.get("typeName") or "Task").strip() or "Task",
        "assigneeName": display,
        "assigneeEmail": email,
        "priorityName": (payload.get("priorityName") or "Medium").strip() or "Medium",
        "storyPoints": payload.get("storyPoints"),
        "description": (payload.get("description") or "").strip(),
        # A new issue starts on the backlog; move it into a sprint from the
        # timeline (or the detail page) when it is planned.
        "sprintId": "",
    }
    state["issues"][key] = issue
    save_mock_state(state)
    return key


def mock_delete(state, key):
    if key not in state["issues"]:
        raise RuntimeError("Okänt ärende {}".format(key))
    del state["issues"][key]
    save_mock_state(state)


def mock_assign_sprint(state, key, sprint_id):
    """Sprint planning in the mock: an id moves the issue into that sprint,
    anything else (or 'backlog') puts it back on the backlog."""
    if key not in state["issues"]:
        raise RuntimeError("Okänt ärende {}".format(key))
    target = str(sprint_id or "").strip()
    if target and target.lower() != "backlog":
        known = [str(s["id"]) for board in MOCK_BOARDS for s in board.get("sprints") or []]
        if target not in known:
            raise RuntimeError("Okänd sprint {}. Tillgängliga: {}".format(
                target, ", ".join(known) or "inga"))
    state["issues"][key]["sprintId"] = "" if target.lower() in ("", "backlog") else target
    save_mock_state(state)
    return state["issues"][key]["sprintId"]


def mock_comments(state, key):
    if key not in state["issues"]:
        raise RuntimeError("Okänt ärende {}".format(key))
    return list((state.get("comments") or {}).get(key) or [])


def mock_add_comment(state, key, text):
    if key not in state["issues"]:
        raise RuntimeError("Okänt ärende {}".format(key))
    body = str(text or "").strip()
    if not body:
        raise RuntimeError("Kommentaren är tom.")
    comments = state.setdefault("comments", {}).setdefault(key, [])
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    cfg = load_config()
    comments.append({
        "id": "c-{}-{}".format(key, len(comments) + 1),
        "authorName": "Alex Weström",
        "authorEmail": cfg.get("email") or "alex@westrom.dev",
        "body": body,
        "createdMs": now,
        "updatedMs": now,
    })
    save_mock_state(state)
    return comments


def mock_options(pkey):
    """Mock counterpart of real_options: the people already in the dataset."""
    people = []
    for seed in MOCK_ISSUES:
        key, project, _sid, _summary, _type, name, email, _prio, _points = seed
        if pkey and project != pkey:
            continue
        if not any(p["displayName"] == name for p in people):
            people.append({
                "accountId": "mock-" + email.split("@")[0],
                "displayName": name,
                "email": email,
                "active": True,
            })
    return {
        "people": people,
        "priorities": MOCK_PRIORITIES,
        "types": [{"id": "mock-task", "name": "Task"},
                  {"id": "mock-story", "name": "Story"},
                  {"id": "mock-bug", "name": "Bug"}],
    }


def mock_update(state, key, payload):
    issue = state["issues"].get(key)
    if not issue:
        raise RuntimeError("Okänt ärende {}".format(key))
    if "summary" in payload:
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            raise RuntimeError("Sammanfattningen får inte vara tom.")
        issue["summary"] = summary
    if "description" in payload:
        issue["description"] = str(payload.get("description") or "")
    if "priorityName" in payload:
        issue["priorityName"] = str(payload.get("priorityName") or "")
    if "assigneeAccountId" in payload or "assigneeEmail" in payload:
        email = str(payload.get("assigneeEmail") or "")
        account = str(payload.get("assigneeAccountId") or "")
        if not email and not account:
            issue["assigneeName"] = ""
            issue["assigneeEmail"] = ""
        else:
            for person in mock_options(issue["projectKey"])["people"]:
                if (email and person["email"] == email) or (account and person["accountId"] == account):
                    issue["assigneeName"] = person["displayName"]
                    issue["assigneeEmail"] = person["email"]
                    break
    if "storyPoints" in payload:
        raw = payload.get("storyPoints")
        issue["storyPoints"] = None if raw in (None, "") else float(raw)
    save_mock_state(state)


def mock_activity(state, pkey, limit=25):
    """Newest first, using the deterministic per-key timestamp."""
    rows = []
    for key, issue in state["issues"].items():
        if pkey and issue["projectKey"] != pkey:
            continue
        row = mock_issue(key, state)
        status_name = MOCK_STATUSES[issue["statusId"]][0]
        row["lastChange"] = ("status: To Do -> {}".format(status_name)
                             if issue["statusId"] != "todo" else "skapad på backloggen")
        row["lastChangeMs"] = row["updatedMs"]
        rows.append(row)
    rows.sort(key=lambda r: r["updatedMs"], reverse=True)
    return rows[:limit]


MOCK_DEV = {
    "MOB-22": {
        "pullRequests": [
            {"id": "412", "title": "Cache search results offline", "status": "OPEN",
             "url": "https://example.invalid/pull/412", "author": "Alex Weström", "updatedMs": 0},
        ],
        "branches": [{"name": "feature/offline-cache", "url": "", "lastCommit": "Wire the cache into search"}],
        "commits": [
            {"id": "a1b2c3d", "message": "Cache the last query and its results", "url": "",
             "author": "Alex Weström", "updatedMs": 0},
            {"id": "e4f5a6b", "message": "Mark cached results stale on reconnect", "url": "",
             "author": "Alex Weström", "updatedMs": 0},
        ],
        "builds": [{"name": "CI · ios-debug", "status": "SUCCESSFUL", "url": ""}],
    },
    "MOB-24": {
        "commits": [{"id": "9f8e7d6", "message": "Guard against a null account on boot", "url": "",
                     "author": "Noah Berg", "updatedMs": 0}],
    },
}


def mock_dev_status(state, key):
    """Seeded git/CI data for two mock issues, empty for the rest, plus the
    issue's (mock) history - so both the populated and the empty state render."""
    issue = state["issues"].get(key)
    if not issue:
        raise RuntimeError("Okänt ärende {}".format(key))
    seeded = MOCK_DEV.get(key) or {}
    out = {"configured": bool(seeded), "counts": {}, "pullRequests": [], "branches": [],
           "commits": [], "builds": [], "applications": ["GitHub"],
           "history": [{"authorName": "Alex Weström", "createdMs": 0,
                        "text": "status: To Do -> {}".format(MOCK_STATUSES[issue["statusId"]][0])}]}
    for data_type in ("pullrequest", "branch", "commit", "build"):
        out["counts"][data_type] = 0
    for field in ("pullRequests", "branches", "commits", "builds"):
        rows = [dict(row) for row in (seeded.get(field) or [])]
        out[field] = rows
    out["counts"]["pullrequest"] = len(out["pullRequests"])
    out["counts"]["branch"] = len(out["branches"])
    out["counts"]["commit"] = len(out["commits"])
    out["counts"]["build"] = len(out["builds"])
    return out


def mock_report(state, board_id, sprint_id):
    """A deterministic burndown for the mock sprint: a straight line from the
    sprint's opening scope down to whatever is still open."""
    board = None
    for candidate in MOCK_BOARDS:
        if str(candidate["id"]) == str(board_id):
            board = candidate
            break
    out = {"sprintId": str(sprint_id), "points": [], "summary": {}, "velocity": []}
    row = None
    for sprint in (board or {}).get("sprints") or []:
        if str(sprint["id"]) == str(sprint_id):
            row = mock_sprint_row(sprint)
            break
    if not row:
        return out

    keys = [k for k, i in state["issues"].items()
            if str(i.get("sprintId") or "") == str(sprint_id)]
    done_keys = [k for k in keys
                 if MOCK_STATUSES[state["issues"][k]["statusId"]][1] == "done"]
    total = len(keys)
    done = len(done_keys)
    steps = 14
    span = max(1, row["endMs"] - row["startMs"])
    for step in range(steps + 1):
        at = row["startMs"] + int(span * step / steps)
        out["points"].append({
            "atMs": at,
            "remaining": max(0, total - int(round(done * step / steps))),
            "total": total,
        })
    out["summary"] = {
        "name": row["name"],
        "state": row["state"],
        "startMs": row["startMs"],
        "endMs": row["endMs"],
        "completed": done,
        "notCompleted": total - done,
        "punted": 0,
        "added": total,
        "completedKeys": done_keys,
        "notCompletedKeys": [k for k in keys if k not in done_keys],
    }
    out["velocity"] = [{"id": row["id"], "name": row["name"],
                        "estimates": total * 3, "completed": done * 3}]
    return out


# ----------------------------------------------------------------- status

# Watcher: notifies about external board changes by diffing the last known
# state. A per-issue fingerprint (the fields we display) is stored on disk;
# the next poll reports whatever moved/added/updated/removed since.

WATCH_FIELD_LABELS = {
    "summary": "Summary",
    "assigneeEmail": "Assignee",
    "priorityName": "Priority",
    "typeName": "Type",
}


def watched_rows(snap, cfg):
    """Rows (issues + backlog) of the tracked board. When the user has picked
    a board only that board is watched; otherwise every board counts so the
    notifier works out of the box."""
    wanted = str(cfg.get("selectedBoardId") or "")
    rows = []
    for board in snap.get("boards") or []:
        if wanted and str(board.get("id")) != wanted:
            continue
        for issue in (board.get("issues") or []) + (board.get("backlog") or []):
            rows.append(issue)
    return rows


def row_signature(row):
    return (
        row.get("summary") or "",
        row.get("statusName") or "",
        row.get("statusId") or "",
        row.get("assigneeEmail") or "",
        row.get("priorityName") or "",
        row.get("typeName") or "",
    )


def watched_scope(cfg):
    """Which part of the snapshot the baseline covers: the selected board id,
    or '' meaning all boards."""
    return str(cfg.get("selectedBoardId") or "")


def baseline_entries(cfg, snap):
    entries = {}
    for row in watched_rows(snap, cfg):
        entries[row.get("key")] = {
            "sig": list(row_signature(row)),
            "summary": row.get("summary") or "",
            "statusName": row.get("statusName") or "",
        }
    return entries


def load_baseline():
    try:
        with open(WATCH_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("issues"), dict):
            return data["issues"], data.get("wanted")
    except (OSError, ValueError):
        pass
    return None, None


def store_baseline(entries, wanted):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(WATCH_PATH, "w", encoding="utf-8") as fh:
        json.dump({"savedAt": utc_now(), "wanted": wanted, "issues": entries},
                  fh, ensure_ascii=False, indent=2)
    try:
        os.chmod(WATCH_PATH, 0o600)
    except OSError:
        pass


def diff_events(prev, cur):
    events = []
    for key in sorted(set(prev) | set(cur)):
        old = prev.get(key)
        new = cur.get(key)
        if old is None:
            events.append({"type": "added", "key": key,
                           "summary": new["summary"], "statusName": new["statusName"],
                           "changed": []})
        elif new is None:
            events.append({"type": "removed", "key": key,
                           "summary": old["summary"], "statusName": old["statusName"],
                           "changed": []})
        elif old["sig"] != new["sig"]:
            if old["sig"][1] != new["sig"][1] or old["sig"][2] != new["sig"][2]:
                events.append({"type": "moved", "key": key,
                               "summary": new["summary"],
                               "fromStatus": old["statusName"],
                               "toStatus": new["statusName"],
                               "changed": []})
            else:
                changed = []
                for idx, label in (
                    (0, "Summary"), (3, "Assignee"), (4, "Priority"), (5, "Type")):
                    if old["sig"][idx] != new["sig"][idx]:
                        changed.append(label)
                events.append({"type": "updated", "key": key,
                               "summary": new["summary"], "statusName": new["statusName"],
                               "changed": changed})
    return events


def notify_text(events):
    if not events:
        return None
    if len(events) == 1:
        e = events[0]
        if e["type"] == "moved":
            return ("OmaJIRA · {} moved to {}".format(e["key"], e["toStatus"]),
                    e["summary"])
        if e["type"] == "added":
            return ("OmaJIRA · new issue {}".format(e["key"]),
                    "{}  ·  {}".format(e["summary"], e["statusName"]))
        if e["type"] == "removed":
            return ("OmaJIRA · {} removed".format(e["key"]), e["summary"])
        detail = " · ".join(e["changed"]) if e["changed"] else "changed"
        return ("OmaJIRA · {} updated".format(e["key"]),
                "{}  ·  {}".format(e["summary"], detail))
    lines = []
    for e in events:
        if e["type"] == "moved":
            lines.append("{} → {}: {}".format(e["key"], e["toStatus"], e["summary"]))
        elif e["type"] == "added":
            lines.append("{} + {}: {}".format(e["key"], e["statusName"], e["summary"]))
        elif e["type"] == "removed":
            lines.append("{} − removed: {}".format(e["key"], e["summary"]))
        else:
            lines.append("{} ~ {}".format(e["key"], e["summary"]))
    return ("OmaJIRA · {} updates".format(len(events)), "\n".join(lines))


def raise_notification(summary, body):
    try:
        subprocess.run(
            ["notify-send", "-a", "OmaJIRA", "-u", "normal", "-t", "8000", summary, body],
            check=False, timeout=10)
    except Exception:
        pass


def watch_payload(cfg):
    try:
        snap = real_snapshot(cfg) if cfg.get("mode") == "real" else mock_snapshot(cfg)
    except RuntimeError as exc:
        err_payload("Kunde inte bevaka: {}".format(exc), mode=cfg.get("mode"))
    wanted = watched_scope(cfg)
    prev, prev_wanted = load_baseline()
    cur = baseline_entries(cfg, snap)
    if prev is None or prev_wanted != wanted:
        # First run or the watched scope changed: prime quietly, so switching
        # boards never replays the previous scope as "removed" issues.
        store_baseline(cur, wanted)
        payload({"events": [], "changed": 0, "watching": True,
                 "primed": prev is not None})
    events = diff_events(prev, cur)
    store_baseline(cur, wanted)
    notif = notify_text(events)
    if notif:
        raise_notification(*notif)
    payload({"events": events, "changed": len(events), "watching": True,
             "primed": True, "notification": bool(notif)})


def capture_baseline(cfg, snap):
    """Record the current board state silently (e.g. right after the user made
    a change themselves), so their own edits never notify."""
    wanted = watched_scope(cfg)
    entries = baseline_entries(cfg, snap)
    if entries:
        store_baseline(entries, wanted)

# ------------------------------------------------------------- skrivskydd
#
# Fyra nät, osynliga i normalfallet men avgörande när något går fel:
#
#   1. **Journal.** Varje skrivning lämnar en rad i `jira-actions.log` (JSON per
#      rad): vad, vilket ärende, av vem och när — även försök som nekades. Utan
#      den går det inte att i efterhand svara på "vad gjorde verktyget?".
#   2. **Lokal papperskorg.** Före varje radering sparas hela ärendet (fält +
#      kommentarer) som JSON i `jira-trash/`. Jira Cloud har ingen papperskorg
#      för ärenden, så den filen är det enda som finns kvar. Går kopian inte att
#      skriva **nekas raderingen** — vi raderar aldrig något vi inte först kunnat
#      spara.
#   3. **Kvotvakt.** Fler än `DELETE_QUOTA` raderingar inom `DELETE_WINDOW_SECS`
#      nekas, med besked om varför. En skur (av misstag eller i en loop) blir då
#      tre ärenden och ett tydligt felmeddelande i stället för en tyst
#      utrensning.
#   4. **Kvitto.** Efter varje skrivning läses ändringen tillbaka och jämförs med
#      vad som beställdes. Stämmer det inte rapporteras det som ett fel — en
#      skrivning får aldrig se ut att ha lyckats när den inte gjorde det.

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def log_action(cfg, action, key, detail="", ok=True, refused=False,
               reason="", before=None, after=None, extra=None):
    """En rad per skrivning. Append-only; en trasig rad får aldrig stoppa en skrivning."""
    entry = {
        "at": iso_now(),
        "action": action,
        "key": key or "",
        "mode": (cfg or {}).get("mode") or "",
        "user": (cfg or {}).get("email") or "",
        "detail": detail or "",
        "ok": bool(ok),
    }
    if refused:
        entry["refused"] = True
        entry["reason"] = reason or ""
    if before is not None:
        entry["before"] = before
    if after is not None:
        entry["after"] = after
    if extra:
        entry["extra"] = extra
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(ACTION_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.chmod(ACTION_LOG_PATH, 0o600)
    except OSError:
        # Journalen är ett skyddsnät, inte en förutsättning: kan den inte skrivas
        # går skrivningen ändå. Felet syns i journal-kommandot.
        pass
    return entry


def read_actions(limit=200):
    """Journalen, nyaste först. Trasiga rader hoppas över i stället för att krascha."""
    rows = []
    if not os.path.exists(ACTION_LOG_PATH):
        return rows
    try:
        with open(ACTION_LOG_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return rows
    rows.reverse()
    return rows[:int(limit)]


def deletes_in_window(secs=DELETE_WINDOW_SECS):
    """Tidpunkterna för de raderingar som lyckats och ligger inom fönstret."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=secs)
    hits = []
    for row in read_actions(limit=500):
        if row.get("action") != "delete" or not row.get("ok") or row.get("refused"):
            continue
        try:
            at = datetime.datetime.fromisoformat(row.get("at") or "")
        except ValueError:
            continue
        if at >= cutoff:
            hits.append(row.get("at"))
    return hits


def refuse(cfg, action, key, reason, detail=""):
    """Nekar en skrivning och lämnar spår av det."""
    log_action(cfg, action, key, detail=detail, ok=False, refused=True, reason=reason)
    return reason


def trash_path_for(key):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(key or "okand"))
    return os.path.join(TRASH_DIR, "{}-{}.json".format(safe, stamp))


def snapshot_issue(cfg, key, why="radering"):
    """Sparar hela ärendet (fält + kommentarer) innan något förstörande sker.

    Kastar RuntimeError om kopian inte kan skrivas — anroparen ska då neka
    åtgärden. En radering utan kopia är precis det vi inte får göra."""
    fields = ("summary,description,issuetype,priority,assignee,reporter,labels,"
              "parent,status,project,customfield_10016,customfield_10020,"
              "customfield_10015,duedate,created,updated")
    issue = jira_get(cfg, "/rest/api/3/issue/{}?fields={}".format(key, fields))
    comments = []
    try:
        data = jira_get(cfg, "/rest/api/3/issue/{}/comment?maxResults=100".format(key))
        comments = data.get("comments") or []
    except RuntimeError:
        comments = []
    blob = {
        "schema": 1,
        "savedAt": iso_now(),
        "why": why,
        "site": cfg.get("siteUrl") or "",
        "savedBy": cfg.get("email") or "",
        "key": key,
        "issue": issue,
        "comments": comments,
    }
    path = trash_path_for(key)
    try:
        os.makedirs(TRASH_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, ensure_ascii=False, indent=2)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise RuntimeError("Kunde inte spara en kopia av {} ({}); raderar därför inget.".format(
            key, exc))
    return path


def trash_entries(key=None):
    """Papperskorgen, nyaste först. Utan key listas allt."""
    if not os.path.isdir(TRASH_DIR):
        return []
    rows = []
    for name in sorted(os.listdir(TRASH_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(TRASH_DIR, name)
        try:
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
        except (OSError, ValueError):
            continue
        # Nyckeln läses ur filen, inte ur filnamnet: filnamnet är säkrat för
        # filsystemet och kan därför skilja sig från ärendets riktiga nyckel.
        if key and (blob.get("key") or "") != key:
            continue
        rows.append({
            "file": name,
            "key": blob.get("key") or "",
            "savedAt": blob.get("savedAt") or "",
            "why": blob.get("why") or "",
            "summary": ((blob.get("issue") or {}).get("fields") or {}).get("summary") or "",
            "comments": len(blob.get("comments") or []),
        })
    rows.sort(key=lambda r: r.get("savedAt") or "", reverse=True)
    return rows


def issue_status(cfg, key):
    """Statusens namn, eller '' om ärendet inte går att läsa."""
    try:
        data = jira_get(cfg, "/rest/api/3/issue/{}?fields=status".format(key))
        return ((data.get("fields") or {}).get("status") or {}).get("name") or ""
    except RuntimeError:
        return ""


def snapshot_issue_entry(snap, key):
    """Ärendet som det ser ut i en snapshot (boards + backlog), annars None."""
    for board in (snap or {}).get("boards") or []:
        for row in (board.get("issues") or []) + (board.get("backlog") or []):
            if row.get("key") == key:
                return row
    return None


def verify_update(cfg, key, payload):
    """Läser tillbaka de fält som ändringen gällde."""
    try:
        data = jira_get(cfg, "/rest/api/3/issue/{}?fields=summary,priority,description,{}".format(
            key, STORY_POINTS_FIELD))
    except RuntimeError as exc:
        return False, "kunde inte läsa tillbaka {}: {}".format(key, exc)
    fields = data.get("fields") or {}
    checks = []
    if "summary" in payload:
        checks.append(("sammanfattning", fields.get("summary") or "", payload.get("summary") or ""))
    if "priorityName" in payload:
        checks.append(("prioritet",
                       ((fields.get("priority") or {}).get("name") or ""),
                       payload.get("priorityName") or ""))
    if "storyPoints" in payload and payload.get("storyPoints") not in (None, ""):
        raw = fields.get(STORY_POINTS_FIELD)
        checks.append(("story points", "" if raw is None else str(raw),
                       str(payload.get("storyPoints"))))
    for label, got, want in checks:
        if str(got).strip() != str(want).strip():
            return False, "{} står som '{}' i {}, beställde '{}'.".format(label, got, key, want)
    return True, ""


def restore_from_trash(cfg, path):
    """Återskapar ett ärende från papperskorgen. Returnerar (ny nyckel, besked).

    Beskedet namnger allt som **inte** kunde läggas tillbaka — en återställning som
    tiger om sina luckor är värre än ingen återställning."""
    try:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Kunde inte läsa kopian {}: {}".format(path, exc))
    fields = ((blob.get("issue") or {}).get("fields") or {})
    project = ((fields.get("project") or {}).get("key") or "")
    if not project:
        raise RuntimeError("Kopian saknar projekt – kan inte återskapa ärendet.")
    body = {"fields": {"project": {"key": project},
                       "summary": fields.get("summary") or "Återställt ärende"}}
    itype = fields.get("issuetype") or {}
    if itype.get("id"):
        body["fields"]["issuetype"] = {"id": str(itype["id"])}
    if fields.get("description") is not None:
        body["fields"]["description"] = fields["description"]
    if fields.get("labels"):
        body["fields"]["labels"] = fields["labels"]
    if (fields.get("priority") or {}).get("id"):
        body["fields"]["priority"] = {"id": str(fields["priority"]["id"])}
    if (fields.get("assignee") or {}).get("accountId"):
        body["fields"]["assignee"] = {"accountId": fields["assignee"]["accountId"]}
    points = fields.get(STORY_POINTS_FIELD)
    if points is not None:
        body["fields"][STORY_POINTS_FIELD] = points
    created = jira_post(cfg, "/rest/api/3/issue", body)
    new_key = (created or {}).get("key") or ""
    if not new_key:
        raise RuntimeError("Jira svarade utan nyckel – inget återskapat.")
    gaps = []
    restored_comments = 0
    for row in blob.get("comments") or []:
        raw = row.get("body")
        text = raw if isinstance(raw, str) else adf_to_text(raw)
        if not text.strip():
            continue
        author = (row.get("author") or {}).get("displayName") or "okänd"
        when = (row.get("created") or "")[:10]
        try:
            jira_post(cfg, "/rest/api/3/issue/{}/comment".format(new_key),
                      {"body": text_to_adf("[{} {}] {}".format(author, when, text))})
            restored_comments += 1
        except RuntimeError:
            gaps.append("en kommentar kunde inte läggas tillbaka")
    status_name = ((fields.get("status") or {}).get("name") or "")
    if status_name and status_name not in ("To Do", "Backlog"):
        try:
            real_move(cfg, new_key, status_name)
        except RuntimeError:
            gaps.append("statusen '{}' kunde inte sättas".format(status_name))
    if fields.get(SPRINT_FIELD):
        gaps.append("sprinttillhörigheten får sättas för hand")
    report = ("{} kommentarer tillbaka.".format(restored_comments) if restored_comments
              else "Inga kommentarer fanns i kopian.")
    if gaps:
        report += " Kvar: " + "; ".join(gaps) + "."
    return new_key, report


def verify_write(cfg, action, key, expect):
    """Läser tillbaka och jämför. Returnerar (ok, besked) — beskedet hamnar i svaret."""
    try:
        if action == "delete":
            jira_get(cfg, "/rest/api/3/issue/{}?fields=summary".format(key))
            return False, "{} svarar fortfarande efter raderingen.".format(key)
        data = jira_get(cfg, "/rest/api/3/issue/{}?fields=summary,status".format(key))
        status = (data.get("fields") or {}).get("status") or {}
        got = status.get("name") or ""
        want_id = str((expect or {}).get("statusId") or "")
        want = (expect or {}).get("status") or ""
        if want_id and str(status.get("id") or "") != want_id:
            return False, "{} står i '{}' ({}), beställde status-id {}.".format(
                key, got, status.get("id"), want_id)
        if want and got != want:
            return False, "{} står i '{}', beställde '{}'.".format(key, got, want)
        return True, ""
    except RuntimeError as exc:
        if action == "delete" and "404" in str(exc):
            return True, ""
        return False, "kunde inte läsa tillbaka {}: {}".format(key, exc)




# ----------------------------------------------------------------- status

def status_payload(cfg):
    mode = cfg.get("mode")
    summary = connection_summary(cfg)
    if mode == "real":
        email = cfg.get("email") or ""
        token = load_secret(account_for(cfg))
        site = cfg.get("siteUrl") or ""
        if not site or not email:
            return ok_payload(mode=mode, connected=False, connection=summary,
                account={
                "email": email, "displayName": "", "siteUrl": site, "connected": False},
                projects=[])
        if not token:
            return ok_payload(mode=mode, connected=False, connection=summary,
                account={
                "email": email, "displayName": "", "siteUrl": site, "connected": False},
                projects=[])
        try:
            myself = jira_get(cfg, "/rest/api/3/myself")
            return ok_payload(mode=mode, connected=True, connection=summary, account={
                "email": myself.get("emailAddress") or email,
                "displayName": myself.get("displayName") or "",
                "siteUrl": site,
                "connected": True,
            }, config={"startView": cfg.get("startView") or ""})
        except RuntimeError as exc:
            return ok_payload(mode=mode, connected=False, connection=summary,
                error=str(exc), account={
                "email": email, "displayName": "", "siteUrl": site, "connected": False})
    # mock
    account = mock_snapshot(cfg)["account"]
    return ok_payload(mode="mock", connected=True, connection=summary, account=account,
                      config={"startView": cfg.get("startView") or ""})


# ----------------------------------------------------------------- main

def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"

    if cmd == "status":
        status_payload(load_config())

    if cmd == "snapshot":
        cfg = load_config()
        try:
            snap = real_snapshot(cfg) if cfg.get("mode") == "real" else mock_snapshot(cfg)
        except RuntimeError as exc:
            err_payload(str(exc), mode=cfg.get("mode"))
        payload(snap)

    if cmd == "transitions":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        if not key:
            err_payload("transitions kräver en issue-key")
        try:
            if cfg.get("mode") == "real":
                rows = real_transitions(cfg, key)
            else:
                rows = mock_transitions(mock_state(), key)
        except RuntimeError as exc:
            err_payload(str(exc))
        ok_payload(transitions=rows, key=key)

    if cmd == "move":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        target = argv[3] if len(argv) > 3 else ""
        if not key or not target:
            err_payload("move kräver key och målstatus")
        try:
            if cfg.get("mode") == "real":
                before = issue_status(cfg, key)
                chosen = real_move(cfg, key, target)
                ok, msg = verify_write(cfg, "move", key,
                                       {"statusId": (chosen or {}).get("toStatusId")})
                log_action(cfg, "move", key,
                           detail="{} → '{}'".format(before or "?", (chosen or {}).get("toStatusName") or target),
                           before=before, after=(chosen or {}).get("toStatusName") or "",
                           ok=ok, reason="" if ok else msg)
                if not ok:
                    err_payload("Flytten av {} kunde inte bekräftas: {}".format(key, msg))
            else:
                mock_move(mock_state(), key, target)
                log_action(cfg, "move", key, detail="till '{}' (mock)".format(target))
        except RuntimeError as exc:
            log_action(cfg, "move", key, detail="till '{}'".format(target), ok=False, reason=str(exc))
            err_payload(str(exc))
        snap = mock_snapshot(cfg) if cfg.get("mode") != "real" else real_snapshot(cfg)
        capture_baseline(cfg, snap)
        payload(snap)

    if cmd == "create":
        cfg = load_config()
        board_id = argv[2] if len(argv) > 2 else ""
        raw = argv[3] if len(argv) > 3 else ""
        if not board_id or not raw:
            err_payload("create kräver boardId och ett JSON-payload")
        try:
            payload_data = json.loads(raw)
        except ValueError as exc:
            err_payload("create fick ogiltig JSON: {}".format(exc))
        try:
            if cfg.get("mode") == "real":
                board = None
                for candidate in real_snapshot(cfg)["boards"]:
                    if str(candidate["id"]) == str(board_id):
                        board = candidate
                        break
                if board is None:
                    raise RuntimeError("Okänd tavla {}.".format(board_id))
                if not board.get("canAdd"):
                    raise RuntimeError("Du saknar rättighet att skapa ärenden i {}.".format(
                        board.get("projectKey")))
                known = set()
                for row in (board.get("issues") or []) + (board.get("backlog") or []):
                    known.add(row.get("key"))
                real_create(cfg, board, payload_data)
                snap = real_snapshot(cfg)
                fresh = [row.get("key") for b in snap.get("boards") or []
                         for row in (b.get("issues") or []) + (b.get("backlog") or [])
                         if row.get("key") not in known]
                new_key = fresh[0] if fresh else ""
                log_action(cfg, "create", new_key,
                           detail=(payload_data.get("summary") or "")[:80],
                           extra={"project": board.get("projectKey"),
                                  "type": (payload_data.get("typeName") or "Task")})
                # Kvitto: ett skapat ärende som inte syns i tavlan efteråt är ett fel,
                # inte en framgång.
                if not new_key:
                    err_payload("Ärendet skapades men syns inte i tavlan efteråt — kontrollera i Jira.")
            else:
                mock_create(mock_state(), board_id, payload_data)
                snap = mock_snapshot(cfg)
        except RuntimeError as exc:
            log_action(cfg, "create", "", detail=(payload_data.get("summary") or "")[:80],
                       ok=False, reason=str(exc))
            err_payload(str(exc))
        capture_baseline(cfg, snap)
        payload(snap)

    if cmd == "delete":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        flags = list(argv[3:])
        confirmed = "--yes" in flags
        forced = "--force" in flags
        if not key:
            err_payload("delete kräver en issue-key")
        # Nät 1: ingen radering utan ett uttalat ja. Gränssnittet frågar först i en
        # ruta som namnger ärendet; kommandoraden måste säga --yes.
        if not confirmed:
            err_payload(refuse(cfg, "delete", key,
                               "Radering av {} kräver ett bekräftat val (--yes).".format(key),
                               detail="obekräftad"))
        # Nät 2: kvotvakten. En skur av raderingar stoppas och syns i svaret, i
        # stället för att 15 ärenden försvinner tyst.
        recent = deletes_in_window()
        if len(recent) >= DELETE_QUOTA and not forced:
            err_payload(refuse(cfg, "delete", key,
                               "{} raderingar de senaste {} minuterna. Stanna och kontrollera "
                               "vad som händer — --force om fler verkligen ska bort.".format(
                                   len(recent), DELETE_WINDOW_SECS // 60),
                               detail="kvotvakt"))
        try:
            if cfg.get("mode") == "real":
                # Nät 3: kopia först, radera sedan. Går kopian inte att skriva nekas raderingen.
                path = snapshot_issue(cfg, key, why="radering")
                base = os.path.basename(path)
                real_delete(cfg, key)
                # Nät 4: läs tillbaka. En radering som inte kan bekräftas är ett fel.
                ok, msg = verify_write(cfg, "delete", key, None)
                log_action(cfg, "delete", key, detail="kopia: " + base, ok=ok,
                           reason="" if ok else msg, extra={"trash": base})
                raise_notification("OmaJIRA · {} raderad".format(key),
                                   "En kopia ligger i papperskorgen ({}) och kan återställas.".format(base))
                if not ok:
                    err_payload("{} raderades men kunde inte bekräftas: {}".format(key, msg))
            else:
                mock_delete(mock_state(), key)
                log_action(cfg, "delete", key, detail="mock-radering")
        except RuntimeError as exc:
            log_action(cfg, "delete", key, detail="misslyckades", ok=False, reason=str(exc))
            err_payload(str(exc))
        snap = mock_snapshot(cfg) if cfg.get("mode") != "real" else real_snapshot(cfg)
        capture_baseline(cfg, snap)
        payload(snap)

    if cmd == "mock-touch":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        if not key:
            err_payload("mock-touch kräver en issue-key")
        state = mock_state()
        if key not in state["issues"]:
            err_payload("Okänd issue: {}".format(key))
        issue = state["issues"][key]
        changed = False
        for arg in argv[3:]:
            if "=" in arg:
                field, value = arg.split("=", 1)
                if field not in ("statusId", "summary", "priorityName",
                                 "assigneeName", "assigneeEmail", "typeName"):
                    err_payload("Okänt fält för mock-touch: {}".format(field))
                if field == "statusId" and value not in MOCK_STATUSES:
                    err_payload("Okänd status: {}".format(value))
                issue[field] = value
                changed = True
            elif arg in MOCK_STATUSES:
                issue["statusId"] = arg
                changed = True
            else:
                err_payload("Okänd status: {}".format(arg))
        if not changed:
            # No explicit change: pretend a teammate advanced the issue one step.
            nxt = MOCK_TRANSITIONS.get(issue["statusId"], [])
            if not nxt:
                err_payload("{} har inga fler övergångar.".format(key))
            issue["statusId"] = nxt[0]
        save_mock_state(state)
        payload(mock_snapshot(cfg))

    if cmd == "assign":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        target = argv[3] if len(argv) > 3 else ""
        if not key or not target:
            err_payload("assign kräver en issue-key och ett sprintId (eller 'backlog')")
        try:
            if cfg.get("mode") == "real":
                real_assign_sprint(cfg, key, target)
            else:
                mock_assign_sprint(mock_state(), key, target)
        except RuntimeError as exc:
            log_action(cfg, "assign", key, detail="→ {}".format(target), ok=False, reason=str(exc))
            err_payload(str(exc))
        snap = mock_snapshot(cfg) if cfg.get("mode") != "real" else real_snapshot(cfg)
        row = snapshot_issue_entry(snap, key)
        got = "" if row is None else (row.get("sprintId") or "")
        want = "" if str(target).lower() in ("", "backlog") else str(target)
        ok = str(got) == want
        log_action(cfg, "assign", key, detail="→ {}".format(target or "backlog"), after=got, ok=ok,
                   reason="" if ok else "ligger i sprint '{}', beställde '{}'".format(got, want))
        capture_baseline(cfg, snap)
        if not ok:
            err_payload("{} ligger i sprint '{}', beställde '{}' — kontrollera i Jira.".format(
                key, got or "backloggen", want or "backloggen"))
        payload(snap)

    if cmd == "comments":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        if not key:
            err_payload("comments kräver en issue-key")
        try:
            rows = real_comments(cfg, key) if cfg.get("mode") == "real" \
                else mock_comments(mock_state(), key)
        except RuntimeError as exc:
            err_payload(str(exc))
        ok_payload(key=key, comments=rows)

    if cmd == "comment":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        text = argv[3] if len(argv) > 3 else ""
        if not key or not text:
            err_payload("comment kräver en issue-key och en text")
        try:
            if cfg.get("mode") == "real":
                rows = real_add_comment(cfg, key, text)
                head = re.sub(r"\s+", " ", text).strip()[:30]
                ok = any(head in re.sub(r"\s+", " ", (r.get("body") or "")) for r in rows)
                log_action(cfg, "comment", key, detail=text[:80], ok=ok,
                           reason="" if ok else "kommentaren syns inte i ärendet efteråt")
                if not ok:
                    err_payload("Kommentaren på {} kunde inte bekräftas.".format(key))
            else:
                rows = mock_add_comment(mock_state(), key, text)
                log_action(cfg, "comment", key, detail=text[:80])
        except RuntimeError as exc:
            log_action(cfg, "comment", key, detail=text[:80], ok=False, reason=str(exc))
            err_payload(str(exc))
        ok_payload(key=key, comments=rows)

    if cmd == "update":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        raw = argv[3] if len(argv) > 3 else ""
        if not key or not raw:
            err_payload("update kräver en issue-key och ett JSON-payload")
        try:
            payload_data = json.loads(raw)
        except ValueError as exc:
            err_payload("update fick ogiltig JSON: {}".format(exc))
        if not payload_data:
            err_payload(refuse(cfg, "update", key, "Ändringen innehåller inga fält.", detail="tomt payload"))
        try:
            if cfg.get("mode") == "real":
                # En ändring av text kan skriva över något som bara fanns där. Kopian
                # läggs i papperskorgen först, så texten går att få tillbaka.
                base = ""
                if any(f in payload_data for f in ("description", "summary")):
                    base = os.path.basename(snapshot_issue(cfg, key, why="före ändring"))
                real_update(cfg, key, payload_data)
                ok, msg = verify_update(cfg, key, payload_data)
                log_action(cfg, "update", key,
                           detail="fält: " + ", ".join(sorted(payload_data.keys())),
                           ok=ok, reason="" if ok else msg,
                           extra={"trash": base} if base else None)
                if not ok:
                    err_payload("Ändringen av {} kunde inte bekräftas: {}".format(key, msg))
            else:
                mock_update(mock_state(), key, payload_data)
                log_action(cfg, "update", key,
                           detail="fält: " + ", ".join(sorted(payload_data.keys())) + " (mock)")
        except RuntimeError as exc:
            log_action(cfg, "update", key,
                       detail="fält: " + ", ".join(sorted(payload_data.keys())),
                       ok=False, reason=str(exc))
            err_payload(str(exc))
        snap = mock_snapshot(cfg) if cfg.get("mode") != "real" else real_snapshot(cfg)
        capture_baseline(cfg, snap)
        payload(snap)

    if cmd == "journal":
        cfg = load_config()
        limit = int(argv[2]) if len(argv) > 2 and str(argv[2]).isdigit() else 50
        ok_payload(log=read_actions(limit), path=ACTION_LOG_PATH)

    if cmd == "trash":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        ok_payload(entries=trash_entries(key or None), path=TRASH_DIR)

    if cmd == "restore":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        if not key:
            err_payload("restore kräver nyckeln på det raderade ärendet")
        rows = trash_entries(key)
        if not rows:
            err_payload("Ingen kopia av {} i papperskorgen ({})".format(key, TRASH_DIR))
        try:
            new_key, report = restore_from_trash(cfg, os.path.join(TRASH_DIR, rows[0]["file"]))
        except RuntimeError as exc:
            log_action(cfg, "restore", key, ok=False, reason=str(exc))
            err_payload(str(exc))
        log_action(cfg, "restore", new_key, detail="från " + rows[0]["file"],
                   extra={"origin": key, "report": report})
        raise_notification("OmaJIRA · {} återställd som {}".format(key, new_key), report)
        snap = mock_snapshot(cfg) if cfg.get("mode") != "real" else real_snapshot(cfg)
        capture_baseline(cfg, snap)
        payload(dict(snap, restoredKey=new_key, restoredFrom=key, report=report))

    if cmd == "options":
        cfg = load_config()
        pkey = argv[2] if len(argv) > 2 else ""
        if not pkey:
            pkey = cfg.get("selectedProjectKey") or ""
        if cfg.get("mode") == "real":
            if not pkey:
                err_payload("options kräver en projektnyckel")
            rows = real_options(cfg, pkey)
        else:
            rows = mock_options(pkey or "WEB")
        ok_payload(projectKey=pkey, options=rows)

    if cmd == "activity":
        cfg = load_config()
        pkey = argv[2] if len(argv) > 2 else ""
        limit = argv[3] if len(argv) > 3 else "25"
        try:
            limit = max(1, min(100, int(limit)))
        except (TypeError, ValueError):
            limit = 25
        if cfg.get("mode") == "real":
            if not pkey:
                err_payload("activity kräver en projektnyckel")
            rows = real_activity(cfg, pkey, limit)
        else:
            rows = mock_activity(mock_state(), pkey or "WEB", limit)
        ok_payload(projectKey=pkey, activity=rows)

    if cmd == "report":
        cfg = load_config()
        bid = argv[2] if len(argv) > 2 else ""
        sid = argv[3] if len(argv) > 3 else ""
        if not bid or not sid:
            err_payload("report kräver boardId och sprintId")
        if cfg.get("mode") == "real":
            rows = real_report(cfg, bid, sid)
        else:
            rows = mock_report(mock_state(), bid, sid)
        ok_payload(boardId=bid, report=rows)

    if cmd == "dev":
        cfg = load_config()
        key = argv[2] if len(argv) > 2 else ""
        if not key:
            err_payload("dev kräver en issue-key")
        try:
            rows = real_dev_status(cfg, key) if cfg.get("mode") == "real" \
                else mock_dev_status(mock_state(), key)
        except RuntimeError as exc:
            err_payload(str(exc))
        ok_payload(key=key, dev=rows)

    if cmd == "watch":
        cfg = load_config()
        watch_payload(cfg)

    if cmd == "configure":
        cfg = load_config()
        raw = argv[2] if len(argv) > 2 else ""
        flags = list(argv[3:])
        try:
            updates = json.loads(raw) if raw else {}
        except ValueError as exc:
            err_payload("configure fick ogiltig JSON: {}".format(exc))
        # Anslutningslåset: en fungerande anslutning (site + konto + token) får
        # inte skrivas över av misstag. Identitetsnycklarna kräver --replace;
        # vanliga UI-nycklar (startView, vald tavla, vald sprint) går som förut.
        touched = sorted(k for k in updates if k in IDENTITY_KEYS)
        summary = connection_summary(cfg)
        if touched and "--replace" not in flags and summary["locked"]:
            err_payload(refuse(cfg, "configure", "",
                               "Anslutningen är låst: {} ändras bara via 'Skapa ny "
                               "anslutning' eller --replace.".format(", ".join(touched)),
                               detail="anslutningslås"),
                        refused=True, connection=summary)
        for key, value in updates.items():
            cfg[key] = value
        save_config(cfg)
        status_payload(cfg)

    if cmd == "login":
        cfg = load_config()
        flags = list(argv[2:])
        token_file = flag_value(argv, "--token-file")
        site = (flag_value(argv, "--site") or cfg.get("siteUrl") or "").strip()
        email = (flag_value(argv, "--email") or cfg.get("email") or "").strip()
        if not site or not email:
            err_payload("siteUrl och email krävs (--site/--email eller i config).")
        summary = connection_summary(cfg)
        same_account = (site == summary["siteUrl"] and email == summary["email"])
        # Att byta anslutning är ett uttalat val. Samma konto igen (ny token) är
        # inte ett byte och ska inte kräva --replace.
        if summary["locked"] and not same_account and "--replace" not in flags:
            err_payload(refuse(cfg, "login", "",
                               "Det finns redan en fungerande anslutning till {}. "
                               "Tryck 'Skapa ny anslutning' för att byta.".format(
                                   summary["siteUrl"] or "Jira"),
                               detail="anslutningslås"),
                        refused=True, connection=summary)
        token = None
        if token_file:
            try:
                with open(token_file, encoding="utf-8") as fh:
                    token = fh.read().strip()
                os.remove(token_file)
            except OSError:
                pass
        if not token:
            token = os.environ.get("JIRA_TOKEN") or stored_token({"email": email})
        if not token:
            err_payload("Ingen token angiven och ingen finns i nyckelringen.")
        # Validera mot Jira FÖRST. Först när kontot svarar skrivs adressen in, så
        # en felstavad site eller e-post kan inte slå ut en anslutning som
        # redan fungerar.
        probe = dict(cfg)
        probe["siteUrl"] = site
        probe["email"] = email
        probe["mode"] = "real"
        try:
            myself = jira_get(probe, "/rest/api/3/myself", token=token)
        except Exception as exc:  # DNS, timeout, HTTP: allt blir ett tydligt svar
            err_payload(refuse(cfg, "login", "",
                               "Kunde inte ansluta till {}: {}".format(site, exc),
                               detail="inloggningen misslyckades"),
                        refused=True, connection=summary)
        store_secret(token, email)
        cfg["mode"] = "real"
        cfg["siteUrl"] = site
        cfg["email"] = email
        save_config(cfg)
        log_action(cfg, "login", "", detail="ansluten till {}".format(site),
                   before=summary, after=connection_summary(cfg))
        return payload({
            "schema": SCHEMA, "ok": True, "error": None,
            "generatedAt": utc_now(),
            "account": {
                "email": myself.get("emailAddress") or email,
                "displayName": myself.get("displayName") or "",
                "siteUrl": site,
                "connected": True,
            },
            "connection": connection_summary(cfg)})

    if cmd == "logout":
        cfg = load_config()
        flags = list(argv[2:])
        summary = connection_summary(cfg)
        if "--yes" not in flags:
            err_payload(refuse(cfg, "logout", "",
                               "Frånkoppling kräver ett bekräftat val (--yes).",
                               detail="obekräftad"),
                        refused=True, connection=summary)
        forget_stored_token(summary["email"] or cfg.get("email"))
        cfg["mode"] = "mock"
        save_config(cfg)
        log_action(cfg, "logout", "",
                   detail="token borttagen för {}".format(summary["email"]),
                   before=summary, after=connection_summary(cfg))
        return payload({"schema": SCHEMA, "ok": True, "error": None,
                        "generatedAt": utc_now(),
                        "connection": connection_summary(cfg)})

    if cmd == "mock-reset":
        mock_reset()
        return ok_payload()

    err_payload("Okänt kommando: {}".format(cmd), usage="see --help")


def run():
    try:
        main(sys.argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure is rendered in QML
        err_payload("{}: {}".format(type(exc).__name__, exc))


if __name__ == "__main__":
    run()
