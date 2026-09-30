"""Språken i panelen: samma nycklar i varje fil, inga oöversatta rader, och samma val
som widgeten läser.

Körs utan nätverk. Nycklarna är den svenska texten -- det är därför en saknad nyckel i en
översatt fil syns direkt: panelen visar då svenska på just den raden.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import importlib.util

spec = importlib.util.spec_from_file_location("panel_server", str(ROOT / "server.py"))
assert spec and spec.loader, "hittar inte server.py"
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

I18N = ROOT / "i18n"
SOURCES = sorted(I18N.glob("*.json"))
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)
    return ok


# --- 1. Källistan finns och är rimlig ---------------------------------------------
source = json.loads((I18N / "sv.json").read_text(encoding="utf-8"))
check(len(source) >= 200, "den svenska källistan ser för kort ut: {} rader".format(len(source)))
check(all(k == v for k, v in source.items()), "sv.json skall vara sin egen översättning")

# --- 2. Varje språkfil: samma nycklar, inget oöversatt ----------------------------
translated_files = [p for p in SOURCES if p.stem != "sv"]
check(bool(translated_files), "inga översatta språkfiler hittades i panel/i18n")

for path in translated_files:
    code = path.stem
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        check(False, "{}: trasig json ({})".format(code, err))
        continue
    missing = sorted(set(source) - set(table))
    extra = sorted(set(table) - set(source))
    same = sorted(k for k, v in table.items() if k == v and k != "Jira")
    check(not missing, "{}: {} nycklar saknas, t.ex. {}".format(code, len(missing), missing[:3]))
    check(not extra, "{}: {} nycklar finns inte i källistan, t.ex. {}".format(code, len(extra), extra[:3]))
    # Några rader är egennamn eller redan engelska ("Jira + GitHub") och blir lika i alla
    # språk. Några få sådana är rätt; många betyder att filen inte är översatt.
    check(len(same) <= 4, "{}: {} rader är oöversatta, t.ex. {}".format(code, len(same), same[:4]))

# --- 3. Kända rader skall vara översatta -----------------------------------------
SPOT = {"Inställningar": {"en": "Settings", "de": "Einstellungen"}}
for source_text, wanted in SPOT.items():
    for code, expect in wanted.items():
        table = json.loads((I18N / "{}.json".format(code)).read_text(encoding="utf-8"))
        got = table.get(source_text, "")
        check(got == expect, "{}: {!r} blev {!r}, väntade {!r}".format(code, source_text, got, expect))

# --- 4. Servern: val av språk, fallback och katalogen ----------------------------
have = server.panel_languages()
check("sv" in have, "svenska skall alltid finnas")
check(server.panel_language("en") == "en" if "en" in have else True, "valt språk skall respekteras")
check(server.panel_language("xx") in have, "ett okänt språk skall falla tillbaka, inte krascha")
check(server.panel_language("") in have, "utan önskemål skall ett språk på disk väljas")

data = server.panel_strings("en")
check(data["ok"] is True, "panel_strings skall svara ok")
check(data["language"] in have, "panel_strings skall välja ett språk som finns")
check(len(data["strings"]) >= len(source), "strängtabellen tappar rader")
if "en" in have:
    check(data["strings"].get("Inställningar") == "Settings",
          "den engelska tabellen svarar inte engelska")

catalog = server.available_languages(have)
check({item["code"] for item in catalog} >= {"sv"}, "katalogen tappar svenska")

# --- 5. Panelen: kablarna som gör bytet -------------------------------------------------
page = (ROOT / "index.html").read_text(encoding="utf-8")
for needle, what in [
    ('fetch("/api/strings"', "panelen hämtar inte strängtabellen"),
    ("translateTree", "översättningen körs inte över trädet"),
    ("new MutationObserver", "omritningar fångas inte"),
    ("#langSel", "rullistan i Inställningar saknas"),
    ('"/api/language"', "valet sparas inte"),
    ("languageSave", "sparfunktionen saknas"),
]:
    check(needle in page, what)
check('id="langBox"' in page, "rutan för språkvalet saknas i vyn")

# Språket skall sättas i bryggans config (samma som widgeten), inte bara i webbläsaren.
check('"configure"' in (ROOT / "server.py").read_text(encoding="utf-8"),
      "servern skriver inte språket via bryggans configure")

print("Språk: {} språkfiler, {} nycklar, {} kontroller".format(len(SOURCES), len(source), checks))
if failures:
    print("MISSLYCKAT:")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("OK: alla kontroller passerar")
