---
name: academic-survey
version: 0.2.0
description: Build evidence-grounded academic survey papers with systematic literature retrieval, taxonomy construction, comparison, trend analysis, and Chinese journal-oriented writing.
---

# Academic Survey Skill

## Use this skill when

The user asks to plan, research, draft, revise, or quality-check an academic survey or review article. It is especially suitable for computer-science surveys targeting Chinese journals such as 《软件学报》《计算机学报》《计算机研究与发展》, while remaining adaptable to English-language surveys.

## Core rule

Do not begin by drafting prose. First build a verifiable evidence base and an explicit knowledge structure.

## Required workflow

1. **Scope the topic** using `workflow/00_topic_scoping.md`.
2. **Create a retrieval protocol** with databases, query groups, date range, inclusion criteria, and exclusion criteria.
3. **Collect and normalize metadata** using `workflow/01_literature_search.md`, `workflow/02_literature_management.md`, and scripts in `scripts/`.
4. **Construct an evidence table**. Every analytical claim must be traceable to one or more papers.
5. **Build a taxonomy** using `workflow/03_taxonomy_construction.md`. Categories should have a clear classification axis and should not merely reproduce paper names.
6. **Analyze research evolution** with milestones, transitions, and unresolved tensions.
7. **Compare methods** under common dimensions rather than summarizing papers sequentially.
8. **Derive challenges and future directions** from repeated evidence, not speculation alone.
9. **Draft the survey** using the target journal's structure and language conventions.
10. **Run reference and quality checks** before finalizing.

## Evidence policy

- Never invent papers, authors, venues, DOI values, experimental results, or citations.
- Separate verified full-text evidence from abstract-only or metadata-only evidence.
- Mark uncertain interpretations explicitly.
- Prefer primary papers for technical claims and existing surveys only for orientation.
- Do not use citation count as a substitute for relevance or quality.

## Anti-patterns

Reject or revise outputs that:

- list papers one by one without synthesis;
- use unexplained categories;
- mix classification axes at the same hierarchy level;
- claim a research gap based on one paper;
- overstate novelty, completeness, or consensus;
- cite a source that does not support the adjacent sentence;
- discuss only recent work while omitting foundational milestones;
- present future directions as slogans without technical obstacles and evaluation criteria.

## Standard deliverables

A complete project should contain:

```text
survey-project/
├── scope.md
├── search_protocol.md
├── literature/
│   ├── raw_results.csv
│   ├── included_papers.csv
│   ├── excluded_papers.csv
│   └── references.bib
├── analysis/
│   ├── evidence_matrix.md
│   ├── taxonomy.md
│   ├── evolution.md
│   ├── comparison.md
│   └── challenges.md
├── figures/
├── manuscript/
│   ├── outline.md
│   └── main.tex or main.docx
└── quality_report.md
```

## Completion gate

A manuscript is not ready until:

- the retrieval date and search protocol are documented;
- duplicate records are resolved;
- inclusion and exclusion decisions are auditable;
- every major category has representative and recent works;
- comparison dimensions are consistently applied;
- citations are verified against source content;
- limitations of the survey itself are stated;
- the abstract, introduction, taxonomy, comparison, challenges, and conclusion tell one consistent story.
