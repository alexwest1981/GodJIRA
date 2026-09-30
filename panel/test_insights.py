#!/usr/bin/env python3
"""Projektets siffror: sammanfattningen, tidslinjen och utvecklingen.

Räknade ur ärendena, med kända datum -- så att fönstren, andelarna och staplarna
kan provas utan Jira, GitHub eller webbläsare.

Run it:  python3 panel/test_insights.py

Det som faktiskt kan gå fel utan att någon märker det:
  * "klara senaste 7 dygnen" räknas på updated     -> varje rörelse i ett stängt
                                                      ärende räknas som att det
                                                      blev klart (felet som fanns)
  * fönstret är öppet i fel ände                   -> framtida ärenden räknas in
  * andelarna summerar inte till 100               -> donuten ljuger
  * epicens framdrift räknar fel barn              -> raden visar fel
  * en stapel hamnar utanför 0-100 %               -> ritningen hamnar utanför rutan
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAY = 86400000.0
NOW = 1_800_000_000_000.0            # fast klockslag: proven får inte bero på klockan
checks = 0
fails: list = []


def check(ok: bool, what: str, detail: object = "") -> None:
    global checks
    checks += 1
    if not ok:
        fails.append((what, detail))


spec = importlib.util.spec_from_file_location("server", HERE / "server.py")
assert spec and spec.loader, "server.py kunde inte läsas in"
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def issue(key, **kw):
    base = {"key": key, "summary": "ärende " + key, "statusCategory": "new",
            "typeName": "Task", "priorityName": "Medium", "updatedMs": NOW - DAY}
    base.update(kw)
    return base


# --- sammanfattningen ---------------------------------------------------------
issues = [
    issue("S-1", statusCategory="done", resolutionMs=NOW - 6 * DAY, updatedMs=NOW - DAY),
    # Klart för nio dygn sedan men rört i dag: får inte räknas som klart i dag.
    issue("S-2", statusCategory="done", resolutionMs=NOW - 9 * DAY, updatedMs=NOW - 1000),
    issue("S-3", statusCategory="indeterminate", createdMs=NOW - 2 * DAY,
          dueMs=NOW + 3 * DAY, priorityName="High"),
    issue("S-4", createdMs=NOW - 1 * DAY, dueMs=NOW - 2 * DAY, priorityName="High",
          typeName="Bug", parentKey="S-9"),
    issue("S-9", typeName="Epic", summary="epic"),
    # Framtiden: skapad och förfallen ligger utanför fönstret och skall inte räknas.
    issue("S-5", createdMs=NOW + 3 * DAY, dueMs=NOW - 30 * DAY, statusCategory="done",
          resolutionMs=NOW + 2 * DAY, typeName="Bug"),
]
s = server.summary(issues, now=NOW)
check(s["completed"] == 1, "klara räknas på avslutsdatum, inte på rörelse", s["completed"])
check(s["updated"] == len(issues), "ändrade räknas på updated", s["updated"])
check(s["created"] == 2, "skapade räknas på skapad", s["created"])
check(s["dueSoon"] == 1, "förfaller inom sju dygn, och bara det", s["dueSoon"])
check(s["status"]["total"] == len(issues), "totalen är alla ärenden", s["status"]["total"])
check(s["status"]["done"] == 3 and s["status"]["inProgress"] == 1 and s["status"]["todo"] == 2,
      "statusdelningen räknar kategorierna", s["status"])
shares = s["status"]["doneShare"] + s["status"]["inProgressShare"] + s["status"]["todoShare"]
check(abs(shares - 100.0) <= 0.2, "andelarna summerar till 100", shares)
seen = [row["name"].lower() for row in s["priorities"]]
check(seen == [n for n in server.PRIORITY_ORDER if n in seen],
      "prioriteringarna står i allvarsordning, inte i storleksordning", seen)
check([row["count"] for row in s["types"]] == sorted([row["count"] for row in s["types"]],
                                                     reverse=True),
      "typerna är störst först", s["types"])
check(sum(row["count"] for row in s["priorities"]) == len(issues),
      "varje ärende hamnar i exakt en prioritering", s["priorities"])
check(all(0 <= row["share"] <= 100 for row in s["types"]), "andelar ligger i 0-100", s["types"])
times = [r["updatedMs"] for r in s["recent"]]
check(len(s["recent"]) == min(8, len(issues)) and times == sorted(times, reverse=True)
      and s["recent"][0]["key"] == "S-2",
      "senast rörda är nyast först, topp åtta", [r["key"] for r in s["recent"][:3]])
check(server.summary([], now=NOW)["status"]["doneShare"] == 0.0, "tomt projekt delar inte med noll")

# --- tidslinjen ---------------------------------------------------------------
kids = [issue("S-9", typeName="Epic", startMs=NOW - 20 * DAY, dueMs=NOW + 10 * DAY),
        issue("S-4", parentKey="S-9", statusCategory="done",
              startMs=NOW - 18 * DAY, dueMs=NOW - 10 * DAY),
        issue("S-6", parentKey="S-9", startMs=NOW - 9 * DAY, dueMs=NOW + 4 * DAY)]
sprints = [{"id": "1", "name": "Sprint 1", "state": "closed",
            "startMs": NOW - 28 * DAY, "endMs": NOW - 14 * DAY},
           {"id": "2", "name": "Sprint 2", "state": "active",
            "startMs": NOW - 13 * DAY, "endMs": NOW + 1 * DAY}]
t = server.timeline(kids, sprints, now=NOW)
check(len(t["rows"]) == 1 and t["rows"][0]["key"] == "S-9", "bara epics blir rader", t["rows"])
row = t["rows"][0]
check(row["children"] == 2 and row["childrenDone"] == 1, "framdriften räknar barnen",
      (row["children"], row["childrenDone"]))
check(row["startMs"] == NOW - 20 * DAY and row["endMs"] == NOW + 10 * DAY,
      "epicens egen kant går före barnens", (row["startMs"], row["endMs"]))
check(len(t["sprints"]) == 2 and t["sprints"][0]["left"] <= t["sprints"][1]["left"],
      "sprintarna i tidsordning", t["sprints"])
undated = server.timeline([issue("S-1", typeName="Epic")], sprints, now=NOW)["rows"]
check(undated and undated[0]["left"] is None and undated[0]["width"] is None,
      "en rad utan datum ritar ingen stapel", undated)
bars = [r for r in t["rows"] if r["left"] is not None] + t["sprints"]
check(all(0 <= b["left"] <= 100 for b in bars), "ingen stapel börjar utanför rutan",
      [b["left"] for b in bars])
check(all(0 < b["width"] and b["left"] + b["width"] <= 100.1 for b in bars),
      "ingen stapel sticker ut", [(b["left"], b["width"]) for b in bars])
check(0 <= t["todayLeft"] <= 100, "idag-linjen ligger inom spannet", t["todayLeft"])
check(t["spanDays"] >= 38, "spannet rymmer sista sprintens slut", t["spanDays"])
check(sum(m["width"] for m in t["months"]) > 95,
      "månaderna täcker spannet", t["months"])
check(t["months"] == sorted(t["months"], key=lambda m: m["left"]), "månaderna i ordning")
empty = server.timeline([issue("S-1")], [], now=NOW)
check(empty["rows"] == [] and empty["spanDays"] >= 1, "tidslinje utan datum går att rita", empty)

# --- utvecklingen -------------------------------------------------------------
jira = {"boards": [{"issues": [
    issue("S-1", statusCategory="done", resolutionMs=NOW - 2 * DAY),
    issue("S-2", typeName="Bug", dueMs=NOW - DAY),
    issue("S-3", typeName="Bug", statusCategory="done", resolutionMs=NOW - DAY),
    issue("S-4", typeName="Bug", statusCategory="done", dueMs=NOW - 5 * DAY, resolutionMs=NOW - DAY),
    issue("S-5", sprintId="7"),
    issue("S-6", sprintId="7"),
], "sprint": {"id": "7"}}]}
d = server.development(jira, {"repos": [{"name": "r", "primaryLanguage": "Python"}],
                              "pullRequests": [{"title": "p", "url": "u", "repo": "r"}]}, now=NOW)
check(d["workItems"] == 3, "klara denna vecka räknas på avslutsdatum", d["workItems"])
check(d["openBugs"] == 1, "öppna buggar räknar bara öppna", d["openBugs"])
check(d["overdue"] == 1, "försenade räknar inte klara ärenden", d["overdue"])
check(d["inSprint"] == 2, "aktiva sprinten räknas", d["inSprint"])
check(d["repos"][0]["language"] == "Python" and d["pullRequests"][0]["repo"] == "r",
      "repon och pr:erna följer med", (d["repos"][0], d["pullRequests"][0]))
check(any("ledtid" in n for n in d["notes"]), "det som inte går att visa sägs rakt ut", d["notes"])
check(server.development({}, {}, now=NOW)["openBugs"] == 0, "utan svar blir det nollor, inte krasch")

# --- eller utan klocka --------------------------------------------------------
fresh = server.summary([issue("S-1", statusCategory="done", resolutionMs=time.time() * 1000)], now=0)
check(fresh["completed"] == 1, "utan givet nu används klockan", fresh["completed"])

if fails:
    print("projektets siffror: {} kontroller, {} fel".format(checks, len(fails)))
    for what, detail in fails:
        print("  FEL  {}  {}".format(what, detail))
    raise SystemExit(1)
print("projektets siffror: {} kontroller, 0 fel".format(checks))
