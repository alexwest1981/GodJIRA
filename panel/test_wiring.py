#!/usr/bin/env python3
"""The wiring that makes the Repos view work, and the one that keeps the shell
from waiting ten seconds before it has anything to click on.

Run it with the panel up:  python3 panel/test_wiring.py

It fails on exactly the things that made the view feel dead:
  * a repo row that opens github instead of the app -> the repo cannot be
                                                       pointed at a Jira project
  * the repo list back in the main area      -> 50 m of scroll before the info
  * the drawing without drag/pan/zoom        -> the flow cannot be read
  * long node names cut off                  -> the step names become guesses
  * the repo detail losing its github link   -> the way out disappears
  * a flow file that is not drawn            -> the flow is invisible
  * drawn nodes/edges not matching the file  -> the drawing lies by omission
  * the view drawing its own copy of a flow  -> it goes stale on the next edit
  * the edit button lost when the flow map replaced the canvas -> the flow can no
                                               longer be edited from this view
  * the repo row/commit link wiring gone    -> rows render as plain text
  * the state fetch without a time limit    -> an unanswered request leaves the
                                               shell empty forever
  * a failed refresh with no retry          -> the page keeps stale data and
                                               says nothing (splashFail is a
                                               no-op once the splash is gone)
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.request

PANEL = "http://127.0.0.1:8788"
HERE = pathlib.Path(__file__).resolve().parent
failed: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + what + ((" -- " + detail) if detail else ""))
    if not ok:
        failed.append(what)


def main() -> int:
    try:
        with urllib.request.urlopen(PANEL + "/api/state", timeout=120) as answer:
            state = json.load(answer)
    except Exception as exc:  # noqa: BLE001 -- the message is the point
        print("the panel did not answer at %s: %s" % (PANEL, exc))
        return 1

    repos = ((state.get("github") or {}).get("repos")) or []
    check(bool(repos), "the state carries repos", "%d repos" % len(repos))
    check(all((r or {}).get("url", "").startswith("https://github.com/") for r in repos[:10]),
          "every repo carries its github url", "the detail needs it for its link out")
    check(any((r or {}).get("url") for r in repos), "at least one repo has a url")

    html = (HERE / "index.html").read_text()
    # Ett reponamn i tabellen skall öppna repot I APPEN (raden är klickbar och
    # öppnar detaljen), inte hoppa till webben. GitHub-länken hör till detaljen.
    check("data-repo=" in html and "repoOpen(" in html,
          "the repo row opens the repo inside the app")
    # Backlog-raden hade samma data-v som tavlan och öppnade därför tavlan.
    check('data-v="items" data-pool="backlog"' in html and "function viewRows()" in html,
          "the Backlog row shows the backlog, not the board")
    check('if (repo) { show("repos"); return repoOpen(repo.dataset.repo); }' in html,
          "the row also switches the main window to the repo")
    # Listan bor i sidofältet, för 59 lines i huvudrutan blev lång skroll innan
    # detaljen. Sökningen filtrerar raderna på plats (ingen omritning = inget tappat
    # fokus), och huvudrutan ritar bara det valda repot.
    check("id=\"repoSearch\"" in html and "function repoRows()" in html,
          "the repo list and its search live in the sidebar")
    check("repoTable" not in html, "the long repo table is gone from the main area")
    check("function markRepo()" in html, "the sidebar marks the open repo")
    # [hidden] räckte inte mot display:flex: regeln sattes men raden visades ändå.
    check("[hidden] { display: none !important; }" in html,
          "the hidden attribute actually hides the filter row")
    # Tillåtlista, inte blocklista: settings/setup/admin visade filtren när listan
    # byggdes som "allt utom dessa". En ny vy får aldrig ärva en rad den inte använder.
    check('!["board", "items"].includes(view)' in html,
          "the filter row shows on the board and the item list, nowhere else")
    check('id="filtersRow" hidden' not in html and "filtersRow" in html,
          "the filter row is one block that can be hidden whole")
    # Ett oavslutat skriptblock tystar hela panelen (mätt: "show is not defined").
    # Starten bor sist i filen. En patch som "äter till slutet" tar med sig
    # load(), pollningen och hashändringen -- då står panelen tom utan ett ord
    # (mätt: STATE förblev null i webbläsaren, inga bad i konsolen).
    check("load(false);" in html and "setInterval(() => load(false), 60000);" in html,
          "the panel starts itself and keeps reading every minute")
    check("show(readHash());" in html and 'addEventListener("hashchange"' in html,
          "the hash opens a view and follows along")
    check(html.count("<script") == html.count("</script>"),
          "every script block is closed",
          "%s öppnade, %s stängda" % (html.count("<script"), html.count("</script>")))
    # Reporaden skall inte hoppa till github -- detaljen äger den länken. Provet tittar
    # i repoRows() själv: som textsökning i hela filen föll det på varje ny länk
    # någon annanstans (tidslinjen länkar sina ärenden).
    repo_fn = html[html.index("function repoRows()"):]
    repo_fn = repo_fn[:repo_fn.index("\n}") + 2]
    check("link(" not in repo_fn,
          "the repo name is not a jump to github", repo_fn[:160])
    check("link(d.about.url," in html, "the repo detail carries the github link")
    check("/commit/" in html and "a.url" in html, "commits are rendered as links")

    # Flödesritningen: varje fil i n8n/workflows ritas, och ritningen stämmer med
    # filens egen rubrik. En ritning som tyst tappar en nod är värre än ingen.
    flows = state.get("flows") or []
    files = sorted(p.name for p in (HERE.parent / "n8n" / "workflows").glob("*.workflow.ts"))
    check(len(flows) == len(files), "every workflow file is in the state",
          "%d av %d: %s" % (len(flows), len(files), ", ".join(files)))
    for path in sorted((HERE.parent / "n8n" / "workflows").glob("*.workflow.ts")):
        text = path.read_text()
        head = re.search(r"// Nodes\s*:\s*(\d+)\s*\|\s*Connections:\s*(\d+)", text)
        drawn = next((f for f in flows if f.get("file") == path.name), None)
        if not head or not drawn:
            check(False, "the workflow map is readable: " + path.name)
            continue
        want_nodes, want_edges = int(head.group(1)), int(head.group(2))
        check(len(drawn.get("nodes") or []) == want_nodes and len(drawn.get("edges") or []) == want_edges,
              "drawn %s matches the file" % path.name,
              "%d/%d noder, %d/%d kopplingar"
              % (len(drawn.get("nodes") or []), want_nodes, len(drawn.get("edges") or []), want_edges))
        # Kanterna pekar på nodernas EGENNAMN (EveryHour), inte på visningsnamnet
        # ("Every hour"): utan nyckeln ritas inga vägar alls (mätt: 0 av 7).
        keys = {n.get("key") for n in (drawn.get("nodes") or [])}
        check(all(e.get("from") in keys and e.get("to") in keys for e in (drawn.get("edges") or [])),
              "every edge lands on a node: " + path.name)
    check("STATE.flows" in html and "flowSel" in html, "the view draws the state's own graph")
    check("EveryHour" not in html, "no flow is drawn by hand in the page")
    # Flödet ritades förr i en egen vy ("Automatik", en egen räl-post). Den låg sida vid
    # sida med kodkartan, samma ruta två gånger, så den bor nu som en källa i Karta.
    check("renderFlow" in html and 'id="flowCanvas"' in html and 'id="pane-repos-automatik"' in html,
          "the flow is drawn in its own Automatik tab")
    check('id="kartaChips"' not in html and 'id="pane-repos-kartan"' in html,
          "the map and the flow are two tabs, not a switch inside the map")
    check("v-automatik" not in html and 'automatik: ["Automatik"' not in html,
          "no separate Automatik view is left")
    # Ritningen skall gå att se och att flytta i: noderna bär data-node, vägarna
    # data-from/data-to (så de kan ritas om för hand vid ett drag), och verktygen
    # (dra, panorera, zooma, nollställ) finns.
    check("data-node=" in html and "class=\"fedge\"" in html and "flowEdgeD" in html,
          "the drawing can be redrawn piece by piece")
    check("pointerdown" in html and "pointermove" in html and "wheel" in html,
          "the drawing can be dragged, panned and zoomed")
    check('data-act="flow-reset"' in html and "localStorage" in html,
          "what you move is remembered, and can be reset")
    check("flowWrap" in html, "long node names wrap instead of being cut")
    # En färsk ritning måste sätta den sparade förskjutningen: gjorde den inte det
    # stod rutorna kvar på filens plats medan vägarna pekade någon helt annanstans.
    check("n8n:s egen tavla" in html, "the view says whether the drawing is n8n's own")
    # Klicket är hela poängen med inspektören: noden berättar vad den är och vad den
    # gör, med de parametrar den faktiskt körs med (mätt: "Report to the panel", 10 lines).
    check('id="flowNode"' in html and "flowNodeShow(drag.key)" in html,
          "a node opens in an inspector when clicked")
    check("flowVal" in html and "pkey" in html, "the inspector draws the node's settings")
    # Projektväljaren i Karta hämtar sina val ur länkregistret -- bara de projekt som är
    # kopplade till både Jira och GitHub, samma lista sidofältet bygger på.
    check('id="kartaProject"' in html and "renderKartaProject" in html and "links()" in html,
          "the map has a project picker fed by the link registry")
    # Ritningen får inte läsa STATE innan den finns: en djup länk (#karta?kalla=flow)
    # ritade vid start och kastade, och då avbröts hela laddningen -- splash stod kvar.
    check("((STATE || {}).flows || [])" in html, "the drawing tolerates an empty state at boot")
    # Editorn där flödet bor: n8n:s egen, inbäddad. Rutan byggs ur flödets id och
    # panelens EGEN värd -- annars hamnar den på en värd man inte är inloggad på.
    # (Mätt: src blev http://127.0.0.1:5678/workflow/Drq6dySctqkoGTPj.)
    check('id="flowEditor"' in html and "location.hostname" in html and "/workflow/" in html
          and 'data-act="flow-edit"' in html,
          "the flow can be edited where it lives, without a second truth")
    # En omladdning var 60:e sekund får inte rycka editorn ur händerna på den som sitter
    # i den -- bara ett flödesbyte stänger rutan.
    check("FLOW.editorFile && FLOW.editorFile !== flow.file" in html,
          "the editor survives a refresh, but not a flow switch")
    # Nytt projekt kräver "Administer Jira", som är en annan behörighet än
    # "Administer Projects". Utan den skall raden säga varför -- inte vara en knapp.
    # Vyerna: show() slaepper bara igenom det som star i TEXT.views, sa en vy-sektion
    # utan post dar ar en dod lank -- kugghjulet gick till "board" i stallet for
    # Installningar (matt i webblasaren). Bada hallen kontrolleras.
    table = re.search(r"views:\s*\{(.*?)\}\s*,\s*\n", html, re.S)
    check(bool(table), "vy-tabellen går att läsa")
    # Panelen är en enda fil: en syntaxmiss i skriptet släcker hela sidan tyst
    # (mätt: tom rail och "adminVisible is not defined"). node --check fångar den.
    import os as _os, subprocess as _sp, tempfile as _tf
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    check(bool(blocks), "skriptblocket går att läsa")
    with _tf.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(blocks))
        js_path = fh.name
    probe = _sp.run(["node", "--check", js_path], capture_output=True, text=True)
    check(probe.returncode == 0, "panelens skript har ren syntax",
          (probe.stderr or "").strip().splitlines()[:4])
    _os.unlink(js_path)
    # Kataloggrupperingen: katalogen bär sökvägen, raden bara namnet. Panelen har ingen
    # egen provkörare, men funktionen är ren -- den extraheras och körs i node med ett
    # litet träd, så en trasig gruppering (rot-filer, djup, sortering) syns direkt.
    fn = re.search(r"function dirGroups\(list\) \{.*?\n\}", html, re.S)
    check(bool(fn), "kataloggrupperingen går att läsa")
    if fn:
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(fn.group(0) + """
const got = dirGroups(["a/b/C.java", "a/b/D.java", ".gitattributes", "x/y/z/F.xml", "a/b/E.java"])
  .map(([d, n]) => d + "|" + n.join(","));
