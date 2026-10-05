"""PostHog-inställningen: nyckeln sparas 0600, och en nyckel som inte svarar sparas inte alls.

Provet ritar ingen panel. Det prövar skrivvägen: filen, rättigheterna, och att den förra
inställningen ligger kvar när svaret inte kom -- det är hela poängen med knappen.
PostHog-uppslaget byts mot ett svar i provet, så ingenting går över nätet.
"""
import importlib.util
import json
import pathlib
import stat
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PANEL = ROOT / "panel"

spec = importlib.util.spec_from_file_location("panel_server", str(PANEL / "server.py"))
server = importlib.util.module_from_spec(spec)
sys.modules["panel_server"] = server
spec.loader.exec_module(server)

failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)
    return ok


def fil_lage(vag):
    return stat.S_IMODE(vag.stat().st_mode)


with tempfile.TemporaryDirectory() as tmp:
    server.PANEL_KONFIG = pathlib.Path(tmp) / "godjira" / "panel.json"

    # 1. En nyckel som svarar sparas, med 0600 i en 0700-mapp
    server.posthog_read = lambda: {"ok": True, "satt": True, "projekt": 4711, "vard": "eu",
                                  "kalla": "panelens installning", "namn": "Prov", "handelser": 3}
    code, svar = server.posthog_save({"nyckel": "phx_prov", "projekt": "4711", "vard": "eu"})
    check(code == 200 and svar.get("sparad") is True, "en nyckel som svarar skulle sparats: {}".format(svar))
    check(server.PANEL_KONFIG.exists(), "inställningsfilen skrevs inte")
    check(fil_lage(server.PANEL_KONFIG) == 0o600, "filen har fel rättigheter: {}".format(
        oct(fil_lage(server.PANEL_KONFIG))))
    check(fil_lage(server.PANEL_KONFIG.parent) == 0o700, "mappen har fel rättigheter")
    innehall = json.loads(server.PANEL_KONFIG.read_text(encoding="utf-8"))
    check(innehall["posthog"]["nyckel"] == "phx_prov", "nyckeln hamnade inte i filen")
    check("phx_prov" not in json.dumps(svar), "nyckeln läcker tillbaka till webbläsaren: {}".format(svar))

    # 2. En nyckel som inte svarar: inget sparas, och den förra ligger kvar (här: tomt igen)
    server.posthog_read = lambda: {"ok": True, "satt": True, "projekt": 4711, "vard": "eu",
                                   "kalla": "panelens installning", "fel": "HTTP 401 unauthorized"}
    code, svar = server.posthog_save({"nyckel": "phx_fel", "projekt": "4711", "vard": "eu"})
    check(code == 400 and svar.get("ok") is False, "en nyckel som inte svarar skulle avvisats: {}".format(svar))
    check("HTTP 401" in svar.get("error", ""), "felet från PostHog tappas bort: {}".format(svar))
    kvar = json.loads(server.PANEL_KONFIG.read_text(encoding="utf-8"))
    check(kvar["posthog"]["nyckel"] == "phx_prov",
          "den förra nyckeln skrevs inte tillbaka: {}".format(kvar["posthog"].get("nyckel")))
    check(svar.get("atervand") is True, "svaret säger inte att den förra ligger kvar")

    # 3. Utan tidigare inställning: filen städas bort i stället för att ligga kvar fel
    server.PANEL_KONFIG.unlink()
    code, svar = server.posthog_save({"nyckel": "phx_fel2", "projekt": "4711", "vard": "eu"})
    check(code == 400, "andra försöket skulle också avvisats: {}".format(svar))
    check(not server.PANEL_KONFIG.exists(), "en felaktig nyckel lämnades kvar i filen")

    # 4. Utan nyckel alls: sägs rakt ut, och ingenting skrivs
    code, svar = server.posthog_save({"projekt": "4711"})
    check(code == 400 and "nyckel" in svar.get("error", ""), "tom nyckel skulle avvisats: {}".format(svar))
    check(not server.PANEL_KONFIG.exists(), "en tom nyckel skapade en fil")

    # 5. Projekt-id:t stannar som sträng i filen men läses som tal av motorn
    server.posthog_read = lambda: {"ok": True, "satt": True, "projekt": 4711, "vard": "us"}
    code, _ = server.posthog_save({"nyckel": "phx_prov", "projekt": "4711", "vard": "us"})
    innehall = json.loads(server.PANEL_KONFIG.read_text(encoding="utf-8"))
    check(innehall["posthog"]["vard"] == "us", "värden sparades inte: {}".format(innehall))

    # 6. En skräpvärd i kroppen får inte skriva över en giltig
    code, _ = server.posthog_save({"nyckel": "phx_prov", "vard": "mars"})
    innehall = json.loads(server.PANEL_KONFIG.read_text(encoding="utf-8"))
    check(innehall["posthog"]["vard"] == "us", "en påhittad värd skrev över den sparade: {}".format(
        innehall["posthog"].get("vard")))

if failures:
    print("MISLYCKAT:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PostHog-inställningen: {} kontroller, allt grönt".format(checks))
