#!/usr/bin/env python3
"""Initialize a reproducible academic-survey project from bundled templates."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence


DIRECTORIES = (
    "protocol",
    "data/raw",
    "data/cleaned",
    "data/screened",
    "evidence",
    "analysis",
    "manuscript/sections",
    "figures",
)

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

CONFIG_TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")

TEMPLATE_FILES = {
    "search_protocol.md": "protocol/search_protocol.md",
    "literature_table.csv": "evidence/literature.csv",
    "evidence_matrix.md": "evidence/evidence_matrix.md",
    "taxonomy.md": "analysis/taxonomy.md",
    "timeline.md": "analysis/timeline.md",
    "comparison.md": "analysis/comparison.md",
    "gaps.md": "analysis/gaps.md",
    "chinese_journal_manuscript.tex": "manuscript/main.tex",
}


def validate_project_name(value: str) -> str:
    """Reject ambiguous, non-portable, or path-traversing project names."""
    windows_stem = value.split(".", 1)[0].upper()
    valid = (
        1 <= len(value) <= 64
        and value[0].isalnum()
        and all(character.isalnum() or character in "._-" for character in value)
        and value not in {".", ".."}
        and not value.endswith(".")
        and windows_stem not in WINDOWS_RESERVED_NAMES
    )
    if not valid:
        raise argparse.ArgumentTypeError(
            "project name must be 1-64 characters using Unicode letters, digits, '.', '_' "
            "or '-', and must start with a letter or digit; trailing dots and Windows "
            "reserved names are not allowed"
        )
    return value


def yaml_scalar(value: object) -> str:
    """Render JSON scalars, which are also valid YAML scalars."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def yaml_list(values: Iterable[str], indent: int = 4) -> str:
    prefix = " " * indent
    items = list(values)
    return "\n".join(f"{prefix}- {yaml_scalar(item)}" for item in items) or f"{prefix}[]"


def render_config(
    template: str,
    *,
    project_name: str,
    topic: str,
    target_journal: str | None,
    from_year: int | None,
    to_year: int,
    languages: Sequence[str],
    sources: Sequence[str],
    created_at: str,
) -> str:
    replacements = {
        "{{PROJECT_NAME}}": yaml_scalar(project_name),
        "{{TOPIC}}": yaml_scalar(topic),
        "{{TARGET_JOURNAL}}": yaml_scalar(target_journal),
        "{{CREATED_AT}}": yaml_scalar(created_at),
        "{{FROM_YEAR}}": yaml_scalar(from_year),
        "{{TO_YEAR}}": yaml_scalar(to_year),
        "{{LANGUAGES}}": yaml_list(languages),
        "{{SOURCES}}": yaml_list(sources),
    }
    template_tokens = set(CONFIG_TOKEN_RE.findall(template))
    expected_tokens = set(replacements)
    missing = sorted(expected_tokens - template_tokens)
    unknown = sorted(template_tokens - expected_tokens)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ValueError("invalid survey.yaml template tokens: " + "; ".join(details))
    return CONFIG_TOKEN_RE.sub(lambda match: replacements[match.group(0)], template)


def replace_markdown_field(text: str, label: str, value: str) -> str:
    pattern = re.compile(rf"^- {re.escape(label)}.*$", re.MULTILINE)
    replacement = f"- {label}{value}"
    return pattern.sub(lambda _: replacement, text, count=1)


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in value)


def personalize_file(
    relative_path: str,
    text: str,
    *,
    project_name: str,
    topic: str,
    target_journal: str | None,
    created_at: str,
) -> str:
    text = text.replace("{{PROJECT_NAME}}", project_name)
    text = text.replace("{{TOPIC}}", topic)
    text = text.replace("{{TARGET_JOURNAL}}", target_journal or "待定")
    text = text.replace("{{CREATED_AT}}", created_at)

    if relative_path == "protocol/search_protocol.md":
        text = replace_markdown_field(text, "中文题目：", topic)
        text = replace_markdown_field(text, "目标期刊：", target_journal or "待定")
        text = replace_markdown_field(text, "检索截止日期：", created_at)
    elif relative_path == "manuscript/main.tex":
        text = re.sub(
            r"\\title\{[^\n]*\}",
            lambda _: rf"\title{{{latex_escape(topic)}}}",
            text,
            count=1,
        )
    return text


