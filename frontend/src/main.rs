//! OpsMind 的 GPUI 桌面控制台入口。

use std::{
    env, fs,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

mod sse;

mod api_client;

use gpui::{
    App, Application, Bounds, ClickEvent, Context, Entity, IntoElement, Render, Subscription,
    Window, WindowBounds, WindowOptions, div, img, prelude::*, px, rgb, rgba, size,
};
use gpui_component::{
    Disableable as _, Root,
    button::{Button, ButtonVariants as _},
    input::{Input, InputEvent, InputState},
};

use crate::{
    api_client::{
        ApiClientError, ApprovalRequest, CreateKnowledgeDocumentRequest, DiagnosisRunHistory,
        DiagnosisRunHistoryItem, DiagnosisRunSummary, HealthStatus, KnowledgeCatalog,
        KnowledgeDocument, McpConfiguration, McpConfigurationUpdate, McpServiceConfiguration,
        ModelConfiguration, OpsMindApiClient, ResumeDiagnosisRequest, StreamDiagnosisRequest,
    },
    sse::ServerSentEvent,
};

const DEFAULT_API_BASE_URL: &str = "http://127.0.0.1:8000";
const MAX_TRAJECTORY_ENTRIES: usize = 64;
const MAX_FINAL_ANSWER_CHARS: usize = 12_000;

struct OpsMindConsole {
    api_base_url: String,
    session_id: String,
    thread_id: String,
    connection: ConnectionState,
    knowledge_catalog: KnowledgeCatalogState,
    selected_knowledge_document: SelectedKnowledgeDocumentState,
    history: HistoryState,
    mcp_configuration: McpConfigurationState,
    diagnosis_input: Entity<InputState>,
    operator_input: Entity<InputState>,
    approval_input: Entity<InputState>,
    knowledge_title_input: Entity<InputState>,
    knowledge_content_input: Entity<InputState>,
    mcp_prometheus_url_input: Entity<InputState>,
    mcp_prometheus_token_input: Entity<InputState>,
    mcp_loki_url_input: Entity<InputState>,
    mcp_loki_token_input: Entity<InputState>,
    mcp_jaeger_url_input: Entity<InputState>,
    mcp_jaeger_token_input: Entity<InputState>,
    mcp_kubernetes_url_input: Entity<InputState>,
    mcp_kubernetes_token_input: Entity<InputState>,
    mcp_cmdb_url_input: Entity<InputState>,
    mcp_cmdb_token_input: Entity<InputState>,
    llm_provider_input: Entity<InputState>,
    llm_model_input: Entity<InputState>,
    llm_api_key_input: Entity<InputState>,
    llm_base_url_input: Entity<InputState>,
    embedding_model_input: Entity<InputState>,
    embedding_api_key_input: Entity<InputState>,
    embedding_base_url_input: Entity<InputState>,
    embedding_vector_size_input: Entity<InputState>,
    submission: SubmissionState,
    operator_submission: SubmissionState,
    approval_submission: SubmissionState,
    knowledge_submission: KnowledgeSubmissionState,
    report_download: ReportDownloadState,
    page: WorkspacePage,
    run: RunState,
    trajectory: Vec<TrajectoryEntry>,
    expanded_trajectory_id: Option<String>,
    _input_subscription: Subscription,
    _operator_input_subscription: Subscription,
    _approval_input_subscription: Subscription,
    _knowledge_title_input_subscription: Subscription,
    _knowledge_content_input_subscription: Subscription,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum WorkspacePage {
    Investigation,
    Knowledge,
    History,
    Mcp,
}

enum ConnectionState {
    Checking,
    Ready,
    Unavailable,
}

enum KnowledgeCatalogState {
    Loading,
    Ready(KnowledgeCatalog),
    Unavailable,
}

enum SelectedKnowledgeDocumentState {
    None,
    Loading { title: String },
    Ready(KnowledgeDocument),
    Unavailable,
}

enum HistoryState {
    Loading,
    Ready(DiagnosisRunHistory),
    Unavailable,
}

enum McpConfigurationState {
    Loading,
    Ready(McpConfiguration),
    Saving(McpConfiguration),
    Unavailable,
}

enum KnowledgeSubmissionState {
    Draft,
    Invalid,
    Saving,
    Saved,
    Failed,
}

enum ReportDownloadState {
    Idle,
    Saved,
    Failed,
}

enum SubmissionState {
    Draft,
    Invalid,
    Prepared { query: String },
}

enum RunState {
    Idle,
    Running {
        event_count: usize,
    },
    WaitingForInput {
        run_id: String,
        question: String,
    },
    ResumingUserInput,
    WaitingForApproval {
        run_id: String,
        tool_name: String,
        reason: String,
    },
    RecordingApproval,
    ApprovalRecorded {
        run_id: String,
        tool_name: String,
    },
    ResumingApproval,
    Finished {
        status: String,
        step_count: usize,
        final_answer: Option<String>,
    },
    Failed,
}

/// 仅保存 API 已投影的安全运行摘要，供工作流面板展示。
#[derive(Clone)]
struct TrajectoryEntry {
    id: String,
    summary: String,
    detail: String,
}

enum StreamUpdate {
    Event(ServerSentEvent),
    Closed { succeeded: bool },
    Resumed(DiagnosisRunSummary),
    ResumeFailed,
    ApprovalRecorded { run_id: String, tool_name: String },
}

impl OpsMindConsole {
    fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let diagnosis_input = cx.new(|cx| {
            InputState::new(window, cx)
                .multi_line(true)
                .rows(4)
                .placeholder("描述告警现象、受影响服务和已知时间范围")
        });
        let input_subscription =
            cx.subscribe(&diagnosis_input, |console, input, event, cx| match event {
                InputEvent::Change => {
                    console.submission = SubmissionState::Draft;
                    cx.notify();
                }
                InputEvent::PressEnter { secondary: true } => {
                    console.start_diagnosis(input.read(cx).value().to_string(), cx);
                }
                InputEvent::PressEnter { secondary: false }
                | InputEvent::Focus
                | InputEvent::Blur => {}
            });
        let operator_input = cx.new(|cx| {
            InputState::new(window, cx)
                .multi_line(true)
                .rows(3)
                .placeholder("填写补充信息，不要粘贴凭据或原始日志")
        });
        let operator_input_subscription =
            cx.subscribe(&operator_input, |console, _, event, cx| match event {
                InputEvent::Change => {
                    console.operator_submission = SubmissionState::Draft;
                    cx.notify();
                }
                InputEvent::PressEnter { .. } | InputEvent::Focus | InputEvent::Blur => {}
            });
        let approval_input = cx.new(|cx| {
            InputState::new(window, cx)
                .multi_line(true)
                .rows(2)
                .placeholder("填写审批理由，例如已确认维护窗口")
        });
        let approval_input_subscription =
            cx.subscribe(&approval_input, |console, _, event, cx| match event {
                InputEvent::Change => {
                    console.approval_submission = SubmissionState::Draft;
                    cx.notify();
                }
                InputEvent::PressEnter { .. } | InputEvent::Focus | InputEvent::Blur => {}
            });
        let knowledge_title_input =
            cx.new(|cx| InputState::new(window, cx).rows(1).placeholder("知识标题"));
        let knowledge_content_input = cx.new(|cx| {
            InputState::new(window, cx)
                .multi_line(true)
                .rows(5)
                .placeholder("输入 Markdown 内容，例如处理步骤、注意事项和适用范围")
        });
        let mcp_prometheus_url_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("http://127.0.0.1:9090")
        });
        let mcp_prometheus_token_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("只读访问令牌（可选）")
        });
        let mcp_loki_url_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("http://127.0.0.1:3100")
        });
        let mcp_loki_token_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("只读访问令牌（可选）")
        });
        let mcp_jaeger_url_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("http://127.0.0.1:16686")
        });
        let mcp_jaeger_token_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("只读访问令牌（可选）")
        });
        let mcp_kubernetes_url_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("https://kubernetes.example.com")
        });
        let mcp_kubernetes_token_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("只读访问令牌")
        });
        let mcp_cmdb_url_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("https://cmdb.example.com")
        });
        let mcp_cmdb_token_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("只读访问令牌（可选）")
        });
        let llm_provider_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("openai 或 anthropic")
        });
        let llm_model_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("例如 gpt-4.1-mini")
        });
        let llm_api_key_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("模型 API 密钥")
        });
        let llm_base_url_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("自定义地址（可选）")
        });
        let embedding_model_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("例如 text-embedding-3-small")
        });
        let embedding_api_key_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("Embedding API 密钥（可选，默认复用模型密钥）")
        });
        let embedding_base_url_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("Embedding 自定义地址（可选）")
        });
        let embedding_vector_size_input = cx.new(|cx| {
            InputState::new(window, cx)
                .rows(1)
                .placeholder("向量维度，例如 1536")
        });
        let knowledge_title_input_subscription =
            cx.subscribe(&knowledge_title_input, |console, _, event, cx| {
                if matches!(event, InputEvent::Change) {
                    console.knowledge_submission = KnowledgeSubmissionState::Draft;
                    cx.notify();
                }
            });
        let knowledge_content_input_subscription =
            cx.subscribe(&knowledge_content_input, |console, _, event, cx| {
                if matches!(event, InputEvent::Change) {
                    console.knowledge_submission = KnowledgeSubmissionState::Draft;
                    cx.notify();
                }
            });
        let mut console = Self {
            api_base_url: env::var("OPSMIND_API_BASE_URL")
                .unwrap_or_else(|_| String::from(DEFAULT_API_BASE_URL)),
            session_id: desktop_identifier("session"),
            thread_id: desktop_identifier("thread"),
            connection: ConnectionState::Checking,
            knowledge_catalog: KnowledgeCatalogState::Loading,
            selected_knowledge_document: SelectedKnowledgeDocumentState::None,
            history: HistoryState::Loading,
            mcp_configuration: McpConfigurationState::Loading,
            diagnosis_input,
            operator_input,
            approval_input,
            knowledge_title_input,
            knowledge_content_input,
            mcp_prometheus_url_input,
            mcp_prometheus_token_input,
            mcp_loki_url_input,
            mcp_loki_token_input,
            mcp_jaeger_url_input,
            mcp_jaeger_token_input,
            mcp_kubernetes_url_input,
            mcp_kubernetes_token_input,
            mcp_cmdb_url_input,
            mcp_cmdb_token_input,
            llm_provider_input,
            llm_model_input,
            llm_api_key_input,
            llm_base_url_input,
            embedding_model_input,
            embedding_api_key_input,
            embedding_base_url_input,
            embedding_vector_size_input,
            submission: SubmissionState::Draft,
            operator_submission: SubmissionState::Draft,
            approval_submission: SubmissionState::Draft,
            knowledge_submission: KnowledgeSubmissionState::Draft,
            report_download: ReportDownloadState::Idle,
            page: WorkspacePage::Investigation,
            run: RunState::Idle,
            trajectory: Vec::new(),
            expanded_trajectory_id: None,
            _input_subscription: input_subscription,
            _operator_input_subscription: operator_input_subscription,
            _approval_input_subscription: approval_input_subscription,
            _knowledge_title_input_subscription: knowledge_title_input_subscription,
            _knowledge_content_input_subscription: knowledge_content_input_subscription,
        };
        console.refresh_backend_status(cx);
        console.refresh_knowledge_catalog(cx);
        console
    }

    /// 在后台请求 FastAPI，避免启动检查阻塞窗口渲染。
    fn refresh_backend_status(&mut self, cx: &mut Context<Self>) {
        let api_base_url = self.api_base_url.clone();
        let snapshot_task = cx
            .background_executor()
            .spawn(async move { OpsMindApiClient::new(api_base_url).health() });
        let this = cx.weak_entity();
        let mut async_cx = cx.to_async();

        cx.foreground_executor()
            .spawn(async move {
                let snapshot = snapshot_task.await;
                let _ = this.update(&mut async_cx, |console, cx| {
                    console.apply_bootstrap_snapshot(snapshot);
                    cx.notify();
                });
            })
            .detach();
    }

    fn apply_bootstrap_snapshot(&mut self, snapshot: Result<HealthStatus, ApiClientError>) {
        match snapshot {
            Ok(_) => {
                self.connection = ConnectionState::Ready;
            }
            Err(_) => {
                self.connection = ConnectionState::Unavailable;
            }
        }
    }

    /// 在后台读取知识目录，避免切换工作区时阻塞窗口。
    fn refresh_knowledge_catalog(&mut self, cx: &mut Context<Self>) {
        self.knowledge_catalog = KnowledgeCatalogState::Loading;
        let api_base_url = self.api_base_url.clone();
        let catalog_task = cx
            .background_executor()
            .spawn(async move { OpsMindApiClient::new(api_base_url).knowledge_catalog() });
        let this = cx.weak_entity();
        let mut async_cx = cx.to_async();

        cx.foreground_executor()
            .spawn(async move {
                let catalog = catalog_task.await;
                let _ = this.update(&mut async_cx, |console, cx| {
                    console.knowledge_catalog = match catalog {
                        Ok(catalog) => KnowledgeCatalogState::Ready(catalog),
                        Err(_) => KnowledgeCatalogState::Unavailable,
                    };
                    cx.notify();
                });
            })
            .detach();
    }

    fn connection_detail(&self) -> String {
        match &self.connection {
            ConnectionState::Checking => String::from("正在连接服务…"),
            ConnectionState::Ready => String::from("服务已连接"),
            ConnectionState::Unavailable => String::from("暂时无法连接服务"),
        }
    }

    fn show_investigation(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        self.page = WorkspacePage::Investigation;
        cx.notify();
    }

    fn show_knowledge(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        self.page = WorkspacePage::Knowledge;
        self.refresh_knowledge_catalog(cx);
        cx.notify();
    }

    fn show_knowledge_document(
        &mut self,
        document_id: String,
        title: String,
        cx: &mut Context<Self>,
    ) {
        self.selected_knowledge_document = SelectedKnowledgeDocumentState::Loading { title };
        let api_base_url = self.api_base_url.clone();
        let task = cx.background_executor().spawn(async move {
            OpsMindApiClient::new(api_base_url).knowledge_document(&document_id)
        });
        let this = cx.weak_entity();
        let mut async_cx = cx.to_async();
        cx.foreground_executor()
            .spawn(async move {
                let document = task.await;
                let _ = this.update(&mut async_cx, |console, cx| {
                    console.selected_knowledge_document = match document {
                        Ok(document) => SelectedKnowledgeDocumentState::Ready(document),
                        Err(_) => SelectedKnowledgeDocumentState::Unavailable,
                    };
                    cx.notify();
                });
            })
            .detach();
    }

    fn show_history(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        self.page = WorkspacePage::History;
        self.refresh_history(cx);
        cx.notify();
    }

    fn show_mcp(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        self.page = WorkspacePage::Mcp;
        self.refresh_mcp_configuration(cx);
        cx.notify();
    }

    fn refresh_history(&mut self, cx: &mut Context<Self>) {
        self.history = HistoryState::Loading;
        let api_base_url = self.api_base_url.clone();
        let task = cx
            .background_executor()
            .spawn(async move { OpsMindApiClient::new(api_base_url).run_history() });
        let this = cx.weak_entity();
        let mut async_cx = cx.to_async();
        cx.foreground_executor()
            .spawn(async move {
                let history = task.await;
                let _ = this.update(&mut async_cx, |console, cx| {
                    console.history = match history {
                        Ok(history) => HistoryState::Ready(history),
                        Err(_) => HistoryState::Unavailable,
                    };
                    cx.notify();
                });
            })
            .detach();
    }

    fn refresh_mcp_configuration(&mut self, cx: &mut Context<Self>) {
        self.mcp_configuration = McpConfigurationState::Loading;
        let api_base_url = self.api_base_url.clone();
        let task = cx
            .background_executor()
            .spawn(async move { OpsMindApiClient::new(api_base_url).mcp_configuration() });
        let this = cx.weak_entity();
        let mut async_cx = cx.to_async();
        cx.foreground_executor()
            .spawn(async move {
                let configuration = task.await;
                let _ = this.update(&mut async_cx, |console, cx| {
                    console.mcp_configuration = match configuration {
                        Ok(configuration) => McpConfigurationState::Ready(configuration),
                        Err(_) => McpConfigurationState::Unavailable,
                    };
                    cx.notify();
                });
            })
            .detach();
    }

    fn save_mcp_configuration(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        self.persist_mcp_configuration(None, cx);
    }

    fn toggle_mcp_configuration(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        let enabled = !self.mcp_is_enabled();
        self.persist_mcp_configuration(Some(enabled), cx);
    }

    fn persist_mcp_configuration(&mut self, enabled: Option<bool>, cx: &mut Context<Self>) {
        let configuration = match &self.mcp_configuration {
            McpConfigurationState::Ready(configuration) => configuration.clone(),
            _ => return,
        };
        let request = McpConfigurationUpdate {
            enabled: enabled.unwrap_or(configuration.enabled),
            command: configuration.command.clone(),
            arguments: configuration.arguments.clone(),
            prometheus_url: self
                .mcp_prometheus_url_input
                .read(cx)
                .value()
                .trim()
                .to_owned(),
            prometheus_bearer_token: self
                .mcp_prometheus_token_input
                .read(cx)
                .value()
                .trim()
                .to_owned(),
            loki_url: self.mcp_loki_url_input.read(cx).value().trim().to_owned(),
            loki_bearer_token: self.mcp_loki_token_input.read(cx).value().trim().to_owned(),
            jaeger_url: self.mcp_jaeger_url_input.read(cx).value().trim().to_owned(),
            jaeger_bearer_token: self
                .mcp_jaeger_token_input
                .read(cx)
                .value()
                .trim()
                .to_owned(),
            kubernetes_url: self
                .mcp_kubernetes_url_input
                .read(cx)
                .value()
                .trim()
                .to_owned(),
            kubernetes_bearer_token: self
                .mcp_kubernetes_token_input
                .read(cx)
                .value()
                .trim()
                .to_owned(),
            cmdb_url: self.mcp_cmdb_url_input.read(cx).value().trim().to_owned(),
            cmdb_bearer_token: self.mcp_cmdb_token_input.read(cx).value().trim().to_owned(),
            llm_provider: self.llm_provider_input.read(cx).value().trim().to_owned(),
            llm_model: self.llm_model_input.read(cx).value().trim().to_owned(),
            llm_api_key: self.llm_api_key_input.read(cx).value().trim().to_owned(),
            llm_base_url: self.llm_base_url_input.read(cx).value().trim().to_owned(),
            embedding_model: self
                .embedding_model_input
                .read(cx)
                .value()
                .trim()
                .to_owned(),
            embedding_api_key: self
                .embedding_api_key_input
                .read(cx)
                .value()
                .trim()
                .to_owned(),
            embedding_base_url: self
                .embedding_base_url_input
                .read(cx)
                .value()
                .trim()
                .to_owned(),
            embedding_vector_size: self
                .embedding_vector_size_input
                .read(cx)
                .value()
                .trim()
                .parse()
                .ok(),
        };
        self.mcp_configuration = McpConfigurationState::Saving(configuration);
        let api_base_url = self.api_base_url.clone();
        let task = cx.background_executor().spawn(async move {
            OpsMindApiClient::new(api_base_url).update_mcp_configuration(&request)
        });
        let this = cx.weak_entity();
        let mut async_cx = cx.to_async();
        cx.foreground_executor()
            .spawn(async move {
                let result = task.await;
                let _ = this.update(&mut async_cx, |console, cx| {
                    console.mcp_configuration = match result {
                        Ok(configuration) => McpConfigurationState::Ready(configuration),
                        Err(_) => McpConfigurationState::Unavailable,
                    };
                    cx.notify();
                });
            })
            .detach();
    }

    fn mcp_is_enabled(&self) -> bool {
        matches!(
            &self.mcp_configuration,
            McpConfigurationState::Ready(configuration) | McpConfigurationState::Saving(configuration)
                if configuration.enabled
        )
    }

    fn mcp_is_saving(&self) -> bool {
        matches!(self.mcp_configuration, McpConfigurationState::Saving(_))
    }

    fn submit_knowledge_document(
        &mut self,
        _: &ClickEvent,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let title = self
            .knowledge_title_input
            .read(cx)
            .value()
            .trim()
            .to_owned();
        let content = self
            .knowledge_content_input
            .read(cx)
            .value()
            .trim()
            .to_owned();
        if title.is_empty()
            || content.is_empty()
            || title.chars().count() > 200
            || content.chars().count() > 20_000
        {
            self.knowledge_submission = KnowledgeSubmissionState::Invalid;
            cx.notify();
            return;
        }
        self.knowledge_submission = KnowledgeSubmissionState::Saving;
        let api_base_url = self.api_base_url.clone();
        let (sender, receiver) = async_channel::bounded(1);
        cx.background_executor()
            .spawn(async move {
                let result = OpsMindApiClient::new(api_base_url)
                    .create_knowledge_document(&CreateKnowledgeDocumentRequest { title, content });
                let _ = sender.send_blocking(result);
            })
            .detach();
        let this = cx.weak_entity();
        let mut async_cx = cx.to_async();
        cx.foreground_executor()
            .spawn(async move {
                if let Ok(result) = receiver.recv().await {
                    let _ = this.update(&mut async_cx, |console, cx| match result {
                        Ok(catalog) => {
                            console.knowledge_catalog = KnowledgeCatalogState::Ready(catalog);
                            console.knowledge_submission = KnowledgeSubmissionState::Saved;
                            cx.notify();
                        }
                        Err(_) => {
                            console.knowledge_submission = KnowledgeSubmissionState::Failed;
                            cx.notify();
                        }
                    });
                }
            })
            .detach();
    }

    fn download_report(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        self.report_download = match self.final_answer().map(save_report_to_downloads) {
            Some(Ok(())) => ReportDownloadState::Saved,
            _ => ReportDownloadState::Failed,
        };
        cx.notify();
    }

    fn submit_diagnosis(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        self.start_diagnosis(self.diagnosis_input.read(cx).value().to_string(), cx);
    }

    fn submit_user_input(&mut self, _: &ClickEvent, window: &mut Window, cx: &mut Context<Self>) {
        let RunState::WaitingForInput { run_id, .. } = &self.run else {
            return;
        };
        let answer = match normalize_diagnosis_query(&self.operator_input.read(cx).value()) {
            Ok(answer) => answer,
            Err(()) => {
                self.operator_submission = SubmissionState::Invalid;
                cx.notify();
                return;
            }
        };
        let run_id = run_id.clone();
        self.operator_submission = SubmissionState::Prepared {
            query: answer.clone(),
        };
        self.run = RunState::ResumingUserInput;
        self.operator_input
            .update(cx, |input, cx| input.set_value("", window, cx));
        cx.notify();

        let api_base_url = self.api_base_url.clone();
        let (sender, receiver) = async_channel::bounded(1);
        cx.background_executor()
            .spawn(async move {
                let update = OpsMindApiClient::new(api_base_url)
                    .resume_with_user_input(&run_id, &ResumeDiagnosisRequest { answer })
                    .map(StreamUpdate::Resumed)
                    .unwrap_or(StreamUpdate::ResumeFailed);
                let _ = sender.send_blocking(update);
            })
            .detach();

        self.receive_single_update(receiver, cx);
    }

    fn approve_run(&mut self, _: &ClickEvent, window: &mut Window, cx: &mut Context<Self>) {
        self.record_approval("approve", window, cx);
    }

    fn reject_run(&mut self, _: &ClickEvent, window: &mut Window, cx: &mut Context<Self>) {
        self.record_approval("reject", window, cx);
    }

    fn record_approval(&mut self, decision: &str, window: &mut Window, cx: &mut Context<Self>) {
        let RunState::WaitingForApproval {
            run_id, tool_name, ..
        } = &self.run
        else {
            return;
        };
        let reason = match normalize_approval_reason(&self.approval_input.read(cx).value()) {
            Ok(reason) => reason,
            Err(()) => {
                self.approval_submission = SubmissionState::Invalid;
                cx.notify();
                return;
            }
        };
        let run_id = run_id.clone();
        let tool_name = tool_name.clone();
        self.approval_submission = SubmissionState::Prepared {
            query: reason.clone(),
        };
        self.run = RunState::RecordingApproval;
        self.approval_input
            .update(cx, |input, cx| input.set_value("", window, cx));
        cx.notify();

        let api_base_url = self.api_base_url.clone();
        let decision = decision.to_owned();
        let (sender, receiver) = async_channel::bounded(1);
        cx.background_executor()
            .spawn(async move {
                let update = OpsMindApiClient::new(api_base_url)
                    .resolve_approval(
                        &run_id,
                        &ApprovalRequest {
                            decision: decision.clone(),
                            reason,
                        },
                    )
                    .map(|summary| {
                        if decision == "approve" {
                            StreamUpdate::ApprovalRecorded { run_id, tool_name }
                        } else {
                            StreamUpdate::Resumed(summary)
                        }
                    })
                    .unwrap_or(StreamUpdate::ResumeFailed);
                let _ = sender.send_blocking(update);
            })
            .detach();

        self.receive_single_update(receiver, cx);
    }

    fn resume_approved_run(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        let RunState::ApprovalRecorded { run_id, .. } = &self.run else {
            return;
        };
        let run_id = run_id.clone();
        self.run = RunState::ResumingApproval;
        cx.notify();

        let api_base_url = self.api_base_url.clone();
        let (sender, receiver) = async_channel::bounded(1);
        cx.background_executor()
            .spawn(async move {
                let update = OpsMindApiClient::new(api_base_url)
                    .resume_approved(&run_id)
                    .map(StreamUpdate::Resumed)
                    .unwrap_or(StreamUpdate::ResumeFailed);
                let _ = sender.send_blocking(update);
            })
            .detach();

        self.receive_single_update(receiver, cx);
    }

    fn receive_single_update(
        &mut self,
        receiver: async_channel::Receiver<StreamUpdate>,
        cx: &mut Context<Self>,
    ) {
        let this = cx.weak_entity();
        let mut async_cx = cx.to_async();
        cx.foreground_executor()
            .spawn(async move {
                if let Ok(update) = receiver.recv().await {
                    let _ = this.update(&mut async_cx, |console, cx| {
                        console.apply_stream_update(update);
                        cx.notify();
                    });
                }
            })
            .detach();
    }

    /// 启动后台 SSE 请求，并将安全事件转交给 GPUI 主线程。
    fn start_diagnosis(&mut self, raw_query: String, cx: &mut Context<Self>) {
        if !self.backend_is_ready()
            || self.is_busy()
            || self.waiting_for_user_input().is_some()
            || self.waiting_for_approval().is_some()
            || self.approval_is_recorded().is_some()
        {
            return;
        }
        let query = match normalize_diagnosis_query(&raw_query) {
            Ok(query) => query,
            Err(()) => {
                self.submission = SubmissionState::Invalid;
                cx.notify();
                return;
            }
        };

        self.submission = SubmissionState::Prepared {
            query: query.clone(),
        };
        self.run = RunState::Running { event_count: 0 };
        self.trajectory.clear();
        self.expanded_trajectory_id = None;
        cx.notify();

        let request = StreamDiagnosisRequest {
            session_id: self.session_id.clone(),
            thread_id: self.thread_id.clone(),
            user_query: query,
        };
        let api_base_url = self.api_base_url.clone();
        let (sender, receiver) = async_channel::unbounded();
        cx.background_executor()
            .spawn(async move {
                let event_sender = sender.clone();
                let result =
                    OpsMindApiClient::new(api_base_url).stream_diagnosis(&request, |event| {
                        let _ = event_sender.send_blocking(StreamUpdate::Event(event));
                    });
                let _ = sender.send_blocking(StreamUpdate::Closed {
                    succeeded: result.is_ok(),
                });
            })
            .detach();

        let this = cx.weak_entity();
        let mut async_cx = cx.to_async();
        cx.foreground_executor()
            .spawn(async move {
                while let Ok(update) = receiver.recv().await {
                    let _ = this.update(&mut async_cx, |console, cx| {
                        console.apply_stream_update(update);
                        cx.notify();
                    });
                }
            })
            .detach();
    }

    fn apply_stream_update(&mut self, update: StreamUpdate) {
        match update {
            StreamUpdate::Event(event) => self.apply_stream_event(event),
            StreamUpdate::Closed { succeeded } if !succeeded && self.is_running() => {
                self.run = RunState::Failed;
            }
            StreamUpdate::Closed { .. } => {}
            StreamUpdate::Resumed(summary) => self.apply_run_summary(summary),
            StreamUpdate::ResumeFailed => self.run = RunState::Failed,
            StreamUpdate::ApprovalRecorded { run_id, tool_name } => {
                self.run = RunState::ApprovalRecorded { run_id, tool_name };
            }
        }
    }

    fn apply_stream_event(&mut self, event: ServerSentEvent) {
        match event.name.as_str() {
            "run_started" => self.run = RunState::Running { event_count: 0 },
            "run_finished" => self.apply_finished_stream(&event.data),
            "run_failed" => self.run = RunState::Failed,
            event_name => {
                if let Some(entry) = trajectory_entry(event_name, &event.data) {
                    if let RunState::Running { event_count } = &mut self.run {
                        *event_count += 1;
                    }
                    self.trajectory.push(entry);
                    if self.trajectory.len() > MAX_TRAJECTORY_ENTRIES {
                        self.trajectory.remove(0);
                    }
                }
            }
        }
    }

    fn toggle_trajectory_entry(&mut self, entry_id: String, cx: &mut Context<Self>) {
        self.expanded_trajectory_id =
            (self.expanded_trajectory_id.as_deref() != Some(entry_id.as_str())).then_some(entry_id);
        cx.notify();
    }

    fn operator_submission_detail(&self) -> String {
        match &self.operator_submission {
            SubmissionState::Draft => String::from("补充信息只会用于恢复当前运行。"),
            SubmissionState::Invalid => String::from("补充信息不能为空，且不得超过 4000 个字符。"),
            SubmissionState::Prepared { query } => {
                format!("正在提交 {} 个字符的补充信息。", query.chars().count())
            }
        }
    }

    fn approval_submission_detail(&self) -> String {
        match &self.approval_submission {
            SubmissionState::Draft => String::from("审批理由只记录为审计决议。"),
            SubmissionState::Invalid => String::from("审批理由不能为空，且不得超过 2000 个字符。"),
            SubmissionState::Prepared { query } => {
                format!("正在记录 {} 个字符的审批理由。", query.chars().count())
            }
        }
    }

    fn backend_is_ready(&self) -> bool {
        matches!(&self.connection, ConnectionState::Ready)
    }

    fn is_running(&self) -> bool {
        matches!(&self.run, RunState::Running { .. })
    }

    fn is_busy(&self) -> bool {
        matches!(
            &self.run,
            RunState::Running { .. }
                | RunState::ResumingUserInput
                | RunState::RecordingApproval
                | RunState::ResumingApproval
        )
    }

    fn waiting_for_user_input(&self) -> Option<(&str, &str)> {
        match &self.run {
            RunState::WaitingForInput { run_id, question } => Some((run_id, question)),
            _ => None,
        }
    }

    fn waiting_for_approval(&self) -> Option<(&str, &str, &str)> {
        match &self.run {
            RunState::WaitingForApproval {
                run_id,
                tool_name,
                reason,
            } => Some((run_id, tool_name, reason)),
            _ => None,
        }
    }

    fn approval_is_recorded(&self) -> Option<(&str, &str)> {
        match &self.run {
            RunState::ApprovalRecorded { run_id, tool_name } => Some((run_id, tool_name)),
            _ => None,
        }
    }

    fn apply_finished_stream(&mut self, data: &serde_json::Value) {
        let status = safe_status(data).unwrap_or_else(|| String::from("unknown"));
        let step_count = data["step_count"].as_u64().unwrap_or_default() as usize;
        if status == "waiting_user_input" {
            let Some(run_id) = data["run_id"]
                .as_str()
                .filter(|value| is_safe_run_id(value))
            else {
                self.run = RunState::Failed;
                return;
            };
            let Some(question) = data["pending_question"]
                .as_str()
                .and_then(safe_pending_question)
            else {
                self.run = RunState::Failed;
                return;
            };
            self.run = RunState::WaitingForInput {
                run_id: run_id.to_owned(),
                question,
            };
            self.operator_submission = SubmissionState::Draft;
            return;
        }
        if status == "waiting_approval" {
            let Some((tool_name, reason)) =
                data["pending_approval"].as_object().and_then(|approval| {
                    safe_pending_approval(
                        approval.get("tool_name")?.as_str()?,
                        approval.get("reason")?.as_str()?,
                    )
                })
            else {
                self.run = RunState::Failed;
                return;
            };
            let Some(run_id) = data["run_id"]
                .as_str()
                .filter(|value| is_safe_run_id(value))
            else {
                self.run = RunState::Failed;
                return;
            };
            self.run = RunState::WaitingForApproval {
                run_id: run_id.to_owned(),
                tool_name,
                reason,
            };
            self.approval_submission = SubmissionState::Draft;
            return;
        }
        self.run = RunState::Finished {
            final_answer: data["final_answer"].as_str().and_then(safe_final_answer),
            status,
            step_count,
        };
    }

    fn apply_run_summary(&mut self, summary: DiagnosisRunSummary) {
        let status = summary.status.as_deref().and_then(safe_status_value);
        let Some(status) = status else {
            self.run = RunState::Failed;
            return;
        };
        if status == "waiting_user_input" {
            let Some(question) = summary
                .pending_question
                .and_then(|value| safe_pending_question(&value))
            else {
                self.run = RunState::Failed;
                return;
            };
            if !is_safe_run_id(&summary.run_id) {
                self.run = RunState::Failed;
                return;
            }
            self.run = RunState::WaitingForInput {
                run_id: summary.run_id,
                question,
            };
            self.operator_submission = SubmissionState::Draft;
            return;
        }
        if status == "waiting_approval" {
            let Some(approval) = summary.pending_approval else {
                self.run = RunState::Failed;
                return;
            };
            let Some((tool_name, reason)) =
                safe_pending_approval(&approval.tool_name, &approval.reason)
            else {
                self.run = RunState::Failed;
                return;
            };
            if !is_safe_run_id(&summary.run_id) {
                self.run = RunState::Failed;
                return;
            }
            self.run = RunState::WaitingForApproval {
                run_id: summary.run_id,
                tool_name,
                reason,
            };
            self.approval_submission = SubmissionState::Draft;
            return;
        }
        self.run = RunState::Finished {
            final_answer: summary.final_answer.as_deref().and_then(safe_final_answer),
            status,
            step_count: summary.step_count,
        };
    }

    fn final_answer(&self) -> Option<&str> {
        match &self.run {
            RunState::Finished {
                status,
                final_answer: Some(answer),
                ..
            } if status == "completed" => Some(answer),
            _ => None,
        }
    }

    fn run_detail(&self) -> String {
        match &self.run {
            RunState::Idle => String::from("等待开始分析。"),
            RunState::Running { event_count } => format!("正在分析，已完成 {event_count} 项。"),
            RunState::WaitingForInput { .. } => String::from("需要补充信息。"),
            RunState::ResumingUserInput => String::from("正在继续分析。"),
            RunState::WaitingForApproval { .. } => String::from("等待确认操作。"),
            RunState::RecordingApproval => String::from("正在记录确认结果。"),
            RunState::ApprovalRecorded { .. } => String::from("已确认，等待继续。"),
            RunState::ResumingApproval => String::from("正在继续分析。"),
            RunState::Finished {
                status, step_count, ..
            } => {
                format!("分析结束 · {} · {step_count} 个步骤", status_label(status))
            }
            RunState::Failed => String::from("本次分析未完成，请稍后重试。"),
        }
    }

    fn mcp_configuration_panel(&self, cx: &mut Context<Self>) -> gpui::Div {
        let mut panel = div()
            .flex()
            .flex_col()
            .gap(px(14.0))
            .p(px(20.0))
            .rounded(px(14.0))
            .bg(rgba(0xffffff8f))
            .border_1()
            .border_color(rgba(0xffffffb8))
            .child(
                div()
                    .flex()
                    .justify_between()
                    .items_center()
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .gap(px(4.0))
                            .child(div().text_color(rgb(0x4f829c)).child("运行设置"))
                            .child(
                                div()
                                    .text_size(px(11.0))
                                    .text_color(rgb(0x697783))
                                    .child("密钥仅保存于本机，不会在界面中回显。"),
                            ),
                    )
                    .child(
                        Button::new("toggle-mcp")
                            .label(if self.mcp_is_enabled() {
                                "已启用"
                            } else {
                                "未启用"
                            })
                            .disabled(self.mcp_is_saving() || !self.backend_is_ready())
                            .on_click(cx.listener(Self::toggle_mcp_configuration)),
                    ),
            );

        match &self.mcp_configuration {
            McpConfigurationState::Loading => {
                panel = panel.child(div().text_color(rgb(0x697783)).child("正在读取连接配置…"));
            }
            McpConfigurationState::Unavailable => {
                panel = panel.child(
                    div()
                        .text_color(rgb(0xc76a62))
                        .child("暂时无法读取或保存连接配置，请确认服务已启动。"),
                );
            }
            McpConfigurationState::Ready(configuration)
            | McpConfigurationState::Saving(configuration) => {
                panel = panel
                    .child(model_configuration_form(
                        &configuration.model,
                        &self.llm_provider_input,
                        &self.llm_model_input,
                        &self.llm_api_key_input,
                        &self.llm_base_url_input,
                        &self.embedding_model_input,
                        &self.embedding_api_key_input,
                        &self.embedding_base_url_input,
                        &self.embedding_vector_size_input,
                    ))
                    .child(
                        div()
                            .text_size(px(12.0))
                            .text_color(rgb(0x4f829c))
                            .child("MCP 数据源"),
                    )
                    .child(mcp_service_form(
                        "Prometheus",
                        "指标",
                        &configuration.prometheus,
                        &self.mcp_prometheus_url_input,
                        &self.mcp_prometheus_token_input,
                    ))
                    .child(mcp_service_form(
                        "Loki",
                        "日志",
                        &configuration.loki,
                        &self.mcp_loki_url_input,
                        &self.mcp_loki_token_input,
                    ))
                    .child(mcp_service_form(
                        "Jaeger",
                        "调用链",
                        &configuration.jaeger,
                        &self.mcp_jaeger_url_input,
                        &self.mcp_jaeger_token_input,
                    ))
                    .child(mcp_service_form(
                        "Kubernetes",
                        "集群资源",
                        &configuration.kubernetes,
                        &self.mcp_kubernetes_url_input,
                        &self.mcp_kubernetes_token_input,
                    ))
                    .child(mcp_service_form(
                        "CMDB",
                        "服务与依赖",
                        &configuration.cmdb,
                        &self.mcp_cmdb_url_input,
                        &self.mcp_cmdb_token_input,
                    ))
                    .child(
                        // 独立操作栏固定在面板内，避免操作按钮与最后一个数据源卡片脱节。
                        div()
                            .w_full()
                            .flex()
                            .justify_end()
                            .p(px(12.0))
                            .rounded(px(10.0))
                            .bg(rgba(0xffffff70))
                            .border_1()
                            .border_color(rgba(0xffffffb8))
                            .child(
                                Button::new("save-mcp")
                                    .label("保存连接")
                                    .info()
                                    .loading(self.mcp_is_saving())
                                    .disabled(self.mcp_is_saving() || !self.backend_is_ready())
                                    .on_click(cx.listener(Self::save_mcp_configuration)),
                            ),
                    );
            }
        }
        panel
    }
}

