#!/usr/bin/env python3
"""Mina uppgifter: rätt lista, och en förklaring som inte kan misstolkas.

Kör mot en panel som är i gång. Provet läser och ber om en förklaring av en nyckel som inte
finns (ingen agentkörning), så det kan köras mot den riktiga panelen utan att kosta något.
"""
import json
import sys
import urllib.request

FAILED: list = []


def get(url: str, data=None, timeout: int = 300) -> tuple:
    request = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"} if data is not None else {})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return answer.status, answer.read().decode("utf-8", "replace")


def check(label: str, condition: bool, detail: object = None) -> None:
    print(("ok   " if condition else "FAIL ") + label
          + ("" if condition else "  <- {}".format(str(detail)[:200])))
    if not condition:
        FAILED.append(label)


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788").rstrip("/")
    code, body = get(base + "/api/mine")
    check("200 från /api/mine", code == 200, code)
    data = json.loads(body)
    check("svaret säger ok", data.get("ok") is True, data.get("error"))
    issues = data.get("issues") or []
    check("varje ärende bär krav, bevis, status och en filsökväg",
          all({"requirement", "proof", "status", "file", "open"} <= set(i) for i in issues), issues[:1])
    check("varje ärende har nyckel och sammanfattning",
          all(i.get("key") and i.get("summary") for i in issues), issues[:1])
    check("de öppna räknas rätt", data.get("open") == len([i for i in issues if i.get("open")]),
          data.get("open"))
    _, ström = get(base + "/api/mine/explain", {"stream": True, "key": "SCRUM-999999"})
    rader = [r for r in ström.strip().splitlines() if r.strip()]
    check("strömmen slutar med ett samlat svar", bool(rader) and '"type": "done"' in rader[-1], rader[-1:])
    sista = json.loads(rader[-1])
    check("ett ärende som inte finns förklaras inte (ingen agentkörning)",
          "finns inte bland dina öppna" in str(sista.get("explained")) + str(sista.get("error"))
          + "".join(str(r) for r in rader), sista.get("error") or sista.get("explained"))
    check("ett ärende som inte finns ger inget fel", sista.get("failed") in ([], None), sista.get("failed"))
    print()
    if FAILED:
        print("{} check(s) failed".format(len(FAILED)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
