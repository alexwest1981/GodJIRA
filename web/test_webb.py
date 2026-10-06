#!/usr/bin/env python3
"""Provet for infosidan: att den svarar, att den sager sanningen, och att den inte laddar
nagot utifran.

Statisk HTML utan JavaScript -- men "inga cookies, ingen spanning, inga anrop till nagon
annan" ar ett pastaende pa sida 2 (integritet.html), och ett pastaende utan matning ar vard
ingenting. Det har provet kollar just det: att inga cookies satts, att det inte finns ett
enda <script>, och att ingen bild, stilmall eller typsnitt hamtas fran en annan vard.

Korning:  python3 web/test_webb.py      (startar sin egen server pa en ledig port)
"""
from __future__ import annotations

import http.client
import re
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serva  # noqa: E402

ROT = Path(__file__).resolve().parent
SIDOR = {"index.html": "GodJIRA", "integritet.html": "Personuppgiftsansvarig",
         "villkor.html": "AGPL-3.0"}
UTANFOR = ("http://", "https://", "//cdn", "//fonts")

kontroller: list[tuple[bool, str, str]] = []


def kolla(ok: bool, vad: str, detalj: str = "") -> None:
    kontroller.append((bool(ok), vad, detalj))
    print(("  ok   " if ok else "  FEL  ") + vad + ((" -- " + detalj) if detalj else ""))


def hamta(port: int, vag: str):
    koppling = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        koppling.request("GET", vag)
        svar = koppling.getresponse()
        kropp = svar.read().decode("utf-8", "replace")
        return svar.status, dict(svar.getheaders()), kropp
    finally:
        koppling.close()


def main() -> int:
    httpd = serva.Server(("127.0.0.1", 0), serva.Tyst)       # 0 = en ledig port
    port = httpd.server_port
    trad = threading.Thread(target=httpd.serve_forever, daemon=True)
    trad.start()
    try:
        for sida, marke in SIDOR.items():
            status, huvuden, kropp = hamta(port, "/" + sida)
            kolla(status == 200 and marke in kropp,
                  "%s svarar och innehaller sitt marke" % sida, "%d, %d tecken" % (status, len(kropp)))
            kolla("set-cookie" not in {k.lower() for k in huvuden}, "%s satter ingen cookie" % sida)
            # Utan no-store cachar Cloudflare .css och .png sjalv, och da kan en NY sida serveras
            # med en GAMMAL stilmall -- den ser trasig ut utan att nagot ar trasigt. Matt en gang:
            # cf-cache-status HIT, age 745 s, stilmall fran 09:45 mot farsk HTML.
            kolla(huvuden.get("Cache-Control") == "no-store",
                  "%s tillater ingen cachning" % sida, str(huvuden.get("Cache-Control")))

        _, _, startsida = hamta(port, "/")
        kolla("<script" not in startsida.lower(), "ingen JavaScript pa sidan")
        # Bilderna och stilmallen skall komma fran samma server. En enda extern sokvag racker
        # for att pastaendet om integritet skall vara falskt.
        # Det som LADDAS skall komma fran samma server: src= (bilder, skript) och <link
        # href=> (stilmallen). En lank (a href) far ga ut -- GitHub-adressen ar ett erbjudande
        # till lasaren, inte ett anrop som sidan sjalv gor.
        laddas = (re.findall(r'src\s*=\s*"(.*?)"', startsida)
                  + re.findall(r'<link[^>]+href\s*=\s*"(.*?)"', startsida))
        kolla(laddas and not any(f.startswith(UTANFOR) for f in laddas),
              "inget laddas fran en annan vard", "%d resurser, alla lokala" % len(laddas))
        falt = re.findall(r'(?:src|href)\s*=\s*"(.*?)"', startsida)
        lokala = [f for f in falt if not f.startswith("#")]

        # Sidorna skall hanga ihop: varje intern lank skall finnas.
        for mal in sorted({f for f in lokala if f.endswith(".html")}):
            status, _, _ = hamta(port, "/" + mal)
            kolla(status == 200, "intern lank fungerar: %s" % mal)
        for bild in sorted({f for f in lokala if f.startswith("bilder/")}):
            status, _, _ = hamta(port, "/" + bild)
            kolla(status == 200, "bilden finns: %s" % bild)

        status, _, _ = hamta(port, "/finns-inte.html")
        kolla(status == 404, "okand sida ger 404", str(status))
        status, _, _ = hamta(port, "/bilagor/")
        kolla(status == 404, "kataloglistning finns inte", str(status))
    finally:
        httpd.shutdown()
        httpd.server_close()

    fel = [k for k, _, _ in kontroller if not k]
    print("\nwebb: %d kontroller" % len(kontroller))
    print("OK: alla kontroller passerar" if not fel else "FEL: %d av %d" % (len(fel), len(kontroller)))
    return 1 if fel else 0


if __name__ == "__main__":
    raise SystemExit(main())
