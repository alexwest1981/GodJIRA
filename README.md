# GodJIRA

<p align="center">
  <img src="assets/godjira-logo.jpeg" alt="GodJIRA" width="320">
</p>

A Jira client inside your Omarchy shell. Board with drag-and-drop, backlog and
summary views in one floating window, kept fresh by a periodic refresh. Add and
delete issues when you have the right permissions.

- **Board view** – kanban columns per status. Drag a card between columns to
  change its status (fallback: open the card and pick a status under
  *"Flytta ärendet"*).
- **Detail page** – click a card to read it (summary, meta, description) and to
  move or delete it (two-click confirm for delete).
- **"Alla / Mina" filter** – the Board and Backlog headers switch between every
  issue on the team board and only the ones assigned to you (matched on your
  account email/display name). The filter is shared across the views.
- **Backlog / Summary** – remaining columns and sprint status.
- **"＋ Ny"** – full page to create an issue on the current board.
- **Change notifications** – the bar widget polls every 30 s and raises a
  desktop notification when an issue on a tracked board was added, moved,
  updated, or removed. Changes you make inside the app are excluded (the
  baseline is bumped after each of your edits). Watch one board via the board
  picker, or leave it unset to track everything.
- Mock-first: ships with a deterministic fake dataset, no credentials needed.
  A real Jira Cloud mode uses the same UI, rendering path and bridge commands.

## Install (Omarchy)

Add the plugin from git. `manifest.json` declares the id `custom.jira`, so the
repo is cloned into `~/.config/omarchy/plugins/custom.jira/` automatically:

```
omarchy plugin add https://github.com/alexwest1981/GodJIRA.git --enable
```

This validates the manifest, then enables the plugin and places the **Jira**
bar widget in the right bar section by default. Click the widget in the bar to
open the panel. A restart of the shell is not needed; plugin code hot-reloads
on save.

Managing it later:

```
omarchy bar move custom.jira --section right  # move the bar widget
omarchy plugin disable custom.jira            # hide the widget (keeps files)
omarchy plugin enable custom.jira             # re-enable after a disable
omarchy plugin update custom.jira             # pull the latest version
omarchy plugin remove custom.jira             # uninstall (removes the folder)
```

### Updating

When a new commit is published to the git repo, pull it into the installed copy:

```
omarchy plugin update custom.jira
```

This runs a `git pull` inside the plugin folder (same as
`git -C ~/.config/omarchy/plugins/custom.jira pull`). QML edits are hot-reloaded
by the shell; if the open window still looks stale after an update, restart the
shell and reopen it (`omarchy-restart-shell`, see Development notes). Updates
need the same prerequisites as install: `git` and `python3` on PATH.

Requires `git` and `python3` on PATH (the bridge is Python stdlib-only; `secret-tool`
is only needed for the real Jira Cloud mode, see below).

## Quick start (mock)

The plugin defaults to `mode: mock`. Toggle the window from the bar widget
(Jira) or with:

```
omarchy-shell shell toggle custom.jira '{}'
```

The mock dataset can be rebuilt at any time:

```
python3 ~/.config/omarchy/plugins/custom.jira/bin/jira_bridge.py mock-reset
```

## Files

```
custom.jira/
├── manifest.json            plugin manifest (panel + bar widget)
├── JiraPanel.qml            root: bridge process, connection, routing
├── BarWidget.qml            bar launcher
├── views/                   BoardView, BacklogView, SummaryView, PlaceholderView
├── components/              IssueCard, IssueDetail
└── bin/jira_bridge.py       everything network/credential related (Python stdlib only)
```

The QML never talks HTTP and never holds credentials: it shells out to
`jira_bridge.py` and reads one JSON document from stdout.

## Bridge commands

Each command prints one JSON document (`schema`, `ok`, `error`, `generatedAt`,
plus command data). Every state the UI can hit exits 0; real bugs exit 1.

```
python3 bin/jira_bridge.py status                         # mode + account / why not connected
python3 bin/jira_bridge.py snapshot                       # neutral board model for the UI
python3 bin/jira_bridge.py transitions WEB-41             # statuses the issue can move to
python3 bin/jira_bridge.py move WEB-41 "In Progress"      # change an issue's status
python3 bin/jira_bridge.py create 1 '{"summary":"...", "statusId":"progress"}'
python3 bin/jira_bridge.py delete WEB-41
python3 bin/jira_bridge.py configure '<json>'             # e.g. {"mode":"real"}
python3 bin/jira_bridge.py watch                          # diff vs baseline; notifies if changed
python3 bin/jira_bridge.py mock-touch WEB-41 done         # simulate a teammate's change (mock)
python3 bin/jira_bridge.py mock-touch MOB-24 assigneeName="Elsa W"  # ...or a field edit
python3 bin/jira_bridge.py mock-reset                     # rebuild the mock dataset
```

`watch` is called by the bar widget every 30 s. It keeps a baseline in
`~/.local/state/omarchy/jira-watch.json`; the first run only primes it. Issue
`mock-touch` (mock mode) simulates someone else editing the board so you can
watch a notification appear without a second user.

`create` needs `CREATE_ISSUES` on the board's project, `delete` needs
`DELETE_ISSUES`; the snapshot carries per-board `canAdd`/`canDelete`, which hide
"＋ Ny" / "Radera ärende" in the UI otherwise.

## Real Jira Cloud

1. Create an API token at
   https://id.atlassian.com/manage-profile/security/api-tokens
2. Configure the site and account, then switch to real mode:

```
python3 bin/jira_bridge.py configure '{"siteUrl":"https://your-domain.atlassian.net","email":"you@example.com"}'
python3 bin/jira_bridge.py configure '{"mode":"real"}'
```

3. Log in. The token is validated against `/rest/api/3/myself` and stored in the
   system keyring via `secret-tool` – never in a file. Either pass it on a temp
   path that is deleted after reading, or set it on stdin via `JIRA_TOKEN`:

```
JIRA_TOKEN=... python3 bin/jira_bridge.py login --token-file /tmp/jira-token
python3 bin/jira_bridge.py login          # falls back to keyring / JIRA_TOKEN
python3 bin/jira_bridge.py logout         # forget the credential
```

The `status` command reports when credentials are missing so the UI can show the
connect screen.

## State and configuration

| Thing | Location |
| --- | --- |
| Plugin | `~/.config/omarchy/plugins/custom.jira/` |
| Config (mode, site, email) | `~/.config/omarchy/jira.json` |
| Mock dataset | `~/.local/state/omarchy/jira-mock.json` |
| Watcher baseline | `~/.local/state/omarchy/jira-watch.json` |
| API token (real mode) | system keyring, service `custom.jira` |

Refresh interval: open the Jira widget's settings (default 30 s, min 10 s).

## Development notes

- After editing QML, restart the shell so the running engine picks the files up:
  `omarchy-restart-shell`, then reopen the window.
- Lint plugin QML against the shell UI types:

```
qmllint -I /usr/share/omarchy/shell -I /usr/lib/qt6/qml views/BoardView.qml JiraPanel.qml
```

- Shell log for runtime errors: `journalctl --user -t omarchy-shell`.