fn normalize_diagnosis_query(raw_query: &str) -> Result<String, ()> {
    let query = raw_query.trim();
    if query.is_empty() || query.chars().count() > 4_000 {
        return Err(());
    }
    Ok(query.to_owned())
}

fn normalize_approval_reason(raw_reason: &str) -> Result<String, ()> {
    let reason = raw_reason.trim();
    if reason.is_empty() || reason.chars().count() > 2_000 {
        return Err(());
    }
    Ok(reason.to_owned())
}

fn desktop_identifier(kind: &str) -> String {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default();
    format!("desktop-{kind}-{timestamp}")
}

fn safe_status(data: &serde_json::Value) -> Option<String> {
    safe_status_value(data["status"].as_str()?)
}

fn safe_status_value(value: &str) -> Option<String> {
    match value {
        "completed" | "blocked" | "waiting_approval" | "waiting_user_input" | "stalled"
        | "failed" => Some(value.to_owned()),
        _ => None,
    }
}

fn safe_pending_question(value: &str) -> Option<String> {
    let question = value.trim();
    if question.is_empty() || question.chars().count() > 2_000 {
        return None;
    }
    Some(question.to_owned())
}

fn safe_pending_approval(tool_name: &str, reason: &str) -> Option<(String, String)> {
    let tool_name = tool_name.trim();
    let reason = reason.trim();
    if tool_name.is_empty()
        || tool_name.chars().count() > 100
        || reason.is_empty()
        || reason.chars().count() > 2_000
    {
        return None;
    }
    Some((tool_name.to_owned(), reason.to_owned()))
}

