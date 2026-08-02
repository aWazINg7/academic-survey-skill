#!/usr/bin/env python3
"""Search OpenAlex and export records in the canonical survey schema.

OpenAlex requires a free API key. Set ``OPENALEX_API_KEY`` (or choose another
environment-variable name with ``--api-key-env``) before running this command.
"""

from __future__ import annotations

import argparse
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from metadata_common import JsonHttpClient, RequestError, clean_text, new_retrieval_id
from metadata_common import utc_now, write_canonical_csv, write_search_log


API_URL = "https://api.openalex.org/works"
SELECT_FIELDS = (
    "id,doi,title,display_name,publication_year,publication_date,type,language,"
    "cited_by_count,authorships,primary_location,open_access,"
    "abstract_inverted_index,topics"
)
ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", re.I)


def inverted_index_to_text(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for token, offsets in index.items():
        if not isinstance(offsets, list):
            continue
        positions.extend((offset, token) for offset in offsets if isinstance(offset, int))
    return " ".join(token for _, token in sorted(positions))


def extract_arxiv_id(*values: object) -> str:
    for value in values:
        match = ARXIV_URL_RE.search(clean_text(value))
        if match:
            return re.sub(r"\.pdf$", "", match.group(1), flags=re.I)
    return ""


def flatten_work(
    work: dict[str, Any],
    *,
    query: str = "",
    rank: int = 0,
    retrieved_at: str = "",
    retrieval_id: str = "",
) -> dict[str, object]:
    authors = []
    for item in work.get("authorships") or []:
        if not isinstance(item, dict):
            continue
        author = item.get("author") or {}
        name = clean_text(author.get("display_name")) if isinstance(author, dict) else ""
        if name:
            authors.append(name)

    location = work.get("primary_location") or {}
    if not isinstance(location, dict):
        location = {}
    source = location.get("source") or {}
    if not isinstance(source, dict):
        source = {}
    landing_page = clean_text(location.get("landing_page_url"))
    pdf_url = clean_text(location.get("pdf_url"))
    openalex_url = clean_text(work.get("id"))
    doi = clean_text(work.get("doi"))
    topic_names = []
    for topic in work.get("topics") or []:
        if isinstance(topic, dict):
            name = clean_text(topic.get("display_name"))
            if name:
                topic_names.append(name)

    open_access = work.get("open_access") or {}
    is_open_access = open_access.get("is_oa") if isinstance(open_access, dict) else None
    if is_open_access is None:
        is_open_access = location.get("is_oa")

    short_id = openalex_url.rsplit("/", 1)[-1] if openalex_url else ""
    return {
        "title": work.get("title") or work.get("display_name"),
        "authors": "; ".join(authors),
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "venue": source.get("display_name"),
        "publication_type": work.get("type"),
        "doi": doi,
        "arxiv_id": extract_arxiv_id(landing_page, pdf_url),
        "url": landing_page or doi or openalex_url,
        "language": work.get("language"),
        "abstract": inverted_index_to_text(work.get("abstract_inverted_index")),
        "subjects": "; ".join(topic_names[:8]),
        "citation_count": work.get("cited_by_count"),
        "citation_count_source": "openalex",
        "is_open_access": is_open_access,
        "source": "openalex",
        "source_id": f"openalex:{short_id}" if short_id else "",
        "source_url": openalex_url,
        "retrieval_id": retrieval_id,
        "retrieved_at": retrieved_at,
        "search_query": query,
        "search_rank": rank,
        "metadata_status": "retrieved",
    }


def iter_works(
    client: JsonHttpClient,
    *,
    query: str,
    from_year: int | None,
    to_year: int | None,
    max_results: int,
    api_key: str,
    refresh: bool = False,
) -> Iterable[dict[str, Any]]:
    cursor = "*"
    yielded = 0
    filters = []
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    if to_year:
        filters.append(f"to_publication_date:{to_year}-12-31")

    while yielded < max_results:
        per_page = min(100, max_results - yielded)
        params = {
            "search": query,
            "per_page": per_page,
            "cursor": cursor,
            "select": SELECT_FIELDS,
            "api_key": api_key,
        }
        if filters:
            params["filter"] = ",".join(filters)
        url = API_URL + "?" + urllib.parse.urlencode(params)
        payload = client.get_json(url, refresh=refresh)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError("OpenAlex response does not contain a results list")
        if not results:
            break
        for work in results:
            if not isinstance(work, dict):
                continue
            yield work
            yielded += 1
            if yielded >= max_results:
                break
        meta = payload.get("meta") or {}
        next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
        if not next_cursor:
            break
        cursor = clean_text(next_cursor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("literature/openalex_results.csv"))
    parser.add_argument("--log", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/openalex"))
    parser.add_argument("--cache-ttl-hours", type=int, default=168)
    parser.add_argument("--refresh", action="store_true", help="Bypass cached responses")
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--min-interval", type=float, default=0.1)
    parser.add_argument("--api-key-env", default="OPENALEX_API_KEY")
    # Accepted for compatibility with pre-2026 commands; OpenAlex now ignores mailto.
    parser.add_argument("--mailto", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.max_results < 1:
        parser.error("--max-results must be positive")
    if args.from_year and args.to_year and args.from_year > args.to_year:
        parser.error("--from-year cannot be greater than --to-year")
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        parser.error(
            f"OpenAlex requires an API key; set the {args.api_key_env} environment variable"
        )

    client = JsonHttpClient(
        cache_dir=args.cache_dir,
        cache_ttl_seconds=args.cache_ttl_hours * 3600,
        max_retries=args.max_retries,
        min_interval_seconds=args.min_interval,
    )
    retrieved_at = utc_now()
    retrieval_id = new_retrieval_id("openalex", retrieved_at)
    try:
        works = list(
            iter_works(
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
        flatten_work(
            work,
            query=args.query,
            rank=index,
            retrieved_at=retrieved_at,
            retrieval_id=retrieval_id,
        )
        for index, work in enumerate(works, start=1)
    ]
    count = write_canonical_csv(args.output, records)
    write_search_log(
        args.log or args.output.with_suffix(".search.json"),
        source="openalex",
        query=args.query,
        result_count=count,
        output=args.output,
        filters={"from_year": args.from_year, "to_year": args.to_year},
        request_stats=client.stats,
        retrieved_at=retrieved_at,
        retrieval_id=retrieval_id,
    )
    print(f"Exported {count} OpenAlex records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
