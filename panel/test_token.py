#!/usr/bin/env python3
"""Nyckeln, och de fält som tystnar: utgångsmatematik, läckage, skräp -- och kartan
som n8n skickar hem men panelen bara behöll om den stod på vitlistan.

Run it with the panel up:  python3 panel/test_token.py

Det som faktiskt kan gå fel utan att någon märker det:
  * dagarna räknas fel        -> larmet kommer för sent, eller aldrig
  * nyckeln läcker i svaret   -> den ligger i webbläsarens historik och loggar
  * skräp sparas som nyckel   -> anslutningen som fungerade slås ut
  * kartan tystas i rapporten -> n8n:s läsning syns aldrig i kortet
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8788"

spec = importlib.util.spec_from_file_location("server", HERE / "server.py")
assert spec and spec.loader, "server.py kunde inte läsas in"
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

checks = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        print("FAILED: {}{}".format(what, " -- " + detail if detail else ""))
        raise SystemExit(1)


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=120) as answer:
        return json.loads(answer.read().decode("utf-8"))


def post(path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as answer:
            return answer.status, json.loads(answer.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# Utgångsmatematiken. Dagen nyckeln går ut är den sista giltiga dagen: 0 dagar kvar.
check(server.days_left("2026-09-30", "2026-09-30") == 0, "utgång idag ger 0 dagar")
check(server.days_left("2026-10-14", "2026-09-30") == 14, "två veckor ger 14 dagar")
check(server.days_left("2026-09-01", "2026-09-30") == -29, "förfallet datum ger ett negativt tal")
check(server.days_left("snart", "2026-09-30") is None, "skräp ger inget datum")
check(server.days_left("", "2026-09-30") is None, "inget datum ger None")

status = get("/api/token")
check(status.get("ok") is True, "status svarar")
check("tokenPage" in status and status["tokenPage"].startswith("https://id.atlassian.com/"),
      "länken till Atlassian följer med", str(status.get("tokenPage")))
# Läckan: svaret får beskriva nyckeln, aldrig bära den. En Atlassian-nyckel är lång
# och alfanumerisk -- finns en sådan sträng i svaret är den på fel plats.
leak = [k for k, v in status.items() if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{20,}", v)]
check(leak == [], "svaret bär inte nyckeln", "fält som ser ut som en nyckel: " + ", ".join(leak))

code, answer = post("/api/token", {})
check(code == 400 and answer.get("ok") is False, "en tom förfrågan nekas", str(answer))
code, answer = post("/api/token", {"token": "inte-en-nyckel"})
check(code == 400 and answer.get("ok") is False, "skräp nekas innan bryggan rörs", str(answer))

# Kartan i n8n:s rapport: den kom utifrån, alltså skall den genom samma kontroll som
# allt annat utifrån -- men den skall inte tystas. Den föll utanför vitlistan förut.
# Rapporten skrivs till en fil i den skarpa installationen, så provet byter fil först:
# det får inte skriva över n8n:s senaste, riktiga läsning.
server.AUTOMATION_FILE = Path(tempfile.mkdtemp(prefix="godjira-test-")) / "automation.json"


def reported(payload: dict) -> dict:
    code, answer = server.automation_report(payload)
    check(code == 200, "rapporten togs emot", str(answer))
    return answer["automation"]


kept = reported({"project": "SCRUM", "theMap": {"issues": "131", "files": 121, "mapped": 125,
                "missing": "6", "silentFiles": 74, "at": "2026-09-30T08:18:00"}})["theMap"]
check(kept is not None, "kartan behålls i rapporten")
check(kept["missing"] == 6 and isinstance(kept["missing"], int), "siffrorna blir tal", str(kept))
check(reported({"project": "SCRUM", "theMap": {"issues": "0"}})["theMap"] is None,
      "en tom karta blir ingen karta")
check(reported({"project": "SCRUM", "theMap": "hej"})["theMap"] is None,
      "en karta som inte är ett objekt kastas")
check(reported({"project": "SCRUM", "theMap": {"issues": -5}})["theMap"] is None,
      "negativa tal blir ingen karta")

after = get("/api/token")
check(after.get("present") == status.get("present"), "nyckeln är orörd efter de nekade försöken")

print("nyckeln: {} kontroller, 0 fel".format(checks))