fn safe_final_answer(value: &str) -> Option<String> {
    let answer = value.trim();
    if answer.is_empty() || answer.chars().count() > MAX_FINAL_ANSWER_CHARS {
        return None;
    }
    Some(answer.to_owned())
}

fn is_safe_run_id(value: &str) -> bool {
    value.len() == 36
        && value.chars().enumerate().all(|(index, character)| {
            matches!(index, 8 | 13 | 18 | 23)
                .then_some(character == '-')
                .unwrap_or_else(|| character.is_ascii_hexdigit())
        })
}

fn trajectory_entry(event_name: &str, data: &serde_json::Value) -> Option<TrajectoryEntry> {
    let event_type = data["event_type"].as_str()?;
    if event_type != event_name || !is_safe_trajectory_event(event_name) {
        return None;
    }

    let step_id = data["step_id"].as_u64()?;
    let latency_ms = data["latency_ms"].as_u64();
    let mut summary = format!("步骤 {step_id} · {}", event_label(event_name));
    if let Some(latency_ms) = latency_ms {
        summary.push_str(&format!(" · 耗时 {latency_ms} ms"));
    }
    let detail = trajectory_detail(event_name, data, latency_ms);
    Some(TrajectoryEntry {
        id: format!("{step_id}-{event_name}"),
        summary,
        detail,
    })
}

