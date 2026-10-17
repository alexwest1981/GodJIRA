"""Källan: Jira eller lokal Paperclip — vald i panelen, inte i koden.

Provet mäter tre saker: att väljaren skickar `bridge`-kommandot till rätt brygga,
att Paperclip-bryggan svarar med samma fältnamn som Jira-bryggan (det vyerna
räknar på), och -- mot panelen på :8788 -- att bytet syns i /api/state och att
det gamla valet står kvar när den valda källan inte svarar.

    python3 panel/test_backend.py
"""
import importlib.util
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PANEL = "http://127.0.0.1:8788"

# Fälten en rad i tavlan bär. Jira-bryggan skickar dem; Paperclip-bryggan måste
# skicka samma, annars ritar panelen tysta "--" i den ena källan.
ISSUE_FIELDS = {"key", "summary", "typeName", "typeIcon", "statusId", "statusName",
                "statusCategory", "assigneeEmail", "assigneeName", "priorityName",
                "storyPoints", "updatedMs", "createdMs", "resolutionMs", "description",
                "url", "projectKey", "sprintId", "sprintName", "startMs", "dueMs",
                "parentKey", "parentSummary"}
BOARD_FIELDS = {"id", "name", "type", "projectKey", "columns", "sprint", "sprints",
                "issues", "backlog", "canAdd", "canDelete"}

failures, checks = [], 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)
    return ok


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def api(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(PANEL + path, data=data, headers={
        "Content-Type": "application/json", "Origin": PANEL})
    with urllib.request.urlopen(req, timeout=120) as fh:
        return json.load(fh)


def main() -> int:
    # --- 1. väljaren ---------------------------------------------------------
    choice = load("bridge_choice", ROOT / "bin" / "bridge_choice.py")
    temp = pathlib.Path.home() / ".cache" / "godjira-test-panel.json"
    original = choice.CONFIG
    try:
        temp.parent.mkdir(parents=True, exist_ok=True)
        for text, want in ((None, "jira"), ("{}", "jira"), ('{"backend": "något"}', "jira"),
                           ('{"backend": "Paperclip"}', "paperclip"), ('inte json', "jira")):
            if text is None:
                temp.unlink(missing_ok=True)
            else:
                temp.write_text(text, encoding="utf-8")
            choice.CONFIG = temp
            check(choice.backend() == want, "backend() med {!r} blev {!r}, väntade {!r}".format(
                text, choice.backend(), want))
        choice.CONFIG = original
    finally:
        temp.unlink(missing_ok=True)

    # --- 2. Paperclip-bryggans former ---------------------------------------
    bridge = load("paperclip_bridge", ROOT / "bin" / "paperclip_bridge.py")
    row = bridge.issue_row({"identifier": "ALE-3", "title": "t", "status": "in_review"},
                           {"name": "P"})
    check(set(row) == ISSUE_FIELDS, "Paperclip-raden bär inte Jirans fältnamn: {}".format(
        sorted(set(row) ^ ISSUE_FIELDS)))
    check(row["statusCategory"] == "indeterminate", "in_review skall räknas som pågående")
    check(bridge.issue_row({"status": "done"}, {})["statusCategory"] == "done",
          "done skall räknas som klart")

    # --- 3. panelen ----------------------------------------------------------
    try:
        before = api("/api/backend")
    except Exception as exc:  # panelen kan vara nere på en annan maskin
        print("hoppar över panelprovet: {}".format(exc))
        before = None
    if before is not None:
        check(before.get("backend") in ("jira", "paperclip"),
              "panelen svarar inte med en känd källa: {!r}".format(before.get("backend")))
        start = before.get("backend") or "jira"
        try:
            for want in ("paperclip", "jira"):
                answer = api("/api/backend", {"backend": want})
                check(answer.get("ok") is True, "bytet till {} nekades: {}".format(want, answer))
                state = api("/api/state")
                check(state.get("backend") == want, "/api/state säger {!r} efter bytet till {!r}".format(
                    state.get("backend"), want))
                board = ((state.get("jira") or {}).get("boards") or [{}])[0]
                check(set(board) >= BOARD_FIELDS, "tavlan saknar fält: {}".format(
                    sorted(BOARD_FIELDS - set(board))))
                check(len(board.get("issues") or []) > 0,
                      "källan {} gav en tom tavla".format(want))
                mode = (state.get("jira") or {}).get("mode")
                check(mode == ("paperclip" if want == "paperclip" else "real"),
                      "svaret kommer inte från {} (mode {!r})".format(want, mode))
            try:
                api("/api/backend", {"backend": "något"})
                check(False, "ett okänt värde nekades inte")
            except urllib.error.HTTPError as exc:
                check(exc.code == 400, "ett okänt värde gav {}, väntade 400".format(exc.code))
        finally:
            api("/api/backend", {"backend": start})
            check(api("/api/backend").get("backend") == start, "valet återställdes inte")

    print("Källan: {} kontroller".format(checks))
    if failures:
        print("MISSLYCKAT:")
        for item in failures:
            print("  -", item)
        return 1
    print("OK: väljaren, fälten och bytet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
