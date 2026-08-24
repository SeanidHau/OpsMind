# 第 34 阶段：模型失败分类

## 目标

为模型调用失败提供可替换的分类策略。Harness 仅重试明确可恢复的传输类故障；结构化输出、权限和未知错误直接结束运行，避免无意义的外部请求。

## 默认分类规则

| 异常类型 | 分类 | 是否重试 |
| --- | --- | --- |
| `TimeoutError`、`ConnectionError`、`OSError` | `transient_transport_error` | 是 |
| `PermissionError` | `authorization_error` | 否 |
| `ValueError`、`TypeError`、`AssertionError` | `invalid_model_response` | 否 |
| 其他 `Exception` | `unclassified_model_error` | 否 |

`PermissionError` 是 `OSError` 的子类，因此必须在通用 I/O 错误之前判断。

## 执行规则

- `ModelFailureClassifier` 是可注入协议。默认使用 `DefaultModelFailureClassifier`。
- 分类结果包含 `category`、`retryable` 和原始错误文本。
- `MODEL_RETRY` 和 `RUN_FAILED` 事件保存分类结果，供快照回放和离线评测使用。
- 不可重试错误不会进入退避等待，也不会消耗额外的模型调用预算。
- 可重试错误沿用第 33 阶段的次数、预算和总运行时限边界。

## 边界

- 默认策略不识别具体模型供应商的异常类型或 HTTP 状态码。
- 接入供应商 SDK 后，可通过 `model_failure_classifier` 注入项目专用分类策略。
- `asyncio` 取消信号不属于此策略的处理范围，仍由调用方和运行时限控制。

## 验收

`tests/test_harness_model_failure.py` 覆盖默认异常分类、不可恢复的结构化输出错误，以及自定义分类策略注入。既有模型重试测试改用 `ConnectionError` 表示可恢复传输故障。
