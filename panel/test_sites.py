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

# Alibit saljer i ett eget Stripe-konto. Tappas den kopplingen lases sajten som tom --
# och en tom kassa ser ut som en sanning. Kontot och markningen skall sta i registret.
alibit = next(s for s in sajter.SAJTER if s["nyckel"] == "alibit")
check(alibit.get("salj"), "Alibit har ingen markning: dess kassa skulle visas som okopplad")
check("alibit" in (alibit.get("kassa_fil") or ""), "Alibit pekar inte pa sin egen nyckelfil")
check(alibit.get("kassa_namn"), "Alibit saknar namnet pa nyckeln i sin miljofil")

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

# --- 7. PostHog-inställningen: kopplas på i panelen, inte i koden -------------------
# Nyckeln får finnas, svaras på och sparas -- men den får aldrig följa med ut i ett svar.
ut = sajter.posthog_lage(env="/finns/inte.env")
check(ut["satt"] is False and ut.get("fel") == "ingen nyckel", "utan nyckel skall laget saga det")
check("phx" not in json.dumps(ut), "nyckeln lacker ut i laget")

serve = (PANEL / "server.py").read_text(encoding="utf-8")
check('if path == "/api/posthog":' in serve, "rutten /api/posthog saknas i server.py")
check('"/api/posthog": posthog_save' in serve, "sparandet av PostHog-inställningen saknas i do_POST")
check("PANEL_KONFIG = Path.home()" in serve, "panelens egen inställningsfil definieras inte")
check("os.chmod(PANEL_KONFIG, 0o600)" in serve, "inställningsfilen skrivs utan 0600")
check("posthog_read" in serve and "seam(\"sites\", \"--posthog\"" in serve,
      "servern frågar inte motorn om PostHog-läget")
for bit in ('id="phBox"', 'data-act="posthog-save"', "function renderPosthog()",
            "async function posthogLoad()", "async function posthogSave("):
    check(bit in html, "inställningsvyn saknar {}".format(bit))
check('renderPosthog(); posthogLoad();' in html, "inställningsvyn laddar inte PostHog-läget")
for nyckel in ("Koppla på PostHog", "Testa anslutningen", "ansluten", "inte ansluten", "källan",
               "händelser senaste sju dygnen", "trafiken hämtas ur ditt eget projekt"):
    check(nyckel in sv, "nyckeln {} saknas i sv.json".format(nyckel))
# Motorn har en egen krok för det panelen frågar efter
check("--posthog" in (ROOT / "bin" / "jira_sites.py").read_text(encoding="utf-8"),
      "motorn har ingen --posthog")

if failures:
    print("MISSLYCKAT:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("Sajtvyn: {} kontroller, allt gront".format(checks))
