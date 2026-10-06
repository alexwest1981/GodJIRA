#!/usr/bin/env python3
"""Rörelsen: ett lager, och en grind som håller det så.

Rörelse är lätt att sprida ut -- en transition här, en animation där -- och till slut har
varje knapp sin egen tid och ingen vet vad som gäller. Provet mäter:

  1. Allt som rör sig ligger innanför (prefers-reduced-motion: no-preference).
  2. Ingen rörelse hittar på sin egen tid: tiderna kommer ur tokens.
  3. Fokusringen finns kvar (två regler stänger av musringen, inte tangentbordets).
  4. Uppräkningen av siffror respekterar inställningen och startar inte om sig själv.
"""
import re
import sys
from pathlib import Path

HTML = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
FAILED: list = []


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        FAILED.append(what)


def klamrar(text: str, från: int) -> str:
    """Texten innanför det block som öppnas av första { efter `från`."""
    j = text.index("{", från)
    djup = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            djup += 1
        elif text[k] == "}":
            djup -= 1
            if djup == 0:
                return text[j + 1:k]
    return ""


css = HTML.split("<style>", 1)[1].split("</style>", 1)[0]
js = HTML.rsplit("<script>", 1)[1]
i = css.find("@media (prefers-reduced-motion: no-preference)")
check(i > 0, "rörelselagret finns")
block = klamrar(css, i) if i > 0 else ""
check(len(block) > 400, "lagret har innehåll (%d tecken)" % len(block))

utanför = [rad.strip() for rad in (css[:i] if i > 0 else css).splitlines()
           if re.search(r"\b(transition|animation)\s*:", rad)]
check(len(utanför) <= 3, "utanför lagret rör sig bara lastningen (%d rader)" % len(utanför))
for rad in utanför:
    check("var(--t" in rad or "spin" in rad,
          "undantaget bär en token eller är ringen: %s" % rad[:58])

siffror = re.findall(r"\b\d+(?:\.\d+)?m?s\b", block)
check(not siffror, "inga tider skrivna på plats i lagret (%s)" % (siffror or "inga"))
check("--ease:" in css and "--t:" in css, "token finns: --t och --ease")
for token in ("--t-fast", "--t-slow", "--t-step", "--ease-io"):
    check(token in css, "token finns: %s" % token)

check(block.count("transition") >= 2, "lagret har transitioner")
check(block.count("animation-delay") >= 4, "entrén är en trappa, inte en klump")
check("tabular-nums" in block, "siffrorna står still i bredd")
check("outline: none" not in block, "lagret stjäl inte fokus")

check(":focus-visible" in css, "tangentbordet får en ring (:focus-visible finns)")
for selektor, kropp in re.findall(r"^[ \t]*([^\n{]*?):focus\s*\{([^}]*)\}", css, re.M):
    if "outline: none" in kropp:
        check(":focus-visible" in css,
              "nollställd musring (%s) har en ring för tangentbordet" % selektor.strip()[:26])

check("countUp" in js, "siffrorna räknar upp")
check("prefers-reduced-motion" in js, "uppräkningen stängs av med rörelsen")
check("WeakSet" in js, "en pågående uppräkning startas inte om")
check("MutationObserver" in js, "uppräkningen hänger på renderingen, inte på varje renderare")
check("</html>" in HTML, "filen är hel (stängd)")

print()
if FAILED:
    print("%d check(s) failed" % len(FAILED))
    sys.exit(1)
print("all checks passed")
