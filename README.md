# 灵动笔卡 (NoteMind AI)

> 把 Markdown 课程笔记，变成会提问的智能复习卡片。

`note_to_cards` 是一款面向在校大学生与备考学生的 AI 学习工具：将用户粘贴的 Markdown 课程笔记，通过大模型自动提炼为**结构化复习提纲**与**自测刷题卡片**（单选题 / 问答题），支持卡片 3D 翻转查看答案与即时对错反馈，帮助学生主动回忆、高效复习。界面采用**玻璃拟态 + 弥散光晕 + 渐变描边**的极简亮色美学。

## 功能特性

### 核心功能
- 顶部大文本框粘贴 Markdown 笔记，一键生成复习内容
- 下方左侧展示树状**结构化复习提纲**（核心概念、关键术语、要点）
- 下方右侧展示**自测刷题卡片**：正面显示题目和选项，背面显示答案与解析

### 交互体验
- 卡片 3D 翻转查看答案（点击卡片或按空格键）
- 答题即时反馈：点击 A/B/C/D 选项先给出 0.8 秒对错反馈（正确浅绿 / 错误浅红并高亮正确项），再平滑翻转显示解析
- 「上一题 / 下一题」按钮 + 键盘 `←` `→` 方向键切换 + 进度条显示

### AI 能力
- 后端 Python FastAPI 接收 Markdown，预设 Prompt 调用大模型生成 JSON
- 兼容 OpenAI / DeepSeek / 通义千问 / Moonshot 等 OpenAI 格式接口
- 未配置 AI 模型时自动降级为内置示例数据，保证开箱即用

### 视觉设计
- 玻璃拟态 + 弥散光晕 + 渐变描边，柔和蓝/青/紫/淡黄渐变四芒星图标
- 极简亮色美学，iOS 弹性缓动，底部流动粒子装饰

## 技术栈

- 后端：Python 3.10+ / FastAPI / httpx / Pydantic
- 前端：原生 HTML + CSS + JavaScript（无构建、零前端依赖）

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. （可选）配置 AI 模型，复制 .env.example 为 .env 并填写
cp .env.example .env

# 3. 启动服务
uvicorn main:app --host 0.0.0.0 --port 8000

# 4. 浏览器访问
# http://localhost:8000
```

### Docker 部署

```bash
docker build -t note-to-cards .
docker run -p 8000:8000 --env-file .env note-to-cards
```

## AI 模型接入（可选）

后端通过 OpenAI 兼容接口调用大模型。在 `.env` 或环境变量中配置：

| 变量 | 说明 | 示例 |
|------|------|------|
| `LLM_API_KEY` | API Key | `sk-xxx` |
| `LLM_BASE_URL` | 接口地址（可不带 `/v1`） | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `LLM_TIMEOUT` | 超时秒数 | `60` |

未配置时，`POST /api/generate` 会返回内置示例数据，用于演示完整前端效果。

## API

### `POST /api/generate`

请求体：

```json
{ "markdown": "# 章节\n- 知识点..." }
```

返回体：

```json
{
  "outline": { "title": "主题", "sections": [ { "title": "章节", "points": ["要点"], "children": [] } ] },
  "questions": [
    { "type": "single", "question": "题干", "options": ["A", "B", "C", "D"], "answer": "B", "explanation": "解析" },
    { "type": "qa", "question": "题干", "answer": "答案", "explanation": "解析" }
  ],
  "source": "llm | sample"
}
```

### `GET /api/health`

服务健康检查。

## 目录结构

```
note_to_cards/
├── main.py            # FastAPI 入口与路由
├── llm.py             # LLM 客户端、Prompt、JSON 解析
├── sample_data.py     # 内置兜底示例数据
├── static/
│   ├── index.html     # 前端页面
│   ├── style.css      # 样式（玻璃拟态 + 弥散光晕 + 渐变描边）
│   └── app.js         # 交互逻辑、3D 翻转、答题反馈
├── requirements.txt
├── .env.example
└── README.md
```

## 测试

```bash
pip install pytest
python -m pytest tests/ -v
```

## 许可证

MIT License