fn trajectory_detail(
    event_name: &str,
    data: &serde_json::Value,
    latency_ms: Option<u64>,
) -> String {
    let mut details = vec![format!("阶段：{}", event_label(event_name))];
    if let Some(tool_name) = data["tool_name"].as_str().and_then(safe_tool_label) {
        details.push(format!("涉及：{tool_name}"));
    }
    if let Some(decision) = data["decision"].as_str().and_then(safe_trajectory_detail) {
        details.push(format!("说明：{decision}"));
    }
    if let Some(latency_ms) = latency_ms {
        details.push(format!("耗时：{latency_ms} ms"));
    }
    if data["error"].is_string() {
        details.push(String::from("该步骤未完成，系统已按安全策略处理。"));
    }
    details.join("\n")
}

fn safe_trajectory_detail(value: &str) -> Option<String> {
    let detail = value.trim();
    if detail.is_empty() || detail.chars().count() > 500 {
        return None;
    }
    Some(detail.to_owned())
}

fn safe_tool_label(value: &str) -> Option<&'static str> {
    match value {
        "query_metrics" => Some("服务指标"),
        "query_logs" => Some("运行日志"),
        "query_topology" => Some("服务关系"),
        "query_knowledge" => Some("处理经验"),
        _ => None,
    }
}

