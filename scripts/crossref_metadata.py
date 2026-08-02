#!/usr/bin/env python3
"""Search Crossref or enrich canonical literature records by DOI."""

from __future__ import annotations

import argparse
import html
import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from metadata_common import JsonHttpClient, RequestError, append_unique, clean_text
from metadata_common import doi_identity
from metadata_common import new_retrieval_id, normalize_record, normalize_title
from metadata_common import normalize_year
from metadata_common import read_canonical_csv, utc_now, write_canonical_csv, write_search_log


API_URL = "https://api.crossref.org/v1/works"
TAG_RE = re.compile(r"<[^>]+>")


def first_value(value: object) -> str:
    if isinstance(value, list):
        return clean_text(value[0]) if value else ""
    return clean_text(value)


def joined_values(value: object) -> str:
    values = value if isinstance(value, list) else ([value] if value else [])
    return "; ".join(filter(None, (clean_text(item) for item in values)))


def publication_date(item: dict[str, Any]) -> str:
    for field in ("published-print", "published-online", "published", "issued"):
        date_container = item.get(field) or {}
        if not isinstance(date_container, dict):
            continue
        date_parts = date_container.get("date-parts") or []
        if not date_parts or not date_parts[0]:
            continue
        parts = [str(value) for value in date_parts[0][:3] if value is not None]
        if not parts:
            continue
        return "-".join(
            [parts[0], *(part.zfill(2) for part in parts[1:])]
        )
    return ""


def clean_abstract(value: object) -> str:
    return clean_text(html.unescape(TAG_RE.sub(" ", clean_text(value))))


def flatten_work(
    item: dict[str, Any],
    *,
    query: str,
    rank: int,
    retrieved_at: str,
    retrieval_id: str = "",
) -> dict[str, object]:
    author_names = []
    raw_authors = item.get("author") or []
    authors = raw_authors if isinstance(raw_authors, list) else [raw_authors]
    for author in authors:
        if not isinstance(author, dict):
            continue
        literal = clean_text(author.get("name"))
        if literal:
            author_names.append(literal)
            continue
        family = clean_text(author.get("family"))
        given = clean_text(author.get("given"))
        name = ", ".join(filter(None, (family, given)))
        if name:
            author_names.append(name)

    doi = clean_text(item.get("DOI"))
    date_value = publication_date(item)
    subjects = joined_values(item.get("subject"))
    publisher_url = clean_text(item.get("URL"))
    return {
        "title": first_value(item.get("title")),
        "authors": "; ".join(author_names),
        "year": date_value[:4],
        "publication_date": date_value,
        "venue": first_value(item.get("container-title")),
        "publication_type": item.get("type"),
        "volume": item.get("volume"),
        "issue": item.get("issue"),
        "pages": item.get("page"),
        "publisher": item.get("publisher"),
        "issn": joined_values(item.get("ISSN")),
        "isbn": joined_values(item.get("ISBN")),
        "doi": doi,
        "url": publisher_url,
        "language": item.get("language"),
        "abstract": clean_abstract(item.get("abstract")),
        "subjects": subjects,
        "citation_count": item.get("is-referenced-by-count"),
        "citation_count_source": "crossref",
        "source": "crossref",
        "source_id": f"crossref:{doi}" if doi else "",
        "source_url": (
            f"https://api.crossref.org/v1/works/{urllib.parse.quote(doi, safe='')}"
            if doi
            else ""
        ),
        "retrieval_id": retrieval_id,
        "retrieved_at": retrieved_at,
        "search_query": query,
        "search_rank": rank,
        "metadata_status": "retrieved",
    }


