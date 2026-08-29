---
title: GitHub 数据库故障转移事故复盘摘要（2018-10-21）
document_type: public_postmortem_summary
incident_type: network_partition_database_failover
service: database-replication-topology
data_origin: public_production_postmortem
source_url: https://github.blog/news-insights/company-news/oct21-post-incident-analysis/
source_published_at: 2018-10-30
---
# GitHub 数据库故障转移事故复盘摘要

## 数据来源与适用范围

本文是对 GitHub 公开生产事故复盘的中文摘要，不是本项目采集的监控、日志或调用链原始数据。事故发生于 2018 年 10 月 21 日至 22 日，所有时间均为 UTC。诊断时应将本文作为「网络分区后的数据库复制与故障转移」知识参考，不能将 GitHub 的架构细节直接当作当前系统拓扑。

## 影响与现象

GitHub 报告称，例行维护导致美国东海岸网络枢纽与主数据中心失联 43 秒。此后服务降级持续 24 小时 11 分钟。部分用户数据展示过期或不一致，Webhook 投递和 GitHub Pages 构建在事故期间大部分时间不可用。GitHub 表示没有用户数据丢失，但需要人工核对少量数据库写入。

## 已确认根因与扩散路径

网络分区期间，数据库编排系统在仍可形成法定人数的节点上进行故障转移，将写入主库切换至美国西海岸。网络恢复后，东西海岸数据库分别存在未相互复制的写入。为了保护数据一致性，团队不能直接安全地切回原数据中心。应用跨区域访问新的主库产生额外延迟，数据库复制拓扑也处于应用无法支持的状态。

## 可用于诊断的证据链

1. 网络维护后出现跨站点连接中断。
2. 监控告警触发，编排系统 API 显示多个数据库集群拓扑异常。
3. 主库切换后，两个站点均存在对方未复制的写入，无法安全回切。
4. 数据库主库恢复到就近站点后，读副本仍有数小时复制延迟，用户可见数据不一致。
5. 增加读副本以降低整体利用率后，复制逐步追平，最后恢复原拓扑并处理积压任务。

## 处置与预防

官方选择「故障前推」而非以牺牲数据一致性换取更短恢复时间：先停止部分写入型后台任务，再从备份恢复、建立复制关系、恢复稳定拓扑，并在系统稳定后处理积压。该案例说明，故障转移策略需要与应用的延迟和一致性假设一致。

## 对当前诊断的启发

网络分区或主从切换后，先确认写入位置、复制延迟和数据一致性边界，再执行回切。低延迟依赖跨区域主库时，应用超时不等同于应用本身故障。暂停写入、切换主库或处理积压任务都会影响业务，必须经人工审批。

## 原始复盘

GitHub Blog：《October 21 post-incident analysis》
<https://github.blog/news-insights/company-news/oct21-post-incident-analysis/>