const want = ["|.gitattributes", "a/b/|C.java,D.java,E.java", "x/y/z/|F.xml"];
console.log(JSON.stringify(got) === JSON.stringify(want) ? "OK" : "FEL " + JSON.stringify(got));
""")
            group_js = fh.name
        group_run = _sp.run(["node", group_js], capture_output=True, text=True)
        _os.unlink(group_js)
        check(group_run.stdout.strip() == "OK",
              "katalogen bär sökvägen, raden bara filnamnet",
              (group_run.stdout + group_run.stderr).strip()[:140])

    # Ändringen: filhuvudena (+++ / ---) börjar också med + och -, så ordningen i
    # färgläggningen är hela skillnaden mellan en grön filväg och rätt svar.
    fn = re.search(r"function diffFiles\(text\) \{.*?\n\}", html, re.S)
    kind = re.search(r"const diffKind = r =>.*?;", html, re.S)
    rader_fn = re.search(r"function diffRows\(rader\) \{.*?\n\}", html, re.S)
    check(bool(fn and kind and rader_fn), "färgläggningen av ändringen går att läsa")
    if fn and kind:
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(fn.group(0) + "\n" + kind.group(0) + "\n" + rader_fn.group(0) + """
const lines = ["diff --git a/x/F.java b/x/F.java", "index 1..2 100644", "--- a/x/F.java",
               "+++ b/x/F.java", "@@ -1 +1 @@", "-gammal", "+ny", " oförändrad"];
