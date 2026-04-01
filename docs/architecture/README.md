# Architecture Notes

这个目录存放本次针对 BettaFish 的一组架构方案文档与 Mermaid 架构图。

## 文件说明

- `01_current_bettafish_multi_agent_report.md`
  - 现有 BettaFish 多 Agent 与报表生成链路说明。
  - 包含当前各引擎的职责、检索轮次、Forum 协作方式、Report Engine 装订流程。

- `02_tea_drink_business_intelligence_target.md`
  - “茶饮经营情报平台”目标形态方案。
  - 固定报表 schema、推荐的数据层/检索层/报表层边界，以及精简后的引擎架构。

- `03_crawler_only_ingestion_service.md`
  - “纯爬虫搜索落库版”方案。
  - 说明当前登录态、扫码、代理、Docker/Linux 单独部署边界。

- `04_tea_drink_page_information_architecture.md`
  - “茶饮经营情报平台”页面信息架构与用户工作流。
  - 定义页面导航、核心工作台、交互顺序和一期/二期边界。

- `05_tea_drink_database_design.md`
  - 经营情报平台数据库设计。
  - 说明如何从现有平台原始表过渡到统一检索索引、事件层和报表层。

- `06_tea_drink_api_design.md`
  - 目标 API 契约设计。
  - 包括检索、采集、报表、配置、健康检查和兼容迁移策略。

- `07_tea_drink_docker_split_plan.md`
  - Docker / Linux 部署拆分方案。
  - 包括爬虫版、检索版、完整情报版的服务切分与阶段建议。

## Mermaid 图文件

- `diagrams/01_current_multi_agent_overall.mmd`
- `diagrams/01_current_insight_loop.mmd`
- `diagrams/02_tea_drink_target_architecture.mmd`
- `diagrams/03_crawler_only_architecture.mmd`
- `diagrams/04_tea_drink_page_ia.mmd`
- `diagrams/05_tea_drink_data_model.mmd`
- `diagrams/06_tea_drink_api_flow.mmd`
- `diagrams/07_tea_drink_docker_split.mmd`

## 推荐阅读顺序

1. 先看 `01_current_bettafish_multi_agent_report.md`
2. 再看 `02_tea_drink_business_intelligence_target.md`
3. 最后看 `03_crawler_only_ingestion_service.md`
4. 如果准备落地产品，继续看 `04` 到 `07`

## 这批文档的用途

- 帮你把“现状为什么慢、为什么跑偏”讲清楚
- 帮你把“茶饮经营情报平台”产品边界先定死
- 帮你把“只保留搜索落库爬虫”这一版的单独部署边界讲清楚
- 帮你把页面、库表、API、容器拆分直接细化到可评审层级
