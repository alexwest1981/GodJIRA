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
    # Listan bor i sidofältet, för 59 rader i huvudrutan blev lång skroll innan
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
    # (mätt: STATE förblev null i webbläsaren, inga fel i konsolen).
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
    check("renderFlow" in html and "kartaShow" in html and 'id="flowCanvas"' in html,
          "the flow is drawn in the Karta view")
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
    # gör, med de parametrar den faktiskt körs med (mätt: "Report to the panel", 10 rader).
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
const fel = fall.filter(([raw, n]) => hiddenPrefs(raw).length !== n)
  .map(([raw, n]) => raw + " -> " + JSON.stringify(hiddenPrefs(raw)));
console.log(fel.length ? "FEL " + fel.join(" | ") : "OK");
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
const fel = fall.filter(([raw, want]) => orderOf(raw, ids).join(",") !== want)
  .map(([raw, want]) => raw + " -> " + orderOf(raw, ids).join(",") + " (ville " + want + ")");
console.log(fel.length ? "FEL " + fel.join(" | ") : "OK");
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
    check('STATE.projectCan' in html and 'data-v="board"' in html,
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
    check("flowMapShow" in html and 'class="flowMap"' in html,
          "the flow map is drawn where the canvas was")
    check('id="flowEdit"' in html and "getElementById(\"flowEdit\")" in html,
          "the edit button sits in the card head, so both views have it")
    # Fliksystemet: Karta flyttade ur rälen in i reposidan som flik, och den gamla
    # vägen dit (#karta) skall fortfarande landa rätt -- djupa länkar och layoutval
    # pekar på den. Mätt i den skarpa panelen innan detta skrevs.
    check('id="tabs-repos"' in html and 'id="pane-repos-kartan"' in html and 'id="pane-repos-repot"' in html,
          "the repo page has tabs and the map is one of them")
    check('karta: ["Karta"' not in html and 'if (v === "karta")' in html,
          "the map left the rail, and its old route still lands")
    # Rälen: en ikonkolumn med fast bredd, och en ikon per vy. Mätt i den skarpa panelen:
    # alla nio etiketterna börjar på samma x (51), ikonrutorna är 16x16, inget svämmar
    # över. Utan ikon ritas raden utan märke, och raderna hamnar snett igen.
    check("grid-template-columns: 22px 1fr" in html, "the rail is an icon column, not a stack")
    vblock = html.split("views: {", 1)[1].split("},", 1)[0]
    vkeys = set(re.findall(r"(\w+):\s*\[", vblock))
    ikeys = set(re.findall(r"^\s{2}(\w+):\s*'<svg", html, re.M))
    check(bool(vkeys) and vkeys <= ikeys,
          "varje vy i rälen har en ikon (%d vyer, %d ikoner)" % (len(vkeys), len(ikeys)))
    check('"/api/flowmap"' in server and "flowmap_available" in server,
          "the flow map route is in the server")

    check("AbortSignal.timeout" in html, "the state fetch has a time limit")
    check("retryLater" in html, "a failed refresh is retried and said out loud")

    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("all good: the repos open in the app, the flows are drawn from their own files, "
          "and the shell cannot hang empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
