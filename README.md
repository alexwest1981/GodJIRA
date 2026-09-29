# GodJIRA

<p align="center">
  <img src="assets/godjira-jira-ide.jpg" alt="Jira och IDE:erna, sammanbundna av GodJIRA" width="760">
</p>

A Jira client inside your Omarchy shell. Board with drag-and-drop, backlog and
summary views in one floating window, kept fresh by a periodic refresh. Add and
delete issues when you have the right permissions.

- **Board view** – kanban columns per status, scoped to a sprint the way
  Jira's board is. The picker under the header switches between *Aktiv* (the
  running sprint, the default), any single sprint, and *Alla* (every issue on
  the board). Drag a card between columns to change its status (fallback: open
  the card and pick a status under *"Flytta ärendet"*).
- **Detail page** – click a card to read it (summary, meta, description) and to
  move or delete it (two-click confirm for delete).
- **"Alla / Mina" filter** – the Board and Backlog headers switch between every
  issue on the team board and only the ones assigned to you (matched on your
  account email/display name). The filter is shared across the views.
- **Backlog** – the shape of Jira's backlog page: one section per sprint
  (oldest start date first) with its dates, state and count, then the unplanned
  backlog as the last section. Sections fold with their `+`/`-` button. The
  count reads `open kvar av total`, because Jira's own header shows only the
  open number: a 35-issue sprint whose 6 issues were completed in an earlier
  sprint is "29 work items" there.
- **Settings** – its own view instead of a connection page you could stumble
  into: language (nine languages, or follow the system), the locked connection
  card, start view and version. Opening it changes nothing.
- **Summary** – counts per status, the running sprint, who is carrying what,
  and the most recently updated issues.
- **Timeline** – the board's sprints as lanes on a date axis, the backlog as the
  last lane. The lanes share the width the window actually has (a long sprint
  gets a wider lane than a short one, and the row never runs past the edge), so
  nothing has to be scrolled sideways. Each lane is as wide as its sprint window and fills up as the
  sprint runs. "⇄" on a card moves the issue to another sprint (or back to the
  backlog); the same choice sits on the detail page.
- **Reports** – per sprint: burndown against the ideal line, completed / left /
  added / punted counts, the issue lists behind them, and velocity once sprints
  have closed. Reads Jira's own chart and sprint-report endpoints.
- **Development** – for the selected issue: pull requests, branches, commits and
  builds from Jira's dev-status API, plus the issue's change history (which is
  shown whether or not a git provider is connected). On a site with no
  integration it says so instead of showing empty lists.
- **Activity** – the project's most recently changed issues, grouped by day,
  with what changed last ("status: To Do -> In Review") when the site keeps
  that history.
- **Comments and editing** – the detail page lists an issue's comments and
  posts new ones, and edits summary, description, priority, assignee and story
  points. Only the fields you actually changed are sent to Jira.
- **Connection lock** – once a site, account and token are stored, the
  connection screen opens read-only: site, account, mode and "token in the
  keyring", with no field to submit by accident. Changing it takes a deliberate
  *Skapa ny anslutning* (prefills the address, so a fresh token is enough) or
  *Koppla från* (two-step; removes the token, keeps the address). See
  "Anslutningslåset".
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

### Floating window (Hyprland)

The panel is a normal Quickshell window, so Hyprland tiles it like any other
app unless a window rule floats it. Add the rule that ships with the plugin
(`window-rule.lua`) once to the end of `~/.config/hypr/hyprland.lua`:

```lua
o.window({ class = "^org.quickshell$", title = "^Jira$" }, { float = true, center = true })
```

