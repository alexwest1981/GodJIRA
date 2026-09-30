"""Plocka ut panelens användarsynliga svenska text -- källistan för översättningen.

    python3 tools/extract_panel.py            # visa listan
    python3 tools/extract_panel.py --json     # skriv panel/i18n/sv.json

Panelen bygger sin HTML i mallsträngar. Den text som blir läsbar för en människa är
den statiska texten mellan ${...}-uttrycken och mellan HTML-taggarna. Det är den här
listan som skall översättas; nycklarna är den svenska texten själv, så panelen kan slå
upp den utan att något anrop måste skrivas om.
"""
import json
import pathlib
import re
import sys

R = pathlib.Path(__file__).resolve().parent.parent   # repots rot, inte en fast stig
panel = (R / "panel/index.html").read_text(encoding="utf-8")

SV = re.compile(r"[åäöÅÄÖ]")
SV_WORD = re.compile(r"\b(alla|och|inga|inte|öppna|öppnar|stäng|senast|visar|väntar|klart|klara|"
                     r"sök|spara|avbryt|ta bort|nytt|nya|repon|uppgifter|ärenden|ärende|flödet|"
                     r"projektet|inställningar|översikt|tavlan|karta|importera|välj|ingen|inget|"
                     r"bara|först|sedan|här|som|din|dina|uppdaterad|lägg|hämta|visa|rensa|"
                     r"tydligt|egen|eget|nej|ja|vyn|rader|rad|sedan|varje|hela|samma|annat|"
                     r"annan|innan|efter|kunde|saknas|finns|behöver|går|kommer|kvar|mellan|"
                     r"under|över|utan|med|till|från|hos|mot|vid|per|hur|vad|när|var|vilka|"
                     r"vilken|vilket|ägaren|ägare|nyckeln|nyckel|behörighet|medlemmar|medlem|"
                     r"oassignerat|assignerat)\b", re.I)

SKIP_VALUE = re.compile(r"^[\s\d\W]*$")


def looks_swedish(text: str) -> bool:
    text = " ".join(text.split())
    if not (3 <= len(text) <= 90) or SKIP_VALUE.match(text):
        return False
    if re.search(r"[<>{}=;\\/|@#*]|\.\.\.|\bpx\b|var\(|--", text):
        return False
    # CSS och klassnamn kan aldrig bli text på skärmen: de skulle bara skräpa i listan
    # (och larma i testet som "oöversatta"). En riktig rad har skiljetecken eller
    # blanksteg; ett ensamt litet ord med bindestreck är ett klassnamn.
    if re.search(r"[\[\]{};]|^[a-z-]+:\s*\S+$", text):
        return False
    if re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+)+$", text):
        return False
    if re.search(r"[a-zA-Z]", text) is None:
        return False
    return bool(SV.search(text) or SV_WORD.search(text))


def strip_expressions(literal: str) -> str:
    """Ta bort ${...} -- kvar blir den statiska texten."""
    out, depth, i = [], 0, 0
    while i < len(literal):
        if literal.startswith("${", i):
            depth, i = 1, i + 2
            while i < len(literal) and depth:
                if literal[i] == "{":
                    depth += 1
                elif literal[i] == "}":
                    depth -= 1
                i += 1
            out.append(" ")
            continue
        out.append(literal[i])
        i += 1
    return "".join(out)


def pieces(raw: str) -> list:
    """Dela upp i textstycken: taggar, uttryck och radbrytningar skiljer."""
    text = strip_expressions(raw)
    text = re.sub(r"<[^>]*>", "\n", text)
    return [p.strip() for p in re.split(r"\n+", text)]


found = {}


def add(text: str, where: str) -> None:
    text = " ".join(text.split())
    if looks_swedish(text):
        found.setdefault(text, set()).add(where)


# Textblocket: nycklarna är kodnamn (nav.overview), men VÄRDENA är den text som syns på
# skärmen. De plockas ut för sig, och blocket tas bort ur resten så inget räknas dubbelt.
block = re.search(r"const TEXT = \{(.*?)\n\};", panel, re.S)
text_block = block.group(1) if block else ""
def add_text(text: str, where: str) -> None:
    """Textblocket: allt som är text är text. Kodnamnen (nav.overview) och ikonerna
    (⦿) sållas bort på formen i stället för på orden -- annars faller svenska ord utan
    å/ä/ö bort, som "oassignerat"."""
    text = " ".join(text.split())
    if not (2 <= len(text) <= 90):
        return
    if re.match(r"^[a-z][a-z0-9]*(\.[a-z0-9]+)+$", text):   # nav.overview, flow.nodes
        return
    if not re.search(r"[a-zA-ZåäöÅÄÖ]", text):
        return
    found.setdefault(text, set()).add(where)


for mm in re.finditer(r'"((?:[^"\\]|\\.)*)"', text_block):
    add_text(mm.group(1).replace('\\"', '"'), "text")

# HTML-kroppen: textnoder och attribut. Skript och stilmall tas bort ur kopian i
# stället för att klippa vid första <script> -- den ligger i <head>, så all markup
# efter den skulle annars hoppas över.
html = re.sub(r"<script\b.*?</script>", "\n", panel, flags=re.S | re.I)
html = re.sub(r"<style\b.*?</style>", "\n", html, flags=re.S | re.I)
for m in re.finditer(r">([^<>]{2,200})<", html):
    add(m.group(1), "html")
for m in re.finditer(r'(?:placeholder|title|aria-label|alt)="([^"]{2,200})"', panel):
    add(m.group(1), "attr")

# JS: mallsträngar och vanliga strängar
script = panel[panel.find("<script"):]
for m in re.finditer(r"`((?:[^`\\]|\\.)*)`", script, re.S):
    for piece in pieces(m.group(1)):
        add(piece, "template")
for m in re.finditer(r'"((?:[^"\\\n]|\\.){2,200})"', script):
    add(m.group(1), "string")
for m in re.finditer(r"'((?:[^'\\\n]|\\.){2,200})'", script):
    add(m.group(1), "string")

# Ord som är svenska men saknar å/ä/ö, och som står inuti ett ${...}-uttryck (en
# reservtext i en mallsträng) -- textstyckena ser dem inte. Läggs till för hand.
# "standarden" är källan panelen visar i spåret, och de tre sista byggs ihop av
# ${...}-uttryck: "nytt projekt" står som text mitt i en mall, "privata"/"publika" står
# som egna ord mellan två räknade tal. Ingen av dem syns för textstyckena.
# "provläge: …" kommer ur panel/server.py (provlägets kvitto), inte ur index.html, men
# den syns på skärmen och går att byta på samma sätt som resten.
EXTRA = ("oassignerat", "standarden", "nytt projekt", "privata", "publika",
         "Utseende", "följ skrivbordet", "ljust", "mörkt",
         "provläge: exempeldata, inget konto kopplat")
for word in EXTRA:
    found.setdefault(word, set()).add("extra")

strings = sorted(found)
print("svenska textstycken i panelen:", len(strings))
print("tecken totalt:", sum(len(s) for s in strings))
if "--json" in sys.argv:
    (R / "panel/i18n").mkdir(exist_ok=True)
    (R / "panel/i18n/sv.json").write_text(
        json.dumps({s: s for s in strings}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("skrev panel/i18n/sv.json")
else:
    for s in strings:
        print("  ", s)