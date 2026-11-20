"""Score a report-review findings table against the planted-error answer key.

Usage: python score.py FINDINGS.md answer-key.md FIXTURE
FINDINGS.md holds the skill's findings table (Severity | Quote | ...). Stdlib only.
"""

import re
import sys


def _norm(text: str) -> str:
    text = text.lower().replace("…", " ").replace("...", " ")
    text = re.sub(r"[\"'\u201c\u201d\u2018\u2019`]", "", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _rows(path: str) -> list[list[str]]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line.startswith("|") or set(line) <= set("|-: "):
                continue
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def _hit(planted: str, quote: str) -> bool:
    return bool(quote) and (planted in quote or (len(quote) >= 12 and quote in planted))


def score(findings_path: str, key_path: str, fixture: str) -> tuple[float, list[str], list[str]]:
    key = [r for r in _rows(key_path) if len(r) >= 5 and r[1] == fixture]
    planted = {r[0]: _norm(r[2]) for r in key}
    found = [r for r in _rows(findings_path) if len(r) >= 2 and r[0].lower() != "severity"]
    hits = {pid for pid, p in planted.items() for r in found if _hit(p, _norm(r[1]))}
    missed = sorted(set(planted) - hits, key=lambda x: int(x[1:]))
    unplanted = [
        f"{r[0]}: {r[1]}"
        for r in found
        if r[0].strip("* ").lower() in {"critical", "major"}
        and not any(_hit(p, _norm(r[1])) for p in planted.values())
    ]
    return (len(hits) / len(planted) if planted else 0.0), missed, unplanted


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    recall, missed, unplanted = score(*sys.argv[1:])
    print(f"recall: {recall:.0%}  missed: {', '.join(missed) or 'none'}")
    print(
        f"unplanted Critical/Major (adjudicate; real errors are not false accusations): {len(unplanted)}"
    )
    for row in unplanted:
        print(f"  - {row}")
