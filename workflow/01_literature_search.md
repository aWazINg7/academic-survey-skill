# 01 多源文献检索与导入

## 目标

用可复现的检索式覆盖英文与中文文献，并让每次成功运行都能追溯到来源、时间、结果数和缓存状态。广泛检索前先完成 `protocol/search_protocol.md`。

## 来源分工

- OpenAlex：跨学科主题检索；当前 API 需要免费的 `OPENALEX_API_KEY`。
- Semantic Scholar：相关度主题检索；`SEMANTIC_SCHOLAR_API_KEY` 可选，使用密钥时默认按 1 请求/秒限流。
- Crossref：DOI 元数据检索与按 DOI 补全。
- DBLP：对计算机会议、期刊及出版年份进行校验，不把它当作全文检索源。
- CNKI、万方或人工导出：从本地 CSV、TSV 或 RIS 导入；不自动抓取受限网站。

IEEE Xplore、ACM Digital Library 等来源可用于人工覆盖检查，但只有在其导出记录进入统一表后，才计入可审计证据库。

## 运行约定

1. 每个来源写入独立的 `data/raw/<source>.csv`。
2. 所有命令传入同一个 `--log protocol/search_log.jsonl`；该日志逐次追加成功检索、导入与合并的权威记录，失败命令以终端错误和非零退出码报告。
3. 缓存放在 `data/raw/.cache/<source>/`，重复运行默认复用七天内的成功响应；用 `--refresh` 明确绕过缓存。
4. API 密钥只从环境变量读取，不写进 `survey.yaml`、命令参数、CSV、日志或缓存。
5. 为同一研究问题保存中文、英文、缩写和机制词组合，不把单一检索式的结果当作完整覆盖。

## 检索策略

组合执行并记录：

1. 主题词、同义词和机制词检索；
2. 参考文献与被引文献滚雪球；
3. 关键作者和研究团队扩展；
4. 目标会议、期刊和时间范围核验；
5. 近三年工作与已有综述的覆盖差异检查。

## 最低元数据

每条记录至少尽力保留题名、作者、年份、来源、DOI/arXiv ID、URL，以及 `source`、`source_id`、`retrieval_id`、`retrieved_at`、`search_query`。缺失值保持为空，不推测题名、DOI、作者或发表信息。

来源主题分类写入 `subjects`；只有作者或数据库明确给出的关键词写入 `keywords`。引用次数必须同时记录 `citation_count_source`，只用于发现线索，不用于证明质量。

## 完成标准

- 同一主题至少形成 OpenAlex、Semantic Scholar、Crossref 三个英文来源记录，以及一份 CNKI、万方或人工中文导入记录；
- 四类记录使用完全相同的规范表头；
- `protocol/search_log.jsonl` 可回溯每次成功检索或导入的精确检索式、UTC 时间、来源、筛选条件、输出文件和结果数量；
- 网络失败重试、限流和缓存行为均有离线测试覆盖。
