#!/usr/bin/env python3
"""Validate conference and journal metadata against the DBLP publication API."""

from __future__ import annotations

import argparse
import csv
import html
import re
import tempfile
import urllib.parse
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from metadata_common import JsonHttpClient, RequestError, append_unique, clean_text
from metadata_common import compatible_first_authors, doi_identity, first_author_signature
from metadata_common import new_retrieval_id, normalize_doi, normalize_title
from metadata_common import read_canonical_csv, utc_now, write_canonical_csv, write_search_log


API_URL = "https://dblp.org/search/publ/api"
TAG_RE = re.compile(r"<[^>]+>")
REPORT_FIELDS = (
    "paper_id",
    "input_title",
    "query",
    "status",
    "score",
    "candidate_title",
    "candidate_year",
    "candidate_venue",
    "candidate_doi",
    "candidate_url",
)


def clean_markup(value: object) -> str:
    return clean_text(html.unescape(TAG_RE.sub(" ", clean_text(value))))


def as_list(value: object) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_authors(info: dict[str, Any]) -> str:
    authors_container = info.get("authors") or {}
    authors = (
        authors_container.get("author")
        if isinstance(authors_container, dict)
        else authors_container
    ) or []
    names = []
    for author in as_list(authors):
        if isinstance(author, dict):
            name = clean_markup(author.get("text") or author.get("name"))
        else:
            name = clean_markup(author)
        if name:
            names.append(name)
    return "; ".join(names)


def flatten_hit(hit: dict[str, Any]) -> dict[str, str]:
    info = hit.get("info") or {}
    if not isinstance(info, dict):
        return {}
    electronic = as_list(info.get("ee"))
    key = clean_text(info.get("key"))
    return {
        "title": clean_markup(info.get("title")),
        "authors": parse_authors(info),
        "year": clean_text(info.get("year")),
        "venue": clean_markup(info.get("venue")),
        "publication_type": clean_text(info.get("type")),
        "volume": clean_text(info.get("volume")),
        "issue": clean_text(info.get("number")),
        "pages": clean_text(info.get("pages")),
        "publisher": clean_markup(info.get("publisher")),
        "doi": normalize_doi(info.get("doi")),
        "url": clean_markup(electronic[0] if electronic else info.get("url")),
        "source_id": f"dblp:{key}" if key else "",
        "source_url": clean_markup(info.get("url")),
    }


