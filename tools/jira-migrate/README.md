# jira-migrate

Jira-projektet ut ur Jira och in i en lokal Paperclip, med kommentarer och struktur kvar.

    python3 jira_export.py --project SCRUM            # -> ~/.local/share/jira-export/SCRUM/issues.json
    python3 jira_import.py                            # -> Paperclip på 127.0.0.1:3100

`jira_export.py` läser bara Jira (token ur nyckelringen, samma väg som `bin/jira_bridge.py`)
och laddar hem bilagorna till `attachments/<nyckel>/`. `jira_import.py` postar via Paperclips
REST-API och är idempotent: `imported.json` i exportkatalogen minns vilka nycklar som är inne,
så en omkörning hoppar över dem. Beskrivningarna konverteras från Atlassians ADF till markdown,
och varje issue får en rad högst upp med sin Jira-nyckel och länk.

Kör om båda innan Jira-abonnemanget sägs upp — exporten är försäkringen.

Paperclip självt: `paperclipai service status|restart|logs`, webben på http://127.0.0.1:3100,
data i `~/.paperclip/instances/default`.
