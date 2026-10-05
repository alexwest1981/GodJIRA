#!/usr/bin/env python3
"""Vad agenterna gjorde: modellerna, tokens, kostnaden -- ur Hermes egen sessionsdatabas.

Samma vana som n8n:s databas: den läses skrivskyddat (`mode=ro`), och talen är Hermes
egna. Panelen ritar dem, den räknar dem inte.

Databasen är Hermes, inte vår, så schemafel är ett svar och inte en krasch: går frågan
inte att ställa säger svaret det (`ok: false`, `note`) i stället för att visa noll.

    python3 bin/jira_agents.py --json --days 7
    python3 bin/jira_agents.py --selftest
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(os.environ.get("HERMES_STATE_DB") or "~/.hermes/state.db").expanduser()
DEFAULT_DAYS = 7

# Modellerna i den här Hermes-installationen är deepseek, gemini och vad användaren
# pekar på; färgen hör till ritningen och slås upp på namn. En modell utan färg får
# panelens accent -- hellre en ärlig grå än en påhittad identitet.
COLORS = {"deepseek": "#4d8df6", "gemini": "#e0a33e", "claude": "#e07b4a",
          "gpt": "#3fbf8f", "codex": "#3fbf8f", "qwen": "#8b7bf0", "lyria": "#e5549f"}


def color_of(model: str) -> str:
    low = (model or "").lower()
    for key, value in COLORS.items():
        if key in low:
            return value
    return ""


def usage(days: int = DEFAULT_DAYS, now: float = None, path=None) -> dict:
    """Ett aggregate per modell och källa inom horisonten. Ren läsning, inga skrivningar."""
    db = Path(path or DB)
    since = (now if now is not None else time.time()) - days * 86400
    out = {"ok": True, "days": days, "since": time.strftime("%Y-%m-%d", time.localtime(since)),
           "db": str(db), "mode": "real", "note": "",
           "totals": {"sessions": 0, "tokens": 0, "cost": 0.0, "calls": 0, "messages": 0,
                      "tools": 0},
           "models": [], "sources": []}
    if not db.exists():
        out.update(ok=False, mode="mock", note="no Hermes session store on this machine ({})".format(db))
        return out
    try:
        con = sqlite3.connect("file:{}?mode=ro".format(db), uri=True)
        try:
            rows = con.execute(
                "select model, count(*), sum(coalesce(input_tokens,0)+coalesce(output_tokens,0)"
                "+coalesce(reasoning_tokens,0)), sum(coalesce(estimated_cost_usd,0)),"
                " sum(coalesce(actual_cost_usd,0)), sum(coalesce(api_call_count,0))"
                " from sessions where started_at >= ? group by model order by 3 desc", (since,)).fetchall()
            sources = con.execute(
                "select source, count(*), sum(coalesce(input_tokens,0)+coalesce(output_tokens,0))"
                " from sessions where started_at >= ? group by source order by 2 desc", (since,)).fetchall()
            # Ingen "aktiv tid": ended_at - started_at på en långlivad skrivbordssession
            # gav 389 dygn på en vecka (mätt). Ett tal som inte betyder något visas inte.
            head = con.execute(
                "select count(*), sum(coalesce(message_count,0)), sum(coalesce(tool_call_count,0))"
                " from sessions where started_at >= ?", (since,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        out.update(ok=False, note="{}: {}".format(type(exc).__name__, exc))
        return out
    for model, sessions, tokens, est, act, calls in rows or []:
        out["models"].append({"model": model or "(unknown)", "sessions": sessions,
                              "tokens": tokens or 0, "cost": round((est or 0) + (act or 0), 4),
                              "calls": calls or 0, "color": color_of(model)})
    out["sources"] = [{"source": source or "(unknown)", "sessions": sessions, "tokens": tokens or 0}
                      for source, sessions, tokens in sources or []]
    tokens_all = sum(m["tokens"] for m in out["models"])
    for model in out["models"]:
        model["share"] = round(100.0 * model["tokens"] / tokens_all) if tokens_all else 0
    out["totals"] = {"sessions": head[0] or 0, "messages": head[1] or 0, "tools": head[2] or 0,
                     "tokens": tokens_all,
                     "cost": round(sum(m["cost"] for m in out["models"]), 4),
                     "calls": sum(m["calls"] for m in out["models"])}
    return out


def human(number: float) -> str:
    for edge, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(number) >= edge:
            return "{:.1f}{}".format(number / edge, suffix)
    return str(int(number))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    days = DEFAULT_DAYS
    if "--days" in argv:
        try:
            days = max(1, int(argv[argv.index("--days") + 1]))
        except (IndexError, ValueError):
            print("--days vill ha ett tal", file=sys.stderr)
            return 2
    data = usage(days)
    if "--json" in argv:
        print(json.dumps(data, ensure_ascii=False))
        return 0 if data["ok"] else 1
    if not data["ok"]:
        print(data["note"] or "inget att läsa", file=sys.stderr)
        return 1
    total = data["totals"]
    print("{} dygn: {} sessioner, {} tokens, ~${:.2f} ({} anrop)".format(
        days, total["sessions"], human(total["tokens"]), total["cost"], total["calls"]))
    for model in data["models"]:
        print("  {:<24} {:>3}% {:>8}  {} sessioner".format(
            model["model"], model["share"], human(model["tokens"]), model["sessions"]))
    return 0


def selftest() -> int:
    """Modulen läser någon annans databas: då måste formen provas utan den databasen."""
    import tempfile
    folder = Path(tempfile.mkdtemp(prefix="jira-agents-"))
    db = folder / "state.db"
    con = sqlite3.connect(db)
    con.executescript("""
        create table sessions (model text, source text, started_at real, ended_at real,
            message_count integer, tool_call_count integer, api_call_count integer,
            input_tokens integer, output_tokens integer, reasoning_tokens integer,
            estimated_cost_usd real, actual_cost_usd real, last_activity_at real);
    """)
    now = time.time()
    rows = [("deepseek-flash", "desktop", now - 100, now - 40, 10, 5, 7, 1000, 2000, 0, 0.5, 0.0, now - 40),
            ("deepseek-flash", "cron", now - 200, now - 180, 2, 1, 1, 500, 500, 0, 0.1, 0.0, now - 180),
            ("gemini-3.7-flash", "desktop", now - 300, now - 250, 4, 2, 3, 3000, 500, 0, 0.2, 0.0, now - 250),
            ("old-model", "desktop", now - 30 * 86400, now - 30 * 86400 + 10, 9, 9, 9, 999, 999, 0, 9.9, 0.0, now - 300)]
    con.executemany("insert into sessions values (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()

    got = usage(7, now=now, path=db)
    assert got["ok"], got
    assert got["totals"]["sessions"] == 3, got["totals"]          # 30 dygn gammal rad räknas inte
    assert got["totals"]["tokens"] == 7500, got["totals"]
    assert got["totals"]["messages"] == 16 and got["totals"]["tools"] == 8, got["totals"]
    assert [m["model"] for m in got["models"]] == ["deepseek-flash", "gemini-3.7-flash"], got["models"]
    assert got["models"][0]["sessions"] == 2 and got["models"][0]["tokens"] == 4000, got["models"]
    assert got["models"][0]["share"] == 53 and got["models"][1]["share"] == 47, got["models"]
    assert abs(got["totals"]["cost"] - 0.8) < 1e-9, got["totals"]
    assert got["models"][0]["color"] and got["models"][1]["color"], "färgen slås upp på namn"
    assert [s["source"] for s in got["sources"]] == ["desktop", "cron"], got["sources"]
    assert human(68758249) == "68.8M" and human(1500) == "1.5k" and human(12) == "12", "talen"

    # En databas utan tabellen är ett svar, inte en krasch.
    empty = folder / "empty.db"
    sqlite3.connect(empty).close()
    bad = usage(7, now=now, path=empty)
    assert bad["ok"] is False and "note" in bad and bad["models"] == [], bad
    # Ingen databas alls: provläget säger varför i stället för att visa noll.
    missing = usage(7, now=now, path=folder / "finns-inte.db")
    assert missing["ok"] is False and missing["mode"] == "mock" and missing["totals"]["tokens"] == 0, missing
    print("jira_agents: 14 kontroller OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
