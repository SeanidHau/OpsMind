//! FastAPI 安全 SSE 事件的纯 Rust 解析器。

use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub struct ServerSentEvent {
    pub name: String,
    pub data: Value,
}

/// 解析 `text/event-stream` 负载中的事件。
///
/// 只接受对象形式的 JSON 数据，避免桌面端把非结构化或意外响应写入运行时间线。
pub fn parse_events(payload: &str) -> Result<Vec<ServerSentEvent>, String> {
    let mut events = Vec::new();
    let mut event_name = String::from("message");
    let mut data_lines = Vec::new();

    let flush = |events: &mut Vec<ServerSentEvent>,
                 event_name: &str,
                 data_lines: &mut Vec<String>|
     -> Result<(), String> {
        if data_lines.is_empty() {
            return Ok(());
        }

        let data: Value = serde_json::from_str(&data_lines.join("\n"))
            .map_err(|error| format!("SSE data must be valid JSON: {error}"))?;
        if !data.is_object() {
            return Err(String::from("SSE data must be a JSON object"));
        }
        events.push(ServerSentEvent {
            name: event_name.into(),
            data,
        });
        data_lines.clear();
        Ok(())
    };

    for line in payload.lines() {
        if line.is_empty() {
            flush(&mut events, &event_name, &mut data_lines)?;
            event_name = String::from("message");
        } else if line.starts_with(':') {
            continue;
        } else if let Some(name) = line.strip_prefix("event:") {
            event_name = name.trim().to_owned();
        } else if let Some(data) = line.strip_prefix("data:") {
            data_lines.push(data.trim_start().to_owned());
        }
    }
    flush(&mut events, &event_name, &mut data_lines)?;

    Ok(events)
}

#[cfg(test)]
mod tests {
    use super::parse_events;

    #[test]
    fn parses_ordered_json_events() {
        let events = parse_events(concat!(
            "event: run_started\n",
            "data: {\"run_id\":\"run-1\"}\n\n",
            "event: tool_finished\n",
            "data: {\"event_type\":\"tool_finished\"}\n\n",
        ))
        .expect("valid SSE payload");

        assert_eq!(events.len(), 2);
        assert_eq!(events[0].name, "run_started");
        assert_eq!(events[1].data["event_type"], "tool_finished");
    }

    #[test]
    fn rejects_non_json_event_data() {
        let error = parse_events("event: run_started\ndata: not-json\n\n")
            .expect_err("invalid data must be rejected");

        assert!(error.contains("valid JSON"));
    }
}
