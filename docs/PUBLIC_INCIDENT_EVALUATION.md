# 公开生产事故复盘评测记录

## 目的与边界

本记录验证 OpsMind 能否使用公开、由事故方发布的生产事故复盘完成检索与受控诊断。评测数据是复盘中公开的现象、根因、处置和预防信息，不包含事故方的原始指标、日志或调用链。因此，评测结果证明 RAG 检索和 Harness 推理链路可用，不证明 OpsMind 能直接访问 Cloudflare、OpenAI 或 GitHub 的生产观测系统。

评测时间：2026-08-29 19:01 至 19:02 UTC。

运行配置：LLM 为 `deepseek-v4-flash`，Embedding 模型为本地 OpenAI 兼容端点上的 `qwen3-embedding`。未记录任何密钥。

## 样本来源

| 案例 | 公开生产事故来源 | 已知根因 |
| --- | --- | --- |
| Cloudflare R2 凭证轮换 | <https://blog.cloudflare.com/cloudflare-incident-march-21-2025/> | 新凭证部署到非生产环境，旧凭证删除后生产 Gateway 无法认证。 |
| OpenAI Kubernetes 遥测发布 | <https://status.openai.com/incidents/01JMYB483C404VMPCW726E8MET/write-up> | 全节点遥测配置造成 Kubernetes API Server 过载，进一步影响 DNS 服务发现。 |
| GitHub 数据库故障转移 | <https://github.blog/news-insights/company-news/oct21-post-incident-analysis/> | 网络分区触发异地故障转移，形成未复制写入与复制延迟，无法安全回切。 |

## 检索评测

运行命令：

```bash
uv run python -m scripts.evaluate_retrieval \
  --cases-file data/evaluations/public_incident_retrieval_cases.json \
  --top-k 3 \
  --fail-on-miss
```

实际结果：

| 指标 | 结果 |
| --- | ---: |
| 样本数 | 3 |
| Recall@3 | 1.00 |
| MRR | 1.00 |
| 目标来源首次命中排名 | 3/3 为第 1 位 |

检索结果按来源评估，而不是按分块去重。每个案例的目标复盘都以第 1 位来源返回。

## 端到端 Harness 评测

运行命令：

```bash
uv run python -m scripts.run_benchmark \
  --cases-file data/evaluations/public_incident_diagnosis_cases.json \
  --knowledge-only
```

`--knowledge-only` 只注册 `query_knowledge`，避免评测误用项目内置的合成日志、指标和拓扑场景。

| 案例 | 运行 ID | 状态 | 根因结论与公开复盘一致 | 模型调用 | 知识检索调用 | 模型调用时延合计 | 知识检索时延合计 | 输入 / 输出 / 总 Token |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Cloudflare R2 | `2d362394-1041-4f0a-9f84-bbdd5809fe4e` | completed | 是：生产环境未切换到新凭证，旧凭证已删除。 | 4 | 2 | 13,834 ms | 2,246 ms | 11,734 / 1,592 / 13,326 |
| OpenAI Kubernetes | `ecb9895c-a5d8-423c-bf97-969591a3900f` | completed | 是：全节点遥测操作压垮 Kubernetes API Server，并影响 DNS 服务发现。 | 4 | 2 | 14,809 ms | 1,782 ms | 11,695 / 1,854 / 13,549 |
| GitHub 数据库故障转移 | `be7df9e2-1360-4b59-b0f9-e67ec79d3887` | completed | 是：网络分区后的异地故障转移导致复制延迟和数据不一致。 | 4 | 2 | 13,392 ms | 1,643 ms | 10,071 / 1,613 / 11,684 |

汇总：3/3 完成；总模型调用 12 次；总知识检索 6 次；模型调用时延合计 42,035 ms；知识检索时延合计 5,671 ms；总 Token 为 38,559。

模型调用时延和 Token 来自 Harness 轨迹中的 `model_called` 事件。知识检索时延来自 `tool_finished` 事件。两者不等同于整次运行的端到端墙钟时长。

## 结论

在本次固定的 3 个公开事故样本上，OpsMind 能检索到正确复盘，并在只使用复盘证据的约束下给出与公开根因一致的候选结论和安全建议。下一步若需要验证实时诊断能力，应接入经授权的 Prometheus、Loki、Jaeger、Kubernetes 或 CMDB 数据源，并用实际事故窗口重新评测。
