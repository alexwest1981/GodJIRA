#!/usr/bin/env python3
"""jira_export -- dra hem ett helt Jira-projekt till disk, med kommentarer och bilagor.

Läser bara. Skriver JSON + nedladdade bilagor i en katalog, så att projekten kan
importeras någon annanstans (Paperclip) innan Jira-abonnemanget tar slut.

    python3 bin/jira_export.py --project SCRUM --out ~/.local/share/jira-export
"""
import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jira_bridge as jb  # token ur nyckelringen, samma väg som resten av hubben

SITE = "https://danieleriksson026.atlassian.net"
ACCOUNT = "inkedalex@gmail.com"


def api(path, auth, raw=False):
    req = urllib.request.Request(
        SITE + path,
        headers={"Authorization": "Basic " + auth,
                 "Accept": "*/*" if raw else "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as fh:
                return fh.read() if raw else json.load(fh)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 502, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="SCRUM")
    ap.add_argument("--out", default=str(pathlib.Path.home() / ".local/share/jira-export"))
    args = ap.parse_args()

    token = jb.stored_token({"email": ACCOUNT})
    if not token:
        sys.exit("inget Jira-token i nyckelringen")
    auth = base64.b64encode(f"{ACCOUNT}:{token}".encode()).decode()

    root = pathlib.Path(args.out).expanduser() / args.project
    (root / "attachments").mkdir(parents=True, exist_ok=True)

    issues, token_next = [], None
    while True:
        body = {"jql": f"project = {args.project} ORDER BY key ASC",
                "maxResults": 50, "fields": ["*all"]}
        if token_next:
            body["nextPageToken"] = token_next
        req = urllib.request.Request(
            SITE + "/rest/api/3/search/jql",
            data=json.dumps(body).encode(),
            headers={"Authorization": "Basic " + auth, "Content-Type": "application/json",
                     "Accept": "application/json"})
        page = json.load(urllib.request.urlopen(req, timeout=60))
        issues.extend(page.get("issues", []))
        token_next = page.get("nextPageToken")
        print(f"{len(issues)} issues", flush=True)
        if not token_next or page.get("isLast"):
            break

    total = len(issues)
    for n, issue in enumerate(issues, 1):
        key = issue["key"]
        issue["comments"] = api(f"/rest/api/3/issue/{key}/comment?maxResults=1000", auth).get("comments", [])
        for att in issue["fields"].get("attachment", []):
            dest = root / "attachments" / key
            dest.mkdir(exist_ok=True)
            safe = att["filename"].replace("/", "_")
            target = dest / safe
            if not target.exists():
                target.write_bytes(api(att["content"], auth, raw=True))
            att["localPath"] = str(target)
        print(f"[{n}/{total}] {key} comments={len(issue['comments'])} "
              f"att={len(issue['fields'].get('attachment', []))}", flush=True)

    (root / "issues.json").write_text(json.dumps(
        {"project": args.project, "site": SITE, "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "count": total, "issues": issues}, ensure_ascii=False, indent=1))
    print(f"klart: {total} issues -> {root}")


if __name__ == "__main__":
    main()
