#!/bin/sh
# Install GodJIRA: the panel (with its user service), the menu icon, the window
# rule, and the Omarchy bar plugin when the shell is here. n8n is a bonus: the
# flow gets a service only if n8n is already installed on this machine.
#
#   ./install.sh                    -> http://127.0.0.1:8788, this machine only
#   PANEL_BIND=0.0.0.0 ./install.sh -> also reachable on the local network
#   PANEL_PORT=9000 ./install.sh    -> another port
#   PLUGIN_DIR=/tmp/t ./install.sh  -> the plugin copy somewhere else (a test)
#
# Nothing here needs root: the panel runs as a user service under your own
# account. Re-running is safe — the service, the icon and the window rule are
# written again or left alone, the plugin copy is refreshed.
#
# Why every file is rewritten rather than copied: the service, the two .desktop
# files and n8n's unit were made on one machine and carry its home directory, its
# uid, its node version and its port. Installing them as they are would start the
# service in someone else's home. The templates hold @ROOT@, @PYTHON@, @PORT@,
# @BIND@, @LAUNCHER@, @N8N@ and @N8N_PATH@, and this script fills them in with
# what it finds here. A unit with an @-word still in it fails loudly in
# `systemctl --user status` instead of quietly running the wrong thing.
set -eu

root=$(cd "$(dirname "$0")" && pwd)
port=${PANEL_PORT:-8788}
bind=${PANEL_BIND:-127.0.0.1}
n8n_port=5678
uid=$(id -u)
plug=${PLUGIN_DIR:-$HOME/.config/omarchy/plugins/GodJIRA.plugin}
old_plug=$HOME/.config/omarchy/plugins/custom.jira
say() { printf '%s\n' "$*"; }

# Vad den här maskinen har, på ett ställe: allt nedan fyller i sökvägar ur det här.
# Provet hittade varför -- fill() läste $launcher innan den fanns och skriptet
# stannade på "unbound variable" med set -u.
python=$(command -v python3 || true)
n8n_bin=$(command -v n8n || true)
launcher=$(command -v omarchy-launch-webapp || echo "xdg-open")
# if-satser, inte "cmd && var=yes": under set -e dör en && -lista som misslyckas,
# alltså precis när verktyget saknas -- det vill säga på kollegans maskin.
have_omarchy=no
if command -v omarchy >/dev/null 2>&1; then have_omarchy=yes; fi
have_systemctl=no
if command -v systemctl >/dev/null 2>&1; then have_systemctl=yes; fi

# fill <template> <dest> [extra sed rules...]
fill() {
	src=$1
	dst=$2
	shift 2
	mkdir -p "$(dirname "$dst")"
	sed -e "s#@ROOT@#$root#g" \
		-e "s#@PYTHON@#$python#g" \
		-e "s#@PORT@#$port#g" \
		-e "s#@BIND@#$bind#g" \
		-e "s#@LAUNCHER@#$launcher#g" \
		-e "s#@N8N@#$n8n_bin#g" \
		-e "s#@N8N_PATH@#$(dirname "${n8n_bin:-/nonexistent}")#g" \
		-e "s#@N8N_PORT@#$n8n_port#g" \
		"$@" "$src" > "$dst"
}

# 1. The panel itself: stdlib only, so python3 is the whole dependency list.
[ -n "$python" ] || {
	say "python3 is missing. Install it first (python3 --version should work), then run this again."
	exit 1
}
[ -f "$root/panel/server.py" ] || {
	say "panel/server.py is missing — run this from a clone of the repo, not a copy of install.sh alone."
	exit 1
}

# 2. The service. Bound to localhost unless asked otherwise: the panel shows your
#    Jira token's data, and a machine on the office network should not be handed
#    that by default.
fill "$root/panel/godjira-panel.service" "$HOME/.config/systemd/user/godjira-panel.service"
if [ "$have_systemctl" = yes ]; then
	systemctl --user daemon-reload
	systemctl --user enable --now godjira-panel.service
	say "service: godjira-panel (port $port, listening on $bind)"
else
	say "systemctl is missing: the unit is written but not started"
