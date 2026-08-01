# Academic Survey Skill

面向高质量计算机领域综述论文的 AI 辅助工作流，重点服务《软件学报》《计算机学报》《计算机研究与发展》等中文期刊，也可用于英文 survey。

本项目借鉴 `latex-arxiv-SKILL` 的结构化写作思想，但不把目标限定为生成 LaTeX 稿件，而是将重点前移到：系统检索、证据管理、分类体系、研究演化、方法比较、挑战推导和引用核验。

## 核心原则

- 先建立知识结构，再开始写作
- 不按论文逐篇拼接摘要
- 所有关键判断都应可追溯到文献证据
- 分类体系必须有判定规则，并通过反例测试
- 不强行比较不同实验设置下的数值
- 未来方向必须从现有局限和证据中推导

## 工作流

1. `workflow/00_topic_scoping.md`：界定主题、研究问题和纳排标准
2. `workflow/01_literature_search.md`：多源检索与滚雪球扩展
3. `workflow/02_literature_management.md`：去重、版本合并与证据等级
4. `workflow/03_taxonomy_construction.md`：构建并验证分类体系
5. `workflow/04_research_evolution_analysis.md`：分析研究阶段与技术转折
6. `workflow/05_method_comparison.md`：统一维度比较方法与权衡
7. `workflow/06_challenges_future_directions.md`：形成挑战和研究议程
8. `workflow/07_survey_writing.md`：组织正文并避免流水账
9. `workflow/08_reference_and_quality_check.md`：引用、覆盖和质量审查

## 目录

```text
workflow/      分阶段综述工作流
prompts/       可复用的论文分析、分类与审稿提示词
templates/     综述提纲和文献表模板
scripts/       文献去重等辅助脚本
examples/      示例项目（后续补充）
```

## 快速开始

### 1. 建立文献表

复制模板：

```bash
cp templates/literature_table.csv data/literature.csv
```

### 2. 填写结构化记录

优先记录正式发表版本。每篇论文至少填写研究问题、核心思想、关键假设、实验设置、主要结论、局限和证据等级。

### 3. 去重

```bash
python scripts/deduplicate_literature.py data/literature.csv \
  --output data/literature_deduplicated.csv \
  --duplicates reports/duplicates.csv
```

脚本按 DOI、arXiv ID 和规范化标题去重，并优先保留元数据更完整的正式版本。

### 4. 构建 taxonomy

使用 `prompts/taxonomy_generation.md`，但必须人工检查分类边界、交叉方法和反例。

### 5. 写作与审查

先完成分类、演化和比较，再写引言、摘要与结论。完成初稿后，使用 `prompts/reviewer_check.md` 做中文综述期刊风格审查。

## 预期产物

```text
data/literature.csv
 taxonomy/taxonomy.md
 comparison/method_matrix.csv
 evolution/stages.md
 future/research_agenda.csv
 paper/main.md
 paper/references.bib
 reports/reference_audit.md
 reports/reviewer_report.md
```

## 当前状态

当前版本已包含完整核心工作流、三类提示词、文献表模板和基础去重脚本。后续可继续扩展 OpenAlex / Semantic Scholar 检索、BibTeX 核验、引用图谱和《软件学报》排版模板。
