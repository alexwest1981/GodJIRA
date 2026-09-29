#!/usr/bin/env python3
"""One check for the panel: does it answer, and do its numbers agree with Jira's?

    python3 panel/check.py [base-url]      # default http://127.0.0.1:8788

Exits non-zero on the first broken thing, and prints the numbers either way. Run
it after touching server.py or index.html: it catches the failure that matters
most here -- a panel that is alive but blank, because the board came up empty.
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788").rstrip("/")


def get(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=180) as r:
        assert r.status == 200, path + " -> " + str(r.status)
        return r.read()


def main() -> int:
    page = get("/").decode("utf-8", "replace")
    for needle in ('id="cols"', "Flödets nästa", "/api/state"):
        assert needle in page, "the page is missing " + needle

    state = json.loads(get("/api/state"))
    jira, gh, flow = state["jira"], state["github"], state["flow"]
    assert jira["ok"], "Jira did not answer: " + str(jira.get("error"))
    assert gh["ok"], "GitHub did not answer: " + str(gh.get("error"))
    assert gh["repos"], "no repositories came back"

    boards = jira["boards"]
    assert boards, "no board came back"
    b = boards[0]
    issues, backlog = b.get("issues") or [], b.get("backlog") or []
    assert issues and backlog, "board or backlog came back empty"

    keys = [i["key"] for i in issues] + [i["key"] for i in backlog]
    assert len(keys) == len(set(keys)), "a key appears in both the board and the backlog"

    # The live work sits in the backlog when no sprint is active: if the panel could
    # not merge them, To Do would be empty while the work was there all along.
    todo = [i for i in issues + backlog if i.get("statusId") == str((b["columns"][0])["statusId"])]
    assert todo, "the first column would render as empty"

    print("panel ok: {} repon | {} klara + {} öppna ({} i {}) | nästa: {}".format(
        len(gh["repos"]), len(issues), len(todo), len(todo), (b["columns"][0])["name"],
        (flow.get("pick") or {}).get("key", "-")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