fi

# 3. The menu icon. omarchy-launch-webapp opens it as its own window; anywhere
#    else a browser tab is the honest fallback.
fill "$root/panel/godjira.desktop" "$HOME/.local/share/applications/godjira.desktop"
# Menyn läser en databas över .desktop-filerna på en del skrivbord; finns verktyget,
# uppdatera den, annars syns ikonen först efter nästa inloggning.
if command -v update-desktop-database >/dev/null 2>&1; then
	update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi
say "menu icon: GodJIRA (opens http://127.0.0.1:$port)"

# 4. The window rule: a tile by default, floating and centred with this rule.
#    Appended with its marker comment, and an older GodJIRA rule for the same
#    window is removed first — two rules for one window contradict each other.
hypr=$HOME/.config/hypr/hyprland.lua
if [ -f "$hypr" ]; then
	if grep -q 'godjira-window-rule' "$hypr"; then
		say "window rule: already in $hypr"
	else
		[ -f "$hypr.bak.godjira" ] || cp "$hypr" "$hypr.bak.godjira"
		grep -v 'o\.window({ class = "\^org\.quickshell\$", title = "\^Jira\$" }' "$hypr" > "$hypr.godjira.new" &&
			mv "$hypr.godjira.new" "$hypr"
		printf '\n' >> "$hypr"
		cat "$root/window-rule.lua" >> "$hypr"
		say "window rule: float and centre added to $hypr"
	fi
	command -v hyprctl >/dev/null 2>&1 &&
		{ hyprctl reload >/dev/null 2>&1 || say "run hyprctl reload yourself when the desktop is free"; }
else
	say "no $hypr: skipping the window rule (only the panel's own window needs it)"
fi

# 5. The plugin, when the Omarchy shell is here. The shell hot-reloads a local
#    plugin on every write inside its folder, so the panel must live outside it
#    and the two CLIs are pointed at this repo through generated shims.
if [ "$have_omarchy" = yes ]; then
	# The id was custom.jira: the folder, and the bar layout that names it, both
	# move to the new name. The folder goes only if it really is ours.
	if [ -d "$old_plug" ] && grep -qi '"jira"' "$old_plug/manifest.json" 2>/dev/null; then
		rm -rf "$old_plug"
		say "plugin: removed the old folder $old_plug"
	fi
	was_new=no
	[ -d "$plug" ] || was_new=yes
	mkdir -p "$plug"
	for p in manifest.json BarWidget.qml JiraPanel.qml components views i18n assets; do
		rm -rf "$plug/$p"
		cp -r "$root/$p" "$plug/$p"
	done
	mkdir -p "$plug/bin"
	for name in jira_bridge.py jira_flow.py; do
		cat > "$plug/bin/$name" <<EOF
#!/usr/bin/env python3
"""Generated by install.sh. The real CLI lives in $root/bin/$name."""
import os, sys
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"   # en .pyc här är en filändring -> skalet laddar om
os.execv(sys.executable, [sys.executable, "$root/bin/$name", *sys.argv[1:]])
EOF
		chmod +x "$plug/bin/$name"
	done
	omarchy plugin validate "$plug"
	omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
	if [ "$was_new" = yes ]; then
		say "plugin: installed in $plug (add the widget to the bar in Omarchy's settings)"
	else
		say "plugin: refreshed in $plug"
	fi

	# The bar layout names the plugin by id. Same rename, or the widget is simply
	# gone from the bar after this. A backup first, and the file must still parse.
	shell_json=$HOME/.config/omarchy/shell.json
	if [ -f "$shell_json" ] && grep -q '"custom\.jira"' "$shell_json"; then
		cp "$shell_json" "$shell_json.bak.godjira"
		sed 's/"custom\.jira"/"GodJIRA.plugin"/g' "$shell_json" > "$shell_json.godjira.new" &&
			mv "$shell_json.godjira.new" "$shell_json"
		if "$python" -c 'import json,sys; json.load(open(sys.argv[1]))' "$shell_json" 2>/dev/null; then
			say "bar layout: custom.jira -> GodJIRA.plugin (kept its place in the bar)"
		else
			cp "$shell_json.bak.godjira" "$shell_json"
			say "bar layout: could not be rewritten (left as it was) — add the widget by hand"
		fi
	elif ! grep -q '"GodJIRA\.plugin"' "$shell_json"; then
		# Förstagångsinstallation: id:t finns inte i layouten alls, så widgeten syns
		# inte i baren förrän den läggs dit. enable skriver in den i sin standardsektion.
		omarchy plugin enable GodJIRA.plugin >/dev/null 2>&1 &&
			say "bar layout: GodJIRA.plugin enabled in the bar" ||
			say "bar layout: enable the widget in Omarchy's settings (Setup > Plugins)"
	else
		say "bar layout: nothing to rename"
	fi
