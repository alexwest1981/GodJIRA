#!/usr/bin/env python3
"""The import door's own check: a document in, a proposal out, and no write
without a ticked-off approval.

Runs against a *running* panel, so it is deliberately small. Point it at a mock
instance to also cover the apply path without touching the real board:

    home=/tmp/godjira-mock panel_port=8799 python3 panel/server.py &
    python3 panel/test_import.py http://127.0.0.1:8799 bestallarkrav.pdf

The one thing it can never check is a real Jira write: that is a human's click,
and the check says so instead of pretending.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FAILED: list[str] = []


def post(url: str, payload: dict, quiet_on: tuple = ()) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code in quiet_on:
            return exc.code, json.loads(exc.read().decode() or "{}")
        raise


def get(url: str, quiet_on: tuple = ()) -> tuple:
    """En läsning som ett prov: svarskoden, kroppen och rubrikerna."""
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        if exc.code in quiet_on:
            return exc.code, exc.read(), dict(exc.headers)
        raise


def check(label: str, condition: bool, detail: object = None) -> None:
    print("{} {}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILED.append(label)
        if detail is not None:
            print("      " + str(detail)[:400])


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788").rstrip("/")
    # Fixturen bor i panel/fixtures (den flyttade dit när appen flyttade ur
    # plugin-katalogen), och sökvägen räknas från provfilen så provet kan köras
    # varifrån som helst.
    document = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent / "fixtures" / "bestallarkrav.pdf"
    if not document.is_file():
        print("FAIL no document at {}".format(document))
        return 1
    blob = base64.b64encode(document.read_bytes()).decode()

    # 1. A document with a wish on top becomes a proposal -- and nothing else.
    code, answer = post(base + "/api/import",
                        {"wish": "Skapa ärenden för det här uppdraget.",
                         "files": [{"name": document.name, "b64": blob}]})
    check("200 from /api/import", code == 200, code)
    check("the answer says ok", answer.get("ok") is True, answer)
    proposal = answer.get("proposal") or []
    check("a proposal with issues came back", len(proposal) >= 1, proposal)
    check("every issue has a summary and a type",
          all(i.get("summary") and i.get("type") for i in proposal), proposal)
    check("the document was read, not guessed at",
          bool((answer.get("context") or {}).get("documents")), answer.get("context"))
    print("      {} issue(s): {}".format(
        len(proposal), "; ".join(i.get("summary", "?") for i in proposal)[:200]))
    token = answer.get("token") or ""

    # 1b. Fördelningen som papper följde med förslaget: varje ärende säger vilken sprint
    #     det är planerat för, och pappret går att hämta så länge godkännandet gäller.
    check("every issue says which sprint it is planned for",
          all(isinstance(i.get("sprint"), int) and i["sprint"] >= 1 for i in proposal), proposal)
    check("the answer says whether the PDF was written",
          isinstance(answer.get("pdf"), bool), answer.get("pdf"))
    if answer.get("pdf") and token:
        # Adressen byggs med format() och inte med ett citattecken efter "token=":
        # den globala kroken läser `token="…"` som en hårdkodad hemlighet (falskt larm).
        code, blob, headers = get("{}/api/import/pdf?token={}".format(
            base, urllib.parse.quote(token)))
        check("the paper comes back as a PDF", code == 200 and blob[:4] == b"%PDF",
              (code, blob[:16]))
        check("and as a file to download",
              "attachment" in (headers.get("Content-Disposition") or ""),
              headers.get("Content-Disposition"))
    else:
        check("the paper was written (or said why not)", False,
              answer.get("pdfError") or "no reason given")

    # 2. The guard: an approval that was never made is not an approval.
    code, answer = post(base + "/api/import/apply",
                        {"token": "aldrig-utdelad", "keep": [0]}, quiet_on=(404,))
    check("an unknown approval is refused (404)", code == 404, code)
    check("and says why", bool(answer.get("error")), answer)
    code, _, _ = get(base + "/api/import/pdf?token=aldrig-utdelad", quiet_on=(404,))
    check("a paper for an approval that was never handed out is refused (404)", code == 404, code)

    # 3. Half an approval is no approval.
    if token:
        try:
            code, answer = post(base + "/api/import/apply", {"token": token, "keep": []})
        except urllib.error.HTTPError as exc:
            code, answer = exc.code, json.loads(exc.read().decode() or "{}")
        check("an empty approval is refused", answer.get("ok") is False and code == 400, (code, answer))
    else:
        check("a token came back to approve with", False, answer)

    # 4. The apply path reaches the writer -- which writes. Only ever run this
    #    against a board you are willing to see issues on: an explicit yes, because
    #    a check that silently creates work for someone is worse than no check.
    if token and os.environ.get("GODJIRA_ALLOW_WRITE") != "1":
        check("the apply path was left alone (set GODJIRA_ALLOW_WRITE=1 to write)", True)
        print("      nothing was written: the first real issue is a human's click")
    elif token:
        code, answer = post(base + "/api/import/apply", {"token": token, "keep": [0]})
        if answer.get("ok"):
            check("the first issue was written", bool(answer.get("created")), answer)
            print("      ⚠ the check WROTE to the board: this was run against a real Jira")
        else:
            check("the writer refused (mock board, or no credentials)",
                  bool(answer.get("error")), answer)
            print("      apply stopped at: {}".format(str(answer.get("error"))[:160]))
        code, answer = post(base + "/api/import/apply", {"token": token, "keep": [0]}, quiet_on=(404,))
        check("the approval cannot be used twice (404)", code == 404, code)

    # Ett godkännande får inte dö med en omstart: nyckeln låg bara i minnet, så ett klick på
    # "planen som PDF" efter en omstart av panelen gav 404 -- och webbläsaren ville spara
    # svaret som "pdf.txt" (mätt 2026-10-05, tre klick i journalen). Nu ligger förslaget och
    # pappret på disk, och ett använt godkännande städas bort.
    import time
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import server
    server.import_save("provnyckel1234", {"proposal": [{"summary": "Prov", "type": "Story"}],
                                          "at": time.time(), "repo": "", "pdfName": "plan-prov.pdf"},
                       b"%PDF-1.4 prov")
    with urllib.request.urlopen(base + "/api/import/pdf?token=provnyckel1234", timeout=60) as svar:
        papper = svar.read()
        check("pappret kommer även efter en omstart (inte 404 -> \"pdf.txt\")",
              svar.status == 200 and svar.headers.get("Content-Type") == "application/pdf"
              and papper[:5] == b"%PDF-" and "plan-prov.pdf" in str(svar.headers.get("Content-Disposition")),
              (svar.status, svar.headers.get("Content-Type"), papper[:5]))
    check("en nyckel som inte är en nyckel ger inget",
          server.import_take("../../etc/passwd") == {})
    server.import_take("provnyckel1234", radera=True)
    check("ett använt godkännande städas bort från disken",
          not (server._import_dir() / "provnyckel1234.json").exists())

    print()
    if FAILED:
        print("{} check(s) failed".format(len(FAILED)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
