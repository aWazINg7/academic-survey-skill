from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts/import_literature.py"
SCRIPTS_DIR = REPOSITORY_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from metadata_common import CANONICAL_FIELDS  # noqa: E402


class ImportLiteratureTests(unittest.TestCase):
    def run_import(
        self,
        input_path: Path,
        *,
        source: str,
        query: str = "",
        extra_args: tuple[str, ...] = (),
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        output = input_path.parent / "canonical.csv"
        log = input_path.parent / "import.search.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(input_path),
                "--source",
                source,
                "--query",
                query,
                "--output",
                str(output),
                "--log",
                str(log),
                *extra_args,
            ],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        return result, output, log

    def read_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_imports_utf8_sig_cnki_csv_and_preserves_unknown_columns(self) -> None:
        source_text = (
            "题名,作者,年份,刊名,DOI,摘要,关键词,被引次数,自定义字段\n"
            '"可信联邦学习综述","张三; 李四",2025,计算机学报,'
            'https://doi.org/10.1000/ABC,"含,逗号的摘要","联邦学习; 可信",12,重点\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "cnki.csv"
            input_path.write_bytes(source_text.encode("utf-8-sig"))

            result, output, log_path = self.run_import(
                input_path, source="cnki", query="可信 联邦学习"
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            rows = self.read_rows(output)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["title"], "可信联邦学习综述")
            self.assertEqual(row["authors"], "张三; 李四")
            self.assertEqual(row["year"], "2025")
            self.assertEqual(row["venue"], "计算机学报")
            self.assertEqual(row["doi"], "10.1000/abc")
            self.assertEqual(row["abstract"], "含,逗号的摘要")
            self.assertEqual(row["keywords"], "联邦学习; 可信")
            self.assertEqual(row["citation_count"], "12")
            self.assertEqual(row["source"], "cnki")
            self.assertEqual(row["search_query"], "可信 联邦学习")
            self.assertEqual(row["search_rank"], "1")
            self.assertEqual(row["metadata_status"], "imported")
            self.assertEqual(json.loads(row["raw_metadata"])["自定义字段"], "重点")

            log = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(log["source"], "cnki")
            self.assertEqual(log["result_count"], 1)
            self.assertEqual(log["filters"]["format"], "csv")
            self.assertEqual(log["filters"]["encoding"], "utf-8-sig")
            self.assertEqual(log["input_files"], [str(input_path)])

    def test_auto_detects_gb18030_tsv_and_wanfang_aliases(self) -> None:
        source_text = (
            "论文题目\t作者\t发表日期\t来源出版物\t卷\t期\t页码\t原文链接\t语种\t万方ID\n"
            "知识图谱安全研究\t王五;赵六\t2023年5月4日\t软件导刊\t34\t5\t100-112\t"
            "https://example.test/wf/42\t中文\tWF-42\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "wanfang.tsv"
            input_path.write_bytes(source_text.encode("gb18030"))

            result, output, log_path = self.run_import(input_path, source="wanfang")

            self.assertEqual(result.returncode, 0, result.stderr)
            row = self.read_rows(output)[0]
            self.assertEqual(row["title"], "知识图谱安全研究")
            self.assertEqual(row["authors"], "王五; 赵六")
            self.assertEqual(row["year"], "2023")
            self.assertEqual(row["publication_date"], "2023-05-04")
            self.assertEqual(row["venue"], "软件导刊")
            self.assertEqual(row["volume"], "34")
            self.assertEqual(row["issue"], "5")
            self.assertEqual(row["pages"], "100-112")
            self.assertEqual(row["url"], "https://example.test/wf/42")
            self.assertEqual(row["language"], "zh")
            self.assertEqual(row["source_id"], "wanfang:WF-42")
            self.assertTrue(row["retrieval_id"].startswith("wanfang-"))

            log = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(log["filters"]["encoding"], "gb18030")
            self.assertEqual(log["filters"]["delimiter"], "\\t")

    def test_auto_detects_ris_continuations_multiple_values_and_unknown_tags(self) -> None:
        source_text = """TY  - JOUR
TI  - 联邦学习中的
      隐私保护
AU  - 张三
AU  - 李四
PY  - 2024/05/01/
JF  - 软件学报
AB  - 第一段，
      第二段。
KW  - 联邦学习
KW  - 隐私保护
DO  - DOI: 10.5555/Example.1
UR  - https://example.test/paper/1
C1  - 某重点实验室
ER  -
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "export.txt"
            input_path.write_text(source_text, encoding="utf-8")

            result, output, log_path = self.run_import(input_path, source="cnki-ris")

            self.assertEqual(result.returncode, 0, result.stderr)
            row = self.read_rows(output)[0]
            self.assertEqual(row["title"], "联邦学习中的 隐私保护")
            self.assertEqual(row["authors"], "张三; 李四")
            self.assertEqual(row["year"], "2024")
            self.assertEqual(row["publication_date"], "2024-05-01")
            self.assertEqual(row["venue"], "软件学报")
            self.assertEqual(row["publication_type"], "journal article")
            self.assertEqual(row["abstract"], "第一段， 第二段。")
            self.assertEqual(row["keywords"], "联邦学习; 隐私保护")
            self.assertEqual(row["doi"], "10.5555/example.1")
            self.assertEqual(row["url"], "https://example.test/paper/1")
            raw = json.loads(row["raw_metadata"])
            self.assertEqual(raw["AU"], ["张三", "李四"])
            self.assertEqual(raw["C1"], "某重点实验室")
            self.assertEqual(raw["TI"], "联邦学习中的\n隐私保护")

            log = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(log["filters"]["format"], "ris")
            self.assertIsNone(log["filters"]["delimiter"])

    def test_header_only_csv_writes_empty_canonical_table_and_zero_count_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "empty.csv"
            input_path.write_text("题名,作者,年份\n", encoding="utf-8")

            result, output, log_path = self.run_import(
                input_path,
                source="manual-cn",
                extra_args=("--format", "csv", "--delimiter", "comma"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows, [list(CANONICAL_FIELDS)])
            log = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(log["result_count"], 0)
            self.assertEqual(log["filters"]["delimiter"], ",")

    def test_rejects_csv_without_recognized_bibliographic_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "unknown.csv"
            input_path.write_text("foo,bar\nvalue one,value two\n", encoding="utf-8")

            result, output, log_path = self.run_import(input_path, source="manual")

            self.assertEqual(result.returncode, 2)
            self.assertIn("recognized bibliographic fields", result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(log_path.exists())

    def test_duplicate_doi_headers_flag_only_real_conflicts(self) -> None:
        source_text = (
            "题名,DOI,doi,doi号\n"
            '"等价 DOI",https://doi.org/10.1000/SAME,10.1000/same,\n'
            '"冲突 DOI",10.1000/first,,10.1000/second\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "duplicate-doi.csv"
            input_path.write_text(source_text, encoding="utf-8")

            result, output, _ = self.run_import(input_path, source="manual")

            self.assertEqual(result.returncode, 0, result.stderr)
            equivalent, conflicting = self.read_rows(output)
            self.assertEqual(equivalent["doi"], "10.1000/same")
            self.assertEqual(equivalent["metadata_status"], "imported")
            self.assertNotIn("import_conflict:doi", equivalent["notes"])
            self.assertEqual(conflicting["doi"], "10.1000/first")
            self.assertEqual(conflicting["metadata_status"], "import_conflict")
            self.assertIn("import_conflict:doi", conflicting["notes"])

    def test_skips_rows_without_a_bibliographic_identity(self) -> None:
        source_text = (
            "题名,作者,年份,DOI,备注\n"
            ",只有作者,2024,N/A,占位 DOI 不是标识符\n"
            "可识别论文,有效作者,2024,,保留\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "partial.csv"
            input_path.write_text(source_text, encoding="utf-8")

            result, output, log_path = self.run_import(input_path, source="manual")

            self.assertEqual(result.returncode, 0, result.stderr)
            rows = self.read_rows(output)
            self.assertEqual([row["title"] for row in rows], ["可识别论文"])
            self.assertEqual(rows[0]["search_rank"], "1")
            log = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(log["result_count"], 1)

    def test_invalid_publication_date_keeps_year_and_adds_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "invalid-date.csv"
            input_path.write_text(
                "题名,发表日期\n日期错误的论文,2023-99-99\n", encoding="utf-8"
            )

            result, output, _ = self.run_import(input_path, source="manual")

            self.assertEqual(result.returncode, 0, result.stderr)
            row = self.read_rows(output)[0]
            self.assertEqual(row["publication_date"], "")
            self.assertEqual(row["year"], "2023")
            self.assertEqual(row["metadata_status"], "import_warning")
            self.assertIn("import_warning:invalid_publication_date", row["notes"])


if __name__ == "__main__":
    unittest.main()
