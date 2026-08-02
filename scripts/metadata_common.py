#!/usr/bin/env python3
"""Shared metadata, HTTP, caching, and audit helpers for literature scripts."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from email.message import Message
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import uuid4


CANONICAL_FIELDS = (
    "paper_id",
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
    "isbn",
    "doi",
    "arxiv_id",
    "url",
    "language",
    "abstract",
    "keywords",
    "subjects",
    "citation_count",
    "citation_count_source",
    "is_open_access",
    "source",
    "source_id",
    "source_url",
    "retrieval_id",
    "retrieved_at",
    "search_query",
    "search_rank",
    "metadata_status",
    "method_family",
    "research_problem",
    "core_idea",
    "trust_model",
    "threat_model",
    "datasets",
    "metrics",
    "main_findings",
    "limitations",
    "evidence_level",
    "role",
    "citation_status",
    "notes",
    "raw_metadata",
)
BIBLIOGRAPHIC_IDENTITY_FIELDS = ("title", "doi", "arxiv_id", "source_id", "url")

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
DOI_PREFIX_RE = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", re.I)
DOI_IDENTITY_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
ARXIV_ID_PATTERN = r"(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7})|(?:\d{4}\.\d{4,5})"
ARXIV_VALUE_RE = re.compile(
    rf"^(?:arxiv:\s*|https?://arxiv\.org/(?:abs|pdf)/)?"
    rf"({ARXIV_ID_PATTERN})(?:v\d+)?(?:\.pdf)?(?:[?#].*)?$",
    re.I,
)
YEAR_RE = re.compile(r"(?:^|\D)((?:18|19|20|21)\d{2})(?:\D|$)")
WHITESPACE_RE = re.compile(r"\s+")
SENSITIVE_QUERY_KEYS = {"api_key", "apikey", "access_token", "token", "key"}


class RequestError(RuntimeError):
    """Describe a terminal HTTP or decoding failure without leaking credentials."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class RequestStats:
    network_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    retries: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "network_requests": self.network_requests,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "retries": self.retries,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_retrieval_id(source: str, retrieved_at: str | None = None) -> str:
    """Create an opaque run identifier that contains no query or credential data."""
    stamp = (retrieved_at or utc_now()).replace("-", "").replace(":", "")
    stamp = re.sub(r"[^0-9TZ]", "", stamp)
    source_slug = re.sub(r"[^a-z0-9]+", "-", source.casefold()).strip("-") or "source"
    return f"{source_slug}-{stamp}-{uuid4().hex[:8]}"


