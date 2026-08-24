# 第 35 阶段：工具失败分类

## 目标

为工具执行错误提供可替换的分类策略。Harness 仅对明确可恢复的传输故障重试；参数、注册和权限错误直接结束运行，避免消耗额外工具预算。

## 默认分类规则

| 异常类型 | 分类 | 是否重试 |
| --- | --- | --- |
| `TimeoutError`、`ConnectionError`、`OSError` | `transient_transport_error` | 是 |
| `PermissionError` | `authorization_error` | 否 |
| `ToolExecutionError`、`ValueError`、`TypeError`、`AssertionError` | `invalid_tool_request` | 否 |
| 其他 `Exception` | `unclassified_tool_error` | 否 |

`PermissionError` 是 `OSError` 的子类，因此在通用 I/O 错误之前判断。`ToolExecutionError` 覆盖未注册工具、缺失参数和未声明参数等可由 Harness 直接识别的问题。

## 执行规则

- `ToolFailureClassifier` 可由调用方注入；默认使用 `DefaultToolFailureClassifier`。
- `TOOL_RETRY`、`RUN_FAILED` 和预算阻断事件保存 `category` 与 `retryable`，便于回放和离线评测。
- 不可恢复错误只执行一次工具调用，不进入退避或重试路径。
- 可恢复错误沿用既有工具重试次数和工具调用预算控制。
- 工具成功路径继续收集证据、记录观察，并将结果交给 Progress Verifier。

## 边界

- 默认策略不识别具体工具供应商的错误码。
- 接入外部工具 SDK 后，可通过 `tool_failure_classifier` 注入专用分类策略。
- 取消信号不由该分类策略处理，仍由调用方和运行时限控制。

## 验收

`tests/test_harness_tool_failure.py` 覆盖默认分类、不可恢复参数错误与自定义分类策略。既有工具重试测试改用 `ConnectionError` 表示可恢复传输故障。
