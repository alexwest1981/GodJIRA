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
import time
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
     "salj": "username", "kassa": "Stripe (eget konto)",
     "kassa_fil": "~/.config/alibit/billing.env",
     "kassa_namn": "REDTHREAD_BILLING_STRIPE_SECRET_KEY"},
    {"nyckel": "higgies", "namn": "Higgies", "url": "http://127.0.0.1:3000/",
     "vardar": ["127.0.0.1:3000", "localhost:3000"], "tjanst": "higgies.service", "salj": None, "kassa": None},
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

# Jobb som skall ha kört av sig själva: backningar och gallringar. Att de tystnar är den
# dyraste sorten fel -- 2026-10-05 dog nattspeglingen efter 25 sekunder och det syntes
# ingenstans förrän hubben fick en rad för det. max_hours är hur gammal den senaste
# LYCKADE körningen får vara innan den räknas som tyst.
VAKTER = [
    {"enhet": "spara-allt.service", "namn": "Backningen", "max_hours": 30},
    {"enhet": "momento-backup.service", "namn": "Momentos backning", "max_hours": 30},
    {"enhet": "momento-gallring.service", "namn": "Momentos gallring", "max_hours": 30},
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


def kassa_nyckel(sajt):
    """Sajtens egen Stripe-nyckel om den har en, annars den gemensamma.

    Alibit saljer i ett eget konto ("Alibit Mystery"): den gemensamma nyckeln ger 404 pa
    dess priser och noll betalningslankar, sa en delad nyckel hade last sajten som tom.
    """
    fil = sajt.get("kassa_fil")
    if fil:
        n = ur_fil(pathlib.Path(fil).expanduser(), sajt.get("kassa_namn") or "STRIPE_SECRET_KEY")
        if n:
            return n
    return stripe_nyckel()


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


def stripe(vag, nyckel=None):
    nyckel = nyckel or stripe_nyckel()
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
    # Sessioner och riktningen: en siffra säger hur mycket, förra perioden säger åt
    # vilket håll. Båda får kosta en fråga var -- de är hela skillnaden mellan en
    # rapport och en mätning.
    sessioner = forsok(fraga, "SELECT count(DISTINCT properties.$session_id) " + bas
                       + " AND timestamp > '" + sedan + "'")
    if sessioner and sessioner != MISSLYCKAT:
        ut["sessioner"] = int(sessioner[0][0])
    sedan_28 = (datetime.now(timezone.utc) - timedelta(days=dagar * 2)).strftime("%Y-%m-%d %H:%M:%S")
    forra = forsok(fraga, "SELECT count() " + bas + " AND timestamp > '" + sedan_28
                   + "' AND timestamp <= '" + sedan + "'")
    if forra and forra != MISSLYCKAT:
        ut["forra"] = int(forra[0][0])
    # Vad folk GJORDE, hur länge de stannade, och om något gick sönder. En siffra säger hur
    # många som kom; de här säger vad som hände när de var där. Fyra frågor mot samma
    # underlag, och varje svar läggs bara in om det kom -- saknas PostHog står fältet tomt
    # i stället för att visa en nolla som ser ut som ett svar.
    vards = "properties.$host IN " + lista
    handelser = forsok(fraga, ("SELECT event, count() FROM events WHERE " + vards
                               + " AND timestamp > '" + sedan + "' GROUP BY 1 ORDER BY 2 DESC LIMIT 6"))
    if handelser and handelser != MISSLYCKAT:
        ut["handelser"] = [[str(r[0]), int(r[1])] for r in handelser]
    enheter = forsok(fraga, ("SELECT properties.$device_type, count(DISTINCT person_id) FROM events WHERE "
                             + vards + " AND timestamp > '" + sedan + "' GROUP BY 1 ORDER BY 2 DESC LIMIT 4"))
    if enheter and enheter != MISSLYCKAT:
        ut["enheter"] = [[str(r[0] or "okänt"), int(r[1])] for r in enheter]
    # Sessionstid: bara sessioner med mer än en händelse -- en enda sidvisning är ingen tid.
    sekunder = forsok(fraga, ("SELECT round(avg(sek)) FROM (SELECT dateDiff('second', min(timestamp), "
                              "max(timestamp)) AS sek FROM events WHERE " + vards
                              + " AND timestamp > '" + sedan + "' GROUP BY properties.$session_id "
                              "HAVING count() > 1)"))
    if sekunder and sekunder != MISSLYCKAT and sekunder[0][0] is not None:
        ut["sessionSekunder"] = int(sekunder[0][0])
    # Fel: PostHogs egen undantagshändelse. Den är en av de få siffror som betyder något
    # även när den är noll, så den visas alltid -- men bara när frågan gick igenom.
    undantag = forsok(fraga, ("SELECT count() FROM events WHERE " + vards
                              + " AND event = '$exception' AND timestamp > '" + sedan + "'"))
    if undantag and undantag != MISSLYCKAT:
        ut["undantag"] = int(undantag[0][0])
    # Just nu: personer inne de senaste fem minuterna.
    live = forsok(fraga, ("SELECT count(DISTINCT person_id) FROM events WHERE " + vards
                          + " AND timestamp > now() - INTERVAL 5 MINUTE"))
    if live and live != MISSLYCKAT:
        ut["live"] = int(live[0][0])
    # Var de är: samma fråga ger både landet (för listan) och punkten (för kartan). PostHogs
    # geo-databas är inte komplett -- en rad utan land eller koordinat hoppas över i stället
    # för att ritas som en gissning i havet.
    geo = forsok(fraga, ("SELECT properties.$geoip_country_name, properties.$geoip_country_code, "
                         "properties.$geoip_latitude, properties.$geoip_longitude, "
                         "count(DISTINCT person_id) FROM events WHERE " + vards
                         + " AND timestamp > '" + sedan + "' GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 40"))
    if geo and geo != MISSLYCKAT:
        länder, punkter = {}, []
        for namn, kod, lat, lon, antal in geo:
            if not namn or lat is None or lon is None:
                continue
            länder[str(namn)] = länder.get(str(namn), 0) + int(antal)
            punkter.append([round(float(lat), 2), round(float(lon), 2), int(antal), str(kod or "")])
        ut["lander"] = sorted(länder.items(), key=lambda rad: -rad[1])[:8]
        ut["punkter"] = punkter
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


def vakt_lage(enhet: str, kor=None, nu=None) -> dict:
    """Senaste körningen av ett jobb som skall ha kört: resultat, när, hur länge sedan.

    Epoch ur systemd (`--timestamp=unix`), inte text: en textstämpel beror på språket och
    den här maskinen svarar på svenska ena dagen och engelska nästa. Tom tidsstämpel betyder
    att enheten aldrig har kört -- `Result` är då "success" ändå, så resultatet ensamt duger
    inte som svar på om jobbet har gjort sitt. `lage` (ActiveState) behövs för att en körning
    som PÅGÅR nollställer tidsstämpeln: den får inte läsas som "har aldrig kört".
    """
    def standard(u):
        return subprocess.run(["systemctl", "--user", "show", "--timestamp=unix",
                               "-p", "Result", "-p", "ExecMainExitTimestamp", "-p", "ActiveState",
                               u], capture_output=True, text=True, timeout=15)

    kor = kor or standard
    try:
        # Nyckel=Värde, inte radposition: systemd lämnar egenskaperna i sin egen ordning --
        # med --value hamnade epoken i "result" och "active" i "at" när tre egenskaper frågades.
        fält = {}
        for rad in (kor(enhet).stdout or "").splitlines():
            nyckel, _, värde = rad.partition("=")
            if värde or nyckel.endswith("Timestamp"):
                fält[nyckel.strip()] = värde.strip()
    except Exception:
        return {"enhet": enhet, "lage": "okand", "result": "", "at": None, "age_h": None}
    result = fält.get("Result", "")
    stamp = fält.get("ExecMainExitTimestamp", "")
    lage = fält.get("ActiveState", "")
    at = None
    if stamp.startswith("@"):
        try:
            at = int(stamp[1:])
        except ValueError:
            at = None
    nu = int(time.time()) if nu is None else nu
    return {"enhet": enhet, "lage": lage, "result": result, "at": at,
            "age_h": round((nu - at) / 3600.0, 1) if at else None}


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


# --------------------------------------------------------------- vad som KÖRS
#
# Sajtvyn visste att tjänsten svarade och att sajten svarade -- men inte VILKEN kod som
# svarade. Det är skillnaden mellan "uppe" och "ute": en tjänst kan vara aktiv och köra en
# commit från i förrgår medan grenen står tre commits längre fram.
#
# Allt läses med LÄSANDE kommandon i någon annans arbetsträd. `--no-optional-locks` gör att
# ett uppslag aldrig skriver i deras index, och en katalog utan git-historik svarar tomt i
# stället för att kasta.
BRANCH_LINE = re.compile(r"^## (?P<branch>[^\s.]+)(?:\.\.\.(?P<upstream>[^\s\[]+))?"
                         r"(?: \[(?P<flags>[^\]]+)\])?(?:\s+\(.*\))?\s*$")


def parse_status(line: str) -> dict:
    """`## main...origin/main [ahead 1, behind 2]` -> gren, uppströms, före, efter.

    Ren funktion: hela tolkningen går att prova utan ett repo. Utan uppströms är före/efter
    okända (None) -- inte noll, för noll betyder "i takt" och det vet vi inte."""
    match = BRANCH_LINE.match(line or "")
    if not match:
        return {"branch": "", "upstream": "", "ahead": None, "behind": None}
    upstream = match.group("upstream") or ""
    flags = match.group("flags") or ""
    ahead = behind = None
    if upstream:
        av = re.search(r"ahead (\d+)", flags)
        bv = re.search(r"behind (\d+)", flags)
        ahead = int(av.group(1)) if av else 0
        behind = int(bv.group(1)) if bv else 0
    return {"branch": match.group("branch"), "upstream": upstream, "ahead": ahead, "behind": behind}


def git_las(katalog: str, args: list, kor=None) -> str:
    """Ett läsande git-svar ur katalogen, eller tom sträng om det inte går."""
    def standard(extra):
        return subprocess.run(["git", "--no-optional-locks", "-C", katalog] + list(extra),
                              capture_output=True, text=True, timeout=30)
    kor = kor or standard
    try:
        svar = kor(args)
        return (svar.stdout or "").strip() if svar.returncode == 0 else ""
    except Exception:
        return ""


def tjanst_katalog(enhet: str, kor=None) -> str:
    """Tjänstens arbetskatalog -- ur systemd, inte ur en gissning."""
    if not enhet:
        return ""
    def standard(u):
        return subprocess.run(["systemctl", "--user", "show", "-p", "WorkingDirectory",
                               "--value", u], capture_output=True, text=True, timeout=15)
    kor = kor or standard
    try:
        svar = kor(enhet)
        vantad = (svar.stdout or "").strip()
        return "" if vantad in ("", "-") else vantad
    except Exception:
        return ""


def drift_of(katalog: str, kor=None) -> dict:
    """Vad som körs i katalogen: commit, när, ämne, gren, före/efter uppströms, ostädat.

    Två kommandon räcker: `git log -1` ger commit + datum + ämne, och `git status -b`
    ger grenen, avståndet till uppströms OCH antalet ostädade filer i samma svar."""
    if not katalog:
        return {"ok": False, "note": "ingen katalog"}
    log = git_las(katalog, ["log", "-1", "--format=%h%x1f%cI%x1f%s"], kor)
    if not log:
        return {"ok": False, "note": "ingen git-historik i katalogen"}
    delar = log.split("\x1f")
    status = git_las(katalog, ["status", "--porcelain", "-b"], kor)
    rader = status.splitlines()
    gren = parse_status(rader[0] if rader else "")
    return dict(gren, ok=True, katalog=katalog,
                commit=delar[0] if delar else "",
                at=delar[1] if len(delar) > 1 else "",
                subject=delar[2] if len(delar) > 2 else "",
                dirty=len([r for r in rader[1:] if r.strip()]))


def drift_rapport() -> dict:
    """Bara det lätta: ingen sajt hämtas, ingen trafik, ingen kassa -- bara vad som körs.

    Panelen frågar den här vägen i /api/state (en gång i minuten), medan hela sajtvyn
    (trafik, kassa, livstecken) hämtas först när man öppnar Sajter."""
    ut = []
    for sajt in SAJTER:
        enhet = sajt.get("tjanst")
        ut.append({"nyckel": sajt["nyckel"], "namn": sajt["namn"], "enhet": enhet,
                   "lage": tjanst_lage(enhet),
                   "drift": drift_of(tjanst_katalog(enhet))})
    return {"genererad": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
            "sajter": ut,
            "vakter": [dict(v, **vakt_lage(v["enhet"])) for v in VAKTER]}


def sajt_rapport(sajt, dagar=14):
    vardar = vardar_for(sajt)
    return {"nyckel": sajt["nyckel"], "namn": sajt["namn"], "url": sajt["url"],
            "liv": livstecken(sajt["url"]),
            "tjanst": {"enhet": sajt.get("tjanst"), "lage": tjanst_lage(sajt.get("tjanst"))},
            # Vilken kod som svarar, inte bara att någon svarar.
            "drift": drift_of(tjanst_katalog(sajt.get("tjanst"))),
            # Vilket konto pengarna kommer ifran: Alibit saljer i ett eget, och en kolumn
            # som blandar tva konton utan att saga det ar en fellasning som vantar.
            "kassa": sajt.get("kassa"),
            "posthog": posthog_installerad(sajt["url"]),
            "trafik": trafik(vardar, dagar),
            # Kassan fragas med sajtens egen nyckel: Alibits konto ar inte Minnoria. 
            "salj": salj(sajt.get("salj"), dagar,
                         hamta=lambda vag: stripe(vag, kassa_nyckel(sajt)))}


def rapport(dagar=14, dagens=None):
    return {"genererad": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
            "dagar": dagar,
            # Perioden skall hela vägen fram: det stod "dagar": dagar i svaret medan
            # sajterna alltid mättes med fjorton. En siffra i svaret som inte gäller är
            # värre än ingen siffra.
            "sajter": [(dagens(s, dagar) if dagens else sajt_rapport(s, dagar)) for s in SAJTER],
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
        d = s.get("drift") or {}
        if d.get("ok"):
            print("          kor: %s %s%s" % (
                d["commit"], (d.get("subject") or "")[:60],
                " · %d efter %s" % (d["behind"], d.get("upstream") or "uppströms")
                if d.get("behind") else (" · i takt" if d.get("upstream") else " · ingen uppströms")))
        else:
            print("          kor: %s" % (d.get("note") or "okänt"))
    for t in r["tjanster"]:
        print("%-9s %s" % (t["namn"], t["lage"]))


def selftest():
    import tempfile

    # Vad som KÖRS: grenraden ur git tolkas rent, och utan uppströms är avståndet OKÄNT
    # (None) -- inte noll, för noll betyder "i takt" och det vet vi inte.
    assert parse_status("## main...origin/main [ahead 1, behind 2]") == {
        "branch": "main", "upstream": "origin/main", "ahead": 1, "behind": 2}
    assert parse_status("## main...origin/main")["behind"] == 0
    assert parse_status("## main...origin/main")["ahead"] == 0
    assert parse_status("## release")["behind"] is None
    assert parse_status("## HEAD (no branch)")["branch"] == "HEAD"
    assert parse_status("")["branch"] == "" and parse_status("skrap")["branch"] == ""

    # drift_of: två kommandon, injicerade -- tolkningen provas utan en maskin med git
    def git_svar(args):
        if args[:2] == ["log", "-1"]:
            return subprocess.CompletedProcess(args, 0,
                                               "abc1234\x1f2026-10-05T09:00:00+02:00\x1fByt färg")
        return subprocess.CompletedProcess(args, 0, "## main...origin/main [behind 3]\n M karta.py\n")

    d = drift_of("/var/www/minnoria", kor=git_svar)
    assert (d["commit"], d["behind"], d["dirty"]) == ("abc1234", 3, 1), d
    assert d["at"].startswith("2026-10-05") and d["subject"] == "Byt färg", d
    # Jobben som skall köra av sig själva: epoch tolkas, och en tom tidsstämpel betyder
    # ALDRIG KÖRT även om Result säger success -- plus att en körning som pågår inte får läsas
    # som en gammal körning.
    def vakt_svar(text):
        return lambda u: subprocess.CompletedProcess([], 0, text)

    nu = 1_800_000_000
    def vakt_rader(result="success", stamp="", lage="inactive"):
        return ("Result=%s\nExecMainExitTimestamp=%s\nActiveState=%s\n" % (result, stamp, lage))

    gjord = vakt_lage("x.service", kor=vakt_svar(vakt_rader(stamp="@1799998000")), nu=nu)
    assert (gjord["result"], gjord["age_h"]) == ("success", 0.6), gjord
    trasig = vakt_lage("x.service", kor=vakt_svar(vakt_rader(result="exit-code",
                                                           stamp="@1799908000")), nu=nu)
    assert trasig["result"] == "exit-code" and trasig["age_h"] > 24, trasig
    aldrig = vakt_lage("x.service", kor=vakt_svar(vakt_rader()), nu=nu)
    assert aldrig["at"] is None and aldrig["age_h"] is None, aldrig
    pagaende = vakt_lage("x.service", kor=vakt_svar(vakt_rader(lage="activating")), nu=nu)
    assert pagaende["lage"] == "activating", pagaende
    # Ordningen skall inte spela roll: epoken får inte hamna i "result"
    omkastad = vakt_lage("x.service", kor=vakt_svar(
        "ActiveState=inactive\nExecMainExitTimestamp=@1799998000\nResult=success\n"), nu=nu)
    assert omkastad["result"] == "success" and omkastad["age_h"] == 0.6, omkastad
    okand = vakt_lage("x.service", kor=vakt_svar(""), nu=nu)
    assert okand["result"] == "" and okand["age_h"] is None, okand

    assert drift_of("")["ok"] is False
    assert drift_of("/tmp", kor=lambda a: subprocess.CompletedProcess(a, 128, ""))["ok"] is False

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

    # Kassan: en sajt med eget konto laser sin egen nyckelfil, inte den gemensamma
    with tempfile.TemporaryDirectory() as tmp:
        fil = pathlib.Path(tmp) / "billing.env"
        namn, varde = "REDTHREAD_BILLING_STRIPE_SECRET_KEY", "prov-" + "nyckel"
        fil.write_text("%s=%s\n" % (namn, varde))
        assert kassa_nyckel({"kassa_fil": str(fil), "kassa_namn": namn}) == varde
        assert kassa_nyckel({"kassa_fil": str(fil), "kassa_namn": "FINNS_INTE"}) == stripe_nyckel()
        assert kassa_nyckel({}) == stripe_nyckel(), "utan egen fil skall den gemensamma anvandas"
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
    # Sessioner och riktningen: frågorna ställs och svaren hamnar på rätt plats. En
    # injicerad fråga gör provet oberoende av nätet (och av att sajten har besök).
    def låtsas(sql):
        if "avg(sek)" in sql:
            return [[83]]
        if "session_id" in sql:
            return [[7]]
        if "$exception" in sql:
            return [[2]]
        if "INTERVAL 5 MINUTE" in sql:
            return [[3]]
        if "$device_type" in sql:
            return [["Mobile", 5], ["Desktop", 3]]
        if "$geoip_country_name" in sql:
            return [["Sweden", "SE", 57.7, 11.97, 5], ["United States", "US", 41.26, -95.85, 4],
                    ["Nowhere", "XX", None, None, 2]]      # utan koordinat: hoppas över
        if "SELECT event" in sql:
            return [["$pageview", 9], ["checkout_started", 2]]
        if "count()" in sql and "timestamp <=" in sql:
            return [[10]]
        if "$pathname" in sql:
            return [["/", 5]]
        if "$referring_domain" in sql:
            return [["google", 3]]
        if "min(timestamp)" in sql:
            return [[12, 9, "2026-09-01 00:00:00", "2026-10-01 00:00:00"]]
        return [["2026-10-01", 4, 3]]
    t = trafik(["x.se"], 14, fraga=låtsas)
    assert t["sessioner"] == 7, t
    assert t["forra"] == 10, t
    assert t["visningar"] == 4 and t["personer"] == 3, t
    # Perioden fick inte vara dekorativ: rapport(7) skall ge sajter som MATS med sju.
    fångade = []
    def fånga(sajt, dagar=14):
        fångade.append(dagar)
        return {"nyckel": sajt["nyckel"], "trafik": {"dagar": dagar}}
    hel = rapport(7, dagens=fånga)
    assert hel["dagar"] == 7 and fångade == [7] * len(SAJTER), (hel["dagar"], fångade)

    assert t["toppSidor"] == [["/", 5]] and t["kallor"] == [["google", 3]], t
    assert t["handelser"] == [["$pageview", 9], ["checkout_started", 2]], t
    assert t["enheter"] == [["Mobile", 5], ["Desktop", 3]], t
    assert t["sessionSekunder"] == 83, t
    assert t["undantag"] == 2 and t["live"] == 3, t
    assert t["lander"] == [("Sweden", 5), ("United States", 4)], t["lander"]
    assert t["punkter"] == [[57.7, 11.97, 5, "SE"], [41.26, -95.85, 4, "US"]], t["punkter"]

    # Uteblir svaret skall fältet vara BORTA, inte noll: en nolla ser ut som ett svar.
    def halvt(sql):
        return [["2026-10-01", 4, 3]] if "toDate" in sql else MISSLYCKAT
    t2 = trafik(["x.se"], 14, fraga=halvt)
    assert t2.get("mats") and t2.get("visningar") == 4, t2
    for nyckel in ("handelser", "enheter", "sessionSekunder", "undantag", "live"):
        assert nyckel not in t2, (nyckel, t2)
    # Utan fråga alls: saknas underlaget står fälten inte kvar som nollor.
    tom = trafik([], 14, fraga=låtsas)
    assert tom["mats"] is False and "sessioner" not in tom, tom

    assert posthog_installerad("http://x", hamta=lambda u: '<script>phc_abc</script>') is True
    assert posthog_installerad("http://x", hamta=lambda u: "<html></html>") is False
    assert posthog_installerad("http://x", hamta=kastar) == MISSLYCKAT

    # PostHog-installningen: panelens egen fil raknas forst, och nyckeln foljer aldrig med ut
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


def dagar_fran_argv(standard=14):
    """--dagar N ur kommandoraden. Golv 1, tak 365: en period är ett antal dygn, och ett
    svar på -3 eller 100000 är inte en period utan ett skrivfel."""
    if "--dagar" in sys.argv:
        i = sys.argv.index("--dagar")
        if i + 1 < len(sys.argv):
            try:
                return max(1, min(365, int(sys.argv[i + 1])))
            except ValueError:
                pass
    return standard


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--drift" in sys.argv:
        # Före --json: den lätta vägen (bara vad som körs) skall inte drunkna i den tunga.
        print(json.dumps(drift_rapport(), ensure_ascii=False))
    elif "--json" in sys.argv:
        print(json.dumps(rapport(dagar_fran_argv()), ensure_ascii=False))
    elif "--posthog" in sys.argv:
        print(json.dumps(posthog_lage(), ensure_ascii=False))
    else:
        skriv_ut(rapport(dagar_fran_argv()))
