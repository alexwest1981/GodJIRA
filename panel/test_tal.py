#!/usr/bin/env python3
"""Sajtvyens tabeller: rader() skall ge en tabell man kan läsa av.

Provet ritar ingen panel. Det tar rader() ur index.html och kör den i node med en lista
där ena raden är dubbelt så stor som den andra -- då MÅSTE stapelns längd skilja sig, och
det var precis vad den inte gjorde förut (flex:1 fyllde raden, så varje streck blev lika
långt och siffran stod utan något att jämföra med).
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
HTML = (HERE / "index.html").read_text(encoding="utf-8")

# esc() behövs av rader(), och den ligger i samma fil -- samma källa, inget prov som glider isär.
ESC = re.search(r"const esc = s =>.*?\n", HTML, re.S)
RADER = re.search(r"  const rader = \(lista, forsta\) => \{.*?\n  \};", HTML, re.S)

PROV = """
{t}const t = (namn, ok) => console.log((ok ? "OK " : "FEL ") + namn);
const h = rader([["/", 8], ["/om", 4]], "sida");
t("rubrikraden säger vad kolumnerna är", h.includes("<th>sida</th>") && h.includes(">antal<") && h.includes(">andel<"));
t("tabellen är en tabell", h.startsWith('<table class="tal">') && h.endsWith("</table>"));
t("stapelns längd är värdet", h.includes("width:100%") && h.includes("width:50%"));
t("procenten är andelen av summan", h.includes(">67 %<") && h.includes(">33 %<"));
t("siffrorna står i egna celler", h.includes('class="n">8<') && h.includes('class="n">4<'));
t("tom lista ger ingen tabell", rader([], "namn") === "" && rader(null) === "");
const k = rader([["Sweden", 12, "SE"]], "land");
t("landet får sin kod på raden", k.includes('data-kod="SE"'));
t("en rad utan kod får inget attribut", !h.includes("data-kod"));
""".format(t=(ESC.group(0) + RADER.group(0)) if (ESC and RADER) else "")

ANTAL = 8

# Kartans punkter: regeln måste peka på något markupen faktiskt skriver. Första versionen
# letade efter en .punkter-behållare som aldrig fanns, så punkterna ritades som tomma
# inline-element -- osynliga -- medan listan bredvid visade länder.
# Kommentarer skalas av först: den här filens egna kommentarer nämner felet, och ett prov
# som fälls av sin egen förklaring är inget prov.
KOD = re.sub(r"^\s*//.*$", "", re.sub(r"/\*.*?\*/", "", HTML, flags=re.S), flags=re.M)
STIL = [
    (".karta i {" in KOD, "punkterna har en regel som träffar dem (.karta i)"),
    (".karta .punkter" not in KOD, "ingen regel pekar på en klass markupen inte skriver"),
]


def main() -> int:
    if not (ESC and RADER):
        print("FAILED: esc() eller rader() hittades inte i index.html")
        return 1
    kord = subprocess.run(["node", "-e", PROV], capture_output=True, text=True)
    rader = [r for r in kord.stdout.strip().splitlines() if r.strip()]
    for rad in rader:
        print("  " + ("ok   " if rad.startswith("OK") else "FAIL ") + rad[3:])
    for ok, vad in STIL:
        print("  " + ("ok   " if ok else "FAIL ") + vad)
    if kord.stderr.strip():
        print("node: " + kord.stderr.strip()[:300])
    fel = [r[3:] for r in rader if not r.startswith("OK")] + [vad for ok, vad in STIL if not ok]
    if len(rader) != ANTAL:
        fel.append("%d prov kördes, väntade %d" % (len(rader), ANTAL))
    if fel:
        print("FAILED: " + ", ".join(fel))
        return 1
    print("all good: tabellerna har rubrikrad, proportionerliga staplar och procent, "
          "och kartans punkter syns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
