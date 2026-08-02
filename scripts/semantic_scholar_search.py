#!/usr/bin/env python3
"""Search Semantic Scholar and export records in the canonical survey schema."""

from __future__ import annotations

import argparse
import os
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from metadata_common import JsonHttpClient, RequestError, clean_text, new_retrieval_id
from metadata_common import utc_now, write_canonical_csv, write_search_log


API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = (
    "paperId,title,abstract,year,publicationDate,authors,venue,publicationTypes,"
    "externalIds,url,citationCount,isOpenAccess,openAccessPdf,fieldsOfStudy"
)


def year_filter(from_year: int | None, to_year: int | None) -> str:
    if from_year and to_year:
        return f"{from_year}-{to_year}"
    if from_year:
        return f"{from_year}-"
    if to_year:
        return f"-{to_year}"
    return ""


def flatten_paper(
    paper: dict[str, Any],
    *,
    query: str,
    rank: int,
    retrieved_at: str,
    retrieval_id: str,
) -> dict[str, object]:
    external_ids = paper.get("externalIds") or {}
    if not isinstance(external_ids, dict):
        external_ids = {}
    authors = [
        clean_text(item.get("name"))
        for item in paper.get("authors") or []
        if isinstance(item, dict)
    ]
    fields = [clean_text(item) for item in paper.get("fieldsOfStudy") or []]
    publication_types = [
        clean_text(item) for item in paper.get("publicationTypes") or []
    ]
    source_url = clean_text(paper.get("url"))
    open_access_pdf = paper.get("openAccessPdf") or {}
    if not isinstance(open_access_pdf, dict):
        open_access_pdf = {}
    paper_id = clean_text(paper.get("paperId"))
    is_open_access = paper.get("isOpenAccess")
    if is_open_access is None:
        is_open_access = bool(open_access_pdf.get("url"))
    return {
        "title": paper.get("title"),
        "authors": "; ".join(filter(None, authors)),
        "year": paper.get("year"),
        "publication_date": paper.get("publicationDate"),
        "venue": paper.get("venue"),
        "publication_type": "; ".join(filter(None, publication_types)),
        "doi": external_ids.get("DOI"),
        "arxiv_id": external_ids.get("ArXiv"),
        "url": source_url or clean_text(open_access_pdf.get("url")),
        "abstract": paper.get("abstract"),
        "subjects": "; ".join(filter(None, fields)),
        "citation_count": paper.get("citationCount"),
        "citation_count_source": "semantic_scholar",
        "is_open_access": is_open_access,
        "source": "semantic_scholar",
        "source_id": f"semantic_scholar:{paper_id}" if paper_id else "",
        "source_url": source_url,
        "retrieval_id": retrieval_id,
        "retrieved_at": retrieved_at,
        "search_query": query,
        "search_rank": rank,
        "metadata_status": "retrieved",
    }


def iter_papers(
    client: JsonHttpClient,
    *,
    query: str,
    from_year: int | None,
    to_year: int | None,
    max_results: int,
    api_key: str | None,
    refresh: bool = False,
) -> Iterable[dict[str, Any]]:
    offset = 0
    headers = {"x-api-key": api_key} if api_key else None
    years = year_filter(from_year, to_year)

    while offset < max_results:
        limit = min(100, max_results - offset)
        params = {
            "query": query,
            "offset": offset,
            "limit": limit,
            "fields": FIELDS,
        }
        if years:
            params["year"] = years
        url = API_URL + "?" + urllib.parse.urlencode(params)
        payload = client.get_json(url, headers=headers, refresh=refresh)
        records = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise ValueError("Semantic Scholar response does not contain a data list")
        if not records:
            break
        yield from records
        next_offset = payload.get("next")
        if next_offset is None:
            break
        next_value = int(next_offset)
        if next_value <= offset:
            break
        offset = next_value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("semantic_scholar_results.csv"))
    parser.add_argument("--log", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/semantic_scholar"))
    parser.add_argument("--cache-ttl-hours", type=int, default=168)
    parser.add_argument("--refresh", action="store_true", help="Bypass cached responses")
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--min-interval", type=float, default=1.0)
    parser.add_argument("--api-key-env", default="SEMANTIC_SCHOLAR_API_KEY")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.max_results < 1 or args.max_results > 1000:
        parser.error("--max-results must be between 1 and 1000 for relevance search")
    if args.from_year and args.to_year and args.from_year > args.to_year:
        parser.error("--from-year cannot be greater than --to-year")

    client = JsonHttpClient(
        cache_dir=args.cache_dir,
        cache_ttl_seconds=args.cache_ttl_hours * 3600,
        max_retries=args.max_retries,
        min_interval_seconds=args.min_interval,
    )
    api_key = os.environ.get(args.api_key_env) or None
    retrieved_at = utc_now()
    retrieval_id = new_retrieval_id("semantic_scholar", retrieved_at)
    try:
        papers = list(
            iter_papers(
                client,
                query=args.query,
                from_year=args.from_year,
                to_year=args.to_year,
                max_results=args.max_results,
                api_key=api_key,
                refresh=args.refresh,
            )
        )[: args.max_results]
    except (RequestError, ValueError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")
    records = [
        flatten_paper(
            paper,
            query=args.query,
            rank=index,
            retrieved_at=retrieved_at,
            retrieval_id=retrieval_id,
        )
        for index, paper in enumerate(papers, start=1)
    ]
    count = write_canonical_csv(args.output, records)
    log_path = args.log or args.output.with_suffix(".search.json")
    write_search_log(
        log_path,
        source="semantic_scholar",
        query=args.query,
        result_count=count,
        output=args.output,
        filters={"from_year": args.from_year, "to_year": args.to_year},
        request_stats=client.stats,
        retrieved_at=retrieved_at,
        retrieval_id=retrieval_id,
    )
    print(f"Exported {count} Semantic Scholar records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
