"""OpsMind 运维诊断工作台入口。"""

from __future__ import annotations

from uuid import uuid4

import streamlit as st

from frontend.client import BackendProtocolError, OpsMindApiClient, ServerSentEvent

st.set_page_config(
    page_title="OpsMind // Incident Console",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      :root { --amber: #ffb000; --ink: #0b1014; --panel: #111a20; --line: #26343c; }
      .stApp {
        background: radial-gradient(circle at 80% 0%, #1e2b29 0, transparent 29%), #0b1014;
        color: #e7ecea;
      }
      [data-testid="stSidebar"] {
        background: #0d151a;
        border-right: 1px solid #26343c;
      }
      .ops-label {
        color: #ffb000;
        font: 700 0.72rem ui-monospace, monospace;
        letter-spacing: .18em;
      }
      .ops-title {
        font: 800 clamp(2rem, 4vw, 4.8rem) ui-monospace, monospace;
        letter-spacing: -.08em;
        margin: .2rem 0;
      }
      .ops-subtitle { color: #91a2a5; max-width: 52rem; }
      .event-row {
        border-left: 2px solid #ffb000;
        background: #111a20;
        padding: .65rem .8rem;
        margin: .45rem 0;
      }
      .event-meta { color: #91a2a5; font: .75rem ui-monospace, monospace; }
      .report-card { border: 1px solid #3a5149; background: #101b1a; padding: 1rem 1.2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def initialize_state() -> None:
    """初始化当前浏览器会话的稳定标识和视图数据。"""
    st.session_state.setdefault("session_id", str(uuid4()))
    st.session_state.setdefault("thread_id", str(uuid4()))
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("final_result", None)


def event_label(event: ServerSentEvent) -> str:
    """生成时间线条目的简短可读标签。"""
    event_type = event.data.get("event_type", event.event)
    tool_name = event.data.get("tool_name")
    node = event.data.get("node")
    detail = tool_name or node or "Harness"
    return f"{event_type} · {detail}"


def render_timeline(events: list[ServerSentEvent]) -> None:
    """渲染安全事件时间线。"""
    if not events:
        st.caption("尚未收到运行事件。")
        return

    for event in events:
        if event.event in {"run_started", "run_finished", "run_failed"}:
            continue
        metadata = event.data
        st.markdown(
            "<div class='event-row'>"
            f"<strong>{event_label(event)}</strong><br>"
            f"<span class='event-meta'>step={metadata.get('step_id', '-')} "
            f"latency={metadata.get('latency_ms', '-')}ms</span>"
            "</div>",
            unsafe_allow_html=True,
        )


def run_diagnosis(*, api_url: str, user_query: str) -> None:
    """消费 SSE，并在单次 Streamlit 请求中逐步更新工作台。"""
    client = OpsMindApiClient(api_url)
    payload = {
        "session_id": st.session_state.session_id,
        "thread_id": st.session_state.thread_id,
        "user_query": user_query,
    }
    st.session_state.events = []
    st.session_state.final_result = None
    timeline_slot = st.empty()

    with st.status("Harness 正在执行诊断", expanded=True) as status_box:
        try:
            for event in client.stream_run(payload):
                st.session_state.events.append(event)
                if event.event == "run_finished":
                    st.session_state.final_result = event.data
                    status_box.update(label="诊断完成", state="complete", expanded=False)
                elif event.event == "run_failed":
                    status_box.update(label="诊断失败", state="error", expanded=True)
                timeline_slot.empty()
                with timeline_slot.container():
                    render_timeline(st.session_state.events)
        except (BackendProtocolError, ConnectionError) as error:
            status_box.update(label="无法完成诊断", state="error", expanded=True)
            st.error(str(error))


initialize_state()

with st.sidebar:
    st.markdown("<p class='ops-label'>CONNECTION</p>", unsafe_allow_html=True)
    api_url = st.text_input("API 地址", value="http://127.0.0.1:8000")
    st.markdown("<p class='ops-label'>SESSION</p>", unsafe_allow_html=True)
    st.code(st.session_state.session_id, language=None)
    st.markdown("<p class='ops-label'>SCENARIOS</p>", unsafe_allow_html=True)
    if st.button("刷新场景目录", use_container_width=True):
        try:
            st.session_state.scenarios = OpsMindApiClient(api_url).list_scenarios()
        except (BackendProtocolError, ConnectionError) as error:
            st.warning(str(error))

    scenarios = st.session_state.get("scenarios", [])
    if scenarios:
        labels = {item["scenario_id"]: item for item in scenarios}
        selected_id = st.selectbox("故障场景", options=list(labels), label_visibility="collapsed")
        selected = labels[selected_id]
        st.caption(
            f"{selected['service']} · {selected['log_count']} logs · "
            f"{selected['dependency_count']} dependencies"
        )

st.markdown("<p class='ops-label'>OPS MIND / HARNESS CONSOLE</p>", unsafe_allow_html=True)
st.markdown("<h1 class='ops-title'>INCIDENT<br>CONTROL ROOM</h1>", unsafe_allow_html=True)
st.markdown(
    "<p class='ops-subtitle'>将故障描述交给受控 Harness。工作台只显示安全轨迹字段，"
    "帮助定位模型、工具、审批与预算边界。</p>",
    unsafe_allow_html=True,
)

left_column, right_column = st.columns([1.15, 0.85], gap="large")
with left_column:
    with st.form("diagnosis-form"):
        user_query = st.text_area(
            "故障描述",
            placeholder="例如：支付服务错误率升高，P99 延迟超过告警阈值。",
            height=160,
        )
        submitted = st.form_submit_button("启动受控诊断", type="primary", use_container_width=True)
    if submitted:
        if user_query.strip():
            run_diagnosis(api_url=api_url, user_query=user_query.strip())
        else:
            st.warning("请输入故障描述。")

with right_column:
    st.markdown("<p class='ops-label'>LIVE TRAJECTORY</p>", unsafe_allow_html=True)
    render_timeline(st.session_state.events)
    final_result = st.session_state.final_result
    if final_result is not None:
        st.markdown("<p class='ops-label'>FINAL REPORT</p>", unsafe_allow_html=True)
        st.markdown("<div class='report-card'>", unsafe_allow_html=True)
        st.write(final_result.get("final_answer") or "运行未产生最终回答。")
        st.caption(f"状态：{final_result.get('status')} · 步骤：{final_result.get('step_count')}")
        st.markdown("</div>", unsafe_allow_html=True)
