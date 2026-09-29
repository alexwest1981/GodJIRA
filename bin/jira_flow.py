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
         [--create] [--json] [--project KEY]
        Hands the customer's wish to the agent you have chosen (JIRA_FLOW_AGENT,
        "claude -p" by default -- any CLI that reads a prompt on stdin and answers
        with JSON works) and gets issue proposals back. GodJIRA never calls a model
        itself: no key, no model list, no bill. Nothing is written until --create,
        and then exactly the list you just read.
        --context hands over the papers the wish came with (pdf, docx/odt/xlsx,
        text, or a folder of them) and --repo the project's own history (branch,
        recent commits, open PRs and issues via gh when the remote is GitHub), so
        the issues land where the project actually is instead of beside it.
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
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import tempfile
import urllib.request
import zipfile
from base64 import b64encode
from html import unescape
from datetime import datetime, timezone
from pathlib import Path

SELF = Path(__file__).resolve()
BRIDGE_DIR = Path(
    os.environ.get("JIRA_BRIDGE_DIR", str(Path.home() / ".config/omarchy/plugins/custom.jira/bin"))
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
AGENT_CHAIN = ("hermes chat --query-file -", "agy -p {prompt}")
AGENT_TIMEOUT = int(os.environ.get("JIRA_FLOW_AGENT_TIMEOUT", "600"))
# Ett agent-svar som inte gick att tolka hamnar här (0600). Annars finns ingenting
# kvar att titta på när flödet säger att svaret var obrukbart.
ANSWER_LOG = os.path.expanduser("~/.local/state/omarchy/jira-flow-answer.log")
PLAN_MAX = int(os.environ.get("JIRA_FLOW_PLAN_MAX", "10"))

# Underlaget agenten får utöver själva önskemålet. Taken finns för att en agent
# som får 300 000 tecken slutar läsa och börjar gissa; allt som klipps bort sägs
# det om i prompten, så ett kort svar aldrig ser ut som ett fullständigt underlag.
DOC_CHARS = 6000        # per dokument
TOTAL_CHARS = 20000     # alla dokument tillsammans
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
{context}
Only what the wish actually asks for. Do not invent scope, do not add epics, at most {limit} issues.
Issue types that exist in this project: {types}.

Answer with one JSON array of objects and nothing else -- no prose, no explanation,
no code fences. Every object has exactly these four fields:
  "summary"       a short imperative for this project, at most 80 characters
  "type"          one of the issue types listed above
  "description"   what to build, and how to know it is done
  "priority"      one of: Highest, High, Medium, Low
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
        # i sin plugin-katalog, custom.jira). Sedan kataloger som *heter* något av namnet.
        for hit in [root] + list(root.glob(short)) + list(root.glob("*" + short)) + list(root.glob(short + "*")):
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


def build_context(docs_text: str, repo_text: str, notes, link_text: str = "") -> str:
    """Kontextblocket i prompten. Tomt när inget underlag gavs."""
    if not (docs_text or repo_text or link_text):
        return ""
    parts = ["Context for the project as it stands — read it before you split the wish:",
             "build on what is already there, and let each description say what it rests on."]
    if link_text:
        parts += ["", "The Jira side the repo is tied to:", "", link_text]
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
        if item.get("description"):
            fields["description"] = {"type": "doc", "version": 1, "content": [
                {"type": "paragraph",
                 "content": [{"type": "text", "text": item["description"]}]}]}
        return self.request("POST", "/rest/api/3/issue", {"fields": fields})

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
        answer = self.jb.real_create(self.cfg, board, dict(item, typeName=item.get("type", "Task")))
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


def parse_plan(text: str):
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
                    "priority": str(item.get("priority") or "").strip()})
    if not out:
        raise ValueError("the agent proposed no issues at all")
    if len(out) > PLAN_MAX:
        raise ValueError("the agent proposed {} issues; the cap is {} "
                         "(JIRA_FLOW_PLAN_MAX)".format(len(out), PLAN_MAX))
    return out


def agents_from(config, override: str = ""):
    """Användarens lista, annars den skeppade. Miljövariabeln går före allt."""
    if override.strip():
        return [override.strip()]
    listed = (config or {}).get("agents")
    if isinstance(listed, list):
        commands = [str(c).strip() for c in listed if str(c).strip()]
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


