# 第 10 阶段：确定性 RRF 融合

## 目标

本阶段将多个检索器的排序结果融合为统一的证据候选列表。当前接入 BM25 结果，并为后续向量检索预留相同输入接口。

本阶段不接入 Qdrant、不生成 embedding，也不在 Harness Loop 中调用检索器。

## 融合规则

- 每个输入使用唯一的检索器名称标识。
- 同一检索器重复返回同一个 `chunk_id` 时，仅保留排名最高的一次。
- 每个分块的总分为所有检索器贡献之和：`1 / (rank_constant + rank)`。
- 结果按总分降序排序；同分时按 `chunk_id` 升序排序。
- 返回结果重新编号为从 1 开始的 `rank`，并保留贡献该结果的检索器名称。

## 新增模块

| 路径 | 用途 |
| --- | --- |
| `app/rag/fusion.py` | RRF 融合输入、去重与稳定排序。 |
| `tests/test_reciprocal_rank_fusion.py` | 多检索器命中、去重、稳定排序与参数边界测试。 |

## 验收命令

```bash
uv lock --check && uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```
