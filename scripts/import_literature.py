#!/usr/bin/env python3
"""Import CSV/TSV or RIS literature exports into the canonical survey schema."""

from __future__ import annotations

import argparse
import codecs
import csv
import io
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from metadata_common import (
    CANONICAL_FIELDS,
    append_unique,
    arxiv_identity,
    clean_text,
    doi_identity,
    new_retrieval_id,
    normalize_doi,
    normalize_title,
    normalize_year,
    utc_now,
    write_canonical_csv,
    write_search_log,
)


RIS_LINE_RE = re.compile(r"^(?P<tag>[A-Z0-9]{2})  - ?(?P<value>.*)$")
HEADER_NORMALIZE_RE = re.compile(r"[^0-9a-z\u3400-\u9fff]+")
MULTIVALUE_FIELDS = {"authors", "keywords", "subjects", "datasets", "metrics"}
CONFLICT_FIELDS = {"title", "year", "venue", "doi", "arxiv_id"}


def normalize_header(value: object) -> str:
    """Normalize an exported column name for tolerant alias matching."""

    text = unicodedata.normalize("NFKC", clean_text(value)).casefold()
    return HEADER_NORMALIZE_RE.sub("", text)


def _build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {
        normalize_header(field): field for field in CANONICAL_FIELDS
    }
    groups: Mapping[str, Sequence[str]] = {
        "title": (
            "paper title",
            "article title",
            "document title",
            "题名",
            "标题",
            "论文题目",
            "篇名",
            "文献题名",
            "文题",
            "Title-题名",
        ),
        "authors": (
            "author",
            "author(s)",
            "creator",
            "作者",
            "全部作者",
            "责任者",
            "Author-作者",
        ),
        "year": (
            "publication year",
            "pub year",
            "pubyear",
            "年份",
            "年",
            "年度",
            "发表年份",
            "发表年度",
            "出版年",
            "Year-年",
        ),
        "publication_date": (
            "date",
            "publication date",
            "published",
            "发表日期",
            "发表时间",
            "出版日期",
            "出版时间",
            "日期",
            "PubTime-发表时间",
        ),
        "venue": (
            "journal",
            "journal title",
            "source",
            "source title",
            "publication",
            "conference",
            "刊名",
            "期刊",
            "期刊名",
            "期刊名称",
            "来源",
            "来源出版物",
            "文献来源",
            "会议",
            "会议名称",
            "Source-文献来源",
        ),
        "publication_type": (
            "type",
            "document type",
            "publication type",
            "literature type",
            "文献类型",
            "资源类型",
            "类型",
        ),
        "doi": (
            "doi number",
            "doi号",
            "doi地址",
            "数字对象唯一标识符",
        ),
        "arxiv_id": ("arxiv", "arxiv id", "arxiv identifier"),
        "url": (
            "link",
            "record url",
            "full text url",
            "full text link",
            "链接",
            "网址",
            "原文链接",
            "全文链接",
            "下载地址",
        ),
        "source_url": ("database url", "数据库链接", "来源链接"),
        "language": ("lang", "语种", "语言"),
        "abstract": ("summary", "摘要", "文摘", "内容提要", "Summary-摘要"),
        "keywords": (
            "keyword",
            "key words",
            "author keywords",
            "关键词",
            "关键字",
            "主题词",
            "Keyword-关键词",
        ),
        "citation_count": (
            "cited by",
            "cited by count",
            "times cited",
            "被引频次",
            "被引量",
            "被引次数",
            "引证次数",
        ),
        "is_open_access": ("oa", "open access", "是否开放获取", "开放获取"),
        "source_id": (
            "id",
            "record id",
            "accession number",
            "cnki id",
            "cnki编号",
            "万方id",
            "万方编号",
            "文献id",
            "记录号",
        ),
        "volume": ("vol", "卷", "卷号", "Roll-卷"),
        "issue": ("number", "no", "期", "期号", "Period-期"),
        "pages": (
            "page",
            "page range",
            "页码",
            "页码范围",
            "起止页",
            "PageCount-页码",
        ),
        "publisher": ("出版者", "出版社", "出版单位"),
        "issn": ("国际标准刊号",),
        "isbn": ("国际标准书号",),
        "notes": ("note", "备注", "附注"),
    }
    for field, names in groups.items():
        for name in names:
            aliases[normalize_header(name)] = field
    return aliases


FIELD_ALIASES = _build_aliases()


