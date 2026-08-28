//! FastAPI 安全 SSE 事件的纯 Rust 解析器。

use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub struct ServerSentEvent {
    pub name: String,
    pub data: Value,
}

/// 按网络分块增量解析 SSE 事件。
pub struct SseDecoder {
    pending_text: String,
    event_name: String,
    data_lines: Vec<String>,
}

impl SseDecoder {
    pub fn new() -> Self {
        Self {
            pending_text: String::new(),
            event_name: String::from("message"),
            data_lines: Vec::new(),
        }
    }

    /// 接收一个 UTF-8 文本分块，并返回其中已经结束的事件。
    pub fn push(&mut self, chunk: &str) -> Result<Vec<ServerSentEvent>, String> {
        self.pending_text.push_str(chunk);
        let mut events = Vec::new();

        while let Some(newline_index) = self.pending_text.find('\n') {
            let mut line: String = self.pending_text.drain(..=newline_index).collect();
            line.pop();
            if line.ends_with('\r') {
                line.pop();
            }
            self.consume_line(&line, &mut events)?;
        }

        Ok(events)
    }

    /// 在 HTTP 连接结束时刷新未以换行结尾的最后一个事件。
    pub fn finish(mut self) -> Result<Vec<ServerSentEvent>, String> {
        let mut events = Vec::new();
        if !self.pending_text.is_empty() {
            let mut line = std::mem::take(&mut self.pending_text);
            if line.ends_with('\r') {
                line.pop();
            }
            self.consume_line(&line, &mut events)?;
        }
        self.flush(&mut events)?;
        Ok(events)
    }

    fn consume_line(
        &mut self,
        line: &str,
        events: &mut Vec<ServerSentEvent>,
    ) -> Result<(), String> {
        if line.is_empty() {
            self.flush(events)?;
            self.event_name = String::from("message");
        } else if line.starts_with(':') {
            // SSE 注释行不属于事件数据。
        } else if let Some(name) = line.strip_prefix("event:") {
            self.event_name = name.trim().to_owned();
        } else if let Some(data) = line.strip_prefix("data:") {
            self.data_lines.push(data.trim_start().to_owned());
        }
        Ok(())
    }

    fn flush(&mut self, events: &mut Vec<ServerSentEvent>) -> Result<(), String> {
        if self.data_lines.is_empty() {
            return Ok(());
        }

        let data: Value = serde_json::from_str(&self.data_lines.join("\n"))
            .map_err(|error| format!("SSE data must be valid JSON: {error}"))?;
        if !data.is_object() {
            return Err(String::from("SSE data must be a JSON object"));
        }
        events.push(ServerSentEvent {
            name: self.event_name.clone(),
            data,
        });
        self.data_lines.clear();
        Ok(())
    }
}

/// 解析 `text/event-stream` 负载中的事件。
///
/// 只接受对象形式的 JSON 数据，避免桌面端把非结构化或意外响应写入运行时间线。
#[cfg(test)]
pub fn parse_events(payload: &str) -> Result<Vec<ServerSentEvent>, String> {
    let mut decoder = SseDecoder::new();
    let mut events = decoder.push(payload)?;
    events.extend(decoder.finish()?);
    Ok(events)
}

#[cfg(test)]
mod tests {
    use super::{SseDecoder, parse_events};

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

    #[test]
    fn emits_events_when_a_network_chunk_ends_an_event() {
        let mut decoder = SseDecoder::new();

        assert!(
            decoder
                .push("event: run_started\ndata: {\"run_id\":\"")
                .expect("partial event")
                .is_empty()
        );
        let events = decoder
            .push("run-1\"}\n\n")
            .expect("completed event in second chunk");

        assert_eq!(events[0].name, "run_started");
        assert_eq!(events[0].data["run_id"], "run-1");
    }
}
