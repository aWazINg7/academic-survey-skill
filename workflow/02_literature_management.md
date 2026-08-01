# 02 文献管理与证据库构建

## 目标

将检索结果整理为可追踪、可去重、可审计的综述证据库，避免后期出现重复引用、信息冲突和“凭印象写作”。

## 必备字段

每篇文献至少记录：

- title
- authors
- year
- venue
- doi
- url
- abstract
- keywords
- method_family
- research_problem
- core_idea
- evaluation_setting
- main_findings
- limitations
- evidence_level
- citation_status

## 去重规则

按以下优先级去重：

1. DOI 完全一致
2. arXiv ID 完全一致
3. 标题标准化后完全一致
4. 标题高相似且第一作者、年份一致

保留正式发表版本，预印本作为补充来源，不重复计数。

## 文献角色标注

- `foundational`：奠基性工作
- `representative`：典型方法
- `state_of_the_art`：近期代表性进展
- `survey`：已有综述
- `benchmark`：数据集、基准或评价体系
- `position`：观点、展望或评论性文章

## 证据等级

- A：全文已核验，实验与结论清晰
- B：正文或官方元数据已核验
- C：仅摘要或二手来源

正文中的关键判断优先引用 A 级证据。C 级证据不得支撑核心结论。

## 输出

- `data/literature.csv`
- `data/literature.bib`
- `data/evidence_notes/`
- `reports/coverage_report.md`

## 完成标准

- 无明显重复记录
- 每条核心结论可追溯到具体文献
- 经典工作、代表性工作与近三年进展均有覆盖
- 已有综述与本文拟建分类体系之间的差异已记录
