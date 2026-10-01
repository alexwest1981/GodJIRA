#!/bin/sh
# Publish one flow file to n8n, and switch it on.
#
# The panel calls this instead of asking someone to remember two commands: a flow that
# sits in n8n but is switched off does nothing, so push and activate belong together.
#
#   publish.sh autocore-flow-code-leftovers.workflow.ts
#
# A name, never a path -- and only the repo's own flow files (the panel checks once more
# before calling). n8nac lives in the mise shims, which the user service's PATH does not
# carry, so the binary is looked up here as well.
#
# ponytail: shell + the CLI, not the REST API. n8nac already owns the sync state and the
# verification; a second client here would be a second opinion about the same thing.
set -u
root=$(cd "$(dirname "$0")/../.." && pwd) || exit 2
case "${1:-}" in
	"" | */* | *..*) echo "usage: publish.sh <flow-file>" >&2; exit 2 ;;
esac
file="n8n/workflows/$1"
[ -f "$root/$file" ] || { echo "no such flow file: $1" >&2; exit 2; }

bin=$(command -v n8nac 2>/dev/null || true)
[ -n "$bin" ] || bin="${HOME}/.local/share/mise/shims/n8nac"
[ -x "$bin" ] || { echo "n8nac not found (looked on PATH and in ~/.local/share/mise/shims)" >&2; exit 2; }

cd "$root" || exit 2
"$bin" push "$file" --verify || exit 2
# The push writes the workflow's own id back into the file, so it is read from there --
# and the first match is the workflow's, the later ones are the nodes'.
id=$(sed -n "s/^[[:space:]]*id: '\([^']*\)'.*/\1/p" "$file" | head -1)
[ -n "$id" ] || { echo "the flow file carries no id" >&2; exit 2; }
"$bin" workflow activate "$id"
