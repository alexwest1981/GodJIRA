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
import json
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

# --- rapporten (scrummästarens flik) -----------------------------------------
# Sprinten är 7, den har inget mål, och ingen har börjat på något -- men ärenden
# stängs. Två av de öppna saknar ägare, ett saknar skattning, ett har legat orört
# i tio dygn, och ett stängt i veckan saknar namn.
board = {
    "sprint": {"id": "7", "name": "Sprint 7", "goal": "", "startMs": NOW - 2 * DAY,
               "endMs": NOW + 3 * DAY},
    "issues": [
        issue("S-1", sprintId="7", statusCategory="done", resolutionMs=NOW - DAY,
              assigneeName="Ada", storyPoints=3),
        issue("S-2", sprintId="7", statusCategory="done", resolutionMs=NOW - 2 * DAY,
              assigneeName="", storyPoints=1),
        issue("S-3", sprintId="7", statusCategory="new", assigneeName="Ada", storyPoints=5),
        issue("S-4", sprintId="7", statusCategory="new", assigneeName="", storyPoints=2),
        issue("S-5", sprintId="7", statusCategory="new", assigneeName="Ada"),
        issue("S-6", sprintId="", statusCategory="new", assigneeName="",
              updatedMs=NOW - 10 * DAY, storyPoints=1),
        issue("S-7", sprintId="", statusCategory="done", resolutionMs=NOW - 3 * DAY,
              assigneeName="", storyPoints=1),
        issue("S-8", sprintId="", statusCategory="new", assigneeName="Bo", storyPoints=8,
              updatedMs=NOW - 10 * DAY),
    ],
    "backlog": [issue("S-9", sprintId="", statusCategory="new", assigneeName="")],
}
r = server.rapport({"boards": [board]}, now=NOW)
f = r["facts"]
check(f["issues"] == 5 and f["done"] == 2 and f["open"] == 3,
      "sprintens ärenden, klara och kvar räknas", f)
check(f["pointsDone"] == 4.0 and f["pointsOpen"] == 7.0, "poängen följer med", f)
check(f["inProgress"] == 0, "ingen är i arbete, och det mäts på statuskategorin", f)
check(r["sprint"]["daysLeft"] == 3, "dagar kvar räknas i kalenderdagar", r["sprint"])
check(r["sprint"]["start"] == NOW - 2 * DAY and r["sprint"]["end"] == NOW + 3 * DAY,
      "sprintens egna datum följer med", r["sprint"])
kinds = {row["kind"] for row in r["rows"]}
check({"goal", "noWork", "unowned", "stale", "unestimated", "closedUnowned"} <= kinds,
      "alla sex raderna tänds av sitt eget skäl", sorted(kinds))
check(all(row["level"] in ("block", "wait", "info") for row in r["rows"]),
      "allvarsgraden är en av de tre panelen känner", [row["level"] for row in r["rows"]])
goal = [row for row in r["rows"] if row["kind"] == "goal"][0]
check("inget mål" in goal["label"] and goal["detail"] == "Sprint 7",
      "målet saknas och sprinten namnges", goal)
nowork = [row for row in r["rows"] if row["kind"] == "noWork"][0]
check(nowork["count"] == 3 and nowork["detail"] == "3 stängda senaste veckan",
      "ingen har börjat, och talet den bygger på står där", nowork)
unowned = [row for row in r["rows"] if row["kind"] == "unowned"][0]
check(unowned["count"] == 1 and unowned["keys"] == ["S-4"],
      "bara sprintens egna ägarlösa räknas", unowned)
stale = [row for row in r["rows"] if row["kind"] == "stale"][0]
check(stale["count"] == 2 and stale["keys"][0] == "S-6",
      "orört listas äldst först, och utanför sprinten också", stale)
unestimated = [row for row in r["rows"] if row["kind"] == "unestimated"][0]
check(unestimated["count"] == 2 and "S-5" in unestimated["keys"] and "S-9" in unestimated["keys"],
      "utan skattning gäller allt öppet, inte bara sprinten", unestimated)

# En epic bär inte sin egen skattning: har barnen poäng skall epiken inte tjatas om.
epic_board = {"sprint": {"id": "7", "name": "Sprint 7", "startMs": NOW, "endMs": NOW + 2 * DAY},
              "issues": [issue("S-1", typeName="Epic", summary="A. Området"),
                         issue("S-2", typeName="Story", parentKey="S-1", storyPoints=3),
                         issue("S-3", typeName="Epic", summary="B. Tomt"),
                         issue("S-4", typeName="Story", parentKey="S-3")],
              "backlog": []}
u = server.rapport({"boards": [epic_board]}, now=NOW)
osk = [row for row in u["rows"] if row["kind"] == "unestimated"]
check(len(osk) == 1 and "S-1" not in osk[0]["keys"] and "S-3" in osk[0]["keys"],
      "epiken med skattade barn tjatar inte, epiken utan skattning gör det", osk)
closed = [row for row in r["rows"] if row["kind"] == "closedUnowned"][0]
check(closed["count"] == 2, "stängda utan ansvarig senaste veckan", closed)
check([p["name"] for p in r["people"]] == ["Ada"] and r["people"][0]["open"] == 2,
      "per person: öppna i sprinten, flest först, och ingen påhittad för oassignerade",
      r["people"])
check(r["people"][0]["points"] == 5.0, "personens poäng är de öppnas", r["people"][0])
check(all(p["name"] for p in r["people"]), "ingen rad utan namn i personlistan", r["people"])

