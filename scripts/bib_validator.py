#!/usr/bin/env python3
"""Perform lightweight validation of a BibTeX database.

Checks duplicate citation keys, missing required fields, malformed DOI values,
and suspicious placeholder text. This is a syntax and metadata audit, not an
online existence check.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List

ENTRY_RE = re.compile(r"@(?P<type>\w+)\s*\{\s*(?P<key>[^,\s]+)\s*,(?P<body>.*?)(?=\n@|\Z)", re.S)
FIELD_RE = re.compile(r"(?P<name>\w+)\s*=\s*[\{\"](?P<value>.*?)[\}\"]\s*,?\s*(?=\n\s*\w+\s*=|\Z)", re.S)
REQUIRED = {
    "article": {"title", "author", "year", "journal"},
    "inproceedings": {"title", "author", "year", "booktitle"},
    "book": {"title", "author", "year", "publisher"},
    "phdthesis": {"title", "author", "year", "school"},
}
PLACEHOLDERS = ("todo", "tbd", "unknown", "xxx", "citation needed")


def parse_entries(text: str) -> List[Dict[str, object]]:
    entries = []
    for match in ENTRY_RE.finditer(text):
        fields = {
            m.group("name").lower(): " ".join(m.group("value").split())
            for m in FIELD_RE.finditer(match.group("body"))
        }
        entries.append({"type": match.group("type").lower(), "key": match.group("key"), "fields": fields})
    return entries


def validate(entries: List[Dict[str, object]]) -> List[str]:
    problems: List[str] = []
    seen = set()
    for entry in entries:
        key = str(entry["key"])
        kind = str(entry["type"])
        fields = entry["fields"]
        assert isinstance(fields, dict)
        if key in seen:
            problems.append(f"ERROR duplicate key: {key}")
        seen.add(key)
        missing = REQUIRED.get(kind, set()) - set(fields)
        if missing:
            problems.append(f"ERROR {key}: missing {', '.join(sorted(missing))}")
        doi = str(fields.get("doi", "")).strip()
        if doi and ("doi.org/" in doi or doi.lower().startswith("doi:")):
            problems.append(f"WARN  {key}: normalize DOI to bare identifier")
        combined = " ".join(str(v).lower() for v in fields.values())
        if any(word in combined for word in PLACEHOLDERS):
            problems.append(f"WARN  {key}: contains placeholder text")
        year = str(fields.get("year", ""))
        if year and not re.fullmatch(r"\d{4}", year):
            problems.append(f"WARN  {key}: unusual year value {year!r}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bibfile")
    args = parser.parse_args()
    path = Path(args.bibfile)
    entries = parse_entries(path.read_text(encoding="utf-8"))
    problems = validate(entries)
    print(f"Parsed {len(entries)} entries from {path}")
    if problems:
        print("\n".join(problems))
        raise SystemExit(1 if any(p.startswith("ERROR") for p in problems) else 0)
    print("No structural metadata problems found.")


if __name__ == "__main__":
    main()
