# 灵动笔卡 (NoteMind AI)

> 把 Markdown 课程笔记，变成会提问的智能复习卡片。

`note_to_cards` 是一款面向在校大学生与备考学生的 AI 学习工具：把用户粘贴的 Markdown 课程笔记，通过大模型自动提炼为**结构化复习提纲**与**自测刷题卡片**（单选题 / 问答题），支持 3D 翻转查看答案、即时对错反馈、**错题本**与**进度持久化**，帮助学生完成"生成 → 自测 → 重练错题"的复习闭环。界面采用**玻璃拟态 + 弥散光晕 + 渐变描边**的极简亮色美学。

## 功能特性

### 核心功能

- 顶部大文本框粘贴 Markdown 笔记，一键生成复习内容
- 左侧树状**结构化复习提纲**（章节 / 要点，可递归嵌套）
- 右侧**自测刷题卡片**：正面题目与选项，背面答案与解析

### 交互体验

- 卡片 3D 翻转查看答案（点击卡片或按 `空格`）
- 答题即时反馈：点击选项先给 0.8 秒对错反馈（正确浅绿、错误浅红并高亮正确项），再平滑翻转显示解析
- 键盘全流程可用：`←` `→` 切换题目、`空格` 翻转、`A`~`F` / `1`~`9` 快速作答
- **错题本**：答错的题自动收录，一键"只练错题"
- **进度持久化**：题目、作答记录与错题通过 `localStorage` 保存，刷新不丢
- **导出**：Markdown（含提纲与答案）与 CSV（Anki / Excel 可直接导入）
- **打乱顺序**：一键重排题目与选项，避免"记住答案位置"造成的假掌握

### AI 能力

- FastAPI 后端接收 Markdown，用预设 Prompt 调用大模型生成结构化 JSON
- 兼容 OpenAI / DeepSeek / 通义千问 / Moonshot / 智谱 GLM 等 OpenAI 格式接口
- **结构化校验**：对模型返回做递归字段清洗，丢弃答案与选项对不齐的题，绝不给用户一道无法答对的题
- **透明降级**：未配置模型或调用失败时返回内置示例数据，并通过 `fallback_reason` 告诉前端具体原因（超时 / 鉴权失败 / 输出被截断 / 解析失败），前端会如实提示
- **结果缓存**：同一篇笔记 30 分钟内重复生成直接命中缓存，不重复消耗额度
- **上游重试**：遇到 429 / 5xx 自动指数退避重试

### 服务端保护

- 每 IP 每分钟请求数限流（默认 10，可通过 `RATE_LIMIT_PER_MINUTE` 调整，设 0 关闭）
- 打往模型接口的并发上限（`LLM_MAX_CONCURRENCY`），避免瞬时打满配额
- 默认**不开启任何跨域**（同源部署无需 CORS），需要时才用 `ALLOW_ORIGINS` 显式放行
- 密钥只存在于服务端环境变量，任何接口都不会回显

## 技术栈

- 后端：Python 3.10+ / FastAPI / httpx / Pydantic
- 前端：原生 HTML + CSS + JavaScript（**无构建、零前端依赖**）

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. （可选）配置模型，复制 .env.example 为 .env 并填写 LLM_API_KEY / LLM_MODEL
cp .env.example .env

# 3. 启动服务（只监听本机，最安全）
uvicorn main:app --host 127.0.0.1 --port 8000