const kind = lines.map(diffKind);
const want = ["", "meta", "meta", "meta", "hunk", "del", "add", ""];
// Radbrytningen byggs med fromCharCode: en escape i provet blir två lager bad.
const tx = ["diff --git a/x/F.java b/x/F.java", "+a",
            "diff --git a/y/G.java b/y/G.java", "+b"].join(String.fromCharCode(10));
const filer = diffFiles(tx).map(f => f.vag);
// Sida vid sida: paret hör ihop, och en oförändrad rad står på båda sidorna.
const p = diffRows(["@@ -1,3 +1,3 @@", "-a", "-b", "+a2", " kvar"]).map(x => [x.l, x.r, x.hel || 0]);
const pWant = [["@@ -1,3 +1,3 @@", "", 1], ["-a", "+a2", 0], ["-b", "", 0], [" kvar", " kvar", 0]];
// En rad som bara LÄGGS TILL ersätter ingenting: vänster cell skall vara tom.
const ensam = diffRows(["@@ -0,0 +1 @@", "+bara ny"]).map(x => [x.l, x.r, x.hel || 0]);
const ensamWant = [["@@ -0,0 +1 @@", "", 1], ["", "+bara ny", 0]];
console.log(JSON.stringify(kind) === JSON.stringify(want) && filer.length === 2 &&
            filer[0] === "x/F.java" && filer[1] === "y/G.java" &&
            JSON.stringify(p) === JSON.stringify(pWant) &&
            JSON.stringify(ensam) === JSON.stringify(ensamWant)
            ? "OK" : "FEL " + JSON.stringify([kind, filer, p, ensam]));