def iter_search_results(
    client: JsonHttpClient,
    *,
    query: str,
    from_year: int | None,
    to_year: int | None,
    max_results: int,
    mailto: str | None,
    refresh: bool = False,
) -> Iterable[dict[str, Any]]:
    offset = 0
    while offset < max_results:
        rows = min(1000, max_results - offset)
        params: dict[str, object] = {
            "query.bibliographic": query,
            "rows": rows,
            "offset": offset,
        }
        filters = []
        if from_year:
            filters.append(f"from-pub-date:{from_year}-01-01")
        if to_year:
            filters.append(f"until-pub-date:{to_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        if mailto:
            params["mailto"] = mailto
        url = API_URL + "?" + urllib.parse.urlencode(params)
        payload = client.get_json(url, refresh=refresh)
        message = payload.get("message") if isinstance(payload, dict) else None
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            raise ValueError("Crossref response does not contain message.items")
        if not items:
            break
        yield from items
        offset += len(items)
        total = int(message.get("total-results") or 0)
        if offset >= total:
            break


def append_note(existing: object, note: str) -> str:
    current = clean_text(existing)
    return f"{current} | {note}" if current else note


def enrich_record(
    original: dict[str, str], crossref: dict[str, object], *, retrieved_at: str
) -> dict[str, str]:
    record = normalize_record(original)
    candidate = normalize_record(crossref)
    conflicts = []
    for field in ("title", "year", "venue", "doi"):
        if field == "year":
            left = record.get("year", "") or record.get("publication_date", "")
            right = candidate.get("year", "") or candidate.get("publication_date", "")
        else:
            left = record.get(field, "")
            right = candidate.get(field, "")
        if not left or not right:
            continue
        if field == "doi":
            comparable_left = doi_identity(left)
            comparable_right = doi_identity(right)
            if not comparable_left or not comparable_right:
                continue
        elif field == "year":
            comparable_left = normalize_year(left)
            comparable_right = normalize_year(right)
        else:
            comparable_left = normalize_title(left)
            comparable_right = normalize_title(right)
        if comparable_left != comparable_right:
            conflicts.append(field)

    if "doi" in conflicts:
        record["metadata_status"] = "crossref_conflict"
        record["notes"] = append_note(
            record.get("notes"), "crossref_conflict:" + ",".join(conflicts)
        )
        return record

    coupled_fields = {
        "year",
        "publication_date",
        "citation_count",
        "citation_count_source",
        "doi",
    }
    for field, value in candidate.items():
        if field in {"paper_id", "notes", "metadata_status"} | coupled_fields:
            continue
        if not record.get(field) and value:
            record[field] = value

    if "year" not in conflicts:
        if not record.get("year") and candidate.get("year"):
            record["year"] = candidate["year"]
        if not record.get("publication_date") and candidate.get("publication_date"):
            record["publication_date"] = candidate["publication_date"]

    original_doi = doi_identity(record.get("doi"))
    candidate_doi = doi_identity(candidate.get("doi"))
    if not original_doi and candidate_doi:
        if clean_text(record.get("doi")):
            record["notes"] = append_note(
                record.get("notes"), "crossref_replaced_invalid_doi"
            )
        record["doi"] = candidate_doi

    record_count = clean_text(record.get("citation_count"))
    record_count_source = clean_text(record.get("citation_count_source"))
    candidate_count = clean_text(candidate.get("citation_count"))
    candidate_count_source = clean_text(candidate.get("citation_count_source"))
    if (
        not record_count
        and candidate_count
        and (not record_count_source or "crossref" in record_count_source.split("; "))
    ):
        record["citation_count"] = candidate_count
        if not record_count_source:
            record["citation_count_source"] = candidate_count_source

    record["source"] = append_unique(record.get("source"), "crossref")
    record["source_id"] = append_unique(
        record.get("source_id"), candidate.get("source_id")
    )
    record["source_url"] = append_unique(
        record.get("source_url"), candidate.get("source_url")
    )
    record["retrieved_at"] = append_unique(record.get("retrieved_at"), retrieved_at)
    record["retrieval_id"] = append_unique(
        record.get("retrieval_id"), candidate.get("retrieval_id")
    )
    record["metadata_status"] = "crossref_enriched"
    if conflicts:
        record["notes"] = append_note(
            record.get("notes"), "crossref_conflict:" + ",".join(conflicts)
        )
    return record


def create_client(args: argparse.Namespace) -> JsonHttpClient:
    user_agent = "academic-survey-skill/0.3"
    if args.mailto:
        user_agent += f" (mailto:{args.mailto})"
    return JsonHttpClient(
        cache_dir=args.cache_dir,
        cache_ttl_seconds=args.cache_ttl_hours * 3600,
        max_retries=args.max_retries,
        min_interval_seconds=args.min_interval,
        user_agent=user_agent,
    )


def add_http_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mailto")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/crossref"))
    parser.add_argument("--cache-ttl-hours", type=int, default=168)
    parser.add_argument("--refresh", action="store_true", help="Bypass cached responses")
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--min-interval", type=float, default=1.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Search Crossref works")
    search.add_argument("--query", required=True)
    search.add_argument("--from-year", type=int)
    search.add_argument("--to-year", type=int)
    search.add_argument("--max-results", type=int, default=100)
    search.add_argument("--output", type=Path, default=Path("crossref_results.csv"))
    search.add_argument("--log", type=Path)
    add_http_options(search)

    enrich = subparsers.add_parser("enrich", help="Fill missing fields using record DOIs")
    enrich.add_argument("input", type=Path)
    enrich.add_argument("--output", type=Path, default=Path("crossref_enriched.csv"))
    enrich.add_argument("--log", type=Path)
    enrich.add_argument("--continue-on-error", action="store_true")
    add_http_options(enrich)
    return parser