else
	say "omarchy is not on PATH: skipping the bar plugin (the panel works without it)"
fi

# 6. The flow, only where n8n already is. Its unit names a node version and the
#    mise shims on the machine it was made on, so the binary is looked up here.
if [ -n "$n8n_bin" ]; then
	fill "$root/n8n/n8n.service" "$HOME/.config/systemd/user/n8n.service"
	fill "$root/panel/godjira-flodet.desktop" "$HOME/.local/share/applications/godjira-flodet.desktop"
	[ "$have_systemctl" = no ] || systemctl --user enable --now n8n.service
	say "flow: n8n found at $n8n_bin, its service and icon are installed too"
else
	say "flow: n8n is not installed on this machine — skipping it (the panel works without it)"
fi

# 7. Does it answer? An installer that says "done" without looking is worth
#    nothing: the first run has no token, so the panel must come up and ask.
if command -v curl >/dev/null 2>&1; then
	tries=0
	while ! curl -fsS -m 3 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; do
		tries=$((tries + 1))
		[ "$tries" -ge 15 ] && break
		sleep 1
	done
	if [ "$tries" -lt 15 ]; then
		say "the panel answers on http://127.0.0.1:$port"
	else
		say "the panel does not answer yet: systemctl --user status godjira-panel.service"
	fi
fi

# ---------------------------------------------------------------- Archify (valfritt)
# Flödeskartan ritas av Archify. Det är tredjepartskod, så den hämtas hit -- aldrig
# in i repot -- och hämtas på en fast commit som är granskad, inte på en gren som
# rör sig. Utan node (eller utan nät) hoppar vi över: panelen ritar flödet på sin
# egen rityta i stället, precis som förut.
vendor="${XDG_DATA_HOME:-$HOME/.local/share}/godjira/vendor"
archify="$vendor/archify"
if ! command -v node >/dev/null 2>&1; then
	say "node missing: the flow map stays on the panel's own canvas"
elif [ -f "$archify/archify/bin/archify.mjs" ]; then
	say "Archify is already in place ($archify)"
else
	pin="d5a1333d7447c866a765adac7d4d062f2f02e4d2"
	mkdir -p "$vendor"
	rm -rf "$archify"
	if git init -q "$archify" 2>/dev/null &&
	   git -C "$archify" remote add origin https://github.com/tt-a1i/archify 2>/dev/null &&
	   git -C "$archify" fetch -q --depth 1 origin "$pin" 2>/dev/null &&
	   git -C "$archify" checkout -q FETCH_HEAD 2>/dev/null &&
	   [ "$(git -C "$archify" rev-parse HEAD 2>/dev/null)" = "$pin" ] &&
	   [ -f "$archify/archify/bin/archify.mjs" ]; then
		say "Archify fetched at $pin (MIT, in $archify -- out of the repo)"
	else
		rm -rf "$archify"
		say "could not fetch Archify: the flow map stays on the panel's own canvas"
	fi
fi

say ""
say "Open http://127.0.0.1:$port and connect the two accounts in Kom igång:"
say "  * a Jira API token (stored in the session keyring, never in a file here)"
say "  * GitHub, with gh auth login  (the GitHub parts stay empty without it)"
[ "$bind" = "0.0.0.0" ] || say "Reachable on this machine only. PANEL_BIND=0.0.0.0 ./install.sh opens it on the network."
