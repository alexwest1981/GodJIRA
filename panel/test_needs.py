#!/usr/bin/env python3
"""Kön: vad som väntar på dig, ur ett svar panelen redan har.

Ren funktion, handgjorda states -- ingen panel, inga anrop, inga filer. Provet mäter
också de tysta fallen: ett *klart* ärende hos dig är inget som väntar, och en nyckel utan
känt utgångsdatum säger ingenting.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import server  # noqa: E402

MIG = {"displayName": "Alex Weström", "email": "alex@example.se"}


def state_av(*, issues=(), backlog=(), runs=(), prs=(), gissues=(), token=None, account=None):
    return {
        "jira": {"account": account or MIG,
                 "boards": [{"issues": list(issues), "backlog": list(backlog)}]},
        "runs": {"runs": list(runs)},
        "github": {"pullRequests": list(prs), "issues": list(gissues)},
        "token": token if token is not None else {"present": True, "connected": True,
                                                  "daysLeft": 30, "warnDays": 14, "alert": ""},
    }


def arende(key, *, kategori="new", vem="name", när=1000):
    rad = {"key": key, "statusCategory": kategori, "updatedMs": när}
    if vem == "name":
        rad.update(assigneeName=MIG["displayName"])
    elif vem == "mail":
        rad.update(assigneeEmail=MIG["email"])
    elif vem == "annan":
        rad.update(assigneeName="Någon annan", assigneeEmail="nagon@example.se")
    return rad


kontroller, fel = [], []


def check(villkor, vad):
    kontroller.append(vad)
    if not villkor:
        fel.append(vad)
        print("FEL: " + vad)


# Ingenting väntar: nyckeln lever, inga ärenden hos dig, inga körningar, inga PR:er.
check(server.needs_list(state_av()) == [], "en tom kö är tom")

# En körning som öppnat en PR väntar på mergen; en som inte kom igenom väntar på ett beslut.
kö = server.needs_list(state_av(runs=[{"key": "SCRUM-9", "pr": "https://x/pull/1", "ok": True},
                                      {"key": "SCRUM-8", "ok": False, "note": "behöver ett beslut"}]))
check([n["label"] for n in kö] == ["körningen väntar på din merge", "körningen gick inte igenom"],
      "körningarna ger sina två rader")
check(kö[0]["url"].endswith("/pull/1") and "behöver ett beslut" in kö[1]["detail"],
      "raden bär PR-adressen och körningens egen orsak")

# Ärenden: bara dina egna, bara de som inte är klara. Talet är hela talet.
iss = [arende("SCRUM-1", när=100), arende("SCRUM-2", vem="mail", när=200),
       arende("SCRUM-3", kategori="done", när=300), arende("SCRUM-4", vem="annan", när=400),
       arende("SCRUM-5", kategori="indeterminate", när=500)]
kö = server.needs_list(state_av(issues=iss))
rad = [n for n in kö if n["kind"] == "issues"][0]
check(rad["count"] == 3 and rad["keys"] == ["SCRUM-5", "SCRUM-2", "SCRUM-1"],
      "ärendena räknas på ansvarig och status, nyast först")
check(all("SCRUM-3" != k and "SCRUM-4" != k for k in rad["keys"]),
      "ett klart ärende och någon annans ärende väntar inte")

# Backloggen är samma källa som tablan: ett öppet ärende där väntar precis lika mycket,
# och en dubblett (samma nyckel i båda listorna) får inte räknas två gånger.
kö = server.needs_list(state_av(issues=[arende("SCRUM-1")], backlog=[arende("SCRUM-2", när=200),
                                                                   arende("SCRUM-1", när=100)]))
rad = [n for n in kö if n["kind"] == "issues"][0]
check(rad["count"] == 2 and sorted(rad["keys"]) == ["SCRUM-1", "SCRUM-2"],
      "backloggen räknas med, och en dubblett räknas en gång")

# Utan ansvarig: den som ingen äger väntar på ett beslut.
kö = server.needs_list(state_av(issues=[arende("SCRUM-1", vem="ingen", när=100),
                                        arende("SCRUM-2", vem="ingen", när=200)]))
rad = [n for n in kö if n["kind"] == "unowned"]
check(rad and rad[0]["count"] == 2 and rad[0]["label"] == "utan ansvarig",
      "öppna ärenden utan ansvarig blir en egen rad")

# GitHub-ärenden (öppna, mina) är också något som väntar.
kö = server.needs_list(state_av(gissues=[{"title": "Byt färg", "number": 7}]))
check([n for n in kö if n["kind"] == "gh-issues"][0]["count"] == 1,
      "öppna GitHub-ärenden räknas")

# Åtta ärenden men bara fyra nycklar: talet skall vara åtta.
kö = server.needs_list(state_av(issues=[arende("SCRUM-%d" % i, när=i) for i in range(1, 9)]))
rad = [n for n in kö if n["kind"] == "issues"][0]
check(rad["count"] == 8 and len(rad["keys"]) == 4, "en kapad lista säger ändå hela talet")

# PR:er.
kö = server.needs_list(state_av(prs=[{"title": "Byt färg", "number": 3}, {"title": "Fix", "number": 4}]))
check([n for n in kö if n["kind"] == "prs"][0]["count"] == 2, "två öppna PR:er blir en rad med talet 2")

# Nyckeln: ett okänt utgångsdatum säger ingenting; en räknad utgång larmar; ett avvisat svar
# blockerar och hamnar först.
check(not [n for n in server.needs_list(state_av(token={"present": True, "connected": True,
                                                        "daysLeft": None, "warnDays": 14}))
           if n["kind"] == "key"], "ett okänt utgångsdatum är inget larm")
larm = [n for n in server.needs_list(state_av(token={"present": True, "connected": True,
                                                     "daysLeft": 3, "warnDays": 14}))
        if n["kind"] == "key"][0]
check(larm["level"] == "wait" and larm["label"] == "nyckeln går ut", "tre dygn kvar larmar")
död = server.needs_list(state_av(token={"present": True, "connected": False, "alert": "401"}))
check(död[0]["kind"] == "key" and död[0]["level"] == "block", "en nyckel Jira avvisar blockerar")

# Bygget: rött på huvudgrenen blockerar och hamnar först. Ett grönt bygge larmar inte.
rött = {"repos": [{"repo": "mig/repo", "state": "red", "branch": "main", "workflow": "CI",
                   "url": "https://x/actions/1"}]}
kö = server.needs_list(dict(state_av(), ci=rött))
check(kö[0]["kind"] == "ci" and kö[0]["level"] == "block" and kö[0]["label"] == "bygget är rött",
      "ett rött bygge blir kön första rad")
check(not [n for n in server.needs_list(dict(state_av(), ci={"repos": [{"state": "green"}]}))
           if n["kind"] == "ci"], "ett grönt bygge larmar inte")

# ci_of: den nyaste körningen PÅ HUVUDGRENEN avgör -- en röd gren man jobbar i är inte
# "bygget är rött", och en körning som inte är klar är inget svar än.
körningar = [{"headBranch": "main", "status": "completed", "conclusion": "failure",
              "createdAt": "2026-10-05T10:00:00Z", "workflowName": "CI", "url": "u"},
             {"headBranch": "develop", "status": "completed", "conclusion": "success",
              "createdAt": "2026-10-05T12:00:00Z"}]
check(server.ci_of(körningar, "main")["state"] == "red", "den nyaste körningen på huvudgrenen avgör")
check(server.ci_of(körningar, "release")["state"] == "none", "en gren utan körningar säger inget")
check(server.ci_of([{"headBranch": "main", "status": "in_progress", "conclusion": "",
                     "createdAt": "x"}], "main")["state"] == "running",
      "en körning som inte är klar är inget svar")
check(server.ci_of([], "main")["state"] == "none", "inga byggen alls är inget larm")

# Ordningen: block före wait före info.
kö = server.needs_list(state_av(issues=[arende("SCRUM-1")],
                                token={"present": True, "connected": False},
                                prs=[{"title": "x"}],
                                runs=[{"key": "SCRUM-7", "ok": False}]))
check([n["level"] for n in kö] == sorted([n["level"] for n in kö], key=lambda l: server.NEED_ORDER[l]),
      "block före wait före info")

# Ingen nyckel alls: dörren till Kom igång, inte en tom skärm.
kö = server.needs_list(state_av(token={"present": False, "connected": False}))
check(kö and kö[0]["view"] == "setup", "utan nyckel pekar kön på setupen")

print("Kön: %d kontroller" % len(kontroller))
print("OK: alla kontroller passerar" if not fel else "FEL: %d av %d" % (len(fel), len(kontroller)))
raise SystemExit(1 if fel else 0)
