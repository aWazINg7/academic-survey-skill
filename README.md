# Academic Survey Skill

面向高质量计算机领域综述论文的证据化工作流，重点服务《软件学报》《计算机学报》《计算机研究与发展》等中文期刊，也可用于英文 survey。

本项目将重点放在系统检索、筛选留痕、证据管理、分类体系、研究演化、方法比较、挑战推导与引用核验，而不是直接生成一篇看似完整但无法追溯的稿件。

## 核心原则

- 先建立知识结构，再开始写作；
- 不按论文逐篇拼接摘要；
- 关键判断必须能回溯到论文全文位置；
- 分类体系必须有统一分类轴、判定规则和反例；
- 不直接比较不同实验设置下的数值；
- 区分论文事实、跨论文归纳和综述作者推断；
- 不生成虚构论文、DOI、引用或实验结论。

## 快速开始

从仓库根目录初始化项目：

```bash
python scripts/init_project.py blockchain-fl \
  --topic "区块链联邦学习研究综述" \
  --target-journal "软件学报" \
  --from-year 2018 \
  --to-year 2026
```

默认在 `projects/blockchain-fl/` 创建完整工程，并启用三个英文检索源、DBLP 校验和 `chinese_import` 中文导入入口。用 `--projects-root <path>` 指定其他位置，用多个 `--language` 或 `--source` 覆盖默认语言和来源。初始化器先检查全部模板，再通过临时目录原子化创建项目；若同名目录已存在，它会停止且不覆盖原文件。

```text
projects/blockchain-fl/
├── survey.yaml
├── protocol/
│   ├── search_protocol.md
│   └── search_log.jsonl
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

先检查 `survey.yaml` 和 `protocol/search_protocol.md`，确认主题边界、研究问题、检索来源、时间范围与纳排标准，再开始检索。`manuscript/main.tex` 是可编译的通用 `ctexart` 骨架，不是任何期刊的官方模板；投稿前必须按目标期刊要求替换文档类和参考文献样式。

## 多源检索

所有来源都输出同一个固定 CSV 表头；即使零结果也会写表头。每条记录保留来源和检索运行 ID，每个命令可把精确检索式、UTC 时间、筛选条件、结果数、重试与缓存统计追加到 `protocol/search_log.jsonl`。

### 1. OpenAlex

当前 OpenAlex API 需要免费密钥。只把密钥放入环境变量，不要写入项目文件：

```bash
export OPENALEX_API_KEY="<your key>"

python scripts/openalex_search.py \
  --query "verifiable aggregation federated learning" \
  --from-year 2020 \
  --to-year 2026 \
  --output projects/blockchain-fl/data/raw/openalex.csv \
  --cache-dir projects/blockchain-fl/data/raw/.cache/openalex \
  --log projects/blockchain-fl/protocol/search_log.jsonl
```

脚本使用当前的 `per_page<=100`、cursor 分页和 `topics` 字段，密钥会在缓存键和错误信息中脱敏。密钥与用量说明见 [OpenAlex 官方文档](https://developers.openalex.org/api-reference/authentication)。

### 2. Semantic Scholar

```bash
python scripts/semantic_scholar_search.py \
  --query "verifiable aggregation federated learning" \
  --from-year 2020 \
  --to-year 2026 \
  --output projects/blockchain-fl/data/raw/semantic_scholar.csv \
  --cache-dir projects/blockchain-fl/data/raw/.cache/semantic_scholar \
  --log projects/blockchain-fl/protocol/search_log.jsonl
```

匿名检索可用；如果设置 `SEMANTIC_SCHOLAR_API_KEY`，脚本通过 `x-api-key` 请求头使用它。相关度检索最多返回前 1000 条，默认按 1 请求/秒执行。接口契约见 [Semantic Scholar Graph API](https://api.semanticscholar.org/api-docs/graph)。

### 3. Crossref

主题检索：

```bash
python scripts/crossref_metadata.py search \
  --query "verifiable aggregation federated learning" \
  --from-year 2020 \
  --to-year 2026 \
  --mailto researcher@example.org \
  --output projects/blockchain-fl/data/raw/crossref.csv \
  --cache-dir projects/blockchain-fl/data/raw/.cache/crossref \
  --log projects/blockchain-fl/protocol/search_log.jsonl
```

按已有 DOI 补全缺失字段：

```bash
python scripts/crossref_metadata.py enrich \
  projects/blockchain-fl/data/cleaned/literature.csv \
  --mailto researcher@example.org \
  --output projects/blockchain-fl/data/cleaned/literature.crossref.csv \
  --log projects/blockchain-fl/protocol/search_log.jsonl
```

补全只填空值；题名、年份或刊会冲突会写入 `notes`，不会静默覆盖原值。默认 1 请求/秒符合 Crossref 公共列表池当前限制；提供 `--mailto` 可进入 polite pool。参见 [Crossref REST API 使用建议](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/)。

### 4. 中文 CSV、TSV 或 RIS

从 CNKI、万方或人工导出的本地文件导入，不自动抓取受限站点：

```bash
python scripts/import_literature.py cnki-export.csv \
  --source cnki \
  --query "区块链 联邦学习" \
  --output projects/blockchain-fl/data/raw/chinese.csv \
  --log projects/blockchain-fl/protocol/search_log.jsonl
