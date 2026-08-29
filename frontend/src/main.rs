//! OpsMind 的 GPUI 桌面控制台入口。

use std::{
    env,
    time::{SystemTime, UNIX_EPOCH},
};

mod sse;

mod api_client;

use gpui::{
    App, Application, Bounds, ClickEvent, Context, Entity, IntoElement, Render, Subscription,
    Window, WindowBounds, WindowOptions, div, prelude::*, px, rgb, rgba, size,
};
use gpui_component::{
    Disableable as _, Root,
    button::{Button, ButtonVariants as _},
    input::{Input, InputEvent, InputState},
};

use crate::{
    api_client::{
        ApprovalRequest, DiagnosisRunSummary, KnowledgeCatalog, OpsMindApiClient,
        ResumeDiagnosisRequest, StreamDiagnosisRequest,
    },
    sse::ServerSentEvent,
};

const DEFAULT_API_BASE_URL: &str = "http://127.0.0.1:8000";
const MAX_TRAJECTORY_ENTRIES: usize = 12;
const MAX_FINAL_ANSWER_CHARS: usize = 12_000;

struct OpsMindConsole {
    api_base_url: String,
    session_id: String,
    thread_id: String,
    connection: ConnectionState,
    catalog: CatalogState,
    knowledge_catalog: KnowledgeCatalogState,
    diagnosis_input: Entity<InputState>,
    operator_input: Entity<InputState>,
    approval_input: Entity<InputState>,
    submission: SubmissionState,
    operator_submission: SubmissionState,
    approval_submission: SubmissionState,
    page: WorkspacePage,
    run: RunState,
    trajectory: Vec<String>,
    _input_subscription: Subscription,
    _operator_input_subscription: Subscription,
    _approval_input_subscription: Subscription,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum WorkspacePage {
    Investigation,
    Knowledge,
    History,
}

enum ConnectionState {
    Checking,
    Ready,
    Unavailable,
}

enum CatalogState {
    Waiting,
    Ready { count: usize },
    Unavailable,
}

enum KnowledgeCatalogState {
    Loading,
    Ready(KnowledgeCatalog),
    Unavailable,
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

enum StreamUpdate {
    Event(ServerSentEvent),
    Closed { succeeded: bool },
    Resumed(DiagnosisRunSummary),
    ResumeFailed,
    ApprovalRecorded { run_id: String, tool_name: String },
}

struct BootstrapSnapshot {
    scenario_count: usize,
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
        let mut console = Self {
            api_base_url: env::var("OPSMIND_API_BASE_URL")
                .unwrap_or_else(|_| String::from(DEFAULT_API_BASE_URL)),
            session_id: desktop_identifier("session"),
            thread_id: desktop_identifier("thread"),
            connection: ConnectionState::Checking,
            catalog: CatalogState::Waiting,
            knowledge_catalog: KnowledgeCatalogState::Loading,
            diagnosis_input,
            operator_input,
            approval_input,
            submission: SubmissionState::Draft,
            operator_submission: SubmissionState::Draft,
            approval_submission: SubmissionState::Draft,
            page: WorkspacePage::Investigation,
            run: RunState::Idle,
            trajectory: Vec::new(),
            _input_subscription: input_subscription,
            _operator_input_subscription: operator_input_subscription,
            _approval_input_subscription: approval_input_subscription,
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
            .spawn(async move { load_bootstrap_snapshot(api_base_url) });
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

    fn apply_bootstrap_snapshot(&mut self, snapshot: Result<BootstrapSnapshot, ()>) {
        match snapshot {
            Ok(snapshot) => {
                self.connection = ConnectionState::Ready;
                self.catalog = CatalogState::Ready {
                    count: snapshot.scenario_count,
                };
            }
            Err(()) => {
                self.connection = ConnectionState::Unavailable;
                self.catalog = CatalogState::Unavailable;
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

    fn catalog_detail(&self) -> String {
        match &self.catalog {
            CatalogState::Waiting => String::from("正在加载可选场景…"),
            CatalogState::Ready { count } => format!("可选场景 {count} 个"),
            CatalogState::Unavailable => String::from("连接后即可查看可选场景"),
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

    fn show_history(&mut self, _: &ClickEvent, _: &mut Window, cx: &mut Context<Self>) {
        self.page = WorkspacePage::History;
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

fn trajectory_entry(event_name: &str, data: &serde_json::Value) -> Option<String> {
    let event_type = data["event_type"].as_str()?;
    if event_type != event_name || !is_safe_trajectory_event(event_name) {
        return None;
    }

    let step_id = data["step_id"].as_u64()?;
    let latency_ms = data["latency_ms"].as_u64();
    let mut entry = format!("步骤 {step_id} · {}", event_label(event_name));
    if let Some(latency_ms) = latency_ms {
        entry.push_str(&format!(" · 耗时 {latency_ms} ms"));
    }
    Some(entry)
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

fn load_bootstrap_snapshot(api_base_url: String) -> Result<BootstrapSnapshot, ()> {
    let client = OpsMindApiClient::new(api_base_url);
    let _health = client.health().map_err(|_| ())?;
    let scenarios = client.scenarios().map_err(|_| ())?;

    Ok(BootstrapSnapshot {
        scenario_count: scenarios.len(),
    })
}

impl Render for OpsMindConsole {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let (page_title, page_description) = match self.page {
            WorkspacePage::Investigation => {
                ("诊断", "输入问题现象，系统将协助收集信息并给出分析结论。")
            }
            WorkspacePage::Knowledge => ("知识库", "查看与诊断相关的操作说明和处理经验。"),
            WorkspacePage::History => ("历史记录", "查看本次应用中已完成的诊断记录。"),
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
                                div()
                                    .size(px(28.0))
                                    .rounded(px(7.0))
                                    .bg(rgb(0x6ca8c3))
                                    .text_color(rgb(0xffffff))
                                    .flex()
                                    .items_center()
                                    .justify_center()
                                    .child("O"),
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
                    .child(workbench_sidebar(&self.catalog_detail(), self.page, cx))
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .flex_1()
                            .p(px(24.0))
                            .gap(px(18.0))
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
                                            .child(div().text_size(px(12.0)).child(page_title))
                                            .child(
                                                div()
                                                    .px(px(8.0))
                                                    .py(px(3.0))
                                                    .rounded(px(999.0))
                                                    .bg(rgb(0xdcebF4))
                                                    .text_size(px(10.0))
                                                    .text_color(rgb(0x4f829c))
                                                    .child("新建"),
                                            ),
                                    )
                                    .child(
                                        div()
                                            .text_size(px(11.0))
                                            .text_color(rgb(0x75818d))
                                            .child("⌘↵ 运行"),
                                    ),
                            )
                            .child(
                                div()
                                    .flex()
                                    .justify_between()
                                    .items_end()
                                    .child(
                                        div()
                                            .flex()
                                            .flex_col()
                                            .gap(px(6.0))
                                            .child(div().text_xl().child(page_title))
                                            .child(
                                                div()
                                                    .text_color(rgb(0x697783))
                                                    .child(page_description),
                                            ),
                                    )
                                    .child(div().text_color(rgb(0x5a8da7)).child("未命名")),
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
                                                    .primary()
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
                                                                .primary()
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
                                                                        .primary()
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
                                                        .primary()
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
                                    |this, answer| this.child(report_panel(&answer)),
                                )
                            })
                            .when(self.page == WorkspacePage::Knowledge, |this| {
                                this.child(knowledge_catalog_panel(&self.knowledge_catalog))
                            })
                            .when(self.page == WorkspacePage::History, |this| {
                                this.child(workspace_empty_state(
                                    "暂无历史记录",
                                    "完成一次诊断后，记录会显示在这里。",
                                ))
                            }),
                    )
                    .child(trajectory_panel(&self.run_detail(), &self.trajectory)),
            )
    }
}

fn workbench_sidebar(
    catalog_detail: &str,
    page: WorkspacePage,
    cx: &mut Context<OpsMindConsole>,
) -> impl IntoElement {
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
                ),
        )
        .child(
            div()
                .flex()
                .flex_col()
                .gap(px(8.0))
                .child(
                    div()
                        .text_size(px(10.0))
                        .text_color(rgb(0x75818d))
                        .child("可选场景"),
                )
                .child(
                    div()
                        .p(px(12.0))
                        .rounded(px(8.0))
                        .bg(rgba(0xffffff7a))
                        .border_1()
                        .border_color(rgba(0xffffffb8))
                        .text_size(px(12.0))
                        .text_color(rgb(0x5c6975))
                        .child(catalog_detail.to_owned()),
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

fn workspace_empty_state(title: &'static str, detail: &'static str) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .items_center()
        .p(px(32.0))
        .gap(px(10.0))
        .rounded(px(14.0))
        .bg(rgba(0xffffff8f))
        .border_1()
        .border_color(rgba(0xffffffb8))
        .child(div().text_xl().text_color(rgb(0x4f829c)).child(title))
        .child(div().text_color(rgb(0x697783)).child(detail))
}

fn knowledge_catalog_panel(catalog: &KnowledgeCatalogState) -> gpui::Div {
    let mut panel = div()
        .flex()
        .flex_col()
        .gap(px(12.0))
        .p(px(20.0))
        .rounded(px(14.0))
        .bg(rgba(0xffffff8f))
        .border_1()
        .border_color(rgba(0xffffffb8));

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
            for document in &catalog.documents {
                panel = panel.child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .p(px(14.0))
                        .rounded(px(10.0))
                        .bg(rgba(0xffffffa3))
                        .border_1()
                        .border_color(rgba(0xffffffd1))
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

    panel
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

fn trajectory_panel(detail: &str, entries: &[String]) -> impl IntoElement {
    let mut panel = div()
        .flex()
        .flex_col()
        .w(px(300.0))
        .p(px(20.0))
        .gap(px(10.0))
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
        for entry in entries.iter().rev().take(3).rev() {
            panel = panel.child(
                div()
                    .p(px(10.0))
                    .rounded(px(8.0))
                    .bg(rgba(0xffffffa3))
                    .text_size(px(12.0))
                    .child(entry.clone()),
            );
        }
    }
    panel
}

fn report_panel(answer: &str) -> impl IntoElement {
    let mut panel = div()
        .flex()
        .flex_col()
        .p(px(20.0))
        .gap(px(8.0))
        .rounded(px(14.0))
        .bg(rgba(0xffffffa3))
        .border_1()
        .border_color(rgba(0xffffffd1))
        .child(div().text_color(rgb(0x4f829c)).child("分析结论"));

    for line in answer.lines().filter(|line| !line.trim().is_empty()) {
        panel = panel.child(div().text_size(px(13.0)).child(line.to_owned()));
    }
    panel
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

        assert_eq!(entry, "步骤 4 · 收集证据 · 耗时 18 ms");
        assert!(!entry.contains("secret"));
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
