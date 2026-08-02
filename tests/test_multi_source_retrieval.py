from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPOSITORY_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import crossref_metadata  # noqa: E402
import deduplicate_literature  # noqa: E402
import dblp_validate  # noqa: E402
import import_literature  # noqa: E402
import openalex_search  # noqa: E402
import semantic_scholar_search  # noqa: E402
from metadata_common import (  # noqa: E402
    CANONICAL_FIELDS,
    JsonHttpClient,
    RequestError,
    doi_identity,
    normalize_doi,
    write_canonical_csv,
    write_search_log,
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class ResettingResponse(FakeResponse):
    def read(self) -> bytes:
        raise ConnectionResetError("peer reset during response body")


class QueueClient:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = list(payloads)
        self.calls: list[tuple[str, object, bool]] = []

    def get_json(
        self,
        url: str,
        *,
        headers: object = None,
        use_cache: bool = True,
        refresh: bool = False,
    ) -> object:
        self.calls.append((url, headers, use_cache))
        return self.payloads.pop(0)


class HttpClientTests(unittest.TestCase):
    def test_retries_connection_reset_while_reading_response(self) -> None:
        calls = 0

        def urlopen(request: object, timeout: float) -> FakeResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                return ResettingResponse({"ignored": True})
            return FakeResponse({"ok": True})

        client = JsonHttpClient(
            urlopen=urlopen,
            sleep=lambda _: None,
            min_interval_seconds=0,
        )
        self.assertEqual(
            client.get_json("https://example.test/data", use_cache=False),
            {"ok": True},
        )
        self.assertEqual(calls, 2)
        self.assertEqual(client.stats.network_requests, 2)
        self.assertEqual(client.stats.retries, 1)

    def test_normalize_doi_preserves_legal_trailing_punctuation(self) -> None:
        self.assertEqual(
            normalize_doi("https://doi.org/10.1002/(sici)example)"),
            "10.1002/(sici)example)",
        )
        self.assertEqual(normalize_doi("doi:10.1000/example;"), "10.1000/example;")
        self.assertEqual(doi_identity("N/A"), "")
        self.assertEqual(doi_identity("-"), "")
        self.assertEqual(doi_identity("无"), "")
        self.assertEqual(doi_identity("https://doi.org/10.1000/Example"), "10.1000/example")

    def test_retries_429_then_uses_secret_free_cache(self) -> None:
        calls = []

        def urlopen(request: object, timeout: float) -> FakeResponse:
            calls.append((request, timeout))
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    {"Retry-After": "0"},
                    None,
                )
            return FakeResponse({"ok": True})

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "cache"
            client = JsonHttpClient(
                cache_dir=cache_dir,
                urlopen=urlopen,
                sleep=lambda _: None,
                wall_time=lambda: 1000.0,
                min_interval_seconds=0,
            )
            url = "https://example.test/works?query=x&api_key=super-secret"
            self.assertEqual(client.get_json(url), {"ok": True})
            self.assertEqual(client.get_json(url), {"ok": True})
            self.assertEqual(len(calls), 2)
            self.assertEqual(client.stats.retries, 1)
            self.assertEqual(client.stats.cache_hits, 1)
            cache_text = next(cache_dir.glob("*.json")).read_text(encoding="utf-8")
            self.assertNotIn("super-secret", cache_text)
            self.assertIn("REDACTED", cache_text)

    def test_does_not_retry_401_or_leak_api_key_in_error(self) -> None:
        calls = 0

        def urlopen(request: object, timeout: float) -> FakeResponse:
            nonlocal calls
            calls += 1
            raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

        client = JsonHttpClient(
            urlopen=urlopen,
            sleep=lambda _: None,
            min_interval_seconds=0,
        )
        with self.assertRaises(RequestError) as captured:
            client.get_json("https://example.test?api_key=do-not-print", use_cache=False)
        self.assertEqual(calls, 1)
        self.assertNotIn("do-not-print", str(captured.exception))
        self.assertEqual(captured.exception.status, 401)

    def test_refresh_bypasses_and_replaces_cached_response(self) -> None:
        payloads = iter(({"version": 1}, {"version": 2}))
        calls = 0

        def urlopen(request: object, timeout: float) -> FakeResponse:
            nonlocal calls
            calls += 1
            return FakeResponse(next(payloads))

        with tempfile.TemporaryDirectory() as temp_dir:
            client = JsonHttpClient(
                cache_dir=Path(temp_dir),
                urlopen=urlopen,
                wall_time=lambda: 1000.0,
                min_interval_seconds=0,
            )
            url = "https://example.test/data"
            self.assertEqual(client.get_json(url), {"version": 1})
            self.assertEqual(client.get_json(url, refresh=True), {"version": 2})
            self.assertEqual(client.get_json(url), {"version": 2})
            self.assertEqual(calls, 2)

    def test_failed_row_generation_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "records.csv"
            output.write_text("preserve\n", encoding="utf-8")

            def records() -> object:
                yield {"title": "first"}
                raise ValueError("later page failed")

            with self.assertRaises(ValueError):
                write_canonical_csv(output, records())
            self.assertEqual(output.read_text(encoding="utf-8"), "preserve\n")

    def test_jsonl_search_log_appends_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "search_log.jsonl"
            for run_id in ("run-1", "run-2"):
                write_search_log(
                    path,
                    source="fixture",
                    query="exact query",
                    result_count=1,
                    output=Path("out.csv"),
                    retrieval_id=run_id,
                )
            lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([line["run_id"] for line in lines], ["run-1", "run-2"])
            self.assertTrue(all(line["query"] == "exact query" for line in lines))