fn event_label(event_name: &str) -> &'static str {
    match event_name {
        "plan_created" | "plan_revised" => "正在规划",
        "context_built" | "context_compressed" => "整理信息",
        "model_called" | "model_retry" => "正在分析",
        "action_proposed" | "action_blocked" => "评估操作",
        "tool_started" | "tool_retry" => "正在查询",
        "tool_finished" | "observation_recorded" | "evidence_collected" => "收集证据",
        "verification_failed" => "需要进一步确认",
        "checkpoint_saved" | "run_paused" | "run_resumed" => "保存进度",
        "run_completed" => "分析完成",
        "run_failed" => "分析未完成",
        _ => "正在处理",
    }
}

fn status_label(status: &str) -> &'static str {
    match status {
        "completed" => "已完成",
        "blocked" => "需要处理",
        "waiting_approval" => "等待确认",
        "waiting_user_input" => "需要补充信息",
        "stalled" => "暂时停滞",
        "failed" => "未完成",
        _ => "状态未知",
    }
}

fn is_safe_trajectory_event(event_name: &str) -> bool {
    matches!(
        event_name,
        "plan_created"
            | "context_built"
            | "model_called"
            | "model_retry"
            | "action_proposed"
            | "action_blocked"
            | "tool_started"
            | "tool_finished"
            | "tool_retry"
            | "observation_recorded"
            | "verification_failed"
            | "context_compressed"
            | "checkpoint_saved"
            | "run_paused"
            | "run_resumed"
            | "run_completed"
            | "run_failed"
            | "evidence_collected"
            | "plan_revised"
    )
}

