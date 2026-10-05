"""Sajtvyn: att motorn svarar med det panelen läser, och att panelen faktiskt visar den.

Körs utan nätverk -- motorns eget prov ror inte natet, och panelkontrollerna laser filer.
Det som mäts är kontraktet mellan dem: fälten motorn skickar är de fält panelen ritar, och
en vy som inte star i TEXT.views ar en dod lank (show() faller tyst tillbaka pa tavlan).
"""
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PANEL = ROOT / "panel"
I18N = PANEL / "i18n"

failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)
    return ok


spec = importlib.util.spec_from_file_location("jira_sites", str(ROOT / "bin" / "jira_sites.py"))
assert spec and spec.loader, "hittar inte bin/jira_sites.py"
sajter = importlib.util.module_from_spec(spec)
sys.modules["jira_sites"] = sajter
spec.loader.exec_module(sajter)

# --- 1. Motorns eget prov ---------------------------------------------------------
sajter.selftest()  # kastar om nagot ar fel; det ar provet, inte en kopia av det

# --- 2. Kontraktet: falten panelen laser finns pa varje sajt ----------------------
for s in sajter.SAJTER:
    for falt in ("nyckel", "namn", "url", "vardar", "tjanst"):
        check(falt in s, "sajten {} saknar faltet {}".format(s.get("nyckel", "?"), falt))
    check(isinstance(s["tjanst"], str) and s["tjanst"].endswith(".service"),
          "{}: tjansten skall vara en systemd-enhet".format(s["nyckel"]))
check(len({s["nyckel"] for s in sajter.SAJTER}) == len(sajter.SAJTER), "tva sajter har samma nyckel")

# --- 3. Panelen: vyn finns, och den ar kopplad till sin egen laddning -------------
html = (PANEL / "index.html").read_text(encoding="utf-8")
check('<section class="view" id="v-sites">' in html, "vyn v-sites saknas i panelen")
check('sites: ["Sajterna"' in html, "sajten ar inte registrerad i TEXT.views (da ar den en dod lank)")
check("sites:" in html.split("const ICONS")[1].split("};")[0], "sajt-ikonen saknas i ICONS")
check("function renderSites()" in html and "async function siteLoad()" in html,
      "ritaren eller laddaren saknas")
check('view === "sites") renderSites()' in html, "render() ritar inte vyn")
check('view === "sites") siteLoad()' in html, "show() laddar inte vyn")
check('fetch("/api/sites")' in html, "panelen fragar inte /api/sites")

# --- 4. Servern: rutten finns och den fragar motorn -------------------------------
server = (PANEL / "server.py").read_text(encoding="utf-8")
check('if path == "/api/sites":' in server, "rutten /api/sites saknas i server.py")
check("def sites_read()" in server, "sites_read saknas i server.py")
check('seam("sites", "--json"' in server, "panelen fragar inte motorn for siffrorna")
check("\n\tsites)" in (ROOT / "n8n" / "bin" / "flow-call.sh").read_text(encoding="utf-8"),
      "flow-call.sh har ingen sites-gren (da svarar seam med panelens egen text)")

# --- 5. Spraket: raderna panelen visar maste finnas som nycklar -------------------
sv = json.loads((I18N / "sv.json").read_text(encoding="utf-8"))
for nyckel in ("Sajterna", "Tjänsterna", "uppe", "nere", "besök", "personer", "kassa",
               "dygn", "mäter inte trafiken", "PostHog saknas", "mest besökta", "varifrån",
               "kassan är inte kopplad", "provkörningar uteslutna"):
    check(nyckel in sv, "nyckeln {} saknas i sv.json -- raden star ooversatt i panelen".format(nyckel))

# --- 6. Tre tillstand: en trasig matning far inte se ut som en nolla --------------
t = sajter.trafik(["minnoria.se"], fraga=lambda *a, **k: (_ for _ in ()).throw(ValueError("nere")))
check(t.get("fel") == sajter.MISSLYCKAT and "visningar" not in t,
      "en misslyckad trafikmatning maste bli ordet, inte noll besok")

if failures:
    print("MISSLYCKAT:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Sajtvyn: {} kontroller, allt gront".format(checks))
