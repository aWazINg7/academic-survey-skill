from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPOSITORY_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "deduplicate_literature.py"
sys.path.insert(0, str(SCRIPTS_DIR))

import deduplicate_literature as dedupe  # noqa: E402
from metadata_common import CANONICAL_FIELDS, write_canonical_csv  # noqa: E402


class DeduplicateLiteratureTests(unittest.TestCase):
    def test_transitive_bridge_merges_three_records_and_provenance(self) -> None:
        entries = [
            dedupe.Entry(
                {
                    "title": "Alpha Method",
                    "doi": "https://doi.org/10.1000/A",
                    "venue": "Journal A",
                    "authors": "A. Author; B. Author",
                    "abstract": "A comparatively complete abstract.",
                    "source": "openalex",
                    "source_id": "openalex:W1",
                    "retrieval_id": "oa-run",
                },
                "openalex.csv",
                2,
            ),
            dedupe.Entry(
                {
                    "title": "Beta Preprint",
                    "arxiv_id": "2401.12345v2",
                    "source": "semantic_scholar",
                    "source_id": "semantic_scholar:S1",
                    "retrieval_id": "s2-run",
                },
                "s2.csv",
                2,
            ),
            dedupe.Entry(
                {
                    "title": "Alpha Method",
                    "url": "https://arxiv.org/abs/2401.12345",
                    "keywords": "可信; 联邦学习",
                    "source": "cnki",
                    "source_id": "cnki:C1",
                    "retrieval_id": "cn-run",
                },
                "cnki.csv",
                2,
            ),
        ]
        merged, duplicates, conflicts = dedupe.merge_entries(entries)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(duplicates), 2)
        row = merged[0]
        self.assertEqual(row["title"], "Alpha Method")
        self.assertEqual(row["doi"], "10.1000/a")
        self.assertEqual(dedupe.extract_arxiv(row), "2401.12345")
        self.assertEqual(row["source"], "cnki; openalex; semantic_scholar")
        self.assertEqual(row["retrieval_id"], "cn-run; oa-run; s2-run")
        self.assertEqual(row["metadata_status"], "merged")
        self.assertTrue(row["paper_id"].startswith("P"))
        self.assertTrue(any(item["field"] == "title" for item in conflicts))
        self.assertTrue(all(item["duplicate_of"] == row["paper_id"] for item in duplicates))
        self.assertTrue(any("arxiv:2401.12345" in item["matched_on"] for item in duplicates))

    def test_conflicting_metadata_is_reported_not_silently_overwritten(self) -> None:
        entries = [
            dedupe.Entry(
                {
                    "title": "Canonical Title",
                    "doi": "10.1000/same",
                    "year": "2024",
                    "venue": "Journal One",
                    "authors": "A; B; C",
                    "source": "openalex",
                }
            ),
            dedupe.Entry(
                {
                    "title": "Deposited Variant",
                    "doi": "https://doi.org/10.1000/SAME",
                    "year": "2023",
                    "venue": "Journal Two",
                    "source": "crossref",
                }
            ),
        ]
        merged, _, conflicts = dedupe.merge_entries(entries)
        row = merged[0]
        self.assertEqual(row["title"], "Canonical Title")
        self.assertEqual(row["year"], "2024")
        self.assertEqual(row["venue"], "Journal One")
        self.assertEqual({item["field"] for item in conflicts}, {"title", "year", "venue"})

    def test_paper_id_and_merge_are_independent_of_input_order(self) -> None:
        first = dedupe.Entry(
            {"title": "Stable Record", "doi": "10.1000/stable", "source": "openalex"}
        )
        second = dedupe.Entry(
            {"title": "Stable Record", "abstract": "More metadata", "source": "crossref"}
        )
        left, _, _ = dedupe.merge_entries([first, second])
        right, _, _ = dedupe.merge_entries([second, first])
        self.assertEqual(left, right)

        venue_variant_one = dedupe.Entry(
            {
                "title": "Stable Weak Match",
                "authors": "Alice Smith",
                "year": "2024",
                "venue": "Journal One",
            }
        )
        venue_variant_two = dedupe.Entry(
            {
                "title": "Stable Weak Match",
                "authors": "Alice Smith",
                "year": "2024",
                "venue": "Journal Two",
            }
        )
        left = dedupe.merge_entries([venue_variant_one, venue_variant_two])
        right = dedupe.merge_entries([venue_variant_two, venue_variant_one])
        self.assertEqual(left, right)

    def test_same_title_with_conflicting_dois_remains_two_papers(self) -> None:
        entries = [
            dedupe.Entry({"title": "Same Paper", "doi": "10.1000/a"}),
            dedupe.Entry({"title": "Same Paper", "doi": "10.1000/b"}),
        ]
        merged, duplicates, conflicts = dedupe.merge_entries(entries)
        self.assertEqual({row["doi"] for row in merged}, {"10.1000/a", "10.1000/b"})
        self.assertEqual(duplicates, [])
        self.assertEqual(conflicts, [])

    def test_same_title_with_conflicting_arxiv_ids_remains_two_papers(self) -> None:
        entries = [
            dedupe.Entry({"title": "Same Preprint", "arxiv_id": "2401.00001v1"}),
            dedupe.Entry({"title": "Same Preprint", "arxiv_id": "2401.00002v1"}),
        ]
        merged, duplicates, conflicts = dedupe.merge_entries(entries)
        self.assertEqual(len(merged), 2)
        self.assertEqual(
            {dedupe.extract_arxiv(row) for row in merged},
            {"2401.00001", "2401.00002"},
        )
        self.assertEqual(duplicates, [])
        self.assertEqual(conflicts, [])

    def test_doi_suffix_is_not_mistaken_for_an_arxiv_identifier(self) -> None:
        entries = [
            dedupe.Entry(
                {"title": "Deposited Article", "doi": "10.5555/2401.12345"}
            ),
            dedupe.Entry(
                {"title": "Actual Preprint", "arxiv_id": "2401.12345"}
            ),
        ]
        merged, duplicates, _ = dedupe.merge_entries(entries)
        self.assertEqual(len(merged), 2)
        self.assertEqual(duplicates, [])
        self.assertEqual(dedupe.extract_arxiv(entries[0].record), "")
        self.assertEqual(dedupe.extract_arxiv(entries[1].record), "2401.12345")

    def test_official_arxiv_doi_is_recognized(self) -> None:
        self.assertEqual(
            dedupe.extract_arxiv({"doi": "https://doi.org/10.48550/arXiv.2401.12345"}),
            "2401.12345",
        )

    def test_placeholder_doi_values_are_not_strong_identity_keys(self) -> None:
        for placeholder in ("N/A", "-", "无"):
            with self.subTest(placeholder=placeholder):
                entries = [
                    dedupe.Entry(
                        {"title": "First Distinct Paper", "year": "2020", "doi": placeholder}
                    ),
                    dedupe.Entry(
                        {"title": "Second Distinct Paper", "year": "2025", "doi": placeholder}
                    ),
                ]
                merged, duplicates, _ = dedupe.merge_entries(entries)
                self.assertEqual(len(merged), 2)
                self.assertEqual(len({row["paper_id"] for row in merged}), 2)
                self.assertEqual(duplicates, [])

    def test_weak_title_match_rejects_year_and_first_author_conflicts(self) -> None:
        year_conflict = [
            dedupe.Entry(
                {"title": "Introduction", "year": "2020", "authors": "Alice Author"}
            ),
            dedupe.Entry(
                {"title": "Introduction", "year": "2025", "authors": "Alice Author"}
            ),
        ]
        merged, duplicates, _ = dedupe.merge_entries(year_conflict)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len({row["paper_id"] for row in merged}), 2)
        self.assertEqual(duplicates, [])

        author_conflict = [
            dedupe.Entry(
                {"title": "Shared Title", "year": "2024", "authors": "Alice Author"}
            ),
            dedupe.Entry(
                {"title": "Shared Title", "year": "2024", "authors": "Bob Researcher"}
            ),
        ]
        merged, duplicates, _ = dedupe.merge_entries(author_conflict)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len({row["paper_id"] for row in merged}), 2)
        self.assertEqual(duplicates, [])

        same_surname_conflict = [
            dedupe.Entry(
                {"title": "Shared Title", "year": "2024", "authors": "Alice Smith"}
            ),
            dedupe.Entry(
                {"title": "Shared Title", "year": "2024", "authors": "Bob Smith"}
            ),
        ]
        merged, duplicates, _ = dedupe.merge_entries(same_surname_conflict)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len({row["paper_id"] for row in merged}), 2)
        self.assertEqual(duplicates, [])

        same_initial_conflict = [
            dedupe.Entry(
                {"title": "Shared Title", "year": "2024", "authors": "Alice Smith"}
            ),
            dedupe.Entry(
                {"title": "Shared Title", "year": "2024", "authors": "Andrew Smith"}
            ),
        ]
        merged, duplicates, _ = dedupe.merge_entries(same_initial_conflict)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len({row["paper_id"] for row in merged}), 2)
        self.assertEqual(duplicates, [])

        abbreviated_author = [
            dedupe.Entry(
                {"title": "Abbreviated Author", "year": "2024", "authors": "A. Smith"}
            ),
            dedupe.Entry(
                {
                    "title": "Abbreviated Author",
                    "year": "2024",
                    "authors": "Alice Smith",
                }
            ),
        ]
        merged, duplicates, _ = dedupe.merge_entries(abbreviated_author)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(duplicates), 1)

        venue_conflict_without_authors = [
            dedupe.Entry(
                {"title": "Editorial", "year": "2024", "venue": "Journal A"}
            ),
            dedupe.Entry(
                {"title": "Editorial", "year": "2024", "venue": "Journal B"}
            ),
        ]
        merged, duplicates, _ = dedupe.merge_entries(venue_conflict_without_authors)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len({row["paper_id"] for row in merged}), 2)
        self.assertEqual(duplicates, [])

    def test_merge_replaces_invalid_strong_ids_with_valid_candidates(self) -> None:
        rich = dedupe.Entry(
            {
                "title": "Identity Repair",
                "authors": "Alice Author",
                "year": "2024",
                "venue": "Journal A",
                "publication_type": "article",
                "volume": "10",
                "issue": "2",
                "pages": "1-20",
                "abstract": "A rich source record.",
                "doi": "N/A",
                "arxiv_id": "N/A",
                "source": "manual",
            }
        )
        sparse = dedupe.Entry(
            {
                "title": "Identity Repair",
                "authors": "Alice Author",
                "year": "2024",
                "doi": "10.1000/valid",
                "arxiv_id": "2401.12345v2",
                "source": "crossref",
            }
        )
        merged, duplicates, conflicts = dedupe.merge_entries([rich, sparse])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(merged[0]["doi"], "10.1000/valid")
        self.assertEqual(merged[0]["arxiv_id"], "2401.12345")
        self.assertEqual(
            merged[0]["paper_id"], dedupe.choose_paper_id({"doi": "10.1000/valid"})
        )
        self.assertNotIn("doi", {item["field"] for item in conflicts})
        self.assertNotIn("arxiv_id", {item["field"] for item in conflicts})

    def test_merge_keeps_year_date_and_citation_source_coupled(self) -> None:
        primary = dedupe.Entry(
            {
                "title": "Coupled Metadata",
                "authors": "Alice Author",
                "year": "2024",
                "venue": "Journal A",
                "doi": "10.1000/coupled",
                "citation_count": "999",
                "citation_count_source": "",
                "source": "manual",
            }
        )
        crossref = dedupe.Entry(
            {
                "title": "Coupled Metadata",
                "authors": "Alice Author",
                "year": "2023",
                "publication_date": "2023-01-02",
                "doi": "10.1000/coupled",
                "citation_count": "7",
                "citation_count_source": "crossref",
                "source": "crossref",
            }
        )
        merged, _, conflicts = dedupe.merge_entries([primary, crossref])
        row = merged[0]
        self.assertEqual(row["year"], "2024")
        self.assertEqual(row["publication_date"], "")
        self.assertEqual(row["citation_count"], "999")
        self.assertEqual(row["citation_count_source"], "")
        self.assertIn("year", {item["field"] for item in conflicts})

    def test_transitive_title_bridge_cannot_collapse_conflicting_dois(self) -> None:
        entries = [
            dedupe.Entry({"title": "Alpha", "doi": "10.1000/a"}),
            dedupe.Entry({"title": "Beta", "doi": "10.1000/b"}),
            dedupe.Entry({"title": "Beta", "doi": "10.1000/a"}),
        ]
        merged, duplicates, conflicts = dedupe.merge_entries(entries)
        self.assertEqual(len(merged), 2)
        self.assertEqual({row["doi"] for row in merged}, {"10.1000/a", "10.1000/b"})
        self.assertEqual(len(duplicates), 1)
        self.assertEqual({item["field"] for item in conflicts}, {"title"})

    def test_empty_records_receive_distinct_fallback_paper_ids(self) -> None:
        entries = [
            dedupe.Entry({}, "a.csv", 2),
            dedupe.Entry({}, "b.csv", 2),
        ]
        merged, duplicates, conflicts = dedupe.merge_entries(entries)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len({row["paper_id"] for row in merged}), 2)
        self.assertEqual(duplicates, [])
        self.assertEqual(conflicts, [])

    def test_duplicate_input_paper_ids_across_components_fail_before_writes(self) -> None:
        entries = [
            dedupe.Entry({"paper_id": "P001", "title": "First Paper"}),
            dedupe.Entry({"paper_id": "P001", "title": "Second Paper"}),
        ]
        with self.assertRaisesRegex(
            ValueError, "duplicate paper_id values across distinct papers: P001"
        ):
            dedupe.merge_entries(entries)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.csv"
            second = root / "second.csv"
            write_canonical_csv(first, [entries[0].record])
            write_canonical_csv(second, [entries[1].record])
            output = root / "output.csv"
            output.write_text("existing output\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(first),
                    str(second),
                    "--output",
                    str(output),
                    "--duplicates",
                    str(root / "duplicates.csv"),
                    "--conflicts",
                    str(root / "conflicts.csv"),
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("duplicate paper_id values", result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing output\n")
            self.assertFalse((root / "duplicates.csv").exists())
            self.assertFalse((root / "conflicts.csv").exists())

    def test_cli_accepts_multiple_inputs_and_old_single_input_form(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "openalex.csv"
            second = root / "cnki.csv"
            write_canonical_csv(
                first,
                [{"title": "Same Paper", "doi": "10.1000/same", "source": "openalex"}],
            )
            write_canonical_csv(
                second,
                [{"title": "Same Paper", "authors": "张三", "source": "cnki"}],
            )

            output = root / "cleaned.csv"
            duplicates = root / "duplicates.csv"
            conflicts = root / "conflicts.csv"
            log = root / "merge.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(first),
                    str(second),
                    "--output",
                    str(output),
                    "--duplicates",
                    str(duplicates),
                    "--conflicts",
                    str(conflicts),
                    "--log",
                    str(log),
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["authors"], "张三")
            with duplicates.open("r", encoding="utf-8-sig", newline="") as handle:
                self.assertIn("duplicate_of", next(csv.reader(handle)))

            single_output = root / "single.csv"
            single_duplicates = root / "single-duplicates.csv"
            single_conflicts = root / "single-conflicts.csv"
            single = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(first),
                    "--output",
                    str(single_output),
                    "--duplicates",
                    str(single_duplicates),
                    "--conflicts",
                    str(single_conflicts),
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(single.returncode, 0, single.stderr)
            with single_output.open("r", encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(next(csv.reader(handle)), list(CANONICAL_FIELDS))

    def test_cli_rejects_non_bibliographic_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "unknown.csv"
            input_path.write_text("foo,bar\none,two\n", encoding="utf-8")
            output = root / "output.csv"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(input_path),
                    "--output",
                    str(output),
                    "--duplicates",
                    str(root / "duplicates.csv"),
                    "--conflicts",
                    str(root / "conflicts.csv"),
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("bibliographic identity header", result.stderr)
            self.assertFalse(output.exists())

            placeholder_path = root / "placeholder.csv"
            placeholder_path.write_text("title,doi\n,N/A\n", encoding="utf-8")
            placeholder_output = root / "placeholder-output.csv"
            placeholder_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(placeholder_path),
                    "--output",
                    str(placeholder_output),
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(placeholder_result.returncode, 2)
            self.assertIn("no bibliographic identity", placeholder_result.stderr)
            self.assertFalse(placeholder_output.exists())


if __name__ == "__main__":
    unittest.main()