def run_search(args: argparse.Namespace) -> int:
    if args.max_results < 1 or args.max_results > 10000:
        raise ValueError("--max-results must be between 1 and 10000")
    if args.from_year and args.to_year and args.from_year > args.to_year:
        raise ValueError("--from-year cannot be greater than --to-year")
    client = create_client(args)
    retrieved_at = utc_now()
    retrieval_id = new_retrieval_id("crossref", retrieved_at)
    items = list(
        iter_search_results(
            client,
            query=args.query,
            from_year=args.from_year,
            to_year=args.to_year,
            max_results=args.max_results,
            mailto=args.mailto,
            refresh=args.refresh,
        )
    )[: args.max_results]
    records = [
        flatten_work(
            item,
            query=args.query,
            rank=index,
            retrieved_at=retrieved_at,
            retrieval_id=retrieval_id,
        )
        for index, item in enumerate(items, start=1)
    ]
    count = write_canonical_csv(args.output, records)
    write_search_log(
        args.log or args.output.with_suffix(".search.json"),
        source="crossref",
        query=args.query,
        result_count=count,
        output=args.output,
        filters={"from_year": args.from_year, "to_year": args.to_year},
        request_stats=client.stats,
        retrieved_at=retrieved_at,
        retrieval_id=retrieval_id,
    )
    print(f"Exported {count} Crossref records to {args.output}")
    return 0


def run_enrich(args: argparse.Namespace) -> int:
    client = create_client(args)
    records = read_canonical_csv(args.input)
    retrieved_at = utc_now()
    retrieval_id = new_retrieval_id("crossref_enrichment", retrieved_at)
    enriched = []
    matched = 0
    not_found = 0
    for record in records:
        raw_doi = record.get("doi", "")
        doi = doi_identity(raw_doi)
        if not doi:
            record["metadata_status"] = (
                "crossref_skipped_invalid_doi"
                if clean_text(raw_doi)
                else "crossref_skipped_no_doi"
            )
            enriched.append(record)
            continue
        params = {"mailto": args.mailto} if args.mailto else {}
        url = f"{API_URL}/{urllib.parse.quote(doi, safe='')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            payload = client.get_json(url, refresh=args.refresh)
        except RequestError as exc:
            if exc.status == 404:
                record["metadata_status"] = "crossref_not_found"
                not_found += 1
                enriched.append(record)
                continue
            if not args.continue_on_error:
                raise
            record["metadata_status"] = "crossref_error"
            record["notes"] = append_note(record.get("notes"), str(exc))
            enriched.append(record)
            continue
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            raise ValueError("Crossref DOI response does not contain a message object")
        candidate = flatten_work(
            message,
            query=f"doi:{doi}",
            rank=1,
            retrieved_at=retrieved_at,
            retrieval_id=retrieval_id,
        )
        enriched.append(enrich_record(record, candidate, retrieved_at=retrieved_at))
        matched += 1

    count = write_canonical_csv(args.output, enriched)
    write_search_log(
        args.log or args.output.with_suffix(".search.json"),
        source="crossref_enrichment",
        query="DOI metadata enrichment",
        result_count=count,
        output=args.output,
        filters={"matched": matched, "not_found": not_found},
        request_stats=client.stats,
        input_files=[args.input],
        retrieved_at=retrieved_at,
        retrieval_id=retrieval_id,
        operation="enrich",
    )
    print(f"Processed {count} records; Crossref matched {matched}, not found {not_found}")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_search(args) if args.command == "search" else run_enrich(args)
    except (RequestError, ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