def field_for_header(header: object) -> str | None:
    normalized = normalize_header(header)
    if not normalized:
        return None
    field = FIELD_ALIASES.get(normalized)
    if field:
        return field
    if re.fullmatch(r"(?:author|作者)\d*", normalized):
        return "authors"
    if re.fullmatch(r"(?:keyword|keywords|关键词|关键字)\d*", normalized):
        return "keywords"
    return None


def decode_input(path: Path, requested_encoding: str) -> tuple[str, str]:
    data = path.read_bytes()
    if requested_encoding.casefold() != "auto":
        encoding = codecs.lookup(requested_encoding).name
        return data.decode(encoding), encoding

    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith(codecs.BOM_UTF32_LE) or data.startswith(codecs.BOM_UTF32_BE):
        return data.decode("utf-32"), "utf-32"
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data.decode("utf-16"), "utf-16"

    errors: list[str] = []
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    raise UnicodeError("cannot decode input as UTF-8, GB18030, or Big5: " + "; ".join(errors))


def detect_format(path: Path, text: str, requested_format: str) -> str:
    if requested_format != "auto":
        return requested_format
    if path.suffix.casefold() == ".ris":
        return "ris"
    nonempty_lines = [line for line in text.splitlines() if line.strip()][:25]
    if any(RIS_LINE_RE.match(line) and line.startswith(("TY", "ER")) for line in nonempty_lines):
        return "ris"
    return "csv"