impl Render for OpsMindConsole {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let report_download_detail = report_download_detail(&self.report_download);
        let (page_title, page_description) = match self.page {
            WorkspacePage::Investigation => {
                ("诊断", "输入问题现象，系统将协助收集信息并给出分析结论。")
            }
            WorkspacePage::Knowledge => ("知识库", "查看与诊断相关的操作说明和处理经验。"),
            WorkspacePage::History => ("历史记录", "查看本次应用中已完成的诊断记录。"),
            WorkspacePage::Mcp => ("设置", "管理模型、知识库和诊断可读取的数据源。"),
        };
        div()
            .flex()
            .flex_col()
            .size_full()
            .bg(rgb(0xe8f1f7))
            .text_color(rgb(0x24313a))
            .child(
                div()
                    .flex()
                    .justify_between()
                    .items_center()
                    .px(px(20.0))
                    .py(px(12.0))
                    .bg(rgba(0xffffff9e))
                    .border_b_1()
                    .border_color(rgba(0xffffffc7))
                    .child(
                        div()
                            .flex()
                            .items_center()
                            .gap(px(10.0))
                            .child(
                                img(PathBuf::from(concat!(
                                    env!("CARGO_MANIFEST_DIR"),
                                    "/assets/opsmind-icon.png"
                                )))
                                .size(px(28.0)),
                            )
                            .child(
                                div()
                                    .flex()
                                    .flex_col()
                                    .gap(px(2.0))
                                    .child(div().child("OpsMind"))
                                    .child(
                                        div()
                                            .text_size(px(10.0))
                                            .text_color(rgb(0x75818d))
                                            .child("智能诊断工作台"),
                                    ),
                            ),
                    )
                    .child(
                        div()
                            .px(px(12.0))
                            .py(px(6.0))
                            .rounded(px(999.0))
                            .bg(rgba(0xffffff8f))
                            .border_1()
                            .border_color(connection_color(&self.connection))
                            .text_size(px(11.0))
                            .text_color(connection_color(&self.connection))
                            .child(self.connection_detail()),
                    ),
            )
            .child(
                div()
                    .flex()
                    .flex_1()
                    .overflow_hidden()
                    .child(workbench_sidebar(self.page, cx))
                    .child(
                        div()
                            .id("main-workspace-scroll")
                            .flex()
                            .flex_col()
                            .flex_1()
                            .min_w(px(0.0))
                            .p(px(24.0))
                            .gap(px(18.0))
                            .overflow_y_scroll()
                            .child(
                                div()
                                    .flex()
                                    .items_center()
                                    .justify_between()
                                    .border_b_1()
                                    .border_color(rgba(0xffffffbd))
                                    .pb(px(14.0))
                                    .child(
                                        div()
                                            .flex()
                                            .items_center()
                                            .gap(px(10.0))
                                            .child(div().text_size(px(12.0)).child(page_title)),
                                    )
                                    .child(
                                        div()
                                            .text_size(px(11.0))
                                            .text_color(rgb(0x75818d))
                                            .child("⌘↵ 运行"),
                                    ),
                            )
                            .child(
                                div().flex().justify_between().items_end().child(
                                    div()
                                        .flex()
                                        .flex_col()
                                        .gap(px(6.0))
                                        .child(div().text_xl().child(page_title))
                                        .child(
                                            div().text_color(rgb(0x697783)).child(page_description),
                                        ),
                                ),
                            )
                            .when(self.page == WorkspacePage::Investigation, |this| {
                                this.child(
                                    div()
                                        .flex()
                                        .flex_col()
                                        .p(px(18.0))
                                        .gap(px(12.0))
                                        .rounded(px(14.0))
                                        .bg(rgba(0xffffffa3))
                                        .border_1()
                                        .border_color(rgba(0xffffffd1))
                                        .child(
                                            div()
                                                .flex()
                                                .justify_between()
                                                .child(
                                                    div()
                                                        .text_size(px(10.0))
                                                        .text_color(rgb(0x71808b))
                                                        .child("诊断说明"),
                                                )
                                                .child(
                                                    div()
                                                        .text_size(px(10.0))
                                                        .text_color(rgb(0x4f829c))
                                                        .child("可以开始"),
                                                ),
                                        )
                                        .child(Input::new(&self.diagnosis_input).h(px(150.0)))
                                        .child(
                                            div().flex().justify_end().child(
                                                Button::new("prepare-diagnosis")
                                                    .label("运行调查  →")
                                                    .info()
                                                    .loading(self.is_busy())
                                                    .disabled(
                                                        !self.backend_is_ready()
                                                            || self.is_busy()
                                                            || self
                                                                .waiting_for_user_input()
                                                                .is_some()
                                                            || self
                                                                .waiting_for_approval()
                                                                .is_some()
                                                            || self
                                                                .approval_is_recorded()
                                                                .is_some(),
                                                    )
                                                    .on_click(cx.listener(Self::submit_diagnosis)),
                                            ),
                                        ),
                                )
                                .child(
                                    div()
                                        .flex()
                                        .gap(px(12.0))
                                        .child(panel("服务状态", &self.connection_detail()))
                                        .child(panel("分析进度", &self.run_detail())),
                                )
                                .when_some(
                                    self.waiting_for_user_input()
                                        .map(|(_, question)| question.to_owned()),
                                    |this, question| {
                                        this.child(
                                            intervention_panel("需要补充信息", question)
                                                .child(Input::new(&self.operator_input).h(px(86.0)))
                                                .child(
                                                    div()
                                                        .flex()
                                                        .items_center()
                                                        .justify_between()
                                                        .child(
                                                            div().text_color(rgb(0x697783)).child(
                                                                self.operator_submission_detail(),
                                                            ),
                                                        )
                                                        .child(
                                                            Button::new("resume-with-user-input")
                                                                .label("提交并继续  →")
                                                                .info()
                                                                .loading(self.is_busy())
                                                                .disabled(
                                                                    !self.backend_is_ready()
                                                                        || self.is_busy(),
                                                                )
                                                                .on_click(cx.listener(
                                                                    Self::submit_user_input,
                                                                )),
                                                        ),
                                                ),
                                        )
                                    },
                                )
                                .when_some(
                                    self.waiting_for_approval().map(|(_, tool_name, reason)| {
                                        (tool_name.to_owned(), reason.to_owned())
                                    }),
                                    |this, (_tool_name, reason)| {
                                        this.child(
                                            intervention_panel("请确认操作", reason)
                                                .child(
                                                    div().child(
                                                        "系统需要执行一项可能影响服务的操作。",
                                                    ),
                                                )
                                                .child(Input::new(&self.approval_input).h(px(70.0)))
                                                .child(
                                                    div()
                                                        .flex()
                                                        .items_center()
                                                        .justify_between()
                                                        .child(
                                                            div().text_color(rgb(0x697783)).child(
                                                                self.approval_submission_detail(),
                                                            ),
                                                        )
                                                        .child(
                                                            div()
                                                                .flex()
                                                                .gap(px(8.0))
                                                                .child(
                                                                    Button::new("reject-approval")
                                                                        .label("拒绝动作")
                                                                        .disabled(
                                                                            !self
                                                                                .backend_is_ready()
                                                                                || self.is_busy(),
                                                                        )
                                                                        .on_click(cx.listener(
                                                                            Self::reject_run,
                                                                        )),
                                                                )
                                                                .child(
                                                                    Button::new("approve-approval")
                                                                        .label("记录批准")
                                                                        .info()
                                                                        .loading(self.is_busy())
                                                                        .disabled(
                                                                            !self
                                                                                .backend_is_ready()
                                                                                || self.is_busy(),
                                                                        )
                                                                        .on_click(cx.listener(
                                                                            Self::approve_run,
                                                                        )),
                                                                ),
                                                        ),
                                                ),
                                        )
                                    },
                                )
                                .when_some(
                                    self.approval_is_recorded()
                                        .map(|(_, tool_name)| tool_name.to_owned()),
                                    |this, _tool_name| {
                                        this.child(
                                            div()
                                                .flex()
                                                .items_center()
                                                .justify_between()
                                                .p(px(16.0))
                                                .rounded(px(10.0))
                                                .bg(rgb(0x18261d))
                                                .border_1()
                                                .border_color(rgb(0x5b7653))
                                                .child(
                                                    div()
                                                        .flex()
                                                        .flex_col()
                                                        .gap(px(4.0))
                                                        .child(
                                                            div()
                                                                .text_color(rgb(0x4f829c))
                                                                .child("已完成确认"),
                                                        )
                                                        .child(
                                                            div().child("再次确认后将继续分析。"),
                                                        ),
                                                )
                                                .child(
                                                    Button::new("resume-approved-run")
                                                        .label("确认并继续")
                                                        .info()
                                                        .loading(self.is_busy())
                                                        .disabled(
                                                            !self.backend_is_ready()
                                                                || self.is_busy(),
                                                        )
                                                        .on_click(
                                                            cx.listener(Self::resume_approved_run),
                                                        ),
                                                ),
                                        )
                                    },
                                )
                                .when_some(
                                    self.final_answer().map(str::to_owned),
                                    |this, answer: String| {
                                        this.child(report_panel(
                                            &answer,
                                            report_download_detail,
                                            cx,
                                        ))
                                    },
                                )
                            })
                            .when(self.page == WorkspacePage::Knowledge, |this| {
                                this.child(knowledge_catalog_panel(
                                    &self.knowledge_catalog,
                                    &self.selected_knowledge_document,
                                    &self.knowledge_title_input,
                                    &self.knowledge_content_input,
                                    &self.knowledge_submission,
                                    cx,
                                ))
                            })
                            .when(self.page == WorkspacePage::History, |this| {
                                this.child(history_panel(&self.history))
                            })
                            .when(self.page == WorkspacePage::Mcp, |this| {
                                this.child(self.mcp_configuration_panel(cx))
                            }),
                    )
                    .child(trajectory_panel(
                        &self.run_detail(),
                        &self.trajectory,
                        self.expanded_trajectory_id.as_deref(),
                        cx,
                    )),
            )
    }
}

fn workbench_sidebar(page: WorkspacePage, cx: &mut Context<OpsMindConsole>) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .w(px(220.0))
        .p(px(14.0))
        .gap(px(18.0))
        .bg(rgba(0xffffff8f))
        .border_r_1()
        .border_color(rgba(0xffffffb8))
        .child(
            div()
                .text_size(px(10.0))
                .text_color(rgb(0x75818d))
                .child("工作区"),
        )
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(4.0))
                .child(
                    workbench_nav_item(
                        "nav-investigation",
                        "诊断",
                        page == WorkspacePage::Investigation,
                    )
                    .on_click(cx.listener(OpsMindConsole::show_investigation)),
                )
                .child(
                    workbench_nav_item("nav-knowledge", "知识库", page == WorkspacePage::Knowledge)
                        .on_click(cx.listener(OpsMindConsole::show_knowledge)),
                )
                .child(
                    workbench_nav_item("nav-history", "历史记录", page == WorkspacePage::History)
                        .on_click(cx.listener(OpsMindConsole::show_history)),
                )
                .child(
                    workbench_nav_item("nav-mcp", "设置", page == WorkspacePage::Mcp)
                        .on_click(cx.listener(OpsMindConsole::show_mcp)),
                ),
        )
        .child(
            div()
                .mt_auto()
                .flex()
                .flex_col()
                .gap(px(6.0))
                .p(px(12.0))
                .rounded(px(8.0))
                .bg(rgba(0xffffff75))
                .border_1()
                .border_color(rgba(0xffffffb8))
                .child(
                    div()
                        .text_size(px(10.0))
                        .text_color(rgb(0x5a8da7))
                        .child("使用提示"),
                )
                .child(
                    div()
                        .text_size(px(11.0))
                        .text_color(rgb(0x667480))
                        .child("描述问题现象，系统会协助你完成分析。"),
                ),
        )
}

fn workbench_nav_item(
    id: &'static str,
    label: &'static str,
    active: bool,
) -> gpui::Stateful<gpui::Div> {
    div()
        .id(id)
        .px(px(10.0))
        .py(px(8.0))
        .rounded(px(7.0))
        .bg(if active {
            rgba(0xffffffb8)
        } else {
            rgba(0xffffff00)
        })
        .text_color(if active { rgb(0x356f8a) } else { rgb(0x687783) })
        .text_size(px(12.0))
        .cursor_pointer()
        .hover(|this| this.bg(rgba(0xffffff8f)))
        .child(label)
}

