#!/usr/bin/env python3
"""Engelskan på infosidan: samma filer, samma markup, bytta textnoder.

Sidan är tre statiska HTML-filer utan JavaScript, och det är ett löfte som
`integritet.html` ger och `test_webb.py` mäter. Översättningen sker därför i servern i
stället för i webbläsaren: samma fil ligger kvar på disk, men texten byts mot raden i
`i18n/<språk>.json` innan svaret går ut.

**Nycklarna är den svenska texten själv**, precis som i panelen (`panel/i18n/sv.json`),
så ingen ritning behöver skrivas om -- och en ändrad svensk rad blir en ny nyckel i
stället för en tyst gammal översättning. `python3 web/sprak.py` säger vilka som saknas.

    python3 web/sprak.py            # läget för varje språkfil
    python3 web/sprak.py --lista    # källistan ur de tre sidorna
    python3 web/sprak.py --json     # skriv web/i18n/sv.json (genererad -- rör den inte för hand)

Textnoden är enheten. Det är den som går att byta utan att röra markupen: en stycke på
tre rader är EN nod, och den delas aldrig av att en `<em>` står mitt i meningen. Priset är
att en rad som byggs ihop av flera noder (``<strong>Ingen JavaScript.</strong> Sidorna…``)
blir två nycklar -- som var för sig är en hel mening att översätta.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROT = Path(__file__).resolve().parent
I18N = ROT / "i18n"
SIDOR = ("index.html", "integritet.html", "villkor.html")
KALLA = "sv"
MAL = "en"
SPRAKVAL = re.compile(r"<a[^>]*data-sprak[^>]*>.*?</a>", re.S)

# Taggar och kommentarer: allt som inte är text hålls orört (klassnamn, id:n, sökvägar).
BITAR = re.compile(r"<!--.*?-->|<[^>]*>", re.S)
# Attribut som syns för en läsare eller en skärmläsare. `content` sitter bara på meta.
ATTRIBUT = re.compile(r'\b(alt|content|title|aria-label)="([^"]*)"')
# En sida: href="villkor.html#namnet". Bara .html -- stilmallen och bilderna är inte sidor.
SIDLANK = re.compile(r'href="((?![/#]|[a-zA-Z]+:)[\w.-]+\.html(?:#[\w-]+)?)"')
# Resten av de relativa adresserna blir absoluta: den engelska sidan ligger ett steg
# djupare (/en/villkor.html), och `href="stil.css"` skulle då leta i /en/.
RELATIV = re.compile(r'\b(href|src)="((?![/#]|[a-zA-Z]+:)[^"]*)"')

# Rader som står oförändrade i båda språken: kommandon, sökvägar, adresser, färger,
# metavärden och egennamn. Ingen nyckel, alltså ingen rad att översätta -- och därför är
# regeln för resten av filen att **ingen annan rad får vara lika på båda språken**.
# En rad för mycket här är en oöversatt rad i en engelsk skärm; `test_webb.py` räknar
# listan mot taket, så den inte växer i tysthet.
OFORANDRA = {
    # maskinvärden: ett metafält, en färg, en adress, en sökväg
    "width=device-width, initial-scale=1",
    "#0a0a0a",
    "http://127.0.0.1:8788",
    "godjira.se",
    "godjira@godjira.se",
    "github.com/alexwest1981/GodJIRA",
    "~/.config/jira-flow/",
    "~/.local/state/jira-flow/",
    "~/.local/state/jira-flow/actions.log",
    # kommandon och terminalens egen rad
    "git clone https://github.com/alexwest1981/GodJIRA.git ~/Projects/godjira",
    "cd ~/Projects/godjira &amp;&amp; python3 panel/server.py",
    "cd ~/Projects/godjira &amp;&amp; ./install.sh",
    "gh auth login",
    "terminal",
    # egennamn och licensens eget namn
    "GodJIRA",
    "God",
    "JIRA",
    "GitHub",
    "Python",
    "AGPL-3.0",
    "GNU Affero General Public License version 3",
    "mock",
    "Alex Weström",
    "GodJIRA · AGPL-3.0 · © 2026 Alex Weström ·",
}


def har_bokstaver(text: str) -> bool:
    return any(tecken.isalpha() for tecken in text)


def nyckel(text: str) -> str:
    """Textnoden som nyckel: radbrytningarna indragna till ett mellanslag."""
    return " ".join(text.split())


def bitar(dokument: str):
    """Dokumentet som text- och taggbitar, i ordning.

    `oversattbar` är falsk för språkvalets egen text (``<a data-sprak>English</a>``): den
    raden är språkets namn på sig självt, och den byggs av `oversatt()` för det språk som
    skall visas -- den står inte i någon språkfil.
    """
    sista, hoppa = 0, False
    for traff in BITAR.finditer(dokument):
        if traff.start() > sista:
            text = dokument[sista:traff.start()]
            yield "text", text, not hoppa
            hoppa = False
        tagg = traff.group(0)
        yield "tagg", tagg, True
        hoppa = "data-sprak" in tagg
        sista = traff.end()
    if sista < len(dokument):
        yield "text", dokument[sista:], not hoppa


def rader(dokument: str) -> list[str]:
    """Alla textnoder (och alt/content) med bokstäver, i den ordning de står.

    Utan hänsyn till undantagslistan: det är måttet listan prövas mot, så en rad som inte
    längre står på någon sida syns (en död rad är en rad som borde ha tagits bort).
    """
    ut: list[str] = []

    def ta(text: str) -> None:
        rad = nyckel(text)
        if har_bokstaver(rad) and rad not in ut:
            ut.append(rad)

    for slag, bit, oversattbar in bitar(dokument):
        if slag == "text":
            if oversattbar:
                ta(bit)
        else:
            for traff in ATTRIBUT.finditer(bit):
                ta(traff.group(2))
    return ut


def skorda(dokument: str) -> list[str]:
    """Källistan ur en sida: raderna som skall översättas."""
    return [rad for rad in rader(dokument) if rad not in OFORANDRA]


def kalla() -> dict[str, str]:
    """Hela källistan, i den ordning raderna står på sidorna."""
    ut: dict[str, str] = {}
    for sida in SIDOR:
        for rad in skorda((ROT / sida).read_text(encoding="utf-8")):
            ut[rad] = rad
    return ut


def _nod(text: str, tabell: dict[str, str]) -> str:
    """En textnod, bytt mot sin rad. Indraget runt om står kvar (det är markupens form)."""
    rad = nyckel(text)
    if rad not in tabell:
        return text
    fore = text[: len(text) - len(text.lstrip())]
    efter = text[len(text.rstrip()) :]
    return fore + tabell[rad] + efter


def _tagg(tagg: str, tabell: dict[str, str], sprak: str) -> str:
    if tagg.lower().startswith("<html"):
        tagg = tagg.replace('lang="sv"', 'lang="%s"' % sprak, 1)
    tagg = ATTRIBUT.sub(lambda m: "%s=\"%s\"" % (m.group(1), _nod(m.group(2), tabell)), tagg)
    tagg = SIDLANK.sub(r'href="/%s/\1"' % sprak, tagg)
    return RELATIV.sub(r'\1="/\2"', tagg)


def oversatt(dokument: str, tabell: dict[str, str], vag: str = "index.html",
             sprak: str = MAL) -> str:
    """Sidan på målspråket. Rader som saknas i tabellen står kvar på källspråket."""
    ut = []
    for slag, bit, oversattbar in bitar(dokument):
        if slag == "tagg":
            ut.append(_tagg(bit, tabell, sprak))
        else:
            ut.append(_nod(bit, tabell) if oversattbar else bit)
    sida = "".join(ut)
    # Bytet tillbaka: den svenska adressen till samma sida. Texten ("Svenska") är språkets
    # namn på sig självt och står därför inte i någon språkfil.
    bytet = '<a class="sprak" href="/%s" hreflang="sv" lang="sv" data-sprak>Svenska</a>' % vag
    return SPRAKVAL.sub(lambda _: bytet, sida, count=1)


def ladda(sprak: str) -> dict[str, str]:
    """Språkfilen, eller en tom tabell om den inte finns (då står svenskan kvar)."""
    try:
        data = json.loads((I18N / (sprak + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def avvikelser(sprak: str) -> tuple[list[str], list[str], list[str]]:
    """(saknas, extra, oöversatta) för en språkfil, räknade mot de tre sidorna."""
    kalla_ = kalla()
    tabell = ladda(sprak)
    saknas = sorted(k for k in kalla_ if k not in tabell)
    extra = sorted(k for k in tabell if k not in kalla_)
    lika = sorted(k for k, v in tabell.items() if v.strip() == k.strip())
    return saknas, extra, lika


def main(argv: list[str]) -> int:
    if "--lista" in argv:
        for rad in kalla():
            print(rad)
        return 0
    if "--json" in argv:
        data = kalla()
        I18N.mkdir(exist_ok=True)
        (I18N / (KALLA + ".json")).write_text(
            json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("%s.json: %d rader ur %s" % (KALLA, len(data), ", ".join(SIDOR)))
        return 0

    filer = sorted(p.stem for p in I18N.glob("*.json"))
    print("{:<4} {:>5} {:>8} {:>6} {:>12}".format("språk", "rader", "saknas", "extra", "oöversatta"))
    problem = 0
    for sprak in filer:
        if sprak == KALLA:
            print("{:<4} {:>5} {:>8} {:>6} {:>12}".format(sprak, len(kalla()), "-", "-", "-"))
            continue
        saknas, extra, lika = avvikelser(sprak)
        problem += len(saknas) + len(extra) + len(lika)
        print("{:<4} {:>5} {:>8} {:>6} {:>12}".format(
            sprak, len(ladda(sprak)), len(saknas), len(extra), len(lika)))
        for vad, rader in (("saknas", saknas), ("extra", extra), ("oöversatt", lika)):
            for rad in rader:
                print("      %s  %s" % (vad, rad))
    print("\nproblem: %d" % problem)
    return 1 if problem else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