""")
            ändring_js = fh.name
        ändring_run = _sp.run(["node", ändring_js], capture_output=True, text=True)
        _os.unlink(ändring_js)
        check(ändring_run.stdout.strip() == "OK",
              "en filväg blir inte grön och en borttagen rad inte röd",
              (ändring_run.stdout + ändring_run.stderr).strip()[:140])

    # Kunskapsgrafen går att GÅ I: en enhets grannar räknas ur relationerna, ett paket får
    # sina egna filer och sina egna ärenden (ingenting läckt från grannpaketet), och filens
    # ärenden är de som nämner just den. Rena funktioner mot en handgjord graf -- de skall
    # inte hänga på att maskinen råkar ha en scanning för tillfället.
    fn = re.search(r"function graphView\(graph, id\) \{.*?\n\}", html, re.S)
    check(bool(fn), "grafens uppslag går att läsa")
    if fn:
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(fn.group(0) + re.search(r"function graphIssues\(graph, id\) \{.*?\n\}",
                                             html, re.S).group(0) + """
const g = {
  entities: [
    {id: "paket:a", type: "package", name: "a"}, {id: "paket:b", type: "package", name: "b"},
    {id: "paket:c", type: "package", name: "c"},
    {id: "fil:a/Foo.java", type: "file", name: "Foo.java", package: "a"},
    {id: "fil:a/Bar.java", type: "file", name: "Bar.java", package: "a"},
    {id: "fil:b/Zap.java", type: "file", name: "Zap.java", package: "b"},
    {id: "ärende:P-1", type: "issue", name: "P-1", summary: "ett"},
    {id: "ärende:P-2", type: "issue", name: "P-2", summary: "två"},
  ],
  relations: [
    {from: "fil:a/Foo.java", to: "paket:a", kind: "ligger-i"},
    {from: "fil:a/Bar.java", to: "paket:a", kind: "ligger-i"},
    {from: "fil:b/Zap.java", to: "paket:b", kind: "ligger-i"},
    {from: "ärende:P-1", to: "fil:a/Foo.java", kind: "nämner"},
    {from: "ärende:P-2", to: "fil:a/Bar.java", kind: "nämner"},
    {from: "ärende:P-1", to: "fil:b/Zap.java", kind: "nämner"},
    {from: "paket:c", to: "paket:a", kind: "använder"},
    {from: "paket:b", to: "paket:a", kind: "använder"},
  ],
};
const namn = xs => (xs || []).map(e => e.name).sort().join(",");
const bad = [];
const p = graphView(g, "paket:a");
if (!p) bad.push("paket:a saknas");
if (namn(p.pointedAtBy["ligger-i"]) !== "Bar.java,Foo.java") bad.push("paketets filer: " + namn(p.pointedAtBy["ligger-i"]));
if (namn(p.pointedAtBy["använder"]) !== "b,c") bad.push("paketets användare: " + namn(p.pointedAtBy["använder"]));
if (p.pointsAt["ligger-i"]) bad.push("paketet skall inte ligga i något");
if (namn(graphIssues(g, "paket:a")) !== "P-1,P-2") bad.push("paketets ärenden: " + namn(graphIssues(g, "paket:a")));
if (namn(graphIssues(g, "fil:a/Foo.java")) !== "P-1") bad.push("filens ärenden: " + namn(graphIssues(g, "fil:a/Foo.java")));
if (namn(graphIssues(g, "paket:b")) !== "P-1") bad.push("grannpaketets ärenden läckte in: " + namn(graphIssues(g, "paket:b")));
const f = graphView(g, "fil:a/Foo.java");
if (namn(f.pointsAt["ligger-i"]) !== "a") bad.push("filens paket: " + namn(f.pointsAt["ligger-i"]));
if (!graphView(g, "ärende:P-1").pointsAt["nämner"].length) bad.push("ärendet pekar inte på några filer");
if (graphView(g, "finns-inte")) bad.push("okänt id gav en vy");
if (graphView({}, "a")) bad.push("tom graf gav en vy");
console.log(bad.length ? "FEL " + bad.join(" | ") : "OK");
""")
            graph_js = fh.name
        graph_run = _sp.run(["node", graph_js], capture_output=True, text=True)
        _os.unlink(graph_js)
        check(graph_run.stdout.strip() == "OK",
              "en enhet får sina egna grannar -- och bara sina egna",
              (graph_run.stdout + graph_run.stderr).strip()[:160])

    # Filhanteringen är en flik som de andra: nyckeln i TABS, panen i markupen, och en
    # laddningsväg i show(). En flik utan pane är ett klick som inte gör något.
    check('["filer", "Filerna"]' in html and 'id="pane-repos-filer"' in html,
          "filhanteringen har en egen flik")
    check('tabState.repos === "filer"' in html, "fliken laddar sitt innehåll när den öppnas")
    check('filUrl("files"' in html and 'filUrl("file"' in html and 'filUrl("commits"' in html,
          "fliken läser filerna, filen och historiken")
    check('data-file=' in html and 'function openFile(' in html,
          "en filrad öppnar filen")

    # Vad som syns: valet ligger i webbläsaren som en lista av det som är AV. Skräp i
    # lagringen skall ge inga val (allt syns), inte ett halvt trasigt gränssnitt.
    fn = re.search(r"function hiddenPrefs\(raw\) \{.*?\n\}", html, re.S)
    check(bool(fn), "listan över avstängt går att läsa")
    if fn:
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(fn.group(0) + """
const fall = [[null, 0], ["[]", 0], ['["ov:stats"]', 1], ['["ov:stats","ov:stats"]', 1],
  ['["ov:stats","view:karta"]', 2], ["trasigt", 0], ['{"a":1}', 0], ['[1,"view:karta"]', 1],
  ['["admin:all","ov:stats;"]', 0], ['["view:"]', 0], ['["ov:stat:jira"]', 1],
  ['["ov:stat:jira","ov:stat:jira"]', 1], ['["ov:stat:JIRA"]', 0], ['["ov:stat:"]', 0]];