fn knowledge_catalog_panel(
    catalog: &KnowledgeCatalogState,
    selected_document: &SelectedKnowledgeDocumentState,
    title_input: &Entity<InputState>,
    content_input: &Entity<InputState>,
    submission: &KnowledgeSubmissionState,
    cx: &mut Context<OpsMindConsole>,
) -> gpui::Div {
    let mut panel = div()
        .flex()
        .flex_col()
        .gap(px(12.0))
        .p(px(20.0))
        .rounded(px(14.0))
        .bg(rgba(0xffffff8f))
        .border_1()
        .border_color(rgba(0xffffffb8))
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(8.0))
                .p(px(14.0))
                .rounded(px(10.0))
                .bg(rgba(0xe3f1f8a8))
                .child(div().text_color(rgb(0x4f829c)).child("新增知识"))
                .child(Input::new(title_input).h(px(34.0)))
                .child(Input::new(content_input).h(px(110.0)))
                .child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .child(
                            div()
                                .text_size(px(11.0))
                                .text_color(rgb(0x697783))
                                .child(knowledge_submission_detail(submission)),
                        )
                        .child(
                            Button::new("create-knowledge-document")
                                .label("保存到知识库")
                                .info()
                                .loading(matches!(submission, KnowledgeSubmissionState::Saving))
                                .disabled(matches!(submission, KnowledgeSubmissionState::Saving))
                                .on_click(cx.listener(OpsMindConsole::submit_knowledge_document)),
                        ),
                ),
        );

    match catalog {
        KnowledgeCatalogState::Loading => {
            panel = panel
                .child(div().text_color(rgb(0x4f829c)).child("正在加载知识库…"))
                .child(
                    div()
                        .text_size(px(12.0))
                        .text_color(rgb(0x697783))
                        .child("正在读取已导入的文档目录。"),
                )
        }
        KnowledgeCatalogState::Unavailable => {
            panel = panel
                .child(div().text_color(rgb(0xc76a62)).child("暂时无法读取知识库"))
                .child(
                    div()
                        .text_size(px(12.0))
                        .text_color(rgb(0x697783))
                        .child("请确认后端服务已启动后重试。"),
                )
        }
        KnowledgeCatalogState::Ready(catalog) => {
            panel = panel.child(
                div()
                    .flex()
                    .items_center()
                    .justify_between()
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .gap(px(4.0))
                            .child(div().text_color(rgb(0x4f829c)).child("已加载的知识"))
                            .child(div().text_size(px(12.0)).text_color(rgb(0x697783)).child(
                                format!(
                                    "{} 份文档，{} 个知识片段",
                                    catalog.document_count, catalog.chunk_count
                                ),
                            )),
                    )
                    .child(
                        div()
                            .px(px(10.0))
                            .py(px(5.0))
                            .rounded(px(999.0))
                            .bg(rgba(0xe3f1f8c7))
                            .text_size(px(11.0))
                            .text_color(rgb(0x4f829c))
                            .child("可用于诊断"),
                    ),
            );
            for (index, document) in catalog.documents.iter().enumerate() {
                let document_id = document.document_id.clone();
                let title = document.title.clone();
                panel = panel.child(
                    div()
                        .id(("knowledge-document", index))
                        .flex()
                        .items_center()
                        .justify_between()
                        .p(px(14.0))
                        .rounded(px(10.0))
                        .bg(rgba(0xffffffa3))
                        .border_1()
                        .border_color(rgba(0xffffffd1))
                        .cursor_pointer()
                        .hover(|this| this.bg(rgba(0xe3f1f8c7)))
                        .on_click(cx.listener(move |console, _, _, cx| {
                            console.show_knowledge_document(document_id.clone(), title.clone(), cx);
                        }))
                        .child(div().text_size(px(13.0)).child(document.title.clone()))
                        .child(
                            div()
                                .text_size(px(11.0))
                                .text_color(rgb(0x697783))
                                .child(format!("{} 个片段", document.chunk_count)),
                        ),
                );
            }
        }
    }

    panel = panel.child(selected_knowledge_document_panel(selected_document));

    panel
}

fn selected_knowledge_document_panel(state: &SelectedKnowledgeDocumentState) -> gpui::Div {
    let mut panel = div()
        .flex()
        .flex_col()
        .gap(px(8.0))
        .p(px(16.0))
        .rounded(px(10.0))
        .bg(rgba(0xffffffa3))
        .border_1()
        .border_color(rgba(0xffffffd1));

    match state {
        SelectedKnowledgeDocumentState::None => {
            panel = panel.child(
                div()
                    .text_size(px(12.0))
                    .text_color(rgb(0x697783))
                    .child("点击已加载的知识，可查看详细内容。"),
            );
        }
        SelectedKnowledgeDocumentState::Loading { title } => {
            panel = panel.child(
                div()
                    .text_color(rgb(0x697783))
                    .child(format!("正在读取《{title}》…")),
            );
        }
        SelectedKnowledgeDocumentState::Unavailable => {
            panel = panel.child(
                div()
                    .text_color(rgb(0xc76a62))
                    .child("暂时无法读取该知识文档。"),
            );
        }
        SelectedKnowledgeDocumentState::Ready(document) => {
            panel = panel.child(
                div()
                    .text_color(rgb(0x4f829c))
                    .child(document.title.clone()),
            );
            for line in document
                .content
                .lines()
                .filter(|line| !line.trim().is_empty())
            {
                panel = panel.child(div().text_size(px(12.0)).child(line.to_owned()));
            }
        }
    }
    panel
}

fn knowledge_submission_detail(submission: &KnowledgeSubmissionState) -> &'static str {
    match submission {
        KnowledgeSubmissionState::Draft => "支持 Markdown，保存后可立即用于诊断。",
        KnowledgeSubmissionState::Invalid => "请填写标题和内容。",
        KnowledgeSubmissionState::Saving => "正在保存并更新知识库。",
        KnowledgeSubmissionState::Saved => "已保存，可用于后续诊断。",
        KnowledgeSubmissionState::Failed => "暂时无法保存，请确认知识服务已配置。",
    }
}

fn model_configuration_form(
    configuration: &ModelConfiguration,
    llm_provider_input: &Entity<InputState>,
    llm_model_input: &Entity<InputState>,
    llm_api_key_input: &Entity<InputState>,
    llm_base_url_input: &Entity<InputState>,
    embedding_model_input: &Entity<InputState>,
    embedding_api_key_input: &Entity<InputState>,
    embedding_base_url_input: &Entity<InputState>,
    embedding_vector_size_input: &Entity<InputState>,
) -> gpui::Div {
    div()
        .flex()
        .flex_col()
        .gap(px(10.0))
        .p(px(14.0))
        .rounded(px(10.0))
        .bg(rgba(0xffffffa3))
        .border_1()
        .border_color(rgba(0xffffffd1))
        .child(
            div()
                .flex()
                .justify_between()
                .items_center()
                .child(div().text_size(px(13.0)).child("模型与知识库"))
                .child(
                    div()
                        .text_size(px(10.0))
                        .text_color(rgb(0x5a8da7))
                        .child("保存后用于新的诊断"),
                ),
        )
        .child(
            div()
                .text_size(px(11.0))
                .text_color(rgb(0x697783))
                .child(format!(
                    "当前模型：{} · {}",
                    configuration.llm_provider.as_deref().unwrap_or("未配置"),
                    configuration.llm_model.as_deref().unwrap_or("未配置"),
                )),
        )
        .child(Input::new(llm_provider_input).h(px(34.0)))
        .child(Input::new(llm_model_input).h(px(34.0)))
        .child(div().text_size(px(11.0)).text_color(rgb(0x697783)).child(
            if configuration.llm_api_key_configured {
                "模型密钥已保存；留空不会覆盖。"
            } else {
                "尚未保存模型密钥。"
            },
        ))
        .child(Input::new(llm_api_key_input).h(px(34.0)))
        .child(Input::new(llm_base_url_input).h(px(34.0)))
        .child(
            div()
                .mt(px(4.0))
                .text_size(px(11.0))
                .text_color(rgb(0x5a8da7))
                .child(format!(
                    "当前 Embedding：{} · {} 维",
                    configuration.embedding_model.as_deref().unwrap_or("未配置"),
                    configuration.embedding_vector_size,
                )),
        )
        .child(Input::new(embedding_model_input).h(px(34.0)))
        .child(div().text_size(px(11.0)).text_color(rgb(0x697783)).child(
            if configuration.embedding_api_key_configured {
                "Embedding 密钥已保存；留空不会覆盖。"
            } else {
                "未单独配置时会复用模型密钥。"
            },
        ))
        .child(Input::new(embedding_api_key_input).h(px(34.0)))
        .child(Input::new(embedding_base_url_input).h(px(34.0)))
        .child(Input::new(embedding_vector_size_input).h(px(34.0)))
}

fn mcp_service_form(
    name: &'static str,
    capability: &'static str,
    configuration: &McpServiceConfiguration,
    url_input: &Entity<InputState>,
    token_input: &Entity<InputState>,
) -> gpui::Div {
    div()
        .flex()
        .flex_col()
        .gap(px(8.0))
        .p(px(14.0))
        .rounded(px(10.0))
        .bg(rgba(0xffffffa3))
        .border_1()
        .border_color(rgba(0xffffffd1))
        .child(
            div()
                .flex()
                .justify_between()
                .items_center()
                .child(div().text_size(px(13.0)).child(name))
                .child(
                    div()
                        .text_size(px(10.0))
                        .text_color(rgb(0x5a8da7))
                        .child(capability),
                ),
        )
        .child(div().text_size(px(11.0)).text_color(rgb(0x697783)).child(
            match &configuration.url {
                Some(url) => format!("当前地址：{url}"),
                None => String::from("尚未配置地址"),
            },
        ))
        .child(Input::new(url_input).h(px(34.0)))
        .child(div().text_size(px(11.0)).text_color(rgb(0x697783)).child(
            if configuration.token_configured {
                "已保存只读令牌；留空不会覆盖。"
            } else {
                "尚未保存令牌；如需认证可在下方填写。"
            },
        ))
        .child(Input::new(token_input).h(px(34.0)))
}

fn history_panel(history: &HistoryState) -> gpui::Div {
    let mut panel = div()
        .flex()
        .flex_col()
        .gap(px(12.0))
        .p(px(20.0))
        .rounded(px(14.0))
        .bg(rgba(0xffffff8f))
        .border_1()
        .border_color(rgba(0xffffffb8))
        .child(div().text_color(rgb(0x4f829c)).child("诊断历史"));

    match history {
        HistoryState::Loading => {
            panel = panel.child(div().text_color(rgb(0x697783)).child("正在读取历史记录…"));
        }
        HistoryState::Unavailable => {
            panel = panel.child(
                div()
                    .text_color(rgb(0xc76a62))
                    .child("暂时无法读取历史记录，请确认服务已启动。"),
            );
        }
        HistoryState::Ready(history) if history.runs.is_empty() => {
            panel = panel.child(
                div()
                    .text_color(rgb(0x697783))
                    .child("还没有诊断记录。完成一次分析后会显示在这里。"),
            );
        }
        HistoryState::Ready(history) => {
            for run in &history.runs {
                panel = panel.child(history_item_panel(run));
            }
        }
    }
    panel
}

