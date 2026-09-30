#!/bin/sh
# distro_matrix -- runs the app's own suite on other distributions, in containers.
#
# Why: the code is built on Arch/Omarchy, so every assumption that only holds there
# (python version, systemd user units, secret-tool, a shell that is not bash) is
# invisible on the machine it was written on. This runs the same tests each distro
# can run itself, and prints one line per check.
#
#   tools/distro_matrix.sh                     # the default set
#   tools/distro_matrix.sh debian:stable ...   # or the images you name
#
# Skips images docker cannot pull, and says so rather than passing quietly.
set -u

root=$(cd "$(dirname "$0")/.." && pwd)
images=${*:-"debian:stable ubuntu:22.04 ubuntu:24.04 fedora:latest alpine:latest rockylinux:9"}
skip=${SKIP:-}

for img in $images; do
	case " $skip " in *" $img "*) continue ;; esac
	printf '\n===== %s\n' "$img"
	docker run --rm -v "$root":/app -w /app -e HOME=/tmp/h -e LC_ALL=POSIX "$img" sh -c '
		# Base images differ: Debian ships no python at all, Fedora no git. Install
		# both through whatever the image itself uses -- the point is to test the
		# app on that distro, not that its base image is bare.
		if ! command -v python3 >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
			{ command -v apt-get >/dev/null && apt-get update -qq && apt-get install -y -qq python3 git; } ||
			{ command -v dnf >/dev/null && dnf install -y -q python3 git; } ||
			{ command -v apk >/dev/null && apk add --no-cache python3 git; } ||
			{ echo "  kunde inte installera python3/git har"; exit 3; } >/dev/null 2>&1
		fi
		command -v python3 >/dev/null 2>&1 || { echo "  ingen python3 ens efter installation"; exit 3; }
		# Containern kör som root mot en monterad kopia som ägs av en annan uid, och git
		# vägrar arbeta i ett repo med annan ägare ("dubious ownership"). Utan detta
		# svarar varje repo-fråga "not a git repository" och sviten mäter fel sak.
		mkdir -p /tmp/h && git config --global --add safe.directory /app 2>/dev/null
		printf "  %s\n" "$(python3 --version 2>&1)"
		# 1. Does every file even parse on this interpreter?
		bad=$(python3 -m py_compile $(find bin panel tools -name "*.py") 2>&1 | head -3)
		[ -n "$bad" ] && { echo "  SYNTAXFEL:"; echo "$bad" | sed "s/^/    /"; }
		# 2. The suites, each printing its own last line.
		for t in panel/test_i18n.py panel/test_guard.py panel/test_coldstart.py; do
			printf "  %-24s " "$(basename $t)"
			python3 "$t" >/tmp/t.log 2>&1 && tail -1 /tmp/t.log || { echo "FEL"; tail -3 /tmp/t.log | sed "s/^/    /"; }
		done
		printf "  %-24s " "jira_flow --selftest"
		python3 bin/jira_flow.py --selftest >/tmp/s.log 2>&1 && tail -1 /tmp/s.log || { echo "FEL"; tail -3 /tmp/s.log | sed "s/^/    /"; }
		# 3. Boot the panel and ask it for strings -- the whole point of the app.
		PANEL_PORT=8791 python3 panel/server.py >/tmp/p.log 2>&1 &
		sleep 8
		printf "  %-24s " "panelen svarar"
		if python3 -c "
import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:8791/api/strings?lang=en', timeout=15).read()[:90].decode())" >/tmp/l.log 2>/tmp/l.err; then
			head -c 90 /tmp/l.log; echo
		else
			echo "INGET SVAR"; sed "s/^/    fel: /" /tmp/l.err | tail -3
			tail -3 /tmp/p.log | sed "s/^/    panel: /"
		fi
		# 4. install.sh on a machine with no Omarchy and no systemd user session.
		printf "  %-24s " "install.sh utan Omarchy"
		HOME=/tmp/h PLUGIN_DIR=/tmp/h/plugins sh install.sh >/tmp/i.log 2>&1 &&
			echo "exit 0" || echo "exit $? -- $(tail -2 /tmp/i.log | head -1)"
	' 2>&1 | tail -16
done
