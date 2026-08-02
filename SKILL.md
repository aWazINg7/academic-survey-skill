---
name: academic-survey
description: Build, revise, and quality-check evidence-grounded academic survey or review papers through reproducible project setup, systematic literature retrieval, screening, taxonomy construction, method comparison, trend analysis, citation verification, and Chinese journal-oriented writing. Use for computer-science surveys targeting journals such as 《软件学报》《计算机学报》《计算机研究与发展》 and for comparable English-language survey workflows.
---

# Academic Survey

## Core contract

Build a verifiable evidence base and an explicit knowledge structure before drafting prose. Never invent papers, authors, venues, DOI values, experiments, results, or citations.

Resolve every bundled path relative to this `SKILL.md`, not relative to the caller's current directory.

## Start or resume a project

For a new survey, initialize the standard workspace from the skill root:

```bash
python scripts/init_project.py <project-name> \
  --topic "<survey topic>" \
  --target-journal "<target journal>" \
  --from-year <year> \
  --to-year <year>
```

Use `--projects-root <path>` when the project should live outside the default `projects/` directory. The command refuses to overwrite an existing project.

For an existing survey, read `survey.yaml` first. Preserve its scope, date range, languages, sources, and inclusion or exclusion criteria unless the user explicitly changes them. Record any agreed scope change in the project files before continuing.

## Required workflow

1. Read `workflow/00_topic_scoping.md` and define answerable research questions, boundaries, and target venue.
2. Read `workflow/01_literature_search.md` and complete `protocol/search_protocol.md` before broad retrieval.
3. Retrieve each source into `data/raw/` with the exact query and run metadata appended to `protocol/search_log.jsonl`. Use `scripts/openalex_search.py`, `scripts/semantic_scholar_search.py`, and `scripts/crossref_metadata.py search` for English discovery; use `scripts/import_literature.py` for CNKI, Wanfang, or manual CSV/RIS exports. Never place API keys in project files or command arguments.
4. Read `workflow/02_literature_management.md`; pass all canonical raw CSV files together to `scripts/deduplicate_literature.py`, review both its duplicate and conflict reports, then use Crossref DOI enrichment and `scripts/dblp_validate.py` where applicable. Do not silently overwrite conflicting title, year, venue, or DOI metadata.
5. Populate `evidence/literature.csv` and `evidence/evidence_matrix.md`. Bind every analytical claim to one or more papers and a verifiable evidence location.
6. Read `workflow/03_taxonomy_construction.md`; define one primary classification axis, explicit decision rules, auxiliary labels, overlaps, and counterexamples in `analysis/taxonomy.md`.
7. Read `workflow/04_research_evolution_analysis.md`; distinguish publication order from evidenced citation or method inheritance in `analysis/timeline.md`.
8. Read `workflow/05_method_comparison.md`; compare methods under common assumptions, datasets, metrics, and threat or problem models in `analysis/comparison.md`.
9. Read `workflow/06_challenges_future_directions.md`; derive challenges and future directions from repeated limitations or clearly label them as survey-author inference in `analysis/gaps.md`.
10. Read `workflow/07_survey_writing.md`; synthesize by question, mechanism, trade-off, and evolution instead of summarizing papers sequentially.
11. Read `workflow/08_reference_and_quality_check.md`; run `scripts/bib_validator.py`, inspect citation coverage, and use `prompts/reviewer_check.md` before finalizing.

Load only the workflow or prompt needed for the current stage. Use `prompts/paper_analysis.md`, `prompts/taxonomy_generation.md`, `prompts/search_strategy.md`, and `prompts/section_synthesis.md` as stage-specific aids, not as substitutes for source verification.

## Evidence policy

- Treat metadata-only evidence as insufficient for technical claims.
- Verify the relevant full-text section, formula, figure, table, or experiment before making a key technical judgment.
- Record the page, section, figure, table, or other stable source location.
- Separate paper facts, cross-paper synthesis, and survey-author inference.
- Mark missing data, inaccessible full text, conflicting evidence, and uncertain interpretations explicitly.
- Prefer primary papers for technical claims; use prior surveys for orientation and coverage checks.
- Treat citation counts as discovery signals, not as proof of relevance or quality.
- Do not directly rank results obtained with different datasets, metrics, baselines, scales, or experimental conditions.

## Standard project

```text
projects/<project-name>/
├── survey.yaml
├── protocol/
│   ├── search_protocol.md
│   └── search_log.jsonl
├── data/{raw,cleaned,screened}/
├── evidence/
│   ├── literature.csv
│   └── evidence_matrix.md
├── analysis/
│   ├── taxonomy.md
│   ├── timeline.md
│   ├── comparison.md
│   └── gaps.md
├── manuscript/
│   ├── main.tex
│   ├── sections/
│   └── references.bib
└── figures/
```

Keep source downloads and exports in `data/raw/`; place normalized records in `data/cleaned/`; place auditable inclusion and exclusion decisions in `data/screened/`.

All retrieval and import scripts emit the same fixed canonical CSV schema, including on zero results. Keep provider classifications in `subjects`, explicit author/database keywords in `keywords`, and citation counts paired with `citation_count_source`. Treat `raw_metadata` as provenance, not evidence for technical claims.

## Completion gate

Do not call a manuscript complete until:

- the retrieval date, databases, exact queries, and screening decisions are auditable;
- at least three English metadata sources and one Chinese import source have been considered, or an explicit scope limitation explains why not;
- duplicate records and preprint or formal-version conflicts are resolved;
- every major category has clear decision rules and representative works;
- every major comparison cell has evidence or is marked “未报告”;
- every key judgment is traceable to source content and location;
- the taxonomy, evolution, comparison, challenges, abstract, and conclusion tell one consistent story;
- the survey's own database, language, time, access, and classification limitations are stated;
- references and adjacent claims have been checked for authenticity and support.
