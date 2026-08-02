# 02 文献管理与证据库构建

## 目标

把 `data/raw/` 中的多源规范记录合并为可追踪、可去重、可审计的文献库，同时保留互补元数据和冲突，不因选中一条“主记录”而丢失其他来源。

## 规范字段

字段分为三组：

- 书目信息：`title`、`authors`、`year`、`publication_date`、`venue`、`publication_type`、卷期页、DOI、arXiv ID、URL、摘要、关键词与主题；
- 溯源信息：`source`、`source_id`、`source_url`、`retrieval_id`、`retrieved_at`、`search_query`、`search_rank` 和 `metadata_status`；
- 人工分析信息：研究问题、核心思想、方法族、信任/威胁模型、数据集、指标、发现、局限、证据等级和文献角色。

检索与导入脚本只填写书目和溯源字段，不从元数据自动生成技术结论。

## 合并与去重

将所有原始表同时传给去重脚本：

```bash
python scripts/deduplicate_literature.py \
  projects/<project>/data/raw/openalex.csv \
  projects/<project>/data/raw/semantic_scholar.csv \
  projects/<project>/data/raw/crossref.csv \
  projects/<project>/data/raw/chinese.csv \
  --output projects/<project>/data/cleaned/literature.csv \
  --duplicates projects/<project>/data/cleaned/duplicates.csv \
  --conflicts projects/<project>/data/cleaned/conflicts.csv \
  --log projects/<project>/protocol/search_log.jsonl
```

脚本只把语法合理且完全一致的 DOI 当作强标识，再考虑显式 arXiv ID 和标准化后完全一致的题名，使用连通分组处理传递重复。`N/A`、`-`、`无` 等 DOI 占位值不参与身份判断；弱匹配组内只要出现互斥 DOI、arXiv ID、年份或第一作者就不合并。第一作者比较使用姓和名字 token，同时容许单字母名字缩写；若并非每条记录都有作者证据，刊会冲突也会阻止题名合并。其余分组逐字段补全缺失信息，`source`、来源 ID 和检索运行 ID 取并集。题名、年份、来源刊会、DOI 或 arXiv ID 冲突不会自动覆盖，而是写入 `conflicts.csv` 供人工核验。若不同论文携带相同的输入 `paper_id`，脚本会在写出结果前报错，要求先修正源数据。

正式发表版本通常作为主记录，预印本链接和版本信息作为补充来源保留。高相似但不完全相同的题名不自动合并，应由人工结合作者、年份和全文判断。

## 人工核验

1. 阅读 `duplicates.csv` 的 `duplicate_of` 与 `matched_on`；
2. 逐项处理 `conflicts.csv`，必要时回查 Crossref、DBLP 或出版方页面；
3. 将确认后的 `data/cleaned/literature.csv` 复制或审阅合并进 `evidence/literature.csv`；
4. 仅在核验正文后填写分析字段和证据等级。

证据等级统一为：`metadata`（仅元数据）、`partial_fulltext`（已核验相关章节/图表/实验）和 `fulltext`（已完整阅读全文）。`metadata` 记录不得支撑关键技术判断。

## 完成标准

- 每个合并记录有稳定 `paper_id`，且能回溯所有来源与检索运行；
- 重复依据和元数据冲突均有独立报告；
- DOI URL/裸 DOI、arXiv 版本号和传递重复可正确合并；
- 关键分析结论均绑定论文及页码、章节、图表或其他稳定全文位置。
