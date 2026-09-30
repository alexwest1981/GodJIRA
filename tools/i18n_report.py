"""Läget för panelens översättningar: vad som saknas i varje språkfil.

    python3 tools/i18n_report.py                 # en rad per språk
    python3 tools/i18n_report.py --missing en    # nycklarna som saknas, som json

Saknade nycklar är inte ett fel i sig: panelen visar den svenska raden då. Men de syns
som svenska ord i en engelsk skärm, så de skall helst fyllas i. Vill man bidra med ett
språk är det bara att lägga en fil i panel/i18n/ med samma nycklar som sv.json --
panel/test_i18n.py säger till om något glider isär.
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
I18N = ROOT / "panel/i18n"


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main():
    parser = argparse.ArgumentParser(description="Läget för panelens språkfiler")
    parser.add_argument("--missing", metavar="SPRÅK", help="skriv nycklarna som saknas, som json")
    parser.add_argument("--untranslated", action="store_true", help="visa rader som är lika på båda språken")
    parser.add_argument("--prune", action="store_true",
                        help="ta bort nycklar som inte längre finns i källistan (skriv först, fråga sedan)")
    args = parser.parse_args()

    source = load(I18N / "sv.json")
    if not source:
        print("hittar ingen källista i {}".format(I18N), file=sys.stderr)
        return 1

    if args.missing:
        table = load(I18N / "{}.json".format(args.missing))
        missing = {key: source[key] for key in source if key not in table}
        print(json.dumps(missing, ensure_ascii=False, indent=1))
        return 0

    if args.prune:
        for path in sorted(I18N.glob("*.json")):
            if path.stem == "sv":
                continue
            table = load(path)
            extra = sorted(set(table) - set(source))
            if extra:
                for key in extra:
                    table.pop(key, None)
                path.write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                print("{}: tog bort {} nycklar som inte finns i källistan".format(path.stem, len(extra)))
        return 0

    print("{:<4} {:>5} {:>8} {:>6} {:>12}".format("språk", "rader", "saknas", "extra", "oöversatta"))
    for path in sorted(I18N.glob("*.json")):
        table = load(path)
        if path.stem == "sv":
            print("{:<4} {:>5} {:>8} {:>6} {:>12}".format("sv", len(table), "-", "-", "-"))
            continue
        missing = sorted(set(source) - set(table))
        extra = sorted(set(table) - set(source))
        same = sorted(key for key, value in table.items() if key == value)
        print("{:<4} {:>5} {:>8} {:>6} {:>12}".format(path.stem, len(table), len(missing), len(extra), len(same)))
        if args.untranslated and same:
            for key in same:
                print("      ", key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
