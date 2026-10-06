#!/usr/bin/env python3
"""Provet for infosidan: att den svarar, att den sager sanningen, och att den inte laddar
nagot utifran.

Sidorna ar statiska HTML-filer med ETT enda skript: besoksräkningen (PostHog, EU). Det star
i integritet.html ("En cookie, för räkningen", "Anonym besoksräkning", "Ett enda skript av
oss", "Bara ett anrop ut"), och ett pastaende utan matning ar vard ingenting. Det har provet
kollar just det: att servern inte satter nagon cookie (snutten satter sin egen i
webblasaren), att varje sida har exakt ett skript, att det ar samma snutt i alla tre, att den
pekar pa EU-hosten, och att ingen bild eller stilmall hamtas fran en annan vard.

Rakningen av skript gors pa VAR egen server: Cloudflare lagger sjalv till sin
e-postskyddare (email-decode.min.js) pa den sida som visar en adress, sa integritet.html har
tva skript ute och ett har (matt 2026-10-06). Det provet kan inte se -- det kor aldrig genom
Cloudflare. Ska loftet "ett enda skript" galla ordagrant maste den installningen stangas av.

Sidan finns ocksa pa engelska (/en/). Provet mater inte att oversattningen ar BRA -- det gor
ogat, pa skarmdumpen -- utan att den ar HEL: ingen svensk rad far sta kvar, stilmallen och
bilderna maste ha blivit absoluta (sidan ligger ett steg djupare), och sprakvalet skall ga
fram och tillbaka. `sprak.py` sager dessutom vilka rader som saknas i varje sprakfil.

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
import sprak  # noqa: E402

ROT = Path(__file__).resolve().parent
SIDOR = {"index.html": "GodJIRA", "integritet.html": "Personuppgiftsansvarig",
         "villkor.html": "AGPL-3.0"}
UTANFOR = ("http://", "https://", "//cdn", "//fonts")

kontroller: list[tuple[bool, str, str]] = []


def kolla(ok: bool, vad: str, detalj: str = "") -> None:
    kontroller.append((bool(ok), vad, detalj))
    print(("  ok   " if ok else "  FEL  ") + vad + ((" -- " + detalj) if detalj else ""))


def hamta(port: int, vag: str, rubriker: dict | None = None):
    koppling = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        koppling.request("GET", vag, headers=rubriker or {})
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
        # Snutten: ETT skript per sida, och samma snutt i alla tre -- annars driver de isar
        # och en sida mater nagot annat an de andra. Den skall vara PostHog mot EU-hosten med
        # minneslagring: da sparas ingenting i webblasaren, som integritetssidan lovar.
        snuttar: dict[str, list[str]] = {}
        for sida in SIDOR:
            _, _, kropp = hamta(port, "/" + sida)
            funna = re.findall(r"<script\b.*?</script>", kropp, re.S | re.I)
            snuttar[sida] = funna
            kolla(len(funna) == 1, "%s har exakt ett skript" % sida, "%d" % len(funna))
        kolla(len({tuple(v) for v in snuttar.values()}) == 1, "samma snutt pa alla tre sidorna")
        snutt = (snuttar["index.html"] or [""])[0]
        kolla("phc_" in snutt, "snutten bar projekt-token (phc_)")
        kolla("eu.i.posthog.com" in snutt, "snutten pekar pa EU-hosten")
        kolla(re.search(r"persistence:\s*'memory'", snutt) is None,
              "snutten anvander standardlaget (cookien som sidan beskriver)")
        kolla(not re.search(r"<script[^>]+src=", startsida, re.I), "inget skript laddas fran en fil")
        # Bilderna och stilmallen skall komma fran samma server. En enda extern sokvag racker
        # for att pastaendet om integritet skall vara falskt.
        # Det som LADDAS skall komma fran samma server: src= (bilder, skript) och <link
        # href=> (stilmallen). En lank (a href) far ga ut -- GitHub-adressen ar ett erbjudande
        # till lasaren, inte ett anrop som sidan sjalv gor.
        laddas = (re.findall(r'src\s*=\s*"(.*?)"', startsida)
                  + re.findall(r'<link[^>]+href\s*=\s*"(.*?)"', startsida))
        kolla(laddas and not any(f.startswith(UTANFOR) for f in laddas),
              "bilder och stilmall kommer fran samma vard", "%d resurser, alla lokala" % len(laddas))
        falt = re.findall(r'(?:src|href)\s*=\s*"(.*?)"', startsida)
        lokala = [f for f in falt if not f.startswith("#")]

        # Sidorna skall hanga ihop: varje intern lank skall finnas. Lanken till engelskan är
        # absolut (/en/...), de svenska sidorna emellan relativa.
        for mal in sorted({f for f in lokala if f.endswith(".html")}):
            status, _, _ = hamta(port, mal if mal.startswith("/") else "/" + mal)
            kolla(status == 200, "intern lank fungerar: %s" % mal)
        for bild in sorted({f for f in lokala if f.startswith("bilder/")}):
            status, _, _ = hamta(port, "/" + bild)
            kolla(status == 200, "bilden finns: %s" % bild)

        status, _, _ = hamta(port, "/finns-inte.html")
        kolla(status == 404, "okand sida ger 404", str(status))
        status, _, _ = hamta(port, "/bilagor/")
        kolla(status == 404, "kataloglistning finns inte", str(status))

        # --- Engelskan ---------------------------------------------------------------
        # Samma tre sidor, samma markup, bara texten bytt (web/sprak.py) och adressen /en
        # framfor. Provet mater inte att oversattningen ar BRA -- det gor ogat, pa
        # skarmdumpen -- utan att den ar HEL: ingen svensk rad far sta kvar.
        engelska = {}
        for sida in ("index.html", "integritet.html", "villkor.html"):
            status, huvuden, kropp = hamta(port, "/en/" + sida)
            engelska[sida] = kropp
            kolla(status == 200, "/en/%s svarar" % sida, str(status))
            kolla(huvuden.get("Content-Language") == "en",
                  "/en/%s sager att den ar engelsk" % sida, str(huvuden.get("Content-Language")))
            kolla('<html lang="en">' in kropp, "/en/%s ar markt engelsk" % sida)
            kolla(re.findall(r"<script\b.*?</script>", kropp, re.S | re.I) == snuttar[sida],
                  "/en/%s har samma snutt som svenskan" % sida)
            kolla("set-cookie" not in {k.lower() for k in huvuden},
                  "/en/%s satter ingen cookie" % sida)
            # Den engelska sidan ligger ett steg djupare an den svenska: stilmallen och
            # bilderna maste ha blivit absoluta, annars letar webblasaren i /en/ efter dem.
            laddas = (re.findall(r'src\s*=\s*"([^"]*)"', kropp)
                      + re.findall(r'<link[^>]+href\s*=\s*"([^"]*)"', kropp))
            kolla(bool(laddas) and all(f.startswith("/") for f in laddas),
                  "/en/%s laddar allt fran rotadressen" % sida,
                  ", ".join(sorted(set(laddas)))[:120])
            for mal in sorted(set(re.findall(r'href="(/[^"]*\.html)"', kropp))):
                status, _, _ = hamta(port, mal)
                kolla(status == 200, "/en/%s: lanken %s fungerar" % (sida, mal))

        kvar = set()
        for kropp in engelska.values():
            kvar |= set(sprak.skorda(kropp)) & (set(sprak.kalla()) - sprak.OFORANDRA)
        kolla(not kvar, "ingen svensk rad star kvar i engelskan",
              "kvar: %s" % sorted(kvar)[:3])

        # Sidorna skall hanga ihop over sprakbytet: fram och tillbaka.
        _, _, svensk = hamta(port, "/villkor.html")
        kolla('href="/en/villkor.html"' in svensk, "villkor.html pekar pa engelskan")
        kolla('href="/villkor.html"' in engelska["villkor.html"],
              "engelskan pekar tillbaka pa svenskan")

        # Sprakvalet: engelskan finns pa /en, och en engelsk webblasare hittar dit.
        status, huvuden, _ = hamta(port, "/en")
        kolla(status == 301 and huvuden.get("Location") == "/en/",
              "/en leder till /en/", "%s %s" % (status, huvuden.get("Location")))
        status, huvuden, _ = hamta(port, "/", {"Accept-Language": "en-GB,en;q=0.9"})
        kolla(status == 302 and huvuden.get("Location") == "/en/",
              "engelsk webblasare skickas till /en/", "%s %s" % (status, huvuden.get("Location")))
        status, _, kropp = hamta(port, "/", {"Accept-Language": "sv-SE,sv;q=0.9,en;q=0.5"})
        kolla(status == 200 and "site-nav" in kropp, "svensk webblasare far svenskan", str(status))
        status, _, _ = hamta(port, "/en/index.html", {"Accept-Language": "sv"})
        kolla(status == 200, "/en star kvar aven for en svensk webblasare", str(status))

        # --- Sprakfilerna -------------------------------------------------------------
        # Varje språkfil jämförs med de tre sidorna: 0 saknade, 0 extra, och ingen rad som är
        # lika på båda språken (undantagslistan i sprak.py bär de rader som SKALL vara lika).
        for fil in sorted(p.stem for p in sprak.I18N.glob("*.json")):
            if fil == sprak.KALLA:
                continue
            saknas, extra, lika = sprak.avvikelser(fil)
            kolla(not (saknas or extra or lika), "%s.json stammer med sidorna" % fil,
                  "%d saknas, %d extra, %d ooversatta" % (len(saknas), len(extra), len(lika)))

        alla = set()
        for sida in sprak.SIDOR:
            alla |= set(sprak.rader((sprak.ROT / sida).read_text(encoding="utf-8")))
        kolla(not (sprak.OFORANDRA - alla), "inga doda rader i undantagslistan",
              str(sorted(sprak.OFORANDRA - alla)[:3]))
        kolla(len(sprak.OFORANDRA) <= 30, "undantagslistan ar inte en oversattning i smyg",
              "%d rader" % len(sprak.OFORANDRA))
    finally:
        httpd.shutdown()
        httpd.server_close()

    fel = [k for k, _, _ in kontroller if not k]
    print("\nwebb: %d kontroller" % len(kontroller))
    print("OK: alla kontroller passerar" if not fel else "FEL: %d av %d" % (len(fel), len(kontroller)))
    return 1 if fel else 0


if __name__ == "__main__":
    raise SystemExit(main())
