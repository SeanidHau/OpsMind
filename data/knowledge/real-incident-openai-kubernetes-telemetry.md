---
title: OpenAI Kubernetes 遥测发布事故复盘摘要（2024-12-11）
document_type: public_postmortem_summary
incident_type: kubernetes_control_plane_overload
service: kubernetes-control-plane
data_origin: public_production_postmortem
source_url: https://status.openai.com/incidents/01JMYB483C404VMPCW726E8MET/write-up
source_published_at: 2024-12-11
---
# OpenAI Kubernetes 遥测发布事故复盘摘要

## 数据来源与适用范围

本文是对 OpenAI 公开生产事故复盘的中文摘要，不是本项目采集的监控、日志或调用链原始数据。事故发生于 2024 年 12 月 11 日，原文时间使用 PST。诊断时应将本文作为「大规模集群中的控制面负载」知识参考，不能把文中的服务影响外推到其他集群。

## 影响与现象

OpenAI 报告称，ChatGPT、API 和 Sora 在 15:16 至 19:38 期间出现显著降级或不可用。新遥测服务开始全量发布后，用户可见故障并非立刻发生；节点上的 DNS 缓存在一段时间内仍可提供旧记录，缓存过期后服务发现开始失败。

## 已确认根因

新遥测服务的配置使每个节点执行成本随集群规模增长的 Kubernetes API 操作。大量节点同时执行这些操作，导致大型集群的 Kubernetes API Server 过载，控制面不可用。控制面异常进一步影响 DNS 服务发现，造成级联故障。预发布测试所在的集群规模较小，因此未暴露这一问题。

## 可用于诊断的证据链

1. 遥测服务发布后，Kubernetes API 负载在大集群中升高。
2. 控制面不可用，但数据面在初期仍能依赖 DNS 缓存维持部分运行。
3. DNS 缓存到期后，服务间解析失败，用户请求开始受影响。
4. 隔离高成本 API 请求、降低集群总 API 负载并扩容 API Server 后，集群逐步恢复。

## 处置与预防

官方处置包括缩小集群规模以降低总 API 负载、阻断新的高成本管理 API 请求，以及扩容 Kubernetes API Server。复盘强调：发布验证除了工作负载 CPU 和内存，还需要覆盖控制面容量；分阶段发布和集群健康监控应覆盖最大规模集群。

## 对当前诊断的启发

出现大范围服务发现失败时，不能只检查业务 Pod 或 DNS 服务本身。应同时检查 Kubernetes API Server 的负载、管理请求速率、集群规模差异，以及最近是否发布了具有全节点作用域的 Agent 或 DaemonSet。缩容、阻断请求和扩容控制面属于变更操作，必须经人工审批。

## 原始复盘

OpenAI Status：《API, ChatGPT & Sora Facing Issues》
<https://status.openai.com/incidents/01JMYB483C404VMPCW726E8MET/write-up>
