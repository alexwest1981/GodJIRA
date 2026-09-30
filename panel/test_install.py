#!/usr/bin/env python3
"""install.sh: hela installationen, provad i en låtsasmaskin.

Kör: python3 panel/test_install.py   (exit 0 = grönt)

Skriptet skriver om sökvägar och grenar på vad som finns på maskinen, och ett fel
där syns inte förrän en kollega har kört det. Provet kör därför install.sh mot en
kopia av repot i /tmp, med attrapp-systemctl/omarchy/n8n/hyprctl först i PATH och
ett eget HOME, och kräver:

  1. Att ingenting från byggmaskinen följer med. Mallarna i repot bär @ROOT@,
     @PYTHON@, @PORT@, @BIND@, @LAUNCHER@, @N8N@ och @N8N_PATH@; lämnas något av
     dem kvar i en färdig fil har ingen fyllt i det, och då skall provet säga det.
  2. Att pluginmappen heter GodJIRA.plugin, att en gammal custom.jira-katalog tas
     bort, och att rälens layout (shell.json) byter namn med — annars är widgeten
     borta ur baren efter en omkörning.
  3. Att fönsterregeln hamnar i hyprland.lua, att en äldre regel för samma fönster
     byts ut i stället för att ligga kvar och säga emot, och att en andra körning
     inte lägger dit den en gång till.
  4. Att panelen bara svarar på den här maskinen som standard, att egna val av port
     och bind slår igenom, och att omarchy/n8n saknas utan att installationen faller.

Ingenting här nuddar den riktiga maskinen: alla kommandon är attrapper, och HOME är
en temporär mapp under /tmp.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FEL = []
# Verktygen skriptet använder, och bara dem: PATH består av den här mappen, alltså
# finns varken omarchy eller n8n där om provet inte lägger dit dem. Att ärva /usr/bin
# gick inte -- den riktiga omarchy-binären låg där och pluginen installerades i smyg.
VERKTYG = ["sh", "python3", "sed", "id", "curl", "sleep", "dirname", "mkdir", "chmod",
           "cp", "mv", "rm", "cat", "uname", "tr", "grep", "wc", "date"]


def check(ok: bool, what: str, detail: str = "") -> None:
    print(("ok   " if ok else "FEL  ") + what + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FEL.append(what)


def stub(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def run(world: Path, log: Path, extra_env=None, omarchy=True, n8n=True,
        hyprctl=True) -> subprocess.CompletedProcess:
    """Kör install.sh i låtsasmaskinen. Ingenting riktigt rörs."""
    bindir = world / "bin"
    for name, body in (("systemctl", 'echo "systemctl $*" >> ' + str(log)),
                       ("omarchy", 'echo "omarchy $*" >> ' + str(log)),
                       ("omarchy-shell", 'echo "omarchy-shell $*" >> ' + str(log)),
                       ("omarchy-launch-webapp", "exit 0"),
                       ("hyprctl", 'echo "hyprctl $*" >> ' + str(log)),
                       ("n8n", "exit 0")):
        p = bindir / name
        if p.exists():
            p.unlink()
        if (name.startswith("omarchy") and omarchy) or (name == "n8n" and n8n) or \
           (name == "hyprctl" and hyprctl) or name == "systemctl":
            stub(bindir, name, body)
    for name in VERKTYG:
        p = bindir / name
        if not p.exists():
            real = shutil.which(name)
            if real:
                p.symlink_to(real)
    env = {"HOME": str(world / "home"), "PATH": str(bindir),
           "PLUGIN_DIR": str(world / "plugin")}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["sh", str(world / "repo/install.sh")], env=env,
                          capture_output=True, text=True, timeout=180)


def varld(tmp: Path, namn: str) -> Path:
    """En tom låtsasmaskin med hem, hypr-config och en kopia av repot."""
    world = tmp / namn
    (world / "home/.config/omarchy/plugins").mkdir(parents=True)
    (world / "home/.config/hypr").mkdir(parents=True)
    (world / "bin").mkdir(parents=True)
    shutil.copytree(ROOT, world / "repo", ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return world


def main() -> int:
    # tempfile under /tmp, inte under scratch: då finns ingen /home/alex alls i
    # det som skrivs, och "ingenting från byggmaskinen" blir en riktig fråga.
    tmp = Path(tempfile.mkdtemp(prefix="godjira-install-", dir="/tmp"))
    try:
        # Mallarna i repot skall vara allmänna: en sökväg från en viss maskin i en
        # mall är precis felet provet finns för.
        mallar = ["panel/godjira-panel.service", "panel/godjira.desktop",
                  "panel/godjira-flodet.desktop", "n8n/n8n.service"]
        kvar = [f for f in mallar if "/home/" in (ROOT / f).read_text()]
        check(not kvar, "mallarna bär inga maskinbundna sökvägar", ", ".join(kvar))
        utan_ph = [f for f in mallar if "@ROOT@" not in (ROOT / f).read_text()]
        check(not utan_ph, "mallarna har platshållaren installern fyller i", ", ".join(utan_ph))

        world = varld(tmp, "forsta")
        log = tmp / "calls.log"

        # En maskin som redan haft den gamla versionen: gammal mapp, gammalt id i
        # rälens layout, och en egen regel för fönstret.
        old = world / "home/.config/omarchy/plugins/custom.jira"
        old.mkdir(parents=True)
        (old / "manifest.json").write_text('{"id": "custom.jira", "name": "Jira"}')
        shell = world / "home/.config/omarchy/shell.json"
        shell.write_text('{"bar": {"layout": {"right": [{"id": "custom.jira"}, '
                         '{"id": "omarchy.audio"}]}}}')
        hypr = world / "home/.config/hypr/hyprland.lua"
        hypr.write_text('require("hypr.bindings")\n'
                        '-- GodJIRA (custom.jira): a real tile, no floating box.\n'
                        'o.window({ class = "^org.quickshell$", title = "^Jira$" }, { tile = true })\n')

        out = run(world, log)
        check(out.returncode == 0, "install.sh går igenom", (out.stderr or out.stdout).strip()[:200])

        # 1. Färdiga filer: inga platshållare kvar, ingenting från byggmaskinen.
        home = world / "home"
        fardiga = [home / ".config/systemd/user/godjira-panel.service",
                   home / ".local/share/applications/godjira.desktop"]
        for f in fardiga:
            check(f.is_file(), "%s skrivs" % f.name)
        text = "\n".join(f.read_text() for f in fardiga if f.is_file())
        kvar_ph = sorted({w.strip('",') for w in text.split() if w.startswith("@") and w.endswith("@")})
        check(not kvar_ph, "inga platshållare kvar i färdiga filer", ", ".join(kvar_ph))
        check("/home/alex" not in text, "inga spår av byggmaskinen i färdiga filer",
              next((l for l in text.splitlines() if "/home/alex" in l), ""))
        check(str(world / "repo") in text, "sökvägarna pekar på kopian som installerades")
        check("PANEL_BIND=127.0.0.1" in text, "panelen svarar bara på den här maskinen som standard")

        # 2. Namnbytet: mappen, id:t i layouten, och den gamla mappen borta.
        plug = world / "plugin"
        check((plug / "manifest.json").is_file(), "pluginen kopieras till GodJIRA.plugin")
        if (plug / "manifest.json").is_file():
            check('"GodJIRA.plugin"' in (plug / "manifest.json").read_text(),
                  "manifestets id är GodJIRA.plugin")
            check("GodJIRA.plugin" in (plug / "BarWidget.qml").read_text(),
                  "widgeten registrerar sig under det nya id:t")
            shim = plug / "bin/jira_flow.py"
            check(shim.is_file() and str(world / "repo") in shim.read_text() and os.access(shim, os.X_OK),
                  "shimmen pekar på kopian och går att köra")
        check(not old.exists(), "gamla custom.jira-mappen tas bort")
        ids = json.dumps(json.loads(shell.read_text()))
        check("custom.jira" not in ids and "GodJIRA.plugin" in ids,
              "rälens layout byter namn med", ids[:120])
        check(ids.index("GodJIRA.plugin") < ids.index("omarchy.audio"),
              "widgeten behåller sin plats i baren")
        calls = log.read_text()
        check("plugin validate" in calls and "enable --now godjira-panel.service" in calls,
              "pluginen valideras och tjänsten slås på")

        # 3. Fönsterregeln: in, gammal regel ut, och bara en gång.
        lua = hypr.read_text()
        check("godjira-window-rule" in lua, "fönsterregeln läggs in i hyprland.lua")
        check("float = true, center = true" in lua, "regeln flyter och centrerar")
        check(lua.count("org.quickshell") == 1, "bara en regel för samma fönster",
              "antal rader: %d" % lua.count("org.quickshell"))
        check("hyprctl reload" in calls, "hyprland laddas om efteråt")
        out2 = run(world, log)
        check(out2.returncode == 0 and hypr.read_text().count("godjira-window-rule") == 1,
              "en andra körning lägger inte regeln en gång till")
        check("already in" in out2.stdout, "och säger att den redan finns", out2.stdout[-200:])

        # 4. Egna val slår igenom, och en andra körning är ofarlig.
        out3 = run(world, log, {"PANEL_PORT": "9000", "PANEL_BIND": "0.0.0.0"})
        unit = (home / ".config/systemd/user/godjira-panel.service").read_text()
        check(out3.returncode == 0 and "PANEL_PORT=9000" in unit and "PANEL_BIND=0.0.0.0" in unit,
              "PANEL_PORT och PANEL_BIND slår igenom vid omkörning")
        check("127.0.0.1:9000" in (home / ".local/share/applications/godjira.desktop").read_text(),
              "ikonen följer med till den nya porten")

        # 4b. En alldeles färsk maskin: inget id i layouten alls -> widgeten skall slås på.
        fresh = varld(tmp, "farsk")
        (fresh / "home/.config/omarchy/shell.json").write_text('{"bar": {"layout": {"right": []}}}')
        log2 = tmp / "farsk.log"
        out5 = run(fresh, log2)
        check(out5.returncode == 0 and "enable GodJIRA.plugin" in log2.read_text(),
              "widgeten slås på när layouten är tom", out5.stdout[-160:])

        # 5. Utan omarchy, utan n8n och utan hyprland.lua: resten skall gå igenom.
        world2 = varld(tmp, "utan")
        shutil.rmtree(world2 / "home/.config/hypr")
        out4 = run(world2, log, omarchy=False, n8n=False)
        check(out4.returncode == 0, "utan omarchy, n8n och hyprland går installationen igenom",
              (out4.stderr or "").strip()[:200])
        check("skipping the bar plugin" in out4.stdout, "pluginen hoppas över")
        check("n8n is not installed" in out4.stdout, "flödet hoppas över")
        check("no " in out4.stdout and "hyprland.lua" in out4.stdout, "fönsterregeln hoppas över")
        check((world2 / "home/.config/systemd/user/godjira-panel.service").is_file(),
              "panelen installeras ändå")
        e = (world2 / "home/.local/share/applications/godjira.desktop").read_text()
        check("Exec=xdg-open" in e, "utan omarchy öppnar ikonen en webbläsare i stället")
        check(not (world2 / "plugin").exists(), "ingen plugin-mapp skrivs utan skalet")
        check(not (world2 / "home/.config/systemd/user/n8n.service").exists(),
              "ingen n8n-tjänst skrivs utan n8n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FEL:
        print("\n%d fel: %s" % (len(FEL), "; ".join(FEL)))
        return 1
    print("install.sh: allt på plats, ingenting från byggmaskinen följer med")
    return 0


if __name__ == "__main__":
    sys.exit(main())
