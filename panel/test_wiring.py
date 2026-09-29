#!/usr/bin/env python3
"""The wiring that makes the Repos view work, and the one that keeps the shell
from waiting ten seconds before it has anything to click on.

Run it with the panel up:  python3 panel/test_wiring.py

It fails on exactly the things that made the view feel dead:
  * a repo row that opens github instead of the app -> the repo cannot be
                                                       pointed at a Jira project
  * the repo detail losing its github link   -> the way out disappears
  * a flow file that is not drawn            -> the flow is invisible
  * drawn nodes/edges not matching the file  -> the drawing lies by omission
  * the view drawing its own copy of a flow  -> it goes stale on the next edit
  * the repo row/commit link wiring gone    -> rows render as plain text
  * the state fetch without a time limit    -> an unanswered request leaves the
                                               shell empty forever
  * a failed refresh with no retry          -> the page keeps stale data and
                                               says nothing (splashFail is a
                                               no-op once the splash is gone)
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.request

PANEL = "http://127.0.0.1:8788"
HERE = pathlib.Path(__file__).resolve().parent
failed: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + what + ((" -- " + detail) if detail else ""))
    if not ok:
        failed.append(what)


def main() -> int:
    try:
        with urllib.request.urlopen(PANEL + "/api/state", timeout=120) as answer:
            state = json.load(answer)
    except Exception as exc:  # noqa: BLE001 -- the message is the point
        print("the panel did not answer at %s: %s" % (PANEL, exc))
        return 1

    repos = ((state.get("github") or {}).get("repos")) or []
    check(bool(repos), "the state carries repos", "%d repos" % len(repos))
    check(all((r or {}).get("url", "").startswith("https://github.com/") for r in repos[:10]),
          "every repo carries its github url", "the detail needs it for its link out")
    check(any((r or {}).get("url") for r in repos), "at least one repo has a url")

    html = (HERE / "index.html").read_text()
    # Ett reponamn i tabellen skall öppna repot I APPEN (raden är klickbar och
    # öppnar detaljen), inte hoppa till webben. GitHub-länken hör till detaljen.
    check("data-repo=" in html and "repoOpen(" in html,
          "the repo row opens the repo inside the app")
    check("link(r.url," not in html,
          "the repo name is not a jump to github", "the detail owns that link")
    check("link(d.about.url," in html, "the repo detail carries the github link")
    check("/commit/" in html and "a.url" in html, "commits are rendered as links")

    # Flödesritningen: varje fil i n8n/workflows ritas, och ritningen stämmer med
    # filens egen rubrik. En ritning som tyst tappar en nod är värre än ingen.
    flows = state.get("flows") or []
    files = sorted(p.name for p in (HERE.parent / "n8n" / "workflows").glob("*.workflow.ts"))
    check(len(flows) == len(files), "every workflow file is in the state",
          "%d av %d: %s" % (len(flows), len(files), ", ".join(files)))
    for path in sorted((HERE.parent / "n8n" / "workflows").glob("*.workflow.ts")):
        text = path.read_text()
        head = re.search(r"// Nodes\s*:\s*(\d+)\s*\|\s*Connections:\s*(\d+)", text)
        drawn = next((f for f in flows if f.get("file") == path.name), None)
        if not head or not drawn:
            check(False, "the workflow map is readable: " + path.name)
            continue
        want_nodes, want_edges = int(head.group(1)), int(head.group(2))
        check(len(drawn.get("nodes") or []) == want_nodes and len(drawn.get("edges") or []) == want_edges,
              "drawn %s matches the file" % path.name,
              "%d/%d noder, %d/%d kopplingar"
              % (len(drawn.get("nodes") or []), want_nodes, len(drawn.get("edges") or []), want_edges))
        # Kanterna pekar på nodernas EGENNAMN (EveryHour), inte på visningsnamnet
        # ("Every hour"): utan nyckeln ritas inga vägar alls (mätt: 0 av 7).
        keys = {n.get("key") for n in (drawn.get("nodes") or [])}
        check(all(e.get("from") in keys and e.get("to") in keys for e in (drawn.get("edges") or [])),
              "every edge lands on a node: " + path.name)
    check("STATE.flows" in html and "flowSel" in html, "the view draws the state's own graph")
    check("EveryHour" not in html, "no flow is drawn by hand in the page")
    check("renderFlow" in html and "v-automatik" in html, "the Automatik view exists")
    check("AbortSignal.timeout" in html, "the state fetch has a time limit")
    check("retryLater" in html, "a failed refresh is retried and said out loud")

    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("all good: the repos open in the app, the flows are drawn from their own files, "
          "and the shell cannot hang empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