const bad = fall.filter(([raw, n]) => hiddenPrefs(raw).length !== n)
  .map(([raw, n]) => raw + " -> " + JSON.stringify(hiddenPrefs(raw)));
console.log(bad.length ? "FEL " + bad.join(" | ") : "OK");
""")
            prefs_js = fh.name
        prefs_run = _sp.run(["node", prefs_js], capture_output=True, text=True)
        _os.unlink(prefs_js)
        check(prefs_run.stdout.strip() == "OK",
              "trasig lagring ger inga avstängningar, dubbletter räknas en gång",
              (prefs_run.stdout + prefs_run.stderr).strip()[:140])

    # Ordningen: sparad ordning först, resten i filens ordning efter -- annars försvinner
    # ett nytt block ur vyn när listan läses, eller hamnar före allt man själv flyttat.
    fn = re.search(r"function orderOf\(raw, ids\) \{.*?\n\}", html, re.S)
    check(bool(fn), "ordningslistan går att läsa")
    if fn:
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(fn.group(0) + """
const ids = ["a", "b", "c"];
const fall = [[null, "a,b,c"], ["[]", "a,b,c"], ['["c"]', "c,a,b"], ['["c","c"]', "c,a,b"],
  ['["x","b"]', "b,a,c"], ["trasigt", "a,b,c"], ['{"a":1}', "a,b,c"], ['[1,"b"]', "b,a,c"],
  ['["b","c","a"]', "b,c,a"]];
const bad = fall.filter(([raw, want]) => orderOf(raw, ids).join(",") !== want)
  .map(([raw, want]) => raw + " -> " + orderOf(raw, ids).join(",") + " (ville " + want + ")");