fn history_item_panel(run: &DiagnosisRunHistoryItem) -> gpui::Div {
    div()
        .flex()
        .flex_col()
        .gap(px(5.0))
        .p(px(14.0))
        .rounded(px(10.0))
        .bg(rgba(0xffffffa3))
        .border_1()
        .border_color(rgba(0xffffffd1))
        .child(div().text_size(px(13.0)).child(run.query.clone()))
        .child(
            div()
                .text_size(px(11.0))
                .text_color(rgb(0x697783))
                .child(format!(
                    "{} · {} 个步骤 · {} · #{}",
                    run.status
                        .as_deref()
                        .map(status_label)
                        .unwrap_or("状态未知"),
                    run.step_count,
                    history_timestamp(&run.captured_at),
                    run.run_id.get(..8).unwrap_or("未知"),
                )),
        )
}

fn history_timestamp(value: &str) -> String {
    value
        .trim_end_matches('Z')
        .replace('T', " ")
        .split('.')
        .next()
        .unwrap_or("时间未知")
        .to_owned()
}

fn panel(title: &'static str, detail: &str) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .flex_1()
        .p(px(16.0))
        .gap(px(8.0))
        .rounded(px(12.0))
        .bg(rgba(0xffffff8f))
        .border_1()
        .border_color(rgba(0xffffffb8))
        .child(
            div()
                .text_size(px(10.0))
                .text_color(rgb(0x5a8da7))
                .child(title),
        )
        .child(div().text_size(px(13.0)).child(detail.to_owned()))
}

fn trajectory_panel(
    detail: &str,
    entries: &[TrajectoryEntry],
    expanded_entry_id: Option<&str>,
    cx: &mut Context<OpsMindConsole>,
) -> impl IntoElement {
    let mut panel = div()
        .id("trajectory-panel-scroll")
        .flex()
        .flex_col()
        .w(px(300.0))
        .h_full()
        .flex_none()
        .p(px(20.0))
        .gap(px(10.0))
        .overflow_y_scroll()
        .rounded(px(14.0))
        .bg(rgba(0xffffff8f))
        .border_1()
        .border_color(rgba(0xffffffb8))
        .child(
            div()
                .text_size(px(11.0))
                .text_color(rgb(0x5a8da7))
                .child("分析进展"),
        )
        .child(div().text_color(rgb(0x697783)).child(detail.to_owned()));

    if entries.is_empty() {
        panel = panel.child(
            div()
                .text_color(rgb(0x7b8994))
                .child("开始分析后，这里会显示进展。"),
        );
    } else {
        for (index, entry) in entries.iter().enumerate() {
            let entry_id = entry.id.clone();
            let expanded = expanded_entry_id == Some(entry_id.as_str());
            let mut entry_panel = div()
                .id(("trajectory-entry", index))
                .flex()
                .flex_col()
                .gap(px(6.0))
                .p(px(10.0))
                .rounded(px(8.0))
                .bg(if expanded {
                    rgba(0xe0f1fae8)
                } else {
                    rgba(0xffffffa3)
                })
                .border_1()
                .border_color(if expanded {
                    rgb(0x8fc3d8)
                } else {
                    rgba(0xffffffb8)
                })
                .cursor_pointer()
                .on_click(cx.listener(move |console, _, _, cx| {
                    console.toggle_trajectory_entry(entry_id.clone(), cx);
                }))
                .child(
                    div()
                        .flex()
                        .justify_between()
                        .items_center()
                        .gap(px(8.0))
                        .child(
                            div()
                                .min_w(px(0.0))
                                .text_size(px(12.0))
                                .child(entry.summary.clone()),
                        )
                        .child(
                            div()
                                .text_size(px(11.0))
                                .text_color(rgb(0x5a8da7))
                                .child(if expanded { "收起" } else { "查看" }),
                        ),
                );
            if expanded {
                entry_panel = entry_panel.child(
                    div()
                        .pt(px(6.0))
                        .border_t_1()
                        .border_color(rgba(0xffffffb8))
                        .text_size(px(11.0))
                        .text_color(rgb(0x596a76))
                        .child(entry.detail.clone()),
                );
            }
            panel = panel.child(entry_panel);
        }
    }
    panel
}

fn report_panel(
    answer: &str,
    download_detail: &'static str,
    cx: &mut Context<OpsMindConsole>,
) -> impl IntoElement {
    let mut panel = div()
        .flex()
        .flex_col()
        .p(px(20.0))
        .gap(px(8.0))
        .rounded(px(14.0))
        .bg(rgba(0xffffffa3))
        .border_1()
        .border_color(rgba(0xffffffd1))
        .child(
            div()
                .flex()
                .items_center()
                .justify_between()
                .child(div().text_color(rgb(0x4f829c)).child("分析结论"))
                .child(
                    Button::new("download-diagnosis-report")
                        .label("下载 Markdown")
                        .on_click(cx.listener(OpsMindConsole::download_report)),
                ),
        )
        .child(
            div()
                .text_size(px(11.0))
                .text_color(rgb(0x697783))
                .child(download_detail),
        );

    for line in answer.lines().filter(|line| !line.trim().is_empty()) {
        panel = panel.child(div().text_size(px(13.0)).child(line.to_owned()));
    }
    panel
}

fn report_download_detail(state: &ReportDownloadState) -> &'static str {
    match state {
        ReportDownloadState::Idle => "可将本次分析保存为 Markdown 文档。",
        ReportDownloadState::Saved => "已保存到“下载”文件夹。",
        ReportDownloadState::Failed => "保存失败，请检查本机“下载”文件夹权限。",
    }
}

fn save_report_to_downloads(report: &str) -> Result<(), ()> {
    let home_directory = env::var_os("HOME").map(PathBuf::from).ok_or(())?;
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| ())?
        .as_secs();
    let path = home_directory
        .join("Downloads")
        .join(format!("OpsMind-诊断报告-{timestamp}.md"));
    fs::write(path, report).map_err(|_| ())
}

fn intervention_panel(title: &'static str, detail: String) -> gpui::Div {
    div()
        .flex()
        .flex_col()
        .p(px(20.0))
        .gap(px(12.0))
        .rounded(px(14.0))
        .bg(rgba(0xfff3e5c7))
        .border_1()
        .border_color(rgb(0xe8c18d))
        .child(
            div()
                .text_size(px(11.0))
                .text_color(rgb(0xa56d2c))
                .child(title),
        )
        .child(div().text_color(rgb(0x75592f)).child(detail))
}

fn connection_color(connection: &ConnectionState) -> gpui::Hsla {
    match connection {
        ConnectionState::Ready => rgb(0x5a8da7).into(),
        ConnectionState::Checking => rgb(0xc7954b).into(),
        ConnectionState::Unavailable => rgb(0xc76a62).into(),
    }
}

fn main() {
    Application::new().run(|cx: &mut App| {
        gpui_component::init(cx);
        let bounds = Bounds::centered(None, size(px(1280.0), px(760.0)), cx);
        cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                ..Default::default()
            },
            |window, cx| {
                let view = cx.new(|cx| OpsMindConsole::new(window, cx));
                cx.new(|cx| Root::new(view, window, cx))
            },
        )
        .expect("failed to open OpsMind desktop window");
        cx.activate(true);
    });
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{
        is_safe_run_id, normalize_approval_reason, normalize_diagnosis_query, safe_final_answer,
        safe_pending_approval, safe_pending_question, trajectory_entry,
    };

    #[test]
    fn normalizes_a_diagnosis_query() {
        let query = normalize_diagnosis_query("  checkout 延迟上升  ").expect("valid query");

        assert_eq!(query, "checkout 延迟上升");
    }

    #[test]
    fn rejects_blank_and_oversized_diagnosis_queries() {
        assert!(normalize_diagnosis_query(" \n ").is_err());
        assert!(normalize_diagnosis_query(&"x".repeat(4_001)).is_err());
    }

    #[test]
    fn trajectory_projection_excludes_extra_event_fields() {
        let entry = trajectory_entry(
            "tool_finished",
            &json!({
                "event_type": "tool_finished",
                "step_id": 4,
                "tool_name": "query_metrics",
                "latency_ms": 18,
                "observation": "credentials=secret",
            }),
        )
        .expect("safe trajectory event");

        assert_eq!(entry.summary, "步骤 4 · 收集证据 · 耗时 18 ms");
        assert_eq!(entry.detail, "阶段：收集证据\n涉及：服务指标\n耗时：18 ms");
        assert!(!entry.detail.contains("secret"));
    }

    #[test]
    fn trajectory_projection_rejects_unknown_event_names() {
        let entry = trajectory_entry(
            "untrusted_event",
            &json!({"event_type": "untrusted_event", "step_id": 1}),
        );

        assert!(entry.is_none());
    }

    #[test]
    fn accepts_only_bounded_operator_questions_and_uuid_run_ids() {
        assert_eq!(
            safe_pending_question("  请确认受影响的服务。  "),
            Some(String::from("请确认受影响的服务。"))
        );
        assert!(safe_pending_question(" ").is_none());
        assert!(safe_pending_question(&"x".repeat(2_001)).is_none());
        assert!(is_safe_run_id("018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1"));
        assert!(!is_safe_run_id("../../unexpected-path"));
    }

    #[test]
    fn accepts_only_safe_approval_summaries_and_reasons() {
        assert_eq!(
            safe_pending_approval(" restart_service ", "  该工具的风险策略要求人工审批。  "),
            Some((
                String::from("restart_service"),
                String::from("该工具的风险策略要求人工审批。")
            ))
        );
        assert!(safe_pending_approval("", "审批原因").is_none());
        assert!(safe_pending_approval("restart_service", &"x".repeat(2_001)).is_none());
        assert!(normalize_approval_reason("  维护窗口已确认。 ").is_ok());
        assert!(normalize_approval_reason(" ").is_err());
    }

    #[test]
    fn accepts_only_bounded_final_answers() {
        assert_eq!(
            safe_final_answer("  根因是连接池耗尽。  "),
            Some(String::from("根因是连接池耗尽。"))
        );
        assert!(safe_final_answer(" ").is_none());
        assert!(safe_final_answer(&"x".repeat(12_001)).is_none());
    }
}
