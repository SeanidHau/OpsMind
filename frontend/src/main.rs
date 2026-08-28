//! OpsMind 的 GPUI 桌面控制台入口。

use std::{
    env,
    time::{SystemTime, UNIX_EPOCH},
};

mod sse;

mod api_client;

use gpui::{
    App, Application, Bounds, ClickEvent, Context, Entity, IntoElement, Render, Subscription,
    Window, WindowBounds, WindowOptions, div, prelude::*, px, rgb, size,
};
use gpui_component::{
    Disableable as _,
    button::{Button, ButtonVariants as _},
    input::{Input, InputEvent, InputState},
};

use crate::{
    api_client::{
        ApprovalRequest, DiagnosisRunSummary, OpsMindApiClient, ResumeDiagnosisRequest,
        StreamDiagnosisRequest,
    },
    sse::ServerSentEvent,
};

const DEFAULT_API_BASE_URL: &str = "http://127.0.0.1:8000";
const MAX_TRAJECTORY_ENTRIES: usize = 12;

struct OpsMindConsole {
    api_base_url: String,
    session_id: String,
    thread_id: String,
    connection: ConnectionState,
    catalog: CatalogState,
    diagnosis_input: Entity<InputState>,
    operator_input: Entity<InputState>,
    approval_input: Entity<InputState>,
    submission: SubmissionState,
    operator_submission: SubmissionState,
    approval_submission: SubmissionState,
    run: RunState,
    trajectory: Vec<String>,
    _input_subscription: Subscription,
    _operator_input_subscription: Subscription,
    _approval_input_subscription: Subscription,
}

enum ConnectionState {
    Checking,
    Ready { version: String },
    Unavailable,
}

