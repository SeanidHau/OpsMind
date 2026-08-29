//! OpsMind FastAPI 的桌面端客户端。

use std::{fmt, io::Read, time::Duration};

use serde::{Deserialize, Serialize};

use crate::sse::{ServerSentEvent, SseDecoder};

const API_PREFIX: &str = "/api/v1";
const MAX_RESPONSE_BYTES: u64 = 512 * 1024;
const MAX_STREAM_BYTES: usize = 1024 * 1024;
const STREAM_TIMEOUT: Duration = Duration::from_secs(130);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HealthStatus {
    pub version: String,
}

/// 工作台知识库页面展示的最小文档信息。
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeDocumentSummary {
    pub document_id: String,
    pub title: String,
    pub chunk_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeDocument {
    pub document_id: String,
    pub title: String,
    pub content: String,
}

/// 知识库目录的安全摘要，不包含正文或向量内容。
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KnowledgeCatalog {
    pub document_count: usize,
    pub chunk_count: usize,
    pub documents: Vec<KnowledgeDocumentSummary>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CreateKnowledgeDocumentRequest {
    pub title: String,
    pub content: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosisRunHistoryItem {
    pub run_id: String,
    pub status: Option<String>,
    pub step_count: usize,
    pub query: String,
    pub captured_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosisRunHistory {
    pub runs: Vec<DiagnosisRunHistoryItem>,
}

/// MCP 连接中单个系统的安全展示信息；令牌值永不返回桌面端。
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct McpServiceConfiguration {
    pub url: Option<String>,
    pub token_configured: bool,
}

/// 内置 MCP Server 的本机连接摘要。
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct McpConfiguration {
    pub enabled: bool,
    pub command: String,
    pub arguments: String,
    pub prometheus: McpServiceConfiguration,
    pub loki: McpServiceConfiguration,
    pub jaeger: McpServiceConfiguration,
    pub kubernetes: McpServiceConfiguration,
    pub cmdb: McpServiceConfiguration,
}

/// 保存 MCP 配置时提交的本机端点与可选只读令牌。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct McpConfigurationUpdate {
    pub enabled: bool,
    pub command: String,
    pub arguments: String,
    pub prometheus_url: String,
    pub prometheus_bearer_token: String,
    pub loki_url: String,
    pub loki_bearer_token: String,
    pub jaeger_url: String,
    pub jaeger_bearer_token: String,
    pub kubernetes_url: String,
    pub kubernetes_bearer_token: String,
    pub cmdb_url: String,
    pub cmdb_bearer_token: String,
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

    /// 读取知识库目录，仅用于展示已加载的文档概览。
    pub fn knowledge_catalog(&self) -> Result<KnowledgeCatalog, ApiClientError> {
        self.get_json("/knowledge")
    }

    pub fn create_knowledge_document(
        &self,
        request: &CreateKnowledgeDocumentRequest,
    ) -> Result<KnowledgeCatalog, ApiClientError> {
        self.post_json("/knowledge", request)
    }

    pub fn knowledge_document(
        &self,
        document_id: &str,
    ) -> Result<KnowledgeDocument, ApiClientError> {
        self.get_json(&format!("/knowledge/{document_id}"))
    }

    pub fn run_history(&self) -> Result<DiagnosisRunHistory, ApiClientError> {
        self.get_json("/runs")
    }

    /// 读取内置 MCP Server 的本机连接配置，不返回任何令牌内容。
    pub fn mcp_configuration(&self) -> Result<McpConfiguration, ApiClientError> {
        self.get_json("/mcp")
    }

    /// 保存 MCP 连接配置并让后续诊断使用更新后的工具目录。
    pub fn update_mcp_configuration(
        &self,
        request: &McpConfigurationUpdate,
    ) -> Result<McpConfiguration, ApiClientError> {
        self.put_json("/mcp", request)
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
        // 模型调用允许占用完整的 Harness 运行时预算；普通请求仍保持 5 秒超时。
        let stream_agent: ureq::Agent = ureq::Agent::config_builder()
            .timeout_global(Some(STREAM_TIMEOUT))
            .build()
            .into();
        let mut response = stream_agent
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

    fn put_json<T>(&self, path: &str, request: &impl Serialize) -> Result<T, ApiClientError>
    where
        T: for<'de> Deserialize<'de>,
    {
        let body = serde_json::to_string(request)
            .map_err(|error| ApiClientError::InvalidResponse(error.to_string()))?;
        let url = format!("{}{API_PREFIX}{path}", self.base_url);
        let mut response = self
            .agent
            .put(&url)
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
        time::Duration,
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

    fn serve_stream_with_delay(
        initial_body: &str,
        delayed_body: &str,
        delay: Duration,
    ) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test server");
        let address = listener.local_addr().expect("test server address");
        let (sender, receiver) = mpsc::channel();
        let initial_body = initial_body.to_owned();
        let delayed_body = delayed_body.to_owned();

        thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept request");
            let _ = sender.send(read_http_request(&mut stream));
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{initial_body}",
                initial_body.len() + delayed_body.len(),
            );
            stream
                .write_all(response.as_bytes())
                .expect("write initial stream");
            stream.flush().expect("flush initial stream");
            thread::sleep(delay);
            stream
                .write_all(delayed_body.as_bytes())
                .expect("write delayed stream");
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
    fn reads_knowledge_catalog_without_document_content() {
        let (base_url, requests) = serve_once(
            r#"{"document_count":1,"chunk_count":2,"documents":[{"document_id":"payment-runbook","title":"支付处理手册","chunk_count":2}]}"#,
        );

        let catalog = OpsMindApiClient::new(base_url)
            .knowledge_catalog()
            .expect("knowledge catalog");

        assert_eq!(catalog.document_count, 1);
        assert_eq!(catalog.documents[0].document_id, "payment-runbook");
        assert_eq!(catalog.documents[0].title, "支付处理手册");
        assert!(
            requests
                .recv()
                .expect("captured request")
                .starts_with("GET /api/v1/knowledge HTTP/1.1")
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
    fn keeps_the_diagnosis_stream_open_for_a_model_response() {
        let (base_url, _) = serve_stream_with_delay(
            concat!("event: run_started\n", "data: {\"run_id\":\"run-1\"}\n\n",),
            concat!(
                "event: model_called\n",
                "data: {\"event_type\":\"model_called\"}\n\n",
            ),
            Duration::from_secs(6),
        );
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
            .expect("stream remains open past the ordinary request timeout");

        assert_eq!(event_names, ["run_started", "model_called"]);
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
