#!/usr/bin/env python3
"""Bygger assets/world.svg: en varldskarta som en enda SVG-path, och provar att den vander ratt.

Natural Earth 110m (land) via npm-paketet world-atlas 2.x. Naturdata: Natural Earth,
public domain (se assets/world.LICENSE.md).

Kartan ritas i gradskalan 0..360 x 0..180, sa att en punkt satts med lon/lat rakt av:
x = lon + 180, y = 90 - lat. Y vaxer nedat i SVG, alltsa ar nord TOPPEN -- och det ar
precis det har gatt fel: forsta versionen skalade om kallans y RAKT AV, och da hamnade
Antarktis i overkant (matt: 177 landpunkter i full bredd overst, och noll landpunkter dar
Stockholm ligger).

Kor:  python3 tools/build_world.py
"""
from __future__ import annotations

import json
import pathlib
import urllib.request

KALLA = "https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json"
ROT = pathlib.Path(__file__).resolve().parent.parent
UT = ROT / "assets" / "world.svg"


def hamta(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as svar:
        return json.loads(svar.read())


def punkter_ur_arc(arc, skal_x, skal_y, flytt_x, flytt_y):
    x = y = 0.0
    ut = []
    for dx, dy in arc:
        x += dx
        y += dy
        ut.append((x * skal_x + flytt_x, y * skal_y + flytt_y))
    return ut


def ring(index, raw):
    skal = raw["transform"]["scale"]
    flytt = raw["transform"]["translate"]
    p = []
    for i in index:
        bit = punkter_ur_arc(raw["arcs"][i] if i >= 0 else raw["arcs"][~i][::-1],
                             skal[0], skal[1], flytt[0], flytt[1])
        p += bit[1:] if p else bit
    return p


def polygoner(geom):
    if geom["type"] == "GeometryCollection":
        return [poly for g in geom["geometries"] for poly in polygoner(g)]
    return [geom["arcs"]] if geom["type"] == "Polygon" else geom["arcs"]


def bygg() -> tuple[str, list[tuple[float, float]]]:
    raw = hamta(KALLA)
    ringar = []
    for poly in polygoner(raw["objects"]["land"]):
        for r in poly:
            pts = ring(r, raw)
            if len(pts) >= 4:
                ringar.append(pts)

    alla = [p for r in ringar for p in r]
    min_x, max_x = min(p[0] for p in alla), max(p[0] for p in alla)
    min_y, max_y = min(p[1] for p in alla), max(p[1] for p in alla)
    bredd, hojd = max_x - min_x, max_y - min_y

    # En equirectangular karta ar dubbelt sa bred som hog. Ar den inte det ar kallan inte
    # grader, och da ar punktformeln ovan en gissning.
    assert 1.85 < bredd / hojd < 2.15, ("kallan ar inte 2:1 -- inte equirectangular", bredd / hojd)

    def grad(p):
        # Y VANDS: kallans y vaxer norrut, SVG:s nedat. Utan vandningen blir varlden uppochner.
        return ((p[0] - min_x) / bredd * 360.0, (max_y - p[1]) / hojd * 180.0)

    grader = [grad(p) for p in alla]
    d = "".join("M" + " ".join("%.1f %.1f" % grad(p) for p in r) + "Z" for r in ringar)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 180" '
           'preserveAspectRatio="none"><path fill-rule="evenodd" d="' + d + '"/></svg>')
    return svg, grader


def kontrollera(grader: list[tuple[float, float]]) -> None:
    """Vandningen provas mot varlden sjalv, inte mot formeln som gjorde fel."""
    band = lambda lo, hi: [p for p in grader if lo <= p[1] < hi]
    nederst, overst = band(171.0, 181.0), band(0.0, 9.0)
    spann = lambda p: (max(x for x, _ in p) - min(x for x, _ in p)) if p else 0

    # Antarktis ar det enda land som gar runt hela jorden: full bredd i nederkant.
    assert len(nederst) > 100 and spann(nederst) > 300, \
        ("ingen fullbredds-landmassa nederst -- Antarktis skall ligga dar", len(nederst), spann(nederst))
    # Arktis ar hav: overst finns land (Gronland, Sibirien) men inte runt hela jorden.
    assert spann(overst) < 300, ("land i full bredd overst -- da ar kartan uppochner", spann(overst))

    # Och sa riktiga stader: ligger de dar lon/lat sager att de skall ligga?
    for namn, lat, lon in (("Stockholm", 59.33, 18.07), ("Sydney", -33.87, 151.21),
                           ("New York", 40.71, -74.01)):
        x, y = lon + 180, 90 - lat
        nara = [p for p in grader if abs(p[0] - x) < 6 and abs(p[1] - y) < 6]
        assert nara, ("ingen landpunkt inom 6 grader av %s (%s, %s)" % (namn, lat, lon), x, y)
    print("kontroll: Antarktis nederst, Arktis utan fullbreddsland, och tre stader pa land")


def main() -> int:
    svg, grader = bygg()
    kontrollera(grader)
    UT.parent.mkdir(exist_ok=True)
    UT.write_text(svg, encoding="utf-8")
    (UT.parent / "world.LICENSE.md").write_text(
        "# world.svg\n\n"
        "En varldskarta i equirectangular projektion, byggd ur Natural Earth 110m (land) via\n"
        "npm-paketet `world-atlas` 2.x av tools/build_world.py. Naturdata: Natural Earth, public\n"
        "domain. Paketets kod: ISC.\n\n"
        "TopoJSON-arcarna viks ut till en enda SVG-path i gradskalan 0..360 x 0..180, sa att en\n"
        "punkt satts med lon/lat rakt av: x = lon + 180, y = 90 - lat (nord ar toppen). Rundat\n"
        "till en decimal -- fler syns inte i den storlek panelen visar kartan.\n",
        encoding="utf-8")
    print("skrev %s: %d rutor, %d tecken (%.0f kB)"
          % (UT.name, svg.count("<path") and svg.count("M"), len(svg), len(svg) / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