```

导入器自动识别 CSV/TSV/RIS、UTF-8-SIG、UTF-8 和 GB18030，也支持显式指定 UTF-16、Big5、格式、编码和分隔符。RIS 的多作者、多关键词和续行会被保留；未知导出字段写入 `raw_metadata`，不会丢失。

## 合并与去重

把三个英文来源和一个中文来源同时合并：

```bash
python scripts/deduplicate_literature.py \
  projects/blockchain-fl/data/raw/openalex.csv \
  projects/blockchain-fl/data/raw/semantic_scholar.csv \
  projects/blockchain-fl/data/raw/crossref.csv \
  projects/blockchain-fl/data/raw/chinese.csv \
  --output projects/blockchain-fl/data/cleaned/literature.csv \
  --duplicates projects/blockchain-fl/data/cleaned/duplicates.csv \
  --conflicts projects/blockchain-fl/data/cleaned/conflicts.csv \
  --log projects/blockchain-fl/protocol/search_log.jsonl
```

脚本只把语法合理的 DOI 当作强标识；`N/A`、`-`、`无` 等占位值会保留供人工检查，但不会触发合并。随后保守处理 arXiv ID 和标准化题名关系，能处理传递重复；弱匹配组内出现互斥 DOI、arXiv ID、年份或第一作者时保持为独立论文。第一作者按姓和名字 token 比较，并兼容 `A. Smith` 与 `Alice Smith` 这类缩写；若并非每条记录都有作者证据，刊会冲突也会阻止题名合并。逐字段保留互补信息，并合并来源、来源 ID 和检索运行 ID。`duplicates.csv` 记录 `duplicate_of` 与 `matched_on`，`conflicts.csv` 单独列出题名、年份、刊会、DOI 和 arXiv ID 冲突。若不同论文携带相同的输入 `paper_id`，命令会在写出任何结果前明确报错，避免产生歧义引用。旧的单输入调用方式仍然可用。

## DBLP 校验

对合并后的计算机会议和期刊记录进行保守匹配：

```bash
python scripts/dblp_validate.py \
  projects/blockchain-fl/data/cleaned/literature.csv \
  --output projects/blockchain-fl/data/cleaned/literature.dblp.csv \
  --report projects/blockchain-fl/data/cleaned/dblp_report.csv \
  --log projects/blockchain-fl/protocol/search_log.jsonl
```

默认匹配阈值为 0.88；不同 DOI，或无精确 DOI 时年份、第一作者冲突，都会否决题名匹配；作者证据缺失时，刊会冲突同样会否决弱匹配。请人工复核 `ambiguous` 和所有 `dblp_conflict:*`。脚本默认请求间隔 1.5 秒并遵循 `Retry-After`；大量或穷尽式检索应使用 DBLP 数据快照，而不是高频调用在线搜索 API。参见 [DBLP Search API](https://dblp.org/faq/How%2Bto%2Buse%2Bthe%2Bdblp%2Bsearch%2BAPI)。

## 缓存与失败处理

- 网络请求默认缓存成功 JSON 七天；`--refresh` 绕过缓存。
- 408、425、429 和常见 5xx 会指数退避重试，并优先遵循 `Retry-After`。
- 400、401、403、404 不会盲目重试。
- 输出采用临时文件后原子替换；后续页面失败时不会截断既有成功文件。
- 缓存、日志和终端错误不保存 API 密钥。

## 工作流

1. `workflow/00_topic_scoping.md`：界定主题、研究问题和纳排标准；
2. `workflow/01_literature_search.md`：多源检索、中文导入与滚雪球扩展；
3. `workflow/02_literature_management.md`：字段统一、去重、版本合并与证据等级；
4. `workflow/03_taxonomy_construction.md`：构建并验证分类体系；
5. `workflow/04_research_evolution_analysis.md`：分析研究阶段与技术转折；
6. `workflow/05_method_comparison.md`：统一维度比较方法与权衡；
7. `workflow/06_challenges_future_directions.md`：形成挑战和研究议程；
8. `workflow/07_survey_writing.md`：组织正文并避免流水账；
9. `workflow/08_reference_and_quality_check.md`：引用、覆盖和质量审查。

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
scripts/       初始化、检索、导入、去重、引用与 BibTeX 辅助脚本
tests/         不依赖实时排名和引用数的固定离线测试
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖项目初始化、统一表头、API 响应字段归一化、429/非重试错误、缓存与密钥脱敏、UTF-8-SIG/GB18030/RIS 导入、Crossref/DBLP 冲突保护、跨文件传递去重和旧单输入命令兼容性。

## 当前状态

已实现可复现项目初始化、OpenAlex/Semantic Scholar/Crossref 多源检索、Crossref DOI 补全、DBLP 元数据校验、中文 CSV/RIS 导入、规范字段与审计日志、网络重试/限流/缓存、跨源去重合并、证据矩阵、引用图谱、BibTeX 基础检查、中文综述 LaTeX 骨架，以及从选题到审校的核心工作流。

系统筛选流程图、全文证据卡片和在线引用真实性核验仍在开发中，详见 [`TASKS.md`](TASKS.md)。
