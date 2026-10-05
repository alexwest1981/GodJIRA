#!/usr/bin/env python3
"""Sajterna: trafiken, kassan och livstecknet -- samlade pa ett stalle.

Panelen visar siffrorna, motorn raknar dem. Samma delning som resten av hubben, och
samma skal: siffror som gar att prova hor i en funktion, inte i en ritning.

Allt las. Ingenting skrivs, ingenting startas, ingenting startas om. En vy som andrar
nagot ar en vy som kan gora skada klockan tre pa natten.

    python3 bin/jira_sites.py             # lasbart
    python3 bin/jira_sites.py --json      # for panelen
    python3 bin/jira_sites.py --selftest  # proven (ror inte natet)
"""

import json
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HEM = pathlib.Path.home()
MISSLYCKAT = "uppslag misslyckades"
# Cloudflare svarar 403 pa python-urllibs standardsträng -- panelen presenterar sig.
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) GodJIRA-panel/1.0"}

# Varje sajt: var den finns, vem som serverar den, och hur dess pengar kanns igen.
#   vardar  -- vardnamnen i PostHog (www och utan www ar samma sajt, inte tva)
#   tjanst  -- systemd-enheten som haller den uppe lokalt (None = bara molnet)
#   salj    -- markningen i Stripe-metadatan. Minnoria marknader sin kassa med order_id,
#              Momento med eventCode/planId (mat i deras egna kod). None = kassan ar inte
#              kopplad till panelen an, och da visas "--", aldrig 0 kr.
#              Att gissa en markning ar varre an att visa att man inte vet.
SAJTER = [
    {"nyckel": "minnoria", "namn": "Minnoria", "url": "https://minnoria.se/",
     "vardar": ["minnoria.se", "www.minnoria.se"], "tjanst": "podposter-shop.service",
     "salj": "order_id", "kassa": "Stripe"},
    {"nyckel": "momento", "namn": "Momento", "url": "https://momentobrollop.se/",
     "vardar": ["momentobrollop.se", "www.momentobrollop.se"], "tjanst": "momento.service",
     "salj": "eventCode", "kassa": "Stripe"},
    {"nyckel": "alibit", "namn": "Alibit", "url": "https://alibit.se/",
     "vardar": ["alibit.se", "www.alibit.se"], "tjanst": "redthread.service",
     "salj": None, "kassa": None},
    {"nyckel": "higgies", "namn": "Higgies", "url": "http://127.0.0.1:3000/",
     "vardar": [], "tjanst": "higgies.service", "salj": None, "kassa": None},
    {"nyckel": "godjira", "namn": "GodJIRA", "url": "http://127.0.0.1:8788/",
     "vardar": [], "tjanst": "godjira-panel.service", "salj": None, "kassa": None},
]

# Tjanster som inte ar sajter men som anda skall synas: en hub som visar att allt ar
# bra medan automatiken ligger nere ar varre an ingen hub (n8n lag nere i tre dygn
# 2026-10-02..05 utan att nagonstans visa det).
TJANSTER = [
    {"nyckel": "n8n", "namn": "n8n (flodena)", "enhet": "n8n.service"},
    {"nyckel": "omniroute", "namn": "OmniRoute (modellrouter)", "enhet": "omniroute.service"},
]

POSTHOG_PROJEKT = 293765

# Panelens egna installningar (PostHog-nyckel, projekt, vard). Utan den har filen lases
# .env som forut -- panelen skall kunna kopplas pa i sin egen vy, utan att ga via mig.
PANEL_KONFIG = HEM / ".config/godjira/panel.json"


