#!/usr/bin/env python3
"""jira_import -- lägg Jira-exporten in i en lokal Paperclip, utan mellanhänder.

Läser issues.json från jira_export och postar via Paperclips REST-API på loopback
(local_trusted-läget kräver ingen inloggning). Idempotent: nycklarna som redan är
importerade står i imported.json och hoppas över vid omkörning.

    python3 jira_import.py --export ~/.local/share/jira-export/SCRUM --port 3100
"""
import argparse
import json
import pathlib
import urllib.error
import urllib.request

STATUS = {"To Do": "todo", "In Progress": "in_progress", "In Review": "in_review",
          "Done": "done", "Blocked": "blocked"}
PRIORITY = {"Highest": "critical", "High": "high", "Medium": "medium",
            "Low": "low", "Lowest": "low"}
SITE = "https://danieleriksson026.atlassian.net"


def _inline(node):
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        text = node.get("text", "")
        marks = {m["type"] for m in node.get("marks", [])}
        if "code" in marks:
            return f"`{text}`"
        if "strong" in marks:
            text = f"**{text}**"
        if "em" in marks:
            text = f"*{text}*"
        return text
    if node.get("type") == "mention":
        return "@" + str(node.get("attrs", {}).get("text", "?")).lstrip("@")
    if node.get("type") == "hardBreak":
        return "  \n"
    return "".join(_inline(c) for c in node.get("content") or [])


def adf(node):
    """En ADF-nod -> markdown. Bara de noder Jira faktiskt skickar i beskrivningar."""
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    kids = node.get("content") or []
    if kind == "doc":
        return "".join(adf(c) for c in kids).strip()
    if kind == "paragraph":
        return _inline(node) + "\n\n"
    if kind == "heading":
        return "#" * int(node.get("attrs", {}).get("level", 1)) + " " + _inline(node) + "\n\n"
    if kind in ("bulletList", "orderedList"):
        marker = "- " if kind == "bulletList" else "1. "
        return "".join(marker + _inline(item).strip() + "\n" for item in kids) + "\n"
    if kind == "listItem":
        return _inline(node)
    if kind == "codeBlock":
        return "```\n" + _inline(node) + "\n```\n\n"
    if kind == "blockquote":
        return "".join("> " + adf(c).strip() + "\n" for c in kids) + "\n"
    if kind == "rule":
        return "---\n\n"
    if kind == "table":
        rows = [" | ".join(_inline(c) for c in cells)
                for row in kids for cells in [row.get("content") or []]]
        if not rows:
            return ""
        return rows[0] + "\n" + "|".join(["---"] * len(rows[0].split(" | "))) + "\n" \
            + "\n".join(rows[1:]) + "\n\n"
    return "".join(adf(c) for c in kids) or _inline(node)


def to_markdown(desc):
    if not isinstance(desc, dict):
        return (desc or "").strip()
    return adf(desc).strip()


def call(port, path, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=60) as fh:
            body = fh.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method or 'POST'} {path} -> {exc.code}: {exc.read()[:400]!r}")
    return json.loads(body) if body else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default=str(pathlib.Path.home() / ".local/share/jira-export/SCRUM"))
    ap.add_argument("--port", type=int, default=3100)
    ap.add_argument("--company", default="Alex")
    ap.add_argument("--project", default="SCRUM (Grupp C)")
    args = ap.parse_args()

    root = pathlib.Path(args.export).expanduser()
    data = json.loads((root / "issues.json").read_text())
    issues = data["issues"]
    done_file = root / "imported.json"
    imported = json.loads(done_file.read_text()) if done_file.exists() else {}

    # företag + projekt: återanvänd om de finns, skapa annars
    company = next((c for c in call(args.port, "/api/companies")
                    if c["name"] == args.company), None)
    if not company:
        company = call(args.port, "/api/companies",
                       {"name": args.company, "description": "Personlig Paperclip"})
    projects = call(args.port, f"/api/companies/{company['id']}/projects")
    project = next((p for p in projects if p["name"] == args.project), None)
    if not project:
        project = call(args.port, f"/api/companies/{company['id']}/projects",
                       {"name": args.project, "status": "in_progress",
                        "description": "Importerat från Jira SCRUM (Grupp C)"})
    print("company", company["id"], "project", project["id"])

    def body_for(issue):
        fields = issue["fields"]
        head = f"Jira: [{issue['key']}]({SITE}/browse/{issue['key']}) · " \
               f"{fields['issuetype']['name']} · skapad {fields['created'][:10]}"
        desc = to_markdown(fields.get("description"))
        return head + ("\n\n" + desc if desc else "")

    def status_of(issue):
        return STATUS.get(issue["fields"]["status"]["name"], "todo")

    def priority_of(issue):
        return PRIORITY.get((issue["fields"].get("priority") or {}).get("name", ""), "medium")

    # ordning: epics först, sedan allt annat, sist subtasks (föräldern måste finnas)
    def rank(issue):
        return {"Epic": 0, "Subtask": 2}.get(issue["fields"]["issuetype"]["name"], 1)

    for n, issue in enumerate(sorted(issues, key=rank), 1):
        key = issue["key"]
        if key in imported:
            continue
        created = call(args.port, f"/api/companies/{company['id']}/issues",
                       {"title": issue["fields"]["summary"],
                        "description": body_for(issue),
                        "status": status_of(issue),
                        "priority": priority_of(issue),
                        "projectId": project["id"]})
        imported[key] = created["id"]
        for comment in issue["comments"]:
            text = to_markdown(comment.get("body"))
            if text:
                call(args.port, f"/api/issues/{created['id']}/comments",
                     {"body": f"**{comment['author']['displayName']}, "
                              f"{comment['created'][:10]}:**\n\n{text}"})
        print(f"[{n}/{len(issues)}] {key} -> {created.get('identifier')}", flush=True)
        done_file.write_text(json.dumps(imported, indent=0))

    # föräldrar: Jira listar barnen på föräldern, inte tvärtom
    for issue in issues:
        parent_id = imported.get(issue["key"])
        for child in issue["fields"].get("subtasks") or []:
            child_id = imported.get(child["key"])
            if parent_id and child_id:
                call(args.port, f"/api/issues/{child_id}", {"parentId": parent_id}, "PATCH")

    print(f"klart: {len(imported)} issues i {args.project}")


if __name__ == "__main__":
    main()
