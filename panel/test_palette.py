#!/usr/bin/env python3
"""Paletten i panel/index.html: rätt ramp, läsbar accent.

Kör: python3 panel/test_palette.py   (exit 0 = grönt)

Provet finns för att paletten numera är data på ett ställe: en rad i :root. Går
någon in och pillar i den ska två saker hålla -- ytorna ska klättra uppåt utan
hopp bakåt (det var felet: rälen låg under kanvasen och allt flöt ihop till en
svart massa) och accenten ska gå att läsa på kanvasen.
"""
import re
import sys
from pathlib import Path

HTML = Path(__file__).with_name("index.html").read_text()
m = re.search(r":root\s*\{(.*?)\}", HTML, re.S)
if not m:
    sys.exit("FEL: hittar ingen :root i index.html")
ROOT = m.group(1)
TOKENS = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", ROOT))

# Ordningen ytorna möter ögat i. Varje steg ska vara ljusare än det förra.
LADDER = ["bg", "rail", "side", "panel", "card", "card-hi"]
# Text på kanvasen. Accenten bär länkar och glyfer, alltså minst 4,5:1 (WCAG AA).
TEXT = ["text", "text-2", "muted"]


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
    for name in LADDER + TEXT + ["accent", "navy", "green", "amber", "red"]:
        if name not in TOKENS:
            fel.append("token --%s saknas i :root" % name)

    for under, over in zip(LADDER, LADDER[1:]):
        if under in TOKENS and over in TOKENS:
            # Lika steg är tillåtna (Plane har surface-2 och layer-1 på samma rung);
            # det som var felet var ett steg som gick MÖRKARE än det förra.
            if lum(TOKENS[over]) < lum(TOKENS[under]) - 1e-9:
                fel.append("ytan --%s (%s) är mörkare än --%s (%s): stegen går bakåt"
                           % (over, TOKENS[over], under, TOKENS[under]))

    if "bg" in TOKENS and "accent" in TOKENS:
        c = contrast(TOKENS["accent"], TOKENS["bg"])
        if c < 4.5:
            fel.append("accenten --accent %s bara %.2f:1 mot --bg %s (krav 4,5:1)"
                       % (TOKENS["accent"], c, TOKENS["bg"]))
        else:
            print("accenten: %.2f:1 mot kanvasen" % c)

    if "bg" in TOKENS:
        for t in TEXT:
            if t in TOKENS:
                c = contrast(TOKENS[t], TOKENS["bg"])
                print("--%-6s %.2f:1 mot kanvasen" % (t, c))
                if c < 4.5:
                    fel.append("texten --%s %s bara %.2f:1 mot kanvasen" % (t, TOKENS[t], c))

    if fel:
        print("\n".join("FEL: " + f for f in fel))
        return 1
    print("paletten ok: %s" % " < ".join(TOKENS[n] for n in LADDER))
    return 0


if __name__ == "__main__":
    sys.exit(main())
