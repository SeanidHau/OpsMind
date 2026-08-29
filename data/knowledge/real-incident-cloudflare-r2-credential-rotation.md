---
title: Cloudflare R2 凭证轮换事故复盘摘要（2025-03-21）
document_type: public_postmortem_summary
incident_type: credential_rotation_authentication_failure
service: object-storage-gateway
data_origin: public_production_postmortem
source_url: https://blog.cloudflare.com/cloudflare-incident-march-21-2025/
source_published_at: 2025-03-25
---
# Cloudflare R2 凭证轮换事故复盘摘要

## 数据来源与适用范围

本文是对 Cloudflare 公开生产事故复盘的中文摘要，不是本项目采集的监控、日志或调用链原始数据。事故发生于 2025 年 3 月 21 日，所有时间均为 UTC。诊断时应将本文作为「变更后认证失败」的知识参考，不能将其中的影响数字套用到当前系统。

## 影响与现象

Cloudflare 报告称，R2 对象存储在 21:38 至 22:45 期间出现错误率升高。写入操作全部失败，读取操作的全球失败比例约为 35%。依赖 R2 的部分服务也受到影响。官方说明此次事故没有造成数据丢失或数据损坏。

## 已确认根因

团队轮换 R2 Gateway 与存储基础设施之间使用的凭证时，部署命令遗漏了生产环境参数。新凭证被部署到默认环境，而不是生产环境。旧凭证随后从存储基础设施中删除，生产 Gateway 仍使用旧凭证，因而无法向存储后端完成认证。

## 可用于诊断的证据链

1. 凭证轮换完成旧凭证删除后，R2 可用性指标逐步下降。
2. 元数据类操作未受影响，而依赖对象读取或写入的操作出现错误。
3. 排查生产 Worker 的发布历史后，团队确认新凭证位于非生产环境。
4. 将新凭证部署到正确的生产 Worker 后，可用性立即恢复。

## 处置与预防

官方处置是将凭证部署至正确的生产 Worker。后续措施包括：在日志中记录用于认证的凭证 ID 后缀；在删除旧凭证前确认日志中的凭证 ID；使用强制环境配置的发布工具替代手工命令；以及在发布前验证新凭证的全局生效状态。

## 对当前诊断的启发

当某次凭证、密钥或配置轮换后出现下游认证错误时，先核对生产环境的实际版本、密钥标识和发布记录。不要仅依据「部署命令已执行」推断生产环境已使用新凭证。涉及密钥删除、回滚或重新部署的操作必须经人工审批。

## 原始复盘

Cloudflare：《Cloudflare incident on March 21, 2025》
<https://blog.cloudflare.com/cloudflare-incident-march-21-2025/>
