#!/usr/bin/env python3
"""GodJIRA:s infosida, serverad.

En liten statisk server med bara standardbiblioteket -- samma krav som panelen sjalv. Den
ligger med flit pa en EGEN port (8790) och inte pa panelens 8788: den har sidan skall kunna
nas utifran, och panelen skall aldrig kunna det. Tunneln pekar pa den har porten och inget
annat.

    python3 web/serva.py                # http://127.0.0.1:8790
    WEBB_PORT=9000 python3 web/serva.py # annan port

Proven kors med `python3 web/test_webb.py` (de startar sin egen server pa en ledig port).
"""
from __future__ import annotations

import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROT = Path(__file__).resolve().parent


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