Save the file (Hyprland reloads on save) or run `hyprctl reload`. The window
then floats and centers, and honours its own size limits (minimum 720x500,
fitted to the screen). Without this rule the window opens tiled instead of
floating.

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
├── views/                   SummaryView, BoardView, BacklogView, TimelineView,
│                            ReportsView, DevelopmentView, ActivityView,
│                            SettingsView (language, connection, start view)
├── components/              IssueCard, IssueDetail
├── i18n/                    en.json (source) + sv, de, fr, es, it, pt, nl, pl
├── window-rule.lua          Hyprland rule that floats the Jira window (see README)
├── panel/                   the hub's front door: server.py + index.html (no deps,
│                            no build step) + check.py + test_import.py (the upload
│                            door: a document in, a proposal out, nothing written)
├── panel/fixtures/          bestallarkrav.pdf, the 860-byte document the check parses
├── assets/godjira.svg      the mark: Inkscape's A4 page cropped to the drawing (the
│                            artwork sat outside its own viewBox, so a plain link to
│                            the original renders nothing). 76 % of the height is the
│                            creature, the last 21 % the wordmark: the rail shows the
│                            creature alone, because 30 px turns the wordmark to mush.
├── n8n/                     the flow on a canvas: bin/flow-call.sh (the seam),
│                            workflows/*.workflow.ts, README.md
└── bin/jira_bridge.py       everything network/credential related (Python stdlib only)
    bin/jira_mcp.py          the same, over MCP, for an agent in an editor
    bin/jira_flow.py         take the next critical issue; editor shims + commit rule
    bin/i18n_check.py        translation check: same keys, same placeholders
```

The QML never talks HTTP and never holds credentials: it shells out to
`jira_bridge.py` and reads one JSON document from stdout.

## The panel, and the doors around the same core

The decisions live in one place (`jira_flow.py`, with `jira_bridge.py` as the only
thing that touches Jira). Everything else is a door onto those files:

| Door | What it is | Where it runs |
|---|---|---|
| the QML panel | the Omarchy bar widget | this desktop only |
| `bin/jira_flow.py` | the CLI, and the editor shims/commit hook | anywhere (Python stdlib) |
| `bin/jira_mcp.py` | MCP over stdio for an agent in an editor | anywhere |
| `n8n/` | the flow on a canvas, with history and a schedule -- parked, for visualizing a flow when that helps | this machine, at `http://omarchy.local:5678` |
| `panel/` | **the hub**: Jira's board + backlog, GitHub's repos, the flow's next pick, in one page | this machine, at `http://omarchy.local:8788` |

The hub does not go through n8n: it calls the same CLIs through
`n8n/bin/flow-call.sh` (the seam both doors share), so it can show something new
but never believe something new. Its first answer costs ~9 s (four CLI calls) and a browser refresh
costs nothing for 60 s (`PANEL_TTL`).

```sh
systemctl --user status godjira-panel      # alive, and starts at login
python3 panel/check.py                     # one check: answers, and numbers agree
```

### Five views, Plane's anatomy

The hub is modelled on Plane (`makeplane/plane`), which is the reference for how it
should read — the *anatomy*, not the colours: the palette comes from the mark
(`assets/godjira.svg`), so the accent is the logo's own teal (measured 5.47:1 on the
background) and the status colours were moved off it rather than near it. Plane's
blue belongs to Plane: an icon rail plus a nav sidebar with section labels, a breadcrumb header
with a view switcher, columns whose heading is a state dot + name + count, and work-item
cards carrying a type glyph, the id, the title, then one row of priority indicator,
state pill and teammate avatar.

| View | What it holds |
|---|---|
| Översikt | the numbers, the flow's next pick (`jira_flow.py next --dry-run`), what the flow itself has written, the repos touched last |
| Tavlan | the board: `issues` + `backlog` merged, because the live work sits in the backlog whenever no sprint is active |
| Uppgifter | Plane's table: every work item from **both** sources in one place, with source, id, status, priority, assignee, sprint |
| Repon | all repositories, their visibility, language, and any open PRs/issues on them |
| Importera | a document in, a proposal out, and nothing written until a human has ticked off what should be there |

The table is where the two sources actually meet, so GitHub's PRs and issues are shaped
like work items rather than given their own screen. Reading is what the hub does; the
one write it has is *Importera*, and only ever the list Alex himself ticked off (below).

### A document in, issues out — but nothing is written until it is approved

*Importera* exists because reading a spec and typing twelve issues is where work goes
to die — and because a model's first answer is not a decision. Drop a **pdf, docx, odt,
xlsx, pptx** or a text file (or several) on the view, say what the document is for, and
press *Tolka → förslag*. What comes back is a **proposal**, not issues: each row shows
type, summary and the whole description, all rows ticked, and under them a button that
says exactly what it will do — `Skapa 4 ärenden i SCRUM (skarpt läge)`. Untick what is
wrong, press it, confirm the count, and *then* the flow writes. Nothing is sent to Jira
by the parse step, so a bad proposal costs a second parse, not a cleanup.

Two server-side rules make that stick (`panel/server.py`):

* the client may only send **which** of the proposed items to write, by number —
  never issue text of its own, and the list it picks from lives in the server's
  memory, not in the page;
* the token from a parse is **one-shot** and lives an hour. An approval is used
  once, so a half-written run cannot be replayed, and an unknown token is a 404.

The runnable check is `panel/test_import.py`:

```sh
python3 panel/test_import.py                    # parses panel/fixtures/bestallarkrav.pdf,
                                                # checks the guards, writes nothing
GODJIRA_ALLOW_WRITE=1 python3 panel/test_import.py   # also writes -- only on a board
                                                     # you are willing to see issues on
```

The core's own part of the same path — `plan --proposal FILE --create`, the list the
panel approved, without asking the agent a second time — is covered by
`python3 bin/jira_flow.py --selftest` (16 checks, offline).

### It is a window, not a floating box

The hub opens as a real application window: `panel/godjira.desktop` (and
`panel/godjira-flodet.desktop` for the n8n canvas) go through Omarchy's own
`omarchy-launch-webapp`, which launches the default browser with `--app=<url>` —
no tabs, no address bar, and **Hyprland tiles it like any other window**. The QML
panel is a tile too: it used to be floated by a rule in `~/.config/hypr/hyprland.lua`
(now `{ tile = true }`, same line as Steam). The floating recipe is kept in
`window-rule.lua` for whoever wants the old behaviour back.

```sh
cp panel/godjira*.desktop ~/.local/share/applications/   # then it is in the launcher
```

On other operating systems the same URL is the whole story: open
`http://<machine>:8788` in a browser, or use the browser's own *Install as app /
Open as window* to get the same chrome-less window. Nothing else is
platform-specific — that was the point of keeping the core in one place.

`check.py` is the one worth keeping: it fails when the panel is alive but blank,
which is exactly what happens if the board — settled work — is shown without the
backlog that holds the live work during a sprint with no active sprint.

## Bridge commands

Each command prints one JSON document (`schema`, `ok`, `error`, `generatedAt`,
plus command data). Every state the UI can hit exits 0; real bugs exit 1.

```
python3 bin/jira_bridge.py status                         # mode + account / why not connected
python3 bin/jira_bridge.py snapshot                       # neutral board model for the UI
python3 bin/jira_bridge.py transitions WEB-41             # statuses the issue can move to
python3 bin/jira_bridge.py move WEB-41 "In Progress"      # change an issue's status
python3 bin/jira_bridge.py create 1 '{"summary":"...", "statusId":"progress"}'
python3 bin/jira_bridge.py delete WEB-41 --yes          # --yes krävs; kopia sparas först
python3 bin/jira_bridge.py journal 20                    # vad verktyget har skrivit, nyaste först
python3 bin/jira_bridge.py trash WEB-41                  # kopiorna som togs före radering/ändring
python3 bin/jira_bridge.py restore WEB-41                # återskapa ur kopian
python3 bin/jira_bridge.py assign WEB-41 7               # into a sprint (or "backlog")
python3 bin/jira_bridge.py comments WEB-41               # read the comment thread
python3 bin/jira_bridge.py comment WEB-41 "Ser bra ut"   # post a comment
python3 bin/jira_bridge.py update WEB-41 '{"priorityName":"High","storyPoints":5}'
python3 bin/jira_bridge.py options WEB                  # assignable people, priorities, types
python3 bin/jira_bridge.py activity WEB 25              # recently changed issues
python3 bin/jira_bridge.py report 1 7                   # burndown + sprint report + velocity
python3 bin/jira_bridge.py dev WEB-41                   # git/PR/build status + issue history
python3 bin/jira_bridge.py attachments WEB-41             # files on the issue
python3 bin/jira_bridge.py attach WEB-41 ./skiss.png      # upload; size is read back
python3 bin/jira_bridge.py download WEB-41 10001 ./ner    # save by id or filename
python3 bin/jira_bridge.py links WEB-41                   # the issue's links + link types
python3 bin/jira_bridge.py link WEB-41 Relates WEB-42     # WEB-41 is the outward side
python3 bin/jira_bridge.py worklogs WEB-41                # time logged
python3 bin/jira_bridge.py log-work WEB-41 "1h 30m" --comment "parprogrammering" [--started 2026-09-28]
python3 bin/jira_bridge.py sprints 1                      # the board's sprints and their state
python3 bin/jira_bridge.py sprint-create 1 "Sprint 4" --start 2026-10-05 --end 2026-10-16
python3 bin/jira_bridge.py sprint-add 12 WEB-41 WEB-42    # into a sprint
python3 bin/jira_bridge.py sprint-start 12 --yes          # one-way; --yes required
python3 bin/jira_bridge.py sprint-close 12 --yes          # moves unfinished issues
python3 bin/jira_bridge.py versions WEB                  # the project's versions
python3 bin/jira_bridge.py version-create WEB "1.2.0"
python3 bin/jira_bridge.py configure '<json>'             # e.g. {"mode":"real","language":"de"}
python3 bin/jira_bridge.py strings [lang]                 # the text table the UI renders (see Languages)
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

### MCP (for an agent in an editor)

`bin/jira_mcp.py` exposes the bridge over MCP (stdio, JSON-RPC), so a coding
agent can read the backlog and move issues without a second Jira client:
twenty-five tools (`jira_next`, `jira_attach`, `jira_link`, `jira_sprint` among
them), and every one of them shells out to
`jira_bridge.py` — the
credential, the read-back verification and the journal/trash nets stay in one
place. `delete`, `restore`, `configure`, `login` and `logout` are deliberately
**not** exposed.

```
{"mcpServers": {"godjira": {"command": "python3",
    "args": ["/home/<you>/.config/omarchy/plugins/custom.jira/bin/jira_mcp.py"]}}}
```

The args are passed to the process literally, so the path has to be absolute.

Antigravity reads that globally from `~/.gemini/config/mcp_config.json` (or per
workspace from `.agents/mcp_config.json`); any other MCP client works the same
way. Self-check: `python3 bin/jira_mcp.py --selftest` (handshake, tool list,
and a real backlog read through the bridge).

Attachments, worklogs, issue links, sprints and versions are readable and
writable, but nothing here **removes** one: an attachment, a link, a worklog, a
sprint or a version is deleted in Jira, not by an agent. `sprint-start` and
`sprint-close` are the two writes that ask for an explicit `--yes` (through MCP:
`confirm` repeating the sprint id) — Jira cannot un-start a sprint, and closing
one moves its unfinished issues.

### The customer's wish, in the panel

The panel has a **Wish** tab: type what the customer asked for, press *Propose
issues*, read the list, then press *Create* to write exactly that list. The same
thing from a shell:

```
python3 bin/jira_flow.py plan --text "kunden vill kunna boka tid"
python3 bin/jira_flow.py plan --file wish.md --create
python3 bin/jira_flow.py plan --context krav.pdf --context https://kund.se/krav --create
python3 bin/jira_flow.py pick --json          # the machine's file dialog (the panel's button)
```

### The papers and the history, before the issues

An issue that guesses where the project stands becomes wrong work. Two flags hand
the agent what the wish came with and where the code is today, so the proposal
lands *on top of* the project instead of beside it:

```
python3 bin/jira_flow.py plan --file wish.md \
  --context ~/Documents/krav/kravspec.pdf \
  --context ~/Documents/krav/motanteckningar \
  --repo ~/Documents/Skolgrejer/Systemarkitektur
```

* `--context PATH` (repeatable) reads a **file**, a **folder** (the files in it)
  or an **https link** (fetched, then read the same way), and takes **pdf** (via
  `pdftotext`/poppler),
  **docx/odt/xlsx/pptx** (zip + XML, no dependency), or anything textual; a
  *folder* reads the files in it. 6000 characters per document, 20000 in total,
  and what is cut is said so in the prompt — a short answer must never look like a
  complete basis. An unknown format is read as text, but binary garbage is refused
  with a message instead of being fed to the agent as mojibake.
* `--repo DIR` adds the branch, the 30 newest commits, the uncommitted count and,
  when the remote is GitHub and `gh` is signed in, the open PRs and issues. The
  local clone alone is enough for the direction; a missing `gh` never stops a wish.
* A page fetched from a link is read as text, with `<script>`, `<style>` and the
  markup stripped, so a spec page reads like a spec; anything larger than 20 MB is
  refused with a message rather than handed over half.
* The prompt also tells the agent to read the board first when its Jira tools are
  within reach, so it proposes what is *missing* and lets each description say
  which existing issue it rests on.
* **In the panel** the same list is filled in three ways — drag and drop it on the
  wish box (a file *or* a link), pick it from the disk with *Add file…*
  (`jira_flow.py pick`, the machine's own dialog), or paste a link in the field
  next to it. What went in is listed with an ✕ each, so it is visible what the
  agent was given, and pressing *Create* clears the list — a second press must
  never create the same issues twice. What could not be read (a broken file in a
  dropped folder, a dead link) is named under the buttons.

Both flags are read-only: `--create` is still what writes, and exactly the list you
read. With `--json` the answer carries a `context` block saying what was read and
how much of it was shown.

GodJIRA never calls a model itself, and nobody's choice is anyone else's problem.
Every user keeps their own list of agents, tried in order, first installed one
answers:

```
python3 bin/jira_flow.py agent                    # your list, and what is installed
python3 bin/jira_flow.py agent add "codex exec"   # append
python3 bin/jira_flow.py agent set "hermes chat --query-file -" "agy -p {prompt}"
python3 bin/jira_flow.py agent rm 2               # by position or by name
```

It is kept in `~/.config/jira-flow/config.json` (`"agents"`), so it travels with
the user, not with the repo. The shipped list is **hermes, then agy**;
`JIRA_FLOW_AGENT` overrides it for a single run. A command containing `{prompt}`
gets the wish as an argument, anything else gets it on stdin. No key to store
here, no model list to keep, no bill of ours. Nothing is written until `--create`
(or the second press), and the write goes through the same bridge call the panel
already used, so Jira's own rules apply.

### Taking the next critical issue (Jira to you, and into the commit)

`bin/jira_flow.py` picks the most critical not-started item, assigns it to you
and moves it to *In Progress*. Shared tasks (`GEMENSAMT:`) come first, then your
own and unassigned work; only when there is none of either does it propose the
most critical item that is **someone else's** — and that one is taken only on a
second press, naming the key it was shown with. The panel's button is that
gesture.

```
python3 bin/jira_flow.py next                       # take it; print the runners-up skipped
python3 bin/jira_flow.py next --dry-run             # show the pick and the move, write nothing
python3 bin/jira_flow.py next --expect SCRUM-147    # the second press: exactly that issue
python3 bin/jira_flow.py current                    # the key you are on right now
python3 bin/jira_flow.py --selftest
```

From an editor, and into the commits:

```
python3 bin/jira_flow.py install /path/to/repo
```

writes a VS Code / Antigravity task, an IntelliJ external tool, an Antigravity
rule and a `prepare-commit-msg` hook that names the issue in the commit subject
(from the branch name, or from `current`). Files that are not the tool's own are
left alone; its own are updated, so a moved `jira_flow.py` never leaves a shim
pointing at the old path.

#### Windows and macOS

The CLI, the editor shims and the commit hook travel; the bar widget and the
panel do not (they are Quickshell, i.e. this Linux desktop). Everything below is
Python 3 standard library only, so a teammate clones the repo and runs it.

- **Credentials without the bridge:** `python3 bin/jira_flow.py login` reads the
  token from stdin and hands it to the machine's own store — **DPAPI** on
  Windows (bound to your user account), the **login keychain** on macOS — so it
  is not lying in a file. `logout` removes it again. On Linux `login` declines
  on purpose: the bridge's keyring, or `~/.config/jira-flow/config.json`, already
  owns that there — unless you pass **`--file`**, which is the machine with no
  desktop (below). `JIRA_SITE`, `JIRA_EMAIL` and `JIRA_TOKEN` in the environment
  still win if you want a one-off. Atlassian's API token, never a password.
- **The commit hook works as it is** on all three systems: Git for Windows ships
  its own bash and runs hooks through it, and the hook looks its interpreter up
  itself (`python3`, then `py`, then `python`).
- **The VS Code / Antigravity task** carries a `"windows": { "command": "py" }`
  override, so the same file works on every platform.
- **On Windows, `install` also writes `jira-flow.cmd`** in the repo and points
  the IntelliJ external tool at it, because IntelliJ has no per-OS variant of an
  external tool and Windows has no `python3`. The launcher tries `py`, then
  `python`.
- Written files are written with `\n`, not the platform's newline: a hook with
  CRLF dies in Git for Windows' bash.

Verified on Linux; the Windows and macOS specifics above are reasoned from Git
for Windows' bundled bash and VS Code's own per-OS override, not run there.

#### A machine with no desktop (a server, a laptop without the keyring)

The token used to be the one thing that tied GodJIRA to this desktop: with no
session bus `secret-tool` cannot answer, and every command died in a traceback.
One function decides now (`jira_bridge.load_secret`), in this order:

1. **the keyring** — where it exists it stays the owner (nothing changes on the
   Omarchy desktop),
2. **`JIRA_TOKEN`** in the environment — what a service unit hands over,
3. **`~/.config/jira-flow/config.json`** (0600) — the flow's own file, read
   **only for the account it names**, so one machine's token cannot be borrowed
   for another address.

Provisioning a server, token on stdin and never in the shell history:

```sh
JIRA_SITE=https://your-domain.atlassian.net JIRA_EMAIL=you@example.com \
  python3 bin/jira_flow.py login --file < /tmp/jira-token   # writes 0600
```

Measured 2026-09-29, session bus unset: `jira_flow.py current --project SCRUM`
and `next --dry-run --json` answer exactly as they do on the desktop — `current`
still exits 1 for "nothing in progress", which is what n8n reads as *no answer
needed* rather than an error.

Credentials come from the bridge's keyring whenever the bridge is there — which
it is, inside this plugin. Outside Omarchy (a plain clone for a teammate) the
same script reads `JIRA_SITE`/`JIRA_EMAIL`/`JIRA_TOKEN`, or
`~/.config/jira-flow/config.json`. Every write is read back from the site and
compared, and a mismatch exits non-zero instead of reporting success.

## Real Jira Cloud

1. Create an API token at
   https://id.atlassian.com/manage-profile/security/api-tokens
2. Connect. `login` validates the credentials against `/rest/api/3/myself`
   FIRST and only then writes the address and stores the token — a typo in the
   site or e-mail can therefore not knock out a connection that already works.
   The token goes to the system keyring via `secret-tool`, never into a file:

```
python3 bin/jira_bridge.py login --site https://your-domain.atlassian.net --email you@example.com
JIRA_TOKEN=... python3 bin/jira_bridge.py login --site ... --email ...   # token on stdin
python3 bin/jira_bridge.py login --token-file /tmp/jira-token --site ... --email ...
python3 bin/jira_bridge.py logout --yes    # remove the stored token
```

   A token that is already in the keyring can be reused: `login --site ... --email ...`
   without a token picks it up, which is how the panel reconnects after
   *Koppla från*.

### Anslutningslåset

While a working connection exists (site + account + token, mode real) the bridge
refuses to let anything overwrite it by accident:

| Command | Without the explicit flag |
| --- | --- |
| `configure '{"siteUrl":…}'` / `{"email":…}` / `{"mode":…}` | refused — needs `--replace` |
| `login` to a different site or account | refused — needs `--replace` |
| `login` for the same account with a new token | allowed (that is not a swap) |
| `logout` | refused — needs `--yes` |
| `configure '{"startView":…}'` and the other UI keys | unaffected |

Every refusal is journaled, so `journal` shows attempts as well as actions, and
`status` reports the lock (`connection.locked`) plus whether a token is stored
(`connection.hasToken`) — the UI needs both to know whether to lock its form.
`logout --yes` removes the token but keeps the address, so reconnecting needs
only a token.

## State and configuration

| Thing | Location |
| --- | --- |
| Plugin | `~/.config/omarchy/plugins/custom.jira/` |
| Config (mode, site, email, startView, language) | `~/.config/omarchy/jira.json` |
| Mock dataset | `~/.local/state/omarchy/jira-mock.json` |
| Watcher baseline | `~/.local/state/omarchy/jira-watch.json` |
| API token (real mode) | system keyring, service `custom.jira` |
| Skrivjournal | `~/.local/state/omarchy/jira-actions.log` (0600) |
| Lokal papperskorg | `~/.local/state/omarchy/jira-trash/` (0600 per fil) |

Refresh interval: open the Jira widget's settings (default 30 s, min 10 s).

Which view the window opens on is a config value — pick it under
**Inställningar → Startvy**, or from the command line (it takes effect the next
time the shell loads the plugin, since a loaded plugin's code is kept):

```
python3 ~/.config/omarchy/plugins/custom.jira/bin/jira_bridge.py \
    configure '{"startView":"timeline"}'
```

Accepted values: `summary`, `board` (default), `backlog`, `timeline`, `reports`,
`dev`, `activity`, `settings`.

The language is a config value too, and changing it writes nothing else:

```
python3 ~/.config/omarchy/plugins/custom.jira/bin/jira_bridge.py \
    configure '{"language":"sv"}'
```

`""` (or `"auto"`) follows the system locale; anything unknown falls back to
English. Only `siteUrl`, `email` and `mode` are protected by the connection
lock — `language` and `startView` change freely.

## Languages

The interface speaks English, Swedish, German, French, Spanish, Italian,
Portuguese, Dutch and Polish. Switch language under **Inställningar → Språk**,
or leave it on *Follow the system* and the panel takes the language from
`LANG`.

One source of truth: `i18n/en.json` is the source text, and every other file is
a translation of it. Both the panel and the Python bridge read the same files —
the bridge serves them to the QML through the `strings` command, so there is no
second copy of any sentence in the QML, and the bridge's own error messages
speak the same language as the window.

```bash
python3 bin/i18n_check.py     # key set and {placeholders} must match en.json
```

The check exits non-zero on a missing key, an extra key, a mismatched
placeholder, or an empty value, and it lists the keys a language happens to
spell exactly like English (allowed, but worth a look). Add a language by
dropping `i18n/<code>.json` next to the others; the picker finds it by itself.

Two things are deliberately not translated: the issue text itself (titles,
statuses, descriptions, names are Jira's data and are shown as Jira writes
them), and the journal's technical `detail` fields, which stay stable so a log
line means the same thing in every language.

Only Jira's own vocabulary stays in English inside the panel where it is a
product term rather than a label — the view names are translated, the statuses
and board names are Jira's.

## Skrivskydd (safety nets)

Klienten får skriva mot din Jira, och i Jira Cloud **går en radering inte att
ångra** — det finns ingen papperskorg för ärenden. Fyra nät finns därför inbyggda.
De är tysta i normalfallet och syns bara när de behövs.

1. **Journal.** Varje skrivning lämnar en rad i `~/.local/state/omarchy/jira-actions.log`:
   vad som gjordes, vilket ärende, av vem och när — även försök som nekades.
   Läs den med `journal` (eller öppna filen; en rad JSON per händelse).
2. **Lokal papperskorg.** Före varje radering — och före en ändring av
   sammanfattning eller beskrivning — sparas hela ärendet (alla fält **och**
   kommentarerna, kommentarerna ordagrant som ADF) i `jira-trash/`. Går kopian inte
   att skriva **nekas åtgärden**: vi raderar aldrig något vi inte först kunnat spara.
   `restore <key>` skapar ärendet igen ur den nyaste kopian och **säger vad som inte
   kunde läggas tillbaka** (kommentarer får den ursprungliga skribenten och datumet
   i raden, eftersom Jira inte tillåter att skriva i någon annans namn).
3. **Kvotvakt.** Fler än tre raderingar inom tio minuter nekas, och beskedet säger
   hur många som redan gjorts. En skur — ett misstag eller en loop — blir då tre
   ärenden och ett tydligt fel i stället för en tyst utrensning. `--force` kringgår
   vakten när man vet vad man gör (städning), och även det hamnar i journalen.
4. **Kvitto.** Efter varje skrivning läses ändringen tillbaka och jämförs med vad som
   beställdes: flytten ska ha landat i rätt status, kommentaren ska finnas i tråden,
   det ändrade fältet ska ha rätt värde, och det raderade ärendet ska svara 404.
   Stämmer det inte rapporteras det som ett fel — en skrivning får aldrig se ut att ha
   lyckats när den inte gjorde det.

I gränssnittet är raderingen två steg: först `Radera ärende…`, sedan en ruta som
**namnger ärendet** och säger att det inte går att ångra i Jira men att en kopia sparas
lokalt. Det andra steget ser medvetet annorlunda ut än det första.

Sajtens egna medel: Jira-administratören kan se **vem och när** ett ärende raderades i
`⚙ → System → Audit log` (sök på `Work item deleted` eller nyckeln). Överväg också att
ta bort rättigheten *Delete Issues* från vanliga medlemmar — en status räcker oftast.

## Development notes

- After editing QML, restart the shell so the running engine picks the files up:
  `omarchy-restart-shell`, then reopen the window. The plugin is declared
  `keepLoaded: true`, so the shell keeps the loaded instance and does **not**
  replace it on hot reload - edits to `JiraPanel.qml` (the entry point) need a
  shell restart to show up, while the view files under `views/` are re-read when
  a view is loaded.
- A plugin's IPC surface is fixed to the methods it had when the shell first
  loaded it: adding a method to the `IpcHandler` does not make it callable
  (`omarchy-shell shell call custom.jira <name>` answers `unknown`), and an
  untyped parameter is rejected outright - "Type of argument 1 (x: QVariant)
  cannot be used across IPC". That is why the view to open with is a config
  value rather than an IPC argument.
- Lint plugin QML against the shell UI types:

```
qmllint -I /usr/share/omarchy/shell -I /usr/lib/qt6/qml views/BoardView.qml JiraPanel.qml
```

- Shell log for runtime errors: `journalctl --user -t omarchy-shell`.
- A drag is carried by the card delegate that started it: `IssueCard`'s
  MouseArea reports the release, and `build()` replaces every delegate (it hands
  out fresh column objects). A drag in flight when that happens never sees its
  release, so the ghost stayed frozen on the board and `dragging` stayed true -
  every later drag was refused until the window was closed. That is why
  `build()` calls `cancelDrag()` before it touches `cols`; keep that first line
  if you add another path that replaces the columns (a poll whose answer differs
  from what is on screen is the one that does it in practice). A rebuild is only
  real when the data changed: assigning an equal-content array to the Repeater
  changes nothing, QML skips the update and the delegates survive.
