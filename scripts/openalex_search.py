#!/usr/bin/env python3
"""Search OpenAlex and export literature metadata for survey projects.

Example:
    python scripts/openalex_search.py \
      --query "verifiable aggregation federated learning" \
      --from-year 2020 --to-year 2026 --max-results 100 \
      --output literature/openalex_results.csv

The script uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List

API_URL = "https://api.openalex.org/works"


def inverted_index_to_text(index: Dict[str, List[int]] | None) -> str:
    if not index:
        return ""
    positions: List[tuple[int, str]] = []
    for token, offsets in index.items():
        positions.extend((offset, token) for offset in offsets)
    return " ".join(token for _, token in sorted(positions))


def request_json(url: str, mailto: str | None = None) -> Dict[str, Any]:
    headers = {"User-Agent": "academic-survey-skill/1.0"}
    if mailto:
        headers["User-Agent"] += f" (mailto:{mailto})"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def iter_works(query: str, from_year: int | None, to_year: int | None,
               max_results: int, mailto: str | None) -> Iterable[Dict[str, Any]]:
    cursor = "*"
    yielded = 0
    filters = []
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    if to_year:
        filters.append(f"to_publication_date:{to_year}-12-31")

    while yielded < max_results:
        per_page = min(200, max_results - yielded)
        params = {
            "search": query,
            "per-page": per_page,
            "cursor": cursor,
            "select": "id,doi,title,publication_year,publication_date,type,authorships,primary_location,host_venue,cited_by_count,open_access,abstract_inverted_index,concepts",
        }
        if filters:
            params["filter"] = ",".join(filters)
        if mailto:
            params["mailto"] = mailto
        url = API_URL + "?" + urllib.parse.urlencode(params)
        data = request_json(url, mailto)
        results = data.get("results", [])
        if not results:
            break
        for work in results:
            yield work
            yielded += 1
            if yielded >= max_results:
                break
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.1)


def flatten_work(work: Dict[str, Any]) -> Dict[str, Any]:
    authors = []
    for item in work.get("authorships") or []:
        name = (item.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
    venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
    concepts = [c.get("display_name", "") for c in (work.get("concepts") or [])[:8]]
    return {
        "title": work.get("title", ""),
        "authors": "; ".join(authors),
        "year": work.get("publication_year", ""),
        "date": work.get("publication_date", ""),
        "venue": venue or "",
        "type": work.get("type", ""),
        "doi": work.get("doi", ""),
        "openalex_id": work.get("id", ""),
        "cited_by_count": work.get("cited_by_count", 0),
        "is_open_access": (work.get("open_access") or {}).get("is_oa", False),
        "concepts": "; ".join(filter(None, concepts)),
        "abstract": inverted_index_to_text(work.get("abstract_inverted_index")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--mailto")
    parser.add_argument("--output", default="literature/openalex_results.csv")
    args = parser.parse_args()

    rows = [flatten_work(w) for w in iter_works(
        args.query, args.from_year, args.to_year, args.max_results, args.mailto
    )]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else [
        "title", "authors", "year", "date", "venue", "type", "doi",
        "openalex_id", "cited_by_count", "is_open_access", "concepts", "abstract"
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Exported {len(rows)} records to {output}")


if __name__ == "__main__":
    main()
