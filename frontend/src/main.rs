//! OpsMind 的 GPUI 桌面控制台入口。

use std::env;

/// 在后续阶段由 GPUI 网络适配器调用的安全 SSE 解析器。
#[allow(dead_code)]
mod sse;

/// 负责读取后端健康状态和场景摘要的只读客户端。
#[allow(dead_code)]
mod api_client;

use gpui::{
    App, Application, Bounds, Context, IntoElement, Render, Window, WindowBounds, WindowOptions,
    div, prelude::*, px, rgb, size,
};

use crate::api_client::OpsMindApiClient;

const DEFAULT_API_BASE_URL: &str = "http://127.0.0.1:8000";

struct OpsMindConsole {
    api_base_url: String,
    connection: ConnectionState,
    catalog: CatalogState,
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

struct BootstrapSnapshot {
    version: String,
    scenario_count: usize,
}

impl OpsMindConsole {
    fn new(cx: &mut Context<Self>) -> Self {
        let mut console = Self {
            api_base_url: env::var("OPSMIND_API_BASE_URL")
                .unwrap_or_else(|_| String::from(DEFAULT_API_BASE_URL)),
            connection: ConnectionState::Checking,
            catalog: CatalogState::Waiting,
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
    fn render(&mut self, _window: &mut Window, _cx: &mut Context<Self>) -> impl IntoElement {
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
                    .p(px(24.0))
                    .gap(px(16.0))
                    .child(panel("CONNECTION", &self.connection_detail()))
                    .child(panel("SCENARIO CATALOG", &self.catalog_detail()))
                    .child(panel(
                        "DIAGNOSIS",
                        "输入、SSE 流与审批交互将在后续阶段接入。",
                    ))
                    .child(panel("LIVE TRAJECTORY", "等待诊断运行事件。")),
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
        let bounds = Bounds::centered(None, size(px(1280.0), px(760.0)), cx);
        cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                ..Default::default()
            },
            |_, cx| cx.new(OpsMindConsole::new),
        )
        .expect("failed to open OpsMind desktop window");
    });
}

#[cfg(test)]
mod tests {
    use super::{BootstrapSnapshot, CatalogState, ConnectionState, OpsMindConsole};

    fn console() -> OpsMindConsole {
        OpsMindConsole {
            api_base_url: String::from("http://127.0.0.1:8000"),
            connection: ConnectionState::Checking,
            catalog: CatalogState::Waiting,
        }
    }

    #[test]
    fn bootstrap_success_updates_connection_and_catalog() {
        let mut console = console();

        console.apply_bootstrap_snapshot(Ok(BootstrapSnapshot {
            version: String::from("0.1.0"),
            scenario_count: 3,
        }));

        assert_eq!(console.connection_detail(), "已连接 · API 0.1.0");
        assert_eq!(console.catalog_detail(), "已加载 3 个安全场景摘要");
    }

    #[test]
    fn bootstrap_failure_does_not_display_transport_details() {
        let mut console = console();

        console.apply_bootstrap_snapshot(Err(()));

        assert_eq!(
            console.connection_detail(),
            "服务不可用 · 检查后端是否已启动"
        );
        assert_eq!(console.catalog_detail(), "连接恢复后将重新读取场景目录");
    }
}
