#!/usr/bin/env python3
"""paperclip_bridge -- samma svar som jira_bridge, men ur den lokala Paperclip.

Panelen frågar bryggan om `snapshot`, `status` och `journal`; svaren här har exakt
samma form som jira_bridge:s, så vyerna behöver inte veta vilken källa de ritar.
Det som Jira har och Paperclip inte har (sprintar, poäng, ärendetyper) lämnas tomt
i stället för att hittas på: panelen visar "--", inte en gissning.

    python3 bin/paperclip_bridge.py snapshot --json
    python3 bin/paperclip_bridge.py status
    python3 bin/paperclip_bridge.py journal 10

    python3 bin/paperclip_bridge.py --selftest   # former, ingen server
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

SITE = os.environ.get("PAPERCLIP_URL", "http://127.0.0.1:3100")

# Paperclips statusar, i den ordning en tavla läses. Etiketten är panelens, spåret
# (statusCategory) är Jiras tre — det är de vyerna räknar på.
STATUSES = [("backlog", "Backlog", "new"), ("todo", "To Do", "new"),
            ("in_progress", "In Progress", "indeterminate"), ("in_review", "In Review", "indeterminate"),
            ("blocked", "Blocked", "new"), ("done", "Done", "done")]
MONTHS = None


def call(path: str, timeout: int = 60):
    req = urllib.request.Request(SITE + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.load(fh)


def ms(stamp) -> int:
    """ISO-tid -> millisekunder. 0 när tiden saknas (panelen ritar ingen stapel då)."""
    if not stamp:
        return 0
    try:
        return int(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def company_and_project():
    companies = call("/api/companies")
    if not companies:
        raise RuntimeError("Paperclip svarar, men har inget företag")
    company = companies[0]
    projects = call(f"/api/companies/{company['id']}/projects")
    if not projects:
        raise RuntimeError("företaget har inget projekt")
    return company, projects[0]


def issue_row(issue: dict, project: dict) -> dict:
    """Ett Paperclip-ärende i Jiras form. Fälten Jira fyller och Paperclip inte har står tomma."""
    status = str(issue.get("status") or "todo")
    label = dict((key, name) for key, name, _ in STATUSES).get(status, status)
    category = dict((key, cat) for key, _, cat in STATUSES).get(status, "new")
    ident = issue.get("identifier") or issue.get("id")
    return {
        "key": ident,
        "summary": issue.get("title") or "",
        "typeName": "Task",
        "typeIcon": "",
        "statusId": status,
        "statusName": label,
        "statusCategory": category,
        "assigneeEmail": "",
        "assigneeName": "",
        "priorityName": str(issue.get("priority") or "").capitalize(),
        "storyPoints": None,
        "updatedMs": ms(issue.get("updatedAt")),
        "createdMs": ms(issue.get("createdAt")),
        "resolutionMs": ms(issue.get("completedAt")) if status == "done" else 0,
        "description": issue.get("description") or "",
        "url": f"{SITE}/issues/{ident}",
        "projectKey": project.get("name") or "",
        "sprintId": "",
        "sprintName": "",
        "startMs": 0,
        "dueMs": ms(issue.get("targetDate")),
        "parentKey": "",
        "parentSummary": "",
    }


def snapshot() -> dict:
    company, project = company_and_project()
    issues = call(f"/api/companies/{company['id']}/issues?projectId={project['id']}", timeout=120)
    rows = [issue_row(i, project) for i in issues]
    by_key = {r["key"]: r for r in rows}
    for issue in issues:  # föräldern syns bara som id på barnet
        parent = issue.get("parentId")
        if parent:
            for other in issues:
                if other.get("id") == parent:
                    row = by_key.get(issue.get("identifier") or "")
                    if row:
                        row["parentKey"] = other.get("identifier") or ""
                        row["parentSummary"] = other.get("title") or ""
                    break
    present = {r["statusId"] for r in rows}
    columns = [{"name": name, "statusId": key, "statusName": name}
               for key, name, _ in STATUSES if key in present] or \
              [{"name": "To Do", "statusId": "todo", "statusName": "To Do"},
               {"name": "Done", "statusId": "done", "statusName": "Done"}]
    board = {"id": str(project["id"]), "name": project.get("name") or "Paperclip",
             "type": "simple", "projectKey": project.get("name") or "PAPERCLIP",
             "columns": columns, "sprint": {}, "sprints": [],
             "issues": rows, "backlog": [], "canAdd": True, "canDelete": True}
    return {"schema": 1, "ok": True, "error": None,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "mode": "paperclip",
            "account": {"email": "", "displayName": company.get("name") or "Paperclip",
                        "siteUrl": SITE + "/", "connected": True},
            "projects": [{"key": project.get("name") or "", "name": project.get("name") or ""}],
            "boards": [board]}


def status() -> dict:
    health = call("/api/health", timeout=15)
    company, _ = company_and_project()
    return {"schema": 1, "ok": True, "error": None,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "mode": "paperclip", "connected": health.get("status") == "ok",
            "account": {"email": "", "displayName": company.get("name") or "Paperclip",
                        "siteUrl": SITE + "/", "connected": health.get("status") == "ok"},
            "connection": {"siteUrl": SITE + "/", "email": "", "mode": "paperclip",
                           "hasToken": True, "locked": False},
            "language": "sv", "languageSetting": "sv",
            "version": str(health.get("version") or ""), "config": {}}


def journal(limit: int = 20) -> dict:
    company, _ = company_and_project()
    events = call(f"/api/companies/{company['id']}/activity?limit={int(limit)}", timeout=60)
    log = []
    for event in events if isinstance(events, list) else events.get("items") or []:
        details = event.get("details") or {}
        log.append({"at": event.get("createdAt") or "",
                    "action": str(event.get("action") or "").split(".")[-1],
                    "key": details.get("identifier") or "",
                    "mode": "paperclip",
                    "user": event.get("actorId") or "",
                    "detail": "", "ok": True})
    return {"schema": 1, "ok": True, "error": None,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "log": log}


COMMANDS = {"snapshot": snapshot, "status": status, "journal": journal}


def selftest() -> int:
    """Former som panelen räknar på, prövade utan server."""
    project = {"name": "SCRUM (Grupp C)"}
    row = issue_row({"identifier": "ALE-7", "title": "Ett ärende", "status": "done",
                     "priority": "high", "createdAt": "2026-10-01T10:00:00.000Z",
                     "updatedAt": "2026-10-02T10:00:00.000Z"}, project)
    assert row["key"] == "ALE-7" and row["statusCategory"] == "done"
    assert row["priorityName"] == "High" and row["storyPoints"] is None
    assert row["parentKey"] == "" and row["url"].endswith("/issues/ALE-7")
    assert issue_row({"status": "in_progress"}, project)["statusCategory"] == "indeterminate"
    assert issue_row({"status": "något-nytt"}, project)["statusCategory"] == "new"
    assert ms("2026-10-01T10:00:00.000Z") > 0 and ms(None) == 0 and ms("skräp") == 0
    assert set(COMMANDS) == {"snapshot", "status", "journal"}
    print("paperclip_bridge: selftest ok")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    if not argv or argv[0] not in COMMANDS:
        print(json.dumps({"ok": False, "error": "okänt kommando: " + " ".join(argv[:1])}))
        return 2
    command, rest = argv[0], [a for a in argv[1:] if not a.startswith("--")]
    try:
        answer = COMMANDS[command](int(rest[0])) if command == "journal" and rest else COMMANDS[command]()
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"Paperclip svarar inte: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(answer, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main(sys.argv[1:]))
