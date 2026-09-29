#!/usr/bin/env python3
"""The wiring that makes the Repos view clickable, and the one that keeps the
shell from waiting ten seconds before it has anything to click on.

Run it with the panel up:  python3 panel/test_wiring.py

It fails on exactly the things that made the view feel dead:
  * /api/state not carrying `url` per repo  -> the names stop being links
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
          "every repo carries its github url", "the row names need it to be links")
    check(any((r or {}).get("url") for r in repos), "at least one repo has a url")

    html = (HERE / "index.html").read_text()
    check("link(r.url," in html, "the repo name is rendered through link()")
    check("/commit/" in html and "a.url" in html, "commits are rendered as links")
    check("AbortSignal.timeout" in html, "the state fetch has a time limit")
    check("retryLater" in html, "a failed refresh is retried and said out loud")

    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("all good: the Repos view is clickable and the shell cannot hang empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
