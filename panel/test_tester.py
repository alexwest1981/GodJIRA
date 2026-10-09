#!/usr/bin/env python3
"""Provkörningen: Surefires rapport, läst rad för rad.

Katalogen pekas om med GODJIRA_TESTS, så provet skriver sina egna rapportfiler i en
temporär katalog och läser dem med samma funktion panelen använder. Ingen Jira, ingen
webbläsare, ingen Maven.

Run it:  python3 panel/test_tester.py

Det som faktiskt kan gå fel utan att någon märker det:
  * en klass med felstopp räknas som grön          -> sviten ser grönare ut än den är
  * röda klasser hamnar längst ner                 -> den röda raden syns inte först
  * en halvskriven fil från en avbruten körning    -> kraschar i stället för att hoppas över
  * en klass räknas två gånger                     -> siffran i rubriken ljuger
  * ingen körning gjord                            -> en grön bock utan data att backa upp den
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
checks = 0
fails: list = []


def check(ok: bool, what: str, detail: object = "") -> None:
    global checks
    checks += 1
    if not ok:
        fails.append((what, detail))


def klass(namn, prov, fel=0, stopp=0, hoppade=0, tid=0.5):
    """En rapportfil som Surefire skriver den, med bara de fält panelen läser."""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<testsuite name="{}" tests="{}" failures="{}" errors="{}" skipped="{}" time="{}"/>'
            .format(namn, prov, fel, stopp, hoppade, tid))


provmapp = Path(tempfile.mkdtemp(prefix="godjira-prov-"))
os.environ["GODJIRA_TESTS"] = str(provmapp)
spec = importlib.util.spec_from_file_location("server", HERE / "server.py")
assert spec and spec.loader, "server.py kunde inte läsas in"
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

# --- ingen körning gjord ------------------------------------------------------
tomt = server.tests_read()
check(tomt["classes"] == [], "en tom katalog ger inga klasser", tomt["classes"])
check(tomt["error"] == "ingen körning hittad", "och den säger det rakt ut", tomt["error"])
check(tomt["total"]["tests"] == 0 and tomt["when"] == "",
      "inga prov och ingen tidpunkt att visa", tomt["total"])

# --- två klasser, en grön och en med fel och felstopp -------------------------
(provmapp / "TEST-a.GronTest.xml").write_text(klass("com.wac.GronTest", 3, tid=0.25))
(provmapp / "TEST-b.RodTest.xml").write_text(klass("com.wac.RodTest", 4, fel=2, stopp=1, tid=1.5))
(provmapp / "TESTING.md").write_text("anteckningar, inte en rapport")
svar = server.tests_read()
check([k["name"] for k in svar["classes"]] == ["RodTest", "GronTest"],
      "röda klasser först, annars i bokstavsordning", [k["name"] for k in svar["classes"]])
check(svar["classes"][0]["ok"] is False and svar["classes"][1]["ok"] is True,
      "bara den felfria klassen är grön", [k["ok"] for k in svar["classes"]])
check(svar["total"] == {"tests": 7, "failures": 2, "errors": 1, "skipped": 0, "time": 1.75},
      "summan räknar varje klass en gång", svar["total"])
check(len(svar["classes"]) == 2, "en fil som inte är en rapport räknas inte", len(svar["classes"]))
check(svar["error"] == "", "en riktig körning ger inget felmeddelande", svar["error"])
check(len(svar["when"]) == 16 and svar["when"][4] == "-",
      "tidpunkten är körningens, i läsbart format", svar["when"])
check(svar["dir"] == str(provmapp), "katalogen som lästes står i svaret", svar["dir"])

# --- felstopp utan fel: fortfarande röd ---------------------------------------
for fil in provmapp.glob("TEST-*.xml"):
    fil.unlink()
(provmapp / "TEST-StoppTest.xml").write_text(klass("com.wac.StoppTest", 2, stopp=1))
stopp = server.tests_read()
check(stopp["classes"][0]["ok"] is False,
      "en klass som kraschar är röd även utan misslyckade prov", stopp["classes"][0])
check(stopp["total"]["errors"] == 1 and stopp["total"]["failures"] == 0,
      "felstoppet räknas som felstopp", stopp["total"])

# --- hoppade prov är inte fel -------------------------------------------------
for fil in provmapp.glob("TEST-*.xml"):
    fil.unlink()
(provmapp / "TEST-HoppTest.xml").write_text(klass("com.wac.HoppTest", 3, hoppade=2))
hopp = server.tests_read()
check(hopp["classes"][0]["ok"] is True,
      "hoppade prov gör inte klassen röd", hopp["classes"][0])
check(hopp["total"]["skipped"] == 2, "hoppade prov redovisas för sig", hopp["total"])

# --- en halvskriven fil -------------------------------------------------------
for fil in provmapp.glob("TEST-*.xml"):
    fil.unlink()
(provmapp / "TEST-Hal.Test.xml").write_text('<testsuite name="com.wac.Hal" tests="1"')
(provmapp / "TEST-HelTest.xml").write_text(klass("com.wac.HelTest", 1))
halv = server.tests_read()
check(len(halv["classes"]) == 1 and halv["classes"][0]["name"] == "HelTest",
      "en avbruten skrivning hoppas över i stället för att fälla läsningen",
      [k["name"] for k in halv["classes"]])

# --- namnet kortas, men hela namnet finns kvar --------------------------------
for fil in provmapp.glob("TEST-*.xml"):
    fil.unlink()
(provmapp / "TEST-paket.xml").write_text(klass("com.wac.autocore.model.BookingTest", 1))
namn = server.tests_read()["classes"][0]
check(namn["name"] == "BookingTest" and namn["full"] == "com.wac.autocore.model.BookingTest",
      "raden visar klassnamnet, svaret bär hela paketnamnet", namn)

if fails:
    print("provkörningen: {} kontroller, {} fel".format(checks, len(fails)))
    for what, detail in fails:
        print("  FEL  {}  {}".format(what, detail))
    raise SystemExit(1)
print("provkörningen: {} kontroller, 0 fel".format(checks))