def ur_fil(vag, nyckel):
    """Laser en hemlighet ur en nyckel=värde-fil. Vardet skrivs aldrig ut."""
    try:
        rader = pathlib.Path(vag).read_text(errors="replace").splitlines()
    except OSError:
        return None
    for rad in rader:
        if rad.startswith(nyckel + "="):
            return rad.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def panel_konfig(vag=None):
    """Panelens egna installningar. Hemligheter bor har (0600), aldrig i repot."""
    try:
        return json.loads(pathlib.Path(vag or PANEL_KONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def posthog_uppgifter(vag=None, env=None):
    """(nyckel, projekt, bas, varifran). Panelens installning forst, .env som forut.

    Nyckeln lamnar aldrig funktionen i ett svar: panelen far veta om den finns och om
    den svarar, inte vad den ar.
    """
    k = panel_konfig(vag).get("posthog") or {}
    if not isinstance(k, dict):
        k = {}
    egen = (k.get("nyckel") or "").strip()
    nyckel = egen or ur_fil(env or (HEM / ".hermes/.env"), "POSTHOG_PERSONAL_KEY")
    kalla = "panelens installning" if egen else (".env" if nyckel else "ingen")
    try:
        projekt = int(k.get("projekt") or POSTHOG_PROJEKT)
    except (TypeError, ValueError):
        projekt = POSTHOG_PROJEKT
    vard = "us" if str(k.get("vard") or "eu").lower().startswith("us") else "eu"
    return nyckel, projekt, "https://%s.posthog.com" % vard, kalla


def posthog_nyckel():
    return posthog_uppgifter()[0]


def feltext(exc):
    """En rad att visa for den som kopplade: tjanstens egen forklaring, inte var."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            kropp = exc.read().decode("utf-8", "replace")[:200].strip()
        except Exception:  # noqa: BLE001 -- en feltext far aldrig bli ett nytt fel
            kropp = ""
        return "HTTP %d %s" % (exc.code, kropp)
    return "%s: %s" % (type(exc).__name__, exc)


def posthog_mot(bas, projekt, nyckel):
    """Fragar PostHog sjalv: vad heter projektet, och hur manga handelser senaste sju dygnen?

    Tva fragor, for de svarar pa olika saker: namnet visar att nyckeln hor till det projekt
    som star i rutan, siffran visar att den ocksa far lasa. En nyckel som bara svarar pa det
    ena ar fel nyckel for panelen.
    """
    req = urllib.request.Request(bas + "/api/projects/%d/" % projekt,
                                 headers={"Authorization": "Bearer " + nyckel})
    with urllib.request.urlopen(req, timeout=45) as s:
        namn = (json.load(s).get("name") or "").strip()
    rader = hogql_med(nyckel, bas, projekt,
                      "SELECT count() FROM events WHERE timestamp > now() - INTERVAL 7 DAY")
    antal = int((rader[0] or [0])[0]) if rader else 0
    return namn, antal


def posthog_lage(vag=None, mata=None, env=None):
    """Svarar nyckeln, och pa vilket projekt? Det ar vad panelen visar i installningarna."""
    nyckel, projekt, bas, kalla = posthog_uppgifter(vag, env)
    ut = {"satt": bool(nyckel), "projekt": projekt, "vard": bas.split("//")[-1].split(".")[0],
          "kalla": kalla}
    if not nyckel:
        ut["fel"] = "ingen nyckel"
        return ut
    try:
        namn, antal = (mata or posthog_mot)(bas, projekt, nyckel)
        if namn:
            ut["namn"] = namn
        ut["handelser"] = antal
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        ut["fel"] = feltext(exc) if isinstance(exc, urllib.error.HTTPError) else str(exc)
    return ut


def stripe_nyckel():
    """Stripe-kontot delas mellan projekten; nyckeln bor dar det projektet lade den."""
    for vag in (HEM / ".config/podposter/miljo", HEM / ".hermes/.env",
                HEM / "Projects/momento/.env", HEM / "Projects/redthread/.env"):
        v = ur_fil(vag, "STRIPE_SECRET_KEY") or ur_fil(vag, "STRIPE_SECRET")
        if v:
            return v
    return None


def hogql(sql):
    """En HogQL-fraga mot panelens installning. Kastar vidare -- anroparen satter tillstandet."""
    nyckel, projekt, bas, _ = posthog_uppgifter()
    if not nyckel:
        return None
    return hogql_med(nyckel, bas, projekt, sql)


def hogql_med(nyckel, bas, projekt, sql):
    req = urllib.request.Request(
        bas + "/api/projects/%d/query/" % projekt,
        data=json.dumps({"query": {"kind": "HogQLQuery", "query": sql}}).encode(),
        headers={"Authorization": "Bearer " + nyckel, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as s:
        return json.load(s).get("results") or []


def stripe(vag):
    nyckel = stripe_nyckel()
    if not nyckel:
        return None
    req = urllib.request.Request("https://api.stripe.com/v1/" + vag,
                                 headers={"Authorization": "Bearer " + nyckel})
    with urllib.request.urlopen(req, timeout=45) as s:
        return json.load(s)


def forsok(fn, *a, **kw):
    """Ett uppslag: svaret, eller ordet som sager att det inte gick igenom."""
    try:
        v = fn(*a, **kw)
        return MISSLYCKAT if v is None else v
    except (urllib.error.URLError, OSError, KeyError, ValueError, IndexError,
            json.JSONDecodeError):
        return MISSLYCKAT


# --- rena funktioner: det som gar att prova utan nat ----------------------------------

def vardar_for(sajt):
    """Vardnamnen en sajt mats pa, utan www-dubletter."""
    sedda, ut = set(), []
    for v in sajt.get("vardar") or []:
        rent = re.sub(r"^www\.", "", v.strip().lower())
        if rent and rent not in sedda:
            sedda.add(rent)
            ut.append(rent)
    return ut


def per_dag(rader):
    """[[dag, visningar, personer], ...] -> [[dag, visningar], ...] i datumordning."""
    return [[str(r[0]), int(r[1])] for r in sorted(rader or [], key=lambda r: str(r[0]))]


def summa(rader, index=1):
    return sum(int(r[index] or 0) for r in rader or [])


def ar_testmarkning(text):
    """Ar markningen en provkorning? En testbetalning ar inte en sald poster.

    Matt 2026-10-05: Momento hade en betald session pa 5 kr vars eventCode var
    "vtestbetal1789637310" -- panelen hade visat den som 5 kr i intakt.
    """
    return bool(re.match(r"^(v?test|prova|provare|demo)", (text or "").strip(), re.I))


def belopp(sessioner):
    """Betalda sessioner -> (antal, ore). Bara de som faktiskt ar betalda rakans."""
    betalda = [s for s in sessioner or [] if s.get("payment_status") == "paid"]
    return len(betalda), sum(int(s.get("amount_total") or 0) for s in betalda)


def trafik(vardar, dagar=14, fraga=hogql):
    """Trafiken for en sajt: per dag, toppsidor, kallor och totalt."""
    if not vardar:
        return {"mats": False, "skal": "ingen PostHog-vard"}
    lista = "(" + ", ".join("'%s'" % v for v in vardar) + ")"
    sedan = (datetime.now(timezone.utc) - timedelta(days=dagar)).strftime("%Y-%m-%d %H:%M:%S")
    bas = ("FROM events WHERE properties.$host IN %s AND event = '$pageview'" % lista)
    ut = {"mats": True, "dagar": dagar}
    per = forsok(fraga, "SELECT toDate(timestamp) AS dag, count(), count(DISTINCT person_id) "
                        + bas + " AND timestamp > '" + sedan + "' GROUP BY dag ORDER BY dag")
    if per == MISSLYCKAT:
        return {"mats": True, "dagar": dagar, "fel": MISSLYCKAT}
    ut["perDag"] = per_dag(per)
    ut["visningar"] = summa(per)
    ut["personer"] = summa(per, 2)
    ut["toppSidor"] = forsok(fraga, "SELECT properties.$pathname, count() " + bas
                             + " GROUP BY 1 ORDER BY 2 DESC LIMIT 5") or []
    ut["kallor"] = forsok(fraga, "SELECT properties.$referring_domain, count() " + bas
                          + " GROUP BY 1 ORDER BY 2 DESC LIMIT 5") or []
    hela = forsok(fraga, "SELECT count(), count(DISTINCT person_id), min(timestamp), max(timestamp) "
                         + bas)
    if hela and hela != MISSLYCKAT:
        ut["totalt"] = {"visningar": int(hela[0][0]), "personer": int(hela[0][1]),
                        "forst": str(hela[0][2])[:10], "senast": str(hela[0][3])[:10]}
    return ut


def livstecken(url, hamta=None):
    """Svarar sajten? Tiden ar en del av svaret -- en sida som svarar pa 9 s ar trasig.

    En egen User-Agent kravs: Cloudflare svarar 403 pa python-urllibs standardsträng
    (matt 2026-10-05: alla tre publika sajter var "nere" utan den, 200 med den).
    """
    if hamta is None:
        def las(u):
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=12) as s:
                return s.status

        hamta = las

    start = datetime.now()
    try:
        kod = hamta(url)
        return {"uppe": True, "kod": int(kod or 0),
                "ms": int((datetime.now() - start).total_seconds() * 1000)}
    except urllib.error.HTTPError as e:
        return {"uppe": False, "kod": int(e.code), "ms": 0}
    except Exception:
        return {"uppe": False, "kod": 0, "ms": 0, "fel": MISSLYCKAT}


def tjanst_lage(enhet, kor=None):
    """systemctl --user is-active: active/inactive/failed, eller att det inte gick."""
    if not enhet:
        return None
    kor = kor or (lambda u: subprocess.run(["systemctl", "--user", "is-active", u],
                                           capture_output=True, text=True, timeout=15).stdout.strip())
    try:
        return kor(enhet) or "okand"
    except Exception:
        return MISSLYCKAT


def posthog_installerad(url, hamta=None):
    """Ligger PostHog-snutten pa sajten? Matt ur den serverade HTML:en, inte ur koden."""
    if hamta is None:
        def las(u):
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15) as s:
                return s.read(400_000).decode("utf-8", "replace")

        hamta = las
    try:
        return "phc_" in (hamta(url) or "")
    except Exception:
        return MISSLYCKAT


def salj(marke, dagar=14, hamta=stripe):
    """Kassan for en sajt: sessioner skapade i fonstret, och allt som nagonsin betalats.

    Bara sessioner med sajtens egen markning i metadata rakans. Stripe-kontot delas med
    Alex andra projekt, sa utan markningen hade en annan butiks testbetalning last som en
    sald poster (matt 2026-10-05: 5 kr fran Momento såg ut som en Minnoriasalj).
    """
    if not marke:
        return {"kopplad": False, "skal": "kassan ar inte kopplad till panelen"}
    nu = int(datetime.now(timezone.utc).timestamp())

    def dela(svar):
        """Sessioner med sajtens markning -> (riktiga, antal provkorningar)."""
        alla = [s for s in (svar or {}).get("data", [])
                if (s.get("metadata") or {}).get(marke)]
        riktiga = [s for s in alla if not ar_testmarkning(s["metadata"][marke])]
        return riktiga, len(alla) - len(riktiga)

    d = forsok(hamta, "checkout/sessions?limit=100&created[gte]=%d" % (nu - dagar * 86400))
    if d == MISSLYCKAT or d is None:
        return {"kopplad": True, "fel": MISSLYCKAT}
    vara, prov = dela(d)
    n, ore = belopp(vara)
    allt = forsok(hamta, "checkout/sessions?limit=100")
    if allt == MISSLYCKAT or allt is None:
        return {"kopplad": True, "dagar": dagar, "antal": n, "kr": ore / 100.0, "prov": prov,
                "fel": MISSLYCKAT}
    allt_vara, prov_allt = dela(allt)
    n_allt, ore_allt = belopp(allt_vara)
    return {"kopplad": True, "marke": marke, "dagar": dagar, "antal": n, "kr": ore / 100.0,
            "antalTotalt": n_allt, "krTotalt": ore_allt / 100.0, "prov": prov,
            "provTotalt": prov_allt,
            # ponytail: taket pa 100 sessioner per uppslag. Sidledes bladdring nar en sajt
            # passerar det -- da ar siffran annars tyst kapad.
            "tak": 100}


def sajt_rapport(sajt, dagar=14):
    vardar = vardar_for(sajt)
    return {"nyckel": sajt["nyckel"], "namn": sajt["namn"], "url": sajt["url"],
            "liv": livstecken(sajt["url"]),
            "tjanst": {"enhet": sajt.get("tjanst"), "lage": tjanst_lage(sajt.get("tjanst"))},
            "posthog": posthog_installerad(sajt["url"]),
            "trafik": trafik(vardar, dagar),
            "salj": salj(sajt.get("salj"), dagar)}


def rapport(dagar=14, dagens=None):
    return {"genererad": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
            "dagar": dagar,
            "sajter": [(dagens(s) if dagens else sajt_rapport(s)) for s in SAJTER],
            "tjanster": [dict(t, lage=tjanst_lage(t["enhet"])) for t in TJANSTER]}


def skriv_ut(r):
    for s in r["sajter"]:
        liv = s["liv"]
        print("%-9s %s  %s" % (s["namn"], "UPPE" if liv.get("uppe") else "NERE",
                               ("%d ms" % liv["ms"]) if liv.get("uppe")
                               else ("HTTP %d" % liv["kod"] if liv.get("kod") else liv.get("fel", ""))))
        t = s["trafik"]
        if t.get("mats") and not t.get("fel"):
            print("          %d sidvisningar, %d personer (%d dygn)" % (t["visningar"], t["personer"], t["dagar"]))
        else:
            print("          trafik: %s" % (t.get("fel") or t.get("skal")))
        k = s["salj"]
        if k.get("krTotalt") is not None:
            print("          kassa: %.2f kr (%d st)%s" % (
                k["krTotalt"], k["antalTotalt"],
                " + %d provkorning utesluten" % k["provTotalt"] if k.get("provTotalt") else ""))
        else:
            print("          kassa: %s" % (k.get("skal") or k.get("fel")))
        print("          posthog: %s | tjanst: %s" % (s["posthog"], s["tjanst"]["lage"]))
    for t in r["tjanster"]:
        print("%-9s %s" % (t["namn"], t["lage"]))


def selftest():
    # Vardnamnen: www och utan www ar samma sajt, dubbletter faller bort
    assert vardar_for({"vardar": ["www.Alibit.se", "alibit.se"]}) == ["alibit.se"]
    assert vardar_for({"vardar": []}) == []

    # Per dag: sorterat i datumordning hur raderna an kommer
    assert per_dag([["2026-10-05", 3, 2], ["2026-10-04", 7, 4]]) == [["2026-10-04", 7], ["2026-10-05", 3]]
    assert summa([["2026-10-05", 3, 2], ["2026-10-04", 7, 4]]) == 10
    assert summa([["x", 3, 9]], 2) == 9

    # Belopp: bara betalda rakans, och ore summeras ratt
    assert belopp([{"payment_status": "paid", "amount_total": 34900},
                   {"payment_status": "unpaid", "amount_total": 999}]) == (1, 34900)
    assert belopp([]) == (0, 0)

    # En provbetalning ar inte en sald poster (matt: Momentos 5-kronassession hette vtestbetal…)
    assert ar_testmarkning("vtestbetal1789637310") is True
    assert ar_testmarkning("provare-1") is True
    assert ar_testmarkning("20261005T075624-5c64d4") is False
    assert ar_testmarkning("") is False

    # Tre tillstand: ett uppslag som kastar blir ordet, inte en nolla
    def kastar(*a, **k):
        raise urllib.error.URLError("natet borta")

    assert forsok(kastar) == MISSLYCKAT
    assert forsok(lambda: None) == MISSLYCKAT
    assert forsok(lambda: 7) == 7

    # Trafiken: en trasig fraga far inte se ut som noll besok
    t = trafik(["minnoria.se"], fraga=kastar)
    assert t.get("fel") == MISSLYCKAT, t
    assert "visningar" not in t
    # Och en sajt utan vardar sager det i stallet for att visa noll
    assert trafik([])["mats"] is False

    # Kassa utan markning visas som okopplad, aldrig som 0 kr
    assert salj(None)["kopplad"] is False
    s = salj("order_id", hamta=kastar)
    assert s.get("fel") == MISSLYCKAT, s

    # Livstecknet: en död sajt ar nere, en som svarar ar uppe med sin tid
    def svarar(u):
        return 200

    assert livstecken("http://x", hamta=svarar)["uppe"] is True
    assert livstecken("http://x", hamta=kastar)["uppe"] is False

    # Tjansten: aktiv/inaktiv rakt igenom, och ett trasigt systemctl blir ett ord
    assert tjanst_lage("x.service", kor=lambda u: "active") == "active"
    assert tjanst_lage(None) is None
    assert tjanst_lage("x.service", kor=kastar) == MISSLYCKAT

    # PostHog: snutten hittas i HTML:en, och ett natfel blir inte "saknas"
    assert posthog_installerad("http://x", hamta=lambda u: '<script>phc_abc</script>') is True
    assert posthog_installerad("http://x", hamta=lambda u: "<html></html>") is False
    assert posthog_installerad("http://x", hamta=kastar) == MISSLYCKAT

    # PostHog-installningen: panelens egen fil raknas forst, och nyckeln foljer aldrig med ut
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        saknas = pathlib.Path(tmp) / "panel.json"
        _, _, _, kalla_tom = posthog_uppgifter(str(saknas))
        assert kalla_tom in (".env", "ingen"), kalla_tom

        saknas.write_text(json.dumps({"posthog": {"nyckel": "phx_prov", "projekt": "4711",
                                                 "vard": "us"}}))
        nyckel, projekt, bas, kalla = posthog_uppgifter(str(saknas))
        assert (nyckel, projekt, bas) == ("phx_prov", 4711, "https://us.posthog.com"), bas
        assert kalla == "panelens installning"

        # Skrap i rutan ar inte en krasch: vardet faller tillbaka pa standarden
        saknas.write_text(json.dumps({"posthog": {"nyckel": "x", "projekt": "inte-ett-tal",
                                                  "vard": "mars"}}))
        _, projekt, bas, _ = posthog_uppgifter(str(saknas))
        assert (projekt, bas) == (POSTHOG_PROJEKT, "https://eu.posthog.com"), (projekt, bas)

        # Laget: nyckeln star inte i svaret, och ett svar fran tjansten ar inte ett natfel
        ut = posthog_lage(str(saknas), mata=lambda b, p, n: ("Min nuna", 12))
        assert "phx_prov" not in json.dumps(ut), ut
        assert ut["namn"] == "Min nuna" and ut["handelser"] == 12 and "fel" not in ut

        # En nyckel som avvisas blir tjanstens egen mening, inte en nolla
        def avvisar(b, p, n):
            raise urllib.error.HTTPError("u", 401, "Unauthorized", None, None)

        ut = posthog_lage(str(saknas), mata=avvisar)
        assert "HTTP 401" in ut.get("fel", ""), ut
        assert "handelser" not in ut, ut

    # Utan nyckel: sagt rakt ut, aldrig som noll handelser
    tom = pathlib.Path(tempfile.gettempdir()) / "godjira-ingen-nyckel.json"
    tom.unlink(missing_ok=True)
    utan = posthog_lage(str(tom), env=str(tom) + ".env")
    assert utan["satt"] is False and utan["fel"] == "ingen nyckel", utan

    print("selftest: OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--json" in sys.argv:
        print(json.dumps(rapport(), ensure_ascii=False))
    elif "--posthog" in sys.argv:
        print(json.dumps(posthog_lage(), ensure_ascii=False))
    else:
        skriv_ut(rapport())
