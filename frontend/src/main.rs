//! OpsMind 的 GPUI 桌面控制台入口。

use std::env;

/// 在后续阶段由 GPUI 网络适配器调用的安全 SSE 解析器。
#[allow(dead_code)]
mod sse;

/// 负责读取后端健康状态和场景摘要的只读客户端。
#[allow(dead_code)]
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

use crate::api_client::OpsMindApiClient;

const DEFAULT_API_BASE_URL: &str = "http://127.0.0.1:8000";

struct OpsMindConsole {
    api_base_url: String,
    connection: ConnectionState,
    catalog: CatalogState,
    diagnosis_input: Entity<InputState>,
    submission: SubmissionState,
    _input_subscription: Subscription,
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
                    console.prepare_diagnosis(input.read(cx).value().to_string(), cx);
                }
                InputEvent::PressEnter { secondary: false }
                | InputEvent::Focus
                | InputEvent::Blur => {}
            });
        let mut console = Self {
            api_base_url: env::var("OPSMIND_API_BASE_URL")
                .unwrap_or_else(|_| String::from(DEFAULT_API_BASE_URL)),
            connection: ConnectionState::Checking,
            catalog: CatalogState::Waiting,
            diagnosis_input,
            submission: SubmissionState::Draft,
            _input_subscription: input_subscription,
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
        self.prepare_diagnosis(self.diagnosis_input.read(cx).value().to_string(), cx);
    }

    /// 保存已校验的诊断意图，等待下一阶段的 SSE 传输层发送。
    fn prepare_diagnosis(&mut self, raw_query: String, cx: &mut Context<Self>) {
        self.submission = match normalize_diagnosis_query(&raw_query) {
            Ok(query) => SubmissionState::Prepared { query },
            Err(()) => SubmissionState::Invalid,
        };
        cx.notify();
    }

    fn submission_detail(&self) -> String {
        match &self.submission {
            SubmissionState::Draft => String::from("填写描述后按 ⌘↵ 准备诊断。"),
            SubmissionState::Invalid => String::from("诊断描述不能为空，且不得超过 4000 个字符。"),
            SubmissionState::Prepared { query } => {
                format!(
                    "已准备 {} 个字符的诊断描述，等待启动流式运行。",
                    query.chars().count()
                )
            }
        }
    }

    fn backend_is_ready(&self) -> bool {
        matches!(self.connection, ConnectionState::Ready { .. })
    }
}

fn normalize_diagnosis_query(raw_query: &str) -> Result<String, ()> {
    let query = raw_query.trim();
    if query.is_empty() || query.chars().count() > 4_000 {
        return Err(());
    }
    Ok(query.to_owned())
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
                            .child(panel("LIVE TRAJECTORY", "等待诊断运行事件。")),
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
                                            .label("准备诊断")
                                            .primary()
                                            .disabled(!self.backend_is_ready())
                                            .on_click(cx.listener(Self::submit_diagnosis)),
                                    ),
                            ),
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
    use super::normalize_diagnosis_query;

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
}
