#!/bin/sh
# The flow, as one JSON line for an orchestrator.
#
# n8n's Execute Command node throws away stdout when a command exits non-zero,
# and this flow uses its exit code as an answer: 1 nothing to take, 2 error,
# 3 someone else's issue and needs a second press. The wrapper therefore always
# exits 0 and puts the code inside the payload:
#
#   {"exitCode": 0, "payload": {...}, "raw": "..."}
#
# payload is the parsed JSON the CLI printed, or null when it printed lines
# instead (journal, current) or nothing at all. raw is the text either way -- a
# caller that sees payload null and raw empty knows the command died before it
# could say anything.
#
#   flow-call.sh flow   next --dry-run --json   -> jira_flow.py
#   flow-call.sh bridge journal 20              -> jira_bridge.py
#   flow-call.sh gh     search prs --author=@me -> gh, the hub's GitHub pane
#
# ponytail: shell + python3 rather than a Node client, because the CLIs are the
# only writer and this must not grow a second opinion about the flow. gh carries
# its own credential (the keyring), so it needs no config here either.
set -u
root=$(cd "$(dirname "$0")/../.." && pwd) || exit 0

case "${1:-flow}" in
	gh)
		shift
		out=$(gh "$@" 2>&1)
		;;
	sites)
		shift
		out=$(cd "$root" && python3 bin/jira_sites.py "$@" 2>&1)
		;;
	agents)
		shift
		out=$(cd "$root" && python3 bin/jira_agents.py "$@" 2>&1)
		;;
	bridge)
		shift
		out=$(cd "$root" && python3 bin/jira_bridge.py "$@" 2>&1)
		;;
	*)
		shift
		out=$(cd "$root" && python3 bin/jira_flow.py "$@" 2>&1)
		;;
esac
code=$?

printf '%s' "$out" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    payload = json.loads(raw)
except Exception:
    payload = None
print(json.dumps({"exitCode": int(sys.argv[1]), "payload": payload, "raw": raw},
                 ensure_ascii=False))' "$code"
