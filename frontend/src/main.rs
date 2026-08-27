//! OpsMind 的 GPUI 桌面控制台入口。

use gpui::{
    App, Application, Bounds, Context, IntoElement, Render, Window, WindowBounds, WindowOptions,
    div, prelude::*, px, rgb, size,
};

struct OpsMindConsole;

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
                    .child(div().text_color(rgb(0xffb000)).child("HARNESS ONLINE")),
            )
            .child(
                div()
                    .flex()
                    .flex_1()
                    .p(px(24.0))
                    .gap(px(16.0))
                    .child(panel("CONNECTION", "FastAPI  ·  http://127.0.0.1:8000"))
                    .child(panel(
                        "DIAGNOSIS",
                        "输入、SSE 流与审批交互将在后续阶段接入。",
                    ))
                    .child(panel("LIVE TRAJECTORY", "等待运行事件。")),
            )
    }
}

fn panel(title: &'static str, detail: &'static str) -> impl IntoElement {
    div()
        .flex()
        .flex_col()
        .flex_1()
        .p(px(18.0))
        .gap(px(12.0))
        .bg(rgb(0x111a20))
        .child(div().text_color(rgb(0xffb000)).child(title))
        .child(div().child(detail))
}

fn main() {
    Application::new().run(|cx: &mut App| {
        let bounds = Bounds::centered(None, size(px(1280.0), px(760.0)), cx);
        cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                ..Default::default()
            },
            |_, cx| cx.new(|_| OpsMindConsole),
        )
        .expect("failed to open OpsMind desktop window");
    });
}
