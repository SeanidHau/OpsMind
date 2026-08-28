//! OpsMind FastAPI 的桌面端客户端。

use std::{fmt, io::Read, time::Duration};

use serde::{Deserialize, Serialize};

use crate::sse::{ServerSentEvent, SseDecoder};

const API_PREFIX: &str = "/api/v1";
const MAX_RESPONSE_BYTES: u64 = 512 * 1024;
const MAX_STREAM_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HealthStatus {
    pub version: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScenarioSummary {
    pub scenario_id: String,
    pub service: String,
    pub log_count: u32,
    pub metric_names: Vec<String>,
    pub dependency_count: u32,
}

/// 创建一次实时诊断运行所需的公开请求字段。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StreamDiagnosisRequest {
    pub session_id: String,
    pub thread_id: String,
    pub user_query: String,
}

/// 恢复等待用户输入的运行所需的公开请求字段。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ResumeDiagnosisRequest {
    pub answer: String,
}

/// 等待审批时可公开展示的最小摘要。
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PendingApprovalSummary {
    pub tool_name: String,
    pub reason: String,
}

/// 高风险工具的人工审批决议。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ApprovalRequest {
    pub decision: String,
    pub reason: String,
}

/// 运行接口返回的最小安全摘要。
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosisRunSummary {
    pub run_id: String,
    pub status: Option<String>,
    pub step_count: usize,
    pub final_answer: Option<String>,
    pub pending_question: Option<String>,
    pub pending_approval: Option<PendingApprovalSummary>,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApiClientError {
    Request(String),
    InvalidResponse(String),
}

impl fmt::Display for ApiClientError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Request(message) => write!(formatter, "API request failed: {message}"),
            Self::InvalidResponse(message) => {
                write!(formatter, "API response is invalid: {message}")
            }
        }
    }
}

impl std::error::Error for ApiClientError {}

/// 访问本机或已配置 OpsMind API 服务的客户端。
pub struct OpsMindApiClient {
    base_url: String,
    agent: ureq::Agent,
}

impl OpsMindApiClient {
    pub fn new(base_url: impl Into<String>) -> Self {
        let config = ureq::Agent::config_builder()
            .timeout_global(Some(Duration::from_secs(5)))
            .build();

        Self {
            base_url: base_url.into().trim_end_matches('/').to_owned(),
            agent: config.into(),
        }
    }

    /// 验证目标服务确实是 OpsMind，并返回其 API 版本。
    pub fn health(&self) -> Result<HealthStatus, ApiClientError> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct HealthResponse {
            status: String,
            service: String,
            version: String,
        }

        let response: HealthResponse = self.get_json("/health")?;
        if response.status != "ok"
            || response.service != "opsmind"
            || response.version.trim().is_empty()
        {
            return Err(ApiClientError::InvalidResponse(String::from(
                "health response does not identify a healthy OpsMind service",
            )));
        }

