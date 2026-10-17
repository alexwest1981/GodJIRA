#!/usr/bin/env python3
"""bridge_choice -- panelens `bridge`-dörr, med källan vald i panelen.

Panelen (och n8n) talar alltid med `bridge`; vilken tracker som svarar avgörs av
`backend` i ~/.config/godjira/panel.json. Läsningarna (snapshot, status, journal)
finns i båda bryggorna och går till den valda. Allt annat -- inloggning, nycklar,
språk, behörigheter -- hör till Jira-kontot och går alltid till jira_bridge, för
det är kontots yta och inte ärendenas.

    python3 bin/bridge_choice.py snapshot     # jira eller paperclip, enligt panelen
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = pathlib.Path.home() / ".config" / "godjira" / "panel.json"

# Kommandon som båda källorna kan svara på. Resten är Jirans.
SHARED = {"snapshot", "status", "journal"}


def backend() -> str:
    """Vald källa. En trasig fil betyder Jira -- standarden, aldrig en tyst tredje väg."""
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "jira"
    value = str((data or {}).get("backend") or "jira").strip().lower()
    return value if value in ("jira", "paperclip") else "jira"


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    script = "paperclip_bridge.py" if backend() == "paperclip" and command in SHARED else "jira_bridge.py"
    target = HERE / script
    os.execv(sys.executable, [sys.executable, str(target), *argv])
    return 1  # os.execv kommer aldrig tillbaka


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main(sys.argv[1:]))
