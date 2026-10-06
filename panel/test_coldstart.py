#!/usr/bin/env python3
"""Provläget: en maskin utan nycklar, utan gh och utan Jira.

Kör: python3 panel/test_coldstart.py   (exit 0 = grönt)

Det här är vägen en ny kollega går: hon klonar repot, startar panelen och tittar
innan hon kopplar något konto. Provet startar därför panelen med ett tomt hem och
säger till om den möts av en tom ruta, av sina egna konton -- eller av en snurra.

Det som mäts är de tre sakerna som gick fel när demoläget byggdes:

  1. mocken kraschade utan konfiguration (NameError), så status svarade fel,
  2. panelen visade projektet "SCRUM", som mocken inte har, alltså en tom tavla,
  3. GitHub-delen körde gh, som svarade med *Alex egna* repos och inloggningsnamn --
     alltså hade en skärmdump av provläget burit ett riktigt konto.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
checks = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        print("FAILED: {}{}".format(what, " -- " + detail if detail else ""))
        raise SystemExit(1)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def git_state() -> str:
    done = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                          capture_output=True, text=True)
    return done.stdout


home = Path(tempfile.mkdtemp(prefix="godjira-cold-"))
port = free_port()
before = git_state()

env = {k: v for k, v in os.environ.items()
       if k not in ("JIRA_TOKEN", "JIRA_SITE", "JIRA_EMAIL", "GH_TOKEN", "PANEL_DEMO")}
env.update(HOME=str(home), PANEL_PORT=str(port), PANEL_BIND="127.0.0.1", TMPDIR=str(home))
panel = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    base = "http://127.0.0.1:{}".format(port)
    ready = False
    # 120 * 0,5 s = 60 s. Räcker gott ensam, men provet skall också hålla när det körs i
    # hela sviten medan andra servrar och sessioner jobbar på samma maskin -- då är en
    # kallstart utan nyckel tyngre. Loopen bryter så fort /healthz svarar, så en höjd
    # gräns kostar ingenting när starten går fort. (Mätt: ett rött körningstillfälle av
    # många, 3 av 3 gröna ensam -- flakigt under last, inte trasigt.)
    for _ in range(240):
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=2):
                ready = True
                break
        except Exception:
            if panel.poll() is not None:
                break
            time.sleep(0.5)
    check(ready, "panelen startar med ett tomt hem", "den svarade aldrig på /healthz")

    with urllib.request.urlopen(base + "/api/state", timeout=120) as answer:
        state = json.loads(answer.read().decode("utf-8"))

    jira, github = state.get("jira") or {}, state.get("github") or {}
    token, flow = state.get("token") or {}, state.get("flow") or {}
    project = state.get("project") or {}

    check(jira.get("mode") == "mock", "utan nyckel kör bryggan sin egen testdata",
          str(jira.get("mode")))
    check(token.get("demo") is True and token.get("present") is False,
          "provläget sägs vara provläge, inte ett saknat konto", json.dumps(token)[:120])

    keys = [p.get("key") for p in (jira.get("projects") or [])]
    check(project.get("key") in keys,
          "projektet panelen visar finns i anslutningen", "{} mot {}".format(project.get("key"), keys))
    check((jira.get("boards") or []) and sum(len(b.get("issues") or []) for b in jira["boards"]) > 0,
          "mocken har ärenden att visa", str(len(jira.get("boards") or [])) + " tavlor")

    account = (jira.get("account") or {}).get("displayName") or ""
    check(bool(account) and account == "Demo User",
          "mockens konto är en påhittad person, inte en riktig", repr(account))

    check(github.get("demo") is True and github.get("login") == "demo-user",
          "GitHub-delen är exempeldata: gh får inte svara med användarens eget konto",
          repr(github.get("login")))
    check(bool(github.get("repos")), "exemplen har repos att visa")
    check(flow.get("demo") is True and bool(flow.get("pick")),
          "flödets nästa visar ett exempel i stället för ett tokenfel",
          json.dumps(flow)[:160])

    check(git_state() == before, "panelen skriver ingenting i sin egen checkout")
    check(not any(home.rglob("*.pyc")), "ingen bytekod hamnade i hemkatalogen")
finally:
    panel.terminate()
    try:
        panel.wait(timeout=10)
    except subprocess.TimeoutExpired:
        panel.kill()

# Fixen som provet vaktar: första besöket utan nyckel gick till Kom igång och lämnade
# splash-fönstret över skärmen -- användaren möttes av en snurra, inte av kopplingen.
frontend = (HERE / "index.html").read_text(encoding="utf-8")
setup_branch = frontend.split("if (!SETUP.met", 1)[1].split("}", 1)[1]
check("splashHide()" in setup_branch,
      "vägen till Kom igång stänger splash-fönstret", setup_branch[:120])
# Provlägesflaggan står raden före: den läses ur svaret och styr om setupen visas.
check('mode === "mock"' in frontend.split("if (!SETUP.met", 1)[0][-200:],
      "provläget visar data i stället för kopplingsskärmen")

print("kallstart: {} kontroller, allt grönt".format(checks))
