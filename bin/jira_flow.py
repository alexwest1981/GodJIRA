#!/usr/bin/env python3
"""jira_flow -- take the next critical Jira item, and keep commits linked to it.

One small CLI instead of one plugin per editor: VS Code and the Antigravity IDE
run it as a task, IntelliJ as an external tool, and git calls it from a
prepare-commit-msg hook. `jira_flow install <repo>` writes all four.

Commands
    login | logout
        Keep the token in the machine's own store: DPAPI on Windows, the login
        keychain on macOS, read from stdin so it stays out of the shell history.
        On Linux this declines -- the Omarchy bridge or the 0600 config file
        already owns it there.
    next [--project KEY] [--status "In Progress"] [--dry-run] [--json] [--expect KEY]
        Take the most critical not-started item (shared tasks first), assign it to
        you, move it to In Progress. With nothing of your own it proposes the most
        critical item that is someone else's and takes it only when the caller
        presses again with --expect KEY, naming that exact issue.
    pick [--title TEXT] [--json]
        The file dialog on this machine (zenity), one chosen path per line. The
        panel uses it so a pick is the same process path as everything else, and
        a machine without a dialog answers with an error instead of doing nothing.
    agent [list | add CMD | set CMD... | rm N|NAME]
        Your own list of agents, tried in order, first installed one answers.
        Kept in ~/.config/jira-flow/config.json ("agents"), so every user has
        their own and nobody's choice depends on someone else's. Shipped list:
        hermes, then agy. JIRA_FLOW_AGENT overrides it for one run.
    plan [--text TEXT | --file PATH] [--context PATH|URL]... [--repo DIR]
         [--create] [--json] [--project KEY] [--pdf PATH] [--lang CODE]
         [--sprints] [--sprint-weeks N] [--sprint-start YYYY-MM-DD]
        Hands the customer's wish to the agent you have chosen (JIRA_FLOW_AGENT,
        "claude -p" by default -- any CLI that reads a prompt on stdin and answers
        with JSON works) and gets issue proposals back. GodJIRA never calls a model
        itself: no key, no model list, no bill. Nothing is written until --create,
        and then exactly the list you just read.
        --context hands over the papers the wish came with (pdf, docx/odt/xlsx,
        text, or a folder of them) and --repo the project's own history (branch,
        recent commits, open PRs and issues via gh when the remote is GitHub), so
        the issues land where the project actually is instead of beside it.
        Every issue comes back with a sprint number, so the proposal says how the
        work is divided and not just what it is. --pdf writes that division out as
        an A4 document (Overview first, then each sprint with its issues): the
        paper a customer or a team reads instead of a list in a window. It is
        drawn from exactly the list that was just proposed, never from a second
        answer, and --lang picks the language of the frame around it (sv, or
        English for anything else -- the issues keep the customer's language).
    current
        The key of the item you are on right now (the commit hook reads this).
    install [repo] [--project KEY]
        Write the editor shims and the commit hook into a repository.
    --selftest
        Offline checks of the pure logic (no network, no credential).

Credentials, two ways, picked automatically:
    * the Omarchy Jira bridge, if it is installed (keyring token, action log),
    * otherwise JIRA_SITE, JIRA_EMAIL, JIRA_TOKEN in the environment or in
      ~/.config/jira-flow/config.json (chmod 600).
"""

from __future__ import annotations

import sys as _sys

# Ingen bytekod bredvid kallkoden. Hubben ligger i sin plugin-katalog, och Omarchys
# skal laddar om ett lokalt plugin sa fort nagot i katalogen andras -- en .pyc vore
# alltsa en omladdning av baren.
_sys.dont_write_bytecode = True

import argparse
import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.parse
import tempfile
import urllib.request
import zipfile
from base64 import b64encode
from html import escape, unescape
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SELF = Path(__file__).resolve()
BRIDGE_DIR = Path(
    os.environ.get("JIRA_BRIDGE_DIR", str(Path(__file__).resolve().parent))
)
CONFIG_FILE = Path.home() / ".config/jira-flow/config.json"
# Repots Jira-koppling. Ingen hemlighet (ingen token), men den hör i samma stängda
# katalog: den säger vilket projekt och vilket ärende ett repo hör till, och den ska
# överleva att plugin-katalogen klonas om.
LINKS_FILE = Path.home() / ".config/jira-flow/links.json"
# Var en lokal kopia brukar ligga när länken ska ge kodkontext. Sista posten är
# hubben själv, som ligger i sin plugin-katalog och inte under någon projektmapp.
LOCAL_ROOTS = ("~/Projects", "~/Work", "~/Documents", str(SELF.parent.parent))
LOG_FILE = Path.home() / ".local/state/jira-flow/actions.log"
DEFAULT_PROJECT = os.environ.get("JIRA_FLOW_PROJECT", "SCRUM")
DEFAULT_STATUS = os.environ.get("JIRA_FLOW_STATUS", "In Progress")

# Kundönskemålet blir ärenden genom *din* agent, inte genom en modell som GodJIRA
# ringer. Ingen nyckel, ingen modellista, ingen räkning att hålla reda på: den CLI
# du redan valt läser prompten på stdin och svarar med ärendena som JSON.
AGENT = os.environ.get("JIRA_FLOW_AGENT", "")  # tom = användarens lista, sedan den skeppade
# Den skeppade listan, i tur och ordning. Var och en har sin egen i
# ~/.config/jira-flow/config.json ("agents") och ändrar den med
# `jira_flow agent add|set|rm` -- ingen behöver vara beroende av någon annans val.
# {prompt} i ett kommando betyder att CLI:t vill ha texten som argument; annars går
# den på stdin (Hermes läser den därifrån).
AGENT_CHAIN = ("hermes chat --query-file - --format stream-json -Q", "agy -p {prompt}")
# Den här raden stod i den skeppade listan förut. Har användaren inte rört den är
# den fortfarande hans -- och då är den också fortfarande utan flaggan som gör svaret
# läsbart. En egen skriven rad lämnas i fred: bara den ordagrant gamla standarden byts.
AGENT_UPGRADE = {"hermes chat --query-file -": AGENT_CHAIN[0]}
AGENT_TIMEOUT = int(os.environ.get("JIRA_FLOW_AGENT_TIMEOUT", "600"))
# Ett agent-svar som inte gick att tolka hamnar här (0600). Annars finns ingenting
# kvar att titta på när flödet säger att svaret var obrukbart.
ANSWER_LOG = os.path.expanduser("~/.local/state/omarchy/jira-flow-answer.log")
# Minnet: vad agenten svarat, en rad per tur, inom en vecka. Samma state-katalog som
# flödets egen logg -- inget nytt ställe för sanningen att bo på. Gallringen sker när
# filen läses (varje chattur läser den), så den töms successivt utan ett eget jobb.
MEMORY_FILE = Path.home() / ".local/state/jira-flow/memory.jsonl"
MEMORY_DAYS = int(os.environ.get("JIRA_FLOW_MEMORY_DAYS", "7"))
MEMORY_TURNS = 20       # turer som får plats i prompten
MEMORY_CHARS = 4000     # och hur mycket de får kosta, sammanlagt
# Taken är vakter mot en agent som spårar ur, inte mot en verklig backlog. Ett tak på 10
# kastade 2026-10-05 bort ett riktigt svar: en pdf gav 16 ärenden med epics, filnamn ur
# koden och sprints -- och hela listan försvann på en sifferkontroll, trots att panelen
# visar förslagen som en lista man bockar av. "Är det 400 scrums som måste göras, så måste
# 400 scrums göras": taket skall ligga där ingen rimlig uppdelning når det.
PLAN_EPICS = int(os.environ.get("JIRA_FLOW_PLAN_EPICS", "50"))
PLAN_MAX = int(os.environ.get("JIRA_FLOW_PLAN_MAX", "500"))
PLAN_SPRINTS = int(os.environ.get("JIRA_FLOW_PLAN_SPRINTS", "3"))

# Ramen runt pappret som importen lägger fram. Uppgifterna står som de skrevs, i kundens
# eget språk -- bara ramen översätts, och bara sv och en finns: ett annat panelspråk får
# den engelska ramen i stället för en halvöversatt svensk.
PLAN_WORDS = {
    "sv": {
        "title": "Fördelningen av uppgifterna",
        "wish": "Kundens önskemål",
        "project": "Projekt",
        "documents": "dokument", "document": "dokument", "cut": "klippt",
        "chars": "tecken",
        "overview": "Översikt", "kinds": "Typer", "of": "av",
        "sprint": "Sprint", "issue": "uppgift", "issues": "uppgifter",
        "holds": "Innehåller", "under": "under", "empty": "inget i den här sprinten",
        "note": "Det här är ett förslag. Ingenting är skrivet till Jira än. När det skrivs "
                "är det exakt den här listan, ingenting annat.",
        "source": "tolkat ur underlaget av {}",
    },
    "en": {
        "title": "How the work is divided",
        "wish": "What the customer asked for",
        "project": "Project",
        "documents": "documents", "document": "document", "cut": "truncated",
        "chars": "characters",
        "overview": "Overview", "kinds": "Types", "of": "of",
        "sprint": "Sprint", "issue": "task", "issues": "tasks",
        "holds": "Holds", "under": "under", "empty": "nothing in this sprint",
        "note": "This is a proposal. Nothing is written to Jira yet. When it is, it is "
                "exactly this list and nothing else.",
        "source": "read from the papers by {}",
    },
}

PLAN_HTML = """<!doctype html>
<html lang="{lang}">
<head><meta charset="utf-8"><title>{title}</title><style>{css}</style></head>
<body>
<h1>{title}</h1>
<p class="meta">{meta}</p>
{wish}
<h2 class="block">{overview}</h2>
<table class="over">
<thead><tr><th>{head}</th><th>#</th><th>{holds}</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="kinds">{kinds}</p>
{body}
<p class="note">{note}</p>
{source}
</body>
</html>
"""

PLAN_CSS = """
@page { size: A4; margin: 17mm 16mm; }
* { box-sizing: border-box; }
body { font-family: "DejaVu Sans", "Noto Sans", system-ui, sans-serif;
       font-size: 10pt; line-height: 1.45; color: #1a1e1d; margin: 0; }
h1 { font-size: 16pt; margin: 0 0 1mm; letter-spacing: -0.2pt; }
p.meta { color: #5b6360; font-size: 8.5pt; margin: 0 0 4mm; }
p.wishlab { font-size: 8.5pt; color: #5b6360; margin: 0 0 1mm; }
p.wish { margin: 0 0 5mm; padding: 0 0 0 3mm; border-left: 2px solid #cfd6d3;
         white-space: pre-wrap; }
h2.block { font-size: 11pt; margin: 0 0 2mm; }
table.over { width: 100%; border-collapse: collapse; margin: 0 0 1.5mm; font-size: 9pt; }
table.over th { text-align: left; font-weight: 600; border-bottom: 1px solid #cfd6d3;
                padding: 0 3mm 1mm 0; color: #5b6360; font-size: 8.5pt; }
table.over td { padding: 1mm 3mm 1mm 0; border-bottom: 1px solid #eceeed; vertical-align: top; }
table.over td:nth-child(2) { width: 12mm; }
p.kinds { color: #5b6360; font-size: 8.5pt; margin: 0 0 6mm; }
section.sprint { border-top: 1px solid #00775c; padding-top: 3mm; margin: 0 0 7mm; }
section.sprint h2 { font-size: 12pt; margin: 0 0 3mm; color: #00775c; }
section.sprint h2 span.count { color: #5b6360; font-size: 9pt; font-weight: 400;
                               margin-left: 3mm; }
div.item { margin: 0 0 3mm; padding: 0 0 0 3mm; border-left: 2px solid #eceeed;
           break-inside: avoid; }
div.item.nested { margin-left: 6mm; border-left-color: #cfd6d3; }
span.type { display: inline-block; min-width: 16mm; color: #5b6360; font-size: 8.5pt; }
span.sum { font-weight: 600; }
span.prio, span.beside { color: #5b6360; font-size: 8.5pt; margin-left: 2mm; }
div.desc { white-space: pre-wrap; color: #3c4340; margin-top: 1mm; }
p.note { margin: 8mm 0 1mm; padding-top: 3mm; border-top: 1px solid #cfd6d3; font-size: 9pt; }
p.source { color: #7c8481; font-size: 8pt; margin: 0; }
"""

# Underlaget agenten får utöver själva önskemålet. Taken finns för att en agent
# som får 300 000 tecken slutar läsa och börjar gissa; allt som klipps bort sägs
# det om i prompten, så ett kort svar aldrig ser ut som ett fullständigt underlag.
# Budgetarna går att styra per körning. Mätt 2026-10-05: ett kravdokument på 7999 tecken
# förlorade sina sista 1999 (25 %) -- och det som klipptes bort var just kravlistan och
# sista avsnittet, alltså det en plan skall vila på. 6000 var för snålt för den här sortens
# dokument; taken finns för att skydda modellen, inte för att spara in ett par sidor.
DOC_CHARS = int(os.environ.get("JIRA_FLOW_DOC_CHARS", "20000"))     # per dokument
TOTAL_CHARS = int(os.environ.get("JIRA_FLOW_TOTAL_CHARS", "60000"))  # alla dokument tillsammans
MAX_FILES = 20          # filer ur en mapp, fler än så är inte ett önskemål
COMMITS = 30            # rader ur git-historiken
REPO_ROWS = 10          # öppna PR:er, ärenden och commits i repo-detaljen
URL_BYTES = 20 * 1024 * 1024   # ett underlag från nätet, inte en film
URL_UA = "GodJIRA/1.0 (jira_flow; +https://github.com/alexwest1981/GodJIRA)"

# Vad servern säger att den skickar får bestämma hur svaret läses. Okänd typ faller
# tillbaka på filnamnets ändelse och sedan på text — med skräpvakten i read_document.
SUFFIX_BY_TYPE = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
}

PLAN_PROMPT = """\
You are a scrum master. Split the customer's wish below into Jira issues for project {project}.
If the board's own tools are within reach (jira_board, jira_backlog, jira_activity), read them
first: the proposal has to fit what is already there, not duplicate it.
The repository text is what the code already does. Read it before you propose anything: work that
is already in the code is not work, and a description that names the files to touch is worth ten
that do not. Say in the description which files the work lands in, taken from the repository text.
{context}
Only what the wish actually asks for. Do not invent scope, at most {limit} issues. Every issue's
description says either which existing board key it builds on, or that the work is new.
Issue types that exist in this project: {types}.
Shape the work as scrum: one epic (type "Epic") per coherent piece of the wish, with its tasks
under it. A task names its epic in "epic" -- exactly the epic's summary. A wish that is one small
thing needs no epic at all. Put every epic before its own tasks in the array.
Spread the issues over the sprints: every issue gets a "sprint" number, and sprint 1 is what the
team builds first. Keep a sprint to what the team gets through in one sprint, keep work that depends
on other work in the same sprint as it or a later one, and never use more than {sprints} sprints.

Write the way the team talks, not the way a brochure does: plain words, active voice, short
sentences. Say what to build and how you know it is done; leave out the pitch, the filler and the
enthusiasm -- the person reading this is the one doing the work.

Answer with one JSON array of objects and nothing else -- no prose, no explanation,
no code fences. Every object has exactly these six fields:
  "summary"       a short imperative for this project, at most 80 characters
  "type"          one of the issue types listed above ("Epic" for an epic)
  "epic"          the summary of the epic this issue belongs to, "" for an epic itself
  "description"   what to build, how to know it is done, and which files it touches
  "priority"      one of: Highest, High, Medium, Low
  "sprint"        which sprint it is planned for: 1 for the first one
Write them in the language the customer wrote in -- a Swedish wish gets Swedish
issues, because that is the language the team reads on the board.

The customer's wish:
{wish}
"""


# ------------------------------------------------------------------ kontext
#
# Ett ärende som gissar var projektet står blir fel arbete. Därför får agenten
# önskemålet OCH underlaget: dokumenten (pdf, docx, text ...) och historiken
# (grenen, de senaste commitarna, öppna PR:er). Allt klipps med ett synligt
# besked — en tyst trunkering ser ut som ett fullständigt svar.

ZIP_TEXT_SUFFIXES = (".docx", ".odt", ".xlsx", ".pptx")


def xml_text(xml: str) -> str:
    """XML -> läsbar text: stycken blir rader, taggarna bort."""
    xml = re.sub(r"</(w:p|w:tr|text:p|text:h|a:p|row|si)>", "\n", xml)
    return unescape(re.sub(r"<[^>]+>", "", xml))


def html_text(markup: str) -> str:
    """HTML -> läsbar text. Skript och stil är inte underlag, de är brus."""
    markup = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(r"(?s)<!--.*?-->", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|section|article|title|blockquote|pre)>",
                    "\n", markup)
    return xml_text(markup)