def ask_agent(prompt: str) -> str:
    """Prompten in, svaret ut. Hermes i grunden, sedan Antigravity; JIRA_FLOW_AGENT
    pekar på vilken CLI som helst som pratar stdin/stdout."""
    argv, stdin_text = agent_argv(prompt)
    try:
        done = subprocess.run(argv, input=stdin_text, capture_output=True, text=True,
                              timeout=AGENT_TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError("{} is not installed".format(argv[0]))
    except subprocess.TimeoutExpired:
        raise RuntimeError("{} gave no answer in {}s".format(argv[0], AGENT_TIMEOUT))
    if done.returncode != 0:
        raise RuntimeError("{} exited {}: {}".format(
            argv[0], done.returncode, (done.stderr or "").strip()[:200]))
    return done.stdout


def cmd_agent(args) -> int:
    """Användarens egen lista: visa, lägg till, byt ut, ta bort."""
    config = load_flow_config()
    listed = agents_from(config, "")
    shipped = list(AGENT_CHAIN)
    action = args.action or "list"

    if action == "list":
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

    docs_text, docs_notes, repo_text, repo_info = "", [], "", {}
    if not approved:
        try:
            docs_text, docs_notes = read_context(getattr(args, "context", []) or [])
            if repo_dir:
                repo_text, repo_info = repo_context(repo_dir)
                if repo_info.get("error"):
                    raise RuntimeError("--repo {}: {}".format(repo_dir, repo_info["error"]))
        except (RuntimeError, OSError) as exc:
            message = "the papers could not be read: {}".format(exc)
            say(args, {"ok": False, "error": message}, ["jira_flow: " + message])
            return 2

    board = client_.board(project)
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
        # Agenten får hela underlaget: önskemålet, dokumenten och repot.
        prompt = PLAN_PROMPT.format(project=project, limit=PLAN_MAX,
                                    types=", ".join(client_.types(project)) or "Story, Task, Bug",
                                    context=build_context(docs_text, repo_text, docs_notes,
                                                          link_context(client_, args)),
                                    wish=wish.strip())
        answered_by, answer, items = ["(no agent answered)"], "", []
        try:
            answered_by = agent_argv(prompt)[0]
            answer = ask_agent(prompt)
            items = parse_plan(answer)
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
    if args.json and not args.create:
        # Med --create kommer ett enda dokument, längst ner, med både förslaget och
        # nycklarna: en maskinläsare ska inte behöva tolka två JSON-dokument i rad.
        print(json.dumps({"ok": True, "created": False, "proposal": items,
                          "project": project, "projectSource": project_source, "agent": answered_by,
                          "context": context_note}, ensure_ascii=False))
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
        print("{} issue(s) proposed for {} ({}):".format(len(items), project, answered_by[0]))
        print("  project from: {}".format(project_source))
        for number, item in enumerate(items, 1):
            print("  {}. [{}] {}  ({})".format(number, item["type"], item["summary"],
                                               item["priority"] or "no priority"))
    if not args.create:
        if not args.json:
            print("nothing written. again with --create writes exactly this list.")
        return 0

    created = []
    for item in items:
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
        created.append({"key": key, "summary": item["summary"]})
        if not args.json:
            print("created: {}  {}".format(key or "(no key back)", item["summary"]))
    for entry in created:
        client_.log("flow-plan", entry["key"], entry["summary"][:80])
    say(args, {"ok": True, "created": created, "proposal": items, "project": project,
               "projectSource": project_source,
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
    entry = {"project": project.upper(),
             "issue": (getattr(args, "issue", "") or "").strip().upper(),
             "note": (getattr(args, "note", "") or "").strip(),
             "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    links[name] = {key: value for key, value in entry.items() if value}
    save_links(links)
    say(args, {"ok": True, "link": dict(links[name], repo=name)},
        ["{} -> {}{}{}".format(name, entry["project"],
                               " ({})".format(entry["issue"]) if entry["issue"] else "",
                               " — " + entry["note"] if entry["note"] else "")])
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
    jql = pick_jql("SCRUM")
    assert "project = SCRUM" in jql and "assignee IS EMPTY" in jql and "priority DESC" in jql, jql
    assert "In Progress" in current_jql("SCRUM")
    assert "assignee IS EMPTY" in pick_jql("SCRUM", "mine")
    assert "assignee IS EMPTY" not in pick_jql("SCRUM", "any"), "the take-over pool is wider"
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
    prompt = PLAN_PROMPT.format(project="SCRUM", limit=PLAN_MAX, types="Story, Task",
                                context="", wish="kunden vill boka")
    assert "SCRUM" in prompt and "kunden vill boka" in prompt, "prompten bär projekt och önskemål"
    for filler in ("{project}", "{wish}", "{limit}", "{context}", "{types}"):
        assert filler not in prompt, "ofylld platshållare: " + filler
    checks += 1

    # Länken repo <-> Jira: namnet ur fjärren, uppslagningen, och vems projekt som vinner.
    assert repo_slug("https://github.com/alexwest1981/GodJIRA.git") == "alexwest1981/GodJIRA"
    assert repo_slug("git@github.com:alexwest1981/HellCrawlers.git") == "alexwest1981/HellCrawlers"
    assert repo_slug("alexwest1981/GodJIRA/") == "alexwest1981/GodJIRA"
    assert repo_slug("GodJIRA") == "GodJIRA" and repo_slug("") == ""
    checks += 1
    global LINKS_FILE
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
        checks += 1
    finally:
        LINKS_FILE = kept_file
        shutil.rmtree(scratch, ignore_errors=True)

    # Underlaget: dokument läses, klipps med besked, och skräp nekas.
    import http.server
    import tempfile
    import threading
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
        filled = PLAN_PROMPT.format(project="S", limit=1, types="T", context=block, wish="w")
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
        def write(self, *a):
            return None

        def flush(self):
            return None

    original_agent, original_stdin, original_stdout = ask_agent, sys.stdin, sys.stdout
    try:
        globals()["ask_agent"] = lambda prompt: '[{"summary": "Boka tid", "type": "Story"}]'
        sys.stdin = type("S", (), {"isatty": lambda self: False, "read": lambda self: "kunden vill boka"})()
        sys.stdout = Quiet()

        plan_argv.text = "kunden vill boka"
        assert cmd_plan(fake, plan_argv) == 0, "förslaget ska gå igenom utan att skriva"
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
        globals()["ask_agent"], sys.stdin, sys.stdout = original_agent, original_stdin, original_stdout
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
    p_agent = sub.add_parser("agent", help="your own agent list: list, add, set, rm")
    p_agent.add_argument("action", nargs="?", default="list",
                         choices=["list", "add", "set", "rm"])
    p_agent.add_argument("commands", nargs="*")
    p_link = sub.add_parser("link", help="which Jira project and issue a repo belongs to")
    p_link.add_argument("action", nargs="?", default="list", choices=["list", "set", "rm"])
    p_link.add_argument("repo", nargs="?", help="the repo: owner/name, or a name")
    p_link.add_argument("--project", default="", help="the Jira project key")
    p_link.add_argument("--issue", default="", help="the issue you are working against (SCRUM-133)")
    p_link.add_argument("--note", default="", help="a line for the humans")
    p_link.add_argument("--json", action="store_true")
    p_repo = sub.add_parser("repo", help="one repo: what it is, what is open, and its link")
    p_repo.add_argument("name", nargs="?", help="owner/name, or a name")
    p_repo.add_argument("--json", action="store_true")
    p_plan = sub.add_parser("plan", help="customer wish in, issue proposal out")
    p_plan.add_argument("--file", default="", help="read the wish from a file (default: stdin)")
    p_plan.add_argument("--text", default="", help="the wish as one argument (the panel sends it this way)")
    p_plan.add_argument("--create", action="store_true",
                        help="write exactly the proposed list (default: write nothing)")
    p_plan.add_argument("--json", action="store_true", help="machine-readable result")
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
    if args.cmd == "agent":
        return cmd_agent(args)
    if args.cmd == "pick":
        return cmd_pick(args)
    if args.cmd == "link":
        return cmd_link(args)
    if args.cmd == "repo":
        return cmd_repo(args)
    # En plats för projektnyckeln: flaggan, annars länken, annars standarden. Nästa,
    # aktuellt och plan går alla genom den -- ingen av dem har en egen uppfattning.
    if args.cmd in ("next", "current") and not (args.project or "").strip():
        args.project, args.project_source = link_project(args)
    jira = client()
    try:
        if args.cmd == "plan":
            return cmd_plan(jira, args)
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