# 4. 浏览器访问 http://localhost:8000
```

> 未配置模型也能直接启动，会展示内置示例数据，便于先看完整前端效果。

### Docker 部署

```bash
docker build -t note-to-cards .
docker run -p 8000:8000 --env-file .env note-to-cards
```

> 镜像通过 `.dockerignore` 与显式 `COPY` 双重保证 `.env` 不会被打包（否则密钥会残留在镜像层里，`docker history` 就能捞出来）。

### 公开部署前请务必确认

1. `POST /api/generate` 会消耗你的模型额度。**只想本地演示就保持 `--host 127.0.0.1`**；
   要放公网请保留默认限流，并在反向代理层再加一层 IP 限流。
2. 部署在 Nginx / Caddy 后面时，设 `TRUST_PROXY=true` 才能按真实客户端 IP 限流。
3. 学生笔记会被发送到第三方模型服务，请自行评估隐私要求。

## AI 模型接入（可选）

在 `.env` 或环境变量中配置：

| 变量 | 说明 | 示例 |
|------|------|------|
| `LLM_API_KEY` | API Key（**必填**） | `sk-xxx` |
| `LLM_MODEL` | 模型名（**必填**） | `deepseek-chat` |
| `LLM_BASE_URL` | 接口地址，带不带 `/v1` 都能识别 | `https://api.deepseek.com` |
| `LLM_TIMEOUT` | 超时秒数，默认 60 | `60` |
| `LLM_MAX_TOKENS` | 单次生成最大输出 token，默认 4000 | `4000` |
| `LLM_MAX_RETRIES` | 429 / 5xx 重试次数，默认 2 | `2` |

常见服务商地址：

| 服务商 | `LLM_BASE_URL` |
|--------|----------------|
| DeepSeek | `https://api.deepseek.com` |
| 通义千问（兼容模式） | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Moonshot | `https://api.moonshot.cn/v1` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` |
| OpenAI | `https://api.openai.com/v1` |

## API

### `GET /api/health`

健康检查，同时返回不含密钥的配置摘要：

```json
{ "status": "ok", "llm_configured": true, "model": "deepseek-chat", "endpoint_host": "api.deepseek.com" }
```

### `POST /api/generate`

请求体：

```json
{ "markdown": "# 章节\n- 知识点..." }
```

`markdown` 上限 50000 字符；为空返回 `400`，超长返回 `422`，触发限流返回 `429`（带 `Retry-After` 头）。

返回体：

```json
{
  "outline": {
    "title": "主题",
    "sections": [
      { "title": "章节", "points": ["要点"], "children": [] }
    ]
  },
  "questions": [
    {
      "id": "a1b2c3d4e5f6",
      "type": "single",
      "question": "题干",
      "options": ["选项一", "选项二"],
      "answer": "选项一",
      "explanation": "解析"
    },
    { "id": "0f9e8d7c6b5a", "type": "qa", "question": "题干", "answer": "参考答案", "explanation": "解析" }
  ],
  "source": "llm"
}
```

约定：

- `questions[].answer` 对单选题**始终是 `options` 中的某一项原文**（不是 `"A"` 这样的字母）。
- `questions[].id` 是由题干派生的稳定哈希，前端用它做错题本与进度持久化的键。
- `source` 为 `"sample"` 时表示走了降级路径，此时额外带 `fallback_reason` 与 `degraded: true`。
- 命中缓存时额外带 `"cached": true`。

`fallback_reason` 取值：`not_configured` / `timeout` / `http_error` / `truncated` / `parse_error` / `no_questions`。

## 目录结构

```
note_to_cards/
├── main.py              # FastAPI 入口：路由、限流、CORS、静态资源
├── config.py            # 环境变量读取（解析失败回退默认值，不让服务 500）
├── llm.py               # LLM 客户端、Prompt、JSON 解析、结构清洗、缓存
├── ratelimit.py         # 进程内滑动窗口限流
├── sample_data.py       # 内置兜底示例数据
├── static/
│   ├── index.html       # 前端页面
│   ├── style.css        # 样式（玻璃拟态 + 弥散光晕 + 渐变描边）
│   └── app.js           # 交互逻辑、3D 翻转、错题本、持久化、导出
├── tests/
│   ├── test_llm.py      # JSON 解析 / URL 规范化 / 结构清洗 / 上游交互
│   ├── test_api.py      # 路由、入参校验、限流、降级
│   ├── test_config.py   # 环境变量解析
│   └── test_ratelimit.py
├── requirements.txt / requirements-dev.txt
├── pyproject.toml       # pytest 与 ruff 配置
├── Dockerfile / .dockerignore
└── .github/workflows/ci.yml
```

## 测试

```bash
pip install -r requirements-dev.txt
pytest -q          # 77 个用例
ruff check .       # 代码风格
```

CI（GitHub Actions）会在每次 push / PR 时自动执行 ruff 与 pytest。

## 许可证

[MIT License](LICENSE)
