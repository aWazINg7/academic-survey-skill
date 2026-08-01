# Academic Survey Skill

面向高质量计算机领域综述论文的证据化工作流，重点服务《软件学报》《计算机学报》《计算机研究与发展》等中文期刊，也可用于英文 survey。

本项目借鉴 `latex-arxiv-SKILL` 的结构化写作思想，但将重点前移到系统检索、筛选留痕、证据管理、分类体系、研究演化、方法比较、挑战推导与引用核验，而不是直接生成一篇看似完整的稿件。

## 核心原则

- 先建立知识结构，再开始写作；
- 不按论文逐篇拼接摘要；
- 关键判断必须能回溯到论文全文位置；
- 分类体系必须有统一分类轴、判定规则和反例；
- 不直接比较不同实验设置下的数值；
- 区分论文事实、跨论文归纳和综述作者推断；
- 不生成虚构论文、DOI、引用或实验结论。

## 快速开始

从仓库根目录运行：

```bash
python scripts/init_project.py blockchain-fl \
  --topic "区块链联邦学习研究综述" \
  --target-journal "软件学报" \
  --from-year 2018 \
  --to-year 2026
```

默认在 `projects/blockchain-fl/` 创建完整工程。也可以用 `--projects-root <path>` 指定其他位置，用多个 `--language` 或 `--source` 覆盖默认语言和检索来源。

初始化器会先检查全部模板，再通过临时目录原子化创建项目。若同名目录已存在，它会停止并保留原文件，不执行覆盖。

```text
projects/blockchain-fl/
├── survey.yaml
├── protocol/
│   └── search_protocol.md
├── data/
│   ├── raw/
│   ├── cleaned/
│   └── screened/
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

初始化后先检查 `survey.yaml` 和 `protocol/search_protocol.md`，确认主题边界、研究问题、检索来源、时间范围与纳排标准，再开始检索。

`manuscript/main.tex` 是可编译的通用 `ctexart` 骨架，不是任何期刊的官方模板；投稿前必须按目标期刊要求替换文档类和参考文献样式。

## 工作流

1. `workflow/00_topic_scoping.md`：界定主题、研究问题和纳排标准；
2. `workflow/01_literature_search.md`：多源检索与滚雪球扩展；
3. `workflow/02_literature_management.md`：去重、版本合并与证据等级；
4. `workflow/03_taxonomy_construction.md`：构建并验证分类体系；
5. `workflow/04_research_evolution_analysis.md`：分析研究阶段与技术转折；
6. `workflow/05_method_comparison.md`：统一维度比较方法与权衡；
7. `workflow/06_challenges_future_directions.md`：形成挑战和研究议程；
8. `workflow/07_survey_writing.md`：组织正文并避免流水账；
9. `workflow/08_reference_and_quality_check.md`：引用、覆盖和质量审查。

## 辅助脚本

OpenAlex 检索：

```bash
python scripts/openalex_search.py \
  --query "verifiable aggregation federated learning" \
  --from-year 2020 \
  --to-year 2026 \
  --output projects/blockchain-fl/data/raw/openalex.csv
```

文献去重：

```bash
python scripts/deduplicate_literature.py \
  projects/blockchain-fl/evidence/literature.csv \
  --output projects/blockchain-fl/data/cleaned/literature.csv \
  --duplicates projects/blockchain-fl/data/cleaned/duplicates.csv
```

BibTeX 基础检查：

```bash
python scripts/bib_validator.py \
  projects/blockchain-fl/manuscript/references.bib
```

## 仓库目录

```text
workflow/      分阶段综述工作流
prompts/       可复用的检索、论文分析、分类、综合与审稿提示词
templates/     配置、检索协议、证据表、分析表和论文模板
scripts/       初始化、检索、去重、引用与 BibTeX 辅助脚本
tests/         可重复运行的自动化测试
```

## 当前状态

当前版本已实现可复现项目初始化、OpenAlex 检索、文献去重、证据矩阵、引用图谱、BibTeX 基础检查、中文综述 LaTeX 骨架，以及从选题到审校的核心工作流。

多源检索统一、系统筛选流程图、全文证据卡片和在线引用真实性核验仍在开发中，详见 [`TASKS.md`](TASKS.md)。

运行当前测试：

```bash
python -m unittest discover -s tests -v
```
