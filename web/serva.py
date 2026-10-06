#!/usr/bin/env python3
"""GodJIRA:s infosida, serverad.

En liten statisk server med bara standardbiblioteket -- samma krav som panelen sjalv. Den
ligger med flit pa en EGEN port (8790) och inte pa panelens 8788: den har sidan skall kunna
nas utifran, och panelen skall aldrig kunna det. Tunneln pekar pa den har porten och inget
annat.

Sidan finns pa svenska och engelska. Engelskan ligger inte som egna filer: /en/ serveras ur
samma HTML med texten bytt (web/sprak.py), sa en andrad svensk rad inte kan lamna en gammal
engelsk efter sig i tysthet -- `python3 web/sprak.py` sager vilka rader som saknas.

    python3 web/serva.py                # http://127.0.0.1:8790
    WEBB_PORT=9000 python3 web/serva.py # annan port

Proven kors med `python3 web/test_webb.py` (de startar sin egen server pa en ledig port).
"""
from __future__ import annotations

import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROT))
import sprak  # noqa: E402 -- språkfilerna och bytet bor i samma mapp som den här filen

PREFIX = "/en"


def vill_engelska(rubrik: str) -> bool:
    """Vill webbläsaren hellre ha engelska? Högsta q-värdet i Accept-Language vinner.

    Bara engelska leder till /en: allt annat far svenska (sidans eget sprak), sa en tysk
    webblasare far den svenska sidan i stallet for en gissning.
    """
    poster = []
    for plats, del_ in enumerate(rubrik.split(",")):
        bitar = del_.strip().split(";")
        tagg = bitar[0].strip().lower()
        if not tagg:
            continue
        q = 1.0
        for parameter in bitar[1:]:
            parameter = parameter.strip()
            if parameter.startswith("q="):
                try:
                    q = float(parameter[2:])
                except ValueError:
                    pass
        poster.append((-q, plats, tagg))
    return bool(poster) and sorted(poster)[0][2].split("-")[0] == "en"


class Tyst(SimpleHTTPRequestHandler):
    """Roten ar web/, och bara web/: en sokvag utanfor den ar en 404, inte ett fillistande."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROT), **kw)

    def list_directory(self, path):        # noqa: ARG002 -- en katalog ar ingen sida
        self.send_error(404, "Not Found")
        return None

    def end_headers(self):
        """Ingen skall hinna se en gammal sida.

        Cloudflare cachar .css och .png pa egen hand nar origin inte sager nagot, och da kunde
        Alex fa den NYA sidan med den GAMLA stilmallen -- sidan sag trasig ut utan att nagot
        var trasigt (matt: cf-cache-status HIT, age 745 s, en stilmall fran 09:45 medan
        HTML:en var farsk). no-store stanger av cachningen bade i kanten och i webblasaren, och
        sidan ar fem filer pa en lokal maskin: att hamta om den kostar ingenting.

        ponytail: no-store pa allt. Vill nagon senare spara bandbredd, lat bilderna fa en kort
        max-age -- men aldrig HTML eller CSS.
        """
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002 -- basklassen har den namnet
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    # -- spraken -----------------------------------------------------------------

    def do_GET(self):                      # noqa: N802 -- basklassen har de har namnen
        self.svara(kropp=True)

    def do_HEAD(self):                     # noqa: N802
        self.svara(kropp=False)

    def svara(self, kropp: bool) -> None:
        """Svenskan gar rakt igenom (filerna pa disk); engelskan byggs ur samma fil."""
        vag = unquote(urlsplit(self.path).path)
        if vag in ("/en", PREFIX + "/"):
            if vag == PREFIX:
                return self.omdirigera(PREFIX + "/", 301)
            vag = "/index.html"
        elif not vag.startswith(PREFIX + "/"):
            if vag in ("/", "/index.html") and vill_engelska(self.headers.get("Accept-Language", "")):
                return self.omdirigera(PREFIX + "/" if vag == "/" else PREFIX + vag)
            return super().do_GET() if kropp else super().do_HEAD()
        else:
            vag = vag[len(PREFIX):]

        fil = (ROT / vag.lstrip("/")).resolve()
        if not fil.is_file() or ROT not in fil.parents:
            return self.send_error(404, "Not Found")
        tabell = sprak.ladda(sprak.MAL)
        if not tabell:
            # Tyst svenska pa en engelsk adress ar varre an ett fel: sida som sida ser
            # oversatt ut (lang="en") medan texten inte ar det. Journalen far veta.
            sys.stderr.write("sprak: %s saknas eller ar trasig -- ingen engelsk sida\n"
                             % (sprak.I18N / (sprak.MAL + ".json")))
            return self.send_error(503, "Translation missing")
        data = fil.read_bytes()
        if fil.suffix == ".html":
            data = sprak.oversatt(data.decode("utf-8"), tabell, vag.lstrip("/")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(fil)))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Language", sprak.MAL)
        self.end_headers()
        if kropp:
            self.wfile.write(data)

    def omdirigera(self, mal: str, kod: int = 302) -> None:
        """302 = sprakvalet vid dörren, 301 = adressen som saknade sitt snedstreck."""
        self.send_response(kod)
        self.send_header("Location", mal)
        if kod == 302:
            self.send_header("Vary", "Accept-Language")
        self.end_headers()


class Server(ThreadingHTTPServer):
    """`server_name` satts ur den egna adressen.

    SimpleHTTPRequestHandler fragar socket.getfqdn(bind) i server_bind() -- en omvand
    DNS-fraga som ligger MELLAN bind() och listen(). Med en slo resolver star processen dar
    utan att lyssna, utan logg, utan fel. Namnet behovs bara i sidhuvudet, sa vi satter det
    sjalva i stallet.
    """

    def server_bind(self):
        super().server_bind()
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def main() -> int:
    port = int(os.environ.get("WEB_PORT") or os.environ.get("WEBB_PORT") or 8790)
    bind = os.environ.get("WEBB_BIND") or "127.0.0.1"
    httpd = Server((bind, port), Tyst)
    print("web: http://%s:%d (roten: %s)" % (bind, port, ROT), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