def resolve_delimiter(requested: str, text: str, path: Path) -> str:
    named = {
        "tab": "\t",
        "\\t": "\t",
        "comma": ",",
        "semicolon": ";",
        "pipe": "|",
    }
    if requested != "auto":
        delimiter = named.get(requested.casefold(), requested)
        if len(delimiter) != 1:
            raise ValueError(
                "--delimiter must be auto, tab, comma, semicolon, pipe, or one character"
            )
        return delimiter

    separator_directive = re.match(r"^sep=(.)\r?\n", text, flags=re.I)
    if separator_directive:
        return separator_directive.group(1)

    sample = text[:65536]
    if sample.strip():
        try:
            return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
        except csv.Error:
            pass
    if path.suffix.casefold() in {".tsv", ".tab"}:
        return "\t"
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    counts = {item: first_line.count(item) for item in (",", "\t", ";", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _conflict_key(field: str, value: object) -> str:
    if field == "doi":
        return normalize_doi(value)
    if field == "year":
        return normalize_year(value)
    if field in {"title", "venue"}:
        return normalize_title(value)
    return unicodedata.normalize("NFKC", clean_text(value)).casefold()


def _mark_import_issue(record: dict[str, object], status: str, note: str) -> None:
    current_status = clean_text(record.get("metadata_status"))
    if status == "import_conflict" or not current_status:
        record["metadata_status"] = status
    record["notes"] = append_unique(record.get("notes", ""), note)


def _merge_field(record: dict[str, object], field: str, value: object) -> None:
    cleaned = clean_text(value)
    if not cleaned:
        return
    if field in MULTIVALUE_FIELDS:
        cleaned = re.sub(r"\s*[;；|\n]+\s*", "; ", cleaned)
        record[field] = append_unique(record.get(field, ""), cleaned)
        return

    existing = clean_text(record.get(field, ""))
    if not existing:
        record[field] = cleaned
    elif field in CONFLICT_FIELDS and _conflict_key(field, existing) != _conflict_key(
        field, cleaned
    ):
        _mark_import_issue(record, "import_conflict", f"import_conflict:{field}")


def has_import_identity(record: Mapping[str, object]) -> bool:
    return bool(
        clean_text(record.get("title"))
        or doi_identity(record.get("doi"))
        or arxiv_identity(record.get("arxiv_id"))
        or clean_text(record.get("source_id"))
        or clean_text(record.get("url"))
    )


def _raw_csv_row(headers: Sequence[str], values: Sequence[str]) -> dict[str, object]:
    raw: dict[str, object] = {}
    for index, header in enumerate(headers):
        key = header or f"column_{index + 1}"
        value = values[index] if index < len(values) else ""
        existing = raw.get(key)
        if existing is None:
            raw[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            raw[key] = [existing, value]
    if len(values) > len(headers):
        raw["__extra__"] = list(values[len(headers) :])
    return raw


def parse_csv_records(text: str, delimiter: str) -> list[dict[str, object]]:
    text = re.sub(r"^sep=.\r?\n", "", text, count=1, flags=re.I)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        headers = next(reader)
    except StopIteration:
        return []

    mapped_fields = [field_for_header(header) for header in headers]
    if not any(field and field != "raw_metadata" for field in mapped_fields):
        raise ValueError("CSV header does not contain recognized bibliographic fields")

    records: list[dict[str, object]] = []
    for values in reader:
        if not any(clean_text(value) for value in values):
            continue
        raw = _raw_csv_row(headers, values)
        record: dict[str, object] = {}
        for index, field in enumerate(mapped_fields):
            if index >= len(values):
                break
            if field and field != "raw_metadata":
                _merge_field(record, field, values[index])
        record["raw_metadata"] = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        if has_import_identity(record):
            records.append(record)
    return records


def parse_ris_blocks(text: str) -> list[dict[str, list[str]]]:
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    current_tag: str | None = None

    def flush() -> None:
        nonlocal current, current_tag
        if current:
            records.append(current)
        current = {}
        current_tag = None

    for raw_line in text.splitlines():
        match = RIS_LINE_RE.match(raw_line)
        if match:
            tag = match.group("tag")
            value = match.group("value").strip()
            if tag == "TY" and current:
                flush()
            if tag == "ER":
                flush()
                continue
            current.setdefault(tag, []).append(value)
            current_tag = tag
        elif raw_line.strip() and current_tag and current.get(current_tag):
            continuation = raw_line.strip()
            current[current_tag][-1] = f"{current[current_tag][-1]}\n{continuation}".strip()
    flush()
    return records


def _first(tags: Mapping[str, Sequence[str]], *names: str) -> str:
    for name in names:
        for value in tags.get(name, ()):  # pragma: no branch - tiny ordered lookup
            if clean_text(value):
                return clean_text(value)
    return ""


def _joined(tags: Mapping[str, Sequence[str]], *names: str) -> str:
    result = ""
    for name in names:
        for value in tags.get(name, ()):
            result = append_unique(result, value)
    return result


RIS_TYPES = {
    "JOUR": "journal article",
    "MGZN": "magazine article",
    "NEWS": "newspaper article",
    "CONF": "conference proceeding",
    "CPAPER": "conference paper",
    "BOOK": "book",
    "CHAP": "book chapter",
    "THES": "thesis",
    "RPRT": "report",
    "ELEC": "electronic resource",
    "GEN": "generic",
    "UNPB": "unpublished work",
}


def _raw_ris_record(tags: Mapping[str, Sequence[str]]) -> dict[str, object]:
    return {
        tag: values[0] if len(values) == 1 else list(values)
        for tag, values in tags.items()
    }


def parse_ris_records(text: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for tags in parse_ris_blocks(text):
        publication_date = _first(tags, "PY", "Y1", "YR", "DA")
        ris_type = _first(tags, "TY")
        serial_number = _first(tags, "SN")
        url = _first(tags, "UR", "L1", "L2")
        record: dict[str, object] = {
            "title": _first(tags, "TI", "T1", "CT", "BT"),
            "authors": _joined(tags, "AU") or _joined(tags, "A1"),
            "year": publication_date,
            "publication_date": publication_date,
            "venue": _first(tags, "T2", "JF", "JO", "JA"),
            "publication_type": _first(tags, "M3") or RIS_TYPES.get(ris_type, ris_type),
            "volume": _first(tags, "VL"),
            "issue": _first(tags, "IS"),
            "pages": ris_pages(tags),
            "publisher": _first(tags, "PB"),
            "issn": serial_number if ris_type in {"JOUR", "MGZN", "NEWS"} else "",
            "isbn": serial_number if ris_type in {"BOOK", "CHAP"} else "",
            "doi": _first(tags, "DO", "DI", "DOI"),
            "url": url,
            "language": _first(tags, "LA"),
            "abstract": _joined(tags, "AB", "N2"),
            "keywords": _joined(tags, "KW", "K1"),
            "source_id": _first(tags, "ID", "AN"),
            "source_url": url,
            "notes": _joined(tags, "N1"),
            "raw_metadata": json.dumps(
                _raw_ris_record(tags), ensure_ascii=False, sort_keys=True
            ),
        }
        records.append(record)
    return records


def normalize_publication_date(value: object) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value))
    year_pattern = r"(?:18|19|20|21)\d{2}"

    match = re.fullmatch(
        rf"({year_pattern})\s*[-/]\s*(\d{{1,2}})\s*[-/]\s*(\d{{1,2}})\s*/?",
        text,
    ) or re.fullmatch(
        rf"({year_pattern})\s*年\s*(\d{{1,2}})\s*月\s*(\d{{1,2}})\s*日?",
        text,
    )
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            normalized = date(year, month, day)
        except ValueError:
            return ""
        return normalized.isoformat()

    match = re.fullmatch(
        rf"({year_pattern})\s*[-/]\s*(\d{{1,2}})\s*/?", text
    ) or re.fullmatch(rf"({year_pattern})\s*年\s*(\d{{1,2}})\s*月", text)
    if match:
        year, month = (int(part) for part in match.groups())
        try:
            date(year, month, 1)
        except ValueError:
            return ""
        return f"{year:04d}-{month:02d}"

    match = re.fullmatch(rf"({year_pattern})(?:\s*/{{1,3}})?", text)
    if match:
        return match.group(1)

    # A year-first value with date separators was intended as a date; do not
    # silently downgrade an invalid month or day to year precision.
    if re.match(rf"^{year_pattern}\s*[-/年]", text):
        return ""
    return normalize_year(text)


def ris_pages(tags: Mapping[str, Sequence[str]]) -> str:
    start = _first(tags, "SP")
    end = _first(tags, "EP")
    if start and end and start != end:
        return f"{start}-{end}"
    return start or end


def add_import_context(
    records: Iterable[dict[str, object]],
    *,
    source: str,
    query: str,
    retrieved_at: str,
    retrieval_id: str,
) -> list[dict[str, object]]:
    imported: list[dict[str, object]] = []
    for record in records:
        if not has_import_identity(record):
            continue
        contextual = dict(record)
        source_id = clean_text(contextual.get("source_id"))
        original_date = clean_text(contextual.get("publication_date"))
        if original_date:
            normalized_date = normalize_publication_date(original_date)
            contextual["publication_date"] = normalized_date
            contextual["year"] = normalize_year(
                contextual.get("year") or original_date
            )
            if not normalized_date:
                _mark_import_issue(
                    contextual,
                    "import_warning",
                    "import_warning:invalid_publication_date",
                )
        language = clean_text(contextual.get("language")).casefold()
        if language in {"中文", "汉语", "chinese", "zh-cn", "zh_cn"}:
            contextual["language"] = "zh"
        elif language in {"英文", "英语", "english", "en-us", "en_us"}:
            contextual["language"] = "en"
        contextual.update(
            {
                "source": source,
                "source_id": f"{source}:{source_id}" if source_id else "",
                "retrieval_id": retrieval_id,
                "retrieved_at": retrieved_at,
                "search_query": query,
                "search_rank": len(imported) + 1,
                "metadata_status": clean_text(contextual.get("metadata_status"))
                or "imported",
            }
        )
        if clean_text(contextual.get("citation_count")):
            contextual["citation_count_source"] = source
        imported.append(contextual)
    return imported


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="CSV/TSV or RIS export to import")
    parser.add_argument("--source", required=True, help="provenance label, e.g. cnki or wanfang")
    parser.add_argument("--format", choices=("auto", "csv", "ris"), default="auto")
    parser.add_argument(
        "--encoding",
        default="auto",
        help="auto, utf-8-sig, utf-8, gb18030, utf-16, big5, or another Python codec",
    )
    parser.add_argument(
        "--delimiter",
        default="auto",
        help="CSV delimiter: auto, tab, comma, semicolon, pipe, or one character",
    )
    parser.add_argument("--query", default="", help="query used to create the source export")
    parser.add_argument("--output", type=Path, default=Path("imported_literature.csv"))
    parser.add_argument("--log", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.source.strip():
        parser.error("--source cannot be empty")

    try:
        text, encoding = decode_input(args.input, args.encoding)
        source_format = detect_format(args.input, text, args.format)
        delimiter: str | None = None
        if source_format == "ris":
            source_records = parse_ris_records(text)
        else:
            delimiter = resolve_delimiter(args.delimiter, text, args.input)
            source_records = parse_csv_records(text, delimiter)
    except (LookupError, OSError, UnicodeError, ValueError, csv.Error) as exc:
        parser.exit(2, f"error: {exc}\n")

    retrieved_at = utc_now()
    retrieval_id = new_retrieval_id(args.source.strip(), retrieved_at)
    records = add_import_context(
        source_records,
        source=args.source.strip(),
        query=args.query,
        retrieved_at=retrieved_at,
        retrieval_id=retrieval_id,
    )
    count = write_canonical_csv(args.output, records)
    log_path = args.log or args.output.with_suffix(".search.json")
    write_search_log(
        log_path,
        source=args.source.strip(),
        query=args.query,
        result_count=count,
        output=args.output,
        filters={
            "format": source_format,
            "encoding": encoding,
            "delimiter": "\\t" if delimiter == "\t" else delimiter,
        },
        input_files=(args.input,),
        retrieved_at=retrieved_at,
        retrieval_id=retrieval_id,
        operation="import",
    )
    print(
        f"Imported {count} {source_format.upper()} records from {args.input} "
        f"to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