class SourceAdapterTests(unittest.TestCase):
    def test_three_english_sources_and_one_chinese_import_merge_end_to_end(self) -> None:
        stamp = "2026-08-01T00:00:00Z"
        openalex = openalex_search.flatten_work(
            {
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.1000/shared",
                "title": "A Shared Topic Survey",
                "publication_year": 2025,
                "authorships": [],
                "primary_location": {"source": {"display_name": "Journal"}},
            },
            query="shared topic",
            rank=1,
            retrieved_at=stamp,
            retrieval_id="oa-run",
        )
        semantic_scholar = semantic_scholar_search.flatten_paper(
            {
                "paperId": "S1",
                "externalIds": {"DOI": "10.1000/shared"},
                "title": "A Shared Topic Survey",
                "authors": [],
            },
            query="shared topic",
            rank=1,
            retrieved_at=stamp,
            retrieval_id="s2-run",
        )
        crossref = crossref_metadata.flatten_work(
            {
                "DOI": "10.1000/shared",
                "title": ["A Shared Topic Survey"],
                "published": {"date-parts": [[2025]]},
            },
            query="shared topic",
            rank=1,
            retrieved_at=stamp,
            retrieval_id="cr-run",
        )
        chinese_source = import_literature.parse_csv_records(
            "题名,作者,年份,DOI\n中文主题综述,张三,2024,10.1000/chinese\n",
            ",",
        )
        chinese = import_literature.add_import_context(
            chinese_source,
            source="cnki",
            query="主题 综述",
            retrieved_at=stamp,
            retrieval_id="cn-run",
        )[0]

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = []
            for name, record in (
                ("openalex", openalex),
                ("semantic_scholar", semantic_scholar),
                ("crossref", crossref),
                ("cnki", chinese),
            ):
                path = Path(temp_dir) / f"{name}.csv"
                write_canonical_csv(path, [record])
                paths.append(path)
            merged, duplicates, _ = deduplicate_literature.merge_entries(
                deduplicate_literature.load_entries(paths)
            )

        self.assertEqual(len(merged), 2)
        self.assertEqual(len(duplicates), 2)
        english = next(row for row in merged if row["doi"] == "10.1000/shared")
        self.assertEqual(
            english["source"], "crossref; openalex; semantic_scholar"
        )
        self.assertEqual(english["retrieval_id"], "cr-run; oa-run; s2-run")
        self.assertTrue(any(row["source"] == "cnki" for row in merged))

    def test_openalex_uses_current_parameters_and_flattens_topics(self) -> None:
        payload = {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "doi": "https://doi.org/10.1000/ABC",
                    "title": "Trusted Learning",
                    "publication_year": 2025,
                    "publication_date": "2025-02-03",
                    "type": "article",
                    "language": "en",
                    "cited_by_count": 7,
                    "authorships": [{"author": {"display_name": "A. Author"}}],
                    "primary_location": {
                        "landing_page_url": "https://example.test/paper",
                        "is_oa": True,
                        "source": {"display_name": "Example Journal"},
                    },
                    "open_access": {"is_oa": True},
                    "abstract_inverted_index": {"hello": [0], "world": [1]},
                    "topics": [{"display_name": "Federated Learning"}],
                }
            ],
            "meta": {"next_cursor": None},
        }
        client = QueueClient([payload])
        works = list(
            openalex_search.iter_works(
                client,
                query="trusted learning",
                from_year=2020,
                to_year=2026,
                max_results=1,
                api_key="secret-key",
            )
        )
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(client.calls[0][0]).query)
        self.assertEqual(params["per_page"], ["1"])
        self.assertNotIn("per-page", params)
        self.assertIn("topics", params["select"][0])
        self.assertNotIn("concepts", params["select"][0])
        self.assertNotIn("host_venue", params["select"][0])
        row = openalex_search.flatten_work(
            works[0],
            query="trusted learning",
            rank=1,
            retrieved_at="2026-08-01T00:00:00Z",
            retrieval_id="oa-run",
        )
        self.assertEqual(row["source_id"], "openalex:W1")
        self.assertEqual(row["subjects"], "Federated Learning")
        self.assertEqual(row["abstract"], "hello world")
        self.assertEqual(row["citation_count_source"], "openalex")

    def test_semantic_scholar_paginates_with_next_and_key_header(self) -> None:
        client = QueueClient(
            [
                {"next": 1, "data": [{"paperId": "S1", "title": "One"}]},
                {"data": [{"paperId": "S2", "title": "Two"}]},
            ]
        )
        papers = list(
            semantic_scholar_search.iter_papers(
                client,
                query="graph survey",
                from_year=None,
                to_year=None,
                max_results=2,
                api_key="s2-key",
            )
        )
        self.assertEqual([paper["paperId"] for paper in papers], ["S1", "S2"])
        self.assertEqual(client.calls[0][1], {"x-api-key": "s2-key"})
        second_params = urllib.parse.parse_qs(urllib.parse.urlsplit(client.calls[1][0]).query)
        self.assertEqual(second_params["offset"], ["1"])
        row = semantic_scholar_search.flatten_paper(
            {
                "paperId": "S1",
                "title": "One",
                "externalIds": {"DOI": "10.1000/ONE"},
                "authors": [{"name": "Researcher"}],
                "fieldsOfStudy": ["Computer Science"],
                "isOpenAccess": False,
            },
            query="graph survey",
            rank=1,
            retrieved_at="2026-08-01T00:00:00Z",
            retrieval_id="s2-run",
        )
        self.assertEqual(row["source_id"], "semantic_scholar:S1")
        self.assertEqual(row["subjects"], "Computer Science")

    def test_crossref_variable_shapes_and_conflicts(self) -> None:
        fixture = {
            "DOI": "10.5555/12345678",
            "title": ["Toward a Unified Citation Index"],
            "container-title": ["Journal of Testing"],
            "author": {"given": "Josiah", "family": "Carberry"},
            "published": {"date-parts": [[2008, 1]]},
            "type": "journal-article",
            "ISSN": ["1234-5678"],
            "subject": ["Metadata"],
            "is-referenced-by-count": 4,
        }
        candidate = crossref_metadata.flatten_work(
            fixture,
            query="citation index",
            rank=1,
            retrieved_at="2026-08-01T00:00:00Z",
            retrieval_id="cr-run",
        )
        self.assertEqual(candidate["publication_date"], "2008-01")
        self.assertEqual(candidate["authors"], "Carberry, Josiah")
        self.assertEqual(candidate["source_id"], "crossref:10.5555/12345678")
        original = {
            "title": "Different deposited title",
            "year": "2008",
            "doi": "10.5555/12345678",
            "source": "manual",
        }
        enriched = crossref_metadata.enrich_record(
            original,
            candidate,
            retrieved_at="2026-08-01T00:00:00Z",
        )
        self.assertEqual(enriched["title"], "Different deposited title")
        self.assertIn("crossref_conflict:title", enriched["notes"])
        self.assertIn("crossref", enriched["source"])
        self.assertEqual(enriched["retrieval_id"], "cr-run")

        conflicting_candidate = dict(candidate)
        conflicting_candidate["doi"] = "10.5555/different"
        conflicting_candidate["source_id"] = "crossref:10.5555/different"
        rejected = crossref_metadata.enrich_record(
            original,
            conflicting_candidate,
            retrieved_at="2026-08-01T00:00:00Z",
        )
        self.assertEqual(rejected["doi"], "10.5555/12345678")
        self.assertEqual(rejected["metadata_status"], "crossref_conflict")
        self.assertEqual(rejected["source"], "manual")
        self.assertNotIn("crossref:10.5555/different", rejected["source_id"])
        self.assertIn("crossref_conflict:title,doi", rejected["notes"])

        coupled_original = {
            "title": "Toward a Unified Citation Index",
            "year": "2024",
            "doi": "10.5555/12345678",
            "citation_count": "99",
            "citation_count_source": "",
            "source": "manual",
        }
        coupled = crossref_metadata.enrich_record(
            coupled_original,
            candidate,
            retrieved_at="2026-08-01T00:00:00Z",
        )
        self.assertEqual(coupled["year"], "2024")
        self.assertEqual(coupled["publication_date"], "")
        self.assertEqual(coupled["citation_count"], "99")
        self.assertEqual(coupled["citation_count_source"], "")
        self.assertIn("crossref_conflict:year", coupled["notes"])

        placeholder_original = {
            "title": "Toward a Unified Citation Index",
            "year": "2008",
            "doi": "N/A",
            "source": "manual",
        }
        repaired = crossref_metadata.enrich_record(
            placeholder_original,
            candidate,
            retrieved_at="2026-08-01T00:00:00Z",
        )
        self.assertEqual(repaired["metadata_status"], "crossref_enriched")
        self.assertEqual(repaired["doi"], "10.5555/12345678")
        self.assertIn("crossref_replaced_invalid_doi", repaired["notes"])

    def test_dblp_normalizes_singletons_entities_and_preserves_conflicts(self) -> None:
        payload = {
            "result": {
                "hits": {
                    "hit": {
                        "info": {
                            "authors": {"author": {"text": "A. Author"}},
                            "title": "A &amp; B",
                            "venue": "J. ACM",
                            "year": "1998",
                            "type": "Journal Articles",
                            "key": "journals/jacm/Kearns98",
                            "doi": "10.1145/293347.293351",
                            "ee": "https://doi.org/10.1145/293347.293351",
                            "url": "https://dblp.org/rec/journals/jacm/Kearns98",
                        }
                    }
                }
            }
        }
        candidate = dblp_validate.extract_hits(payload)[0]
        self.assertEqual(candidate["title"], "A & B")
        self.assertEqual(candidate["authors"], "A. Author")
        self.assertEqual(candidate["source_id"], "dblp:journals/jacm/Kearns98")
        original = {
            "title": "A & B",
            "year": "1998",
            "venue": "Another venue",
            "doi": "10.1145/293347.293351",
            "source": "manual",
        }
        merged = dblp_validate.apply_candidate(
            original,
            candidate,
            retrieved_at="2026-08-01T00:00:00Z",
            retrieval_id="dblp-run",
        )
        self.assertEqual(merged["venue"], "Another venue")
        self.assertIn("dblp_conflict:venue", merged["notes"])
        self.assertIn("dblp:journals/jacm/Kearns98", merged["source_id"])

    def test_dblp_rejects_same_title_with_conflicting_strong_identity(self) -> None:
        original = {
            "title": "Identical Title",
            "year": "2024",
            "doi": "10.1000/a",
            "source": "manual",
        }
        candidate = {
            "title": "Identical Title",
            "year": "1999",
            "doi": "10.1000/b",
            "source_id": "dblp:conflicting",
        }
        self.assertEqual(dblp_validate.match_score(original, candidate), 0.0)
        merged = dblp_validate.apply_candidate(
            original,
            candidate,
            retrieved_at="2026-08-01T00:00:00Z",
            retrieval_id="dblp-run",
        )
        self.assertEqual(merged["metadata_status"], "dblp_conflict")
        self.assertNotIn("dblp", merged["source"].split("; "))
        self.assertNotIn("dblp:conflicting", merged.get("source_id", ""))
        self.assertIn("dblp_conflict:year,doi", merged["notes"])

        self.assertLess(
            dblp_validate.match_score(
                {"title": "First Distinct Paper", "doi": "N/A"},
                {"title": "Second Unrelated Work", "doi": "N/A"},
            ),
            0.88,
        )

        placeholder_record = {
            "title": "Exact Paper",
            "authors": "Alice Author",
            "year": "2024",
            "doi": "N/A",
            "source": "manual",
        }
        valid_candidate = {
            "title": "Exact Paper",
            "authors": "Alice Author",
            "year": "2024",
            "doi": "10.1000/valid",
            "source_id": "dblp:valid-paper",
        }
        self.assertEqual(
            dblp_validate.match_score(placeholder_record, valid_candidate), 1.0
        )
        repaired = dblp_validate.apply_candidate(
            placeholder_record,
            valid_candidate,
            retrieved_at="2026-08-01T00:00:00Z",
            retrieval_id="dblp-run",
        )
        self.assertEqual(repaired["metadata_status"], "dblp_validated")
        self.assertEqual(repaired["doi"], "10.1000/valid")
        self.assertIn("dblp_replaced_invalid_doi", repaired["notes"])

    def test_dblp_rejects_title_only_match_with_conflicting_year(self) -> None:
        self.assertEqual(
            dblp_validate.match_score(
                {"title": "Identical Title", "year": "2024"},
                {"title": "Identical Title", "year": "1999"},
            ),
            0.0,
        )

    def test_dblp_rejects_weak_match_with_conflicting_first_author(self) -> None:
        self.assertFalse(
            dblp_validate.compatible_first_authors("Alice Goldsmith", "Bob Smith")
        )
        self.assertFalse(
            dblp_validate.compatible_first_authors("Alice Smith", "Bob Smith")
        )
        self.assertFalse(
            dblp_validate.compatible_first_authors("Alice Smith", "Andrew Smith")
        )
        self.assertTrue(
            dblp_validate.compatible_first_authors("A. Smith", "Alice Smith")
        )
        self.assertTrue(
            dblp_validate.compatible_first_authors("van Rossum, Guido", "Guido van Rossum")
        )
        self.assertEqual(
            dblp_validate.match_score(
                {
                    "title": "A Common Title",
                    "authors": "Alice Smith",
                    "year": "2024",
                },
                {
                    "title": "A Common Title",
                    "authors": "Andrew Smith",
                    "year": "2024",
                },
            ),
            0.0,
        )
        original = {
            "title": "A Common Title",
            "authors": "Alice Author; Coauthor One",
            "year": "2024",
            "venue": "Venue One",
            "source": "manual",
        }
        candidate = {
            "title": "A Common Title",
            "authors": "Bob Researcher; Coauthor Two",
            "year": "2024",
            "venue": "Venue Two",
            "source_id": "dblp:other-paper",
        }
        self.assertEqual(dblp_validate.match_score(original, candidate), 0.0)
        merged = dblp_validate.apply_candidate(
            original,
            candidate,
            retrieved_at="2026-08-01T00:00:00Z",
            retrieval_id="dblp-run",
        )
        self.assertEqual(merged["metadata_status"], "dblp_conflict")
        self.assertEqual(merged["source"], "manual")
        self.assertIn("dblp_conflict:authors,venue", merged["notes"])

    def test_dblp_rejects_venue_conflict_without_author_evidence(self) -> None:
        original = {
            "title": "Editorial",
            "year": "2024",
            "venue": "Journal One",
            "source": "manual",
        }
        candidate = {
            "title": "Editorial",
            "year": "2024",
            "venue": "Journal Two",
            "source_id": "dblp:other-editorial",
        }
        self.assertEqual(dblp_validate.match_score(original, candidate), 0.0)
        merged = dblp_validate.apply_candidate(
            original,
            candidate,
            retrieved_at="2026-08-01T00:00:00Z",
            retrieval_id="dblp-run",
        )
        self.assertEqual(merged["metadata_status"], "dblp_conflict")
        self.assertEqual(merged["source"], "manual")
        self.assertIn("dblp_conflict:venue", merged["notes"])

    def test_all_adapters_write_the_same_header_even_without_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for name in ("openalex", "semantic_scholar", "crossref", "cnki"):
                path = Path(temp_dir) / f"{name}.csv"
                write_canonical_csv(path, [])
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    self.assertEqual(next(csv.reader(handle)), list(CANONICAL_FIELDS))
        template = REPOSITORY_ROOT / "templates/literature_table.csv"
        with template.open("r", encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(next(csv.reader(handle)), list(CANONICAL_FIELDS))


if __name__ == "__main__":
    unittest.main()