def url_text(url: str) -> str:
    """Ett underlag från nätet. Sidor och text läses direkt; pdf och annat binärt
    landar i scratch och går genom exakt samma läsare som en lokal fil."""
    request = urllib.request.Request(url, headers={"User-Agent": URL_UA})
    with urllib.request.urlopen(request, timeout=60) as response:
        ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        body = response.read(URL_BYTES)
        clipped = bool(response.read(1))
    name = os.path.basename(url.split("?")[0].split("#")[0].rstrip("/"))
    suffix = SUFFIX_BY_TYPE.get(ctype) or Path(name).suffix.lower()
    if ctype.startswith("text/") or suffix in (".html", ".htm", ".txt", ".md", ".json",
                                               ".csv", ".xml", ".rst", ".log", ""):
        text = body.decode("utf-8", "replace")
        return html_text(text) if "html" in ctype or suffix in (".html", ".htm") else text
    if clipped:
        # En halv pdf läses inte alls: ett avhugget underlag ska säga det, inte gissa.
        raise RuntimeError("{} is larger than {} MB; download it and pass the file"
                           .format(url, URL_BYTES // 1024 // 1024))
    folder = tempfile.mkdtemp(prefix="jira-flow-url-")
    landed = Path(folder) / ("url" + suffix)
    landed.write_bytes(body)
    return read_document(landed)


def zip_text(path) -> str:
    """Texten ur docx/odt/xlsx/pptx: zip + XML ur stdlib, inget mer beroende."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "word/document.xml" in names:
            wanted = ["word/document.xml"]
        elif "content.xml" in names:
            wanted = ["content.xml"]
        elif "xl/sharedStrings.xml" in names:
            wanted = ["xl/sharedStrings.xml"]
        else:
            wanted = sorted(n for n in names
                            if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        return "\n".join(xml_text(z.read(n).decode("utf-8", "replace")) for n in wanted)


def pdf_text(path) -> str:
    """PDF via poppler (pdftotext). En egen PDF-tolkare vore ett projekt i sig."""
    tool = shutil.which("pdftotext")
    if not tool:
        raise RuntimeError("reading {} needs pdftotext on PATH (install poppler)"
                           .format(os.path.basename(str(path))))
    done = subprocess.run([tool, "-enc", "UTF-8", str(path), "-"],
                          capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError("pdftotext could not read {}: {}".format(
            os.path.basename(str(path)), (done.stderr or "").strip()[:200]))
    return done.stdout


def read_document(path) -> str:
    """Ett dokument som text. Ett okänt format läses som text — men skräp nekas:
    en agent som får mojibake skriver ärenden om ingenting."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise RuntimeError("no such file: {}".format(path))
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return pdf_text(p)
    if suffix in ZIP_TEXT_SUFFIXES:
        return zip_text(p)
    text = p.read_bytes().decode("utf-8", "replace")
    if text.count("\ufffd") > max(50, len(text) // 5):
        raise RuntimeError("{} is not text (binary?); supported: pdf, {}, plain text"
                           .format(p.name, ", ".join(x.lstrip(".") for x in ZIP_TEXT_SUFFIXES)))
    return text


def context_items(raw):
    """Underlaget som (post, namngiven): en fil blir en, en mapp blir de filer den
    har, och en http(s)-länk hämtas. Panelens släpp ger file://-adresser."""
    raw = re.sub(r"^file://(localhost)?", "", str(raw).strip())
    if re.match(r"^https?://", raw, re.I):
        return [(raw, True)]
    p = Path(raw).expanduser()
    if p.is_dir():
        found = [f for f in sorted(p.iterdir())
                 if f.is_file() and not f.name.startswith(".")][:MAX_FILES]
        return [(f, False) for f in found]
    return [(p, True)]


def read_context(raw_paths):
    """Dokumenten som text och vad som lästes. En namngiven fil (eller länk) som
    inte går att läsa är ett fel; en fil som hittas i en mapp får hoppas över med
    besked — där är urvalet någon annans."""
    chunks, notes, used = [], [], 0
    for raw in raw_paths or []:
        items = context_items(raw)
        if not items:
            notes.append({"path": str(raw), "chars": 0, "error": "folder is empty"})
        for item, named in items:
            try:
                text = url_text(item) if isinstance(item, str) else read_document(item)
            except (RuntimeError, OSError, zipfile.BadZipFile, ValueError) as exc:
                if named:
                    raise RuntimeError(str(exc))
                notes.append({"path": str(item), "chars": 0, "error": str(exc)})
                continue
            text = re.sub(r"[ \t]+\n", "\n", text.replace("\r\n", "\n"))
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            whole = len(text)
            keep = min(whole, DOC_CHARS, max(0, TOTAL_CHARS - used))
            shown = text[:keep]
            note = {"path": str(item), "chars": len(shown), "documentChars": whole}
            if keep < whole:
                note["truncated"] = True
                shown += "\n[... {} of {} characters shown]".format(len(shown), whole)
            used += len(shown)
            chunks.append("### {}\n{}".format(str(item), shown))
            notes.append(note)
    return "\n\n".join(chunks), notes


def git_out(root, *argv, timeout=30) -> str:
    done = subprocess.run(["git", "-C", str(root)] + list(argv),
                          capture_output=True, text=True, timeout=timeout)
    return done.stdout.strip() if done.returncode == 0 else ""


def gh_json(*argv, root=None, timeout=60):
    """Kör gh och läs svaret som JSON. Kastar gh:s egen sista rad, inte en traceback."""
    done = subprocess.run(["gh"] + [str(a) for a in argv], cwd=str(root) if root else None,
                          capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        lines = [l for l in (done.stderr or "gh failed").strip().splitlines() if l.strip()]
        raise RuntimeError(lines[-1][:200] if lines else "gh failed")
    text = (done.stdout or "").strip()
    return json.loads(text) if text else None


def gh_soft(*argv, root=None, timeout=60):
    """Samma anrop, men ett fel blir en anteckning i stället för ett avbrott.

    Ett repo utan ärenden, en avstängd issue-flik eller en gh som inte svarar ska
    visa resten av repot -- inte ingenting.
    """
    try:
        return gh_json(*argv, root=root, timeout=timeout), ""
    except (RuntimeError, ValueError) as exc:
        return None, str(exc)


def gh_open(root, what: str, limit: int = 10):
    """Öppna PR:er eller ärenden i den här kopian, ur gh."""
    return gh_json(what, "list", "--state", "open", "--limit", str(limit),
                   "--json", "number,title,updatedAt", root=root) or []


def repo_context(raw) -> tuple:
    """Var projektet står: gren, senaste commitarna, öppna PR:er och ärenden.

    Bara läsning, inget skrivs och inget nätverk utom gh:s egna API-anrop. Ett
    saknat gh (eller en fjärr som inte är GitHub) ska inte stoppa ett önskemål:
    historiken i den lokala klonen bär riktningen ändå.
    """
    root = Path(raw).expanduser()
    if not root.is_dir():
        return "", {"repo": str(root), "error": "not a directory"}
    if not git_out(root, "rev-parse", "--is-inside-work-tree"):
        return "", {"repo": str(root), "error": "not a git repository"}
    branch = git_out(root, "rev-parse", "--abbrev-ref", "HEAD")
    remote = git_out(root, "remote", "get-url", "origin")
    log = [row for row in git_out(root, "log", "--date=short", "--pretty=%h %ad %s",
                                  "-n", str(COMMITS)).splitlines() if row.strip()]
    dirty = [row for row in git_out(root, "status", "--porcelain").splitlines() if row.strip()]
    info = {"repo": str(root), "branch": branch, "remote": remote,
            "commits": len(log), "uncommitted": len(dirty)}
    lines = ["branch: {}".format(branch or "?"),
             "uncommitted files: {}".format(len(dirty)),
             "last {} commits (newest first):".format(len(log))]
    lines += ["  " + row for row in log]
    if "github.com" in (remote or "") and shutil.which("gh"):
        for what, label in (("pr", "open pull requests"), ("issue", "open issues")):
            try:
                rows = gh_open(root, what)
            except (RuntimeError, ValueError) as exc:
                info[what] = "unreadable"
                lines.append("{}: could not be read ({})".format(label, exc))
                continue
            info[what] = len(rows)
            lines.append("{}: {}".format(label, len(rows)))
            lines += ["  #{} {}{}".format(r.get("number"), r.get("title") or "",
                                          " ({})".format((r.get("updatedAt") or "")[:10]))
                      for r in rows]
    elif remote:
        lines.append("remote is not github.com ({}) — local history only".format(remote))
    return "\n".join(lines), info


# ------------------------------------------------------------- kodkontexten
#
# Ett anslag skall mötas av vad som redan FINNS i repot, inte bara av dess
# historik: "finns bokningen redan?" besvaras av koden, inte av commit-raden.
# Git vet vilka filer som är med (ls-files), så ignorerade filer och byggskräp
# följer med gratis. Kontraktsraderna (def/class/interface/...) säger vad en fil
# gör; hela texten tas bara för de filer som ligger närmast önskemålet, och allt
# klipps med ett synligt besked -- en tyst trunkering ser ut som ett fullständigt
# svar, och då gissar modellen i stället för att läsa.

CODE_BUDGET = int(os.environ.get("JIRA_FLOW_CODE_CHARS", "60000"))
CODE_FILES = int(os.environ.get("JIRA_FLOW_CODE_FILES", "12"))
CODE_MAP_MAX = int(os.environ.get("JIRA_FLOW_CODE_MAP", "400"))
CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".kts",
                 ".go", ".rs", ".rb", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".php", ".sh",
                 ".sql", ".qml", ".vue", ".svelte", ".swift", ".lua", ".toml", ".yaml", ".yml")
CODE_MANIFESTS = ("readme", "manifest", "package.json", "pyproject.toml", "cargo.toml", "go.mod",
                  "requirements", "pom.xml", "build.gradle", "makefile", "dockerfile", "schema",
                  "migration", "settings.gradle", "compose.")
CODE_DEF = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|export|interface|type|struct|impl|enum|pub\s+fn|"
    r"fn|public|private|protected|static|void|const|let|var|module|namespace|CREATE\s+(?:TABLE|INDEX))\b",
    re.I)
CODE_WORD = re.compile(r"[A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö0-9_]{3,}")
CODE_STOP = {
    "samt", "eller", "detta", "denna", "skall", "skulle", "kunna", "finns", "finnas", "vilket",
    "vilken", "vilka", "sedan", "även", "måste", "behöver", "kunden", "önskemål", "when", "with",
    "that", "this", "from", "into", "should", "shall", "have", "must", "there", "their", "about",
    "would", "could", "just", "only", "also", "make", "need", "want", "user", "issue", "issues",
}


def code_words(text: str) -> set:
    """Orden ur ett önskemål som är värda att matcha mot en filsökväg."""
    return {w for w in (m.group(0).lower() for m in CODE_WORD.finditer(text or ""))
            if w not in CODE_STOP}


SCAN_FILE = Path.home() / ".config/jira-flow"


def scan_map(bridge, project: str, root: Path, limit: int = 250) -> dict:
    """Kartan: vilka filer varje ärende i projektet pekar på.

    Frågan den svarar på är Alex egen: var behöver man göra ändringar? Två listor är
    svaret -- de ärenden som inte pekar på någon fil (oklart var jobbet skall göras)
    och de filer som ingen ärendetext nämner (kod utan spår i tavlan). Resten är
    kopplingarna, ärende för ärende.

    ponytail: ett git grep per ärende, inget typsnitt läses -- O(ärenden) anrop. Ett
    par hundra ärenden tar några sekunder; en delad index lönar sig först i den
    storleken projektet sällan har.
    """
    board = bridge.board(project)
    rows = [(r, "aktiv") for r in (board.get("issues") or [])]
    rows += [(r, "backlog") for r in (board.get("backlog") or [])]
    listed = [line.strip() for line in git_out(root, "ls-files").splitlines() if line.strip()]
    if not listed:
        # Ett projekt behöver inte vara ett git-repo: skolans inlämning är en zip med
        # källkod i. Då läses katalogen i stället -- samma svar, utan git.
        skip = {".git", ".idea", "node_modules", "target", "build", "dist", "out", ".gradle"}
        listed = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                        if p.is_file() and not any(part in skip for part in p.parts))[:4000]
    # Ord som står i nästan varje sökväg pekar inte ut något: repots eget namn ligger
    # i varenda fil (mätt: gav 125 av 131 "mappade" -- allt matchade allt). Ett ord som
    # finns i mer än var fjärde fil säger ingenting om vilken fil ärendet gäller.
    listed_low = [(p, p.lower()) for p in listed]
    all_words = set()
    for row, _pool in rows[:limit]:
        all_words |= code_words(f"{row.get('summary') or ''} {row.get('description') or ''}")
    common = {w for w in all_words
              if sum(1 for _p, low in listed_low if w in low) > max(3, len(listed_low) // 4)}
    mentioned = set()
    mapped, unmapped = [], []
    for row, pool in rows[:limit]:
        words = [w for w in sorted(code_words(
            f"{row.get('summary') or ''} {row.get('description') or ''}")) if w not in common][:12]
        hits = []
        if words:
            pairs = [piece for word in words for piece in ("-e", word)]
            hits = [line.strip() for line in
                    git_out(root, "grep", "-l", "-i", "-F", *pairs, timeout=120).splitlines()
                    if line.strip()]
        # git grep svarar "något av orden", och ett enda löst ord ger träff i nästan
        # allt (mätt: 130 av 131 ärenden "mappade", README som svar på "Boka
        # avstämning"). Bara två sorters svar räknas som en koppling:
        #   - ordet står i filens namn (det är vad filen handlar om), eller
        #   - minst två av ärendets ord står i filens text.
        # Ett ensamt ord i texten är brus och lämnas till "oklart var"-listan.
        named, weak = [], []
        for hit in hits:
            low = hit.lower()
            if any(w in low for w in words):
                named.append(hit)
                continue
            try:
                text = (root / hit).read_text(errors="replace").lower()
            except (OSError, UnicodeError):
                continue
            if sum(1 for w in words if w in text) >= 2:
                weak.append(hit)
        top = (named + weak)[:3]
        mentioned.update(top)
        entry = {"key": row.get("key"), "summary": row.get("summary"),
                 "status": row.get("statusName") or "", "pool": pool, "files": top}
        (mapped if top else unmapped).append(entry)
    silent = [p for p in listed if p not in mentioned]
    return {"project": project.upper(), "root": str(root), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "files": len(listed), "issues": len(rows[:limit]),
            "mapped": len(mapped), "unmapped": len(unmapped),
            "map": mapped, "missing": [{"key": e["key"], "summary": e["summary"], "pool": e["pool"]}
                                       for e in unmapped],
            # Hela listan: kapades den tyst såg 60 av 74 filer ut som alla (mätt). Ett
            # tak behövs bara om filerna är många fler än så -- och då skall det stå.
            "silent": silent, "silentCount": len(silent),
            "issuesTotal": len(rows)}


# Kodkartans språk: en rad per ändelse, för raden är svaret på "vilka språk läser kartan?".
# Den som vill ha ett språk till lägger till en rad -- inte en parser.
SOURCE_LANGS = {
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".cs": "C#", ".py": "Python", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript", ".vue": "Vue", ".svelte": "Svelte",
    ".rs": "Rust", ".go": "Go", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++",
    ".hpp": "C++", ".hh": "C++", ".m": "Objective-C", ".mm": "Objective-C",
    ".gd": "GDScript", ".lua": "Lua", ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang",
    ".hs": "Haskell", ".dart": "Dart", ".r": "R", ".jl": "Julia", ".pl": "Perl",
}
# Kataloger som aldrig är någons kod: beroenden, byggutdata, cachar. `bin` är INTE med --
# det heter en mapp i både Python- och C#-projekt, och GodJIRAs egen motor bor i en.
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "vendor", "target", "build", "dist",
             "out", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
             ".gradle", "obj", ".next", ".nuxt", "coverage", "Pods", "deps", "_build",
             ".tox", ".idea", ".vscode", "site-packages", ".dart_tool", "tmp"}
# Ett mönster per språkfamilj, inte en parser per språk: alla importer ser ut som något av
# de här. Fångstgruppen är det som pekas på -- ett paket, en modul eller en filsökväg.
IMPORT_PATTERNS = (
    r"^\s*(?:import|from)\s+(?:static\s+)?([\w.]+)",                 # Java, Python, Kotlin, Scala
    r"^\s*import\s+(?:type\s+)?\{[^}]*\}\s*from\s*['\"]([^'\"]+)['\"]",
    r"^\s*(?:import|export)\s+[^;\n]*?from\s*['\"]([^'\"]+)['\"]",   # TS, JS (Vue/Svelte också)
    r"require\(\s*['\"]([^'\"]+)['\"]\s*\)",                    # JS, TS, PHP, Ruby
    r"^\s*using\s+([\w.]+)\s*;",                                    # C#
    r"^\s*use\s+([\w:]+)",                                          # Rust
    r"^\s*use\s+([\w\\]+)\s*;",                                    # PHP-namnrymder
    r"^\s*import\s+(?:\w+\s+)?['\"]([^'\"]+)['\"]",               # Go (en rad)
    r"^\s*(?:require|require_relative|load)\s+\(?\s*['\"]([^'\"]+)['\"]",  # Ruby
    r"^\s*#include\s*[<\"]([^>\"]+)[>\"]",                         # C, C++
    r"(?:^|[^\w.])(?:extends|preload|load)\s*\(?\s*['\"]([^'\"]+)['\"]",  # GDScript
)
# \ufeff: en del filer har bytemarkeringsmärket före första raden, och då matchar inte
# ^\s* -- filen hamnade i sin mapp i stället för i sitt paket (mätt: tre vägar i AutoCore).
DECLARED_MODULE = (r"^\ufeff?\s*package\s+([\w.]+)\s*;",
                   r"^\ufeff?\s*namespace\s+([\w.]+)\s*[{;]")


# Ordnad lista över vad ett ord är, för referensläsningen: språk importerar inte alltid.
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def module_of_path(path: Path, root: Path) -> str:
    """Modulen en fil hör till: det deklarerade paketet om filen har ett, annars katalogen.

    Java och C# namnger sina moduler i filen, och då är det namnet sanningen. Alla andra
    språk låter katalogen vara modul, och då är katalogen svaret. Filen i roten blir
    "(utan paket)" -- samma ord som förut, för det är samma sak.
    """
    here = path.parent.relative_to(root)
    return str(here).replace("\\", "/") if here.parts else "(utan paket)"


def code_map(root: Path) -> dict:
    """Kodkartan: modulerna som noder, importerna som vägar, lagda i lager.

    Frågan den svarar på är Alex egen: hur hänger koden ihop? Noden är en modul -- det
    deklarerade paketet i Java och C#, annars katalogen -- och vägen är att en modul
    nämner en annan. Lagret (x) är hur djupt modulen ligger i beroendekedjan: en väg går
    alltid åt höger, som i en ritning över ett flöde.

    ponytail: importerna läses med en rad mönster per språkfamilj i stället för med en
    parser per språk, och en nämnd modul slås upp mot det som finns i repot. Det räcker
    för "hur hänger koden ihop"; räcker det inte i något språk (fel vägar där) är steget
    upp en riktig parser för just det språket -- inte fler mönster här.
    """
    root = Path(root)
    files, by_lang = [], {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        lang = SOURCE_LANGS.get(path.suffix.lower())
        if not lang:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        files.append(path)
        by_lang[lang] = by_lang.get(lang, 0) + 1
    if not files:
        return {"root": str(root), "nodes": [], "edges": [], "layers": 0, "files": 0,
                "note": "kodkartan hittade inga källfiler i repot ({})".format(
                    ", ".join(sorted(SOURCE_LANGS.values())))}
    package_of, classes_of, imports_of, words_of = {}, {}, {}, {}
    for path in files:
        try:
            source = path.read_text(errors="replace")
        except OSError:
            continue
        declared = ""
        for pattern in DECLARED_MODULE:
            hit = re.search(pattern, source, re.M)
            if hit:
                declared = hit.group(1)
                break
        package = declared or module_of_path(path, root)
        package_of[path] = package
        classes_of.setdefault(package, []).append(path.stem)
        found = []
        for pattern in IMPORT_PATTERNS:
            found += re.findall(pattern, source, re.M)
        imports_of[path] = found
        # Orden sparas, inte texten: referensläsningen nedan frågar bara vilka namn filen
        # nämner, och en mängd ord per fil är billigare att hålla än hela källkoden.
        words_of[path] = set(WORD.findall(source))
    # Uppslagning: nodnamn, och filnamn -> dess modul. Ett filnamn kan finnas i flera
    # moduler; då vinner den sista, och det är samma eftergift som förut (där klassen
    # avgjorde). Utan den här tabellen hade Java-importen "import a.b.Db;" inte hittat
    # något alls, för den namnger typen och inte modulen.
    modules = set(classes_of)
    by_stem, by_name = {}, {}
    for path, package in package_of.items():
        by_stem[path.stem.lower()] = package
        # by_name är den versalkänsliga: en typreferens skrivs med sin versal (Db, Model,
        # WorldMapView) medan vanliga ord står i gemener. Utan det blev ordet "app" en väg
        # till src/ui/app -- mätt: 143 vägar mellan två moduler i sonix som inte hör ihop.
        by_name[path.stem] = package

    def resolve(name: str):
        """Det nämnda namnet -> en modul i repot, eller inget alls."""
        clean = name.strip().strip("'\"").replace("::", ".").replace("\\", "/")
        clean = re.sub(r"^(crate|self|super|crate::)\.", "", clean)
        clean = clean.split("://")[-1]                     # res:// (Godot), http:// o.s.v.
        # Filen pekas på med sin ändelse: preload("res://main.gd"), import x from "./db.ts".
        # Utan det här blir sista biten "gd" och inte "main", och ingen modul hittas.
        clean = re.sub(r"\.(" + "|".join(e.lstrip(".") for e in SOURCE_LANGS) + r")$", "",
                       clean, flags=re.I)
        clean = clean.lstrip(".").strip("/")
        if not clean:
            return None
        if clean in modules:                       # hela namnet är modulen
            return clean
        stem = re.split(r"[./]", clean)[-1].lower()
        if stem in by_stem:                        # typen/klassen/filen, som i Java
            return by_stem[stem]
        # Längsta modul som namnet börjar med: "com.wac.autocore.data.Db" -> paketet.
        head = [m for m in modules if clean == m or re.match(re.escape(m) + r"[./]", clean)]
        if head:
            return max(head, key=len)
        # Sista biten av en sökväg, för moduler som nämns med sitt paketnamn (Go, TS).
        tail = [m for m in modules if m.endswith("/" + clean) or m.endswith("." + clean)]
        if tail:
            return max(tail, key=len)
        deepest = [m for m in modules if m.split("/")[-1] == stem or m.split(".")[-1] == stem]
        return max(deepest, key=len) if deepest else None

    weight = {}
    for path, package in package_of.items():
        for target in imports_of.get(path, []):
            other = resolve(target)
            if other and other != package:
                weight[(package, other)] = weight.get((package, other), 0) + 1
    # Referenserna: ett språk importerar inte alltid. GDScript säger "extends
    # WorldMapView" och filen heter WorldMapView.gd -- samma namn, ingen import. Tabellen
    # filnamn -> modul finns redan, så en fil som nämner ett annat filnamn får en väg.
    # ponytail: ett namn som två moduler delar pekar på den sista av dem, och ett vanligt
    # ord kan sammanfalla med ett filnamn. Räcker det inte är steget upp riktiga
    # deklarationer per språk (class/struct/trait), inte fler ord här.
    for path, package in package_of.items():
        for word in words_of.get(path, ()):
            other = by_name.get(word)
            if other and other != package and path.stem != word:
                weight[(package, other)] = weight.get((package, other), 0) + 1
    edges = [{"from": a, "to": b, "weight": n} for (a, b), n in sorted(weight.items())]
    # Lagret: en kant går alltid åt höger. Moduler som beroende av varandra i en slinga
    # finns i riktig kod, och en utjämning ("mottagaren ett steg längre fram") växer då
    # utan gräns -- mätt: 71 lager och x = 21800. Här plockas i stället de moduler som
    # ingen pekar på ut först, varv för varv; det som bara är en slinga hamnar på samma.
    pairs = {(edge["from"], edge["to"]) for edge in edges}
    order, visited = [], set()

    def visit(node):
        visited.add(node)
        for other in sorted(t for a, t in pairs if a == node):
            if other not in visited:
                visit(other)
        order.append(node)

    for node in sorted(classes_of):
        if node not in visited:
            visit(node)
    rank = {node: i for i, node in enumerate(order)}
    kept = [(a, b) for a, b in pairs if rank[b] < rank[a]]
    depth = {package: 0 for package in classes_of}
    for node in sorted(classes_of, key=lambda n: -rank[n]):
        for a, b in kept:
            if a == node:
                depth[b] = max(depth[b], depth[node] + 1)
    # Etiketten: det gemensamma ledet bort ("com.wac.autocore."), kvar blir "ui.views".
    # Både punkt och snedstreck är led, för Java namnger med punkter och katalognamn gör
    # det inte. Minst ett led lämnas kvar, annars blir etiketten tom.
    shared: list = []
    names = sorted(classes_of)
    parts = [re.split(r"[./]", name) for name in names if re.search(r"[./]", name)]
    if len(parts) > 1:
        while (all(len(p) > len(shared) for p in parts)
               and len({p[len(shared)] for p in parts}) == 1):
            shared.append(parts[0][len(shared)])
    def short(package: str) -> str:
        if not shared:
            return package
        return "/".join(re.split(r"[./]", package)[len(shared):]) or package
    nodes, seen = [], {}
    for package in names:
        slot = seen.get(depth[package], 0)
        seen[depth[package]] = slot + 1
        nodes.append({"key": package, "name": short(package),
                      "type": "{} filer".format(len(classes_of[package])),
                      "x": 40 + depth[package] * 320, "y": 40 + slot * 120,
                      "examples": sorted(classes_of[package])[:6]})
    return {"root": str(root), "nodes": nodes, "edges": edges, "layers": max(depth.values()) + 1,
            "files": len(files), "packages": len(classes_of), "edgesCount": len(edges),
            "languages": [{"name": name, "files": n} for name, n in sorted(by_lang.items(),
                                                                          key=lambda kv: -kv[1])],
            "prefix": ".".join(shared)}


def graph_build(root: Path, project: str, scan: dict) -> dict:
    """Kunskapsgrafen: paketen, filerna och ärendena i samma bild, med sina relationer.

    Kodkartan vet hur paketen hänger ihop, scanningen vet vilka filer varje ärende rör --
    två halvor av samma svar. Här blir de en graf: noder för paket, fil och ärende, och
    kanter som säger varför de hör ihop (använder, ligger-i, nämner). Frågan den svarar på
    är utvecklarens och agentens: vad hänger ihop med vad, och var skall ändringen göras?

    ponytail: inget Jira-anrop. Grafen byggs ur scan-filen och källkoden, så den kan byggas
    om hur ofta som helst och av vem som helst. Ärendena kommer ur scan-filen -- finns den
    inte säger kommandot det i stället för att visa en halv graf.
    """
    code = code_map(root)
    entries = list((scan or {}).get("map") or [])
    skip = list((scan or {}).get("missing") or [])
    files = sorted({path for entry in entries for path in (entry.get("files") or [])} |
                   set((scan or {}).get("silent") or []))
    package_of = {}
    for rel in files:
        try:
            source = (Path(root) / rel).read_text(errors="replace")
        except (OSError, UnicodeError):
            continue
        hit = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.M)
        package_of[rel] = hit.group(1) if hit else "(utan paket)"

    entities, relations, known = [], [], set()
    for node in code.get("nodes") or []:
        known.add(node["key"])
        entities.append({"id": "paket:" + node["key"], "type": "package", "name": node["name"],
                         "full": node["key"], "label": node.get("type") or "",
                         "examples": list(node.get("examples") or [])})
    for rel in files:
        package = package_of.get(rel)
        entities.append({"id": "fil:" + rel, "type": "file", "name": Path(rel).name,
                         "path": rel, "package": package})
        if not package:
            continue
        if package not in known:      # ett paket kodkartan inte såg (annat språk, ingen import)
            known.add(package)
            entities.append({"id": "paket:" + package, "type": "package", "name": package,
                             "full": package, "label": "", "examples": []})
        relations.append({"from": "fil:" + rel, "to": "paket:" + package, "kind": "ligger-i"})
    for edge in code.get("edges") or []:
        relations.append({"from": "paket:" + edge["from"], "to": "paket:" + edge["to"],
                          "kind": "använder", "weight": edge.get("weight", 1)})
    for entry in entries:
        issue = "ärende:" + str(entry.get("key"))
        entities.append({"id": issue, "type": "issue", "name": entry.get("key"),
                         "summary": entry.get("summary") or "", "status": entry.get("status") or "",
                         "pool": entry.get("pool") or ""})
        for rel in entry.get("files") or []:
            relations.append({"from": issue, "to": "fil:" + rel, "kind": "nämner"})
    for entry in skip:
        entities.append({"id": "ärende:" + str(entry.get("key")), "type": "issue",
                         "name": entry.get("key"), "summary": entry.get("summary") or "",
                         "status": "", "pool": entry.get("pool") or "", "unclear": True})

    incoming = {}
    for rel in relations:
        if rel["kind"] == "använder":
            # Nyckeln utan "paket:"-prefixet: navet skall gå att läsa och jämföra rakt av.
            target = str(rel["to"]).split(":", 1)[-1]
            incoming[target] = incoming.get(target, 0) + rel.get("weight", 1)
    return {
        "ok": True, "project": (project or "").upper(), "repo": repo_slug_of_dir(str(root)) or str(root),
        "root": str(root), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "entities": entities, "relations": relations,
        "counts": {"paket": sum(1 for e in entities if e["type"] == "package"),
                   "filer": sum(1 for e in entities if e["type"] == "file"),
                   "ärenden": sum(1 for e in entities if e["type"] == "issue"),
                   "relationer": len(relations)},
        "issuesWithoutFile": len(skip),
        "filesWithoutIssue": len((scan or {}).get("silent") or []),
        # Det agenten behöver först: vilka paket allt annat lutar sig mot.
        "hubs": [{"package": key, "usedBy": count}
                 for key, count in sorted(incoming.items(), key=lambda kv: (-kv[1], kv[0]))[:8]],
    }


# --------------------------------------------------------------- flödeskartan
#
# Flödet ritat som en artefakt (Archify): n8n:s egna noder, kanter, banor och
# kort, lästa ur n8n:s databas. Panelen visar artefakten; n8n äger flödet.
#
# Raderna nedan är panelens svenska källrader. En rad som saknar språket faller
# tillbaka på engelska -- "så långt det går, annars engelska". Nodnamnen kommer
# från n8n och översätts aldrig: de är någon annans ord.

FLOWMAP_STRINGS = {
    "Väckt av": {"en": "Started by", "de": "Gestartet von", "es": "Iniciado por",
                 "fr": "Déclenché par", "it": "Avviato da", "nl": "Gestart door",
                 "pl": "Uruchamiane przez", "pt": "Iniciado por"},
    "Kontext": {"en": "Context", "de": "Kontext", "es": "Contexto", "fr": "Contexte",
                "it": "Contesto", "nl": "Context", "pl": "Kontekst", "pt": "Contexto"},
    "Stegen": {"en": "The steps", "de": "Die Schritte", "es": "Los pasos",
               "fr": "Les étapes", "it": "I passi", "nl": "De stappen",
               "pl": "Kroki", "pt": "Os passos"},
    "Sammanställning": {"en": "Summary", "de": "Zusammenfassung", "es": "Resumen",
                        "fr": "Synthèse", "it": "Riepilogo", "nl": "Samenvatting",
                        "pl": "Podsumowanie", "pt": "Resumo"},
    "Rapportering": {"en": "Reporting", "de": "Meldung", "es": "Informe",
                     "fr": "Rapport", "it": "Rendicontazione", "nl": "Rapportage",
                     "pl": "Raportowanie", "pt": "Relatório"},
    "trycker själv": {"en": "you press it", "de": "du drückst", "es": "lo pulsas",
                      "fr": "tu le lances", "it": "lo avvii", "nl": "je drukt erop",
                      "pl": "uruchamiasz", "pt": "você aciona"},
    "schemat": {"en": "on a schedule", "de": "nach Zeitplan", "es": "por horario",
                "fr": "sur horaire", "it": "a orario", "nl": "op schema",
                "pl": "z harmonogramu", "pt": "por horário"},
    "projekt + verktygskatalog": {"en": "project + tool directory",
                                  "de": "Projekt + Werkzeugkatalog",
                                  "es": "proyecto + catálogo de herramientas",
                                  "fr": "projet + catalogue d'outils",
                                  "it": "progetto + catalogo strumenti",
                                  "nl": "project + gereedschapsmap",
                                  "pl": "projekt + katalog narzędzi",
                                  "pt": "projeto + catálogo de ferramentas"},
    "räknar ihop": {"en": "adds it up", "de": "rechnet zusammen",
                    "es": "lo suma", "fr": "fait le total", "it": "somma",
                    "nl": "telt op", "pl": "podsumowuje", "pt": "soma"},
    "POST till panelen": {"en": "POST to the panel", "de": "POST an die Oberfläche",
                          "es": "POST al panel", "fr": "POST vers le panneau",
                          "it": "POST al pannello", "nl": "POST naar het paneel",
                          "pl": "POST do panelu", "pt": "POST para o painel"},
    "Manuell trigger": {"en": "Manual trigger", "de": "Manueller Auslöser",
                        "es": "Disparador manual", "fr": "Déclencheur manuel",
                        "it": "Trigger manuale", "nl": "Handmatige trigger",
                        "pl": "Wyzwalacz ręczny", "pt": "Gatilho manual"},
    "Schema": {"en": "Schedule", "de": "Zeitplan", "es": "Horario", "fr": "Horaire",
               "it": "Orario", "nl": "Schema", "pl": "Harmonogram", "pt": "Horário"},
    "Projekt": {"en": "Project", "de": "Projekt", "es": "Proyecto", "fr": "Projet",
                "it": "Progetto", "nl": "Project", "pl": "Projekt", "pt": "Projeto"},
    "Varifrån": {"en": "Where this comes from", "de": "Woher das kommt",
                 "es": "De dónde viene", "fr": "D'où cela vient",
                 "it": "Da dove viene", "nl": "Waar dit vandaan komt",
                 "pl": "Skąd to jest", "pt": "De onde vem"},
    "noder": {"en": "nodes", "de": "Knoten", "es": "nodos", "fr": "nœuds",
              "it": "nodi", "nl": "knopen", "pl": "węzły", "pt": "nós"},
    "kopplingar": {"en": "connections", "de": "Verbindungen", "es": "conexiones",
                   "fr": "liaisons", "it": "collegamenti", "nl": "verbindingen",
                   "pl": "połączenia", "pt": "ligações"},
}

# n8n:s nodtyp -> (bana, sort i artefakten, undertext). Sorterna är de som finns i
# Archifys egna exempel: att hitta på fler ger ingen stil, bara en okänd ruta.
FLOWMAP_KIND = {
    "scheduleTrigger": ("vackt", "external", "schemat"),
    "manualTrigger": ("vackt", "external", "trycker själv"),
    "set": ("kontext", "backend", "projekt + verktygskatalog"),
    "executeCommand": ("steg", "backend", ""),
    "code": ("sammanst", "backend", "räknar ihop"),
    "httpRequest": ("rapport", "messagebus", "POST till panelen"),
}
FLOWMAP_LANES = (("vackt", "Väckt av"), ("kontext", "Kontext"), ("steg", "Stegen"),
                 ("sammanst", "Sammanställning"), ("rapport", "Rapportering"))
FLOWMAP_COL_MAX = 5      # artefaktens rutnät har sex kolumner, 0..5 (deras schema)



# Ramen runt artefakten: deras egen ordlista (viewer.message.* -> svenska). Nycklarna
# är Archifys kanoniska nycklar och valideras mot deras engelska katalog -- en felstavad
# nyckel fäller renderingen, vilket är precis vad man vill. En nyckel som inte står här
# faller tillbaka på engelska, så listan får vara hur kort som helst. Felmeddelandena
# (viewer.export.error.*, ~55 stycken) står med flit kvar på engelska: de syns bara när
# något går sönder, och en halvöversatt feltext är sämre än en engelsk.
FLOWMAP_CHROME_FILE = Path(__file__).resolve().parent / "flowmap-chrome.json"


def flowmap_chrome_read() -> dict:
    """Ordlistan för artefaktens ram, ett språk i taget.

    Ren data: 438 nycklar gånger åtta språk är inte kod. Saknas filen blir ramen
    engelsk, vilket är precis vad som händer när vi inte har en katalog för språket.
    """
    try:
        return json.loads(FLOWMAP_CHROME_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


FLOWMAP_CHROME = flowmap_chrome_read()


def flowmap_locale(lang: str) -> str:
    """Ramen på valt språk, annars engelska.

    Archify har bara engelska och kinesiska inbyggda. Ett språk vi inte har en
    katalog för behåller deras engelska ram -- det är hela regeln.
    """
    return lang if lang in FLOWMAP_CHROME else "en"


def flowmap_text(row: str, lang: str) -> str:
    """En rad på valt språk, annars engelska, annars den svenska källraden.

    Per rad, inte per språk: en halvfärdig översättning skall ge engelska på de
    rader som saknas -- inte svenska.
    """
    if not lang or lang == "sv":
        return row
    table = FLOWMAP_STRINGS.get(row) or {}
    return table.get(lang) or table.get("en") or row


def flowmap_columns(lanes: list) -> list:
    """Kedjan lagd över sex kolumner: lägsta lediga kolumn som inte går bakåt.

    Delar två noder en kolumn måste de ligga i olika banor -- det är därför
    banorna finns. Räcker inte kolumnerna till (en kedja längre än rutnätet) får
    vi en tom lista, och då får artefakten vara: ritytan visas i stället.
    """
    taken, out, prev = set(), [], 0
    for lane in lanes:
        col = prev
        while col <= FLOWMAP_COL_MAX and (lane, col) in taken:
            col += 1
        if col > FLOWMAP_COL_MAX:
            return []
        taken.add((lane, col))
        out.append(col)
        prev = col
    return out


def flowmap_chain(nodes: list, conns: dict) -> tuple:
    """Körordningen ur kopplingarna, plus de noder som inte ligger på kedjan.

    n8n:s egna positioner är musminne: två noder kan dela kolumn i filen och
    ändå köras i tur och ordning. Ordningen hämtas därför ur kanterna, och en
    parallell start (två triggers) hamnar vid sidan om kedjan, inte i den.
    """
    edges = {}
    for src, out in (conns or {}).items():
        for lane in (out.get("main") or []):
            for link in (lane or []):
                if link.get("node"):
                    edges.setdefault(src, []).append(link["node"])
    rooted = set()
    for target in edges.values():
        rooted.update(target)
    starts = [n["name"] for n in nodes if n["name"] not in rooted]
    if not starts:
        starts = [n["name"] for n in nodes[:1]]
    chain, seen = [], set()

    def walk(name):
        while name and name not in seen:
            seen.add(name)
            chain.append(name)
            step = edges.get(name) or []
            name = step[0] if len(step) == 1 else ""

    walk(starts[0])
    path = list(chain)          # berättelsen: bara kedjan, varje steg en riktig kant
    # En parallell start (två triggers) ligger inte på kedjan. Den sätts in precis
    # före sitt mål -- annars hamnade den sist, med en kant som pekar bakåt (mätt:
    # validatorn fällde det som "moves backward from col 5 to 0").
    for name in [n["name"] for n in nodes if n["name"] not in seen]:
        targets = edges.get(name) or []
        pos = next((chain.index(t) for t in targets if t in chain), len(chain))
        chain.insert(pos, name)
        seen.add(name)
    return chain, path


def flowmap_build(wid: str, lang: str = "en") -> dict:
    """n8n:s databas -> Archify workflow-IR. Läser skrivskyddat; skriver ingenting."""
    db = Path(os.environ.get("N8N_DB") or (Path.home() / ".n8n" / "database.sqlite"))
    con = sqlite3.connect("file:{}?mode=ro".format(db), uri=True)
    try:
        row = con.execute("select name, nodes, connections, active from workflow_entity"
                          " where id=?", (wid,)).fetchone()
    finally:
        con.close()
    if not row:
        raise ValueError("inget flöde med id {}".format(wid))
    title, nodes_j, conns_j, active = row
    everything = json.loads(nodes_j or "[]")
    conns = json.loads(conns_j or "{}")
    nodes = [n for n in everything if not (n.get("type") or "").endswith("stickyNote")]
    note = ""
    for n in everything:
        if (n.get("type") or "").endswith("stickyNote"):
            text = ((n.get("parameters") or {}).get("content") or "").strip()
            lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
            if lines:
                note = re.sub(r"<[^>]+>", "", lines[0])[:160]
                break

    chain, path = flowmap_chain(nodes, conns)
    by_name = {n["name"]: n for n in nodes}
    chain = [n for n in chain if n in by_name]
    lanes, subs, kinds = [], [], []
    for name in chain:
        kind = (by_name[name].get("type") or "").split(".")[-1]
        lane, sort, sub = FLOWMAP_KIND.get(kind, ("steg", "backend", ""))
        lanes.append(lane)
        kinds.append(sort)
        subs.append(flowmap_text(sub, lang) if sub else "")
    cols = flowmap_columns(lanes)
    if not cols:
        raise ValueError("kedjan ryms inte i rutnätet ({} noder)".format(len(chain)))

    def slug(text):
        low = text.lower()
        for a, b in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e")):
            low = low.replace(a, b)
        return re.sub(r"[^a-z0-9]+", "-", low).strip("-") or "n"

    out_nodes = [{"id": slug(name), "lane": lanes[i], "col": cols[i], "type": kinds[i],
                  "label": name, "sublabel": subs[i], "width": 190}
                 for i, name in enumerate(chain)]
    ids = {name: slug(name) for name in chain}
    edges = []
    for src, targets in (conns or {}).items():
        if src not in ids:
            continue
        for lane in (targets.get("main") or []):
            for link in (lane or []):
                to = link.get("node")
                if to in ids:
                    edges.append({"id": "e-" + ids[src] + "-" + ids[to], "from": ids[src],
                                  "to": ids[to], "role": "main", "variant": "default"})

    cards = [{"dot": "cyan", "title": flowmap_text("Väckt av", lang), "items": [
        it for it in [flowmap_text("Manuell trigger", lang) if any(
            (by_name[n].get("type") or "").endswith("manualTrigger") for n in chain) else "",
            flowmap_text("Schema", lang) if any(
                (by_name[n].get("type") or "").endswith("scheduleTrigger") for n in chain) else ""]
        if it]}]
    for name in chain:
        kind = (by_name[name].get("type") or "").split(".")[-1]
        params = by_name[name].get("parameters") or {}
        if kind == "executeCommand" and params.get("command"):
            cards.append({"dot": "cyan", "title": name,
                          "items": [str(params["command"]).replace("\n", " ").strip()[:220]]})
        if kind == "httpRequest":
            cards.append({"dot": "cyan", "title": name,
                          "items": ["%s %s" % (params.get("method") or "GET", params.get("url") or ""),
                                    "jsonBody " + str(params.get("jsonBody") or "")[:120]]})
    # Länken avgör vilket projekt stegen läser; den hör därför i "varifrån" och inte
    # som en gissning i varje stegkommando.
    linked = ["%s %s → %s" % (flowmap_text("Projekt", lang), name, (link or {}).get("project"))
              for name, link in sorted(load_links().items()) if (link or {}).get("project")]
    cards.append({"dot": "cyan", "title": flowmap_text("Varifrån", lang), "items": [
        "n8n /api %s" % wid,
        "%s: %d · %s: %d" % (flowmap_text("noder", lang), len(chain),
                             flowmap_text("kopplingar", lang), len(edges))] + linked})

    return {
        "schema_version": 2,
        "diagram_type": "workflow",
        "meta": {"title": title, "subtitle": note or "",
                 "output": "reports/flow.html",
                 # Ramen (deras UI) på vårt språk när vi har en katalog för det, annars
                 # deras engelska. Våra egna rader ligger redan översatta i IR:en.
                 "locale": flowmap_locale(lang),
                 "translations": FLOWMAP_CHROME.get(lang, {}),
                 "animation": "trace", "visual_preset": "signal-flow",
                 "quality_profile": "standard"},
        "lanes": [{"id": lid, "label": flowmap_text(label, lang)} for lid, label in FLOWMAP_LANES],
        "mainPath": [ids[name] for name in path if name in ids],
        "nodes": out_nodes,
        "edges": edges,
        "cards": cards,
    }


def flowmap_binary() -> str:
    """Var Archify bor: miljövariabeln först, annars den installerade kopian."""
    # ponytail: samma sökvägar står också i panelen och i install.sh -- flytta till
    # en delad konstant om de behöver ändras en gång till.
    root = Path.home() / ".local/share/godjira/vendor"
    for path in (os.environ.get("ARCHIFY") or "",
                 str(root / "archify" / "archify" / "bin" / "archify.mjs"),
                 str(root / "archify" / "bin" / "archify.mjs")):
        if path and Path(path).exists():
            return path
    return ""


def flowmap_render(ir: dict, out: Path) -> dict:
    """Kör Archifys renderare på IR:en och lämnar tillbaka deras kvitto."""
    binary = flowmap_binary()
    node = shutil.which("node")
    if not binary:
        return {"ok": False, "diagnostics": [{"code": "flowmap/no-archify",
                "message": "Archify saknas (ARCHIFY=<sökväg>, eller kör install.sh)"}]}
    if not node:
        return {"ok": False, "diagnostics": [{"code": "flowmap/no-node",
                "message": "node saknas"}]}
    out.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(ir, handle, ensure_ascii=False)
        handle.close()
        proc = subprocess.run([node, binary, "finalize", "workflow", handle.name, str(out),
                               "--quality", "standard", "--json"],
                              capture_output=True, text=True, timeout=300)
    finally:
        os.unlink(handle.name)
    try:
        return json.loads(proc.stdout or "")
    except ValueError:
        return {"ok": False, "diagnostics": [{"code": "flowmap/no-receipt",
                "message": ((proc.stderr or "") + (proc.stdout or ""))[-400:]}]}


def cmd_flowmap(args) -> int:
    """Flödets artefakt: n8n -> IR -> HTML. Utan --out skrivs bara IR:en."""
    lang = (getattr(args, "lang", "") or "en").strip().lower()
    try:
        ir = flowmap_build(args.workflow, lang)
    except (ValueError, OSError, sqlite3.Error) as why:
        print(json.dumps({"ok": False, "why": str(why)}, ensure_ascii=False))
        return 1
    if getattr(args, "ir", ""):
        Path(args.ir).expanduser().write_text(
            json.dumps(ir, ensure_ascii=False, indent=1), encoding="utf-8")
    if not getattr(args, "out", ""):
        print(json.dumps({"ok": True, "lang": lang, "nodes": len(ir["nodes"]),
                          "edges": len(ir["edges"]), "cards": len(ir["cards"])}))
        return 0
    receipt = flowmap_render(ir, Path(args.out).expanduser())
    # Innehållsgrindarna avgör. browser-check klagar på att sidan är högre än
    # skärmen (mätt: 1474 px i en 1320 px-ruta) -- artefakten är en webbsida som
    # skrollas, och i panelens ruta gör den det med flit. Att stympa diagrammet
    # för att slippa skrollning vore att göra sämre bild för en felaktig regel.
    gates = receipt.get("gates") or {}
    content_ok = all(gates.get(g) == "pass" for g in ("validate", "deliver", "check"))
    ok = bool((receipt.get("artifact") or {}).get("bytes")) and content_ok
    if ok and (gates.get("browser-check") or "") not in ("pass", "not-run"):
        receipt["note"] = "artefakten skrollas (browser-check: sidan är högre än rutan)"
    receipt["ok"] = ok            # kvittot bär sitt eget svar, även för --json
    if getattr(args, "json", False):
        print(json.dumps(receipt, ensure_ascii=False))      # hela, aldrig kapad
    elif ok:
        print("flödet ritat: %d noder, %d kort -> %s" % (len(ir["nodes"]), len(ir["cards"]), args.out))
    else:
        for item in (receipt.get("diagnostics") or [])[:4]:
            print("  %s: %s" % (item.get("code"), (item.get("message") or "")[:200]))
        print("  ingen artefakt skrevs (%s)" % (receipt.get("failedStage") or "?"))
    return 0 if ok else 1


def chosen_repo_dir(args) -> str:
    """Repot kommandot gäller, när det är namngivet.

    Föll förut tillbaka på "den enda länken" även när ett namn gavs, och den enda länken
    är AutoCore -- alltså svarade kommandot för AutoCore hur man än frågade (mätt:
    obsidian-valvet, hermes-skills och OmaNotation fick alla AutoCore-kartan, 22 paket).
    Ett namn som inte känns igen skall ge ett besked, inte ett annat repos svar.
    """
    root = plan_repo_dir(args)
    if root or (getattr(args, "repo_name", "") or "").strip():
        return root
    linked = [dict(value or {}, repo=key) for key, value in load_links().items()
              if (value or {}).get("project")]
    return local_clone(linked[0]["repo"]) if len(linked) == 1 else ""


def cmd_codemap(args) -> int:
    """Kodkartan för repot: hur paketen hänger ihop, ritad som en tavla."""
    root = chosen_repo_dir(args)
    if not root or not Path(root).is_dir():
        # Namnet står i beskedet: "no local copy" sade inte vilket repo som inte hade
        # någon kopia, och den som frågade om ett repo fick ett svar om ett annat.
        say(args, {"ok": False, "error": "no local copy of {} to map".format(
            (getattr(args, "repo_name", "") or "the repo").strip())},
            ["no local copy of {} to map — link it, or give --repo <dir>".format(
                (getattr(args, "repo_name", "") or "the repo").strip())])
        return 1
    graph = code_map(Path(root))
    graph["repo"] = repo_slug_of_dir(root) or root
    graph["ok"] = bool(graph["nodes"])
    say(args, graph, [
        "{}: {} files in {} packages, {} connections between them.".format(
            graph["repo"], graph.get("files", 0), graph.get("packages", 0),
            graph.get("edgesCount", 0)),
    ] if graph["nodes"] else [graph.get("note", "no map")])
    return 0 if graph["nodes"] else 2


# --------------------------------------------------- filhanteringen: repot i panelen
# Panelen är en dörr mot repot, inte en editor: allt här är skrivskyddat. En sökväg ur en
# förfrågan är en GRÄNS -- den skall peka på en fil inuti repot, annars läser panelen vad
# som helst på maskinen.

FILE_MAX_BYTES = 400_000      # källkod, inte en datadump: mer än så är inte en fil man läser
FILES_MAX = 3000
COMMITS_MAX = 200
COMMIT_FILES_MAX = 40


def repo_dir_or_say(args, verb: str):
    """Samma grind som kodkartan: ett okänt repo får ett besked om sig självt."""
    root = chosen_repo_dir(args)
    if not root or not Path(root).is_dir():
        name = (getattr(args, "repo_name", "") or "the repo").strip()
        say(args, {"ok": False, "error": "no local copy of {} to {}".format(name, verb)},
            ["no local copy of {} to {} — link it, or give --repo <dir>".format(name, verb)])
        return None
    return Path(root)


def safe_repo_path(root: Path, raw: str):
    """(path, "") eller (None, skäl). Absolute vägar och .. nekas, och resolve() följer
    symlänkar -- en länk ut ur repot är samma hål som en ..-väg."""
    text = (raw or "").strip().lstrip("/")
    if not text:
        return None, "no path given"
    try:
        bas = Path(root).resolve()
        target = (bas / text).resolve()
    except OSError as exc:
        return None, "the path could not be resolved ({})".format(type(exc).__name__)
    if target != bas and bas not in target.parents:
        return None, "the path points outside the repo"
    if not target.is_file():
        return None, "no such file in the repo"
    return target, ""


def repo_files(root: Path) -> list:
    """Filerna git känner till. Utan git läses katalogen i stället, med byggskräp
    bortskalat -- ett projekt behöver inte vara ett git-repo (skolans inlämning är en zip)."""
    listed = [line.strip() for line in git_out(root, "ls-files").splitlines() if line.strip()]
    if listed:
        return listed
    skip = set(SKIP_DIRS) | {".git", "node_modules", ".venv", "__pycache__"}
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and not (skip & set(p.relative_to(root).parts)))


def repo_commit_log(root: Path, limit: int, rel: str = "") -> list:
    """Commitnoterna: en post per commit med författare, datum, ämne, hela meddelandet och
    filerna den rörde. Ett anrop, inte ett per commit."""
    fmt = "%x1e%h%x1f%an%x1f%ad%x1f%s%x1f%b%x1f"
    argv = ["log", "-n", str(limit), "--date=short", "--pretty=format:" + fmt, "--name-only"]
    if rel:
        argv += ["--", rel]
    out = git_out(root, *argv, timeout=60)
    poster = []
    for chunk in out.split("\x1e"):
        if not chunk.strip():
            continue
        fields = chunk.split("\x1f")
        if len(fields) < 5:
            continue
        # Mätt med od: sista fältet är "\n<filnamn>\n\n" -- namnen kommer FÖRST i det, och
        # filnamnens egen tomrad ligger efter. Att leta efter en tomrad före namnen gav
        # tom filnamnslista och filnamnen inbakade i meddelandet. Kroppen är allt mellan
        # ämnet och sista fältet, så ett meddelande med egen tomrad klarar sig.
        body = "\n".join(fields[4:-1]).strip()
        navn = [line.strip() for line in fields[-1].splitlines() if line.strip()]
        poster.append({"sha": fields[0].strip(), "author": fields[1], "date": fields[2],
                       "subject": fields[3], "body": body,
                       "files": navn[:COMMIT_FILES_MAX], "filesTotal": len(navn)})
    return poster


def cmd_files(args) -> int:
    """Filerna i repot: vad git känner till, i sorterad ordning."""
    root = repo_dir_or_say(args, "list files in")
    if not root:
        return 1
    filer = repo_files(root)
    limit = max(1, getattr(args, "limit", 0) or FILES_MAX)
    say(args, {"ok": True, "repo": repo_slug_of_dir(root) or str(root), "root": str(root),
               "count": len(filer), "files": filer[:limit]},
        ["{} files in {}".format(len(filer), root)])
    return 0


def cmd_file(args) -> int:
    """En fil ur repot: innehållet, med ärliga tak."""
    root = repo_dir_or_say(args, "read a file in")
    if not root:
        return 1
    begart = (getattr(args, "path", "") or "").strip()
    target, fel = safe_repo_path(root, begart)
    if not target:
        say(args, {"ok": False, "error": fel, "path": begart}, ["{}: {}".format(begart, fel)])
        return 1
    rel = target.relative_to(root.resolve()).as_posix()
    with open(target, "rb") as fh:
        raw = fh.read(FILE_MAX_BYTES + 1)
    klippt = len(raw) > FILE_MAX_BYTES
    raw = raw[:FILE_MAX_BYTES]
    # En binärfil är ingen förfrågan -- den är ett faktum. Panelen säger det i stället för
    # att visa skräp, och "ok" är sant: svaret är att filen inte är text.
    binart = b"\0" in raw
    text_ = "" if binart else raw.decode("utf-8", errors="replace")
    svar = {"ok": True, "repo": repo_slug_of_dir(root) or str(root), "path": rel,
            "bytes": target.stat().st_size, "binary": binart, "truncated": klippt,
            "lines": 0 if binart else text_.count("\n") + 1, "text": text_}
    say(args, svar, ["{}: {} bytes{}".format(rel, svar["bytes"],
                                             " (visar de första {} kB)".format(
                                                 FILE_MAX_BYTES // 1000) if klippt else "")])
    return 0


def cmd_commits(args) -> int:
    """Commitnoterna för repot -- eller för en fil: historiken är per fil i GitHub också."""
    root = repo_dir_or_say(args, "read the history of")
    if not root:
        return 1
    rel = (getattr(args, "path", "") or "").strip()
    if rel:
        target, fel = safe_repo_path(root, rel)     # samma grind: sökvägen är en gräns
        if not target:
            say(args, {"ok": False, "error": fel, "path": rel}, ["{}: {}".format(rel, fel)])
            return 1
        rel = target.relative_to(root.resolve()).as_posix()
    limit = max(1, min(COMMITS_MAX, getattr(args, "limit", 0) or 30))
    poster = repo_commit_log(root, limit, rel)
    say(args, {"ok": True, "repo": repo_slug_of_dir(root) or str(root), "path": rel,
               "count": len(poster), "commits": poster},
        ["{} commits{}{}".format(len(poster), " in " + rel if rel else "",
                                 "" if not poster else ": " + poster[0]["sha"] + " " + poster[0]["subject"])])
    return 0 if poster else 2


DIFF_MAX_LINES = 3000


def safe_sha(raw: str):
    """Ett sha ur en förfrågan: bara hex, alltså en commit och inget annat.

    Argumentet går som argv (aldrig genom ett skal), men git läser ett inledande
    bindestreck som en FLAGGA -- "--upload-pack=..." vore en commit som inte finns och ett
    kommando till. Grinden står här, en gång, för både panelen och MCP.
    """
    text = (raw or "").strip()
    return text if re.fullmatch(r"[0-9a-f]{4,40}", text) else None


def repo_diff(root: Path, sha: str, rel: str = "") -> dict:
    """Ändringen en commit gjorde, rad för rad, som git skriver den.

    En commit kan röra hundra filer: då kapas texten och SÄGER det. En halv diff som ser
    hel ut är värre än en som erkänner sitt tak.
    """
    argv = ["show", "--format=", "--patch", "--no-color", "--no-ext-diff", "--unified=3", sha]
    if rel:
        argv += ["--", rel]
    rader = git_out(root, *argv, timeout=120).splitlines()
    klippt = len(rader) > DIFF_MAX_LINES
    if klippt:
        rader = rader[:DIFF_MAX_LINES]
    return {"lines": len(rader), "total": len(rader) if not klippt else None,
            "truncated": klippt, "text": "\n".join(rader)}


def cmd_diff(args) -> int:
    root = repo_dir_or_say(args, "show the change in")
    if not root:
        return 1
    sha = safe_sha(getattr(args, "sha", "") or "")
    if not sha:
        say(args, {"ok": False, "error": "not a commit"},
            ["{}: not a commit".format(getattr(args, "sha", ""))])
        return 1
    rel = (getattr(args, "path", "") or "").strip()
    if rel:
        mål, fel = safe_repo_path(root, rel)
        if not mål:
            say(args, {"ok": False, "error": fel}, ["{}: {}".format(rel, fel)])
            return 1
        rel = mål.relative_to(root.resolve()).as_posix()
    svar = repo_diff(root, sha, rel)
    svar.update({"ok": True, "repo": repo_slug_of_dir(root) or str(root), "sha": sha, "path": rel})
    say(args, svar, ["{} {}s: {} lines{}".format(sha, rel or "the commit", svar["lines"],
                                                 " (cut)" if svar["truncated"] else "")])
    return 0


def cmd_graph(args) -> int:
    """Kunskapsgrafen för ett repo: allt vi vet, med sina relationer, i en fil.

    Skrivs till `~/.config/jira-flow/graph-<PROJEKT>.json` (0600), samma ställe och samma
    arbetsdelning som scanningen: kommandot läser, panelen och agenten läser filen.
    """
    project, _source = link_project(args)
    root = chosen_repo_dir(args)
    if not root or not Path(root).is_dir():
        # Namnet står i beskedet: "no local copy" sade inte vilket repo som inte hade
        # någon kopia, och den som frågade om ett repo fick ett svar om ett annat.
        say(args, {"ok": False, "error": "no local copy of {} to graph".format(
            (getattr(args, "repo_name", "") or "the repo").strip())},
            ["no local copy of {} to graph — link it, or give --repo <dir>".format(
                (getattr(args, "repo_name", "") or "the repo").strip())])
        return 1
    scan_path = SCAN_FILE / "scan-{}.json".format(project.upper())
    scan = {}
    if scan_path.exists():
        try:
            scan = json.loads(scan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            scan = {}
    if not scan.get("map") and not scan.get("missing"):
        say(args, {"ok": False, "error": "no scan to build the graph from",
                   "hint": "run: jira_flow scan --json"}, [])
        print("no scan to build the graph from — run `jira_flow scan` first (it reads Jira once)")
        return 1
    graph = graph_build(Path(root), project, scan)
    SCAN_FILE.mkdir(parents=True, exist_ok=True)
    path = SCAN_FILE / "graph-{}.json".format(graph["project"])
    try:
        path.write_text(json.dumps(graph, ensure_ascii=False, indent=2))
        os.chmod(path, 0o600)
        graph["savedTo"] = str(path)
    except OSError as exc:
        print("the graph could not be written: {}: {}".format(type(exc).__name__, exc))
        return 2
    counts = graph["counts"]
    with_files = [e for e in graph["entities"] if e["type"] == "issue" and not e.get("unclear")]
    sample = sorted(with_files, key=lambda e: -len([r for r in graph["relations"]
                                                    if r["from"] == e["id"]]))[:1]
    say(args, graph, [
        "{}: {} packages, {} files, {} issues — {} relations.".format(
            graph["repo"], counts["paket"], counts["filer"], counts["ärenden"], counts["relationer"]),
        "most used: " + " · ".join("{} ({})".format(h["package"].split(".")[-1], h["usedBy"])
                                   for h in graph["hubs"]),
        "{} issues point at no file, {} files are named by no issue.".format(
            graph["issuesWithoutFile"], graph["filesWithoutIssue"]),
    ] + (["e.g. {} touches {} files".format(sample[0]["name"], len(
        [r for r in graph["relations"] if r["from"] == sample[0]["id"]]))] if sample else []) + [
        "the graph is at {} (read it whole with: jira_flow graph --json)".format(path),
    ])
    return 0


def code_context(root: Path, words) -> tuple:
    """Filkartan ur git, kontrakten per fil, och hela texten för de närmaste filerna.

    `words` kommer ur önskemålet: filer vars sökväg nämner samma sak läses först.
    Allt som klipps sägs det om, så den som läser förslaget vet vad agenten såg.
    """
    listed = [line.strip() for line in git_out(root, "ls-files").splitlines() if line.strip()]
    if not listed:
        return "", {"files": 0, "error": "no files are tracked by git here"}
    # Ett ord ur anslaget kan stå i filens namn eller i filens text. Texten är det
    # säkrare svaret ("finns bokningen redan?" besvaras av den som skriver om den),
    # och git grep svarar på det utan att lämna det git följer.
    words = sorted(words)[:12]
    mentions = set()
    if words:
        pairs = [piece for word in words for piece in ("-e", word)]
        hits = git_out(root, "grep", "-l", "-i", "-F", *pairs)
        mentions = {line.strip() for line in hits.splitlines() if line.strip()}
    sizes, scored = {}, []
    for path in listed:
        try:
            sizes[path] = len((root / path).read_text(errors="replace").splitlines())
        except (OSError, UnicodeError):
            sizes[path] = -1
        low = path.lower()
        score = sum(3 for word in words if word in low)
        if any(marker in low for marker in CODE_MANIFESTS):
            score += 5
        if low.endswith(CODE_SUFFIXES):
            score += 1
        if path in mentions:
            score += 4          # filen nämner själv det anslaget handlar om
        if "/test" in low or low.startswith(("test", "spec/")):
            score -= 2          # en testfil beskriver vad som finns, men bygger inget
        if score > 0:
            scored.append((score, sizes[path], path))
    # Flest träffar först. Vid lika många träffar går den lilla filen före den stora:
    # den hinner läsas i sin helhet inom budgeten, medan en fil på 20 000 rader
    # ändå bara blir till kontraktsrader.
    scored.sort(key=lambda row: (-row[0], row[1]))
    picked = [row[2] for row in scored[:CODE_FILES]]

    map_lines = ["file map (git ls-files, {} files{}):".format(
        len(listed), ", {} biggest shown".format(CODE_MAP_MAX) if len(listed) > CODE_MAP_MAX else "")]
    for path in listed[:CODE_MAP_MAX]:
        map_lines.append("  {}  ({} lines)".format(path, sizes.get(path, -1)))
    text = ["\n".join(map_lines)]

    budget, read, clipped = CODE_BUDGET - len(text[0]), [], False
    for path in picked:
        try:
            raw = (root / path).read_text(errors="replace")
        except OSError as exc:
            text.append("### {} -- could not be read ({})".format(path, exc))
            continue
        header = "\n\n### {} ({} lines)".format(path, sizes.get(path, -1))
        if len(raw) + len(header) <= budget:
            text.append(header + "\n" + raw)
            read.append(path)
            budget -= len(raw) + len(header)
            continue
        # Inte plats: kontrakten ur filen säger ändå vad den innehåller.
        contract = [line for line in raw.splitlines() if line.strip() and CODE_DEF.match(line.strip())]
        clipped = True
        kept = contract[:40] if contract else raw.splitlines()[:20]
        text.append(header + " -- contracts only ({} of {} lines shown)\n{}".format(
            len(kept), sizes.get(path, -1), "\n".join(kept)))
        budget -= sum(len(line) + 1 for line in kept) + len(header)
        if budget <= 0:
            clipped = True
            break
    joined = "\n".join(text)
    info = {"files": len(listed), "mapShown": min(len(listed), CODE_MAP_MAX),
            "read": read, "picked": picked, "chars": len(joined),
            "mentions": sorted(mentions), "budget": CODE_BUDGET, "truncated": clipped}
    return joined, info


# ------------------------------------------------- repot och dess Jira-sida

def repo_slug(raw: str) -> str:
    """owner/name ur en git-fjärr, eller det namn som gavs.

    Tar https://github.com/owner/name.git, git@github.com:owner/name.git och ett
    naket namn (som blir namnet utan ägare -- en användare här har en ägare, och
    den slås upp ur gh när den behövs).
    """
    text = (raw or "").strip()
    hit = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", text)
    if hit:
        return "{}/{}".format(hit.group(1), hit.group(2))
    tail = text.rstrip("/")
    return tail[:-4] if tail.endswith(".git") else tail


def repo_slug_of_dir(root) -> str:
    """Repots namn på GitHub, ur den lokala kopians fjärr."""
    return repo_slug(git_out(root, "remote", "get-url", "origin"))


def local_clone(name: str) -> str:
    """Var en lokal kopia ligger, om det finns en.

    Ett namn, inte en sökning i hela hemmet: katalogerna i LOCAL_ROOTS, och fjärren
    jämförs -- en katalog som heter samma sak men pekar någon annanstans räknas inte.
    """
    slug = repo_slug(name).lower()
    short = slug.split("/")[-1]
    if not short:
        return ""
    for raw_root in LOCAL_ROOTS:
        root = Path(raw_root).expanduser()
        if not root.is_dir():
            continue
        seen = {}
        # Roten själv först: en kopia behöver inte heta samma som repot (hubben ligger
        # i sin plugin-katalog). Sedan kataloger som *heter* något av
        # namnet -- jämfört utan skiftläge, för katalogen heter AutoCore medan repot
        # heter autocore, och glob är skiftlägeskänsligt (mätt: gav ingen kopia alls).
        try:
            near = [p for p in root.iterdir() if short in p.name.lower()]
        except OSError:
            near = []
        for hit in [root] + near:
            seen[str(hit)] = hit
        for hit in seen.values():
            if not (hit / ".git").is_dir():
                continue
            remote = repo_slug_of_dir(hit).lower()
            # Ett naket namn har ingen ägare att jämföra med, så kortnamnet får räcka
            # (mätt: "GodJIRA" gav ingen träff mot "alexwest1981/GodJIRA").
            if remote == slug or (("/" not in slug) and remote.split("/")[-1] == short):
                return str(hit)
    return ""


def load_links() -> dict:
    if not LINKS_FILE.exists():
        return {}
    try:
        data = json.loads(LINKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_links(data: dict) -> None:
    """Skriv länkarna. Katalogen är samma som token bor i, alltså 0700/0600."""
    LINKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LINKS_FILE.parent.chmod(0o700)
    with LINKS_FILE.open("w", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    LINKS_FILE.chmod(0o600)


def link_for(name: str) -> dict:
    """Länken för ett repo: "owner/name" först, sedan ett naket namn."""
    slug = repo_slug(name).lower()
    if not slug:
        return {}
    links = load_links()
    for key, value in links.items():
        if str(key).lower() == slug:
            return dict(value or {}, repo=key)
    short = slug.split("/")[-1]
    for key, value in links.items():
        if str(key).lower().split("/")[-1] == short:
            return dict(value or {}, repo=key)
    return {}


# Vad en koppling behöver veta om kontot. Listan är Jiras egna behörighetsnamn.
PROJECT_PERMISSIONS = ("BROWSE_PROJECTS", "CREATE_ISSUES", "EDIT_ISSUES", "ASSIGN_ISSUES",
                       "TRANSITION_ISSUES", "MANAGE_SPRINTS_PERMISSION", "ADMINISTER_PROJECTS")


def path_of(url: str) -> str:
    """\"/rest/...\" ur en absolut rollänk -- jira_get tar en sökväg, inte en sajt."""
    at = str(url or "").find("/rest/")
    return str(url)[at:] if at >= 0 else ""


def project_caps(bridge, project: str) -> dict:
    """Kontot, rollen och vad man får i ett projekt.

    Kopplar man ett repo till en Jira skall API:t vara på plats först: den här läser
    kontot och projektet med samma token som resten använder, så en länk utan
    fungerande API kan inte skapas. Rollen kommer ur projektets egna roller; en
    next-gen-tavla kan svara att den inte har några, och då står behörigheterna kvar
    som svar i stället för en påhittad roll.

    ponytail: ett token per installation, inte per länk. Den dag två sajter skall
    kopplas samtidigt får länken bära sajt + tokenreferens i stället.
    """
    me = bridge.get("/rest/api/3/myself") or {}
    data = bridge.get("/rest/api/3/mypermissions?projectKey={}&permissions={}".format(
        project, ",".join(PROJECT_PERMISSIONS))) or {}
    have = {name: bool((row or {}).get("havePermission"))
            for name, row in (data.get("permissions") or {}).items()}
    account = str((me or {}).get("accountId") or "")
    role, note = "", ""
    try:
        for label, url in (bridge.get("/rest/api/3/project/{}/role".format(project)) or {}).items():
            body = bridge.get(path_of(url)) if path_of(url) else {}
            if any(str(((a or {}).get("actorUser") or {}).get("accountId") or "") == account
                   for a in (body or {}).get("actors") or []):
                role = label
                break
    except Exception as exc:      # noqa: BLE001 -- rollen är en upplysning, inte porten
        note = "{}: {}".format(type(exc).__name__, exc)
    return {"account": (me or {}).get("displayName") or "", "accountId": account,
            "role": role, "roleNote": note,
            "can": sorted(name for name, yes in have.items() if yes),
            "cannot": sorted(name for name, yes in have.items() if not yes)}


def admin_roles(bridge, project: str) -> list:
    """Rollerna i projektet och vilka som sitter i dem.

    Rollistan är projektets egen (Administrators, Developers …), inte sajtens. Det är
    den man behöver se för att veta vem man kan fråga om vad.
    """
    rows = []
    for label, url in (bridge.get("/rest/api/3/project/{}/role".format(project)) or {}).items():
        body = bridge.get(path_of(url)) if path_of(url) else {}
        actors = []
        for actor in (body or {}).get("actors") or []:
            # Namnet står på aktören själv, inte inuti actorUser (mätt: alla namn blev
            # tomma med den läsningen). `type` säger om det är en person eller en grupp.
            kind = str((actor or {}).get("type") or "").lower()
            actors.append({"name": str((actor or {}).get("displayName") or (actor or {}).get("name") or ""),
                           "kind": "grupp" if "group" in kind else "person",
                           "accountId": str((((actor or {}).get("actorUser") or {}).get("accountId"))
                                            or (((actor or {}).get("actorGroup") or {}).get("groupId")) or "")})
        rows.append({"role": label, "actors": actors})
    # Apparnas roller (addons) är maskiner, inte folk: de hamnar sist i listan.
    # Apparnas och gästernas roller är inte folk: de hamnar sist, efter människorna.
    return sorted(rows, key=lambda row: (any(w in row["role"].lower() for w in ("addon", "guest")),
                                         row["role"].lower()))


def admin_fields(bridge) -> list:
    """Fälten på sajten. Egna fält är de man annars letar efter i webbgränssnittet."""
    rows = bridge.get("/rest/api/3/field") or []
    return sorted(({"id": str(f.get("id") or ""), "name": str(f.get("name") or ""),
                    "custom": bool(f.get("custom")),
                    "type": str(((f.get("schema") or {}).get("type")) or "")}
                   for f in rows if isinstance(f, dict)),
                  key=lambda f: (not f["custom"], f["name"].lower()))


def admin_people(bridge, query: str) -> list:
    """Personer sajten känner igen -- det man behöver för att kunna tilldela något."""
    rows = bridge.get("/rest/api/3/user/search?maxResults=20&query={}".format(
        urllib.parse.quote(query))) or []
    return [{"name": str(p.get("displayName") or ""), "email": str(p.get("emailAddress") or ""),
             "accountId": str(p.get("accountId") or ""), "active": bool(p.get("active"))}
            for p in rows if isinstance(p, dict)]


def cmd_admin(args) -> int:
    """Admin-ytan: roller, fält och folk. Läsning -- och det panelen får visa beror
    på vad Jira svarar om kontots behörigheter, inte på vad sidan gissar."""
    what = (getattr(args, "what", "") or "all").lower()
    bridge = client()
    project, source = link_project(args)
    data = {"project": project, "projectSource": source}
    try:
        # Utan `permissions` i frågan svarar Jira 400: den vill veta vad den skall svara om.
        ask = ",".join(sorted(set(PROJECT_PERMISSIONS) | {"ADMINISTER"}))
        perms = (bridge.get("/rest/api/3/mypermissions?permissions=" + ask) or {}).get("permissions") or {}
        data["can"] = {name: bool((row or {}).get("havePermission")) for name, row in perms.items()}
    except Exception as exc:      # noqa: BLE001 -- behörigheten är en upplysning
        data["can"] = {}
        data["permissionError"] = "{}: {}".format(type(exc).__name__, exc)
    if what in ("all", "roles"):
        data["roles"] = admin_roles(bridge, project)
    if what in ("all", "fields"):
        data["fields"] = admin_fields(bridge)
    if what == "people":
        data["people"] = admin_people(bridge, " ".join(getattr(args, "words", []) or []))
    say(args, data, [
        "{} ({}): {} roller, {} fält.".format(project, source, len(data.get("roles") or []),
                                              len(data.get("fields") or [])),
    ] + ["  {}: {}".format(row["role"], ", ".join(a["name"] for a in row["actors"]) or "tom")
         for row in (data.get("roles") or [])] if what in ("all", "roles") else [])
    return 0


def link_project(args, repo_dir: str = "") -> tuple:
    """(projekt, varifrån) -- flaggan vinner, sedan länken, sedan standarden.

    Det här är hela poängen med kopplingen: den som jobbar mot ett repo ska inte
    behöva komma ihåg projektnyckeln, och panelen ska kunna säga var den kom ifrån.
    """
    if (getattr(args, "project", "") or "").strip():
        return args.project.strip(), "flaggan"
    # Repot först när det är namngivet, annars det enda länkade repot -- det är
    # projektet man är kopplad till. Flera länkar utan namn är tvetydigt: standarden.
    candidates = [(getattr(args, "repo_name", "") or "", "länken"),
                  (repo_slug_of_dir(repo_dir) if repo_dir else "", "--repo")]
    for name, source in candidates:
        link = link_for(name) if name else {}
        if link.get("project"):
            return link["project"], "{} ({})".format(source, link.get("repo") or name)
    if not repo_dir and not (getattr(args, "repo_name", "") or "").strip():
        linked = [dict(value or {}, repo=key) for key, value in load_links().items() if (value or {}).get("project")]
        if len(linked) == 1:
            return linked[0]["project"], "länken ({})".format(linked[0].get("repo"))
    return DEFAULT_PROJECT, "standarden"


def plan_repo_dir(args) -> str:
    """Katalogen önskemålet gäller: --repo, annars den lokala kopian av länken."""
    given = (getattr(args, "repo", "") or "").strip()
    if given:
        return given
    name = (getattr(args, "repo_name", "") or "").strip()
    return local_clone(name) if name else ""


DEFAULT_TEAM = {"people": 0, "roles": "", "sprint_weeks": 2}


def team_of(config: dict, args=None) -> dict:
    """Teamet: hur många, vilka roller, och hur lång en sprint är.

    Ur användarens konfiguration (config.json), med körningens argument överst. Utan ett
    team blir raden tom och prompten säger ingenting om det -- en påhittad gissning om
    teamet vore värre än tystnad, för då planeras det för ett team ingen har.
    """
    team = dict(DEFAULT_TEAM)
    saved = (config or {}).get("team") or {}
    if isinstance(saved, dict):
        for key in ("people", "roles", "sprint_weeks"):
            if saved.get(key) not in (None, ""):
                team[key] = saved[key]
    for attr, key in (("team_people", "people"), ("team_roles", "roles"),
                      ("sprint_weeks", "sprint_weeks")):
        value = getattr(args, attr, None) if args is not None else None
        if value not in (None, "", 0):
            team[key] = value
    try:
        team["people"] = int(team["people"] or 0)
    except (TypeError, ValueError):
        team["people"] = 0
    try:
        team["sprint_weeks"] = int(team["sprint_weeks"] or 2)
    except (TypeError, ValueError):
        team["sprint_weeks"] = 2
    team["roles"] = str(team["roles"] or "").strip()
    return team


def team_text(team: dict) -> str:
    """Teamet som en rad i prompten (engelska: prompten är engelsk). Tom utan team."""
    people, roles = (team or {}).get("people") or 0, (team or {}).get("roles") or ""
    veckor = (team or {}).get("sprint_weeks") or 2
    if not people and not roles:
        return ""
    vilka = "The team is {} people".format(people) if people else "The team"
    if roles:
        vilka += " ({})".format(roles)
    return ("{}: the plan has to fit what THIS team gets through in a sprint of {} weeks -- "
            "not what a bigger or faster team would. Count the work the same way the team "
            "does.".format(vilka, veckor))


def board_text(client_, project: str, limit: int = 200) -> tuple[str, str, int]:
    """Vad som redan står på tavlan: (texten till prompten, varför den är tom, antalet).

    Utan det här hänger jämförelsen med det befintliga på att agenten själv råkar anropa
    tavlan. Med det står de befintliga korten i prompten, och varje förslag kan säga vilket
    kort det bygger på -- eller att det är nytt. Felet lämnas tillbaka i stället för att
    sväljas: en tom tavla och en trasig sökning ser annars likadana ut.
    """
    if not project:
        return "", "inget projekt att läsa tavlan för", 0
    try:
        # Samma väg som resten av motorn (find): den kodar JQL:en och använder
        # /rest/api/3/search/jql -- Atlassians gamla /rest/api/3/search svarar 410 Gone
        # sedan migreringen (mätt 2026-10-05), och en rå JQL i URL:en ger InvalidURL.
        issues = find(client_, "project={} ORDER BY key ASC".format(project), limit)
    except Exception as exc:  # noqa: BLE001 -- tavlan är sammanhang, inte ett krav
        return "", "{}: {}".format(type(exc).__name__, str(exc)[:160]), 0
    rader = []
    for issue in issues:
        fields = issue.get("fields") or {}
        rader.append("{} [{}] {}".format(
            issue.get("key") or "?", (fields.get("status") or {}).get("name") or "",
            str(fields.get("summary") or "")[:110]))
    if not rader:
        return "", "", 0
    # /search/jql ger högst en sida (100) -- står det "100" utan förbehåll ser tavlan
    # mindre ut än den är, och det är samma sorts tysta underdrift som ett klippt underlag.
    fler = " (the first page of {}, there may be more)".format(len(rader)) \
        if len(rader) >= min(limit, 100) else ""
    return ("{} issue(s) already on the board{} -- build on these where they fit, and do not "
            "propose the same work again:\n".format(len(rader), fler) + "\n".join(rader),
            "", len(rader))


def link_context(client_, args) -> str:
    """Jira-sidan av länken, som text till agenten. Tom när inget är länkat."""
    link = link_for(getattr(args, "repo_name", "") or repo_slug_of_dir(getattr(args, "repo", "") or ""))
    if not link:
        return ""
    parts = ["repo {} is linked to Jira project {}{}.".format(
        link.get("repo"), link.get("project") or "?",
        " — " + str(link["note"]).strip() if link.get("note") else "")]
    key = str(link.get("issue") or "").strip()
    if key:
        parts.append("the work in hand is {}.".format(key))
        try:
            issue = client_.get("/rest/api/3/issue/{}?fields=summary,status".format(key)) or {}
            fields = issue.get("fields") or {}
            parts.append("{}: {} ({}).".format(key, (fields.get("summary") or "").strip(),
                                               ((fields.get("status") or {}).get("name") or "")))
        except Exception as exc:  # noqa: BLE001 -- en nyckel som inte går att läsa stoppar inte planen
            parts.append("({} could not be read: {})".format(key, exc if str(exc) else type(exc).__name__))
    return "\n".join(parts) + "\n"


def build_context(docs_text: str, repo_text: str, notes, link_text: str = "",
                  team_line: str = "", board_line: str = "") -> str:
    """Kontextblocket i prompten. Tomt när inget underlag gavs.

    Teamet och tavlan hör hit: det är sammanhanget fördelningen skall passa in i, och båda
    kommer ur samma ställe som resten -- inga nya platshållare i prompten, som hade krävt
    att varje anropare kom ihåg dem.
    """
    if not (docs_text or repo_text or link_text or team_line or board_line):
        return ""
    parts = ["Context for the project as it stands — read it before you split the wish:",
             "build on what is already there, and let each description say what it rests on."]
    if team_line:
        parts += ["", team_line]
    if link_text:
        parts += ["", "The Jira side the repo is tied to:", "", link_text]
    if board_line:
        parts += ["", board_line]
    if docs_text:
        parts += ["", "Papers handed in with the wish:", "", docs_text]
    if repo_text:
        parts += ["", "Where the code is today:", "", repo_text]
    if any(note.get("truncated") for note in notes or []):
        parts += ["", "Text above was cut to fit; the whole documents are on disk."]
    return "\n".join(parts) + "\n"


# -------------------------------------------------------------- pure logic

SHARED_PREFIX = "GEMENSAMT"


def pick_jql(project: str, scope: str = "mine") -> str:
    """Not-started work, most critical first.

    ``statusCategory = "To Do"`` rather than a status name: the workflow's names
    are the site's business, the category is not. ``scope="mine"`` keeps the
    work that is nobody's or already mine; ``scope="any"`` drops that clause, and
    is the pool you may take from with a second press (see ``--expect``).
    """
    claim = "AND (assignee IS EMPTY OR assignee = currentUser()) " if scope == "mine" else ""
    return (
        'project = {} AND statusCategory = "To Do" {}'
        "ORDER BY priority DESC, created ASC".format(project, claim)
    )


def is_shared(issue) -> bool:
    """A shared task: the summary is marked GEMENSAMT (how the group marks them)."""
    summary = ((issue.get("fields") or {}).get("summary") or "").strip()
    return summary.upper().startswith(SHARED_PREFIX)


def shared_first(issues):
    """Shared tasks before personal ones, each group keeping the query's order.

    ``sorted`` is stable, so the priority-then-oldest order Jira returned stands
    within a group -- no second comparison to get wrong.
    """
    return sorted(issues or [], key=lambda issue: 0 if is_shared(issue) else 1)


def current_jql(project: str) -> str:
    return (
        'project = {} AND assignee = currentUser() AND statusCategory = "In Progress" '
        "ORDER BY updated DESC".format(project)
    )


def key_in(text: str) -> str:
    """The first Jira key in a string, e.g. a branch name or a commit subject."""
    found = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", text or "")
    return found.group(1) if found else ""


def prefix_subject(message: str, key: str) -> str:
    """A commit subject that names its issue, or the message unchanged.

    Jira's GitHub integration links on the key being present in the message, so
    naming it in the subject is enough -- no trailer, no marker syntax.
    """
    if not key or not message.strip():
        return message
    if key in message:
        return message
    lines = message.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    lines[0] = "{}: {}".format(key, lines[0].strip())
    body = "\n".join(lines)
    return body + ("\n" if message.endswith("\n") and not body.endswith("\n") else "")


def row(issue) -> dict:
    """The fields that matter, for a human line or for an agent's JSON."""
    fields = issue.get("fields") or {}
    return {
        "key": issue.get("key"),
        "summary": fields.get("summary") or "",
        "priority": (fields.get("priority") or {}).get("name") or "",
        "status": (fields.get("status") or {}).get("name") or "",
        "assignee": (fields.get("assignee") or {}).get("displayName") or "",
    }


def describe(issue) -> str:
    it = row(issue)
    return "{}  {}  [{}]  {}".format(it["key"], it["priority"] or "-", it["status"] or "-",
                                    it["summary"][:70])


# ---------------------------------------------------------------- clients

class Http:
    """Plain Jira Cloud REST v3 over stdlib urllib. This is what a clone uses."""

    def __init__(self, site: str, email: str, token: str):
        self.base = site.rstrip("/")
        if not self.base.startswith("http"):
            self.base = "https://" + self.base
        self.auth = b64encode("{}:{}".format(email, token).encode()).decode()

    def request(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", "Basic " + self.auth)
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise SystemExit("jira_flow: {} {} -> {} {}\n{}".format(
                method, path.split("?")[0], exc.code, exc.reason, exc.read().decode()[:400]))

    def get(self, path: str):
        return self.request("GET", path)

    def myself(self) -> dict:
        me = self.get("/rest/api/3/myself") or {}
        return {"accountId": me.get("accountId"), "displayName": me.get("displayName")}

    def assign(self, key: str, account_id: str) -> None:
        self.request("PUT", "/rest/api/3/issue/{}".format(key),
                     {"fields": {"assignee": {"accountId": account_id}}})

    def move(self, key: str, status: str) -> None:
        found = self.get("/rest/api/3/issue/{}/transitions".format(key)) or {}
        transitions = found.get("transitions", [])
        for tr in transitions:
            if (tr.get("to") or {}).get("name") == status or str(tr.get("id")) == str(status):
                self.request("POST", "/rest/api/3/issue/{}/transitions".format(key),
                             {"transition": {"id": tr["id"]}})
                return
        offered = ", ".join((t.get("to") or {}).get("name", "?") for t in transitions)
        raise SystemExit("jira_flow: {} cannot move to {!r} (offered: {})".format(
            key, status, offered or "none"))

    def board(self, project: str) -> dict:
        found = self.get("/rest/agile/1.0/board?projectKeyOrId={}".format(project)) or {}
        boards = found.get("values") or []
        return boards[0] if boards else {}

    def types(self, project: str):
        try:
            found = self.get("/rest/api/3/issue/createmeta/{}/issuetypes".format(project)) or {}
        except SystemExit:
            return []
        return [str(t.get("name", "")) for t in (found.get("issueTypes") or found.get("values") or [])]

    def create(self, board: dict, item: dict) -> dict:
        fields = {"project": {"key": (board or {}).get("projectKey") or DEFAULT_PROJECT},
                  "summary": item["summary"], "issuetype": {"name": item.get("type") or "Task"}}
        if item.get("parentKey"):
            fields["parent"] = {"key": item["parentKey"]}
        if item.get("priority"):
            fields["priority"] = {"name": item["priority"]}
        if item.get("description"):
            fields["description"] = {"type": "doc", "version": 1, "content": [
                {"type": "paragraph",
                 "content": [{"type": "text", "text": item["description"]}]}]}
        return self.request("POST", "/rest/api/3/issue", {"fields": fields})

    def comment(self, key: str, text: str) -> None:
        """En kommentar. Kroppen är ADF, en stycke-nod per rad -- API v3 avvisar en vanlig
        sträng, och en enda nod med radbrytningar inuti är inte samma sak som rader."""
        content = [{"type": "paragraph", "content": [{"type": "text", "text": line}]}
                   for line in (str(text).splitlines() or [""])]
        self.request("POST", "/rest/api/3/issue/{}/comment".format(key),
                     {"body": {"type": "doc", "version": 1, "content": content}})

    def sprint_create(self, board_id: str, name: str, start: str, end: str) -> dict:
        return self.request("POST", "/rest/agile/1.0/sprint",
                            {"name": name, "originBoardId": board_id,
                             "startDate": start, "endDate": end})

    def sprint_add(self, sprint_id: str, keys: list) -> None:
        self.request("POST", "/rest/agile/1.0/sprint/{}/issue".format(sprint_id),
                     {"issues": list(keys)})

    def log(self, action: str, key: str, detail: str = "", ok: bool = True) -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.parent.chmod(0o700)
        with LOG_FILE.open("a") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "action": action, "key": key, "detail": detail, "ok": ok},
                                ensure_ascii=False) + "\n")
        LOG_FILE.chmod(0o600)


class Bridge:
    """The Omarchy Jira bridge: the same token in the keyring, plus its action log."""

    def __init__(self, module, cfg):
        self.jb, self.cfg = module, cfg

    def get(self, path: str):
        return self.jb.jira_get(self.cfg, path)

    def myself(self) -> dict:
        me = self.jb.jira_get(self.cfg, "/rest/api/3/myself") or {}
        return {"accountId": me.get("accountId"), "displayName": me.get("displayName")}

    def assign(self, key: str, account_id: str) -> None:
        self.jb.real_update(self.cfg, key, {"assigneeAccountId": account_id})

    def move(self, key: str, status: str) -> None:
        self.jb.real_move(self.cfg, key, status)

    def comment(self, key: str, text: str) -> None:
        """Bryggans egen kommentar: den bygger ADF och läser tillbaka ärendet efteråt."""
        self.jb.real_add_comment(self.cfg, key, text)

    def sprint_create(self, board_id: str, name: str, start: str, end: str) -> dict:
        """Bryggan skapar sprinten OCH läser tillbaka den: Jira svarar inte alltid med
        raden, och ett svar utan id ser ut som en sprint som finns."""
        return self.jb.real_sprint_create(self.cfg, board_id, name, start=start, end=end)

    def sprint_add(self, sprint_id: str, keys: list) -> None:
        """Bryggan lägger ärendena i sprinten och kontrollerar att de verkligen hamnade
        där innan den svarar -- den flyttar ingenting tyst."""
        self.jb.real_sprint_add(self.cfg, sprint_id, list(keys))

    def log(self, action: str, key: str, detail: str = "", ok: bool = True) -> None:
        self.jb.log_action(self.cfg, action, key, detail=detail, ok=ok)

    def board(self, project: str) -> dict:
        """Tavlan på projektnyckel, samma väg som panelen och MCP:n går."""
        snap = self.jb.real_snapshot(self.cfg) or {}
        for board in snap.get("boards") or []:
            if str(board.get("projectKey", "")).upper() == str(project).upper():
                return board
        boards = snap.get("boards") or []
        return boards[0] if boards else {}

    def types(self, project: str):
        return [str(r.get("name", "")) for r in
                (self.jb.real_issue_types(self.cfg, project) or []) if r.get("name")]

    def create(self, board: dict, item: dict) -> dict:
        # Bryggan lämnar tillbaka nyckeln som en sträng, HTTP ett objekt: en form ut
        # till anroparen, så ingen behöver veta vilken väg som användes.
        answer = self.jb.real_create(self.cfg, board, dict(
            item, typeName=item.get("type", "Task"), priorityName=item.get("priority") or "",
            parentKey=item.get("parentKey") or ""))
        if isinstance(answer, dict):
            return answer
        return {"key": str(answer or "")}


def client():
    """The bridge when it is installed, otherwise a plain HTTP client."""
    if BRIDGE_DIR.is_dir() and str(BRIDGE_DIR) not in sys.path:
        sys.path.insert(0, str(BRIDGE_DIR))
    try:
        import jira_bridge  # noqa: PLC0415 -- the local import is the point

        return Bridge(jira_bridge, jira_bridge.load_config())
    except ImportError:
        pass

    import jira_secrets  # bara den här vägen behöver den; bryggan sköter token annars

    config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    site = os.environ.get("JIRA_SITE") or config.get("site") or ""
    email = os.environ.get("JIRA_EMAIL") or config.get("email") or ""
    try:
        stored = jira_secrets.store_for().read()
    except RuntimeError as exc:
        raise SystemExit("jira_flow: {}".format(exc))
    token = os.environ.get("JIRA_TOKEN") or stored or config.get("token") or ""
    missing = [name for name, value in
               (("JIRA_SITE", site), ("JIRA_EMAIL", email), ("JIRA_TOKEN", token)) if not value]
    if missing:
        raise SystemExit(
            "jira_flow: no credentials. Install the Omarchy Jira bridge, or set {} "
            "(environment, or {}).\n"
            "Create a token at https://id.atlassian.com/manage-profile/security/api-tokens".format(
                ", ".join(missing), CONFIG_FILE))
    return Http(site, email, token)


# --------------------------------------------------------------- commands

def find(client_, jql: str, limit: int = 4):
    path = "/rest/api/3/search/jql?jql={}&maxResults={}&fields=summary,status,assignee,priority".format(
        urllib.parse.quote(jql), limit)
    return (client_.get(path) or {}).get("issues", [])


def fetch_one(client_, key: str):
    """One not-started item by key, or None if it moved on. The second press takes
    exactly what was named, never whatever happens to be on top now."""
    issue = client_.get(
        "/rest/api/3/issue/{}?fields=summary,status,assignee,priority".format(key)) or {}
    category = ((issue.get("fields") or {}).get("status") or {}).get("statusCategory") or {}
    if category.get("key") != "new":
        return None
    return issue


def say(args, payload: dict, lines) -> None:
    """One payload, two faces: an agent reads the JSON, a human reads the lines."""
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for line in lines:
            print(line)


def cmd_next(client_, args) -> int:
    me = client_.myself()
    if not me.get("accountId"):
        say(args, {"ok": False, "error": "could not read your account from the site"},
            ["jira_flow: could not read your account from the site."])
        return 2

    if args.expect:
        # Andra trycket: exakt det ärende som knappen namngav. Är det redan påbörjat
        # eller borta är svaret nej, inte "här är en annan".
        chosen = fetch_one(client_, args.expect)
        if chosen is None:
            message = "{} is no longer not-started; nothing was taken.".format(args.expect)
            say(args, {"ok": False, "error": message, "expect": args.expect}, [message])
            return 2
        rest = shared_first(find(client_, pick_jql(args.project)))
        pool = [chosen] + [i for i in rest if i["key"] != chosen["key"]]
        chosen, rest = pool[0], pool[1:]
    else:
        pool = shared_first(find(client_, pick_jql(args.project), limit=10))
        if pool:
            chosen, rest = pool[0], pool[1:]
        else:
            # Inget att ta: föreslå den mest kritiska uppgiften som är någon annans,
            # och gör ingenting förrän ett andra tryck bekräftar just den nyckeln.
            others = shared_first(find(client_, pick_jql(args.project, "any"), limit=10))
            if not others:
                message = "Nothing to take: nothing not-started in {}.".format(args.project)
                say(args, {"ok": False, "error": message, "project": args.project,
                           "projectSource": getattr(args, "project_source", "")}, [message])
                return 1
            top = others[0]
            message = "Nothing of your own; {} is the most critical. Press again to take it over.".format(
                top["key"])
            say(args, {"ok": False, "error": message, "proposal": row(top),
                       "requiresConfirmation": True},
                [message,
                 "  {}".format(describe(top))])
            return 3
    key = chosen["key"]
    skipped = [row(issue) for issue in rest]

    if args.dry_run:
        say(args,
            {"ok": True, "dryRun": True, "wouldTake": row(chosen), "status": args.status,
             "project": args.project, "projectSource": getattr(args, "project_source", ""),
             "assignTo": me.get("displayName"), "skipped": skipped},
            ["would take: {}".format(describe(chosen)),
             "            assign to {} and move to {}".format(me.get("displayName"), args.status)])
    else:
        client_.assign(key, me["accountId"])
        try:
            client_.move(key, args.status)
        except SystemExit as exc:
            client_.log("flow-next", key, "assigned only: {}".format(exc), ok=False)
            say(args, {"ok": False, "error": str(exc), "took": row(chosen), "moved": False},
                ["jira_flow: assigned {} but could not move it: {}".format(key, exc)])
            return 2

        # Read it back: a write is done when the site says it is, never when the
        # call returned. A mismatch is an error, not a success.
        after = client_.get(
            "/rest/api/3/issue/{}?fields=summary,status,assignee,priority".format(key)) or {}
        fields = after.get("fields") or {}
        ok = ((fields.get("assignee") or {}).get("accountId") == me["accountId"]
              and (fields.get("status") or {}).get("name") == args.status)
        client_.log("flow-next", key, "assign + {}".format(args.status), ok=ok)
        if not ok:
            say(args, {"ok": False, "error": "the site did not keep the write", "took": row(after)},
                ["jira_flow: wrote {}, the site reads back:".format(key),
                 "          " + describe(after)])
            return 2
        say(args, {"ok": True, "took": row(after), "skipped": skipped},
            ["took: {}".format(describe(after))])

    if not args.json:
        for issue in rest:
            print("skipped: {}".format(describe(issue)))
    return 0


# --------------------------------------------------- körningen: work <KEY>
# Flödet TAR ett ärende (`next`); körningen GÖR det. Ärendet in, en gren och en PR ut,
# med kvittot tillbaka på ärendet. Agenten jobbar i en egen git-worktree, så den egna
# trädkatalogen aldrig rörs -- och en körning som går fel lämnar ingenting efter sig där.
#
# Skrivytan är med flit liten i den första versionen: körningen lägger grenen på fjärren,
# öppnar PR:en och kommenterar ärendet. Den flyttar INTE statusen -- det är `next`s jobb,
# och två kommandon som flyttar samma ärende är två sanningar om samma sak.

RUNS_FILE = Path.home() / ".config/jira-flow/runs.jsonl"
RUNS_DIR = Path.home() / ".config/jira-flow/runs"


def work_branch(key: str, given: str = "") -> str:
    """Grenen körningen jobbar på: godjira/<KEY>, eller det man själv namngav.

    Namnet går rakt in i git, så det prövas HÄR i stället för att git säger nej mitt i en
    körning som redan kostat en agent. Reglerna är gits egna (refs/heads): inga mellanslag,
    `..`, `~`, `^`, `:`, `?`, `*`, `[`, `\\`, ingen inledande `-`, inget `@{`.
    """
    name = (given or "").strip() or "godjira/{}".format(str(key).strip().upper())
    bad = " ~^:?*[\\\x7f"
    if (not name or name.startswith("-") or name.endswith("/") or ".." in name
            or "@{" in name or any(c in bad or ord(c) < 32 for c in name)):
        raise ValueError("git would not accept the branch name {!r}".format(name))
    return name


def work_dir(key: str) -> Path:
    """Körningens egen katalog. Ärendenyckeln räcker: den är unik och går att läsa."""
    return RUNS_DIR / str(key).strip().upper()


def parse_commits(text: str) -> list:
    """`git log --oneline` -> en rad per commit. Ingen commit är ett svar, inte ett fel."""
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, subject = line.partition(" ")
        rows.append({"sha": sha, "subject": subject.strip()})
    return rows


def parse_shortstat(text: str) -> dict:
    """Filen och raderna ur gits shortstat. Talen LÄSES ur git, de räknas inte fram."""
    def number(pattern):
        found = re.search(pattern, text or "")
        return int(found.group(1)) if found else 0
    return {"files": number(r"(\d+) files? changed"),
            "added": number(r"(\d+) insertions?\(\+\)"),
            "removed": number(r"(\d+) deletions?\(-\)")}


def adf_plain(node) -> str:
    """Beskrivningen som text. Bryggan kan läsa ADF; går den inte att importera läses
    textnoderna rakt av -- en körning skall inte falla på en formatering."""
    if isinstance(node, str):
        return node.strip()
    if not isinstance(node, dict):
        return ""
    try:
        if str(BRIDGE_DIR) not in sys.path:
            sys.path.insert(0, str(BRIDGE_DIR))
        import jira_bridge  # noqa: PLC0415 -- samma lokala import som client() gör
        return jira_bridge.adf_to_text(node).strip()
    except Exception:  # noqa: BLE001 -- formen är någon annans, en krasch hjälper ingen
        return " ".join(re.findall(r'"text":\s*"([^"]+)"', json.dumps(node)))


def work_issue(client_, key: str):
    """Ärendet körningen gäller: beskrivningen, läget, kommentarerna -- i ett anrop.

    `fetch_one` duger inte här: den svarar bara för ostartat arbete och bär ingen
    beskrivning. En körning mot ett ärende man inte ser är en körning mot ingenting.
    """
    issue = client_.get("/rest/api/3/issue/{}?fields=summary,description,status,assignee,"
                        "priority,comment".format(key)) or {}
    if not issue.get("key"):
        return None
    fields = issue.get("fields") or {}
    said = []
    for row_ in ((fields.get("comment") or {}).get("comments") or [])[-5:]:
        body = row_.get("body")
        said.append({"author": (row_.get("author") or {}).get("displayName") or "",
                     "body": body if isinstance(body, str) else adf_plain(body)})
    return {"key": issue["key"], "summary": fields.get("summary") or "",
            "description": adf_plain(fields.get("description")) if fields.get("description") else "",
            "status": (fields.get("status") or {}).get("name") or "",
            "assignee": (fields.get("assignee") or {}).get("displayName") or "",
            "comments": said}


def scan_files_for(key: str, project: str) -> list:
    """Filerna scanningen pekar på för ett ärende -- agentens första ledtråd.

    Saknas scanningen är svaret tomt: en påhittad fil vore värre än ingen ledtråd.
    """
    wherever = SCAN_FILE / "scan-{}.json".format(project)
    if not wherever.exists():
        return []
    try:
        data = json.loads(wherever.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    for entry in data.get("map") or []:
        if str(entry.get("key", "")).strip().upper() == str(key).strip().upper():
            return [str(f) for f in (entry.get("files") or [])][:12]
    return []


def work_prompt(key: str, issue: dict, files: list, project: str, branch: str,
                where: str) -> str:
    """Vad agenten får: ärendet, ledtrådarna, och gränsen för uppdraget."""
    lines = [
        "You are doing one Jira issue, end to end, in a git worktree that is only yours.",
        "",
        "Issue {} in project {}: {}".format(key, project, issue.get("summary") or ""),
        "Status: {}   Assignee: {}".format(issue.get("status") or "?", issue.get("assignee") or "(nobody)"),
    ]
    if issue.get("description"):
        lines += ["", "What the issue says:", issue["description"].strip()[:4000]]
    if issue.get("comments"):
        lines += ["", "What has been said about it:"]
        lines += ["- {}: {}".format(c.get("author") or "?", " ".join(str(c.get("body") or "").split())[:400])
                  for c in issue["comments"]]
    if files:
        lines += ["", "Where in the code the issue seems to live -- GodJIRA's scan of the repo,"
                      " a hint and not a rule:"]
        lines += ["- " + f for f in files]
    lines += [
        "",
        "The repository is checked out at {} on branch {}. That is your working directory.".format(where, branch),
        "The GodJIRA MCP connection is locked to project {}: read and write that project, nothing else.".format(project),
        "",
        "Do the work:",
        "- read the code before you change it, and follow the style that is already there",
        "- run the repo's own tests or build if it has one, and say what you ran and what it answered",
        "- commit with `git add <the files you touched>` -- never `git add -A` -- and a message that names {}".format(key),
        "- do NOT push and do NOT open a pull request: GodJIRA does that from your commits",
        "- if the issue cannot be done -- it is unclear, needs a decision, or would break something"
        " else -- make no commits at all and say plainly what you need",
        "",
        "Answer with: what you changed and why, which files, what you ran, and what is left.",
    ]
    return "\n".join(lines)


def work_receipt(key: str, branch: str, commits: list, stat: dict, pr: str = "",
                 agent: str = "", note: str = "") -> str:
    """Kvittot som går till ärendet. Grenen och commitarna ÄR svaret på vad som gjordes;
    en sammanfattning i prosa hade varit en andra version av samma sak."""
    lines = ["GodJIRA worked {}".format(key), "", "Branch: {}".format(branch)]
    if pr:
        lines.append("Pull request: {}".format(pr))
    if agent:
        lines.append("Agent: {}".format(agent))
    if commits:
        lines.append("Commits ({}):".format(len(commits)))
        lines += ["- {} {}".format(c["sha"], c["subject"]) for c in commits[:10]]
    else:
        lines.append("No commits: the agent changed nothing.")
    if stat.get("files"):
        lines.append("Diff: {} files changed, +{} -{}".format(stat["files"], stat["added"], stat["removed"]))
    if note:
        lines += ["", note]
    return "\n".join(lines)


def runs_recent(limit: int = 20) -> list:
    """Körningarna, nyast först. Filen är JSONL och läses i sin helhet -- en körning är
    några rader, och en databas för det vore mer kod än den ersätter."""
    if not RUNS_FILE.exists():
        return []
    rows = []
    for line in RUNS_FILE.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:][::-1]


def run_remember(entry: dict) -> None:
    """En körning till i liggaren. Ett fel här får inte fälla körningen -- arbetet är
    redan gjort när raden skrivs."""
    entry = dict(entry)
    entry.setdefault("at", datetime.now().isoformat(timespec="seconds"))
    try:
        RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUNS_FILE.parent.chmod(0o700)
        with RUNS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        RUNS_FILE.chmod(0o600)
    except OSError:
        pass


def git_do(root, *argv, timeout: int = 120) -> tuple:
    """git som svarar med både koden och orden. En worktree som inte blev till skall säga
    varför i stället för att se ut som att den finns."""
    try:
        done = subprocess.run(["git", "-C", str(root)] + [str(a) for a in argv],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "{}: {}".format(type(exc).__name__, exc)
    return done.returncode, (done.stdout + done.stderr).strip()


def agent_name() -> str:
    """Vem som svarade, som ett namn -- samma läsning som chatten gör."""
    try:
        return Path(agent_argv("")[0][0]).name
    except RuntimeError:
        return ""


def cmd_work(client_, args) -> int:
    """Ett ärende: egen worktree, agenten i den, gren + PR + kvitto tillbaka."""
    key = (getattr(args, "key", "") or "").strip().upper()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", key):
        message = "{!r} does not look like an issue key".format(getattr(args, "key", "") or "")
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2
    root = chosen_repo_dir(args)
    if not root or not Path(root).is_dir():
        message = "no local copy of the repo to work in -- link the repo, or pass --repo DIR"
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2
    if not Path(root, ".git").exists():
        message = "{} is not a git repository, so there is no branch to work on".format(root)
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2
    project, source = link_project(args, root)
    if not project:
        message = "no Jira project for {} -- link the repo first: jira_flow link set <repo> <PROJECT>".format(root)
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2
    try:
        branch = work_branch(key, getattr(args, "branch", "") or "")
    except ValueError as exc:
        say(args, {"ok": False, "error": str(exc)}, ["jira_flow: " + str(exc)])
        return 2
    issue = work_issue(client_, key)
    if issue is None:
        message = "{} is not an issue this account can see".format(key)
        say(args, {"ok": False, "error": message, "project": project}, ["jira_flow: " + message])
        return 2
    where = work_dir(key)
    prompt = work_prompt(key, issue, scan_files_for(key, project), project, branch, str(where))
    base = git_out(root, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
    plan = {"ok": True, "dryRun": bool(args.dry_run), "key": key, "project": project,
            "projectSource": source, "repo": root, "branch": branch, "base": base,
            "worktree": str(where), "prompt": prompt,
            "filesFromScan": scan_files_for(key, project)}

    if args.dry_run:
        # Läsvägen: visa uppdraget, skriv ingenting -- varken worktree, gren eller ärende.
        say(args, plan, [prompt])
        return 0

    # 1. Arbetskopian. Finns grenen redan (en körning som kört förut) knyts katalogen till
    #    den i stället för att fälla -- att återuppta är vad man vill den andra gången.
    if str(where) not in git_out(root, "worktree", "list", "--porcelain"):
        exists = bool(git_out(root, "rev-parse", "--verify", "--quiet", branch))
        argv = ["worktree", "add"] + ([] if exists else ["-b", branch]) + [str(where), branch if exists else base]
        code, out = git_do(root, *argv)
        if code != 0:
            message = "could not make the worktree: {}".format(out.splitlines()[-1] if out else code)
            run_remember({"key": key, "project": project, "repo": root, "branch": branch,
                          "ok": False, "note": message})
            say(args, dict(plan, ok=False, error=message, dryRun=False), ["jira_flow: " + message])
            return 2

    # 2. Agenten, i arbetskopian. Kedjan är användarens egen (samma som chatten använder).
    if (getattr(args, "agent", "") or "").strip():
        global AGENT
        AGENT = args.agent.strip()
    tried = agent_name()
    try:
        answer = ask_agent(prompt, {"GODJIRA_MCP_PROJECT": project}, cwd=str(where))
    except RuntimeError as exc:
        run_remember({"key": key, "project": project, "repo": root, "branch": branch,
                      "worktree": str(where), "agent": tried, "ok": False, "note": str(exc)})
        say(args, dict(plan, ok=False, error=str(exc), dryRun=False),
            ["jira_flow: the agent could not run: {}".format(exc)])
        return 2

    # 3. Vad som blev gjort: commitarna och diffen, lästa ur grenen -- inte ur agentens ord.
    commits = parse_commits(git_out(where, "log", "--oneline", "{}..HEAD".format(base), timeout=60))
    stat = parse_shortstat(git_out(where, "diff", "--shortstat", "{}..HEAD".format(base), timeout=60))
    dirty = [line for line in git_out(where, "status", "--porcelain", timeout=60).splitlines() if line.strip()]
    note = "{} files were left uncommitted in the worktree.".format(len(dirty)) if dirty else ""

    # 4. Ut: grenen, PR:en, kvittot. Utan commit finns ingenting att skicka -- och då
    #    skrivs ingenting på fjärren heller. Ett nej är ett svar.
    pr, pushed = "", False
    if commits and not args.no_push:
        code, out = git_do(where, "push", "-u", "origin", branch, timeout=300)
        pushed = code == 0
        if not pushed:
            note = (note + " " if note else "") + "the push failed: {}".format(out.splitlines()[-1] if out else code)
        elif shutil.which("gh"):
            body = work_receipt(key, branch, commits, stat, "", tried, note)
            code, out = git_do(where, "diff", "--stat", "{}..HEAD".format(base), timeout=60)
            try:
                made = subprocess.run(
                    ["gh", "pr", "create", "--title", "{} {}".format(key, issue["summary"])[:120],
                     "--body", body + "\n\n```\n" + out[:2000] + "\n```", "--head", branch,
                     "--base", base if base != "HEAD" else "", "--fill"],
                    cwd=str(where), capture_output=True, text=True, timeout=120)
                url = [line.strip() for line in (made.stdout or "").splitlines() if line.strip().startswith("http")]
                if made.returncode == 0 and url:
                    pr = url[-1]
                else:
                    note = (note + " " if note else "") + "the pull request was not opened: {}".format(
                        ((made.stderr or "").strip().splitlines() or ["?"])[-1][:200])
            except (OSError, subprocess.TimeoutExpired) as exc:
                note = (note + " " if note else "") + "the pull request was not opened: {}".format(exc)

    receipt = work_receipt(key, branch, commits, stat, pr, tried, note)
    client_.log("flow-work", key, "{}: {} commits{}".format(branch, len(commits), ", PR" if pr else ""),
                ok=bool(commits))
    if commits and not args.no_comment:
        try:
            client_.comment(key, receipt)
        except (RuntimeError, SystemExit) as exc:
            note = (note + " " if note else "") + "the issue was not commented: {}".format(exc)
    run_remember({"key": key, "project": project, "repo": repo_slug_of_dir(root) or root,
                  "branch": branch, "worktree": str(where), "agent": tried, "commits": len(commits),
                  "files": stat["files"], "added": stat["added"], "removed": stat["removed"],
                  "pushed": pushed, "pr": pr, "answer": " ".join((answer or "").split())[:600],
                  "ok": bool(commits), "note": note})

    if args.rm and commits and (pushed or args.no_push):
        git_do(root, "worktree", "remove", "--force", str(where), timeout=120)

    payload = dict(plan, dryRun=False, ok=bool(commits), commits=commits, diff=stat,
                   pushed=pushed, pr=pr, agent=tried, note=note, answer=answer,
                   worktreeRemoved=bool(args.rm and commits))
    lines = ["{}: {} commit(s) on {}".format(key, len(commits), branch)] if commits else \
            ["{}: the agent committed nothing -- nothing was pushed.".format(key)]
    if stat["files"]:
        lines.append("  {} files changed, +{} -{}".format(stat["files"], stat["added"], stat["removed"]))
    lines += ["  " + c["sha"] + " " + c["subject"] for c in commits[:8]]
    if pr:
        lines.append("  PR: " + pr)
    if note:
        lines.append("  note: " + note)
    lines.append("  worktree: " + str(where))
    say(args, payload, lines)
    return 0 if commits else 1


def cmd_runs(args) -> int:
    """Liggaren: vad körningarna gjorde. Panelen läser samma fil genom den här."""
    rows = runs_recent(getattr(args, "limit", 20))
    if args.json:
        print(json.dumps({"ok": True, "runs": rows}, ensure_ascii=False))
        return 0
    if not rows:
        print("inga körningar än ({})".format(RUNS_FILE))
        return 0
    for entry in rows:
        print("{}  {}  {}  {} commit(s){}".format(
            entry.get("at"), entry.get("key"), entry.get("branch"), entry.get("commits") or 0,
            "  " + str(entry.get("pr")) if entry.get("pr") else ""))
    return 0


def is_epic(item: dict) -> bool:
    """En epic är en epic på sin typ, inte på sin plats i listan."""
    return (item.get("type") or "").strip().lower() == "epic"


def sprint_of(item: dict) -> int:
    """Sprintnumret ur agentens rad. Skräp blir 1: en rad utan planerad sprint hör till
    den första, och ett påhittat nummer skall inte fälla ett förslag som är bra annars.
    Siffran letas upp i texten, för en agent skriver lika gärna "sprint 2" som 2."""
    found = re.search(r"\d+", str(item.get("sprint") or ""))
    return max(1, int(found.group())) if found else 1


def parse_plan(text: str, warnings: list = None):
    """Ärendena ur agentens svar. Hel array eller inget: en halv lista blir aldrig
    några ärenden, och skräp ger fel i stället för halvskrivna tavlor."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*|```$", "", body).strip()
    # raw_decode från första "[" och framåt: den stannar vid slutet av första
    # giltiga värdet. En regex från första till sista klammerparentesen klistrade
    # ihop två arrayer när modellen skrev ett exempel och sedan svaret (mätt).
    # strict=False: modeller skickar ofta ett literalt radbryt inuti en sträng,
    # vilket inte är giltig JSON men är precis vad de menade.
    decoder = json.JSONDecoder(strict=False)
    # Svaret kommer inuti en hel utskrift: agent-CLI:n skriver sin egen fråga, sina
    # verktygsrader och en avslutande session-sammanfattning runt svaret (mätt
    # 2026-09-29 med `hermes chat`). Att ta första "[" råkade då plocka en parentes
    # ur utskriften. Nu samlas varje lista av ärendeobjekt in -- och den sista tas,
    # för agentens svar är det sista den skriver.
    candidates, index = [], body.find("[")
    while index >= 0:
        try:
            value, _ = decoder.raw_decode(body[index:])
        except ValueError:
            value = None
        if (isinstance(value, list) and value
                and all(isinstance(x, dict) for x in value)
                and any("summary" in x for x in value)):
            candidates.append(value)
        index = body.find("[", index + 1)
    if not candidates:
        # Här hamnar också en lista där någon post inte är ett objekt: en halv lista
        # blir inga ärenden, och en sträng som "result" ska aldrig bli ett ärende.
        raise ValueError("the agent answered without a JSON array of issue objects "
                         "(summary, type, description, priority)")
    items = candidates[-1]
    out = []
    for item in items:
        summary = str((item or {}).get("summary") or "").strip()
        if not summary:
            raise ValueError("an issue came back without a summary")
        if "<" in summary or ">" in summary:
            # Modellen ekade schemat i stället för att svara (mätt 2026-09-28, Hermes).
            raise ValueError("the agent echoed the field description instead of answering: "
                             "{}".format(summary[:80]))
        out.append({"summary": summary[:250],
                    "type": str(item.get("type") or "Task").strip() or "Task",
                    "description": str(item.get("description") or "").strip(),
                    "priority": str(item.get("priority") or "").strip(),
                    # Vilken epic uppgiften hör till, som epikens sammanfattning.
                    "epic": str(item.get("epic") or "").strip()[:250],
                    "sprint": sprint_of(item)})
    if not out:
        raise ValueError("the agent proposed no issues at all")
    if len(out) > PLAN_MAX:
        raise ValueError("the agent proposed {} issues; the cap is {} "
                         "(JIRA_FLOW_PLAN_MAX)".format(len(out), PLAN_MAX))
    epics = [item["summary"] for item in out if is_epic(item)]

    def epic_of(namn: str) -> str:
        """Epikens riktiga sammanfattning, ur det agenten skrev.

        Agenten skriver ofta en förkortning: "Arbetsordertyper" för "Arbetsordertyper, utkast
        och livscykel i logik och databas". Ett helt svar kastades förr på det (mätt
        2026-10-05). Exakt träff först, sedan förled -- men bara om förledet pekar på EN
        epic: en gissning mitt emellan två är värre än ett fel, för då hamnar uppgiften under
        fel rot utan att någon ser det.
        """
        namn = str(namn or "").strip()
        if not namn:
            return ""
        if namn in epics:
            return namn
        små = namn.lower()
        träffar = [e for e in epics
                   if e.lower().startswith(små) or små.startswith(e.lower())]
        return träffar[0] if len(träffar) == 1 else ""
    if len(epics) > PLAN_EPICS:
        raise ValueError("the agent proposed {} epics; the cap is {} "
                         "(JIRA_FLOW_PLAN_EPICS)".format(len(epics), PLAN_EPICS))
    # En uppgift som pekar på en epic som inte finns i listan blir inget ärende under den.
    # Ett tyst träd utan rot är värre än ett fel -- men svaret kastas inte: agenten pekade
    # på en rot den menade, och ett helt svar försvann förr på det (mätt 2026-10-05: 1 epic
    # listad, 4 refererade, 19 ärenden kastade -- och användaren hade bett om flera epics).
    # Den saknade epiken läggs till i stället, och det sägs högt i en varning: förslaget
    # granskas av en människa innan något skrivs, och då är en synlig epic bättre än inget.
    saknade = []
    for item in out:
        if is_epic(item):
            item["epic"] = ""
        elif item["epic"]:
            sådan = epic_of(item["epic"])
            if sådan:
                item["epic"] = sådan      # agentens förkortning -> epikens eget namn
            elif item["epic"] not in saknade:
                saknade.append(item["epic"])
    if saknade:
        # Epikerna först: ett barn som skrivs före sin epic blir ett träd utan rot, och
        # ordningen i listan får inte styra (samma regel som skrivningen).
        out = [{"summary": namn, "type": "Epic", "description": "", "priority": "",
                "epic": "", "sprint": out[0]["sprint"] if out else 1} for namn in saknade] + out
        if warnings is not None:
            warnings.append("{} epic(s) the agent referred to were not in its list and were "
                            "added: {}".format(len(saknade), ", ".join(s[:40] for s in saknade)))
    return out


def plan_words(lang: str) -> dict:
    """Ramen runt pappret. Svensk ram för svenska, engelsk för allt annat -- uppgifterna
    står som de skrevs, i kundens eget språk, och översätts aldrig här."""
    code = (lang or "sv").strip().lower()[:2]
    return PLAN_WORDS["sv"] if code == "sv" else PLAN_WORDS["en"]


def plan_sprints(items: list) -> list:
    """Fördelningen: sprintarna i tur och ordning, med uppgifterna som hör till dem.

    Ren funktion, så panelen och pappret räknar samma tal ur samma lista och ett prov kan
    mata den med en handgjord lista. Ordningen inom en sprint är förslagets egen.
    """
    def number(item: dict) -> int:
        return int(item.get("sprint") or 1)

    return [{"sprint": sprint, "items": [i for i in items if number(i) == sprint]}
            for sprint in sorted({number(i) for i in items})]


def sprint_holds(items: list, words: dict) -> str:
    """Vad en sprint rymmer, i en rad: epikerna i den, annars de första uppgifterna."""
    names = []
    for item in items:
        if is_epic(item) and item["summary"] not in names:
            names.append(item["summary"])
    if not names:
        names = [i["summary"] for i in items[:2]]
    return ", ".join(names[:3]) or words["empty"]


def avklippt(documents, words: dict) -> str:
    """Markören för underlag som klipptes: vad som visades av vad som fanns.

    Ren funktion, så att den går att prova: summan av det visade mot summan av det hela,
    över de dokument som faktiskt klipptes.
    """
    klippta = [n for n in documents or [] if (n or {}).get("truncated")]
    if not klippta:
        return ""
    visat = sum(n.get("chars") or 0 for n in klippta)
    helt = sum(n.get("documentChars") or 0 for n in klippta)
    return " ({}: {} {} {} {})".format(words["cut"], visat, words.get("of") or "av",
                                       helt, words.get("chars") or "tecken")


def plan_document(items, meta, lang: str = "") -> str:
    """Fördelningen som en A4-sida HTML: översikt över sprintarna, sedan varje sprint med
    sina uppgifter. Ren funktion -- inget anrop, ingen fil, ingenting skrivet."""
    words = plan_words(lang)
    sprints = plan_sprints(items)
    agent = str(meta.get("agent") or "").strip()
    kinds = {}
    for item in items:
        name = str(item.get("type") or "Task")
        kinds[name] = kinds.get(name, 0) + 1
    meta_line = " · ".join(x for x in [
        meta.get("project") and "{} {}".format(words["project"], escape(str(meta["project"]))),
        meta.get("repo") and escape(str(meta["repo"])),
        escape(str(meta.get("date") or "")),
        # Antalet dokument, och om något av dem bara lästes till en del: vad som klippts
        # bort hör synas på pappret också, inte bara i panelens rad.
        meta.get("documents") and "{} {}{}".format(
            len(meta["documents"]),
            words["documents"] if len(meta["documents"]) > 1 else words["document"],
            # Markören säger hur mycket som klipptes, inte bara att något gjorde det:
            # "klippt" utan siffror säger inte om det saknas en rad eller en tredjedel.
            avklippt(meta["documents"], words)),
    ] if x)

    rows = []
    for group in sprints:
        seen = set()
        body = []
        for item in group["items"]:
            nested = bool(item.get("epic")) and item["epic"] in seen
            seen.add(item["summary"])
            body.append(
                '<div class="item{nested}">'
                '<span class="type">{type}</span>'
                '<span class="sum">{summary}</span>'
                '{priority}{beside}{desc}</div>'.format(
                    nested=" nested" if nested else "",
                    type=escape(str(item.get("type") or "Task")),
                    summary=escape(str(item.get("summary") or "")),
                    priority=(' <span class="prio">{}</span>'.format(
                        escape(str(item["priority"]))) if item.get("priority") else ""),
                    beside=(' <span class="beside">{} {}</span>'.format(
                        words["under"], escape(str(item["epic"])))
                        if item.get("epic") and not nested else ""),
                    desc=('<div class="desc">{}</div>'.format(escape(str(item["description"])))
                          if item.get("description") else "")))
        rows.append(
            '<section class="sprint"><h2>{words[sprint]} {n}'
            '<span class="count">{c} {what}</span></h2>{body}</section>'.format(
                words=words, n=group["sprint"], c=len(group["items"]),
                what=words["issues"] if len(group["items"]) != 1 else words["issue"],
                body="".join(body)))

    overview = "".join(
        "<tr><td><b>{} {}</b></td><td>{}</td><td>{}</td></tr>".format(
            escape(words["sprint"]), group["sprint"], len(group["items"]),
            escape(sprint_holds(group["items"], words))) for group in sprints)

    return PLAN_HTML.format(
        lang="sv" if plan_words(lang) is PLAN_WORDS["sv"] else "en",
        css=PLAN_CSS,
        title=escape(words["title"]),
        meta=meta_line,
        wish=('<p class="wishlab">{}</p><p class="wish">{}</p>'.format(
            escape(words["wish"]), escape(str(meta.get("wish") or "")))
            if (meta.get("wish") or "").strip() else ""),
        overview=escape(words["overview"]),
        head=escape(words["sprint"]),
        holds=escape(words["holds"]),
        rows=overview,
        kinds="{}: {}".format(
            escape(words["kinds"]),
            escape(" · ".join("{} {}".format(name, count) for name, count in kinds.items()))),
        body="".join(rows),
        note=escape(words["note"]),
        source=('<p class="source">{}</p>'.format(escape(words["source"].format(agent)))
                if agent else ""),
    )


def pdf_browser() -> str:
    """Vilken webbläsare som gör pappret. Chromium skriver PDF ur vanlig HTML, så
    dokumentet är samma markup som allt annat här i huset i stället för ännu ett
    PDF-bibliotek att hålla reda på (mätt: ~1 s för en A4-sida)."""
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "brave", "brave-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("no chromium on PATH to write the PDF with (install chromium)")


def plan_pdf(items: list, meta: dict, out_path, lang: str = "") -> str:
    """Skriver fördelningen till out_path och svarar med sökvägen. Kastar om filen inte
    blev något: ett tomt papper skall säga det, inte se ut som ett som gick bra."""
    out = Path(out_path).expanduser()
    folder = Path(tempfile.mkdtemp(prefix="jira-flow-pdf-"))
    source = folder / "plan.html"
    source.write_text(plan_document(items, meta, lang), encoding="utf-8")
    done = subprocess.run(
        [pdf_browser(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         "--user-data-dir=" + str(folder / "profile"), "--print-to-pdf=" + str(out),
         source.as_uri()],
        capture_output=True, text=True, timeout=180)
    if not out.is_file() or not out.stat().st_size:
        raise RuntimeError("the PDF was not written ({}): {}".format(
            done.returncode, (done.stderr or done.stdout or "no output").strip()[-200:]))
    shutil.rmtree(folder, ignore_errors=True)
    return str(out)


def agent_label(answered_by) -> str:
    """Vem som svarade, som ett namn. Provet skrev answered_by[0] -- för en sökväg blir
    det första tecknet, alltså "/" i stället för agentens namn."""
    if isinstance(answered_by, str):
        return os.path.basename(answered_by) or answered_by
    return (answered_by or ["(no agent answered)"])[0]


def sprint_windows(start: str, weeks: int, count: int) -> list:
    """Start- och slutdatum per sprint, back to back ur startdagen. Ren funktion, så
    provet kan mata den med ett eget datum i stället för med dagens."""
    day = date.today()
    if str(start or "").strip():
        day = datetime.strptime(str(start).strip(), "%Y-%m-%d").date()
    width = max(1, int(weeks or 2))
    windows = []
    for number in range(max(1, int(count or 1))):
        first = day + timedelta(days=7 * width * number)
        windows.append((first.isoformat(), (first + timedelta(days=7 * width - 1)).isoformat()))
    return windows


def sprint_write(client_, board: dict, placed: list, weeks: int, start: str) -> list:
    """En sprint per nummer i förslaget, med sina ärenden i. Vad som skapades svaras.

    Ärendena skrivs först och flyttas in efteråt: Jira delar ut nycklarna vid skapandet,
    och en sprint med ärenden i är ett andra steg. En sprint som redan står på tavlan med
    samma namn används i stället för att en ny skapas -- importen skall gå att köra om en
    gång utan att tavlan fylls av två likadana "Sprint 1".
    """
    numbers = sorted({int(n) for n, _ in placed})
    if not numbers or not str((board or {}).get("id") or "").strip():
        raise RuntimeError("the board did not say which board it is; no sprint to create in")
    known = {str(s.get("name") or "").strip().lower(): s for s in (board.get("sprints") or [])}
    windows = sprint_windows(start, weeks, max(numbers))
    made = []
    for number in numbers:
        name = "Sprint {}".format(number)
        first, last = windows[number - 1]
        row = known.get(name.lower()) or {}
        sprint_id = str(row.get("id") or "")
        if not sprint_id:
            row = client_.sprint_create(str(board["id"]), name, first, last) or {}
            sprint_id = str(row.get("id") or "")
            if not sprint_id:
                raise RuntimeError("Jira took {} but gave back no id".format(name))
        keys = [key for n, key in placed if int(n) == number and key]
        if keys:
            client_.sprint_add(sprint_id, keys)
        made.append({"name": name, "id": sprint_id, "keys": keys,
                     "start": str(row.get("startDate") or first),
                     "end": str(row.get("endDate") or last)})
    return made


def agents_from(config, override: str = ""):
    """Användarens lista, annars den skeppade. Miljövariabeln går före allt."""
    if override.strip():
        return [override.strip()]
    listed = (config or {}).get("agents")
    if isinstance(listed, list):
        commands = [AGENT_UPGRADE.get(str(c).strip(), str(c).strip())
                    for c in listed if str(c).strip()]
        if commands:
            return commands
    return list(AGENT_CHAIN)


def load_flow_config() -> dict:
    return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}


def save_flow_config(data: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.parent.chmod(0o700)
    with CONFIG_FILE.open("w", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    CONFIG_FILE.chmod(0o600)


def agent_argv(prompt: str):
    """Vilken CLI som ska svara, och hur den vill ha texten.

    Kedjan i tur och ordning: första installerade vinner. JIRA_FLOW_AGENT överstyr
    hela kedjan. Ett kommando med {prompt} får texten som argument; annars går den
    på stdin -- och stängs, så en CLI som väntar på mer input inte kan hänga panelen.
    """
    commands = agents_from(load_flow_config(), AGENT)
    for command in commands:
        argv = shlex.split(command)
        if not argv or not shutil.which(argv[0]):
            continue
        if "{prompt}" in command:
            return [piece.replace("{prompt}", prompt) for piece in argv], ""
        return argv, prompt
    raise RuntimeError("no agent installed; tried {} (set JIRA_FLOW_AGENT)".format(
        ", ".join(shlex.split(c)[0] for c in commands if shlex.split(c))))


def memory_recent(project: str = "", days: int = None, now: float = None) -> list:
    """Turens minne: det som svarades inom horisonten, äldst först.

    Filen är JSONL och läses i sin helhet -- en veckas chatt är några hundra rader, och
    en databas för det vore mer kod än den ersätter. Allt äldre än horisonten skrivs
    bort när filen läses: minnet tömmer sig självt, ingen städning behöver kommas ihåg.
    """
    if not MEMORY_FILE.exists():
        return []
    window = days if days is not None else MEMORY_DAYS
    edge = (now if now is not None else time.time()) - window * 86400
    kept, dropped, rows = [], 0, []
    for line in MEMORY_FILE.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            dropped += 1
            continue
        when = float(entry.get("at") or 0)
        if when < edge:
            dropped += 1
            continue
        rows.append(entry)
        if not project or str(entry.get("project") or "").upper() == project.upper():
            kept.append(entry)
    if dropped:
        memory_rewrite(rows)
    return kept


def memory_rewrite(rows: list) -> None:
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.parent.chmod(0o700)
        with MEMORY_FILE.open("w", encoding="utf-8") as fh:
            for entry in rows:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        MEMORY_FILE.chmod(0o600)
    except OSError:
        pass          # ett minne som inte går att skriva är inte värt att fälla en tur för


def memory_remember(entry: dict) -> None:
    """En tur till i minnet. Anropas efter svaret, aldrig innan: ett minne utan svar
    vore ett minne av en fråga ingen besvarade."""
    entry = dict(entry)
    entry.setdefault("at", time.time())
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.parent.chmod(0o700)
        with MEMORY_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        MEMORY_FILE.chmod(0o600)
    except OSError:
        pass


def memory_lines(rows: list, limit: int = MEMORY_TURNS, budget: int = MEMORY_CHARS):
    """Minnet som text åt agenten, och vad som blev kvar av det.

    Kortat med besked: står det att raden är kapad är den det, och står det att fler
    turer finns är de kvar i filen. Ett minne som ser fullständigt ut men inte är det
    är värre än inget minne.
    """
    if not rows:
        return [], 0
    picked, used, cut = [], 0, 0
    for entry in reversed(rows[-limit:]):
        stamp = time.strftime("%a %H:%M", time.localtime(float(entry.get("at") or 0)))
        question = " ".join(str(entry.get("question") or "").split())[:240]
        answer = " ".join(str(entry.get("answer") or "").split())
        if len(answer) > 400:
            answer = answer[:400] + " …"
        line = "{} | {} | {} -> {}".format(stamp, entry.get("project") or "?", question, answer)
        if used + len(line) > budget:
            cut = len(rows) - len(picked)
            break
        picked.append(line)
        used += len(line)
    picked.reverse()
    return picked, len(rows) - len(picked) + cut


def say_step(text: str) -> None:
    """Ett steg ut till den som tittar, på samma väg som agentens egna händelser.

    NDJSON på stdout, en rad per steg: panelen ritar den medan den kommer i stället för att
    visa en räknare och hoppas. Bara när --stream är satt -- annars är stdout svaret, och
    ett steg mitt i det vore skräp i svaret.
    """
    sys.stdout.write(json.dumps({"type": "step", "text": text}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read_agent_reply(out: str) -> str:
    """Svaret ur det CLI:t skrev -- inte dekoren runt det.

    Med --format stream-json kommer svaret som NDJSON, och sista raden av typen
    "result" bär texten. Allt annat (rutan, sessionsraden, resume-tipset) är CLI:ts
    egen terminalutsmyckning och hör inte till svaret: den som läste det som ett svar
    fick en ruta i knät. En CLI som skriver ren text (agy, claude -p) går rakt igenom
    -- känner vi igen strömmen tar vi resultatet, annars är hela utdata svaret.
    """
    text, chunks, stream = "", [], False
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or not event.get("type"):
            continue
        stream = True
        if event["type"] == "text" and isinstance(event.get("text"), str):
            chunks.append(event["text"])
        elif event["type"] == "result" and isinstance(event.get("text"), str):
            text = event["text"]
    if text:
        return text.strip()
    if chunks:
        return "".join(chunks).strip()
    return "" if stream else out.strip()


def ask_agent_stream(prompt: str, env: dict = None, on_event=None, cwd: str = None) -> str:
    """Samma anrop som ask_agent, men varje händelse får passera medan den kommer.

    CLI:t skriver NDJSON (--format stream-json): texten i bitar, varje verktygsanrop och
    dess resultat, och sist hela svaret. on_event får varje rad som den är -- den som
    visar den behöver inte vänta på att agenten ska bli klar, och behöver inte gissa vad
    som hände under tiden. Svaret läses ur samma ström, av samma funktion som förut.
    """
    argv, stdin_text = agent_argv(prompt)
    where = dict(os.environ)
    where.update(env or {})
    if not env:
        where.pop("GODJIRA_MCP_PROJECT", None)
    # Mätt mot hermes-CLI:t: verktygsanropen kommer ut medan de händer, men hela
    # svarstexten släpps i ett svep när turens modellsvar är färdigt (89 bitar inom 0,1 s
    # -- samma med tty och med PYTHONUNBUFFERED). Det sitter i CLI:t, inte i röret här, så
    # texten ritas när den kommer och verktygen ritas medan de händer. Ett annat CLI som
    # skriver bitarna i sin egen takt får sin text strömmad utan ändring här.
    try:
        # cwd finns för körningen (`work`): agenten skall stå i sin egen worktree, inte i
        # den katalog panelen råkade startas från. Chatten lämnar den tom, som förut.
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=where, cwd=cwd)
    except FileNotFoundError:
        raise RuntimeError("{} is not installed".format(argv[0]))
    printed = []
    try:
        if stdin_text:
            proc.stdin.write(stdin_text)
        proc.stdin.close()
    except (BrokenPipeError, ValueError):
        pass
    for line in proc.stdout:
        printed.append(line)
        if on_event and line.strip().startswith("{"):
            try:
                on_event(json.loads(line))
            except ValueError:
                pass
    try:
        proc.wait(timeout=AGENT_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("{} gave no answer in {}s".format(argv[0], AGENT_TIMEOUT))
    if proc.returncode != 0:
        raise RuntimeError("{} exited {}: {}".format(
            argv[0], proc.returncode, (proc.stderr.read() or "").strip()[:200]))
    return read_agent_reply("".join(printed))


def ask_agent(prompt: str, env: dict = None, cwd: str = None) -> str:
    """Prompten in, svaret ut. Hermes i grunden, sedan Antigravity; JIRA_FLOW_AGENT
    pekar på vilken CLI som helst som pratar stdin/stdout.

    `env` läggs ovanpå den ärvda miljön. Chatten skickar med GODJIRA_MCP_PROJECT där:
    agenten startar sin MCP-koppling mot GodJIRA som underprocess och ärver variabeln,
    så kopplingen är låst till projektet utan att någon config behöver röras.
    """
    return ask_agent_stream(prompt, env, None, cwd)


CHAT_CHARS = 12000      # sammanhanget agenten får. Mer än så slutar den läsa och börjar gissa.


def chat_prompt(project: str, repo: str, question: str, history=None, errors=None,
                graph=None, memory=None) -> str:
    """Vad agenten får: frågan, sammanhanget den behöver, och gränsen för uppdraget.

    Samtalet följer med varje tur. Det är hela skillnaden mot att hålla en session:
    GodJIRA behöver inte minnas något mellan turerna, den skickar med det som sades.
    """
    lines = [
        "You are the assistant inside GodJIRA, helping a developer with one repo and its Jira project.",
        "",
        "The project is {} and the GodJIRA MCP connection is locked to it: read and write that".format(project),
        "project, nothing else. If a question needs another project, say so instead of guessing.",
        "Answer in the language the question is written in. Be concrete -- name the issue key, the file,",
        "the command -- and say plainly when you do not know. Say what you checked.",
        "",
        "Repository: {}".format(repo or "(not linked to a local clone yet)"),
    ]
    if graph:
        counts = graph.get("counts") or {}
        hubs = ", ".join("{}{}".format(str(h.get("package") or "").split(".")[-1],
                                       " (" + str(h.get("usedBy")) + ")")
                         for h in (graph.get("hubs") or [])[:5])
        lines += [
            "The knowledge graph: {} issues, {} files, {} packages, {} relations.".format(
                counts.get("ärenden", "?"), counts.get("filer", "?"), counts.get("paket", "?"),
                counts.get("relationer", "?")),
            "Packages the rest leans on: {}".format(hubs or "(no imports read yet)"),
        ]
    if memory:
        recent, left = memory_lines(memory)
        if recent:
            lines += ["", "What you answered earlier in this project, newest last:"]
            lines += recent
            if left:
                lines += ["({} earlier turns are in the memory file but did not fit here)".format(left)]
    if errors:
        lines += ["", "What the panel itself cannot answer right now:"]
        lines += ["- {}: {}".format(str(e.get("what") or "?"), str(e.get("why") or "")) for e in errors[:8]]
    if history:
        lines += ["", "The conversation so far:"]
        for turn in history[-12:]:
            who = "Developer" if str(turn.get("role")) == "user" else "Assistant"
            lines.append("{}: {}".format(who, str(turn.get("text") or "").strip()[:1200]))
    lines += ["", "The developer asks:", question.strip()]
    text = "\n".join(lines)
    if len(text) > CHAT_CHARS:
        text = text[:CHAT_CHARS] + "\n\n[context cut here -- ask for the rest if you need it]"
    return text


def cmd_chat(args) -> int:
    """En tur med agenten: frågan in, svaret ut.

    Kopplingen är användarens egen kedja (samma som flödet använder) -- den väljs med
    `jira_flow agent`. Projektet följer med i miljön, så agentens MCP-koppling mot
    GodJIRA är låst till det projektet: den agenten ser inget annat.
    """
    question = (args.question or "").strip()
    if not question and not sys.stdin.isatty():
        question = sys.stdin.read().strip()
    if not question:
        say(args, {"ok": False, "error": "no question"},
            ["nothing to ask -- give the question as an argument or on stdin"])
        return 2
    root = plan_repo_dir(args)
    project, source = link_project(args)
    if not project:
        say(args, {"ok": False, "error": "no Jira project to ask about"},
            ["no project to ask about -- link the repo first: jira_flow link set <repo> <PROJECT>"])
        return 2
    graph = {}
    wherever = SCAN_FILE / "graph-{}.json".format(project)
    if wherever.exists():
        try:
            graph = json.loads(wherever.read_text())
        except (OSError, ValueError):
            graph = {}
    memory = memory_recent(project)
    history, errors = [], []
    for path, into in ((args.history, history), (args.context, errors)):
        if not path:
            continue
        try:
            loaded = json.loads(Path(path).read_text())
        except (OSError, ValueError) as exc:
            say(args, {"ok": False, "error": "{}: {}".format(type(exc).__name__, exc)},
                ["could not read {}: {}".format(path, exc)])
            return 2
        into.extend(loaded if isinstance(loaded, list) else [])
    prompt = chat_prompt(project, str(root or ""), question, history, errors, graph, memory)
    if args.dry_run:
        print(prompt)
        return 0
    tools = []

    def emit(event):
        """En händelse från agenten, vidare ut som den är. Panelen ritar den medan den
        kommer; att hålla den tillbaka vore att göra om samma väntan som förut."""
        if event.get("type") == "tool_use":
            tools.append(str(event.get("name") or ""))
        if args.stream:
            sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    try:
        if args.stream:
            answer = ask_agent_stream(prompt, {"GODJIRA_MCP_PROJECT": project}, emit)
        else:
            answer = ask_agent(prompt, {"GODJIRA_MCP_PROJECT": project})
    except RuntimeError as exc:
        if args.stream:
            sys.stdout.write(json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        say(args, {"ok": False, "error": str(exc), "project": project},
            ["the agent could not answer: {}".format(exc)] if not args.stream else [])
        return 2
    answer = answer.strip()
    who = ""
    try:
        who = Path(agent_argv("")[0][0]).name
    except RuntimeError:
        who = ""
    memory_remember({"project": project, "repo": str(root or ""), "agent": who,
                     "question": question, "answer": answer, "tools": len(tools)})
    payload = {"ok": True, "project": project, "agent": who, "source": source,
               "answer": answer, "promptChars": len(prompt), "tools": tools,
               "remembered": len(memory) + 1}
    if args.stream:
        sys.stdout.write(json.dumps(dict(payload, type="done"), ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0 if answer else 1
    say(args, payload, [answer] if answer else ["(the agent answered nothing)"])
    return 0 if answer else 1


def cmd_memory(args) -> int:
    """Vad agenten minns: turerna inom horisonten -- och hur man tömmer dem."""
    rows = memory_recent(args.project or "", days=args.days)
    if args.clear:
        memory_rewrite([])
        say(args, {"ok": True, "cleared": len(rows), "file": str(MEMORY_FILE)},
            ["minnet är tömt ({} turer togs bort)".format(len(rows))])
        return 0
    payload = {"ok": True, "days": args.days, "turns": len(rows), "file": str(MEMORY_FILE),
               "entries": [{"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(float(r.get("at") or 0))),
                            "project": r.get("project"), "question": r.get("question"),
                            "answer": r.get("answer"), "tools": r.get("tools")}
                           for r in rows]}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if not rows:
        print("minnet är tomt — inget svarat inom {} dagar ({})".format(args.days, MEMORY_FILE))
        return 0
    print("{} turer inom {} dagar ({})".format(len(rows), MEMORY_DAYS, MEMORY_FILE))
    for entry in payload["entries"][-MEMORY_TURNS:]:
        print("  {} {} | {}".format(entry["at"], entry["project"],
                                    " ".join(str(entry["question"] or "").split())[:90]))
    return 0


def cmd_agent(args) -> int:
    """Användarens egen lista: visa, lägg till, byt ut, ta bort."""
    config = load_flow_config()
    listed = agents_from(config, "")
    shipped = list(AGENT_CHAIN)
    action = args.action or "list"

    if action == "list":
        rows = [{"command": command,
                 "binary": (shlex.split(command) or [""])[0],
                 "installed": bool(shutil.which((shlex.split(command) or [""])[0]))}
                for command in listed]
        if getattr(args, "json", False):
            say(args, {"ok": True, "agents": rows, "yours": "agents" in config,
                       "anything": any(row["installed"] for row in rows)}, [])
            return 0 if (any(row["installed"] for row in rows) or "agents" not in config) else 2
        for number, command in enumerate(listed, 1):
            first = shlex.split(command)[0] if shlex.split(command) else ""
            mark = "installed" if shutil.which(first) else "NOT installed"
            print("{}. {:<28} {}".format(number, command, mark))
        if "agents" not in config:
            print("(the shipped list; your own is written here with `agent add` or `agent set`)")
        elif not any(shutil.which(shlex.split(c)[0]) for c in listed if shlex.split(c)):
            print("none of them is installed -- nothing would answer.")
            return 2
        return 0

    commands = listed if ("agents" in config or args.action in ("add", "rm")) else shipped
    if action == "add":
        if not args.commands:
            print("jira_flow: agent add needs a command.", file=sys.stderr)
            return 2
        commands = commands + list(args.commands)
    elif action == "set":
        if not args.commands:
            print("jira_flow: agent set needs at least one command.", file=sys.stderr)
            return 2
        commands = list(args.commands)
    elif action == "rm":
        if not args.commands:
            print("jira_flow: agent rm needs a number or a name.", file=sys.stderr)
            return 2
        for target in args.commands:
            match = None
            for index, command in enumerate(commands, 1):
                if str(index) == target or shlex.split(command)[0] == target:
                    match = index
                    break
            if match is None:
                print("jira_flow: {} is not in your list.".format(target), file=sys.stderr)
                return 2
            commands = commands[:match - 1] + commands[match:]
    else:
        print("jira_flow: agent {}? list, add, set or rm.".format(action), file=sys.stderr)
        return 2

    if not commands:
        print("jira_flow: that would leave no agent at all; use `agent set <command>`.", file=sys.stderr)
        return 2
    config["agents"] = commands
    save_flow_config(config)
    for number, command in enumerate(commands, 1):
        print("{}. {}".format(number, command))
    return 0


def pick_files(title: str) -> list:
    """Filväljaren på maskinen. zenity är den Omarchy har — en egen dialog i QML
    vore ett projekt, och den här fungerar från terminalen också."""
    tool = shutil.which("zenity")
    if not tool:
        raise RuntimeError("no file dialog on this machine (zenity is missing)")
    done = subprocess.run([tool, "--file-selection", "--multiple", "--separator", "\n",
                           "--title", title,
                           "--file-filter=Papers | *.pdf *.txt *.md *.docx *.odt "
                           "*.xlsx *.pptx *.csv *.json *.rtf"],
                          capture_output=True, text=True, timeout=900)
    if done.returncode != 0:
        # 1 = avbruten dialog och det är inget fel. Men zenity skriver också till
        # stderr när den inte kan öppna en bildskärm alls — mätt: den vägen gav
        # förut ett tomt val, alltså en knapp som ingenting händer med.
        complaint = (done.stderr or "").strip()
        if complaint:
            raise RuntimeError(complaint.splitlines()[-1][:200])
        return []
    return [row.strip() for row in (done.stdout or "").splitlines() if row.strip()]


def cmd_pick(args) -> int:
    """Panelens filvalsknapp. Samma svarsväg som resten: JSON med ok/error."""
    try:
        files = pick_files(getattr(args, "title", "") or "Choose the papers")
    except (RuntimeError, OSError) as exc:
        say(args, {"ok": False, "error": str(exc)}, ["jira_flow: " + str(exc)])
        return 2
    say(args, {"ok": True, "files": files},
        files if files else ["(nothing chosen)"])
    return 0


def cmd_plan(client_, args) -> int:
    """Kundens önskemål in, ärendeförslag ut. Ingenting skrivs förrän --create.

    Med --proposal PATH läses listan ur en fil i stället för ur agentens svar:
    panelen visar förslaget, människan bockar av, och exakt den listan skrivs.
    Agenten tillfrågas inte en gång till -- den svarar olika varje gång -- så det
    som godkändes är det som hamnar på tavlan.
    """
    approved = getattr(args, "proposal", "") or ""
    if not approved and getattr(args, "text", ""):
        # Panelen har texten i ett fält, inte i en fil: argv är oshellat, så
        # inget kan citeras sönder på vägen.
        wish = args.text
    elif args.file:
        wish = Path(args.file).read_text()
    else:
        wish = "" if sys.stdin.isatty() else sys.stdin.read()
    if not approved and not wish.strip():
        message = "No wish to work from (stdin, --file PATH or --proposal PATH)."
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2

    # Projektet och repot avgörs här, en gång: --project vinner, annars länken för
    # repot (--repo, --repo-name eller den lokala kopian av ett länkat repo), annars
    # standarden. Panelen skickar bara repot och får tillbaka var nyckeln kom ifrån.
    repo_dir = plan_repo_dir(args)
    project, project_source = link_project(args, repo_dir)

    # Stegen: vad som läses och när. Panelen visar dem medan de händer, så en väntan på
    # flera minuter ser ut som arbete i stället för som en död knapp.
    streaming = bool(getattr(args, "stream", False))

    def step(text):
        if streaming:
            say_step(text)

    docs_text, docs_notes, repo_text, repo_info = "", [], "", {}
    if not approved:
        try:
            step("läser underlaget …")
            docs_text, docs_notes = read_context(getattr(args, "context", []) or [])
            step("underlaget: {} dokument, {} tecken".format(
                len(docs_notes), sum(n.get("chars") or 0 for n in docs_notes)))
            if repo_dir:
                step("läser repot {} …".format(os.path.basename(str(repo_dir))))
                repo_text, repo_info = repo_context(repo_dir)
                if repo_info.get("error"):
                    raise RuntimeError("--repo {}: {}".format(repo_dir, repo_info["error"]))
                # Koden, inte bara loggen: anslaget skall mötas av vad som redan
                # finns, och filnamnen styr vilka filer som läses (önskemålets ord).
                code_text, code_info = code_context(Path(repo_dir), code_words(wish))
                repo_info["code"] = code_info
                step("koden: {} filer i {}".format(
                    code_info.get("files") or 0, repo_info.get("repo") or os.path.basename(str(repo_dir))))
                if code_text:
                    repo_text = "{}\n\n{}".format(repo_text, code_text) if repo_text else code_text
        except (RuntimeError, OSError) as exc:
            message = "the papers could not be read: {}".format(exc)
            say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
            return 2

    board = client_.board(project)
    varningar = []          # det agenten sade som en människa bör se innan hon godkänner
    if approved:
        # Samma kontroll som agentens svar går igenom: en lista någon har redigerat i
        # är inte mer pålitlig än en modell, och en halv lista ska bli noll ärenden.
        answered_by, items = ["the approved list"], []
        try:
            items = parse_plan(Path(approved).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            message = "the approved list could not be used: {}".format(exc)
            say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
            return 2
    else:
        # Agenten får hela underlaget: önskemålet, dokumenten, repot, teamet och tavlan.
        # Teamet: antal, roller och sprintlängd -- ur konfigurationen, med argumenten överst.
        team = team_of(load_flow_config(), args)
        step(team_text(team) or "inget team angett — fördelningen vet inte hur många som skall göra det")
        # Tavlan: de befintliga korten som text, så jämförelsen inte hänger på att agenten
        # själv råkar anropa tavlan.
        på_tavlan, tavlefel, tavlantal = board_text(client_, project)
        if på_tavlan:
            step("tavlan: {} befintliga ärenden lästa".format(tavlantal))
        elif tavlefel:
            step("tavlan kunde inte läsas: " + tavlefel)
        prompt = PLAN_PROMPT.format(project=project, limit=PLAN_MAX, sprints=PLAN_SPRINTS,
                                    types=", ".join(client_.types(project)) or "Story, Task, Bug",
                                    context=build_context(docs_text, repo_text, docs_notes,
                                                          link_context(client_, args),
                                                          team_text(team), på_tavlan),
                                    wish=wish.strip())
        answered_by, answer, items = ["(no agent answered)"], "", []
        try:
            answered_by = agent_argv(prompt)[0]
            step("agenten läser underlaget och koden, och föreslår ärenden …")
            if streaming:
                # Agentens egna händelser vidare ut, som chatten gör: verktygsanropen är
                # det som visas medan de händer.
                def emit(event):
                    if event.get("type") == "tool_use":
                        say_step("verktyg: {}".format(str(event.get("name") or "")[:60]))
                    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
                answer = ask_agent_stream(prompt, None, emit)
            else:
                answer = ask_agent(prompt)
            varningar = []
            items = parse_plan(answer, varningar)
            for varning in varningar:
                step(varning)
            epics = [i for i in items if is_epic(i)]
            step("förslaget: {} ärenden, {} epics, {} sprints".format(
                len(items), len(epics), len(plan_sprints(items))))
        except (ValueError, RuntimeError) as exc:
            # Ett svar som inte går att tolka sparas: annars är det borta för alltid och
            # den som felsöker har bara felet att gå på. Svaret kan innehålla kundtext,
            # så filen är 0600 och ligger i användarens egen state-katalog.
            kept = ""
            if answer and isinstance(exc, ValueError):
                try:
                    os.makedirs(os.path.dirname(ANSWER_LOG), mode=0o700, exist_ok=True)
                    with open(ANSWER_LOG, "a", encoding="utf-8") as fh:
                        os.chmod(ANSWER_LOG, 0o600)
                        fh.write("\n--- {} | {} | {} chars\n{}\n".format(
                            time.strftime("%Y-%m-%d %H:%M:%S"), answered_by, len(answer), answer))
                    kept = " -- the answer is kept in {}".format(ANSWER_LOG)
                except OSError as keep_exc:
                    kept = " -- the answer could not be kept ({})".format(keep_exc)
            message = str(exc) + kept
            say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
            return 2

    context_note = {"documents": docs_notes, "repo": repo_info}
    if approved:
        context_note["approved"] = approved
    # Fördelningen som papper: ur exakt den lista som just blev förslaget, aldrig ur ett
    # andra varv hos agenten (den svarar olika varje gång). Ett papper som inte blev
    # något får inte fälla förslaget -- felet sägs högt i stället.
    pdf_path, pdf_error = "", ""
    if getattr(args, "pdf", ""):
        try:
            pdf_path = plan_pdf(items, {"wish": (getattr(args, "text", "") or wish).strip(),
                                        "documents": docs_notes, "project": project,
                                        "repo": getattr(args, "repo_name", "")
                                                or os.path.basename(repo_dir or ""),
                                        # En godkänd lista har ingen agent bakom sig, och
                                        # då skall raden inte hitta på en.
                                        "agent": "" if approved else agent_label(answered_by),
                                        "date": time.strftime("%Y-%m-%d")},
                                args.pdf, getattr(args, "lang", ""))
        except (RuntimeError, OSError) as exc:
            pdf_error = str(exc)
    if args.json and not args.create:
        # Med --create kommer ett enda dokument, längst ner, med både förslaget och
        # nycklarna: en maskinläsare ska inte behöva tolka två JSON-dokument i rad.
        # Med --stream slutar raden strömmen: type=done säger att svaret är färdigt.
        out = {"ok": True, "created": False, "proposal": items,
               "warnings": varningar,
               "project": project, "projectSource": project_source, "agent": answered_by,
               "pdf": pdf_path, "pdfError": pdf_error,
               "context": context_note}
        if streaming:
            out["type"] = "done"
        print(json.dumps(out, ensure_ascii=False))
    elif not args.json:
        if docs_notes or repo_info:
            print("read with the wish: {} document(s) ({} chars){}".format(
                len(docs_notes), sum(n.get("chars") or 0 for n in docs_notes),
                ", repo {} ({})".format(repo_info.get("branch"), repo_info.get("repo"))
                if repo_info.get("repo") else ""))
            for note in docs_notes:
                print("  {}{}{}".format(note["path"], "" if not note.get("error") else " — " + note["error"],
                                        " [{} of {} chars]".format(note["chars"], note["documentChars"])
                                        if note.get("truncated") else ""))
        print("{} issue(s) proposed for {} ({}):".format(len(items), project, agent_label(answered_by)))
        print("  project from: {}".format(project_source))
        for number, item in enumerate(items, 1):
            print("  {}. [{}] {}{}  ({})".format(
                number, item["type"], item["summary"],
                "  — under: " + item["epic"][:44] if item.get("epic") else "",
                item["priority"] or "no priority"))
        for group in plan_sprints(items):
            print("  sprint {}: {}".format(
                group["sprint"], ", ".join(i["summary"][:36] for i in group["items"])))
        if pdf_path:
            print("the division as a PDF: {}".format(pdf_path))
        if pdf_error:
            print("the PDF was not written: {}".format(pdf_error))
    if not args.create:
        if not args.json:
            print("nothing written. again with --create writes exactly this list.")
        return 0

    created, keys = [], {}
    # Epics först: barnen behöver deras nycklar. Ordningen i listan får inte styra,
    # för ett barn som skrivs före sin epic blir ett träd utan rot.
    order = [item for item in items if is_epic(item)] + [item for item in items if not is_epic(item)]
    for item in order:
        if is_epic(item):
            item["parentKey"] = ""
        elif item.get("epic"):
            item["parentKey"] = keys.get(item["epic"], "")
            if not item["parentKey"]:
                message = "epicen skrevs inte, så uppgiften kunde inte läggas under den: {}".format(
                    item["epic"][:60])
                say(args, {"ok": False, "error": message, "created": created},
                    ["jira_flow: " + message])
                return 2
        try:
            answer = client_.create(board, item) or {}
        except Exception as exc:  # noqa: BLE001 -- en krasch får inte dölja en halv skrivning
            # Stanna på första felet: hellre halv tavla med besked än tyst halv tavla.
            message = "{} failed: {} ({} of {} written)".format(
                item["summary"][:40], exc if str(exc) else type(exc).__name__,
                len(created), len(items))
            say(args, {"ok": False, "error": message, "created": created},
                ["jira_flow: " + message,
                 "           already written: " + ", ".join(c["key"] for c in created)])
            return 2
        if isinstance(answer, str):
            answer = {"key": answer}
        key = answer.get("key") or (answer.get("result") or {}).get("key") or ""
        created.append({"key": key, "summary": item["summary"], "type": item.get("type"),
                        "epic": item.get("parentKey") or "",
                        "sprint": int(item.get("sprint") or 1)})
        keys[item["summary"]] = key
        if not args.json:
            print("created: {}  {}{}".format(key or "(no key back)", item["summary"],
                                             "  under {}".format(item["parentKey"])
                                             if item.get("parentKey") else ""))
    for entry in created:
        client_.log("flow-plan", entry["key"], entry["summary"][:80])
    # Sprintarna skrivs EFTER ärendena: Jira delar ut nycklarna vid skapandet, och en
    # sprint med ärenden i är ett andra steg. Att skapa sprintar rör tavlan, så det görs
    # bara när någon bad om det (--sprints) -- och ett fel här lämnar ärendena i fred
    # och säger var det stannade.
    sprints_made, sprint_error = [], ""
    if getattr(args, "sprints", False):
        placed = [(entry.get("sprint") or 1, entry.get("key") or "") for entry in created]
        try:
            sprints_made = sprint_write(client_, board, placed,
                                        getattr(args, "sprint_weeks", 2),
                                        getattr(args, "sprint_start", ""))
        except Exception as exc:  # noqa: BLE001 -- tavlan är skriven, det skall sägas
            sprint_error = "{} ({} issues are written)".format(
                exc if str(exc) else type(exc).__name__, len(created))
        for row_ in sprints_made:
            client_.log("flow-plan-sprint", row_["id"], row_["name"])
            if not args.json:
                print("sprint: {}  {} -> {}  ({})".format(
                    row_["name"], row_["start"], row_["end"],
                    ", ".join(row_["keys"]) or "no issues"))
        if sprint_error and not args.json:
            print("jira_flow: the sprints were not written: " + sprint_error)
    say(args, {"ok": True, "created": created, "proposal": items, "project": project,
               "projectSource": project_source,
               "sprints": sprints_made, "sprintError": sprint_error,
               "pdf": pdf_path, "pdfError": pdf_error,
               "agent": answered_by, "context": context_note}, [])
    return 0


def cmd_link(args) -> int:
    """Repots Jira-koppling: visa, sätt eller ta bort.

    Länken är det som gör att ett önskemål hamnar i rätt projekt utan att någon
    skriver projektnyckeln: panelens import skickar repot, och CLI:t slår upp resten.
    """
    action = (args.action or "list").lower()
    links = load_links()
    if action == "list":
        rows = [dict(value or {}, repo=key) for key, value in sorted(links.items())]
        say(args, {"ok": True, "links": rows},
            ["{} link(s){}".format(len(rows), ":" if rows else "")] +
            ["  {} -> {}{}{}".format(row["repo"], row.get("project") or "?",
                                     " ({})".format(row["issue"]) if row.get("issue") else "",
                                     " — " + str(row["note"]) if row.get("note") else "") for row in rows])
        return 0

    name = repo_slug(args.repo or "")
    if not name:
        message = "which repo? (a name, or owner/name)"
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2

    if action == "rm":
        # Ta bort både den exakta nyckeln och ett naket namn som pekar på samma repo.
        gone = [key for key in links if key.lower() == name.lower()] or (
            [str(link_for(name).get("repo") or "")] if link_for(name) else [])
        gone = [key for key in gone if key]
        if not gone:
            message = "{} is not linked".format(name)
            say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
            return 1
        for key in gone:
            links.pop(key, None)
        save_links(links)
        say(args, {"ok": True, "removed": gone}, ["unlinked: " + ", ".join(gone)])
        return 0

    project = (getattr(args, "project", "") or "").strip()
    if not project:
        message = "which Jira project? (--project KEY)"
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2
    # API:t först: en koppling utan fungerande token vore en koppling som ser ut att
    # finnas. Rollen och behörigheterna sparas med länken, så den som frågar vad man
    # får göra i projektet får Jiras svar i stället för en gissning.
    # Kravet gäller en riktig anslutning: i provläget finns ingen API av
    # konstruktion, och då skall länken kunna sättas utan att ljuga om en roll.
    bridging = client()
    try:
        caps = (project_caps(bridging, project.upper())
                if str(getattr(bridging, "cfg", {}).get("mode") or "") == "real" else {})
    except Exception as exc:      # noqa: BLE001 -- allt som inte svarar är samma svar
        message = ("kan inte koppla {} till {}: API:t för Jira svarar inte ({}). "
                   "Koppla på din API först (`jira_flow login`) och försök igen."
                   .format(name, project.upper(), exc))
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 1
    entry = {"project": project.upper(),
             "issue": (getattr(args, "issue", "") or "").strip().upper(),
             "note": (getattr(args, "note", "") or "").strip(),
             "account": caps.get("account") or "", "role": caps.get("role") or "",
             "can": caps.get("can") or [], "roleNote": caps.get("roleNote") or "",
             "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    links[name] = {key: value for key, value in entry.items() if value}
    save_links(links)
    say(args, {"ok": True, "link": dict(links[name], repo=name)},
        ["{} -> {}{}{}".format(name, entry["project"],
                               " ({})".format(entry["issue"]) if entry["issue"] else "",
                               " — " + entry["note"] if entry["note"] else "")])
    return 0


def cmd_scan(args) -> int:
    """Hela projektet mot repots kod: en karta över kopplingarna, och luckorna.

    Skrivs till `~/.config/jira-flow/scan-<PROJEKT>.json` (0600) så att panelen kan
    visa den utan att själv gå mot Jira -- samma arbetsdelning som länken.
    """
    started = time.time()
    project, source = link_project(args)
    # Utan namn: den enda länken är den man menar. Med ett namn som inte känns igen är
    # svaret inget repo alls -- annars skannade ett okänt namn AutoCore och skrev sin
    # scanning över det projektets fil (scan-<PROJEKT>.json).
    root = chosen_repo_dir(args)
    if not root or not Path(root).is_dir():
        say(args, {}, [])
        print("no local copy of {} to scan — link it, or give --repo <dir>".format(
            (getattr(args, "repo_name", "") or "the repo").strip()))
        return 1
    try:
        result = scan_map(client(), project, Path(root), limit=int(getattr(args, "limit", 250)))
        if result["issuesTotal"] > result["issues"]:
            print("note: the project has {} issues; the map covers the first {} "
                  "(--limit raises it).".format(result["issuesTotal"], result["issues"]))
    except Exception as exc:      # noqa: BLE001 -- ett svar, inte en stacktrace
        print("scan failed: {}: {}".format(type(exc).__name__, exc))
        return 2
    result.update({"projectSource": source, "repo": repo_slug_of_dir(root) or root,
                   "seconds": round(time.time() - started, 1)})
    SCAN_FILE.mkdir(parents=True, exist_ok=True)
    path = SCAN_FILE / "scan-{}.json".format(result["project"])
    try:
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        os.chmod(path, 0o600)
        result["savedTo"] = str(path)
    except OSError as exc:
        print("the map could not be written: {}: {}".format(type(exc).__name__, exc))
        return 2
    say(args, result, [
        "{}: {} issues against {} files in {}s — {} point at code, {} do not.".format(
            result["project"], result["issues"], result["files"], result["seconds"],
            result["mapped"], result["unmapped"]),
        "{} files are named by no issue. The map is at {}.".format(
            result["silentCount"], path),
    ])
    return 0


def cmd_repo(args) -> int:
    """Ett repo: vad det är, vad som är öppet, var kopian ligger, och Jira-länken.

    Ren läsning via gh (inget Jira, inget skrivs), så panelen kan visa den även när
    token är trasig -- och det är den enda vägen till detaljen, ingen egen kopia i
    frontend.
    """
    name = repo_slug(args.name or "")
    if not name:
        message = "which repo? (a name, or owner/name)"
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2
    try:
        if "/" not in name:
            owner = ((gh_json("api", "user") or {}).get("login") or "").strip()
            if not owner:
                raise RuntimeError("gh could not say who is logged in")
            name = "{}/{}".format(owner, name)
        about = gh_json("repo", "view", name, "--json",
                        "name,owner,description,visibility,isPrivate,isArchived,primaryLanguage,"
                        "stargazerCount,forkCount,defaultBranchRef,updatedAt,pushedAt,url,"
                        "licenseInfo,hasIssuesEnabled") or {}
    except (RuntimeError, ValueError) as exc:
        message = "{}: {}".format(name, exc)
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2

    notes = []
    prs, err = gh_soft("pr", "list", "--repo", name, "--state", "open", "--limit", str(REPO_ROWS),
                       "--json", "number,title,isDraft,updatedAt,url,headRefName")
    if err:
        notes.append("pull requests: " + err)
    issues, err = gh_soft("issue", "list", "--repo", name, "--state", "open", "--limit", str(REPO_ROWS),
                          "--json", "number,title,updatedAt,url")
    if err:
        notes.append("issues: " + err)
    raw_commits, err = gh_soft("api", "repos/{}/commits?per_page={}".format(name, REPO_ROWS))
    if err:
        notes.append("commits: " + err)
    commits = [{"sha": (row.get("sha") or "")[:7],
                "date": (((row.get("commit") or {}).get("author") or {}).get("date") or "")[:10],
                "summary": (((row.get("commit") or {}).get("message") or "").splitlines() or [""])[0][:120]}
               for row in (raw_commits or []) if isinstance(row, dict)]
    link = link_for(name)
    local = local_clone(name)
    payload = {
        "ok": True, "repo": (about.get("nameWithOwner") or name), "about": {
            "name": about.get("name"), "owner": ((about.get("owner") or {}).get("login") or ""),
            "description": about.get("description") or "",
            "visibility": about.get("visibility") or "",
            "isPrivate": bool(about.get("isPrivate")), "isArchived": bool(about.get("isArchived")),
            "language": ((about.get("primaryLanguage") or {}).get("name") or ""),
            "stars": about.get("stargazerCount"), "forks": about.get("forkCount"),
            "branch": ((about.get("defaultBranchRef") or {}).get("name") or ""),
            "pushedAt": about.get("pushedAt"), "updatedAt": about.get("updatedAt"),
            "url": about.get("url") or "https://github.com/" + name,
            "license": ((about.get("licenseInfo") or {}).get("spdxId") or ""),
            "hasIssues": bool(about.get("hasIssuesEnabled")),
        },
        "openPRs": prs or [], "openIssues": issues or [], "commits": commits,
        "link": link, "local": local, "notes": notes,
    }
    if not args.json:
        a = payload["about"]
        print("{}  ({}, {}{}{})".format(payload["repo"], a["visibility"].lower() or "?",
                                        a["language"] or "no language",
                                        ", ★{}".format(a["stars"]) if a["stars"] else "",
                                        ", arkiverat" if a["isArchived"] else ""))
        if a["description"]:
            print("  " + a["description"])
        print("  {}  ·  standardgren {}".format(a["url"], a["branch"] or "?"))
        if link.get("project"):
            print("  Jira: projekt {}{}".format(
                link["project"], " · " + link["issue"] if link.get("issue") else ""))
        else:
            print("  Jira: inte länkat än")
        print("  lokal kopia: " + (local or "ingen hittad"))
        print("  öppna PR:er: {}".format(len(payload["openPRs"])))
        for row in payload["openPRs"]:
            print("    #{} {}{}".format(row.get("number"), row.get("title") or "",
                                        " (utkast)" if row.get("isDraft") else ""))
        print("  öppna ärenden: {}".format(len(payload["openIssues"])))
        for row in payload["openIssues"]:
            print("    #{} {}".format(row.get("number"), row.get("title") or ""))
        print("  senaste commitarna:")
        for row in commits:
            print("    {}  {}  {}".format(row["sha"], row["date"], row["summary"]))
        for note in notes:
            print("  ! " + note)
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_current(client_, args) -> int:
    found = find(client_, current_jql(args.project), limit=5)
    if not found:
        # Never leave the caller guessing: an exit code with no words is what makes
        # a working step look like a broken one.
        print("{}: nothing of mine is in progress".format(args.project), file=sys.stderr)
        return 1
    if len(found) > 1:
        print("# {} in progress; the most recently updated is {}".format(len(found), found[0]["key"]),
              file=sys.stderr)
    print(found[0]["key"])
    return 0


# ------------------------------------------------------------------ shims

VSCODE_TASK = """{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Jira: take next critical",
      "type": "shell",
      "command": "python3",
      "args": ["{self}", "next"],
      "windows": { "command": "py" },
      "options": { "cwd": "${workspaceFolder}" },
      "presentation": { "reveal": "always", "panel": "shared" },
      "problemMatcher": []
    },
    {
      "label": "Jira: what am I on",
      "type": "shell",
      "command": "python3",
      "args": ["{self}", "current"],
      "options": { "cwd": "${workspaceFolder}" },
      "presentation": { "reveal": "always", "panel": "shared" },
      "problemMatcher": []
    }
  ]
}
"""

# Windows har ingen python3: py är Launcher-skapelsen, python finns i PATH-varianten.
# if-satsen, inte &&-kedja: ett felaktigt körningsresultat får inte betyda "kör en gång till".
LAUNCHER = ("@echo off\r\n"
            "where py >nul 2>nul\r\n"
            "if %errorlevel%==0 (\r\n"
            "  py \"%~dp0jira_flow.py\" %*\r\n"
            ") else (\r\n"
            "  python \"%~dp0jira_flow.py\" %*\r\n"
            ")\r\n")

IDEA_TOOL = """<tool name="Jira: take next critical" description="Assign the most critical Jira item to me and start it" showInMainMenu="true" showInEditor="true" showInProject="true" showInSearchPopup="true" disabled="false" useConsole="true" showConsoleOnStdOut="true" showConsoleOnStdErr="true" synchronizeAfterRun="true">
  <exec>
    <option name="COMMAND" value="{command}" />
    <option name="PARAMETERS" value="{params}" />
    <option name="WORKING_DIRECTORY" value="$ProjectFileDir$" />
  </exec>
</tool>
"""

AGY_RULE = """# Jira

Work items live in Jira; the credential is never in this repository.

## Taking work

    python3 {self} next            # most critical not-started item -> me -> In Progress
    python3 {self} next --dry-run  # show the pick and the move, write nothing

The pick is `project = {project} AND statusCategory = "To Do" AND (assignee IS
EMPTY OR assignee = currentUser()) ORDER BY priority DESC, created ASC`. The
command prints the item it took and the runners-up it skipped; never guess which
item is most critical, run it.

If this session has a Jira MCP server (for example the `godjira` one), prefer its
tools -- `jira_backlog`, `jira_update`, `jira_move` -- for anything this CLI does
not cover. Never delete or restore an issue.
"""

HOOK = """#!/bin/sh
# prepare-commit-msg: put the Jira key in the commit subject, so the issue and
# the commit are linked without anyone remembering a number.
# Written by `jira_flow install`. $1 = message file, $2 = source.
case "$2" in
  merge|squash|commit) exit 0 ;;   # git writes merge subjects; amend is deliberate
esac

[ -z "$1" ] && exit 0
grep -qE '[A-Z][A-Z0-9]+-[0-9]+' "$1" && exit 0

key=$(git branch --show-current 2>/dev/null | grep -oE '[A-Z][A-Z0-9]+-[0-9]+' | head -1)
if [ -z "$key" ]; then
  # Git for Windows kör hooks genom sin egen bash, så den här filen fungerar på alla
  # tre systemen. Tolken är det som skiljer: python3 på macOS/Linux, py eller python
  # på Windows. Slå upp den vid körning i stället för att skriva in en sökväg.
  py=""
  for kandidat in python3 py python; do
    command -v "$kandidat" >/dev/null 2>&1 && py="$kandidat" && break
  done
  if [ -n "$py" ]; then
    key=$("$py" {self} current 2>/dev/null | grep -oE '[A-Z][A-Z0-9]+-[0-9]+' | head -1)
  fi
fi
[ -z "$key" ] && exit 0

printf '%s: %s' "$key" "$(cat "$1")" > "$1.jiraflow" && mv "$1.jiraflow" "$1"
echo "jira_flow: the subject names $key"
"""


def write_once(path: Path, text: str, dry_run: bool) -> None:
    # Our own files are rewritten -- they carry the tool's name -- so a moved or
    # updated jira_flow never leaves a shim pointing at the old path. Something
    # that is not ours is left alone.
    existed = path.exists()
    if existed and "jira_flow" not in path.read_text():
        print("kept (not ours): {}".format(path))
        return
    if existed and path.read_text() == text:
        print("kept (already right): {}".format(path))
        return
    if dry_run:
        print("{}: {}".format("would update" if existed else "would write", path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" med flit: Windows textläge skriver \r\n, och en sh-hook med CRLF
    # dör på "\r: command not found" i Git for Windows' bash.
    with path.open("w", newline="\n") as fh:
        fh.write(text)
    if path.name == "prepare-commit-msg":
        path.chmod(0o755)
    print("{}: {}".format("updated" if existed else "wrote", path))


def cmd_login(args) -> int:
    """Store the token in the machine's own store. Read from stdin, never as an
    argument: arguments end up in shell history and in process lists."""
    token = sys.stdin.read().strip()
    if not token:
        print("jira_flow: no token on stdin.", file=sys.stderr)
        return 2

    if getattr(args, "file", False):
        # Maskinen utan skrivbord. Där finns ingen nyckelring att vara trogen, så
        # 0600-filen är butiken — samma fil som bryggan nu läser som sista utväg.
        data = load_flow_config()
        site = os.environ.get("JIRA_SITE") or data.get("site") or ""
        email = os.environ.get("JIRA_EMAIL") or data.get("email") or ""
        if not (site and email):
            print("jira_flow: sätt JIRA_SITE och JIRA_EMAIL (eller skriv dem i {}) "
                  "först.".format(CONFIG_FILE), file=sys.stderr)
            return 2
        data.update({"site": site, "email": email, "token": token})
        save_flow_config(data)
        print("token stored in {} (0600).".format(CONFIG_FILE))
        return 0

    import jira_secrets

    store = jira_secrets.store_for()
    if isinstance(store, jira_secrets.NoStore):
        print("jira_flow: " + _no_store_reason(), file=sys.stderr)
        return 2
    try:
        store.write(token)
    except RuntimeError as exc:
        print("jira_flow: {}".format(exc), file=sys.stderr)
        return 2
    print("token stored in {} ({}).".format(type(store).__name__, jira_secrets.SECRET_FILE
                                            if isinstance(store, jira_secrets.WindowsStore)
                                            else "the login keychain"))
    return 0


def cmd_logout(args) -> int:
    import jira_secrets

    try:
        gone = jira_secrets.store_for().delete()
    except RuntimeError as exc:
        print("jira_flow: {}".format(exc), file=sys.stderr)
        return 2
    print("token removed." if gone else "nothing stored here.")
    return 0


def _no_store_reason() -> str:
    """Sagt en gång, på det system där det gäller."""
    return ("på Linux sköts token av bryggans nyckelring (`jira_bridge.py login`) "
            "eller av {} (chmod 600) — på en maskin utan skrivbord: "
            "`jira_flow.py login --file` (token på stdin)"
            .format(Path.home() / ".config/jira-flow/config.json"))


def cmd_install(args) -> int:
    repo = Path(args.repo or ".").resolve()
    if not (repo / ".git").is_dir():
        print("jira_flow: {} is not a git repository.".format(repo), file=sys.stderr)
        return 2

    print("editor shims in {}".format(repo))
    # Windows har inget python3 och IntelliJ har ingen per-OS-variant av ett externt
    # verktyg, så där läggs en startfil i repot som verktyget pekar på i stället.
    windows = os.name == "nt"
    write_once(repo / ".vscode/tasks.json", VSCODE_TASK.replace("{self}", str(SELF)), args.dry_run)
    if windows:
        write_once(repo / "jira-flow.cmd", LAUNCHER, args.dry_run)
    write_once(repo / ".idea/tools/jira-flow.xml",
               IDEA_TOOL.replace("{command}", str(repo / "jira-flow.cmd") if windows else "python3")
                        .replace("{params}", "next" if windows else "{} next".format(SELF)),
               args.dry_run)
    write_once(repo / ".agents/rules/jira.md",
               AGY_RULE.replace("{self}", str(SELF)).replace("{project}", args.project), args.dry_run)
    write_once(repo / ".git/hooks/prepare-commit-msg", HOOK.replace("{self}", str(SELF)), args.dry_run)

    if not args.dry_run:
        print("\nVS Code / Antigravity IDE: Run Task -> 'Jira: take next critical'")
        print("IntelliJ: Tools -> External Tools -> 'Jira: take next critical' (key: Settings -> Keymap)")
    return 0


# -------------------------------------------------------------- self-check

def selftest() -> int:
    checks = 0
    # Flödeskartan: rutnätet (sex kolumner), banorna, och engelskan som fallback.
    lanes = ["steg"] * 3 + ["vackt"] * 2 + ["rapport"]
    cols = flowmap_columns(lanes)
    assert cols and cols == sorted(cols) and max(cols) <= FLOWMAP_COL_MAX, cols
    assert len(set(zip(lanes, cols))) == len(lanes), "två noder i samma bana och kolumn krockar"
    assert flowmap_columns(["steg"] * 7) == [], "en kedja längre än rutnätet får vara"
    assert flowmap_text("Stegen", "en") == "The steps"
    assert flowmap_text("Stegen", "de") == "Die Schritte"
    assert flowmap_text("Stegen", "sv") == "Stegen", "svenska är källraden"
    assert flowmap_text("Stegen", "tlh") == "The steps", "okänt språk får engelska"
    assert flowmap_text("bara svensk rad", "en") == "bara svensk rad", "rad utan översättning får källraden"
    assert all(flowmap_locale(tag) == tag for tag in FLOWMAP_CHROME), "ramen: varje språk med katalog"
    assert flowmap_locale("sv") == "sv" and flowmap_locale("fi") == "en", "ramen: vår katalog, annars engelska"
    # Minnet: en tur skrivs, allt äldre än horisonten faller bort när filen läses, och
    # prompten bär det som är kvar. Filen är JSONL i state-katalogen -- ingen databas för
    # en veckas chatt.
    global MEMORY_FILE, CONFIG_FILE
    scratch = Path(tempfile.mkdtemp(prefix="jira-flow-memory-"))
    kept_memory, kept_config = MEMORY_FILE, CONFIG_FILE
    MEMORY_FILE, CONFIG_FILE = scratch / "memory.jsonl", scratch / "config.json"
    try:
        now = time.time()
        memory_remember({"at": now - 30 * 86400, "project": "SCRUM", "question": "gammal", "answer": "gammalt"})
        memory_remember({"at": now - 3600, "project": "SCRUM", "question": "färsk", "answer": "färskt"})
        memory_remember({"at": now - 3600, "project": "WEB", "question": "annat", "answer": "annat"})
        assert [r["question"] for r in memory_recent("SCRUM")] == ["färsk"], memory_recent("SCRUM")
        assert memory_recent("WEB")[0]["question"] == "annat", "minnet är per projekt"
        assert "gammal" not in MEMORY_FILE.read_text(), \
            "en tur äldre än horisonten skall vara borta ur filen, inte bara ur svaret"
        assert oct(MEMORY_FILE.stat().st_mode & 0o777) == "0o600", "minnet skrivs 0600"
        kept_lines, left = memory_lines(memory_recent(""))
        assert len(kept_lines) == 2 and left == 0, (kept_lines, left)
        asked = chat_prompt("SCRUM", "", "vad sa du nyss?", memory=memory_recent("SCRUM"))
        assert "What you answered earlier" in asked and "färsk" in asked, asked
        assert "annat" not in asked, "en annan projekttur hör inte hit"

        # Strömmen: en låtsas-agent som skriver NDJSON. Provet mäter kanalen -- att
        # raderna kommer ut en och en och att svaret läses ur dem -- utan en modell.
        fake = scratch / "fake-agent.py"
        fake.write_text(
            "import json, sys\n"
            "sys.stdin.read()\n"
            "print(json.dumps({'type': 'text', 'text': 'hal'}), flush=True)\n"
            "print(json.dumps({'type': 'tool_use', 'name': 'jira_status'}), flush=True)\n"
            "print(json.dumps({'type': 'result', 'text': 'halvt'}), flush=True)\n",
            encoding="utf-8")
        CONFIG_FILE.write_text(json.dumps({"agents": ["{} {}".format(sys.executable, fake)]}),
                               encoding="utf-8")
        seen = []
        streamed = ask_agent_stream("x", {}, seen.append)
        assert streamed == "halvt", streamed
        assert [e.get("type") for e in seen] == ["text", "tool_use", "result"], seen
        assert agent_argv("")[0][0] == sys.executable, agent_argv("")[0]
    finally:
        MEMORY_FILE, CONFIG_FILE = kept_memory, kept_config
        shutil.rmtree(scratch, ignore_errors=True)
    checks += 1

    # Agentens svar: strömmen ger texten, dekoren runt den kastas. Provet är på
    # formen, inte på en verklig körning -- den kostar minuter och en modell.
    stream = ("{\"type\": \"system\", \"subtype\": \"init\", \"session_id\": \"abc\"}\n"
              "{\"type\": \"text\", \"text\": \"KL\"}\n"
              "{\"type\": \"text\", \"text\": \"ART\"}\n"
              "{\"type\": \"result\", \"text\": \"KLART\", \"exit_code\": 0}\n"
              "\nsession_id: abc\n")
    assert read_agent_reply(stream) == "KLART", read_agent_reply(stream)
    assert read_agent_reply("{\"type\": \"text\", \"text\": \"bara bitar\"}\n") == "bara bitar"
    assert read_agent_reply("rent svar\nutan dekor\n") == "rent svar\nutan dekor"
    assert read_agent_reply("") == "", "inget svar är tomt, inte dekorerat"
    assert agents_from({"agents": ["hermes chat --query-file -"]}) == [AGENT_CHAIN[0]], \
        "den gamla skeppade raden uppgraderas"
    assert agents_from({"agents": ["hermes chat --query-file - --max-turns 3"]}) == \
        ["hermes chat --query-file - --max-turns 3"], "en egen rad lämnas i fred"
    checks += 1

    asked = chat_prompt("SCRUM", "/tmp/klon", "varför står det så?",
                        [{"role": "user", "text": "hej"}], [{"what": "flödet", "why": "inget svar"}],
                        {"counts": {"ärenden": 131}, "hubs": [{"package": "x.model", "usedBy": 142}]})
    assert "SCRUM" in asked and "varför står det så?" in asked, asked
    assert "Developer: hej" in asked, "samtalet följer med"
    assert "flödet: inget svar" in asked, "panelens egna fel följer med"
    assert "model (142)" in asked, "grafen följer med"
    assert "locked to it" in asked, "gränsen står i prompten"
    big = chat_prompt("SCRUM", "", "x" * 40000)
    assert len(big) <= CHAT_CHARS + 80 and "context cut here" in big, len(big)
    checks += 1

    # Kunskapsgrafen: paket, filer och ärenden i en bild. Byggd ur en syntetisk scan och
    # ett minimalt repo, så provet mäter formen och inte dagens data -- den växer.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src" / "app" / "store").mkdir(parents=True)
        (root / "src" / "app" / "A.java").write_text(
            "package app;\nimport app.store.B;\nclass A {}\n", encoding="utf-8")
        (root / "src" / "app" / "store" / "B.java").write_text(
            "package app.store;\nclass B {}\n", encoding="utf-8")
        drawn_graph = graph_build(root, "X", {
            "map": [{"key": "X-1", "summary": "s", "status": "To Do", "pool": "aktiv",
                     "files": ["src/app/A.java"]}],
            "silent": ["src/app/store/B.java"],
            "missing": [{"key": "X-2", "summary": "t", "pool": "aktiv"}]})
    ids = {e["id"] for e in drawn_graph["entities"]}
    kinds = {r["kind"] for r in drawn_graph["relations"]}
    assert {"paket:app", "paket:app.store", "fil:src/app/A.java", "ärende:X-1"} <= ids, \
        "grafen: paket, fil och ärende är noder"
    assert {"ligger-i", "använder", "nämner"} <= kinds, "grafen: de tre slagen av kanter"
    assert drawn_graph["counts"] == {"paket": 2, "filer": 2, "ärenden": 2, "relationer": 4}, \
        "grafen: räknar sina noder"
    assert any(e["id"] == "ärende:X-2" and e.get("unclear") for e in drawn_graph["entities"]), \
        "grafen: ärenden utan fil märks"
    assert drawn_graph["hubs"] and drawn_graph["hubs"][0]["package"] == "app.store", \
        "grafen: navet är det mest använda"
    checks += 1
    assert "legend.title" in FLOWMAP_CHROME["sv"], "ramens nycklar är Archifys egna"
    checks += 1
    jql = pick_jql("SCRUM")
    assert "project = SCRUM" in jql and "assignee IS EMPTY" in jql and "priority DESC" in jql, jql
    assert "In Progress" in current_jql("SCRUM")
    assert "assignee IS EMPTY" in pick_jql("SCRUM", "mine")
    assert "assignee IS EMPTY" not in pick_jql("SCRUM", "any"), "the take-over pool is wider"
    checks += 1
    class _FakeBridge:
        """Svarar som Jira gör: aktörens namn står på aktören, inte inuti actorUser
        (den läsningen gav tomma namn i den skarpa körningen)."""

        def get(self, path: str):
            if path.endswith("/role"):
                return {"Administrators": "https://x/rest/api/3/project/SCRUM/role/10002"}
            if "/role/" in path:
                return {"actors": [{"displayName": "Anna Test", "type": "atlassian-user-role-actor",
                                    "actorUser": {"accountId": "acc-1"}},
                                   {"name": "Developers", "type": "atlassian-group-role-actor",
                                    "actorGroup": {"groupId": "grp-7"}}]}
            if path.startswith("/rest/api/3/field"):
                return [{"id": "summary", "name": "Summary"},
                        {"id": "customfield_10016", "name": "Story Points", "custom": True,
                         "schema": {"type": "number"}}]
            return {}

    roles = admin_roles(_FakeBridge(), "SCRUM")
    assert roles[0]["role"] == "Administrators", roles
    assert roles[0]["actors"][0]["name"] == "Anna Test", roles      # inte tomt
    assert [a["kind"] for a in roles[0]["actors"]] == ["person", "grupp"], roles
    fields = admin_fields(_FakeBridge())
    assert fields[0]["custom"] and fields[0]["name"] == "Story Points", fields  # egna först
    checks += 1
    shared = {"key": "S-1", "fields": {"summary": "GEMENSAMT: testa flödet"}}
    mine_high = {"key": "S-2", "fields": {"summary": "A1 bokningen"}}
    mine_low = {"key": "S-3", "fields": {"summary": "A2 tabellen"}}
    order = [i["key"] for i in shared_first([mine_high, mine_low, shared])]
    assert order == ["S-1", "S-2", "S-3"], order          # gemensam först
    assert [i["key"] for i in shared_first([mine_low, mine_high])] == ["S-3", "S-2"], \
        "ordningen inom gruppen är frågans, inte sorterarens"   # stabil
    checks += 1
    assert key_in("feature/SCRUM-147-booking-list") == "SCRUM-147", key_in("feature/SCRUM-147-booking-list")
    assert key_in("no key here") == "", "a keyless string must give an empty key"
    assert key_in("fix AB-1 thing") == "AB-1"
    checks += 1
    assert prefix_subject("fix the list", "SCRUM-147") == "SCRUM-147: fix the list"
    assert prefix_subject("SCRUM-147: fix", "SCRUM-147") == "SCRUM-147: fix", "no double key"
    assert prefix_subject("", "SCRUM-147") == "" and prefix_subject("fix", "") == "fix"
    assert prefix_subject("\n\nfix the list\n", "SCRUM-147") == "SCRUM-147: fix the list\n", "leading blanks"
    checks += 1
    assert LAUNCHER.startswith("@echo off\r\n") and LAUNCHER.count("%~dp0") == 2, "cmd-filen"
    assert "&&" not in LAUNCHER, "en &&-kedja hade kört skriptet två gånger vid felkod"
    windows_idea = (IDEA_TOOL.replace("{command}", "C:/repo/jira-flow.cmd").replace("{params}", "next"))
    unix_idea = IDEA_TOOL.replace("{command}", "python3").replace("{params}", "/x/y.py next")
    assert "python3" not in windows_idea and "/x/y.py next" in unix_idea, "två system, två verktyg"
    checks += 1
    for template in (VSCODE_TASK, IDEA_TOOL, AGY_RULE, HOOK):
        filled = (template.replace("{self}", "/x/y.py").replace("{project}", "SCRUM")
                  .replace("{command}", "python3").replace("{params}", "/x/y.py next"))
        assert "{self}" not in filled and "{project}" not in filled, "template placeholder unfilled"
    json.loads(VSCODE_TASK.replace("{self}", "/x/y.py"))  # the task template stays valid JSON
    assert '\r' not in HOOK, "en hook med CRLF dör i Git for Windows' bash"
    assert "command -v" in HOOK, "hooken ska slå upp sin tolk (py på Windows, python3 annars)"
    assert '"windows"' in VSCODE_TASK, "VS Code-tasken behöver py på Windows"
    checks += 1
    # plan: agentens svar tolkas helt eller inte alls, och inget skrivs utan --create.
    fenced = "Här är förslagen:\n```json\n[{\"summary\": \"Boka tid\", \"type\": \"Story\", "\
             "\"description\": \"kunden kan boka\", \"priority\": \"High\"}, "\
             "{\"summary\": \"Bekräfta bokning\", \"type\": \"Task\"}]\n```\nHör av dig!"
    items = parse_plan(fenced)
    assert len(items) == 2 and items[0]["summary"] == "Boka tid", items
    assert items[1]["type"] == "Task" and items[0]["priority"] == "High"
    checks += 1
    raw_newline = '[{"summary": "Boka tid", "description": "rad ett\nrad två", "type": "Task"}]'
    assert parse_plan(raw_newline)[0]["description"] == "rad ett\nrad två", "literalt radbryt"
    two_arrays = 'Ett exempel: [{"summary": "A", "type": "Task"}]\nOch svaret:\n' \
                 '[{"summary": "B", "type": "Task"}]'
    assert [i["summary"] for i in parse_plan(two_arrays)] == ["B"], \
        "sista listan, för svaret kommer sist"
    transcript = ("Query: dela upp [önskemålet] i ärenden\n"
                  "  ┊ 🔌 Godjira · jira board  0.1s [tool_call takes exactly one entry]\n"
                  "  ┊ grep  \"summary\": \"[^\"]*\"\n"
                  "\u256d\u2500\u2500\n[\n  {\"summary\": \"Skicka bokningsbekräftelse\", "
                  "\"type\": \"Task\", \"description\": \"VAD: mejla kunden.\"},\n"
                  "  {\"summary\": \"Visa besked utan e-post\", \"type\": \"Task\"}\n]\n"
                  "\u2570\u2500\u2500\nResume this session with: hermes --resume 20260929_090612\n")
    transcript_items = parse_plan(transcript)
    assert [i["summary"] for i in transcript_items] == ["Skicka bokningsbekräftelse",
                                                        "Visa besked utan e-post"], transcript_items
    assert transcript_items[0]["description"] == "VAD: mejla kunden."
    # Mätt 2026-09-29: en agent svarade med strängar i listan och flödet dog på
    # item.get. Nu ska varje post som inte är ett objekt namnges i ett besked —
    # aldrig en krasch, och aldrig ett ärende som heter "result".
    for rubbish in ('[42]', '[["summary", "Boka"]]', '["Flytta bokning mellan bilar"]',
                    '[{"summary": "Boka"}, "result"]'):
        try:
            parse_plan(rubbish)
            raise AssertionError("skräp i listan skulle ha vägrats: " + rubbish)
        except ValueError as exc:
            assert "issue objects" in str(exc), exc
    try:
        parse_plan('[{"summary": "<short imperative>", "type": "Story"}]')
        raise AssertionError("platshållartext skulle ha vägrats")
    except ValueError as exc:
        assert "echoed" in str(exc), exc
    # En epic som agenten pekade på men inte listade läggs till i stället för att kasta
    # svaret, och orden sägs högt. En riktig epic (förkortad) träffas av förledet.
    varn = []
    lagade = parse_plan('[{"summary": "Servicepaket", "type": "Epic", "epic": "", "sprint": 1},'
                        ' {"summary": "Bokningen bär flera tjänster", "type": "Story",'
                        '  "epic": "Servicepak", "sprint": 1},'
                        ' {"summary": "Nytt underlag", "type": "Story", "epic": "Fakturering",'
                        '  "sprint": 2}]', varn)
    # 1 epic + 2 uppgifter från agenten, och den saknade epiken tillagd först
    assert len(lagade) == 4 and "Fakturering" in [i["summary"] for i in lagade if is_epic(i)], lagade
    assert lagade[0]["epic"] == "" and lagade[0]["type"] == "Epic", "epiken först i listan"
    assert [i for i in lagade if i["summary"] == "Bokningen bär flera tjänster"][0]["epic"] == \
        "Servicepaket", "en förkortning träffar sin epic"
    assert varn and "Fakturering" in varn[0], varn
    # Två tänkbara rötter är ingen träff: då gissar vi inte, utan lägger till den nya
    varn2 = []
    två = parse_plan('[{"summary": "Servicepaket för bilar", "type": "Epic", "epic": "", "sprint": 1},'
                     ' {"summary": "Servicepaket för båtar", "type": "Epic", "epic": "", "sprint": 1},'
                     ' {"summary": "Uppgift", "type": "Story", "epic": "Servicepaket", "sprint": 1}]', varn2)
    assert len([i for i in två if is_epic(i)]) == 3, "en tvetydig referens blir en egen epic, inte en gissning"

    # Markören för klippt underlag: siffrorna med, och tom när inget klipptes. Ett kravdokument
    # på 7999 tecken tappade sina sista 1999 med den gamla budgeten, och "klippt" utan siffror
    # sade inte om det saknades en rad eller en tredjedel.
    ordbok = PLAN_WORDS["sv"]
    assert avklippt([], ordbok) == "", "inget klippt underlag ger ingen markör"
    assert avklippt([{"chars": 8000, "documentChars": 8000}], ordbok) == "", "helt underlag tiger"
    assert avklippt([{"chars": 6000, "documentChars": 7999, "truncated": True}], ordbok) == \
        " (klippt: 6000 av 7999 tecken)", avklippt([{"chars": 6000, "documentChars": 7999, "truncated": True}], ordbok)
    assert avklippt([{"chars": 100, "documentChars": 200, "truncated": True},
                     {"chars": 50, "documentChars": 150, "truncated": True}], ordbok) == \
        " (klippt: 150 av 350 tecken)", "två klippta dokument summeras"
    assert DOC_CHARS >= 20000 and TOTAL_CHARS >= DOC_CHARS, "budgetarna skall rymma ett kravdokument"

    # Ett svar på 16 ärenden är ett riktigt svar, inte ett tak-fel: en pdf gav precis det,
    # och hela förslaget kastades. Taket skall bara fånga en agent som spårar ur.
    riktigt = "[" + ",".join('{"summary": "uppgift %d"}' % i for i in range(16)) + "]"
    assert len(parse_plan(riktigt)) == 16, "sexton ärenden skall gå igenom"
    assert PLAN_MAX >= 200, "taket skall ligga over en rimlig uppdelning"
    for bad, why in (("inga ärenden här, bara prat", "utan array"),
                     ('{"summary": "inte en lista"}', "objekt i stället för lista"),
                     ("[{\"type\": \"Task\"}]", "utan summary"),
                     ("[]", "tom lista"),
                     ("[" + ",".join('{"summary": "x"}' for _ in range(PLAN_MAX + 1)) + "]",
                      "över taket")):
        try:
            parse_plan(bad)
            raise AssertionError("skulle ha vägrat: " + why)
        except ValueError:
            pass
    checks += 1
    prompt = PLAN_PROMPT.format(project="SCRUM", limit=PLAN_MAX, sprints=PLAN_SPRINTS,
                                types="Story, Task", context="", wish="kunden vill boka")
    assert "SCRUM" in prompt and "kunden vill boka" in prompt, "prompten bär projekt och önskemål"
    for filler in ("{project}", "{wish}", "{limit}", "{context}", "{types}", "{sprints}"):
        assert filler not in prompt, "ofylld platshållare: " + filler
    checks += 1

    # Scrum-formen: en epic med sina uppgifter under sig -- och inget halvt träd.
    tree = ('[{"summary": "Bokning i butik", "type": "Epic", "description": "VAD: boka"}, '
            '{"summary": "Boka tid", "type": "Story", "epic": "Bokning i butik"}, '
            '{"summary": "Bekräfta", "type": "Task", "epic": "Bokning i butik"}]')
    items = parse_plan(tree)
    assert is_epic(items[0]) and items[0]["epic"] == "", items[0]
    assert [i["epic"] for i in items[1:]] == ["Bokning i butik"] * 2, items
    assert is_epic({"type": "EPIC"}) and not is_epic({"type": "episkt"}), "typen avgör, inte ordet"
    try:
        parse_plan('[{"summary": "Boka", "type": "Task", "epic": "Ingen sådan epic"}]')
        raise AssertionError("en uppgift under en epic som inte finns skulle ha vägrats")
    except ValueError as exc:
        assert "not in the list" in str(exc), exc
    too_many = "[" + ",".join('{{"summary": "E{}", "type": "Epic"}}'.format(n)
                              for n in range(PLAN_EPICS + 1)) + "]"
    try:
        parse_plan(too_many)
        raise AssertionError("fler epics än taket skulle ha vägrats")
    except ValueError as exc:
        assert "epics" in str(exc), exc
    checks += 1

    # Fördelningen: sprintnumret ur agentens rad, grupperingen, och pappret som ritas ur
    # den. Papperet är en ren funktion -- provet rör ingen webbläsare och ingen fil.
    planned = parse_plan('[{"summary": "Boka tid", "type": "Story", "sprint": 2}, '
                         '{"summary": "Bekräfta", "type": "Task"}, '
                         '{"summary": "Rensa", "type": "Task", "sprint": "sprint 2"}]')
    assert [i["sprint"] for i in planned] == [2, 1, 2], planned
    groups = plan_sprints(planned)
    assert [g["sprint"] for g in groups] == [1, 2], groups
    assert [len(g["items"]) for g in groups] == [1, 2], groups
    page = plan_document(planned, {"wish": "kunden vill boka", "project": "SCRUM",
                                   "repo": "shop", "agent": "hermes", "date": "2026-10-05",
                                   "documents": [{"path": "krav.pdf", "chars": 1200}]}, "sv")
    assert "Fördelningen av uppgifterna" in page, page[:120]
    assert "Sprint 1" in page and "Sprint 2" in page and "Boka tid" in page, page
    body = page.split("</style>", 1)[1]
    assert "{" not in body and "}" not in body, "ofylld platshållare i pappret"
    assert "How the work is divided" in plan_document(planned, {}, "en"), "engelska ramen"
    assert "&lt;script&gt;" in plan_document(
        [{"summary": "<script>alert(1)</script>", "type": "Task", "sprint": 1,
          "description": "a & b"}], {}, "sv"), "texten ur underlaget escapas"
    checks += 1

    # Kodkontexten: filkartan ur git, filen närmast önskemålet i sin helhet, och
    # det som klipps sagt högt. Ett anslag skall mötas av koden, inte av loggen.
    code_root = Path(os.environ.get("TMPDIR") or "/tmp") / "jira-flow-code-selftest"
    shutil.rmtree(code_root, ignore_errors=True)
    (code_root / "shop").mkdir(parents=True)
    (code_root / "shop" / "booking.py").write_text("def book(car):\n    return car\n" * 4)
    (code_root / "shop" / "invoice.py").write_text("def bill(car):\n    return 0\n" * 4)
    (code_root / "ledger.py").write_text("# invoice: här skrivs fakturan\ndef total():\n    pass\n")
    (code_root / "huge.py").write_text("x = 1\n" * 20000)
    (code_root / ".gitignore").write_text("secret.py\n")
    (code_root / "secret.py").write_text("TOKEN = 'hemligt'\n")
    for argv in (("init", "-q"), ("add", "-A")):
        subprocess.run(["git", "-C", str(code_root)] + list(argv), capture_output=True, check=True)
    assert code_words("the and booking") == {"booking"}, code_words("the and booking")
    text, info = code_context(code_root, code_words("the customer wants invoice handling"))
    assert "shop/booking.py  (8 lines)" in text, "kartan bär filerna med radantal"
    assert "def book(car)" in text, "filen närmast önskemålet läses i sin helhet"
    assert "TOKEN" not in text, "en ignorerad fil följer inte med (git vet vad som är med)"
    assert info["files"] == 5, info
    assert "ledger.py" in info["mentions"] and any(p.endswith("ledger.py") for p in info["picked"]), \
        "filens text pekar ut den, även när namnet inget säger: " + str(info)
    assert info["truncated"] and "contracts only" in text, \
        "det som klipps skall sägas, annars ser svaret fullständigt ut: " + str(info)
    checks += 1

    # Länken repo <-> Jira: namnet ur fjärren, uppslagningen, och vems projekt som vinner.
    assert repo_slug("https://github.com/alexwest1981/GodJIRA.git") == "alexwest1981/GodJIRA"
    assert repo_slug("git@github.com:alexwest1981/HellCrawlers.git") == "alexwest1981/HellCrawlers"
    assert repo_slug("alexwest1981/GodJIRA/") == "alexwest1981/GodJIRA"
    assert repo_slug("GodJIRA") == "GodJIRA" and repo_slug("") == ""
    checks += 1
    global LINKS_FILE, project_caps, client
    # Kopplingen läser rollen ur Jiras svar (Alex' regel: ingen Jira-koppling utan
    # påkopplad API). Provet styr det svaret i stället för att gå ut på nätet, och
    # prövar längre ner att ett uteblivet svar nekar kopplingen.
    project_caps = lambda bridge, projekt: {
        "account": "Test", "role": "Developer", "roleNote": "",
        "can": ["CREATE_ISSUES", "TRANSITION_ISSUES"]}
    kept_file = LINKS_FILE
    # Ingen tempfile här: funktionen importerar tempfile längre ner, och ett namn som
    # binds senare är lokal i hela kroppen (mätt: UnboundLocalError).
    scratch = Path(os.environ.get("TMPDIR") or "/tmp") / "jira-flow-links-selftest"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    LINKS_FILE = scratch / "links.json"
    try:
        import contextlib
        quiet = contextlib.redirect_stdout(io.StringIO())
        assert link_for("GodJIRA") == {}, "ett tomt register ger ingen länk"
        with quiet:
            code = cmd_link(argparse.Namespace(action="set", repo="GodJIRA", project="scrum",
                                              issue="scrum-133", note="hubben", json=True))
        assert code == 0, code
        assert (LINKS_FILE.stat().st_mode & 0o777) == 0o600, "länkregistret ska vara 0600"
        assert link_for("GodJIRA")["project"] == "SCRUM", "naket namn hittar owner/name"
        assert link_for("godjira")["issue"] == "SCRUM-133", "skiftläget spelar ingen roll"
        assert link_for("annat-repo") == {}, "ett oreponterat namn ger ingenting"
        checks += 1
        # --project vinner, sedan länken (--repo eller --repo-name), sedan standarden.
        assert link_project(argparse.Namespace(project="", repo="", repo_name="GodJIRA")) == \
            ("SCRUM", "länken (GodJIRA)"), "länken ger projektet"
        assert link_project(argparse.Namespace(project="OTHER", repo="", repo_name="GodJIRA"))[0] == "OTHER", \
            "flaggan vinner över länken"
        assert link_project(argparse.Namespace(project="", repo="", repo_name="okänt"))[0] == DEFAULT_PROJECT
        # Ett enda länkat repo är "projektet man är kopplad till" när inget namnges.
        assert link_project(argparse.Namespace(project=""))[0] == "SCRUM", "den enda länken ger projektet"
        assert link_project(argparse.Namespace(project="", repo=""))[1].startswith("länken"), "och säger varifrån"
        with quiet:
            cmd_link(argparse.Namespace(action="set", repo="annat-repo", project="OTHER", issue="",
                                        note="", json=True))
        assert link_project(argparse.Namespace(project=""))[0] == DEFAULT_PROJECT, \
            "två länkar utan namn är tvetydigt: standarden"
        with quiet:
            cmd_link(argparse.Namespace(action="rm", repo="annat-repo", project="", issue="",
                                        note="", json=True))
        assert plan_repo_dir(argparse.Namespace(repo="/tmp/nagonstans", repo_name="GodJIRA")) == "/tmp/nagonstans", \
            "--repo går före den lokala kopian"
        assert plan_repo_dir(argparse.Namespace(repo="", repo_name="")) == "", "utan repo ingen katalog"
        checks += 1
        # Ett oreponterat avbrott är ett svar, inte en krasch: 1 betyder "fanns inte".
        with quiet:
            assert cmd_link(argparse.Namespace(action="rm", repo="aldrig-lankad", project="",
                                               issue="", note="", json=True)) == 1
            assert cmd_link(argparse.Namespace(action="set", repo="HellCrawlers", project="hel",
                                               issue="", note="", json=True)) == 0
        assert link_for("HellCrawlers")["project"] == "HEL", "projektnyckeln skrivs i versaler"
        with quiet:
            assert cmd_link(argparse.Namespace(action="rm", repo="hellcrawlers", project="",
                                               issue="", note="", json=True)) == 0
        assert link_for("HellCrawlers") == {}, "borttagningen tar med skiftläget"
        assert json.loads(LINKS_FILE.read_text())["GodJIRA"]["issue"] == "SCRUM-133", "resten står kvar"

        def utan_api(bridge, projekt):
            raise RuntimeError("ingen API-token i nyckelringen")

        # Kontrollen gäller en riktig anslutning: i provläget sätts länken utan roll,
        # med flit. Den måste därför köra mot en klient som säger "real" i stället för
        # att låna maskinens egen config -- annars blir svaret olika på olika maskiner,
        # och den som just klonat repot får en röd rad utan att något är fel.
        class RealClient:
            cfg = {"mode": "real"}

        verklig_client, verklig_caps = client, project_caps
        client = lambda: RealClient()          # noqa: E731 -- ett stubb, inte en regel
        project_caps = utan_api
        try:
            with quiet:
                assert cmd_link(argparse.Namespace(action="set", repo="utan-api", project="SCRUM",
                                                   issue="", note="", json=True)) == 1, \
                    "utan API nekas kopplingen"
        finally:
            client, project_caps = verklig_client, verklig_caps
        assert link_for("utan-api") == {}, "och ingen länk skrivs av en halv koppling"
        checks += 1
    finally:
        LINKS_FILE = kept_file
        shutil.rmtree(scratch, ignore_errors=True)

    # Underlaget: dokument läses, klipps med besked, och skräp nekas.
    import http.server
    import threading      # tempfile importeras högst upp: en lokal import här gjorde
                          # namnet lokalt i hela selftest(), och allt ovanför kraschade
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "krav.txt").write_text("Krav: kunden ska kunna spara.\n")
        with zipfile.ZipFile(base / "krav.docx", "w") as z:
            z.writestr("word/document.xml",
                       "<w:document><w:body><w:p><w:t>Krav: knappen ska spara kunden"
                       "</w:t></w:p><w:p><w:t>Rad två</w:t></w:p></w:body></w:document>")
        (base / "stor.txt").write_text("x" * (DOC_CHARS + 500))
        (base / "bild.bin").write_bytes(bytes(range(256)) * 60)
        (base / "anteckningar").mkdir()
        (base / "anteckningar" / "mote.txt").write_text("Möte: vi fryser priset i oktober.")
        (base / "anteckningar" / "trasig.bin").write_bytes(bytes(range(256)) * 60)

        docs, notes = read_context([str(base / "krav.txt"), str(base / "krav.docx"),
                                   str(base / "stor.txt"), str(base / "anteckningar")])
        by_name = {note["path"].split("/")[-1]: note for note in notes}
        assert "Krav: kunden ska kunna spara." in docs, "textfilen lästes"
        assert "knappen ska spara kunden" in docs and "Rad två" in docs, "docx-texten lästes"
        assert by_name["stor.txt"]["truncated"] and by_name["stor.txt"]["chars"] == DOC_CHARS
        assert "characters shown]" in docs, "klippet syns i texten"
        assert by_name["mote.txt"]["chars"] > 0, "filen i mappen lästes"
        assert by_name["trasig.bin"].get("error"), "skräp i en mapp hoppas över med besked"
        assert sum(n["chars"] for n in notes) <= TOTAL_CHARS + 100
        for needed in ("krav.pdf", "nonsense.png", "finns-inte.txt"):
            target = base / needed
            if needed != "finns-inte.txt" and not target.exists():
                if needed.endswith(".pdf"):
                    target.write_bytes(b"%PDF-1.4 inte en riktig pdf")
                else:
                    target.write_bytes(bytes(range(256)) * 60)
            try:
                read_context([str(target)])
                raise AssertionError("skulle ha vägrat: " + needed)
            except (RuntimeError, OSError):
                pass
        # En länk läses på samma villkor som en fil: text blir text, html tappar
        # taggarna och skriptet, och en 404 är ett fel — inte ett tomt underlag.
        served = {"/krav.txt": ("text/plain", "K1. Bokningen ska kunna flyttas."),
                  "/sida.html": ("text/html", "<html><head><title>Krav</title>"
                                              "<style>p{color:red}</style></head><body>"
                                              "<p>K2. Prislistan ska frysas.</p>"
                                              "<script>var x=1;</script></body></html>")}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hit = served.get(self.path)
                if not hit:
                    self.send_error(404)
                    return
                raw = hit[1].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", hit[0])
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *ignored):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host = "http://127.0.0.1:{}".format(server.server_address[1])
        try:
            from_url, url_notes = read_context([host + "/krav.txt", host + "/sida.html"])
            assert "K1. Bokningen" in from_url and "K2. Prislistan" in from_url, from_url[:200]
            assert "<p>" not in from_url and "var x=1" not in from_url, "html är rensad"
            assert "color:red" not in from_url, "stilen är borta"
            assert [n["path"] for n in url_notes] == [host + "/krav.txt", host + "/sida.html"]
            try:
                read_context([host + "/finns-inte"])
                raise AssertionError("en 404 ska vara ett fel")
            except RuntimeError as exc:
                assert "404" in str(exc), str(exc)
        finally:
            server.shutdown()
            server.server_close()
        assert context_items("file:///tmp/krav.pdf")[0][0] == Path("/tmp/krav.pdf"), \
            "panelens släpp ger file://-adresser"

        # Filväljaren: avbruten dialog är inget fel, två val blir två rader.
        if os.name == "posix":
            with tempfile.TemporaryDirectory() as bin_dir:
                stub = Path(bin_dir) / "zenity"
                saved_path = os.environ.get("PATH", "")
                os.environ["PATH"] = bin_dir
                try:
                    stub.write_text("#!/bin/sh\nexit 1\n")
                    stub.chmod(0o755)
                    assert pick_files("prova") == [], "avbruten dialog ger inget val"
                    stub.write_text("#!/bin/sh\necho /tmp/a.pdf\necho /tmp/b.txt\n")
                    stub.chmod(0o755)
                    assert pick_files("prova") == ["/tmp/a.pdf", "/tmp/b.txt"], "valen läses radvis"
                    stub.write_text("#!/bin/sh\necho 'Failed to open display' >&2\nexit 1\n")
                    stub.chmod(0o755)
                    try:
                        pick_files("prova")
                        raise AssertionError("ingen bildskärm ska vara ett fel, inte ett tomt val")
                    except RuntimeError as exc:
                        assert "display" in str(exc), str(exc)
                finally:
                    os.environ["PATH"] = saved_path
            try:
                os.environ["PATH"] = ""
                pick_files("prova")
                raise AssertionError("utan zenity ska det vara ett fel, inte tystnad")
            except RuntimeError as exc:
                assert "zenity" in str(exc), str(exc)
            finally:
                os.environ["PATH"] = saved_path

        assert build_context("", "", notes) == "", "inget underlag ger inget block"
        block = build_context(docs, "", notes)
        assert "knappen ska spara kunden" in block, "blocket bär underlaget"
        filled = PLAN_PROMPT.format(project="S", limit=1, sprints=2, types="T", context=block, wish="w")
        assert "knappen ska spara kunden" in filled, "underlaget hamnar i prompten"
        assert "Context for the project as it stands" in filled, filled[:200]
    checks += 1

    # Historiken: en riktig klon i scratch, och ett tydligt nej för allt annat.
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "prov-repo"
        repo.mkdir()
        env = dict(os.environ, GIT_AUTHOR_NAME="prov", GIT_AUTHOR_EMAIL="prov@local",
                   GIT_COMMITTER_NAME="prov", GIT_COMMITTER_EMAIL="prov@local")
        def git(*argv):
            return subprocess.run(["git", "-C", str(repo)] + list(argv), env=env,
                                  capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        (repo / "a.txt").write_text("hej")
        git("add", "a.txt")
        git("commit", "-q", "-m", "SCRUM-101 bygg knappen som sparar")
        text, info = repo_context(str(repo))
        assert info["branch"] == "main" and info["commits"] == 1, info
        assert "SCRUM-101 bygg knappen som sparar" in text, text
        assert "uncommitted files: 0" in text, text
        assert repo_context(str(Path(tmp) / "finns-inte"))[1].get("error"), "saknad mapp"
        assert repo_context(str(base / "krav.txt"))[1].get("error"), "en fil är inte ett repo"
        (Path(tmp) / "inte-repo").mkdir()
        probe = Path(tmp) / "inte-repo"
        if git_out(probe, "rev-parse", "--is-inside-work-tree"):
            # En temp-mapp kan ligga inuti ett annat repo (scratch-katalogen gör det
            # här): då läser git historiken uppåt, vilket är gits egen regel.
            print("note: the temp dir sits inside a work tree; no-repo case not exercised")
        else:
            assert repo_context(str(probe))[1].get("error"), "mapp utanför repo"
    checks += 1

    class FakeClient:
        def __init__(self):
            self.written = []
        def board(self, project):
            return {"id": "7", "projectKey": project}
        def types(self, project):
            return ["Story", "Task"]
        def create(self, board, item):
            self.written.append(item)
            return {"key": "SCRUM-{}".format(900 + len(self.written))}
        def log(self, *a, **k):
            pass

    fake = FakeClient()
    plan_argv = argparse.Namespace(file="", text="", create=False, json=True, project="SCRUM")
    # Byt den globala agenten och önskemålet: ett självprov får aldrig starta en
    # riktig agent, och aldrig läsa på en riktig stdin (den kan vara en pipe som
    # aldrig tar slut). Båda anropen går mot samma fejkade agent.
    class Quiet:
        """Tystar utskriften, men behaller det sista som sades.

        Ett fall ska forklara sig sjalvt: kontrollen nedan provar att ett forslag gar
        igenom, och utan den har raden ar felet bara "det gick inte" -- pa en maskin
        ingen kan undersoka.
        """

        def __init__(self):
            self.said = []

        def write(self, text, *a):
            if str(text).strip():
                self.said.append(str(text).strip())
            return len(str(text))

        def flush(self):
            return None

    original_agent, original_argv, original_stdin, original_stdout = \
        ask_agent, agent_argv, sys.stdin, sys.stdout
    try:
        globals()["ask_agent"] = lambda prompt: '[{"summary": "Boka tid", "type": "Story"}]'
        # agent_argv letar efter en installerad agent och kastar om ingen finns. Den har
        # kontrollen galler sjalva plan-vagen, inte vad som rakar vara installerat pa
        # maskinen -- annars ar provet gront pa en utvecklarmaskin och rott hos alla andra.
        globals()["agent_argv"] = lambda prompt: ["selftest-agent", prompt]
        sys.stdin = type("S", (), {"isatty": lambda self: False, "read": lambda self: "kunden vill boka"})()
        sys.stdout = quiet_out = Quiet()

        plan_argv.text = "kunden vill boka"
        assert cmd_plan(fake, plan_argv) == 0, (
            "förslaget ska gå igenom utan att skriva"
            + (" -- sade: " + quiet_out.said[-1] if quiet_out.said else ""))
        assert fake.written == [], "utan --create får ingenting skrivas"
        assert fake.written == [], "utan --create får ingenting skrivas"

        plan_argv.create = True
        captured = io.StringIO()
        sys.stdout = captured
        try:
            assert cmd_plan(fake, plan_argv) == 0, "med --create ska listan skrivas"
        finally:
            sys.stdout = Quiet()
        assert len(fake.written) == 1 and fake.written[0]["summary"] == "Boka tid", fake.written
        documents = json.loads(captured.getvalue())   # ett dokument, inte två
        assert documents["created"] and documents["proposal"], documents

        # En oväntad krasch mitt i skrivandet ska rapportera vad som redan skrivits
        # (mätt: ett fel i nyckelutläsningen skapade SCRUM-175 och dog tyst om det).
        class Cranky(FakeClient):
            def create(self, board, item):
                if self.written:
                    raise ValueError("boom")
                return FakeClient.create(self, board, item)

        globals()["ask_agent"] = lambda prompt: '[{"summary": "A"}, {"summary": "B"}]'
        cranky = Cranky()
        assert cmd_plan(cranky, plan_argv) == 2, "en krasch ska ge fel, inte tyst halv tavla"
        assert len(cranky.written) == 1, "och ska ha skrivit precis ett ärende"

        # Sprintarna: fönstren räknas ur startdagen, och skrivningen gör en sprint per
        # nummer och lägger ärendena i den. En sprint som redan står på tavlan används i
        # stället för att en andra "Sprint 1" skapas.
        assert sprint_windows("2026-01-05", 2, 2) == [("2026-01-05", "2026-01-18"),
                                                     ("2026-01-19", "2026-02-01")], \
            sprint_windows("2026-01-05", 2, 2)
        assert sprint_windows("2026-01-05", 1, 1) == [("2026-01-05", "2026-01-11")], "en vecka"

        class SprintClient(FakeClient):
            def __init__(self):
                FakeClient.__init__(self)
                self.made, self.added = [], []

            def board(self, project):
                row = FakeClient.board(self, project)
                row["sprints"] = [{"id": "31", "name": "Sprint 1"}]
                return row

            def sprint_create(self, board_id, name, start, end):
                self.made.append((board_id, name, start, end))
                return {"id": str(40 + len(self.made)), "name": name,
                        "startDate": start, "endDate": end}

            def sprint_add(self, sprint_id, keys):
                self.added.append((sprint_id, list(keys)))

        globals()["ask_agent"] = lambda prompt: ('[{"summary": "A", "sprint": 1}, '
                                                 '{"summary": "B", "sprint": 2}, '
                                                 '{"summary": "C", "sprint": 2}]')
        sprinty = SprintClient()
        sprint_argv = argparse.Namespace(file="", text="kunden vill boka", create=True, json=True,
                                         project="SCRUM", sprints=True, sprint_weeks=2,
                                         sprint_start="2026-01-05")
        captured = io.StringIO()
        sys.stdout = captured
        try:
            assert cmd_plan(sprinty, sprint_argv) == 0, "sprintarna skrivs efter ärendena"
        finally:
            sys.stdout = Quiet()
        out = json.loads(captured.getvalue())
        assert [row["name"] for row in out["sprints"]] == ["Sprint 1", "Sprint 2"], out["sprints"]
        assert out["sprints"][0]["id"] == "31", "sprinten som fanns återanvänds: " + repr(out["sprints"])
        assert sprinty.made == [("7", "Sprint 2", "2026-01-19", "2026-02-01")], sprinty.made
        assert sprinty.added == [("31", ["SCRUM-901"]), ("41", ["SCRUM-902", "SCRUM-903"])], \
            sprinty.added
        assert out["sprintError"] == "" and out["sprints"][0]["keys"] == ["SCRUM-901"], out
        checks += 1

        # Den godkända listan: panelen visar ett förslag, människan bockar av, och
        # exakt den listan skrivs -- agenten ska inte tillfrågas en andra gång, för
        # den svarar olika varje gång och då är det som godkändes inte det som skrivs.
        scratch = Path(tempfile.mkdtemp(prefix="jira-flow-selftest-"))
        approved = scratch / "approved.json"
        approved.write_text(json.dumps([{"summary": "Godkänd A", "type": "Story"},
                                       {"summary": "Godkänd B", "type": "Task"},
                                       {"summary": "Ej godkänd C", "type": "Task"}]), encoding="utf-8")
        approved_argv = argparse.Namespace(file="", text="", create=False, json=True,
                                           project="SCRUM", proposal=str(approved))

        def no_agent(prompt):
            raise AssertionError("med --proposal ska agenten inte frågas")

        globals()["ask_agent"] = no_agent
        fake2 = FakeClient()
        assert cmd_plan(fake2, approved_argv) == 0, "en godkänd lista ska gå igenom utan att skriva"
        assert fake2.written == [], "utan --create får ingenting skrivas"
        approved_argv.create = True
        captured = io.StringIO()
        sys.stdout = captured
        try:
            assert cmd_plan(fake2, approved_argv) == 0, "med --create ska listan skrivas"
        finally:
            sys.stdout = Quiet()
        assert [w["summary"] for w in fake2.written] == ["Godkänd A", "Godkänd B", "Ej godkänd C"], \
            "exakt den godkända listan, i den ordningen: " + repr(fake2.written)

        # En fil någon har redigerat i är inte mer pålitlig än en modell: halv lista
        # blir noll ärenden, och ingenting skrivs.
        broken = scratch / "broken.json"
        broken.write_text('[{"type": "Story"}]', encoding="utf-8")
        broken_argv = argparse.Namespace(file="", text="", create=True, json=True,
                                         project="SCRUM", proposal=str(broken))
        fake3 = FakeClient()
        assert cmd_plan(fake3, broken_argv) == 2, "en halv lista ska ge fel"
        assert fake3.written == [], "och ingenting ska skrivas"
        missing_argv = argparse.Namespace(file="", text="", create=True, json=True,
                                          project="SCRUM", proposal=str(scratch / "finns-inte.json"))
        assert cmd_plan(FakeClient(), missing_argv) == 2, "en fil som inte finns ska ge fel"
    finally:
        globals()["ask_agent"], globals()["agent_argv"], sys.stdin, sys.stdout = \
            original_agent, original_argv, original_stdin, original_stdout
    checks += 2
    # Listan är användarens: egen lista vinner, miljövariabeln vinner över allt,
    # och en tom lista faller tillbaka på den skeppade i stället för på ingenting.
    assert agents_from({}, "") == list(AGENT_CHAIN), "utan egen lista gäller den skeppade"
    assert agents_from({"agents": ["x -p {prompt}"]}, "") == ["x -p {prompt}"]
    assert agents_from({"agents": []}, "") == list(AGENT_CHAIN), "tom lista = skeppad"
    assert agents_from({"agents": ["x"]}, "y") == ["y"], "miljövariabeln går före"
    checks += 1

    # add/set/rm mot en config i scratch: den riktiga får aldrig röras av ett prov.
    with tempfile.TemporaryDirectory() as tmp:
        saved_file = CONFIG_FILE
        try:
            globals()["CONFIG_FILE"] = Path(tmp) / "config.json"
            # Ett självprov skriver en rad: både stdout och stderr tystas, annars ser
            # en förväntad vägran ut som ett fel i en grön körning.
            quiet_stdout, quiet_stderr = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = Quiet()
            try:
                assert cmd_agent(argparse.Namespace(action="add", commands=["codex exec"])) == 0
                assert load_flow_config()["agents"] == list(AGENT_CHAIN) + ["codex exec"]
                assert cmd_agent(argparse.Namespace(action="rm", commands=["1"])) == 0
                assert load_flow_config()["agents"] == list(AGENT_CHAIN)[1:] + ["codex exec"]
                assert cmd_agent(argparse.Namespace(action="set", commands=["agy -p {prompt}"])) == 0
                assert load_flow_config()["agents"] == ["agy -p {prompt}"]
                assert cmd_agent(argparse.Namespace(action="rm", commands=["agy"])) == 2, "tomt får inte gå"
            finally:
                sys.stdout, sys.stderr = quiet_stdout, quiet_stderr
            assert load_flow_config()["agents"] == ["agy -p {prompt}"], "inget skrevs vid vägran"
            if os.name != "nt":
                assert (Path(tmp) / "config.json").stat().st_mode & 0o777 == 0o600, "0600"
        finally:
            globals()["CONFIG_FILE"] = saved_file
    checks += 1

    # Kommandotolken: Hermes läser förfrågan på stdin, agy vill ha den som argument,
    # och en okänd CLI ger fel i stället för tyst ingenting. Båda formerna prövas med
    # sys.executable, så provet inte hänger på vad som är installerat på maskinen.
    saved_agent = AGENT
    try:
        assert AGENT_CHAIN[0].startswith("hermes ") and AGENT_CHAIN[1].startswith("agy "), AGENT_CHAIN
        globals()["AGENT"] = sys.executable
        argv, on_stdin = agent_argv("hej")
        assert argv == [sys.executable] and on_stdin == "hej", "utan {prompt} går texten på stdin"
        globals()["AGENT"] = "{} {{prompt}}".format(sys.executable)
        argv, on_stdin = agent_argv("hej")
        assert argv == [sys.executable, "hej"] and on_stdin == "", "med {prompt} blir den ett argument"
        globals()["AGENT"] = "no-such-agent-3f9c"
        try:
            agent_argv("hej")
            raise AssertionError("okänd agent skulle ha vägrat")
        except RuntimeError:
            pass
    finally:
        globals()["AGENT"] = saved_agent
    checks += 1

    # Kodkartan: paketen som noder, importerna som vägar. Provet bygger ett litet repo
    # där ui använder service som använder model -- en väg skall alltid gå åt höger, och
    # paketet ingen pekar på skall ligga först.
    tmp = tempfile.mkdtemp(prefix="godjira-codemap-")
    try:
        root = Path(tmp)
        # Klassnamnen måste vara de importen pekar på: uppslaget går på det enkla namnet.
        for package, cls, target in (("ui", "Ui", "Service"),
                                     ("service", "Service", "Model"),
                                     ("model", "Model", "")):
            folder = root / package
            folder.mkdir()
            line = "import com.x.{0}.{1};".format(target.lower(), target) if target else ""
            (folder / (cls + ".java")).write_text(
                "package com.x.{};\n{}\nclass {} {{}}\n".format(package, line, cls))
        graph = code_map(root)
        layers = {node["key"]: node["x"] for node in graph["nodes"]}
        assert graph["packages"] == 3 and graph["edgesCount"] == 2, graph
        assert layers["com.x.ui"] < layers["com.x.service"] < layers["com.x.model"], layers
        assert [node["name"] for node in graph["nodes"] if node["x"] == min(layers.values())] == ["ui"]
        assert {edge["from"] for edge in graph["edges"]} == {"com.x.ui", "com.x.service"}
        empty = code_map(Path(tmp) / "finns-inte")
        assert empty["nodes"] == [] and "Java" in empty["note"], "utan java-filer sägs det"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    checks += 1

    # Referensläsningen: ett språk importerar inte alltid. Ett nämnt typnamn är en väg även
    # utan import, men bara med sin egen versal -- ett gemener-ord som sammanfaller med ett
    # filnamn är ingen väg (mätt i sonix: ordet "app" band ihop två moduler med 143 vägar).
    tmp = tempfile.mkdtemp(prefix="godjira-referens-")
    try:
        root = Path(tmp)
        (root / "core").mkdir()
        (root / "ui").mkdir()
        (root / "core" / "World.gd").write_text("class_name World\n")
        (root / "ui" / "Screen.gd").write_text("extends World\nvar w = World.new()\n")
        (root / "ui" / "Other.gd").write_text("var w = world.new()\n")
        graph = code_map(root)
        pairs = {(edge["from"], edge["to"]) for edge in graph["edges"]}
        assert ("ui", "core") in pairs, graph["edges"]
        assert graph["edgesCount"] == 1, graph["edges"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    checks += 1

    # Kartan: hela listan med, inte ett tyst tak. Kapades den (60 av 74) såg de visade
    # ut som alla -- samma klass av fel som att tiga om vad som klippts. Provet har fler
    # filer än det gamla taket, så en återinförd kapning syns direkt.
    tmp = tempfile.mkdtemp(prefix="godjira-scan-")
    try:
        root = Path(tmp)
        (root / "BookingRepository.java").write_text("class BookingRepository {}\n")
        (root / "gui").mkdir()
        (root / "gui" / "View.java").write_text("class View {}\n")
        for extra in range(70):
            (root / "filler{:02d}.txt".format(extra)).write_text("ingenting\n")
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True, capture_output=True)

        class _ScanBridge:
            def board(self, project):
                return {"issues": [{"key": "S-1", "summary": "Booking repository",
                                    "description": ""}], "backlog": []}

        m = scan_map(_ScanBridge(), "SCRUM", root)
        assert m["silentCount"] == len(m["silent"]), \
            "kartan sade {} filer men visade {}".format(m["silentCount"], len(m["silent"]))
        assert m["silentCount"] > 60, "provet skall ha fler filer än det gamla taket"
        assert m["issuesTotal"] == m["issues"] == 1, "totalen följer med"
        assert m["mapped"] == 1, "ärendet pekar på repository-filen"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    checks += 1

    # Filhanteringen: en sökväg ur en förfrågan är en GRÄNS, och commitnoterna läses i ett
    # anrop -- en flerradig commit-text får inte klyvas av tomluckan före filnamnen.
    tmp = tempfile.mkdtemp(prefix="godjira-files-")
    ickegit = tempfile.mkdtemp(prefix="godjira-ickegit-")
    try:
        root = Path(tmp)
        (root / "gui").mkdir()
        (root / "gui" / "View.java").write_text("class View {}\n")
        (root / "hemlig.txt").write_text("inte repots\n")

        def git(*argv):
            return subprocess.run(["git"] + list(argv), cwd=tmp, check=True, capture_output=True)

        git("init", "-q")
        git("config", "user.email", "prov@example.invalid")
        git("config", "user.name", "Prov")
        git("add", "-A")
        git("commit", "-q", "-m", "första: lägg in vyn")
        (root / "gui" / "View.java").write_text("class View { int x; }\n")
        git("add", "-A")
        git("commit", "-q", "-m", "andra: fält i vyn\n\nEn rad till.\n\nOch en tredje.")

        assert safe_repo_path(root, "gui/View.java")[0] is not None
        assert safe_repo_path(root, "../hemlig.txt")[0] is None, "en ..-väg skall nekas"
        assert safe_repo_path(root, "/etc/passwd")[0] is None, "en absolut väg skall nekas"
        assert safe_repo_path(root, "")[0] is None, "tom sökväg skall nekas"
        assert safe_repo_path(root, "gui")[0] is None, "en katalog är ingen fil"
        assert "gui/View.java" in repo_files(root), repo_files(root)

        log = repo_commit_log(root, 5)
        assert len(log) == 2, log
        assert log[0]["subject"] == "andra: fält i vyn", log[0]
        assert "Och en tredje." in log[0]["body"], log[0]
        assert log[0]["files"] == ["gui/View.java"] and log[0]["filesTotal"] == 1, log[0]
        assert log[1]["subject"] == "första: lägg in vyn", log[1]
        assert len(repo_commit_log(root, 5, "hemlig.txt")) == 1, "historiken är per fil"

        # Ändringen: git skriver den, vi färglägger den. Ett sha ur en förfrågan är hex, för
        # git läser ett inledande bindestreck som en FLAGGA, inte som en commit.
        ändring = repo_diff(root, log[0]["sha"])
        assert "-class View {}" in ändring["text"], ändring["text"][:200]
        assert "+class View { int x; }" in ändring["text"], ändring["text"][:200]
        assert ändring["truncated"] is False, ändring
        bara = repo_diff(root, log[0]["sha"], "gui/View.java")
        assert 0 < bara["lines"] <= ändring["lines"], (bara["lines"], ändring["lines"])
        assert safe_sha("--upload-pack=evil") is None, "en flagga är inte en commit"
        assert safe_sha("HEAD~1") is None and safe_sha("") is None, "bara hex"
        assert safe_sha(log[0]["sha"]) == log[0]["sha"], "ett riktigt sha skall släppas igenom"

        ickegit_path = Path(ickegit)
        (ickegit_path / "node_modules").mkdir()
        (ickegit_path / "node_modules" / "skrap.js").write_text("x")
        (ickegit_path / "Kod.java").write_text("x")
        assert repo_files(ickegit_path) == ["Kod.java"], repo_files(ickegit_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(ickegit, ignore_errors=True)
    checks += 1

    # ---- körningen (`work`): grennamnet, katalogen, commitarna, diffen, uppdraget,
    # kvittot och liggaren. Det som går att prova utan en agent provas här.
    assert work_branch("scrum-42") == "godjira/SCRUM-42", work_branch("scrum-42")
    assert work_branch("X-1", "feature/min-gren") == "feature/min-gren", "ett eget namn går före"
    assert work_branch("X-1", "  min-gren  ") == "min-gren", "blanksteg runt namnet putsas bort"
    for dåligt in ("-flagga", "har mellanslag", "punkt..punkt", "kolon:", "fråga?", "stjärna*",
                   "klam[", "tilde~", "hatt^", "@{", "slut/", "back" + chr(92) + "slash",
                   "ta" + chr(9) + "b", "rad" + chr(10) + "brytning"):
        try:
            work_branch("X-1", dåligt)
        except ValueError:
            continue
        raise AssertionError("grennamnet {!r} skulle ha nekats".format(dåligt))
    checks += 1
    assert work_dir(" scrum-7 ") == RUNS_DIR / "SCRUM-7", work_dir(" scrum-7 ")
    checks += 1
    assert parse_commits("") == [] and parse_commits("\n  \n") == [], "tomt är tomt"
    rader = parse_commits("a1b2c3 först\nd4e5f6 SCRUM-7: fixa saken\n")
    assert len(rader) == 2 and rader[1]["sha"] == "d4e5f6"
    assert rader[1]["subject"] == "SCRUM-7: fixa saken", rader
    assert parse_shortstat("3 files changed, 42 insertions(+), 7 deletions(-)") == {
        "files": 3, "added": 42, "removed": 7}
    assert parse_shortstat("1 file changed, 2 insertions(+)") == {"files": 1, "added": 2, "removed": 0}
    assert parse_shortstat("") == {"files": 0, "added": 0, "removed": 0}
    checks += 1
    uppdrag = work_prompt(
        "SCRUM-42",
        {"summary": "Boka avstämning", "description": "Gör X i Y", "status": "To Do",
         "assignee": "Alex", "comments": [{"author": "Kim", "body": "tänk på Z"}]},
        ["src/Bokning.java"], "SCRUM", "godjira/SCRUM-42", "/tmp/SCRUM-42")
    for bit in ("SCRUM-42", "Boka avstämning", "Gör X i Y", "Kim", "src/Bokning.java",
                "godjira/SCRUM-42", "/tmp/SCRUM-42", "never `git add -A`", "do NOT push",
                "locked to project SCRUM"):
        assert bit in uppdrag, (bit, uppdrag)
    utan = work_prompt("SCRUM-1", {"summary": "s", "description": "", "status": "", "assignee": "",
                                   "comments": []}, [], "SCRUM", "godjira/SCRUM-1", "/tmp/x")
    assert "hint and not a rule" not in utan, "utan ledtrådar skall uppdraget inte låtsas ha några"
    assert "do NOT push" in utan, "gränsen gäller även det tomma uppdraget"
    checks += 1
    kvitto = work_receipt("SCRUM-42", "godjira/SCRUM-42", [{"sha": "abc123", "subject": "SCRUM-42: fix"}],
                          {"files": 2, "added": 10, "removed": 3}, "https://github.com/x/y/pull/1",
                          "hermes", "en anteckning")
    for bit in ("SCRUM-42", "godjira/SCRUM-42", "abc123", "pull/1", "hermes",
                "2 files changed, +10 -3", "en anteckning"):
        assert bit in kvitto, (bit, kvitto)
    tomt = work_receipt("SCRUM-1", "b", [], {"files": 0}, "", "", "")
    assert "No commits" in tomt and "Diff:" not in tomt, tomt
    # Kommentaren: kroppen skall vara ADF (API v3 avvisar en vanlig sträng), en stycke-nod
    # per rad. Fångas genom att låna request-vägen -- ingenting skickas i väg här.
    fångat = {}
    verklig = Http("exempel.atlassian.net", "a@b.c", "nyckel")
    verklig.request = lambda method, path, body=None: fångat.update(
        {"method": method, "path": path, "body": body}) or {}
    verklig.comment("SCRUM-42", "GodJIRA worked SCRUM-42\n\nBranch: godjira/SCRUM-42")
    assert fångat["method"] == "POST" and fångat["path"].endswith("/issue/SCRUM-42/comment"), fångat
    doc = fångat["body"]["body"]
    assert doc["type"] == "doc" and doc["version"] == 1, doc
    assert all(n["type"] == "paragraph" for n in doc["content"]), doc
    rader = [n["content"][0]["text"] for n in doc["content"]]
    assert rader == ["GodJIRA worked SCRUM-42", "", "Branch: godjira/SCRUM-42"], rader
    checks += 1
    global RUNS_FILE          # utan raden blir namnet lokalt i hela funktionen (fällan)
    kept_runs = RUNS_FILE
    RUNS_FILE = Path(scratch) / "runs.jsonl"
    try:
        run_remember({"key": "SCRUM-1", "ok": True, "commits": 1})
        run_remember({"key": "SCRUM-2", "ok": False, "commits": 0})
        sedan = runs_recent(10)
        assert [r["key"] for r in sedan] == ["SCRUM-2", "SCRUM-1"], "nyast först"
        assert sedan[0]["at"], "tiden sätts när raden skrivs"
        assert RUNS_FILE.stat().st_mode & 0o777 == 0o600, "liggaren är 0600"
        RUNS_FILE.write_text(RUNS_FILE.read_text() + "skräp\n", encoding="utf-8")
        assert len(runs_recent(10)) == 2, "en trasig rad hoppas över, den fäller inte läsningen"
        RUNS_FILE.unlink()
        assert runs_recent(10) == [], "en liggare som inte finns är tom, inte ett fel"
    finally:
        RUNS_FILE = kept_runs
    checks += 1

    print("jira_flow self-check: {} checks, 0 failed".format(checks))
    return 0


# ------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jira_flow", description=__doc__.split("\n")[0])
    parser.add_argument("--selftest", action="store_true", help="offline checks of the pure logic")
    sub = parser.add_subparsers(dest="cmd")
    p_next = sub.add_parser("next", help="take the most critical item and start it")
    p_cur = sub.add_parser("current", help="the key you are on right now")
    p_pick = sub.add_parser("pick", help="the file dialog on this machine, one path per line")
    p_pick.add_argument("--title", default="", help="what the dialog asks for")
    p_pick.add_argument("--json", action="store_true")
    p_ins = sub.add_parser("install", help="write the editor shims and the commit hook into a repo")
    p_login = sub.add_parser("login", help="store the Jira token in this machine's own store (reads stdin)")
    p_login.add_argument("--file", action="store_true",
                         help="store in the 0600 config file instead of an OS store (a machine without a desktop)")
    sub.add_parser("logout", help="remove it again")
    for p in (p_next, p_cur):
        p.add_argument("--project", default="",
                       help="override the project; default comes from the repo's link")
    p_next.add_argument("--status", default=DEFAULT_STATUS)
    p_next.add_argument("--dry-run", action="store_true")
    p_next.add_argument("--json", action="store_true", help="machine-readable result (for an agent)")
    p_next.add_argument("--expect", metavar="KEY", default="",
                        help="take exactly KEY, which a human confirmed (the second press)")
    p_work = sub.add_parser("work", help="do one issue: worktree, agent, branch, PR")
    p_work.add_argument("key", help="the issue key, e.g. SCRUM-42")
    p_work.add_argument("--repo", default="", help="a local clone to work in")
    p_work.add_argument("--repo-name", default="", help="a repo by name (owner/name)")
    p_work.add_argument("--project", default="", help="override the project; default comes from the link")
    p_work.add_argument("--branch", default="", help="the branch to work on (default godjira/<KEY>)")
    p_work.add_argument("--agent", default="", help="the agent for this run (default: your own list)")
    p_work.add_argument("--dry-run", action="store_true", help="show the assignment, write nothing")
    p_work.add_argument("--no-push", action="store_true", help="keep the branch on this machine")
    p_work.add_argument("--no-comment", action="store_true", help="write nothing on the issue")
    p_work.add_argument("--rm", action="store_true", help="remove the worktree after a pushed run")
    p_work.add_argument("--json", action="store_true", help="machine-readable result")
    p_runs = sub.add_parser("runs", help="the run ledger: what the runs did")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.add_argument("--json", action="store_true")
    p_chat = sub.add_parser("chat", help="ask your agent about the linked project (one turn)")
    p_chat.add_argument("question", nargs="?", help="the question; empty reads stdin")
    p_chat.add_argument("--repo", default="", help="a local clone to ask about")
    p_chat.add_argument("--repo-name", default="", help="a repo by name (owner/name)")
    p_chat.add_argument("--project", default="", help="the Jira project to lock the agent to")
    p_chat.add_argument("--history", metavar="FILE", default="",
                        help="the conversation so far: JSON [{role, text}, ...]")
    p_chat.add_argument("--context", metavar="FILE", default="",
                        help="what the panel cannot answer: JSON [{what, why}, ...]")
    p_chat.add_argument("--dry-run", action="store_true", help="print what the agent would be asked")
    p_chat.add_argument("--stream", action="store_true",
                        help="print the agent's events as they arrive (NDJSON, one per line)")
    p_chat.add_argument("--json", action="store_true", help="machine-readable result")
    p_memory = sub.add_parser("memory", help="what the agent remembers, and for how long")
    p_memory.add_argument("--project", default="", help="only this project's turns")
    p_memory.add_argument("--days", type=int, default=MEMORY_DAYS, help="the horizon in days")
    p_memory.add_argument("--clear", action="store_true", help="empty the memory file")
    p_memory.add_argument("--json", action="store_true", help="machine-readable")
    p_agent = sub.add_parser("agent", help="your own agent list: list, add, set, rm")
    p_agent.add_argument("action", nargs="?", default="list",
                         choices=["list", "add", "set", "rm"])
    p_agent.add_argument("commands", nargs="*")
    p_agent.add_argument("--json", action="store_true", help="machine-readable list")
    p_link = sub.add_parser("link", help="which Jira project and issue a repo belongs to")
    p_link.add_argument("action", nargs="?", default="list", choices=["list", "set", "rm"])
    p_link.add_argument("repo", nargs="?", help="the repo: owner/name, or a name")
    p_link.add_argument("--project", default="", help="the Jira project key")
    p_link.add_argument("--issue", default="", help="the issue you are working against (SCRUM-133)")
    p_link.add_argument("--note", default="", help="a line for the humans")
    p_link.add_argument("--json", action="store_true")
    p_admin = sub.add_parser("admin", help="the admin surface: roles, fields, and people")
    p_admin.add_argument("what", nargs="?", default="all", choices=["all", "roles", "fields", "people"])
    p_admin.add_argument("words", nargs="*", help="the search words when asking for people")
    p_admin.add_argument("--project", default="", help="override the linked project key")
    p_admin.add_argument("--repo-name", default="", help="the linked repo (default: the only link)")
    p_admin.add_argument("--json", action="store_true")
    p_scan = sub.add_parser("scan", help="map the whole Jira project to the repo's code")
    p_scan.add_argument("--repo-name", default="", help="the linked repo (default: the only link)")
    p_scan.add_argument("--repo", default="", help="a directory instead of the local copy")
    p_scan.add_argument("--project", default="", help="override the linked project key")
    p_scan.add_argument("--limit", type=int, default=250, help="how many issues to map")
    p_scan.add_argument("--json", action="store_true")
    p_codemap = sub.add_parser("codemap", help="the packages of a repo and how they depend on each other")
    p_codemap.add_argument("--repo-name", default="", help="the linked repo (default: the only link)")
    p_codemap.add_argument("--repo", default="", help="a directory instead of the local copy")
    p_codemap.add_argument("--json", action="store_true")
    p_graph = sub.add_parser("graph", help="everything we know about a repo, with its relations")
    p_graph.add_argument("--repo", default="", help="a directory instead of the local copy")
    p_graph.add_argument("--repo-name", default="", help="the repo's name, resolved to its local copy")
    p_graph.add_argument("--project", default="", help="Jira key (default: the link)")
    p_graph.add_argument("--json", action="store_true", help="the whole graph as JSON")

    p_diff = sub.add_parser("diff", help="the change one commit made, as git writes it")
    p_diff.add_argument("--repo-name", default="", help="the linked repo (default: the only link)")
    p_diff.add_argument("--repo", default="", help="a directory instead of the local copy")
    p_diff.add_argument("--sha", required=True, help="the commit to show")
    p_diff.add_argument("--path", default="", help="one file out of the commit")
    p_diff.add_argument("--json", action="store_true")
    p_files = sub.add_parser("files", help="the files of a repo, as git knows them")
    p_files.add_argument("--repo-name", default="", help="the linked repo (default: the only link)")
    p_files.add_argument("--repo", default="", help="a directory instead of the local copy")
    p_files.add_argument("--limit", type=int, default=0, help="how many files to return")
    p_files.add_argument("--json", action="store_true")
    p_file = sub.add_parser("file", help="one file out of the repo, read-only")
    p_file.add_argument("--repo-name", default="", help="the linked repo (default: the only link)")
    p_file.add_argument("--repo", default="", help="a directory instead of the local copy")
    p_file.add_argument("--path", required=True, help="the file's path inside the repo")
    p_file.add_argument("--json", action="store_true")
    p_commits = sub.add_parser("commits", help="the commit notes of a repo, or of one file")
    p_commits.add_argument("--repo-name", default="", help="the linked repo (default: the only link)")
    p_commits.add_argument("--repo", default="", help="a directory instead of the local copy")
    p_commits.add_argument("--path", default="", help="one file's history instead of the repo's")
    p_commits.add_argument("--limit", type=int, default=30, help="how many commits")
    p_commits.add_argument("--json", action="store_true")

    p_flowmap = sub.add_parser("flowmap", help="the flow drawn as an artifact (Archify)")
    p_flowmap.add_argument("--workflow", required=True, help="n8n's workflow id")
    p_flowmap.add_argument("--lang", default="", help="the panel's language; missing rows fall back to English")
    p_flowmap.add_argument("--out", default="", help="where the artifact is written (without it, only the IR)")
    p_flowmap.add_argument("--ir", default="", help="write the intermediate JSON here too")
    p_flowmap.add_argument("--json", action="store_true", help="the whole receipt, uncut")
    p_repo = sub.add_parser("repo", help="one repo: what it is, what is open, and its link")
    p_repo.add_argument("name", nargs="?", help="owner/name, or a name")
    p_repo.add_argument("--json", action="store_true")
    p_plan = sub.add_parser("plan", help="customer wish in, issue proposal out")
    p_plan.add_argument("--file", default="", help="read the wish from a file (default: stdin)")
    p_plan.add_argument("--text", default="", help="the wish as one argument (the panel sends it this way)")
    p_plan.add_argument("--create", action="store_true",
                        help="write exactly the proposed list (default: write nothing)")
    p_plan.add_argument("--json", action="store_true", help="machine-readable result")
    p_plan.add_argument("--stream", action="store_true",
                        help="report each step as it happens (NDJSON on stdout)")
    p_plan.add_argument("--project", default="",
                        help="override the project; default comes from the repo's link")
    p_plan.add_argument("--context", action="append", default=[], metavar="PATH",
                        help="papers the wish came with: a file or a folder (repeatable)")
    p_plan.add_argument("--repo", default="", metavar="DIR",
                        help="the project's repo: branch, recent commits, open PRs/issues")
    p_plan.add_argument("--repo-name", default="", metavar="OWNER/NAME",
                        help="a GitHub repo instead of a path: its link picks project, "
                             "issue and local copy")
    p_plan.add_argument("--proposal", default="", metavar="PATH",
                        help="an already-approved list of issues: skips the agent (the panel sends this)")
    p_plan.add_argument("--pdf", default="", metavar="PATH",
                        help="write the division of the issues (sprint by sprint) as a PDF here")
    p_plan.add_argument("--lang", default="", metavar="CODE",
                        help="the language of the PDF's frame: sv, or English for anything else")
    p_plan.add_argument("--sprints", action="store_true",
                        help="with --create: also make one sprint per sprint number and put "
                             "the issues in them (a write to the board)")
    p_plan.add_argument("--team-people", type=int, default=0, metavar="N",
                        help="how many people the plan has to fit (0 = from the config)")
    p_plan.add_argument("--team-roles", default="", metavar="TEXT",
                        help="who they are, e.g. 'one on UI, two on backend'")
    p_plan.add_argument("--sprint-weeks", type=int, default=2, metavar="N",
                        help="how long a sprint is (default 2)")
    p_plan.add_argument("--sprint-start", default="", metavar="YYYY-MM-DD",
                        help="the first sprint's start day (default: today)")
    p_ins.add_argument("repo", nargs="?", help="repository root (default: here)")
    p_ins.add_argument("--project", default=DEFAULT_PROJECT)
    p_ins.add_argument("--dry-run", action="store_true")
    return parser


def main(argv) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.cmd:
        build_parser().print_help()
        return 1
    if args.cmd == "install":
        return cmd_install(args)
    if args.cmd == "login":
        return cmd_login(args)
    if args.cmd == "logout":
        return cmd_logout(args)
    if args.cmd == "chat":
        return cmd_chat(args)
    if args.cmd == "memory":
        return cmd_memory(args)
    if args.cmd == "agent":
        return cmd_agent(args)
    if args.cmd == "pick":
        return cmd_pick(args)
    if args.cmd == "link":
        return cmd_link(args)
    if args.cmd == "repo":
        return cmd_repo(args)
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "graph":
        return cmd_graph(args)
    if args.cmd == "codemap":
        return cmd_codemap(args)
    if args.cmd == "diff":
        return cmd_diff(args)
    if args.cmd == "files":
        return cmd_files(args)
    if args.cmd == "file":
        return cmd_file(args)
    if args.cmd == "runs":
        return cmd_runs(args)
    if args.cmd == "commits":
        return cmd_commits(args)
    if args.cmd == "flowmap":
        return cmd_flowmap(args)
    if args.cmd == "admin":
        return cmd_admin(args)
    # En plats för projektnyckeln: flaggan, annars länken, annars standarden. Nästa,
    # aktuellt och plan går alla genom den -- ingen av dem har en egen uppfattning.
    # `work` lämnar raden i fred: den slår upp projektet själv med repot i hand, och en
    # förifylld flagga fick svaret att säga "flaggan" om ett projekt som kom ur länken.
    if args.cmd in ("next", "current") and not (args.project or "").strip():
        args.project, args.project_source = link_project(args)
    jira = client()
    try:
        if args.cmd == "plan":
            return cmd_plan(jira, args)
        if args.cmd == "work":
            return cmd_work(jira, args)
        return cmd_next(jira, args) if args.cmd == "next" else cmd_current(jira, args)
    except (RuntimeError, OSError, KeyError, urllib.error.URLError) as exc:
        # Allt som går mot Jira går genom här: en trasig token, en tavla som inte
        # svarar eller ett svar i fel form blir ett svar panelen kan visa i stället
        # för en stacktrace -- panelen visar bara första raden, och den är alltid
        # "Traceback (most recent call last):", vilket inte säger någonting.
        message = "{}: {}".format(type(exc).__name__, exc) if str(exc) else type(exc).__name__
        say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