def redact_url(url: str) -> str:
    """Remove common credential parameters before caching, logging, or reporting errors."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    redacted = [
        (key, "REDACTED" if key.casefold() in SENSITIVE_QUERY_KEYS else value)
        for key, value in query
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(redacted), parts.fragment)
    )


def clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return WHITESPACE_RE.sub(" ", str(value)).strip()


def normalize_doi(value: object) -> str:
    doi = clean_text(value).lower()
    doi = DOI_PREFIX_RE.sub("", doi).strip()
    return doi


def doi_identity(value: object) -> str:
    """Return a DOI only when it is plausible enough to act as a strong key."""
    doi = normalize_doi(value)
    return doi if DOI_IDENTITY_RE.fullmatch(doi) else ""


def arxiv_identity(value: object) -> str:
    """Return a normalized arXiv ID only for recognized identifier shapes."""
    match = ARXIV_VALUE_RE.fullmatch(clean_text(value))
    return match.group(1).casefold() if match else ""


def normalize_year(value: object) -> str:
    text = clean_text(value)
    match = YEAR_RE.search(text)
    return match.group(1) if match else ""


def normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", clean_text(value)).casefold()
    return re.sub(r"[^\w\u3400-\u9fff]+", "", text)


def first_author_signature(value: object) -> tuple[str, str]:
    """Return a normalized ``(family_name, given_name)`` author signature."""
    first = re.split(r"\s*[;；]\s*", clean_text(value), maxsplit=1)[0]
    if not first:
        return "", ""
    if "," in first:
        family_text, given_text = first.split(",", 1)
        family_parts = family_text.split()
        family = family_parts[-1] if family_parts else family_text
        given_parts = given_text.split()
        given = normalize_title(given_parts[0] if given_parts else given_text)
    else:
        parts = first.split()
        if len(parts) == 1:
            return normalize_title(first), ""
        family = parts[-1]
        given = normalize_title(parts[0])
    return normalize_title(family), given


def author_signatures_compatible(
    left: tuple[str, str], right: tuple[str, str]
) -> bool:
    left_family, left_given = left
    right_family, right_given = right
    if not left_family or not right_family:
        return True
    if left_family != right_family:
        return False
    if not left_given or not right_given or left_given == right_given:
        return True
    if len(left_given) == 1 or len(right_given) == 1:
        return left_given[0] == right_given[0]
    return False


def compatible_first_authors(left: object, right: object) -> bool:
    return author_signatures_compatible(
        first_author_signature(left), first_author_signature(right)
    )


def normalize_boolean(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = clean_text(value).casefold()
    if text in {"1", "true", "yes", "y", "是", "开放"}:
        return "true"
    if text in {"0", "false", "no", "n", "否", "非开放"}:
        return "false"
    return text


def normalize_record(record: Mapping[str, object]) -> dict[str, str]:
    normalized = {field: clean_text(record.get(field, "")) for field in CANONICAL_FIELDS}
    normalized["doi"] = normalize_doi(record.get("doi", ""))
    normalized["year"] = normalize_year(record.get("year", ""))
    normalized["is_open_access"] = normalize_boolean(record.get("is_open_access", ""))
    return normalized


def has_bibliographic_identity(record: Mapping[str, object]) -> bool:
    return bool(
        clean_text(record.get("title"))
        or doi_identity(record.get("doi"))
        or arxiv_identity(record.get("arxiv_id"))
        or clean_text(record.get("source_id"))
        or clean_text(record.get("url"))
    )


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace a text file only after its complete content has been written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(text)
            temporary_path = Path(handle.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def read_canonical_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        recognized = set(reader.fieldnames) & set(CANONICAL_FIELDS)
        if not recognized.intersection(BIBLIOGRAPHIC_IDENTITY_FIELDS):
            raise ValueError(f"CSV has no recognized bibliographic identity header: {path}")
        records = []
        for row_number, row in enumerate(reader, start=2):
            if not any(clean_text(value) for value in row.values()):
                continue
            record = normalize_record(row)
            if not has_bibliographic_identity(record):
                raise ValueError(
                    f"CSV row {row_number} has no bibliographic identity: {path}"
                )
            records.append(record)
        return records


def write_canonical_csv(path: Path, records: Iterable[Mapping[str, object]]) -> int:
    rows = [normalize_record(record) for record in records]
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
            writer = csv.DictWriter(
                handle, fieldnames=CANONICAL_FIELDS, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)
            temporary_path = Path(handle.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return len(rows)


def append_unique(existing: object, new_value: object, separator: str = "; ") -> str:
    values: list[str] = []
    for value in (clean_text(existing), clean_text(new_value)):
        for item in value.split(separator):
            item = item.strip()
            if item and item not in values:
                values.append(item)
    return separator.join(values)


class JsonHttpClient:
    """Fetch JSON with polite pacing, retry, and an optional filesystem cache."""

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        cache_ttl_seconds: int = 7 * 24 * 60 * 60,
        max_retries: int = 4,
        backoff_seconds: float = 1.0,
        min_interval_seconds: float = 0.1,
        timeout_seconds: float = 30.0,
        user_agent: str = "academic-survey-skill/0.3",
        urlopen: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds cannot be negative")
        self.cache_dir = cache_dir
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_retries = max_retries
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.urlopen = urlopen or urllib.request.urlopen
        self.sleep = sleep
        self.wall_time = wall_time
        self.monotonic = monotonic
        self.stats = RequestStats()
        self._last_request_at: float | None = None

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(redact_url(url).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> Any | None:
        cache_path = self._cache_path(url)
        if cache_path is None or not cache_path.is_file():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            fetched_at = float(payload["fetched_at"])
            if self.wall_time() - fetched_at > self.cache_ttl_seconds:
                return None
            if payload.get("url") != redact_url(url):
                return None
            self.stats.cache_hits += 1
            return payload["data"]
        except (KeyError, TypeError, ValueError, OSError):
            return None

    def _write_cache(self, url: str, data: Any) -> None:
        cache_path = self._cache_path(url)
        if cache_path is None:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": self.wall_time(), "url": redact_url(url), "data": data}
        atomic_write_text(
            cache_path,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def _pace(self) -> None:
        now = self.monotonic()
        if self._last_request_at is not None:
            remaining = self.min_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at = self.monotonic()

    def _retry_delay(self, attempt: int, headers: Message | Mapping[str, str] | None) -> float:
        retry_after = headers.get("Retry-After") if headers is not None else None
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(retry_after)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return max(0.0, parsed.timestamp() - self.wall_time())
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(60.0, self.backoff_seconds * (2**attempt))

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        use_cache: bool = True,
        refresh: bool = False,
    ) -> Any:
        if use_cache:
            if not refresh:
                cached = self._read_cache(url)
                if cached is not None:
                    return cached
            self.stats.cache_misses += 1

        request_headers = {"Accept": "application/json", "User-Agent": self.user_agent}
        if headers:
            request_headers.update(headers)

        for attempt in range(self.max_retries + 1):
            self._pace()
            request = urllib.request.Request(url, headers=request_headers)
            try:
                self.stats.network_requests += 1
                with self.urlopen(request, timeout=self.timeout_seconds) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if use_cache:
                    self._write_cache(url, data)
                return data
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS_CODES or attempt >= self.max_retries:
                    raise RequestError(
                        f"HTTP {exc.code} while requesting {redact_url(url)}", status=exc.code
                    ) from exc
                self.stats.retries += 1
                self.sleep(self._retry_delay(attempt, exc.headers))
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                json.JSONDecodeError,
            ) as exc:
                if attempt >= self.max_retries:
                    raise RequestError(f"request failed for {redact_url(url)}") from exc
                self.stats.retries += 1
                self.sleep(self._retry_delay(attempt, None))

        raise AssertionError("unreachable")


def write_search_log(
    path: Path,
    *,
    source: str,
    query: str,
    result_count: int,
    output: Path,
    filters: Mapping[str, object] | None = None,
    request_stats: RequestStats | None = None,
    input_files: Sequence[Path] = (),
    retrieved_at: str | None = None,
    retrieval_id: str | None = None,
    operation: str = "retrieve",
    warnings: Sequence[str] = (),
) -> None:
    stamp = retrieved_at or utc_now()
    payload = {
        "schema_version": "1.0",
        "run_id": retrieval_id,
        "operation": operation,
        "source": source,
        "query": query,
        "filters": dict(filters or {}),
        "retrieved_at": stamp,
        "result_count": result_count,
        "output": str(output),
        "input_files": [str(item) for item in input_files],
        "requests": request_stats.as_dict() if request_stats else None,
        "warnings": list(warnings),
    }
    if path.suffix.casefold() == ".jsonl":
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
