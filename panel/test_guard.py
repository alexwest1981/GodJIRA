#!/usr/bin/env python3
"""Väktaren: vem får prata med panelen, och vad svarar den en främmande sida?

Kör: python3 panel/test_guard.py   (exit 0 = grönt)

Provet startar servern på en ledig port på 127.0.0.1 och skickar riktiga förfrågningar
med förfalskad Host och Origin -- precis det en sida du råkar besöka gör. Ingen rutt
som skriver något körs: de nekade anropen stoppas före hanteraren, och det tillåtna
lokala anropet går till en okänd rutt (404), aldrig till /api/token.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
checks = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        print("FAILED: {}{}".format(what, " -- " + detail if detail else ""))
        raise SystemExit(1)


# Standardbindningen skall vara den egna maskinen: en som klonar repot och kör
# `python3 panel/server.py` rakt av skall inte råka öppna panelen i nätet.
out = subprocess.run([sys.executable, "-c",
                      "import importlib.util; s=importlib.util.spec_from_file_location('s',"
                      " 'panel/server.py'); m=importlib.util.module_from_spec(s); "
                      "s.loader.exec_module(m); print(m.BIND)"],
                     env={k: v for k, v in os.environ.items() if k != "PANEL_BIND"},
                     capture_output=True, text=True, cwd=str(HERE.parent), timeout=120)
check(out.stdout.strip() == "127.0.0.1", "utan PANEL_BIND lyssnar panelen bara lokalt",
      out.stdout.strip() + out.stderr.strip())

spec = importlib.util.spec_from_file_location("server", HERE / "server.py")
assert spec and spec.loader, "server.py kunde inte läsas in"
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

for value, want in [("127.0.0.1:8788", True), ("localhost", True), ("[::1]:8788", True),
                    ("192.168.1.5:8788", True),                  # en panel medvetet i nätet
                    ("evil.example:8788", False),                # främmande värd
                    ("127.0.0.1.evil.example", False),           # rebinding-mot värdnamn
                    ("http://evil.example", False), ("null", False), ("", False)]:
    check(server.host_ok(value) is want, "värdkontroll: {!r}".format(value), repr(server.host_ok(value)))

httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def ask(path: str, host: str, origin: str | None = None, method: str = "GET") -> tuple[int, str]:
    data = json.dumps({}).encode("utf-8") if method == "POST" else None
    request = urllib.request.Request("http://127.0.0.1:{}{}".format(port, path), data=data, method=method)
    request.add_header("Host", host)
    if origin:
        request.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            return answer.status, answer.read(60).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(60).decode("utf-8", "replace")


good = "127.0.0.1:{}".format(port)
check(ask("/api/state", good)[0] == 200, "den egna maskinen kommer in")
code, _ = ask("/api/state", "evil.example")
check(code == 403, "ett främmande värdnamn nekas (rebinding)", str(code))
code, _ = ask("/api/state", "127.0.0.1.evil.example")
check(code == 403, "ett värdnamn som pekar på 127.0.0.1 nekas", str(code))
code, _ = ask("/api/nonsense", good, origin="https://evil.example", method="POST")
check(code == 403, "en POST från en främmande sida nekas (CSRF)", str(code))
code, _ = ask("/api/nonsense", good, method="POST")
check(code == 404, "en POST utan ursprung (curl) når fram och får 404", str(code))

httpd.shutdown()
print("väktaren: {} kontroller, allt grönt".format(checks))
