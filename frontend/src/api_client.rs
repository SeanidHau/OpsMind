//! OpsMind FastAPI 的只读桌面端客户端。

use std::{fmt, time::Duration};

use serde::Deserialize;

const API_PREFIX: &str = "/api/v1";
const MAX_RESPONSE_BYTES: u64 = 512 * 1024;

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

    use super::OpsMindApiClient;

    fn serve_once(body: &str) -> (String, Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test server");
        let address = listener.local_addr().expect("test server address");
        let (sender, receiver) = mpsc::channel();
        let body = body.to_owned();

        thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept request");
            let mut request = [0_u8; 2048];
            let size = stream.read(&mut request).expect("read request");
            let _ = sender.send(String::from_utf8_lossy(&request[..size]).into_owned());
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
}
