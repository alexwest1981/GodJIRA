#!/usr/bin/env python3
"""Bevakningen: vad som är fel just nu, medan ingen tittar.

Körs av godjira-watch.timer var femtonde minut. Den skriver de problem den hittade -- och
bara problem: en tom lista är ett svar, en påhittad nolla är det inte. Hittar den inget
skriver den en fil utan rader, och då står det ingenting i panelen.

Två källor, samma som panelen läser:
  * driftrapporten (bin/jira_sites.py --drift): sajter som inte kör, och vakter (backningar
    m.m.) som inte gjort sitt inom sin egen max_hours.
  * panelens egen körning (/api/work): en agent som stått still i över sex timmar. En
    körning som fastnat ser annars ut som en körning som jobbar.

    python3 bin/jira_watch.py             # kör kontrollerna, skriv filen
    python3 bin/jira_watch.py --json      # samma, men svaret på stdout
    python3 bin/jira_watch.py --selftest  # proven, utan att röra något
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

ROT = pathlib.Path(__file__).resolve().parent.parent
FIL = pathlib.Path.home() / ".local/state/omarchy/godjira-watch.json"
PANEL = "http://127.0.0.1:8788"
STILLE_H = 6          # en körning utan livstecken så här länge har fastnat


def kor(cmd: list[str], timeout: int = 120) -> dict | None:
    """En kontroll. Svarar den inte är det ett problem i sig -- men det skrivs som ett fel,
    inte som en nolla som ser ut som ett svar."""
    try:
        fardig = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return json.loads(fardig.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def kort_om(drift: dict) -> str:
    """Varför driften inte är ok, i så få ord som möjligt."""
    bitar = []
    if drift.get("behind"):
        bitar.append("%d bakom" % int(drift["behind"]))
    if drift.get("ahead"):
        bitar.append("%d före" % int(drift["ahead"]))
    if drift.get("dirty"):
        bitar.append("lokala ändringar")
    return ", ".join(bitar) or ("inte i takt med " + str(drift.get("upstream") or "upstream"))


def problem_ur_drift(d: dict) -> list[dict]:
    """Sajter och vakter ur driftrapporten. Ren funktion: samma svar varje gång."""
    ut: list[dict] = []
    for s in d.get("sajter") or []:
        namn = str(s.get("namn") or s.get("nyckel") or "?")
        lage = str(s.get("lage") or "")
        if lage and lage != "active":
            ut.append({"kind": "sajt", "text": "%s kör inte (%s)" % (namn, lage),
                       "view": "sites"})
        elif (s.get("drift") or {}).get("ok") is False:
            ut.append({"kind": "sajt", "text": "%s: %s" % (namn, kort_om(s.get("drift") or {})),
                       "view": "sites"})
    for v in d.get("vakter") or []:
        namn, alder, tak = str(v.get("namn") or v.get("enhet") or "?"), v.get("age_h"), v.get("max_hours")
        if alder is None:
            ut.append({"kind": "vakt", "text": "%s har inte gjort sitt än" % namn, "view": "sites"})
        elif tak and float(alder) > float(tak):
            ut.append({"kind": "vakt", "text": "%s: %s h sedan (tak %s h)"
                       % (namn, alder, tak), "view": "sites"})
    return ut


def problem_ur_korning(svar: dict | None) -> list[dict]:
    """Panelen svarar med körningens läge. Står den still är det ett problem."""
    if not svar or svar.get("state") != "running":
        return []
    sekunder = float(svar.get("seconds") or 0)
    if sekunder <= STILLE_H * 3600:
        return []
    return [{"kind": "korning", "text": "%s har kört i %d h utan att bli klar"
             % (svar.get("key") or "en körning", round(sekunder / 3600)), "view": "overview"}]


def samla() -> dict:
    drift = kor(["python3", str(ROT / "bin" / "jira_sites.py"), "--drift"], timeout=180)
    problem = problem_ur_drift(drift or {}) if drift else \
        [{"kind": "sajt", "text": "driftrapporten svarade inte", "view": "sites"}]
    try:
        with urllib.request.urlopen(PANEL + "/api/work", timeout=10) as svar:
            korning = json.loads(svar.read())
    except Exception:            # noqa: BLE001 -- panelen kan vara nere; det är inte ett problem i sig
        korning = None
    problem += problem_ur_korning(korning)
    return {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "problem": problem,
            "kollat": {"sajter": len((drift or {}).get("sajter") or []),
                       "vakter": len((drift or {}).get("vakter") or []),
                       "korning": (korning or {}).get("state") or "okänd"}}


def skriv(rapport: dict) -> None:
    FIL.parent.mkdir(parents=True, exist_ok=True)
    FIL.parent.chmod(0o700)
    FIL.write_text(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    FIL.chmod(0o600)


def selftest() -> int:
    """Proven: vad som blir ett problem, och vad som inte blir det."""
    bra = {"sajter": [{"nyckel": "x", "namn": "X", "lage": "active",
                       "drift": {"ok": True, "behind": 0, "ahead": 0}}],
           "vakter": [{"namn": "Backningen", "age_h": 2, "max_hours": 30}]}
    assert problem_ur_drift(bra) == [], problem_ur_drift(bra)

    nere = {"sajter": [{"nyckel": "x", "namn": "X", "lage": "failed", "drift": {"ok": True}}], "vakter": []}
    assert problem_ur_drift(nere)[0]["text"] == "X kör inte (failed)", problem_ur_drift(nere)

    efter = {"sajter": [{"nyckel": "x", "namn": "X", "lage": "active",
                         "drift": {"ok": False, "behind": 3, "dirty": True}}], "vakter": []}
    assert problem_ur_drift(efter)[0]["text"] == "X: 3 bakom, lokala ändringar", problem_ur_drift(efter)

    forsenad = {"sajter": [], "vakter": [{"namn": "Backningen", "age_h": 40, "max_hours": 30},
                                         {"namn": "Nya", "age_h": None, "max_hours": 30}]}
    rader = problem_ur_drift(forsenad)
    assert len(rader) == 2 and "40" in rader[0]["text"] and "inte gjort sitt" in rader[1]["text"], rader

    # Tomt underlag ger inga problem -- och inga påhittade sådana. Skillnaden mellan
    # "allt är bra" och "ingen svarade" hanteras i samla(), inte här.
    assert problem_ur_drift({}) == [] and problem_ur_drift({"sajter": [], "vakter": []}) == []

    # Korningen: still star still, men bara over taket.
    assert problem_ur_korning({"state": "idle"}) == []
    assert problem_ur_korning({"state": "running", "key": "SCRUM-1", "seconds": 60}) == []
    still = problem_ur_korning({"state": "running", "key": "SCRUM-1", "seconds": STILLE_H * 3600 + 60})
    assert len(still) == 1 and "SCRUM-1" in still[0]["text"], still

    # Och filen skrivs med 0600 i en 0700-mapp, som de andra tillstandsfilerna.
    print("selftest: OK")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    rapport = samla()
    skriv(rapport)
    if "--json" in argv:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        antal = len(rapport["problem"])
        print("bevakningen: %d problem · %d sajter, %d vakter, körning %s (%s)"
              % (antal, rapport["kollat"]["sajter"], rapport["kollat"]["vakter"],
                 rapport["kollat"]["korning"], rapport["at"]))
        for p in rapport["problem"]:
            print("  - " + p["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
