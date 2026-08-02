#!/usr/bin/env python3
"""Merge and deduplicate one or more canonical literature CSV files.

Records are connected by normalized DOI, then conservatively by arXiv identifier
or exact normalized title when strong identifiers do not conflict. Connected
components are merged field by field so complementary metadata and provenance
are retained. Potentially meaningful conflicts are reported and never silently
overwrite the selected base value.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from metadata_common import CANONICAL_FIELDS, arxiv_identity, clean_text, doi_identity
from metadata_common import author_signatures_compatible, first_author_signature
from metadata_common import new_retrieval_id
from metadata_common import normalize_doi as canonical_normalize_doi
from metadata_common import normalize_record, normalize_title, normalize_year
from metadata_common import read_canonical_csv, utc_now
from metadata_common import write_canonical_csv, write_search_log


ARXIV_ID_PATTERN = r"(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7})|(?:\d{4}\.\d{4,5})"
ARXIV_URL_RE = re.compile(
    rf"arxiv\.org/(?:abs|pdf)/({ARXIV_ID_PATTERN})(?:v\d+)?(?:\.pdf)?", re.I
)
ARXIV_NOTE_RE = re.compile(rf"\barxiv:\s*({ARXIV_ID_PATTERN})(?:v\d+)?", re.I)
ARXIV_DOI_RE = re.compile(
    rf"^10\.48550/arxiv\.({ARXIV_ID_PATTERN})(?:v\d+)?$", re.I
)
CONFLICT_FIELDS = {"title", "year", "venue", "doi", "arxiv_id"}
LIST_FIELDS = {
    "issn",
    "isbn",
    "keywords",
    "subjects",
    "source",
    "source_id",
    "source_url",
    "retrieval_id",
    "retrieved_at",
    "search_query",
}
RICH_TEXT_FIELDS = {"abstract", "authors"}
COUPLED_FIELDS = {"year", "publication_date", "citation_count", "citation_count_source"}
CONFLICT_REPORT_FIELDS = (
    "paper_id",
    "field",
    "kept_value",
    "other_value",
    "kept_source",
    "other_source",
    "other_input_file",
    "other_input_row",
)
DUPLICATE_EXTRA_FIELDS = ("input_file", "input_row", "duplicate_of", "matched_on")


def normalize_text(value: str) -> str:
    """Backward-compatible title normalizer."""
    return normalize_title(value)


def normalize_doi(value: str) -> str:
    """Backward-compatible DOI normalizer."""
    return canonical_normalize_doi(value)


def valid_doi_key(value: object) -> str:
    """Return only DOI values safe to use as strong identity keys."""
    return doi_identity(value)


def record_year(row: Mapping[str, str]) -> str:
    return normalize_year(row.get("year", "") or row.get("publication_date", ""))


def first_author_key(row: Mapping[str, str]) -> str:
    family, given_name = first_author_signature(row.get("authors", ""))
    return f"{family}:{given_name}" if family else ""


def record_venue(row: Mapping[str, str]) -> str:
    return normalize_title(row.get("venue", ""))


def extract_arxiv(row: Mapping[str, str]) -> str:
    explicit = clean_text(row.get("arxiv_id", ""))
    normalized_explicit = arxiv_identity(explicit)
    if normalized_explicit:
        return normalized_explicit
    match = ARXIV_URL_RE.search(explicit)
    if match:
        return match.group(1).casefold()
    for value in (row.get("url", ""), row.get("notes", "")):
        text = clean_text(value)
        match = ARXIV_URL_RE.search(text) or ARXIV_NOTE_RE.search(text)
        if match:
            return match.group(1).casefold()
    match = ARXIV_DOI_RE.fullmatch(normalize_doi(row.get("doi", "")))
    if match:
        return match.group(1).casefold()
    return ""


def record_keys(row: Mapping[str, str]) -> Iterable[tuple[str, str]]:
    doi = valid_doi_key(row.get("doi", ""))
    if doi:
        yield ("doi", doi)
    arxiv_id = extract_arxiv(row)
    if arxiv_id:
        yield ("arxiv", arxiv_id)
    title = normalize_text(row.get("title", ""))
    if title:
        yield ("title", title)


def score_record(row: Mapping[str, str]) -> int:
    """Prefer richer formal-publication records as merge bases."""
    bibliographic = (
        "title",
        "authors",
        "year",
        "publication_date",
        "venue",
        "publication_type",
        "volume",
        "issue",
        "pages",
        "publisher",
        "issn",
        "url",
        "abstract",
    )
    score = sum(bool(clean_text(row.get(field, ""))) for field in bibliographic)
    if valid_doi_key(row.get("doi", "")):
        score += 7
    venue = clean_text(row.get("venue", "")).casefold()
    url = clean_text(row.get("url", "")).casefold()
    if venue and "arxiv" not in venue:
        score += 4
    if "arxiv.org" in url and not valid_doi_key(row.get("doi", "")):
        score -= 1
    return score


def stable_record_key(row: Mapping[str, str]) -> tuple[str, ...]:
    return (
        valid_doi_key(row.get("doi", "")),
        extract_arxiv(row),
        normalize_title(row.get("title", "")),
        clean_text(row.get("source_id", "")),
        clean_text(row.get("source", "")),
        json.dumps(normalize_record(row), ensure_ascii=False, sort_keys=True),
    )


def split_values(value: object) -> list[str]:
    return [item.strip() for item in clean_text(value).split("; ") if item.strip()]


def union_values(*values: object) -> str:
    unique: dict[str, str] = {}
    for value in values:
        for item in split_values(value):
            unique.setdefault(item.casefold(), item)
    return "; ".join(unique[key] for key in sorted(unique))


class UnionFind:
    def __init__(self, records: Sequence[Mapping[str, str]]) -> None:
        size = len(records)
        self.parent = list(range(size))
        self.rank = [0] * size
        self.dois = [{valid_doi_key(record.get("doi", ""))} - {""} for record in records]
        self.arxiv_ids = [{extract_arxiv(record)} - {""} for record in records]
        self.years = [{record_year(record)} - {""} for record in records]
        self.first_authors = [
            {first_author_signature(record.get("authors", ""))} - {("", "")}
            for record in records
        ]
        self.venues = [{record_venue(record)} - {""} for record in records]

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    @staticmethod
    def _conflict(left: set[str], right: set[str]) -> bool:
        return bool(left and right and left.isdisjoint(right))

    def union(self, left: int, right: int, *, matched_on: str) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return True
        if matched_on != "doi" and self._conflict(
            self.dois[left_root], self.dois[right_root]
        ):
            return False
        if matched_on == "title" and self._conflict(
            self.arxiv_ids[left_root], self.arxiv_ids[right_root]
        ):
            return False
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.dois[left_root].update(self.dois[right_root])
        self.arxiv_ids[left_root].update(self.arxiv_ids[right_root])
        self.years[left_root].update(self.years[right_root])
        self.first_authors[left_root].update(self.first_authors[right_root])
        self.venues[left_root].update(self.venues[right_root])
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1
        return True


@dataclass(frozen=True)
class Entry:
    record: dict[str, str]
    input_file: str = ""
    input_row: int = 0


def choose_paper_id(record: Mapping[str, str], *, fallback_identity: str = "") -> str:
    existing = clean_text(record.get("paper_id", ""))
    if existing:
        return existing
    title = normalize_title(record.get("title", ""))
    author_identity = first_author_key(record)
    venue_identity = "" if author_identity else record_venue(record)
    title_identity = (
        f"title:{title}|year:{record_year(record)}|author:{author_identity}"
        f"|venue:{venue_identity}"
        if title
        else ""
    )
    identity = (
        valid_doi_key(record.get("doi", ""))
        or extract_arxiv(record)
        or title_identity
        or clean_text(record.get("source_id", ""))
        or fallback_identity
        or json.dumps(normalize_record(record), ensure_ascii=False, sort_keys=True)
    )
    return "P" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12].upper()


def merge_raw_metadata(values: Iterable[str]) -> str:
    unique = sorted({clean_text(value) for value in values if clean_text(value)})
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    parsed = []
    for value in unique:
        try:
            parsed.append(json.loads(value))
        except json.JSONDecodeError:
            parsed.append(value)
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)


def merge_component(
    entries: Sequence[Entry],
    *,
    fallback_identity: str = "",
) -> tuple[dict[str, str], list[dict[str, object]]]:
    ordered = sorted(
        entries,
        key=lambda entry: (-score_record(entry.record), stable_record_key(entry.record)),
    )
    base_entry = ordered[0]
    merged = normalize_record(base_entry.record)
    conflicts: list[dict[str, object]] = []

    def add_conflict(
        field: str,
        kept_value: str,
        other_value: str,
        entry: Entry,
        candidate: Mapping[str, str],
    ) -> None:
        conflicts.append(
            {
                "field": field,
                "kept_value": kept_value,
                "other_value": other_value,
                "kept_source": merged.get("source", ""),
                "other_source": candidate.get("source", ""),
                "other_input_file": entry.input_file,
                "other_input_row": entry.input_row,
            }
        )

    for entry in ordered[1:]:
        candidate = normalize_record(entry.record)

        kept_year = record_year(merged)
        other_year = record_year(candidate)
        year_conflict = bool(kept_year and other_year and kept_year != other_year)
        if year_conflict:
            add_conflict("year", kept_year, other_year, entry, candidate)
        elif other_year and not clean_text(merged.get("year")):
            merged["year"] = other_year
        other_date = clean_text(candidate.get("publication_date"))
        kept_date = clean_text(merged.get("publication_date"))
        if not year_conflict and other_date:
            if not kept_date or (
                other_date.startswith(record_year(merged)) and len(other_date) > len(kept_date)
            ):
                merged["publication_date"] = other_date

        kept_count = clean_text(merged.get("citation_count"))
        kept_count_source = clean_text(merged.get("citation_count_source"))
        other_count = clean_text(candidate.get("citation_count"))
        other_count_source = clean_text(candidate.get("citation_count_source"))
        if (
            not kept_count
            and other_count
            and (
                not kept_count_source
                or other_count_source in split_values(kept_count_source)
            )
        ):
            merged["citation_count"] = other_count
            if not kept_count_source:
                merged["citation_count_source"] = other_count_source

        for field in CANONICAL_FIELDS:
            if field in {"paper_id", "raw_metadata"} | COUPLED_FIELDS:
                continue
            kept = clean_text(merged.get(field, ""))
            other = clean_text(candidate.get(field, ""))
            if field == "doi":
                kept_identity = valid_doi_key(kept)
                other_identity = valid_doi_key(other)
                if not kept_identity and other_identity:
                    merged[field] = other_identity
                elif kept_identity and other_identity and kept_identity != other_identity:
                    add_conflict(field, kept, other, entry, candidate)
                continue
            if field == "arxiv_id":
                kept_identity = arxiv_identity(kept)
                other_identity = arxiv_identity(other)
                if not kept_identity and other_identity:
                    merged[field] = other_identity
                elif kept_identity and other_identity and kept_identity != other_identity:
                    add_conflict(field, kept, other, entry, candidate)
                continue
            if not other:
                continue
            if field in LIST_FIELDS:
                merged[field] = union_values(kept, other)
                continue
            if not kept:
                merged[field] = other
                continue
            if kept == other:
                continue
            if field in CONFLICT_FIELDS:
                if field in {"title", "venue"}:
                    comparable_kept = normalize_title(kept)
                    comparable_other = normalize_title(other)
                elif field == "arxiv_id":
                    comparable_kept = extract_arxiv({"arxiv_id": kept})
                    comparable_other = extract_arxiv({"arxiv_id": other})
                else:
                    comparable_kept = kept
                    comparable_other = other
                if comparable_kept != comparable_other:
                    add_conflict(field, kept, other, entry, candidate)
            elif field in RICH_TEXT_FIELDS and len(other) > len(kept):
                merged[field] = other

    for field in LIST_FIELDS:
        merged[field] = union_values(*(entry.record.get(field, "") for entry in ordered))
    merged["raw_metadata"] = merge_raw_metadata(
        entry.record.get("raw_metadata", "") for entry in ordered
    )
    merged["paper_id"] = choose_paper_id(merged, fallback_identity=fallback_identity)
    merged["metadata_status"] = (
        "merged" if len(entries) > 1 else merged.get("metadata_status") or "deduplicated"
    )
    for conflict in conflicts:
        conflict["paper_id"] = merged["paper_id"]
    return normalize_record(merged), conflicts


def merge_entries(
    entries: Sequence[Entry],
) -> tuple[list[dict[str, str]], list[dict[str, object]], list[dict[str, object]]]:
    if not entries:
        return [], [], []
    union_find = UnionFind([entry.record for entry in entries])
    indices_by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, entry in enumerate(entries):
        for key in record_keys(entry.record):
            indices_by_key[key].append(index)

    # Resolve strong identifiers before considering weak title links. For a
    # weak-key group containing contradictory strong identifiers, do not merge
    # any components through that key: assigning an identifier-free record to
    # one side would otherwise be input-order dependent.
    for kind in ("doi", "arxiv", "title"):
        for (key_kind, _), indices in sorted(indices_by_key.items()):
            if key_kind != kind or len(indices) < 2:
                continue
            roots = sorted({union_find.find(index) for index in indices})
            dois = set().union(*(union_find.dois[root] for root in roots))
            arxiv_ids = set().union(*(union_find.arxiv_ids[root] for root in roots))
            years = set().union(*(union_find.years[root] for root in roots))
            first_authors = set().union(
                *(union_find.first_authors[root] for root in roots)
            )
            venues = set().union(*(union_find.venues[root] for root in roots))
            author_evidence_roots = sum(
                bool(union_find.first_authors[root]) for root in roots
            )
            ordered_authors = sorted(first_authors)
            incompatible_authors = any(
                not author_signatures_compatible(left, right)
                for position, left in enumerate(ordered_authors)
                for right in ordered_authors[position + 1 :]
            )
            if kind != "doi" and len(dois) > 1:
                continue
            if kind == "title" and len(arxiv_ids) > 1:
                continue
            if kind == "title" and (len(years) > 1 or incompatible_authors):
                continue
            if (
                kind == "title"
                and author_evidence_roots < len(roots)
                and len(venues) > 1
            ):
                continue
            anchor = indices[0]
            for index in indices[1:]:
                union_find.union(anchor, index, matched_on=kind)

    components: dict[int, list[Entry]] = defaultdict(list)
    for index, entry in enumerate(entries):
        components[union_find.find(index)].append(entry)

    merged_rows: list[dict[str, str]] = []
    duplicate_rows: list[dict[str, object]] = []
    conflict_rows: list[dict[str, object]] = []
    for component_root, component in sorted(components.items()):
        locations = sorted(
            f"{entry.input_file}:{entry.input_row}"
            for entry in component
            if entry.input_file or entry.input_row
        )
        fallback_identity = "|".join(locations) or f"component:{component_root}"
        merged, conflicts = merge_component(
            component,
            fallback_identity=fallback_identity,
        )
        merged_rows.append(merged)
        conflict_rows.extend(conflicts)
        if len(component) == 1:
            continue
        key_counts = Counter(key for entry in component for key in record_keys(entry.record))
        representative = sorted(
            component,
            key=lambda entry: (-score_record(entry.record), stable_record_key(entry.record)),
        )[0]
        for entry in component:
            if entry is representative:
                continue
            duplicate = dict(normalize_record(entry.record))
            duplicate.update(
                {
                    "input_file": entry.input_file,
                    "input_row": entry.input_row,
                    "duplicate_of": merged["paper_id"],
                    "matched_on": "; ".join(
                        f"{kind}:{value}"
                        for kind, value in record_keys(entry.record)
                        if key_counts[(kind, value)] > 1
                    ),
                }
            )
            duplicate_rows.append(duplicate)

    paper_id_counts = Counter(row["paper_id"] for row in merged_rows)
    colliding_ids = sorted(
        paper_id for paper_id, count in paper_id_counts.items() if count > 1
    )
    if colliding_ids:
        preview = ", ".join(colliding_ids[:5])
        suffix = " ..." if len(colliding_ids) > 5 else ""
        raise ValueError(
            "duplicate paper_id values across distinct papers: " + preview + suffix
        )

    merged_rows.sort(
        key=lambda row: (
            row.get("year", ""),
            normalize_title(row.get("title", "")),
            row["paper_id"],
        )
    )
    duplicate_rows.sort(key=lambda row: (clean_text(row["duplicate_of"]), int(row["input_row"])))
    conflict_rows.sort(
        key=lambda row: (
            clean_text(row["paper_id"]),
            clean_text(row["field"]),
            clean_text(row["other_value"]),
        )
    )
    return merged_rows, duplicate_rows, conflict_rows


def deduplicate(
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Backward-compatible in-memory entry point."""
    entries = [Entry(normalize_record(row), input_row=index) for index, row in enumerate(rows, 2)]
    merged, duplicates, _ = merge_entries(entries)
    return merged, [normalize_record(row) for row in duplicates]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Backward-compatible CSV reader."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    """Backward-compatible atomic CSV writer."""
    write_report(path, fieldnames, rows)


def write_report(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
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
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            temporary_path = Path(handle.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def load_entries(paths: Sequence[Path]) -> list[Entry]:
    entries = []
    for path in paths:
        for row_number, record in enumerate(read_canonical_csv(path), start=2):
            entries.append(Entry(record, str(path), row_number))
    return entries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+", help="One or more literature CSV files")
    parser.add_argument("--output", type=Path, default=Path("literature_deduplicated.csv"))
    parser.add_argument("--duplicates", type=Path, default=Path("literature_duplicates.csv"))
    parser.add_argument("--conflicts", type=Path, default=Path("literature_conflicts.csv"))
    parser.add_argument("--log", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        entries = load_entries(args.inputs)
        merged, duplicates, conflicts = merge_entries(entries)
        write_canonical_csv(args.output, merged)
        write_report(
            args.duplicates,
            (*DUPLICATE_EXTRA_FIELDS, *CANONICAL_FIELDS),
            duplicates,
        )
        write_report(args.conflicts, CONFLICT_REPORT_FIELDS, conflicts)
        retrieved_at = utc_now()
        retrieval_id = new_retrieval_id("deduplication", retrieved_at)
        write_search_log(
            args.log or args.output.with_suffix(".merge.json"),
            source="multi_source_merge",
            query=f"merge:{len(args.inputs)} inputs",
            result_count=len(merged),
            output=args.output,
            filters={
                "input_records": len(entries),
                "unique_records": len(merged),
                "duplicates": len(duplicates),
                "conflicts": len(conflicts),
            },
            input_files=args.inputs,
            retrieved_at=retrieved_at,
            retrieval_id=retrieval_id,
            operation="merge_deduplicate",
        )
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f"error: {exc}\n")

    print(
        f"Input: {len(entries)} | Kept: {len(merged)} | "
        f"Duplicates: {len(duplicates)} | Conflicts: {len(conflicts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
