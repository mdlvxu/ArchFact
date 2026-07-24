# Groq 免费 Qwen3.6-27B 接入

ArchFact 通过 Groq 的 OpenAI 兼容接口调用 `qwen/qwen3.6-27b`，用于把逐页 OCR 文本整理为结构化器物记录。

## 创建 Key

1. 登录 `https://console.groq.com/`。
2. 打开 `https://console.groq.com/keys`。
3. 创建一个名为 `archfact-backend-dev` 的 API Key。
4. 只把 Key 写入后端 `.env`，不要放入 Vue、Git 或聊天消息。

## 配置

先填写 Groq Key：

```dotenv
EXTRACTION_ENGINE=local
LLM_PROVIDER=groq
LLM_API_BASE=https://api.groq.com/openai/v1
LLM_API_KEY=你的_Groq_Key
LLM_MODEL=qwen/qwen3.6-27b
LLM_THINKING_ENABLED=false
LLM_TIMEOUT_SECONDS=180
LLM_MAX_RETRIES=2
LLM_MAX_TOKENS=4096
```

完成最小连通性测试后，再将 `EXTRACTION_ENGINE` 改为 `llm` 并重启 FastAPI。

## 免费额度边界

免费计划存在每分钟请求数、每日请求数、每分钟 Token 和每日 Token 限制。后端会对 HTTP 429 和 5xx 响应自动重试，并遵循数值形式的 `Retry-After` 响应头，最长等待 30 秒。

完整 PDF 仍按页调用。若免费额度耗尽，任务会保留已完成页并以警告状态结束，不会生成伪造数据。生产环境应配置本地 Ollama 或其他模型作为回退通道。