enum CatalogState {
    Waiting,
    Ready { count: usize },
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
    version: String,
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
            diagnosis_input,
            operator_input,
            approval_input,
            submission: SubmissionState::Draft,
            operator_submission: SubmissionState::Draft,
            approval_submission: SubmissionState::Draft,
            run: RunState::Idle,
            trajectory: Vec::new(),
            _input_subscription: input_subscription,
            _operator_input_subscription: operator_input_subscription,
            _approval_input_subscription: approval_input_subscription,
        };
        console.refresh_backend_status(cx);
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
                self.connection = ConnectionState::Ready {
                    version: snapshot.version,
                };
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

    fn connection_detail(&self) -> String {
        match &self.connection {
            ConnectionState::Checking => String::from("正在检查 FastAPI 服务…"),
            ConnectionState::Ready { version } => format!("已连接 · API {version}"),
            ConnectionState::Unavailable => String::from("服务不可用 · 检查后端是否已启动"),
        }
    }

    fn catalog_detail(&self) -> String {
        match &self.catalog {
            CatalogState::Waiting => String::from("正在读取可用场景…"),
            CatalogState::Ready { count } => format!("已加载 {count} 个安全场景摘要"),
            CatalogState::Unavailable => String::from("连接恢复后将重新读取场景目录"),
        }
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

    fn submission_detail(&self) -> String {
        match &self.submission {
            SubmissionState::Draft => String::from("填写描述后按 ⌘↵ 或点击按钮开始诊断。"),
            SubmissionState::Invalid => String::from("诊断描述不能为空，且不得超过 4000 个字符。"),
            SubmissionState::Prepared { query } => {
                format!(
                    "正在使用 {} 个字符的诊断描述进行流式诊断。",
                    query.chars().count()
                )
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
        matches!(&self.connection, ConnectionState::Ready { .. })
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
        self.run = RunState::Finished { status, step_count };
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
            status,
            step_count: summary.step_count,
        };
    }

    fn run_detail(&self) -> String {
        match &self.run {
            RunState::Idle => String::from("等待诊断运行事件。"),
            RunState::Running { event_count } => {
                format!("正在接收安全轨迹事件：{event_count} 条。")
            }
            RunState::WaitingForInput { .. } => String::from("等待操作人员补充信息。"),
            RunState::ResumingUserInput => String::from("正在提交补充信息并恢复运行。"),
            RunState::WaitingForApproval { .. } => String::from("等待高风险动作审批。"),
            RunState::RecordingApproval => String::from("正在记录审批决议。"),
            RunState::ApprovalRecorded { .. } => String::from("审批已记录，等待显式续跑。"),
            RunState::ResumingApproval => String::from("正在恢复已批准的动作。"),
            RunState::Finished { status, step_count } => {
                format!("运行结束 · {status} · {step_count} 个步骤")
            }
            RunState::Failed => String::from("运行未完成。请检查后端服务后重试。"),
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
    let tool_name = data["tool_name"].as_str();
    let latency_ms = data["latency_ms"].as_u64();
    let mut entry = format!("#{step_id} · {event_name}");
    if let Some(tool_name) = tool_name {
        entry.push_str(&format!(" · {tool_name}"));
    }
    if let Some(latency_ms) = latency_ms {
        entry.push_str(&format!(" · {latency_ms} ms"));
    }
    Some(entry)
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
    let health = client.health().map_err(|_| ())?;
    let scenarios = client.scenarios().map_err(|_| ())?;

    Ok(BootstrapSnapshot {
        version: health.version,
        scenario_count: scenarios.len(),
    })
}

impl Render for OpsMindConsole {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        div()
            .flex()
            .flex_col()
            .size_full()
            .bg(rgb(0x0b1014))
            .text_color(rgb(0xe7ecea))
            .child(
                div()
                    .flex()
                    .justify_between()
                    .items_center()
                    .p(px(24.0))
                    .bg(rgb(0x111a20))
                    .child(div().text_xl().child("OPSMIND // INCIDENT CONTROL ROOM"))
                    .child(
                        div()
                            .text_color(match &self.connection {
                                ConnectionState::Ready { .. } => rgb(0x8ee6a8),
                                ConnectionState::Checking => rgb(0xffb000),
                                ConnectionState::Unavailable => rgb(0xff6b6b),
                            })
                            .child("HARNESS CONSOLE"),
                    ),
            )
            .child(
                div()
                    .flex()
                    .flex_1()
                    .flex_col()
                    .p(px(24.0))
                    .gap(px(16.0))
                    .child(
                        div()
                            .flex()
                            .gap(px(16.0))
                            .child(panel("CONNECTION", &self.connection_detail()))
                            .child(panel("SCENARIO CATALOG", &self.catalog_detail()))
                            .child(trajectory_panel(&self.run_detail(), &self.trajectory)),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .p(px(18.0))
                            .gap(px(12.0))
                            .bg(rgb(0x111a20))
                            .child(div().text_color(rgb(0xffb000)).child("DIAGNOSIS BRIEF"))
                            .child(Input::new(&self.diagnosis_input).h(px(120.0)))
                            .child(
                                div()
                                    .flex()
                                    .items_center()
                                    .justify_between()
                                    .child(
                                        div()
                                            .text_color(rgb(0xaab6bc))
                                            .child(self.submission_detail()),
                                    )
                                    .child(
                                        Button::new("prepare-diagnosis")
                                            .label("开始诊断")
                                            .primary()
                                            .loading(self.is_busy())
                                            .disabled(
                                                !self.backend_is_ready()
                                                    || self.is_busy()
                                                    || self.waiting_for_user_input().is_some()
                                                    || self.waiting_for_approval().is_some()
                                                    || self.approval_is_recorded().is_some(),
                                            )
                                            .on_click(cx.listener(Self::submit_diagnosis)),
                                    ),
                            ),
                    )
                    .when_some(
                        self.waiting_for_user_input()
                            .map(|(_, question)| question.to_owned()),
                        |this, question| {
                            this.child(
                                div()
                                    .flex()
                                    .flex_col()
                                    .p(px(18.0))
                                    .gap(px(12.0))
                                    .bg(rgb(0x1a2024))
                                    .child(
                                        div()
                                            .text_color(rgb(0xffb000))
                                            .child("OPERATOR INPUT REQUIRED"),
                                    )
                                    .child(div().child(question))
                                    .child(Input::new(&self.operator_input).h(px(92.0)))
                                    .child(
                                        div()
                                            .flex()
                                            .items_center()
                                            .justify_between()
                                            .child(
                                                div()
                                                    .text_color(rgb(0xaab6bc))
                                                    .child(self.operator_submission_detail()),
                                            )
                                            .child(
                                                Button::new("resume-with-user-input")
                                                    .label("提交并继续")
                                                    .primary()
                                                    .loading(self.is_busy())
                                                    .disabled(
                                                        !self.backend_is_ready() || self.is_busy(),
                                                    )
                                                    .on_click(cx.listener(Self::submit_user_input)),
                                            ),
                                    ),
                            )
                        },
                    )
                    .when_some(
                        self.waiting_for_approval().map(|(_, tool_name, reason)| {
                            (tool_name.to_owned(), reason.to_owned())
                        }),
                        |this, (tool_name, reason)| {
                            this.child(
                                div()
                                    .flex()
                                    .flex_col()
                                    .p(px(18.0))
                                    .gap(px(12.0))
                                    .bg(rgb(0x241b16))
                                    .child(
                                        div()
                                            .text_color(rgb(0xffb000))
                                            .child("HIGH-RISK ACTION REVIEW"),
                                    )
                                    .child(div().child(format!("工具：{tool_name}")))
                                    .child(div().text_color(rgb(0xaab6bc)).child(reason))
                                    .child(Input::new(&self.approval_input).h(px(70.0)))
                                    .child(
                                        div()
                                            .flex()
                                            .items_center()
                                            .justify_between()
                                            .child(
                                                div()
                                                    .text_color(rgb(0xaab6bc))
                                                    .child(self.approval_submission_detail()),
                                            )
                                            .child(
                                                div()
                                                    .flex()
                                                    .gap(px(8.0))
                                                    .child(
                                                        Button::new("reject-approval")
                                                            .label("拒绝动作")
                                                            .disabled(
                                                                !self.backend_is_ready()
                                                                    || self.is_busy(),
                                                            )
                                                            .on_click(
                                                                cx.listener(Self::reject_run),
                                                            ),
                                                    )
                                                    .child(
                                                        Button::new("approve-approval")
                                                            .label("记录批准")
                                                            .primary()
                                                            .loading(self.is_busy())
                                                            .disabled(
                                                                !self.backend_is_ready()
                                                                    || self.is_busy(),
                                                            )
                                                            .on_click(
                                                                cx.listener(Self::approve_run),
                                                            ),
                                                    ),
                                            ),
                                    ),
                            )
                        },
                    )
                    .when_some(
                        self.approval_is_recorded()
                            .map(|(_, tool_name)| tool_name.to_owned()),
                        |this, tool_name| {
                            this.child(
                                div()
                                    .flex()
                                    .items_center()
                                    .justify_between()
                                    .p(px(18.0))
                                    .bg(rgb(0x163022))
                                    .child(
                                        div()
                                            .flex()
                                            .flex_col()
                                            .gap(px(4.0))
                                            .child(
                                                div()
                                                    .text_color(rgb(0x8ee6a8))
                                                    .child("APPROVAL RECORDED"),
                                            )
                                            .child(div().child(format!(
                                                "已批准 {tool_name}；需要再次确认才会继续运行。"
                                            ))),
                                    )
                                    .child(
                                        Button::new("resume-approved-run")
                                            .label("确认并继续")
                                            .primary()
                                            .loading(self.is_busy())
                                            .disabled(!self.backend_is_ready() || self.is_busy())
                                            .on_click(cx.listener(Self::resume_approved_run)),
                                    ),
                            )
                        },
                    ),
            )
    }
}

fn panel(title: &'static str, detail: &str) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .flex_1()
        .p(px(18.0))
        .gap(px(12.0))
        .bg(rgb(0x111a20))
        .child(div().text_color(rgb(0xffb000)).child(title))
        .child(div().child(detail.to_owned()))
}

fn trajectory_panel(detail: &str, entries: &[String]) -> impl IntoElement {
    let mut panel = div()
        .flex()
        .flex_col()
        .flex_1()
        .p(px(18.0))
        .gap(px(8.0))
        .bg(rgb(0x111a20))
        .child(div().text_color(rgb(0xffb000)).child("LIVE TRAJECTORY"))
        .child(div().text_color(rgb(0xaab6bc)).child(detail.to_owned()));

    if entries.is_empty() {
        panel = panel.child(div().child("尚无安全轨迹事件。"));
    } else {
        for entry in entries.iter().rev().take(3).rev() {
            panel = panel.child(div().text_size(px(12.0)).child(entry.clone()));
        }
    }
    panel
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
            |window, cx| cx.new(|cx| OpsMindConsole::new(window, cx)),
        )
        .expect("failed to open OpsMind desktop window");
    });
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{
        is_safe_run_id, normalize_approval_reason, normalize_diagnosis_query,
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

        assert_eq!(entry, "#4 · tool_finished · query_metrics · 18 ms");
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
}
