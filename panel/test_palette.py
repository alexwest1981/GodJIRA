#!/usr/bin/env python3
"""Paletten i panel/index.html: rätt ramp och läsbara färger, i BÅDA temana.

Kör: python3 panel/test_palette.py   (exit 0 = grönt)

Provet finns för att paletten är data på ett ställe: tokenraderna i :root, med båda
temana i samma rad (light-dark(ljust, mörkt)). Går någon in och pillar ska tre
saker hålla:

  1. Ytorna klättrar uppåt i båda temana. Det var felet: rälen låg MÖRKARE än
     kanvasen, och då flöt krom, sida och innehåll ihop till en svart massa.
  2. Hover-steget skiljer sig från kortet, annars finns ingen hover i det temat.
  3. Accent och text går att läsa mot kanvasen (WCAG AA 4,5:1), och status- och
     typfärgerna ligger över 3:1 (de bär glyfer och tintar, inte brödtext).
"""
import re
import sys
from pathlib import Path

HTML = Path(__file__).with_name("index.html").read_text()
m = re.search(r":root\s*\{(.*?)\}", HTML, re.S)
if not m:
    sys.exit("FEL: hittar ingen :root i index.html")

# --namn: light-dark(#a, #b)  eller  --namn: #a   ->  {namn: (ljust, mörkt)}
TOKENS = {}
for namn, ljus, mork in re.findall(
        r"--([a-z0-9-]+):\s*light-dark\(\s*(#[0-9a-fA-F]{6})\s*,\s*(#[0-9a-fA-F]{6})\s*\)", m.group(1)):
    TOKENS[namn] = (ljus, mork)
for namn, enda in re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", m.group(1)):
    TOKENS.setdefault(namn, (enda, enda))

# Ordningen ytorna möter ögat i: kanvas -> räl -> sidofält -> panel -> kort.
LADDER = ["bg", "rail", "side", "panel", "card"]
# Texten mot kanvasen.
TEXT = ["text", "text-2", "muted"]
# Status- och typfärger: glyfer, prickar och tintar.
GLYF = ["green", "amber", "red", "navy"]
TEMAN = ("ljust", "mörkt")


def srgb(v):
    v /= 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def lum(hex6):
    r, g, b = (int(hex6[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b)


def contrast(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def main():
    fel = []
    for namn in LADDER + TEXT + GLYF + ["accent", "card-hi"]:
        if namn not in TOKENS:
            fel.append("token --%s saknas i :root" % namn)
    if fel:
        print("\n".join("FEL: " + f for f in fel))
        return 1

    for i, tema in enumerate(TEMAN):
        # Lika steg är tillåtna (Plane har surface-2 och layer-1 på samma rung);
        # det som var felet var ett steg som gick MÖRKARE än det förra.
        for under, over in zip(LADDER, LADDER[1:]):
            a, b = TOKENS[under][i], TOKENS[over][i]
            if lum(b) < lum(a) - 1e-9:
                fel.append("%s: ytan --%s (%s) är mörkare än --%s (%s), stegen går bakåt"
                           % (tema, over, b, under, a))
        # Ljusläget skall inte lysa: högst EN yta får vara nära vit (#f9f9f9 och
        # uppåt). Fyra vita ytor var "flashbangen" -- panelen, sidofältet, rälen
        # och kortet var alla #ffffff eller nästan.
        vita = [n for n in LADDER if lum(TOKENS[n][0]) > 0.94] if i == 0 else []
        if len(vita) > 1:
            fel.append("ljust: %d ytor är nära vita (%s), högst en" % (len(vita), ", ".join(vita)))
        if TOKENS["card-hi"][i] == TOKENS["card"][i]:
            fel.append("%s: --card-hi är samma färg som --card, ingen hover syns" % tema)
        for namn, krav in [("accent", 4.5)] + [(t, 4.5) for t in TEXT] + [(g, 3.0) for g in GLYF]:
            c = contrast(TOKENS[namn][i], TOKENS["bg"][i])
            if c < krav:
                fel.append("%s: --%s %s bara %.2f:1 mot kanvasen (krav %.1f:1)"
                           % (tema, namn, TOKENS[namn][i], c, krav))

        print("%s: %s | accent %.2f:1 | text %s | glyfer %s" % (
            tema,
            " < ".join(TOKENS[n][i] for n in LADDER),
            contrast(TOKENS["accent"][i], TOKENS["bg"][i]),
            "/".join("%.1f" % contrast(TOKENS[t][i], TOKENS["bg"][i]) for t in TEXT),
            "/".join("%.1f" % contrast(TOKENS[g][i], TOKENS["bg"][i]) for g in GLYF)))

    # Märket: den tvåfärgade logotypen ska bli en färg per tema. Rälens märke målas med
    # --text (mask), logotypen i splash-fönstret filtreras. Tappar någon någon av dem står
    # logotypen kvar i marinblått mot mörk botten och syns knappt -- det var felet.
    if "background-color: var(--text);" not in HTML or "url(/godjira.svg)" not in HTML:
        fel.append("rälens märke målas inte med --text (mask), logotypen följer inte temat")
    for val in ('[data-theme="light"] .logo { filter: brightness(0); }',
                '[data-theme="dark"] .logo { filter: brightness(0) invert(1); }'):
        if val not in HTML:
            fel.append("logotypen saknar filterregel: " + val)

    # Tavlan: varje nodtyp som får en glyf måste också få en färg, och färgerna måste
    # finnas i paletten. Tappar någon färgen blir rutorna i kodkartan grå klumpar igen --
    # där är n.type bara filantalet ("3 filer") och lagret ligger i namnet, så en ny
    # nodtyp utan färg märks inte förrän man tittar på tavlan.
    tab = re.search(r"const FLOW_KINDS = \{(.*?)\n\};", HTML, re.S)
    if not tab:
        fel.append("hittar inte FLOW_KINDS i index.html")
    else:
        # Rader med BÅDE glyf och färg. En halv rad fångas: det var felet som gjorde
        # varje ruta i kodkartan till samma grå klump.
        rader = re.findall(r'([A-Za-z]+): \["(.)", "(var\(--[a-z0-9-]+\))"\]', tab.group(1))
        antal = len(re.findall(r"[A-Za-z]+: \[", tab.group(1)))
        if len(rader) != antal:
            fel.append("FLOW_KINDS: %d rader, bara %d med både glyf och färg" % (antal, len(rader)))
        farger = set()
        for _, _, farg in rader:
            tok = re.search(r"var\(--([a-z0-9-]+)\)", farg).group(1)
            farger.add(tok)
            if tok not in TOKENS:
                fel.append("FLOW_KINDS pekar på --%s som inte finns i :root" % tok)
        fall = re.search(r'const FLOW_FALLBACK = \["(.)", "(var\(--([a-z0-9-]+)\))"\]', HTML)
        if not fall:
            fel.append("FLOW_FALLBACK saknas i index.html")
        elif fall.group(3) not in TOKENS:
            fel.append("FLOW_FALLBACK pekar på --%s som inte finns i :root" % fall.group(3))
        print("tavlan: %d sorter i %d färger" % (len(rader), len(farger)))

    if fel:
        print("\n".join("FEL: " + f for f in fel))
        return 1
    print("paletten ok i båda temana")
    return 0


if __name__ == "__main__":
    sys.exit(main())