def extract_hits(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        raise ValueError("DBLP response is not an object")
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise ValueError("DBLP response result is not an object")
    hits_container = result.get("hits") or {}
    if not isinstance(hits_container, dict):
        raise ValueError("DBLP response hits is not an object")
    raw_hits = hits_container.get("hit") or []
    return [flatten_hit(hit) for hit in as_list(raw_hits) if isinstance(hit, dict)]


def match_score(record: dict[str, str], candidate: dict[str, str]) -> float:
    left_doi = doi_identity(record.get("doi"))
    right_doi = doi_identity(candidate.get("doi"))
    if left_doi and right_doi:
        return 1.0 if left_doi == right_doi else 0.0
    left_title = normalize_title(record.get("title"))
    right_title = normalize_title(candidate.get("title"))
    if not left_title or not right_title:
        return 0.0
    score = SequenceMatcher(None, left_title, right_title).ratio()
    left_year = clean_text(record.get("year"))
    right_year = clean_text(candidate.get("year"))
    if left_year and right_year and left_year != right_year:
        return 0.0
    if not compatible_first_authors(record.get("authors"), candidate.get("authors")):
        return 0.0
    left_author = first_author_signature(record.get("authors"))
    right_author = first_author_signature(candidate.get("authors"))
    left_venue = normalize_title(record.get("venue"))
    right_venue = normalize_title(candidate.get("venue"))
    if (
        (not left_author[0] or not right_author[0])
        and left_venue
        and right_venue
        and left_venue != right_venue
    ):
        return 0.0
    return max(0.0, min(1.0, score))


def append_note(existing: object, note: str) -> str:
    current = clean_text(existing)
    return f"{current} | {note}" if current else note


def apply_candidate(
    record: dict[str, str],
    candidate: dict[str, str],
    *,
    retrieved_at: str,
    retrieval_id: str,
) -> dict[str, str]:
    conflicts = []
    for field in ("title", "authors", "year", "venue", "doi"):
        left = clean_text(record.get(field))
        right = clean_text(candidate.get(field))
        if not left or not right:
            continue
        if field in {"title", "venue"}:
            equal = normalize_title(left) == normalize_title(right)
        elif field == "authors":
            equal = compatible_first_authors(left, right)
        elif field == "doi":
            left_identity = doi_identity(left)
            right_identity = doi_identity(right)
            if not left_identity or not right_identity:
                continue
            equal = left_identity == right_identity
        else:
            equal = left == right
        if not equal:
            conflicts.append(field)

    left_doi = doi_identity(record.get("doi"))
    right_doi = doi_identity(candidate.get("doi"))
    exact_doi = bool(left_doi and right_doi and left_doi == right_doi)
    left_author = first_author_signature(record.get("authors"))
    right_author = first_author_signature(candidate.get("authors"))
    has_author_evidence = bool(left_author[0] and right_author[0])
    strong_conflict = "doi" in conflicts or (
        (
            any(field in conflicts for field in ("authors", "year"))
            or ("venue" in conflicts and not has_author_evidence)
        )
        and not exact_doi
    )
    if strong_conflict:
        record["metadata_status"] = "dblp_conflict"
        record["notes"] = append_note(
            record.get("notes"), "dblp_conflict:" + ",".join(conflicts)
        )
        return record

    for field in (
        "title",
        "authors",
        "year",
        "venue",
        "publication_type",
        "volume",
        "issue",
        "pages",
        "publisher",
        "url",
    ):
        if not record.get(field) and candidate.get(field):
            record[field] = candidate[field]
    if not left_doi and right_doi:
        if clean_text(record.get("doi")):
            record["notes"] = append_note(
                record.get("notes"), "dblp_replaced_invalid_doi"
            )
        record["doi"] = right_doi
    record["source"] = append_unique(record.get("source"), "dblp")
    record["source_id"] = append_unique(
        record.get("source_id"), candidate.get("source_id")
    )
    record["source_url"] = append_unique(
        record.get("source_url"), candidate.get("source_url")
    )
    record["retrieved_at"] = append_unique(record.get("retrieved_at"), retrieved_at)
    record["retrieval_id"] = append_unique(record.get("retrieval_id"), retrieval_id)
    record["metadata_status"] = "dblp_validated"
    if conflicts:
        record["notes"] = append_note(
            record.get("notes"), "dblp_conflict:" + ",".join(conflicts)
        )
    return record


def write_report(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            temporary_path = Path(handle.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("dblp_validated.csv"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--threshold", type=float, default=0.88)
    parser.add_argument("--candidates", type=int, default=5)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/dblp"))
    parser.add_argument("--cache-ttl-hours", type=int, default=168)
    parser.add_argument("--refresh", action="store_true", help="Bypass cached responses")
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--min-interval", type=float, default=1.5)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not 0.0 <= args.threshold <= 1.0:
        parser.error("--threshold must be between 0 and 1")
    if args.candidates < 1 or args.candidates > 100:
        parser.error("--candidates must be between 1 and 100")

    client = JsonHttpClient(
        cache_dir=args.cache_dir,
        cache_ttl_seconds=args.cache_ttl_hours * 3600,
        max_retries=args.max_retries,
        min_interval_seconds=args.min_interval,
    )
    records = read_canonical_csv(args.input)
    retrieved_at = utc_now()
    retrieval_id = new_retrieval_id("dblp_validation", retrieved_at)
    output_rows = []
    report_rows: list[dict[str, object]] = []
    matched = 0

    for record in records:
        input_title = record.get("title", "")
        query = clean_text(record.get("title")) or doi_identity(record.get("doi"))
        if not query:
            record["metadata_status"] = "dblp_skipped_no_query"
            output_rows.append(record)
            report_rows.append(
                {
                    "paper_id": record.get("paper_id"),
                    "input_title": record.get("title"),
                    "query": "",
                    "status": "skipped_no_query",
                    "score": "",
                }
            )
            continue

        params = {"q": query, "format": "json", "h": args.candidates, "c": 0}
        url = API_URL + "?" + urllib.parse.urlencode(params)
        try:
            candidates = extract_hits(client.get_json(url, refresh=args.refresh))
        except (RequestError, ValueError, OSError) as exc:
            if not args.continue_on_error:
                parser.exit(2, f"error: {exc}\n")
            record["metadata_status"] = "dblp_error"
            record["notes"] = append_note(record.get("notes"), str(exc))
            output_rows.append(record)
            report_rows.append(
                {
                    "paper_id": record.get("paper_id"),
                    "input_title": record.get("title"),
                    "query": query,
                    "status": "error",
                    "score": "",
                }
            )
            continue

        ranked = sorted(
            ((match_score(record, candidate), candidate) for candidate in candidates),
            key=lambda item: item[0],
            reverse=True,
        )
        score, candidate = ranked[0] if ranked else (0.0, {})
        if candidate and score >= args.threshold:
            status = "matched"
            matched += 1
            record = apply_candidate(
                record,
                candidate,
                retrieved_at=retrieved_at,
                retrieval_id=retrieval_id,
            )
        elif candidate:
            status = "ambiguous"
            record["metadata_status"] = "dblp_ambiguous"
        else:
            status = "not_found"
            record["metadata_status"] = "dblp_not_found"
        output_rows.append(record)
        report_rows.append(
            {
                "paper_id": record.get("paper_id"),
                "input_title": input_title,
                "query": query,
                "status": status,
                "score": f"{score:.4f}" if candidate else "",
                "candidate_title": candidate.get("title", ""),
                "candidate_year": candidate.get("year", ""),
                "candidate_venue": candidate.get("venue", ""),
                "candidate_doi": candidate.get("doi", ""),
                "candidate_url": candidate.get("source_url", ""),
            }
        )

    count = write_canonical_csv(args.output, output_rows)
    report_path = args.report or args.output.with_suffix(".dblp_report.csv")
    write_report(report_path, report_rows)
    write_search_log(
        args.log or args.output.with_suffix(".search.json"),
        source="dblp_validation",
        query=f"validate:{args.input}",
        result_count=count,
        output=args.output,
        filters={"matched": matched, "threshold": args.threshold},
        request_stats=client.stats,
        input_files=[args.input],
        retrieved_at=retrieved_at,
        retrieval_id=retrieval_id,
        operation="validate",
    )
    print(f"Validated {count} records against DBLP; matched {matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