console.log(bad.length ? "FEL " + bad.join(" | ") : "OK");
""")
            order_js = fh.name
        order_run = _sp.run(["node", order_js], capture_output=True, text=True)
        _os.unlink(order_js)
        check(order_run.stdout.strip() == "OK",
              "ett nytt block hamnar sist, skräp ger filens ordning",
              (order_run.stdout + order_run.stderr).strip()[:140])

    if table:
        named = set(re.findall(r"(\w+):\s*\[", table.group(1)))
        sections = set(re.findall(r'<section class="view[^"]*" id="v-(\w+)"', html))
        check(named == sections, "varje vy finns i bade tabellen och sidan",
              "bara i tabellen: %s - bara som sektion: %s" % (sorted(named - sections), sorted(sections - named)))
    check("STATE.projectCan" in html and "nytt projekt" in html,
          "the sidebar says whether a project may be created")
    check("def project_can" in (HERE / "server.py").read_text(),
          "the API carries the permission answer")
    check('transform="translate(${spot.x - n.x} ${spot.y - n.y})"' in html,
          "a fresh drawing applies the moves you made")
    # Luften kommer ur .wrap (padding 24/28/56). Ett tidigt </div> gjorde att admin,
    # setup och settings hamnade utanfor rutan: de arvde ingen padding och lag kant i
    # kant med fonstret (matt: luft vanster 0 px, hoger 11 px = bara scrollbaren).
    parents, stack = {}, []
    for tok in re.finditer(r"<(/?)(\w+)([^>]*)>", html):
        close, tag, attrs = tok.group(1), tok.group(2).lower(), tok.group(3)
        if tag in ("meta", "link", "br", "img", "input", "hr"):
            continue
        if close:
            if stack:
                stack.pop()
            continue
        if tag == "section" and 'class="view' in attrs:
            parents[re.search(r'id="v-(\w+)"', attrs).group(1)] = stack[-1] if stack else "?"
        stack.append(tag)
    without = sorted(k for k, v in parents.items() if v != "div")
    check(bool(parents) and not without, "varje vy ligger i luft-rutan, inte direkt i main",
          "utanfor: %s" % without)

    # Kartan fragade efter repot pa fyra stallen och glomde det i vyer utan valt repo
    # -- svaret sag ut som en saknad karta. Ett uppslag, alla vagar.
    check(html.count("scanRepo()") >= 4 and "repo: REPO.name }" not in html,
          "scanningen frågar efter samma repo överallt", "scanRepo() x%d" % html.count("scanRepo()"))

    # Flödeskartan: artefakten tar ritytans plats. "✎ ändra" byggdes förut inuti
    # ritytans rendering, så den försvann i samma stund som artefakten kom in.
    server = (HERE / "server.py").read_text(encoding="utf-8")
    sites = (HERE.parent / "bin" / "jira_sites.py").read_text(encoding="utf-8")
    check("flowMapShow" in html and 'class="flowMap"' in html,
          "the flow map is drawn where the canvas was")
    # Kunskapsgrafen: samma arbetsdelning som kartan -- motorn bygger, panelen och
    # agenten läser. Ingen Jira-trafik i den, så den svarar på under en sekund.
    check('"/api/graph"' in server and "graph_read" in server,
          "the knowledge graph is served to whoever asks for it")
    check('"/api/chat"' in server and "chat_ask" in server and '"/api/agent"' in server,
          "the chat and the agent connection are served")
    check('id="v-chat"' in html and "renderChat" in html and "agentLoad" in html,
          "the agent has a view of its own, with the connection in it")
    check('id="flowEdit"' in html and "getElementById(\"flowEdit\")" in html,
          "the edit button sits in the card head, so both views have it")
    # Fliksystemet: Karta flyttade ur rälen in i reposidan som flik, och den gamla
    # vägen dit (#karta) skall fortfarande landa rätt -- djupa länkar och layoutval
    # pekar på den. Mätt i den skarpa panelen innan detta skrevs.
    check('id="tabs-repos"' in html and 'id="pane-repos-kartan"' in html and 'id="pane-repos-repot"' in html,
          "the repo page has tabs and the map is one of them")
    check('karta: ["Karta"' not in html and 'if (v === "karta")' in html,
          "the map left the rail, and its old route still lands")
    # Navigeringen är EN kolumn. Rälens ikonkolumn bor nu i sidofältets rader: ikonen har
    # en fast ruta, så etiketterna börjar på samma x. Utan den ritas raden utan märke och
    # raderna hamnar snett igen (mätt: alla etiketter på samma x i den skarpa panelen).
    # Ikonen får inte vara bredare än sin ruta: då skjuts etiketterna olika långt in och
    # kolumnen spricker. Rutan är 18, ikonen 17,5 -- tio procent större än förut, samma rad.
    check(".item .g { width: 18px" in html and ".item .g svg { width: 17.5px" in html,
          "the one navigation is a column: every icon fits its box, so labels line up")
    check('id="rail"' not in html and "renderRail" not in html and ".railitem" not in html,
          "det finns en navigering, inte två (rälen är borta, inte bara tömd)")
    vblock = html.split("views: {", 1)[1].split("},", 1)[0]
    vkeys = set(re.findall(r"(\w+):\s*\[", vblock))
    ikeys = set(re.findall(r"^\s{2}(\w+):\s*'<svg", html, re.M))
    check(bool(vkeys) and vkeys <= ikeys,
          "varje vy i rälen har en ikon (%d vyer, %d ikoner)" % (len(vkeys), len(ikeys)))
    check('"/api/flowmap"' in server and "flowmap_available" in server,
          "the flow map route is in the server")

    check("AbortSignal.timeout" in html, "the state fetch has a time limit")
    check("retryLater" in html, "a failed refresh is retried and said out loud")

    # Flödeskartan: bara flödet för projektet man står i (hemvisten ur .scopes.json),
    # och panelens EGEN palett pålagd på Archifys HTML i stället för renderarens blå.
    check('String(f.scope).toUpperCase() === hem' in html and "const hem =" in html,
          "flödeslistan visar bara repots eget flöde, utan reservväg")
    check("data-from=" in server and "flowmap_theme" in server,
          "panelens palett läggs på flödeskartan")

    # Kön och bygget: ett block som ingen renderare når är en tom plats som ser ut som ett
    # bad, och talen hör i servern (de går att prova), inte i ritningen.
    check('data-ov="needs"' in html and "function renderNeeds()" in html and "renderNeeds();" in html,
          "kön har ett block och ritas på Översikt")

    # Lyssnare hör hemma bland de globala, inte inuti en annan vys ritare: sökningens
    # koppling låg först i renderChat(), alltså kopplades den när man besökte Agenten --
    # och fram till dess hände ingenting när man skrev (mätt i webbläsaren).
    chatt = re.search(r"function renderChat\(\) \{.*?\n\}", html, re.S)
    check(bool(chatt) and "findInput" not in chatt.group(0),
          "sökningens lyssnare ligger inte inuti en annan vys ritare")
    check(html.count("const findInput = document.getElementById") == 1,
          "sökningens lyssnare kopplas på ett ställe")
    check('answer["needs"] = needs_list(answer)' in server and "def needs_list(" in server,
          "kön räknas i servern ur state-svaret")
    check('id="impTeamPeople"' in html and 'id="impTeamRoles"' in html
          and 'id="impTeamWeeks"' in html and 'data-act="imp-team-save"' in html
          and "def team_save(payload: dict)" in server and '"/api/team": team_save' in server,
          "teamet går att ange: antal, roller och sprintlängd, och sparas i panelens fil")
    check('"--team-people"' in server and '"--team-roles"' in server
          and '"team": pool.submit(team_read)' in server,
          "teamet följer med varje körning in i motorn")
    check('id="impLog"' in html and "function impEvent(line)" in html
          and "stream: true }) });" in html and "def import_stream(payload: dict)" in server
          and 'handler is import_parse and payload.get("stream")' in server,
          "importen visar stegen medan de händer i stället för att bara räkna sekunder")
    check("def import_parse(payload: dict, on_line=None)" in server
          and "on_line(line if line.endswith" in server,
          "samma import kör både den buffrade vägen och strömmen")
    check("const RAIL = [" in html and "const DELNAMN = {" in html
          and 'RAIL.filter(([namn]) => namn !== "systemet")' in html
          and '<span class="pil"' in html
          and "Object.fromEntries(navViews())" in html,
          "avdelningarna är rubriker i sidofältet (en tabell, med sin ikon), inställningarna är ingen avdelning, och behörigheten bestämmer vad som syns")
    check('data-fall="' in html and 'role="button"' in html and "const FALL_KEY =" in html
          and "const vik = (namn) => {" in html and "renderSide();" in html.split("const vik")[1][:200],
          "varje del i sidofältet går att fälla ihop, och valet ligger utanför ritningen")
    check(".grupp.falt { display: none; }" in html and 'closest("[data-fall]")' in html,
          "ihopfälld grupp försvinner (utan [hidden], som en display-regel hade vunnit över)")
    check("const FOT = [" in html and '<div class="fot">' in html
          and "margin-top: auto" in html and "FOT.filter(v => synliga[v]).map(rad)" in html,
          "inställningarna ligger sist i sidofältet, i sin egen fot")
    check('data-v="board"><span class="g">▤</span>' not in html,
          "tavlan står inte både i menyn och som genväg (samma mål i två ordförråd)")

    check("function driftRow(s)" in html and "const drift = driftRow(s);" in html
          and "return drift +" in html
          and "if (!t.mats) return drift +" in html,
          "den körande commiten står i sajtens detalj, inte bara för dem med trafik")
    check("VAKTER = [" in sites and "def vakt_lage(" in sites and "\"vakter\": [dict(v, **vakt_lage" in sites,
          "jobben som skall köra av sig själva läses med samma lätta anrop som det som körs")
    check("\"vakter\"" in server and "backningen gick inte igenom" in server,
          "kön får en rad för en backning som föll")
    check("def drift_read()" in server and '"drift": pool.submit' in server,
          "vad som körs läses i state (den lätta vägen), cachat")
    check("def ci_of(" in server and "def ci_state(" in server and "ov:stat:ci" in html,
          "byggstatusen läses för de kopplade repona och har ett kort")

    # Väntetiden: en räknare som visar att något händer, och ingen siffra som kan bli en
    # lögn. Mätt: ett litet dokument tar 133 s, en pdf 240 s -- "en till två minuter" var
    # fel, och fyra tysta minuter ser ut som en död knapp.
    check("const mmss = ms =>" in html and "IMP.tick = setInterval(" in html
          and "clearInterval(IMP.tick)" in html,
          "importen räknar upp väntetiden medan svaret väntar")
    check("en till två minuter" not in html
          and "en till två minuter" not in (HERE / "i18n" / "sv.json").read_text(encoding="utf-8"),
          "ingen siffra lovas som mätningen inte håller")

    # Ett id som används två gånger: getElementById ger den FÖRSTA, och skriver man till
    # den andra hamnar texten i en nod som inte ritas. Importen hade "impFiles" på både
    # filväljaren och etiketten, så raden stod kvar på "inga filer valda" hur många filer
    # man än valde (mätt i webgläsaren). Provet fångar nästa dubbelt använda id.
    # (Ingen lokal import av re här: i Python blir namnet lokalt i HELA funktionen, och
    # en rad ovanför som använder det dör med UnboundLocalError.)
    import collections
    ids = re.findall(r'\bid="([A-Za-z0-9_.:-]+)"', html)
    dupes = sorted(k for k, v in collections.Counter(ids).items() if v > 1)
    check(not dupes, "inget id används två gånger", ", ".join(dupes))
    check('id="impFilesNote"' in html and 'getElementById("impFilesNote").textContent' in html,
          "importens etikett har ett eget id (inputen äger sitt)")

    # Sökningen: en ruta över allt. Funktionen är ren (fråga, state), så den körs i node mot
    # ett handgjort svar -- tillsammans med EXAKT de parts den använder (board/pickedBoard/
    # boardItems), för då bevisas att backloggen kommer från samma källa som tablan och inte
    # från en egen kopia som glider isär.
    parts = []
    for pattern in (r"const F = \{[^\n]*\};", r"const board = \(st\) =>[^\n]*\n",
                    r"const issues = \(st\) =>[^\n]*\n", r"const backlog = \(st\) =>[^\n]*\n",
                    r"function pickedBoard\(st\) \{.*?\n\}", r"function boardItems\(st\) \{.*?\n\}",
                    r"function findHits\(q, state\) \{.*?\n\}",
                    r"function findFileHits\(q, files, repo\) \{.*?\n\}"):
        m = re.search(pattern, html, re.S)
        check(bool(m), "sökningens parts går att läsa: " + pattern[:26])
        if m:
            parts.append(m.group(0))
    if len(parts) == 8:
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(parts) + """
var TEXT = { views: { items: ["Uppgifter", "▤"], settings: ["Inställningar", "⚙"] } };
const state = { jira: { boards: [{ issues: [{ key: "SCRUM-1", summary: "Byt färg", statusName: "To Do" }],
                                        backlog: [{ key: "SCRUM-9", summary: "Gammal grej", statusName: "To Do" }] }] },
                github: { repos: [{ name: "AutoCore", description: "skolprojekt", visibility: "PRIVATE" }],
                          pullRequests: [{ number: 4, title: "Byt färg i tavlan", url: "u",
                                           repository: { nameWithOwner: "mig/AutoCore" } }], issues: [] },
                runs: { runs: [{ key: "SCRUM-101", branch: "godjira/SCRUM-101", ok: false, pr: "" }] },
                flows: [{ id: "Dv5", name: "AutoCore — flödet" }] };