def initialize_project(
    *,
    repository_root: Path,
    output_root: Path,
    project_name: str,
    topic: str,
    target_journal: str | None,
    from_year: int | None,
    to_year: int,
    languages: Sequence[str],
    sources: Sequence[str],
    created_at: str | None = None,
) -> Path:
    """Create a complete project without overwriting an existing directory."""
    try:
        validate_project_name(project_name)
    except argparse.ArgumentTypeError as exc:
        raise ValueError(str(exc)) from exc

    topic = topic.strip()
    if "\n" in topic or "\r" in topic:
        raise ValueError("topic must be a single line")
    target_journal = target_journal.strip() if target_journal else None
    if target_journal and ("\n" in target_journal or "\r" in target_journal):
        raise ValueError("target journal must be a single line")
    languages = tuple(dict.fromkeys(item.strip() for item in languages if item.strip()))
    sources = tuple(dict.fromkeys(item.strip() for item in sources if item.strip()))

    if from_year is not None and from_year > to_year:
        raise ValueError("from-year cannot be greater than to-year")
    if not topic:
        raise ValueError("topic cannot be empty")
    if not languages:
        raise ValueError("at least one language is required")
    if not sources:
        raise ValueError("at least one literature source is required")

    templates_root = repository_root / "templates"
    required = [templates_root / name for name in TEMPLATE_FILES]
    required.append(templates_root / "survey.yaml")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing required template(s): " + ", ".join(missing))

    output_root = output_root.expanduser().resolve()
    target = output_root / project_name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"project already exists: {target}")

    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{project_name}-", dir=output_root))
    stamp = created_at or date.today().isoformat()

    try:
        for directory in DIRECTORIES:
            (staging / directory).mkdir(parents=True, exist_ok=True)

        for template_name, destination in TEMPLATE_FILES.items():
            source = templates_root / template_name
            text = source.read_text(encoding="utf-8")
            text = personalize_file(
                destination,
                text,
                project_name=project_name,
                topic=topic,
                target_journal=target_journal,
                created_at=stamp,
            )
            output = staging / destination
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")

        config_template = (templates_root / "survey.yaml").read_text(encoding="utf-8")
        config = render_config(
            config_template,
            project_name=project_name,
            topic=topic,
            target_journal=target_journal,
            from_year=from_year,
            to_year=to_year,
            languages=languages,
            sources=sources,
            created_at=stamp,
        )
        (staging / "survey.yaml").write_text(config, encoding="utf-8")
        (staging / "manuscript/references.bib").write_text(
            f"% Verified references for {project_name}.\n", encoding="utf-8"
        )
        (staging / "protocol/search_log.jsonl").write_text("", encoding="utf-8")

        if target.exists() or target.is_symlink():
            raise FileExistsError(f"project already exists: {target}")
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_name", type=validate_project_name)
    parser.add_argument("--topic", required=True, help="Survey topic or working title")
    parser.add_argument(
        "--output-root",
        "--projects-root",
        dest="output_root",
        type=Path,
        default=Path("projects"),
    )
    parser.add_argument("--target-journal")
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int, default=date.today().year)
    parser.add_argument("--language", dest="languages", action="append")
    parser.add_argument("--source", dest="sources", action="append")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    default_sources = (
        "openalex",
        "semantic_scholar",
        "crossref",
        "dblp",
        "chinese_import",
    )

    try:
        target = initialize_project(
            repository_root=repository_root,
            output_root=args.output_root,
            project_name=args.project_name,
            topic=args.topic,
            target_journal=args.target_journal,
            from_year=args.from_year,
            to_year=args.to_year,
            languages=args.languages or ("zh", "en"),
            sources=args.sources or default_sources,
        )
    except (FileExistsError, FileNotFoundError, ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")

    print(f"Created survey project: {target}")
    print(f"Next: review {target / 'survey.yaml'} and {target / 'protocol/search_protocol.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
