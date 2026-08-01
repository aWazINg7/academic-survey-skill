from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts/init_project.py"


class InitProjectTests(unittest.TestCase):
    def run_initializer(
        self, output_root: Path, *extra_args: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "blockchain-fl",
                "--topic",
                "区块链联邦学习综述",
                "--output-root",
                str(output_root),
                *extra_args,
            ],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_creates_complete_project_and_personalizes_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "projects"
            result = self.run_initializer(
                output_root,
                "--target-journal",
                "软件学报",
                "--from-year",
                "2020",
                "--to-year",
                "2026",
                "--source",
                "openalex",
                "--source",
                "cnki-import",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            project = output_root / "blockchain-fl"
            expected_directories = {
                "protocol",
                "data/raw",
                "data/cleaned",
                "data/screened",
                "evidence",
                "analysis",
                "manuscript/sections",
                "figures",
            }
            expected_files = {
                "survey.yaml",
                "protocol/search_protocol.md",
                "evidence/literature.csv",
                "evidence/evidence_matrix.md",
                "analysis/taxonomy.md",
                "analysis/timeline.md",
                "analysis/comparison.md",
                "analysis/gaps.md",
                "manuscript/main.tex",
                "manuscript/references.bib",
            }
            self.assertTrue(all((project / item).is_dir() for item in expected_directories))
            self.assertTrue(all((project / item).is_file() for item in expected_files))

            config = (project / "survey.yaml").read_text(encoding="utf-8")
            self.assertIn('topic: "区块链联邦学习综述"', config)
            self.assertIn('target_journal: "软件学报"', config)
            self.assertIn("from_year: 2020", config)
            self.assertIn('    - "zh"', config)
            self.assertIn('    - "en"', config)
            self.assertIn('    - "cnki-import"', config)
            self.assertNotIn("{{", config)

            protocol = (project / "protocol/search_protocol.md").read_text(encoding="utf-8")
            self.assertIn("- 中文题目：区块链联邦学习综述", protocol)
            self.assertIn("- 目标期刊：软件学报", protocol)

            manuscript = (project / "manuscript/main.tex").read_text(encoding="utf-8")
            self.assertIn(r"\title{区块链联邦学习综述}", manuscript)

    def test_refuses_to_overwrite_existing_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "projects"
            first = self.run_initializer(output_root)
            self.assertEqual(first.returncode, 0, first.stderr)
            marker = output_root / "blockchain-fl" / "keep.txt"
            marker.write_text("preserve me", encoding="utf-8")

            second = self.run_initializer(output_root)
            self.assertEqual(second.returncode, 2)
            self.assertIn("project already exists", second.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve me")

    def test_rejects_invalid_year_range_without_partial_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "projects"
            result = self.run_initializer(output_root, "--from-year", "2027", "--to-year", "2026")
            self.assertEqual(result.returncode, 2)
            self.assertIn("from-year cannot be greater", result.stderr)
            self.assertFalse((output_root / "blockchain-fl").exists())

    def test_missing_template_fails_before_creating_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_root = Path(temp_dir) / "skill"
            copied_script = fake_root / "scripts/init_project.py"
            copied_script.parent.mkdir(parents=True)
            shutil.copy2(SCRIPT, copied_script)
            output_root = Path(temp_dir) / "projects"

            result = subprocess.run(
                [
                    sys.executable,
                    str(copied_script),
                    "incomplete",
                    "--topic",
                    "test",
                    "--projects-root",
                    str(output_root),
                ],
                cwd=temp_dir,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("missing required template", result.stderr)
            self.assertFalse((output_root / "incomplete").exists())

    def test_rejects_path_traversal_project_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "projects"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "../escape",
                    "--topic",
                    "test",
                    "--output-root",
                    str(output_root),
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("project name must be", result.stderr)
            self.assertFalse((Path(temp_dir) / "escape").exists())

    def test_rejects_windows_reserved_project_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for project_name in ("CON", "nul.txt", "LPT1", "survey."):
                with self.subTest(project_name=project_name):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPT),
                            project_name,
                            "--topic",
                            "test",
                            "--projects-root",
                            temp_dir,
                        ],
                        cwd=REPOSITORY_ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("project name must be", result.stderr)

    def test_supports_unicode_project_name_and_yaml_sensitive_topic(self) -> None:
        topic = '图学习 # 安全: "综述" {{SOURCES}}'
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "智能体安全综述",
                    "--topic",
                    topic,
                    "--projects-root",
                    temp_dir,
                ],
                cwd=temp_dir,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            config_path = Path(temp_dir) / "智能体安全综述/survey.yaml"
            config = config_path.read_text(encoding="utf-8")
            topic_line = next(line for line in config.splitlines() if line.startswith("  topic: "))
            self.assertEqual(json.loads(topic_line.split(": ", 1)[1]), topic)
            self.assertIn("target_journal: null", config)
            manuscript_path = Path(temp_dir) / "智能体安全综述/manuscript/main.tex"
            manuscript = manuscript_path.read_text(encoding="utf-8")
            self.assertIn(r'\title{图学习 \# 安全: "综述" \{\{SOURCES\}\}}', manuscript)


if __name__ == "__main__":
    unittest.main()