const lines = [];
const t = (namn, ok) => lines.push((ok ? "OK " : "FEL ") + namn);
const h = (q, villkor) => findHits(q, state).some(villkor);
t("nyckeln", h("SCRUM-1", v => v.key === "SCRUM-1"));
t("sammanfattningen", h("färg", v => v.key === "SCRUM-1"));
t("backloggen (samma källa som tablan)", h("Gammal", v => v.key === "SCRUM-9"));
t("repot", h("(x", v => false) === false && h("AutoCore", v => v.kind === "repo"));
t("körningen", h("SCRUM-101", v => v.kind === "run"));
t("vyn", h("inställ", v => v.kind === "view"));
t("ärendet går till uppgifterna", (findHits("SCRUM-1", state)[0] || {}).go.view === "items");
t("en bokstav är ingen fråga", findHits("S", state).length === 0);
t("taket står vid tjugo", findHits("e", { jira: { boards: [{ issues: Array.from({ length: 30 },
    (_, i) => ({ key: "K-" + i, summary: "ett ärende" })) }] } }).length <= 20);
const filer = ["README.md", "src/com/wac/service/CustomerService.java", "src/ui/App.java"];
t("filens sökväg", findFileHits("service/", filer, "AutoCore").some(f => f.title === filer[1]));
t("filens namn", findFileHits("customer", filer, "AutoCore").some(f => f.key === "CustomerService.java"));
t("filen vet sitt repo", findFileHits("readme", filer, "AutoCore")[0].sub === "AutoCore");
t("filen går till Filer-fliken", findFileHits("readme", filer, "AutoCore")[0].go.file === "README.md");
t("ingen fil utan fråga", findFileHits("x", filer, "AutoCore").length === 0);
t("filernas tak står vid tjugo", findFileHits(".java",
    Array.from({ length: 30 }, (_, i) => "a/f" + i + ".java"), "AutoCore").length === 20);
