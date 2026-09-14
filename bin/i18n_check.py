#!/usr/bin/env python3
"""Check the translation files against the English source.

Rules this enforces, so a half-translated file cannot slip through:

  * every language has exactly the same key set as en.json
  * every language keeps the same {placeholders} in each string as en.json
  * no value is empty, and no value is left as English unless it is a word
    that is genuinely the same in that language (allow-listed below)

Run: python3 bin/i18n_check.py [--langs sv,de] [--quiet]
Exit code 0 = all good, 1 = problems (they are listed).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
I18N_DIR = os.path.join(os.path.dirname(HERE), "i18n")
SOURCE = "en"

# Values that are legitimately identical to English in a given language. Every
# entry is a word that is genuinely that term in that language or a product
# term that must not be translated - not a place to hide missing work.
SAME_IS_FINE = {
    # key: languages where this text is legitimately the same as English.
    # "*" = every language (a product name, a hostname, a term nobody translates).
    "nav.backlog": {"*"},                                   # Jira's own term
    "app.name": {"*"},                                      # the product's name
    "nav.board": {"de"},                                    # Jira DE says "Board"
    "board.fieldBoard": {"de"},
    "board.fieldStatus": {"sv", "de", "nl", "pl", "pt"},    # "Status" in five of them
    "common.boardPrefix": {"de"},                           # Jira DE says "Board"
    "conn.account": {"it", "nl"},                           # "Account" is the word there
    "conn.mock": {"*"},                                     # mode name, kept as-is
    "conn.modeLive": {"sv", "nl", "de"},                    # "Live" in all three
    "conn.sitePlaceholder": {"*"},                          # a hostname
    "detail.description": {"fr"},                           # French: "Description"
    "detail.points": {"sv", "de", "nl", "fr"},
    "detail.sprint": {"*"},                                 # Jira's term, untranslated
    "dev.branch": {"*"},
    "dev.build": {"*"},                                     # the developers' words
    "dev.commit": {"*"},
    "dev.pullRequests": {"*"},                              # PRs are PRs
    "reports.burndown": {"*"},                              # the chart's name
    "reports.velocity": {"sv", "de"},                       # kept in both
    "settings.version": {"*"},                              # "GodJIRA {version}"
    "timeline.countIssues": {"nl"},                         # Dutch devs say "issues"
    "timeline.countIssueOne": {"it", "nl"},                 # same, singular
    "timeline.countSprints": {"es", "fr", "nl", "pt"},      # "sprint" is the word
    "timeline.countSprintOne": {"*"},                       # invariable everywhere
    "backlog.countBacklog": {"nl"},
    "backlog.countBacklogOne": {"it", "nl"},
}


PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv):
    langs = None
    quiet = "--quiet" in argv
    if "--langs" in argv:
        langs = argv[argv.index("--langs") + 1].split(",")

    files = sorted(f for f in os.listdir(I18N_DIR) if f.endswith(".json"))
    available = [os.path.splitext(f)[0] for f in files]
    if SOURCE not in available:
        print("missing source file: %s.json" % SOURCE)
        return 1
    if langs:
        unknown = [l for l in langs if l not in available]
        if unknown:
            print("unknown language(s): %s" % ", ".join(unknown))
            return 1
        check = langs
    else:
        check = [l for l in available if l != SOURCE]

    src = load(os.path.join(I18N_DIR, SOURCE + ".json"))
    src_keys = {k for k in src if not k.startswith("_")}
    failures = 0
    print("source: %s.json (%d keys)" % (SOURCE, len(src_keys)))
    print("languages: %s" % ", ".join(check))
    print()

    for lang in check:
        data = load(os.path.join(I18N_DIR, lang + ".json"))
        keys = {k for k in data if not k.startswith("_")}
        missing = sorted(src_keys - keys)
        extra = sorted(keys - src_keys)
        empty = sorted(k for k in keys if not str(data[k]).strip())
        ph_bad = []
        for k in sorted(keys & src_keys):
            want = set(PLACEHOLDER.findall(str(src[k])))
            got = set(PLACEHOLDER.findall(str(data[k])))
            if want != got:
                ph_bad.append((k, sorted(want), sorted(got)))
        same = []
        for k in sorted(keys & src_keys):
            if str(data[k]) == str(src[k]):
                allow = SAME_IS_FINE.get(k, set())
                if "*" not in allow and lang not in allow:
                    same.append(k)

        meta = data.get("_meta") or {}
        status = "OK" if not (missing or extra or empty or ph_bad or same) else "PROBLEMS"
        print("%-3s %-22s %3d keys  %s" % (lang, meta.get("native") or "", len(keys), status))
        if not quiet:
            for k in missing:
                print("      missing   %s" % k)
                failures += 1
            for k in extra:
                print("      extra     %s" % k)
                failures += 1
            for k in empty:
                print("      empty     %s" % k)
                failures += 1
            for k, want, got in ph_bad:
                print("      placeholders %-28s want %s got %s" % (k, want, got))
                failures += 1
            for k in same:
                print("      still english %s" % k)
                failures += 1
        else:
            failures += len(missing) + len(extra) + len(empty) + len(ph_bad) + len(same)

    print()
    print("problems: %d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