# Bedömningen: läget i tid och poäng, styrkan, svagheterna och prognosen. Talen skall
# komma ur sprintens egna datum och ärendenas egna poäng -- inte ur en gissning.
p = r["position"]
check(p["runDays"] == 2 and p["timeShare"] == 40.0,
      "tiden räknas på sprintens egna datum", p)
check(p["points"] == 11.0 and p["pointsShare"] == 36.4,
      "poängandelen är sprintens klara mot sprintens hela", p)
check(p["pace"] == 2.0 and p["needed"] == 2.3,
      "takten hittills och takten som krävs", p)
check(p["expected"] == 6.0 and p["expectedLeft"] == 5.0,
      "vid dagens slut: en dag till i samma takt, aldrig mer än scenariot", p)
check([s["label"] for s in r["strengths"]] == ["ärenden stängs"],
      "styrkan som bär ett tal (och ingen påhittad)", r["strengths"])
check([w["label"] for w in r["weaknesses"]] == ["efter plan", "takten räcker inte"],
      "svagheterna: andelen av poängen och takten", r["weaknesses"])
check([n["label"] for n in r["forecast"]] == ["Vid dagens slut"] and
      r["forecast"][0]["detail"] == "6.0 p klara, 5.0 p kvar om takten håller",
      "prognosen namnges och bär sina tal", r["forecast"])

# En frisk tavla skall ge styrkor, inte en tom rapport: det är hela skälet till att
# bedömningen finns. Ett ärende i taget kontrolleras via etiketterna.
rent = {"sprint": {"id": "7", "name": "Sprint 7", "goal": "målet", "startMs": NOW - DAY,
                   "endMs": NOW + DAY},
        "issues": [issue("S-1", sprintId="7", assigneeName="Ada", storyPoints=1, updatedMs=NOW),
                   issue("S-2", sprintId="7", statusCategory="done", resolutionMs=NOW,
                         assigneeName="Ada", storyPoints=3)],
        "backlog": []}
r_frisk = server.rapport({"boards": [rent]}, now=NOW)
styr = {s["label"]: s["detail"] for s in r_frisk["strengths"]}
check({"före plan", "takten håller", "ärenden stängs", "tavlan är i ordning"} <= set(styr),
      "en frisk tavla ger sina styrkor", sorted(styr))
check(styr["tavlan är i ordning"] == "1 öppna · alla med ägare och skattning · inget orört i 7 dygn",
      "styrkan väger öppna, ägare, skattning och orördhet", styr.get("tavlan är i ordning"))

# Ett mål som finns skall tysta raden, och ett pågående ärende skall tysta "ingen har börjat".
lugn = json.loads(json.dumps(board))
lugn["sprint"]["goal"] = "leverera bokningen"
lugn["issues"][2]["statusCategory"] = "indeterminate"
r2 = server.rapport({"boards": [lugn]}, now=NOW)
kinds2 = {row["kind"] for row in r2["rows"]}
check("goal" not in kinds2 and "noWork" not in kinds2,
      "mål och påbörjat arbete tar bort sina rader", sorted(kinds2))
check(r2["facts"]["inProgress"] == 1, "det pågående ärendet räknas", r2["facts"])

# Sista dagen: tiden själv är en svaghet, och taktkravet är hela återstoden.
slut = json.loads(json.dumps(lugn))
slut["sprint"]["endMs"] = NOW + DAY
r_slut = server.rapport({"boards": [slut]}, now=NOW)
svag = {w["label"]: w["detail"] for w in r_slut["weaknesses"]}
check(svag.get("tiden är snart slut") == "1 d kvar · 3 ärenden (7.0 p) kvar",
      "sista dagen står som en svaghet med sina egna tal", svag)
check(r_slut["position"]["needed"] == 7.0
      and "arbete pågår" in {s["label"] for s in r_slut["strengths"]},
      "en dag kvar ger hela återstoden som taktkrav, och det pågående arbetet syns",
      r_slut["position"])

# Utan aktiv sprint: en rad som säger det, i stället för en krasch eller en tom ruta.
utan = server.rapport({"boards": [{"sprint": {}, "issues": [issue("S-1")], "backlog": []}]}, now=NOW)
check(utan["rows"] and utan["rows"][0]["kind"] == "noSprint" and utan["facts"]["issues"] == 0,
      "utan aktiv sprint sägs det rakt ut", utan["rows"])
check(utan["strengths"] == [] and utan["weaknesses"] == [] and utan["forecast"] == []
      and utan["position"]["points"] == 0,
      "utan sprint blir bedömningen tom, inte påhittad", utan["position"])
check(server.rapport({}, now=NOW)["rows"][0]["kind"] == "noSprint",
      "ett tomt svar ger en rad, inte en krasch")
check(server.rapport({}, now=NOW)["strengths"] == [],
      "ett tomt svar ger ingen påhittad styrka")

# --- eller utan klocka --------------------------------------------------------
fresh = server.summary([issue("S-1", statusCategory="done", resolutionMs=time.time() * 1000)], now=0)
check(fresh["completed"] == 1, "utan givet nu används klockan", fresh["completed"])

if fails:
    print("projektets siffror: {} kontroller, {} fel".format(checks, len(fails)))
    for what, detail in fails:
        print("  FEL  {}  {}".format(what, detail))
    raise SystemExit(1)
print("projektets siffror: {} kontroller, 0 fel".format(checks))