        Ok(HealthStatus {
            version: response.version,
        })
    }

    /// 读取可公开展示的场景摘要，不请求任何原始诊断证据。
    pub fn scenarios(&self) -> Result<Vec<ScenarioSummary>, ApiClientError> {
        self.get_json("/scenarios")
    }

    /// 创建诊断运行，并在读取到每个完整 SSE 事件时立即回调。
    pub fn stream_diagnosis(
        &self,
        request: &StreamDiagnosisRequest,
        mut on_event: impl FnMut(ServerSentEvent),
    ) -> Result<(), ApiClientError> {
        let body = serde_json::to_string(request)
            .map_err(|error| ApiClientError::InvalidResponse(error.to_string()))?;
        let url = format!("{}{API_PREFIX}/runs/stream", self.base_url);
        let mut response = self
            .agent
            .post(&url)
            .header("Content-Type", "application/json")
            .header("Accept", "text/event-stream")
            .send(&body)
            .map_err(|error| ApiClientError::Request(error.to_string()))?;
        let mut reader = response.body_mut().as_reader();
        let mut decoder = SseDecoder::new();
        let mut buffer = [0_u8; 4096];
        let mut received_bytes = 0_usize;

        loop {
            let size = reader
                .read(&mut buffer)
                .map_err(|error| ApiClientError::Request(error.to_string()))?;
            if size == 0 {
                break;
            }
            received_bytes += size;
            if received_bytes > MAX_STREAM_BYTES {
                return Err(ApiClientError::InvalidResponse(String::from(
                    "SSE response exceeded the maximum allowed size",
                )));
            }

            let chunk = std::str::from_utf8(&buffer[..size])
                .map_err(|error| ApiClientError::InvalidResponse(error.to_string()))?;
            for event in decoder
                .push(chunk)
                .map_err(ApiClientError::InvalidResponse)?
            {
                on_event(event);
            }
        }

        for event in decoder.finish().map_err(ApiClientError::InvalidResponse)? {
            on_event(event);
        }
        Ok(())
    }

    /// 提交一条用户补充信息，并恢复同一条已暂停的诊断运行。
    pub fn resume_with_user_input(
        &self,
        run_id: &str,
        request: &ResumeDiagnosisRequest,
    ) -> Result<DiagnosisRunSummary, ApiClientError> {
        self.post_json(&format!("/runs/{run_id}/input"), request)
    }

    /// 记录高风险工具的审批决议，不在当前请求执行工具。
    pub fn resolve_approval(
        &self,
        run_id: &str,
        request: &ApprovalRequest,
    ) -> Result<DiagnosisRunSummary, ApiClientError> {
        self.post_json(&format!("/runs/{run_id}/approval"), request)
    }

    /// 从已记录的批准决议恢复同一条诊断运行。
    pub fn resume_approved(&self, run_id: &str) -> Result<DiagnosisRunSummary, ApiClientError> {
        let url = format!(
            "{}{API_PREFIX}/runs/{run_id}/approval/resume",
            self.base_url
        );
        let mut response = self
            .agent
            .post(&url)
            .header("Accept", "application/json")
            .send_empty()
            .map_err(|error| ApiClientError::Request(error.to_string()))?;
        let response_body = response
            .body_mut()
            .with_config()
            .limit(MAX_RESPONSE_BYTES)
            .read_to_string()
            .map_err(|error| ApiClientError::Request(error.to_string()))?;

        serde_json::from_str(&response_body)
            .map_err(|error| ApiClientError::InvalidResponse(error.to_string()))
    }

    fn post_json<T>(&self, path: &str, request: &impl Serialize) -> Result<T, ApiClientError>
    where
        T: for<'de> Deserialize<'de>,
    {
        let body = serde_json::to_string(request)
            .map_err(|error| ApiClientError::InvalidResponse(error.to_string()))?;
        let url = format!("{}{API_PREFIX}{path}", self.base_url);
        let mut response = self
            .agent
            .post(&url)
            .header("Content-Type", "application/json")
            .header("Accept", "application/json")
            .send(&body)
            .map_err(|error| ApiClientError::Request(error.to_string()))?;
        let response_body = response
            .body_mut()
            .with_config()
            .limit(MAX_RESPONSE_BYTES)
            .read_to_string()
            .map_err(|error| ApiClientError::Request(error.to_string()))?;

        serde_json::from_str(&response_body)
            .map_err(|error| ApiClientError::InvalidResponse(error.to_string()))
    }

    fn get_json<T>(&self, path: &str) -> Result<T, ApiClientError>
    where
        T: for<'de> Deserialize<'de>,
    {
        let url = format!("{}{API_PREFIX}{path}", self.base_url);
        let mut response = self
            .agent
            .get(&url)
            .call()
            .map_err(|error| ApiClientError::Request(error.to_string()))?;
        let body = response
            .body_mut()
            .with_config()
            .limit(MAX_RESPONSE_BYTES)
            .read_to_string()
            .map_err(|error| ApiClientError::Request(error.to_string()))?;

        serde_json::from_str(&body)
            .map_err(|error| ApiClientError::InvalidResponse(error.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::mpsc::{self, Receiver},
        thread,
    };

    use super::{
        ApprovalRequest, OpsMindApiClient, ResumeDiagnosisRequest, StreamDiagnosisRequest,
    };

    fn serve_once(body: &str) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test server");
        let address = listener.local_addr().expect("test server address");
        let (sender, receiver) = mpsc::channel();
        let body = body.to_owned();

        thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept request");
            let _ = sender.send(read_http_request(&mut stream));
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len(),
            );
            stream
                .write_all(response.as_bytes())
                .expect("write response");
        });

        (format!("http://{address}"), receiver)
    }

    fn read_http_request(stream: &mut std::net::TcpStream) -> String {
        let mut request = Vec::new();
        let mut buffer = [0_u8; 2048];

        loop {
            let size = stream.read(&mut buffer).expect("read request");
            request.extend_from_slice(&buffer[..size]);
            let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
            else {
                continue;
            };
            let headers = String::from_utf8_lossy(&request[..header_end]);
            let content_length = headers
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then_some(value.trim())
                })
                .and_then(|value| value.parse::<usize>().ok())
                .unwrap_or(0);
            if request.len() >= header_end + 4 + content_length {
                return String::from_utf8_lossy(&request).into_owned();
            }
        }
    }

    #[test]
    fn reads_healthy_opsmind_service() {
        let (base_url, requests) =
            serve_once(r#"{"status":"ok","service":"opsmind","version":"0.1.0"}"#);

        let health = OpsMindApiClient::new(base_url)
            .health()
            .expect("healthy API");

        assert_eq!(health.version, "0.1.0");
        assert!(
            requests
                .recv()
                .expect("captured request")
                .starts_with("GET /api/v1/health HTTP/1.1")
        );
    }

    #[test]
    fn reads_only_scenario_summaries() {
        let (base_url, requests) = serve_once(
            r#"[{"scenario_id":"checkout-latency","service":"checkout","log_count":2,"metric_names":["latency_p95"],"dependency_count":1}]"#,
        );

        let scenarios = OpsMindApiClient::new(base_url)
            .scenarios()
            .expect("scenario catalog");

        assert_eq!(scenarios[0].service, "checkout");
        assert_eq!(scenarios[0].metric_names, ["latency_p95"]);
        assert!(
            requests
                .recv()
                .expect("captured request")
                .starts_with("GET /api/v1/scenarios HTTP/1.1")
        );
    }

    #[test]
    fn rejects_a_response_from_another_service() {
        let (base_url, _) = serve_once(r#"{"status":"ok","service":"other","version":"0.1.0"}"#);

        let error = OpsMindApiClient::new(base_url)
            .health()
            .expect_err("wrong service must be rejected");

        assert!(error.to_string().contains("does not identify"));
    }

    #[test]
    fn posts_a_diagnosis_request_and_streams_safe_events() {
        let (base_url, requests) = serve_once(concat!(
            "event: run_started\n",
            "data: {\"run_id\":\"run-1\"}\n\n",
            "event: run_finished\n",
            "data: {\"run_id\":\"run-1\",\"status\":\"completed\"}\n\n",
        ));
        let mut event_names = Vec::new();

        OpsMindApiClient::new(base_url)
            .stream_diagnosis(
                &StreamDiagnosisRequest {
                    session_id: String::from("desktop-session"),
                    thread_id: String::from("desktop-thread"),
                    user_query: String::from("checkout latency increased"),
                },
                |event| event_names.push(event.name),
            )
            .expect("stream diagnosis");

        assert_eq!(event_names, ["run_started", "run_finished"]);
        let request = requests.recv().expect("captured request");
        assert!(request.starts_with("POST /api/v1/runs/stream HTTP/1.1"));
        assert!(
            request.contains("\"user_query\":\"checkout latency increased\""),
            "unexpected request: {request}"
        );
    }

    #[test]
    fn posts_operator_input_to_resume_the_existing_run() {
        let (base_url, requests) = serve_once(
            r#"{"run_id":"018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1","status":"completed","step_count":3,"final_answer":"safe summary","pending_question":null,"pending_approval":null,"errors":[]}"#,
        );

        let summary = OpsMindApiClient::new(base_url)
            .resume_with_user_input(
                "018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1",
                &ResumeDiagnosisRequest {
                    answer: String::from("影响范围是支付服务。"),
                },
            )
            .expect("resume response");

        assert_eq!(summary.status.as_deref(), Some("completed"));
        let request = requests.recv().expect("captured request");
        assert!(
            request.starts_with(
                "POST /api/v1/runs/018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1/input HTTP/1.1"
            )
        );
        assert!(request.contains("\"answer\":\"影响范围是支付服务。\""));
    }

    #[test]
    fn records_approval_without_resuming_the_run() {
        let (base_url, requests) = serve_once(
            r#"{"run_id":"018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1","status":null,"step_count":1,"final_answer":null,"pending_question":null,"pending_approval":null,"errors":[]}"#,
        );

        OpsMindApiClient::new(base_url)
            .resolve_approval(
                "018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1",
                &ApprovalRequest {
                    decision: String::from("approve"),
                    reason: String::from("维护窗口已确认。"),
                },
            )
            .expect("approval response");

        let request = requests.recv().expect("captured request");
        assert!(request.starts_with(
            "POST /api/v1/runs/018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1/approval HTTP/1.1"
        ));
        assert!(request.contains("\"decision\":\"approve\""));
        assert!(!request.contains("/approval/resume"));
    }

    #[test]
    fn resumes_only_after_an_explicit_approval_resume_request() {
        let (base_url, requests) = serve_once(
            r#"{"run_id":"018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1","status":"completed","step_count":2,"final_answer":"safe summary","pending_question":null,"pending_approval":null,"errors":[]}"#,
        );

        OpsMindApiClient::new(base_url)
            .resume_approved("018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1")
            .expect("approved resume response");

        assert!(requests.recv().expect("captured request").starts_with(
            "POST /api/v1/runs/018f4d1d-4d5d-7fe0-a7c4-a481c9d0f1c1/approval/resume HTTP/1.1"
        ));
    }
}
