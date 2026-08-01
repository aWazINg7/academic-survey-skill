#!/usr/bin/env python3
"""Deduplicate a literature CSV by DOI, arXiv ID, and normalized title."""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ARXIV_RE = re.compile(r"(?:arxiv:)?\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower().strip()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def normalize_doi(value: str) -> str:
    doi = (value or "").strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    return doi


def extract_arxiv(row: Dict[str, str]) -> str:
    haystack = " ".join([row.get("url", ""), row.get("doi", ""), row.get("notes", "")])
    match = ARXIV_RE.search(haystack)
    return match.group(1) if match else ""


def record_keys(row: Dict[str, str]) -> Iterable[Tuple[str, str]]:
    doi = normalize_doi(row.get("doi", ""))
    if doi:
        yield ("doi", doi)
    arxiv_id = extract_arxiv(row)
    if arxiv_id:
        yield ("arxiv", arxiv_id)
    title = normalize_text(row.get("title", ""))
    if title:
        yield ("title", title)


def score_record(row: Dict[str, str]) -> int:
    """Prefer records with richer metadata and formal publication details."""
    score = sum(bool((value or "").strip()) for value in row.values())
    venue = (row.get("venue", "") or "").lower()
    url = (row.get("url", "") or "").lower()
    if venue and "arxiv" not in venue:
        score += 4
    if normalize_doi(row.get("doi", "")):
        score += 3
    if "arxiv.org" in url:
        score -= 1
    return score


def deduplicate(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    kept: List[Dict[str, str]] = []
    duplicates: List[Dict[str, str]] = []
    key_to_index: Dict[Tuple[str, str], int] = {}

    for row in rows:
        matching = {key_to_index[key] for key in record_keys(row) if key in key_to_index}
        if not matching:
            index = len(kept)
            kept.append(row)
            for key in record_keys(row):
                key_to_index[key] = index
            continue

        target = min(matching)
        current = kept[target]
        if score_record(row) > score_record(current):
            duplicates.append(current)
            kept[target] = row
            for key in record_keys(row):
                key_to_index[key] = target
        else:
            duplicates.append(row)

    return kept, duplicates


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV has no header")
        return list(reader.fieldnames), list(reader)


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input literature CSV")
    parser.add_argument("--output", type=Path, default=Path("literature_deduplicated.csv"))
    parser.add_argument("--duplicates", type=Path, default=Path("literature_duplicates.csv"))
    args = parser.parse_args()

    fieldnames, rows = read_csv(args.input)
    kept, duplicates = deduplicate(rows)
    write_csv(args.output, fieldnames, kept)
    write_csv(args.duplicates, fieldnames, duplicates)
    print(f"Input: {len(rows)} | Kept: {len(kept)} | Duplicates: {len(duplicates)}")


if __name__ == "__main__":
    main()