console.log(lines.join("\\n"));
""")
            search_js = fh.name
        search = _sp.run(["node", search_js], capture_output=True, text=True)
        _os.unlink(search_js)
        lines = [r for r in search.stdout.strip().splitlines() if r.strip()]
        bad = [r for r in lines if not r.startswith("OK")]
        felrader = bad + (["%d rader, väntade 15" % len(lines)] if len(lines) != 15 else [])
        check(not felrader, "sökningen hittar rätt sak och går till rätt ställe",
              felrader[0] if felrader else "")

    # Två SVG:er på två adresser, och rätt fil på varje. Förut serverades märket på båda,
    # så kartan i sajtvyn ritade en stor GodJIRA mitt i huvudytan (mätt: samma 53 550 byte
    # på /world.svg som på /godjira.svg).
    def hamta(adress: str) -> bytes:
        with urllib.request.urlopen(PANEL + adress, timeout=60) as svar:
            return svar.read()

    karta, marke = hamta("/world.svg"), hamta("/godjira.svg")
    check(karta == (HERE.parent / "assets" / "world.svg").read_bytes() and karta != marke,
          "kartan på /world.svg är kartan, inte märket")
    check(b'viewBox="0 0 360 180"' in karta and b'viewBox="0 0 360 180"' not in marke,
          "kartan har kartans egen ram -- 360x180 grader, som punkterna räknas i")

    # Vändningen, matt mot världen själv: Antarktis är det enda land som går runt hela jorden,
    # så det skall ligga som en fullbredds-remsa i nederkant. Förut låg den i överkant, och då
    # hamnade Alex i Australien.
    grader = [tuple(map(float, p.split())) for p in
              re.findall(r"-?\d+\.\d+ -?\d+\.\d+", karta.decode("utf-8", "replace").split('d="', 1)[1])]
    nederst = [p for p in grader if p[1] > 171]
    check(len(nederst) > 100 and (max(x for x, _ in nederst) - min(x for x, _ in nederst)) > 300,
          "Antarktis ligger i nederkant -- kartan är inte uppochner")
    for namn, lat, lon in (("Stockholm", 59.33, 18.07), ("Sydney", -33.87, 151.21)):
        x, y = lon + 180, 90 - lat
        check(any(abs(px - x) < 6 and abs(py - y) < 6 for px, py in grader),
              "%s ligger på land på kartan" % namn)
    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("all good: the repos open in the app, the flows are drawn from their own files, "
          "and the shell cannot hang empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
