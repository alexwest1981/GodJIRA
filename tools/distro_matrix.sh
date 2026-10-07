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
# En bild vars svit faller -- eller som inte går att hämta -- räknas som fel och
# matrisen slutar 1. Förr kunde en röd svit se grön ut: `|| { echo FEL; }` lämnar
# status 0, och röret genom tail åt containerns utgångskod (mätt: sviten föll i
# rockylinux:9 medan det här jobbet var grönt).
set -u

root=$(cd "$(dirname "$0")/.." && pwd)
images=${*:-"debian:stable ubuntu:22.04 ubuntu:24.04 fedora:latest alpine:latest rockylinux:9"}
skip=${SKIP:-}
failures=0
checked=0

for img in $images; do
	case " $skip " in *" $img "*) continue ;; esac
	checked=$((checked + 1))
	printf '\n===== %s\n' "$img"
	# Utdata fångas i en variabel i stället för att röras genom tail, så att
	# containerns utgångskod bär hela vägen hit.
	report=$(docker run --rm -v "$root":/app -w /app -e HOME=/tmp/h -e LC_ALL=POSIX "$img" sh -c '
		# Base images differ: Debian ships no python at all, Fedora no git. Install
		# both through whatever the image itself uses -- the point is to test the
		# app on that distro, not that its base image is bare.
		fail=0
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
		bad=$(python3 -m py_compile $(find bin panel tools web -name "*.py") 2>&1 | head -3)
		if [ -n "$bad" ]; then
			echo "  SYNTAXFEL:"; echo "$bad" | sed "s/^/    /"; fail=1
		fi
		# 2. The suites, each printing its own last line.
		for t in panel/test_i18n.py panel/test_guard.py panel/test_coldstart.py; do
			printf "  %-24s " "$(basename $t)"
			python3 "$t" >/tmp/t.log 2>&1 && tail -1 /tmp/t.log || { echo "FEL"; tail -3 /tmp/t.log | sed "s/^/    /"; fail=1; }
		done
		printf "  %-24s " "jira_flow --selftest"
		python3 bin/jira_flow.py --selftest >/tmp/s.log 2>&1 && tail -1 /tmp/s.log || { echo "FEL"; tail -3 /tmp/s.log | sed "s/^/    /"; fail=1; }
		# 3. Boot the panel and ask it for strings -- the whole point of the app.
		PANEL_PORT=8791 python3 panel/server.py >/tmp/p.log 2>&1 &
		sleep 8
		printf "  %-24s " "panelen svarar"
		# Proben som fil: citattecken inuti citattecken gick sonder i skalet och
		# SyntaxError lades ut som "panelen svarar inget", vilket ar tva helt olika fel.
		cat >/tmp/probe.py <<'"'"'PY'"'"'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8791/api/strings?lang=en", timeout=15).read()[:90].decode())
PY
		if python3 /tmp/probe.py >/tmp/l.log 2>/tmp/l.err; then
			head -c 90 /tmp/l.log; echo
		else
			echo "INGET SVAR"; sed "s/^/    fel: /" /tmp/l.err | tail -3
			tail -3 /tmp/p.log | sed "s/^/    panel: /"; fail=1
		fi
		# 4. install.sh on a machine with no Omarchy and no systemd user session.
		printf "  %-24s " "install.sh utan Omarchy"
		if HOME=/tmp/h PLUGIN_DIR=/tmp/h/plugins sh install.sh >/tmp/i.log 2>&1; then
			echo "exit 0"
		else
			echo "exit $? -- $(tail -2 /tmp/i.log | head -1)"; fail=1
		fi
		exit $fail
	' 2>&1)
	rc=$?
	printf '%s\n' "$report" | tail -20
	if [ "$rc" -ne 0 ]; then
		failures=$((failures + 1))
		printf '  ^ %s slutade %s\n' "$img" "$rc"
	fi
done

if [ "$failures" -ne 0 ]; then
	printf '\n%d av %d bild(er) hade ett fel -- matrisen är inte grön.\n' "$failures" "$checked"
	exit 1
fi
printf '\n%d bild(er), inga fel.\n' "$checked"
