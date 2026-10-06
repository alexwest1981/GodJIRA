#!/usr/bin/env python3
"""Kön: what som väntar på dig, ur ett svar panelen redan har.

Ren funktion, handgjorda states -- ingen panel, inga anrop, inga filer. Provet mäter
också de tysta fallen: ett *klart* ärende hos dig är inget som väntar, och en nyckel utan
känt utgångsdatum säger ingenting.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import server  # noqa: E402

ME = {"displayName": "Alex Weström", "email": "alex@example.se"}


def state_of(*, issues=(), backlog=(), runs=(), prs=(), gissues=(), token=None, account=None,
             watch=None):
    return {
        "jira": {"account": account or ME,
                 "boards": [{"issues": list(issues), "backlog": list(backlog)}]},
        "runs": {"runs": list(runs)},
        "github": {"pullRequests": list(prs), "issues": list(gissues)},
        "watch": watch if watch is not None else {},
        "token": token if token is not None else {"present": True, "connected": True,
                                                  "daysLeft": 30, "warnDays": 14, "alert": ""},
    }


def issue(key, *, category="new", who="name", when=1000):
    row = {"key": key, "statusCategory": category, "updatedMs": when}
    if who == "name":
        row.update(assigneeName=ME["displayName"])
    elif who == "mail":
        row.update(assigneeEmail=ME["email"])
    elif who == "annan":
        row.update(assigneeName="Någon annan", assigneeEmail="nagon@example.se")
    return row


checks, bad = [], []


def check(ok, what):
    checks.append(what)
    if not ok:
        bad.append(what)
        print("FEL: " + what)


# Ingenting väntar: nyckeln lever, inga ärenden hos dig, inga ci_runs, inga PR:er.
check(server.needs_list(state_of()) == [], "en tom queue är tom")

# En körning som öppnat en PR väntar på mergen; en som inte kom igenom väntar på ett beslut.
queue = server.needs_list(state_of(runs=[{"key": "SCRUM-9", "pr": "https://x/pull/1", "ok": True},
                                      {"key": "SCRUM-8", "ok": False, "note": "behöver ett beslut"}]))
check([n["label"] for n in queue] == ["körningen väntar på din merge", "körningen gick inte igenom"],
      "körningarna ger sina två rader")
check(queue[0]["url"].endswith("/pull/1") and "behöver ett beslut" in queue[1]["detail"],
      "raden bär PR-adressen och körningens egen orsak")

# Men en körning som föll på ett ärende som redan är stängt tjatar inte: ärendet är klart,
# och liggarens rad står kvar under Körningar. Samma körning på ett öppet ärende väntar.
queue = server.needs_list(state_of(issues=[issue("SCRUM-7", category="done")],
                                   runs=[{"key": "SCRUM-7", "ok": False, "note": "föll"}]))
check(not [n for n in queue if n["kind"] == "failed"], "en körning på ett stängt ärende väntar inte")
queue = server.needs_list(state_of(issues=[issue("SCRUM-7")],
                                   runs=[{"key": "SCRUM-7", "ok": False, "note": "föll"}]))
check([n for n in queue if n["kind"] == "failed"], "samma körning på ett öppet ärende väntar")

# Ärenden: bara dina egna, bara de som inte är klara. Talet är hela talet.
items = [issue("SCRUM-1", when=100), issue("SCRUM-2", who="mail", when=200),
       issue("SCRUM-3", category="done", when=300), issue("SCRUM-4", who="annan", when=400),
       issue("SCRUM-5", category="indeterminate", when=500)]
queue = server.needs_list(state_of(issues=items))
row = [n for n in queue if n["kind"] == "issues"][0]
check(row["count"] == 3 and row["keys"] == ["SCRUM-5", "SCRUM-2", "SCRUM-1"],
      "ärendena räknas på ansvarig och status, nyast först")
check(all("SCRUM-3" != k and "SCRUM-4" != k for k in row["keys"]),
      "ett klart ärende och någon annans ärende väntar inte")

# Backloggen är samma källa som tablan: ett öppet ärende där väntar precis lika mycket,
# och en dubblett (samma nyckel i båda listorna) får inte räknas två gånger.
queue = server.needs_list(state_of(issues=[issue("SCRUM-1")], backlog=[issue("SCRUM-2", when=200),
                                                                   issue("SCRUM-1", when=100)]))
row = [n for n in queue if n["kind"] == "issues"][0]
check(row["count"] == 2 and sorted(row["keys"]) == ["SCRUM-1", "SCRUM-2"],
      "backloggen räknas med, och en dubblett räknas en gång")

# Utan ansvarig: den som ingen äger väntar på ett beslut.
queue = server.needs_list(state_of(issues=[issue("SCRUM-1", who="ingen", when=100),
                                        issue("SCRUM-2", who="ingen", when=200)]))
row = [n for n in queue if n["kind"] == "unowned"]
check(row and row[0]["count"] == 2 and row[0]["label"] == "utan ansvarig",
      "open_items ärenden utan ansvarig blir en egen row")

# GitHub-ärenden (open_items, mine) är också något som väntar.
queue = server.needs_list(state_of(gissues=[{"title": "Byt färg", "number": 7}]))
check([n for n in queue if n["kind"] == "gh-issues"][0]["count"] == 1,
      "open_items GitHub-ärenden räknas")

# Åtta ärenden men bara fyra nycklar: talet skall vara åtta.
queue = server.needs_list(state_of(issues=[issue("SCRUM-%d" % i, when=i) for i in range(1, 9)]))
row = [n for n in queue if n["kind"] == "issues"][0]
check(row["count"] == 8 and len(row["keys"]) == 4, "en kapad lista säger ändå hela talet")

# PR:er.
queue = server.needs_list(state_of(prs=[{"title": "Byt färg", "number": 3}, {"title": "Fix", "number": 4}]))
check([n for n in queue if n["kind"] == "prs"][0]["count"] == 2, "två open_items PR:er blir en row med talet 2")

# Vad som körs: en tjänst som står still blockerar, en som kör en äldre commit än sin gren
# väntar. En tjänst i takt, och ett okänt svar, larmar inte.
drift = {"sajter": [
    {"nyckel": "web", "namn": "Web", "lage": "active",
     "drift": {"ok": True, "commit": "a1b2c3", "behind": 3, "upstream": "origin/main"}},
    {"nyckel": "api", "namn": "Api", "lage": "failed", "drift": {"ok": False, "note": "ingen katalog"}},
    {"nyckel": "lugn", "namn": "Lugn", "lage": "active",
     "drift": {"ok": True, "commit": "d4e5f6", "behind": 0, "upstream": "origin/main"}}]}
queue = server.needs_list(dict(state_of(), drift=drift))
sorter = sorted((n["kind"], n["level"]) for n in queue if n["kind"] in ("service", "deploy"))
check(sorter == [("deploy", "wait"), ("service", "block")],
      "en stillastående tjänst blockerar, en gammal commit väntar")
check([n for n in queue if n["kind"] == "deploy"][0]["detail"] == "Web · 3 efter origin/main",
      "raden säger vilken sajt och hur långt efter")

# Jobben som skall köra av sig själva: en backning som föll blockerar, en som tystnat väntar,
# en som kör just nu säger ingenting, och en i tid är tyst.
vakter = {"sajter": [], "vakter": [
    {"namn": "Backningen", "max_hours": 30, "lage": "inactive", "result": "exit-code", "age_h": 15.0},
    {"namn": "Momentos backning", "max_hours": 30, "lage": "inactive", "result": "success", "age_h": 190.0},
    {"namn": "Momentos gallring", "max_hours": 30, "lage": "activating", "result": "success", "age_h": None},
    {"namn": "Frisk", "max_hours": 30, "lage": "inactive", "result": "success", "age_h": 2.0}]}
queue = server.needs_list(dict(state_of(), drift=vakter))
backuper = [n for n in queue if n["kind"] == "backup"]
check(len(backuper) == 2, "bara de två trasiga backningarna hamnar i kön (fick %d)" % len(backuper))
check(sorted(n["level"] for n in backuper) == ["block", "wait"],
      "den som föll blockerar, den som tystnat väntar")
check([n for n in backuper if n["level"] == "block"][0]["detail"] == "Backningen · exit-code · 15.0 h sedan",
      "block-raden säger namn, resultat och hur länge sedan")
check([n for n in backuper if n["level"] == "wait"][0]["detail"] == "Momentos backning · 7 dygn sedan",
      "den väntande raden räknar dygn, inte timmar")

# Nyckeln: ett okänt utgångsdatum säger ingenting; en räknad utgång larmar; ett avvisat svar
# blockerar och hamnar först.
check(not [n for n in server.needs_list(state_of(token={"present": True, "connected": True,
                                                        "daysLeft": None, "warnDays": 14}))
           if n["kind"] == "key"], "ett okänt utgångsdatum är inget alarm")
alarm = [n for n in server.needs_list(state_of(token={"present": True, "connected": True,
                                                     "daysLeft": 3, "warnDays": 14}))
        if n["kind"] == "key"][0]
check(alarm["level"] == "wait" and alarm["label"] == "nyckeln går ut", "tre dygn kvar larmar")
dead = server.needs_list(state_of(token={"present": True, "connected": False, "alert": "401"}))
check(dead[0]["kind"] == "key" and dead[0]["level"] == "block", "en nyckel Jira avvisar blockerar")

# Bygget: rött på huvudgrenen blockerar och hamnar först. Ett grönt bygge larmar inte.
red_build = {"repos": [{"repo": "mig/repo", "state": "red", "branch": "main", "workflow": "CI",
                   "url": "https://x/actions/1"}]}
queue = server.needs_list(dict(state_of(), ci=red_build))
check(queue[0]["kind"] == "ci" and queue[0]["level"] == "block" and queue[0]["label"] == "bygget är rött",
      "ett rött bygge blir kön första rad")
check(not [n for n in server.needs_list(dict(state_of(), ci={"repos": [{"state": "green"}]}))
           if n["kind"] == "ci"], "ett grönt bygge larmar inte")

# ci_of: den nyaste körningen PÅ HUVUDGRENEN avgör -- en röd gren man jobbar i är inte
# "bygget är rött", och en körning som inte är klar är inget svar än.
ci_runs = [{"headBranch": "main", "status": "completed", "conclusion": "failure",
              "createdAt": "2026-10-05T10:00:00Z", "workflowName": "CI", "url": "u"},
             {"headBranch": "develop", "status": "completed", "conclusion": "success",
              "createdAt": "2026-10-05T12:00:00Z"}]
check(server.ci_of(ci_runs, "main")["state"] == "red", "den nyaste körningen på huvudgrenen avgör")
check(server.ci_of(ci_runs, "release")["state"] == "none", "en gren utan ci_runs säger inget")
check(server.ci_of([{"headBranch": "main", "status": "in_progress", "conclusion": "",
                     "createdAt": "x"}], "main")["state"] == "running",
      "en körning som inte är klar är inget svar")
check(server.ci_of([], "main")["state"] == "none", "inga byggen alls är inget alarm")

# Ordningen: block före wait före info.
queue = server.needs_list(state_of(issues=[issue("SCRUM-1")],
                                token={"present": True, "connected": False},
                                prs=[{"title": "x"}],
                                runs=[{"key": "SCRUM-7", "ok": False}]))
check([n["level"] for n in queue] == sorted([n["level"] for n in queue], key=lambda l: server.NEED_ORDER[l]),
      "block före wait före info")

# Bevakningen: ett problem blir en rad i kon, i samma form som de andra -- och ett tomt
# svar blir ingen rad. "Inget fel" och "ingen har tittat" får inte se likadana ut.
queue = server.needs_list(state_of(watch={"at": "2026-10-06T09:00:00Z",
                                          "problem": [{"kind": "sajt",
                                                       "text": "Minnoria kör inte (failed)",
                                                       "view": "sites"}]}))
check(len(queue) == 1 and queue[0]["kind"] == "watch" and queue[0]["level"] == "block"
      and "Minnoria" in queue[0]["label"] and queue[0]["view"] == "sites",
      "bevakningens problem blir en rad i kon")
check(server.needs_list(state_of(watch={"at": "x", "problem": []})) == [],
      "en tom bevakning ger ingen rad")

# Ingen nyckel alls: dörren till Kom igång, inte en tom skärm.
queue = server.needs_list(state_of(token={"present": False, "connected": False}))
check(queue and queue[0]["view"] == "setup", "utan nyckel pekar kön på setupen")

print("Kön: %d checks" % len(checks))
print("OK: alla checks passerar" if not bad else "FEL: %d av %d" % (len(bad), len(checks)))
raise SystemExit(1 if bad else 0)
