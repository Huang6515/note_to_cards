"use strict";

/* ============================================================
   灵动笔卡 (NoteMind AI) — 前端交互
   零依赖、无构建步骤：原生 DOM + fetch + localStorage
   ============================================================ */

const $ = (id) => document.getElementById(id);

const MAX_MARKDOWN = 50000;
const REQUEST_TIMEOUT_MS = 90000;
const STORAGE_KEY = "notemind.session.v2";
const OPTION_KEYS = ["A", "B", "C", "D", "E", "F"];

const els = {
  markdown: $("markdown"),
  charCount: $("char-count"),
  generate: $("btn-generate"),
  clear: $("btn-clear"),
  fill: $("btn-fill"),
  status: $("status"),
  restoreHint: $("restore-hint"),
  result: $("result"),
  outline: $("outline"),
  shuffle: $("btn-shuffle"),
  wrongbook: $("btn-wrongbook"),
  wrongCount: $("wrong-count"),
  reset: $("btn-reset"),
  exportMd: $("btn-export-md"),
  exportCsv: $("btn-export-csv"),
  cardInner: $("card-inner"),
  cardType: $("card-type"),
  cardQuestion: $("card-question"),
  cardOptions: $("card-options"),
  cardHint: $("card-hint"),
  cardAnswer: $("card-answer"),
  cardExplanation: $("card-explanation"),
  feedback: $("feedback"),
  prev: $("btn-prev"),
  next: $("btn-next"),
  counter: $("card-counter"),
  progressText: $("card-progress"),
  progressTrack: $("progress-track"),
  progressFill: $("progress-fill"),
};

/* 后端降级原因 -> 给用户看的提示。取值与 llm.py 中的 failure_reason 一一对应。 */
const FALLBACK_MESSAGES = {
  not_configured: "未配置 AI 模型（缺少 LLM_API_KEY 或 LLM_MODEL），当前展示内置示例数据。",
  timeout: "调用 AI 接口超时，当前展示内置示例数据。可调大 LLM_TIMEOUT 后重试。",
  http_error: "调用 AI 接口失败（请检查 Key、余额、网络与 LLM_BASE_URL），当前展示内置示例数据。",
  truncated: "AI 输出被 max_tokens 截断，笔记可能过长。建议拆分笔记后重试。",
  parse_error: "AI 返回的内容无法解析为 JSON，当前展示内置示例数据。",
  no_questions: "AI 没有返回有效题目，当前展示内置示例数据。",
  unknown: "AI 调用失败，当前展示内置示例数据。",
};

const SAMPLE_TEXT = `# 计算机网络基础

## 1. 网络分层模型
- OSI 七层模型：物理层、数据链路层、网络层、传输层、会话层、表示层、应用层
- TCP/IP 四层模型：网络接口层、网际层、传输层、应用层

### 1.1 传输层
- TCP：面向连接、可靠传输、流量控制、拥塞控制
- UDP：无连接、不可靠、低延迟，适合实时音视频

## 2. TCP 三次握手与四次挥手
- 三次握手：SYN → SYN+ACK → ACK
- 四次挥手：FIN → ACK → FIN → ACK

## 3. IP 地址与子网
- IPv4 32 位，IPv6 128 位
- 子网掩码划分网络号与主机号`;

/* ---------- 状态 ---------- */

const state = {
  markdown: "",
  outline: null,
  questions: [],
  index: 0,
  mode: "all", // all | wrong
  answered: new Set(), // 已作答的题目 id
  picks: {}, // 题目 id -> 用户选中的选项文本
  wrong: new Set(), // 答错过的题目 id
};

let flipTimer = null;

/* ---------- 小工具 ---------- */

function setStatus(message, type) {
  if (!message) {
    els.status.hidden = true;
    els.status.textContent = "";
    els.status.className = "status";
    return;
  }
  els.status.hidden = false;
  els.status.className = "status " + (type || "");
  // 全部用 DOM 节点拼装，不碰 innerHTML，从根上避免 XSS
  els.status.textContent = "";
  if (type === "loading") {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    spinner.setAttribute("aria-hidden", "true");
    els.status.appendChild(spinner);
  }
  els.status.appendChild(document.createTextNode(message));
}

function showRestoreHint(text) {
  els.restoreHint.textContent = text;
  els.restoreHint.hidden = false;
}

function hideRestoreHint() {
  els.restoreHint.hidden = true;
  els.restoreHint.textContent = "";
}

function shuffleArray(items) {
  const arr = items.slice();
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function fallbackId(question) {
  return `${question.type || "qa"}:${String(question.question || "").slice(0, 64)}`;
}

function updateCharCount() {
  const length = els.markdown.value.length;
  els.charCount.textContent = `${length} / ${MAX_MARKDOWN}`;
  els.charCount.classList.toggle("char-count-warn", length > MAX_MARKDOWN * 0.9);
}

/* ---------- 题目集合 ---------- */

function activeList() {
  if (state.mode === "wrong") {
    return state.questions.filter((q) => state.wrong.has(q.id));
  }
  return state.questions;
}

/* ---------- 提纲渲染 ---------- */

function renderOutline(outline) {
  const container = els.outline;
  container.textContent = "";

  const sections = outline && Array.isArray(outline.sections) ? outline.sections : [];
  if (sections.length === 0) {
    const empty = document.createElement("div");
    empty.className = "outline-empty";
    empty.textContent = "暂无提纲";
    container.appendChild(empty);
    return;
  }

  const title = document.createElement("div");
  title.className = "outline-title";
  title.textContent = (outline && outline.title) || "复习提纲";
  container.appendChild(title);

  const tree = document.createElement("ul");
  tree.className = "outline-tree";
  sections.forEach((section) => tree.appendChild(buildOutlineNode(section)));
  container.appendChild(tree);
}

function buildOutlineNode(section) {
  const data = section && typeof section === "object" ? section : {};
  const li = document.createElement("li");
  li.className = "outline-node";

  const head = document.createElement("div");
  head.className = "outline-node-head";

  const bullet = document.createElement("span");
  bullet.className = "outline-bullet";
  head.appendChild(bullet);

  const title = document.createElement("span");
  title.className = "outline-node-title";
  title.textContent = data.title || "";
  head.appendChild(title);

  li.appendChild(head);

  // 后端已保证是数组，这里再兜一层：模型数据不可信，不能让它打断整块渲染
  const points = Array.isArray(data.points) ? data.points : [];
  points.forEach((point) => {
    const p = document.createElement("span");
    p.className = "outline-point";
    p.textContent = point;
    li.appendChild(p);
  });

  const children = Array.isArray(data.children) ? data.children : [];
  if (children.length > 0) {
    const childrenUl = document.createElement("ul");
    childrenUl.className = "outline-children";
    children.forEach((child) => childrenUl.appendChild(buildOutlineNode(child)));
    li.appendChild(childrenUl);
  }

  return li;
}

/* ---------- 卡片渲染 ---------- */

function resetFlipTimer() {
  if (flipTimer) {
    clearTimeout(flipTimer);
    flipTimer = null;
  }
}

function renderQuestion(index) {
  const list = activeList();
  const q = list[index];

  resetFlipTimer();
  els.cardInner.classList.remove("flipped");
  els.cardOptions.scrollTop = 0;
  els.cardExplanation.scrollTop = 0;
  els.feedback.textContent = "";

  if (!q) {
    els.cardType.textContent = state.mode === "wrong" ? "错题本" : "自测";
    els.cardQuestion.textContent =
      state.mode === "wrong" ? "错题本是空的 —— 答错的题会自动收进这里。" : "暂无题目";
    els.cardOptions.textContent = "";
    els.cardOptions.hidden = true;
    els.cardHint.hidden = true;
    els.cardAnswer.textContent = "";
    els.cardExplanation.textContent = "";
    els.counter.textContent = `0 / ${list.length}`;
    els.progressText.textContent = `共 ${list.length} 题`;
    els.prev.disabled = true;
    els.next.disabled = true;
    updateProgress();
    return;
  }

  const isSingle = q.type === "single";
  const hasOptions = isSingle && Array.isArray(q.options) && q.options.length > 0;
  const picked = state.picks[q.id];

  els.cardType.textContent = isSingle ? "单选题" : "问答题";
  els.cardQuestion.textContent = q.question || "";

  if (hasOptions) {
    els.cardOptions.hidden = false;
    els.cardHint.hidden = true;
    els.cardOptions.textContent = "";

    q.options.forEach((opt, i) => {
      const li = document.createElement("li");
      li.className = "card-option";
      li.dataset.option = opt;
      li.setAttribute("role", "button");
      li.setAttribute("tabindex", "0");
      // 读屏器要把选项内容一起念出来，只念「A」是没有意义的
      li.setAttribute("aria-label", `${OPTION_KEYS[i] || i + 1}. ${opt}`);

      const key = document.createElement("span");
      key.className = "option-key";
      key.setAttribute("aria-hidden", "true");
      key.textContent = OPTION_KEYS[i] || String(i + 1);

      const text = document.createElement("span");
      text.className = "option-text";
      text.textContent = opt;

      li.appendChild(key);
      li.appendChild(text);

      li.addEventListener("click", () => answerOption(li, q));
      li.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          answerOption(li, q);
        }
      });

      els.cardOptions.appendChild(li);
    });

    if (picked) applyAnswerStyles(q, picked);
  } else {
    els.cardOptions.hidden = true;
    els.cardOptions.textContent = "";
    els.cardHint.hidden = false;
  }

  els.cardAnswer.textContent = q.answer || "";
  els.cardExplanation.textContent = q.explanation || "（无解析）";
  els.counter.textContent = `${index + 1} / ${list.length}`;
  els.progressText.textContent = `共 ${list.length} 题`;
  els.prev.disabled = index === 0;
  els.next.disabled = index === list.length - 1;
  updateProgress();
}

function applyAnswerStyles(q, picked) {
  const isCorrect = picked === q.answer;
  els.cardOptions.querySelectorAll(".card-option").forEach((li) => {
    const option = li.dataset.option;
    // 正确选项始终高亮为绿色
    if (option === q.answer) li.classList.add("correct");
    if (!isCorrect) {
      if (option === picked) li.classList.add("wrong");
      else if (option !== q.answer) li.classList.add("dimmed");
    }
    li.setAttribute("aria-disabled", "true");
    li.setAttribute("tabindex", "-1");
  });
}

function answerOption(clicked, q) {
  if (!q || state.picks[q.id]) return; // 每题只允许作答一次

  const picked = clicked.dataset.option;
  state.picks[q.id] = picked;
  state.answered.add(q.id);

  const isCorrect = picked === q.answer;
  if (!isCorrect) state.wrong.add(q.id);

  applyAnswerStyles(q, picked);
  updateWrongCount();
  updateProgress();
  els.feedback.textContent = isCorrect
    ? "回答正确"
    : `回答错误，正确答案是 ${q.answer}`;
  saveSession();

  // 先给 0.8 秒对错反馈，再翻面看解析
  resetFlipTimer();
  flipTimer = setTimeout(() => els.cardInner.classList.add("flipped"), 800);
}

function toggleCard() {
  els.cardInner.classList.toggle("flipped");
}

function updateProgress() {
  const list = activeList();
  const total = list.length || 1;
  const done = list.filter((q) => state.answered.has(q.id)).length;
  const percent = Math.round((done / total) * 100);
  els.progressFill.style.width = `${percent}%`;
  els.progressTrack.setAttribute("aria-valuenow", String(percent));
}

function updateWrongCount() {
  els.wrongCount.textContent = String(state.wrong.size);
  els.wrongbook.setAttribute("aria-pressed", String(state.mode === "wrong"));
  els.wrongbook.classList.toggle("active", state.mode === "wrong");
  els.wrongbook.disabled = state.wrong.size === 0 && state.mode !== "wrong";
}

/* ---------- 题目导航 ---------- */

function goToQuestion(index) {
  const list = activeList();
  if (!list.length) return;
  const target = Math.min(Math.max(0, index), list.length - 1);
  if (target === state.index) return;
  state.index = target;
  renderQuestion(state.index);
  saveSession();
}

function prevQuestion() {
  goToQuestion(state.index - 1);
}

function nextQuestion() {
  goToQuestion(state.index + 1);
}

function setMode(mode) {
  state.mode = mode === "wrong" ? "wrong" : "all";
  state.index = 0;
  updateWrongCount();
  renderQuestion(0);
  saveSession();
  if (state.mode === "wrong") {
    const count = activeList().length;
    setStatus(count ? `错题本模式：共 ${count} 道错题` : "错题本还是空的", count ? "ok" : "warn");
  }
}

function shuffleQuestions() {
  if (!state.questions.length) return;
  state.questions = shuffleArray(state.questions).map((q) => {
    const item = Object.assign({}, q);
    if (item.type === "single" && Array.isArray(item.options)) {
      item.options = shuffleArray(item.options);
    }
    return item;
  });
  state.index = 0;
  renderQuestion(0);
  saveSession();
  setStatus("已打乱题目与选项顺序", "ok");
}

function resetProgress() {
  state.answered = new Set();
  state.picks = {};
  state.wrong = new Set();
  state.mode = "all";
  state.index = 0;
  updateWrongCount();
  renderQuestion(0);
  saveSession();
  setStatus("已重置答题进度", "ok");
}

/* ---------- 本地持久化 ---------- */

function saveSession() {
  if (!state.questions.length) return;
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: 2,
        markdown: state.markdown,
        outline: state.outline,
        questions: state.questions,
        index: state.index,
        mode: state.mode,
        answered: Array.from(state.answered),
        picks: state.picks,
        wrong: Array.from(state.wrong),
        savedAt: Date.now(),
      })
    );
  } catch (err) {
    // 无痕模式 / 配额写满：静默降级，不影响主流程
  }
}

function clearSession() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (err) {
    /* 忽略 */
  }
}

function loadSession() {
  let raw = null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch (err) {
    return null;
  }
  if (!raw) return null;
  try {
    const data = JSON.parse(raw);
    if (!data || data.version !== 2) return null;
    if (!Array.isArray(data.questions) || data.questions.length === 0) return null;
    return data;
  } catch (err) {
    return null;
  }
}

function restoreSession() {
  const saved = loadSession();
  if (!saved) return false;

  state.markdown = typeof saved.markdown === "string" ? saved.markdown : "";
  state.outline = saved.outline || null;
  state.questions = saved.questions;
  state.answered = new Set(Array.isArray(saved.answered) ? saved.answered : []);
  state.picks = saved.picks && typeof saved.picks === "object" ? saved.picks : {};
  state.wrong = new Set(Array.isArray(saved.wrong) ? saved.wrong : []);
  state.mode = saved.mode === "wrong" && state.wrong.size > 0 ? "wrong" : "all";
  const maxIndex = Math.max(0, activeList().length - 1);
  state.index = Math.min(Math.max(0, Number(saved.index) || 0), maxIndex);

  els.markdown.value = state.markdown;
  updateCharCount();
  renderOutline(state.outline);
  updateWrongCount();
  els.result.hidden = false;
  renderQuestion(state.index);
  showRestoreHint(
    `已恢复上次的复习进度（共 ${state.questions.length} 题，错题 ${state.wrong.size} 道）。`
  );
  return true;
}

/* ---------- 生成 ---------- */

async function describeHttpError(response) {
  let detail = "";
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") detail = body.detail;
  } catch (err) {
    /* 响应不是 JSON，用下面的兜底文案 */
  }
  if (detail) return detail;
  if (response.status === 400) return "请求内容为空";
  if (response.status === 422) return `笔记不符合要求（可能超过 ${MAX_MARKDOWN} 字符上限）`;
  if (response.status === 429) return "请求过于频繁，请稍后重试";
  return `服务返回错误：${response.status}`;
}

function prepareQuestion(raw) {
  const question = Object.assign({}, raw);
  question.id =
    typeof question.id === "string" && question.id ? question.id : fallbackId(question);
  if (question.type === "single" && Array.isArray(question.options)) {
    question.options = shuffleArray(question.options);
  }
  return question;
}

function applyGenerated(markdown, data) {
  state.markdown = markdown;
  state.outline = data && data.outline ? data.outline : null;
  const rawQuestions = data && Array.isArray(data.questions) ? data.questions : [];
  state.questions = rawQuestions.map(prepareQuestion);
  state.index = 0;
  state.mode = "all";
  state.answered = new Set();
  state.picks = {};
  state.wrong = new Set();

  renderOutline(state.outline);
  updateWrongCount();
  saveSession();

  if (!state.questions.length) {
    setStatus("生成成功，但没有解析出有效题目，请补充更详细的笔记后重试。", "error");
    els.result.hidden = false;
    renderQuestion(0);
    return;
  }

  renderQuestion(0);
  els.result.hidden = false;
  els.result.scrollIntoView({ behavior: "smooth", block: "start" });

  if (data && data.source === "sample") {
    const reason = data.fallback_reason || "unknown";
    setStatus(FALLBACK_MESSAGES[reason] || FALLBACK_MESSAGES.unknown, "warn");
  } else if (data && data.cached) {
    setStatus(`生成完成（命中缓存，共 ${state.questions.length} 题）`, "ok");
  } else {
    setStatus(`生成完成，共 ${state.questions.length} 题`, "ok");
  }
}

async function generate() {
  const markdown = els.markdown.value.trim();
  if (!markdown) {
    setStatus("请先粘贴 Markdown 笔记内容", "error");
    els.markdown.focus();
    return;
  }
  if (markdown.length > MAX_MARKDOWN) {
    setStatus(`笔记过长（${markdown.length} 字符），上限为 ${MAX_MARKDOWN} 字符`, "error");
    return;
  }

  els.generate.disabled = true;
  setStatus("正在调用 AI 模型生成复习内容，请稍候…", "loading");
  els.result.hidden = true;
  hideRestoreHint();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(await describeHttpError(response));
    applyGenerated(markdown, await response.json());
  } catch (err) {
    if (err && err.name === "AbortError") {
      setStatus(
        `生成超时（超过 ${Math.round(REQUEST_TIMEOUT_MS / 1000)} 秒）。长笔记会更慢，可稍后重试或调大 LLM_TIMEOUT。`,
        "error"
      );
    } else {
      setStatus(`生成失败：${(err && err.message) || "未知错误"}`, "error");
    }
  } finally {
    clearTimeout(timer);
    els.generate.disabled = false;
  }
}

/* ---------- 导出 ---------- */

function csvCell(value) {
  const text = value == null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function appendOutlineMarkdown(lines, section, depth) {
  const data = section && typeof section === "object" ? section : {};
  const indent = "  ".repeat(depth);
  lines.push(`${indent}- **${data.title || "未命名章节"}**`);
  const points = Array.isArray(data.points) ? data.points : [];
  points.forEach((point) => lines.push(`${indent}  - ${point}`));
  const children = Array.isArray(data.children) ? data.children : [];
  children.forEach((child) => appendOutlineMarkdown(lines, child, depth + 1));
}

function buildMarkdownExport() {
  const lines = [];
  const outline = state.outline;
  if (outline) {
    lines.push(`# ${outline.title || "复习提纲"}`, "");
    const sections = Array.isArray(outline.sections) ? outline.sections : [];
    sections.forEach((section) => appendOutlineMarkdown(lines, section, 0));
    lines.push("");
  }
  lines.push("## 自测题目", "");
  state.questions.forEach((q, index) => {
    const type = q.type === "single" ? "单选题" : "问答题";
    lines.push(`### ${index + 1}. ${type}`, "", q.question, "");
    if (q.type === "single" && Array.isArray(q.options)) {
      q.options.forEach((opt, i) => lines.push(`- ${OPTION_KEYS[i] || i + 1}. ${opt}`));
      lines.push("");
    }
    lines.push(`**答案**：${q.answer || ""}`, "");
    if (q.explanation) lines.push(`**解析**：${q.explanation}`, "");
  });
  return lines.join("\n");
}

function buildCsvExport() {
  const rows = [["题目", "类型", "答案", "解析", "选项"]];
  state.questions.forEach((q) => {
    rows.push([
      q.question || "",
      q.type === "single" ? "单选题" : "问答题",
      q.answer || "",
      q.explanation || "",
      Array.isArray(q.options) ? q.options.join(" | ") : "",
    ]);
  });
  // 带 BOM，Excel 才会正确识别 UTF-8
  return "\uFEFF" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
}

function downloadFile(filename, content, mime) {
  try {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return true;
  } catch (err) {
    setStatus("当前环境不支持直接下载，可手动复制内容。", "error");
    return false;
  }
}

function exportMarkdown() {
  if (!state.questions.length) return;
  const stamp = new Date().toISOString().slice(0, 10);
  const name = `灵动笔卡-复习卡片-${stamp}.md`;
  if (downloadFile(name, buildMarkdownExport(), "text/markdown;charset=utf-8")) {
    setStatus("已导出 Markdown 文件", "ok");
  }
}

function exportCsv() {
  if (!state.questions.length) return;
  const stamp = new Date().toISOString().slice(0, 10);
  const name = `灵动笔卡-自测题目-${stamp}.csv`;
  if (downloadFile(name, buildCsvExport(), "text/csv;charset=utf-8")) {
    setStatus("已导出 CSV（可导入 Anki / Excel）", "ok");
  }
}

/* ---------- 事件绑定 ---------- */

function bindEvents() {
  els.markdown.addEventListener("input", updateCharCount);

  els.generate.addEventListener("click", generate);

  els.clear.addEventListener("click", () => {
    els.markdown.value = "";
    state.markdown = "";
    state.outline = null;
    state.questions = [];
    state.index = 0;
    state.mode = "all";
    state.answered = new Set();
    state.picks = {};
    state.wrong = new Set();
    els.result.hidden = true;
    clearSession();
    updateCharCount();
    updateWrongCount();
    hideRestoreHint();
    setStatus("");
    els.markdown.focus();
  });

  els.fill.addEventListener("click", () => {
    els.markdown.value = SAMPLE_TEXT;
    updateCharCount();
    setStatus("");
  });

  els.shuffle.addEventListener("click", shuffleQuestions);
  els.reset.addEventListener("click", resetProgress);
  els.exportMd.addEventListener("click", exportMarkdown);
  els.exportCsv.addEventListener("click", exportCsv);
  els.wrongbook.addEventListener("click", () => {
    setMode(state.mode === "wrong" ? "all" : "wrong");
  });

  els.prev.addEventListener("click", prevQuestion);
  els.next.addEventListener("click", nextQuestion);

  // 卡片点击：问答随时翻面；单选必须先作答
  els.cardInner.addEventListener("click", (event) => {
    if (event.target.closest && event.target.closest(".card-option")) return;
    const q = activeList()[state.index];
    if (!q) return;
    if (q.type === "single" && !state.picks[q.id]) return;
    toggleCard();
  });

  // 全局快捷键只在这一个处理器里实现。
  // 之前卡片自身也监听空格，一次按键会触发两次 toggleCard() 互相抵消，空格翻转因此失效。
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (!target || !target.tagName) return;
    if (target.tagName === "TEXTAREA" || target.tagName === "INPUT" || target.isContentEditable) {
      return;
    }
    // 原生按钮与选项自己处理空格/回车，全局快捷键必须让路
    // （否则焦点在按钮上按空格，浏览器原生的按钮激活会被 preventDefault 吃掉）
    if (typeof target.closest === "function" && target.closest("button")) return;
    if (typeof target.closest === "function" && target.closest(".card-option")) return;

    const q = activeList()[state.index];
    if (!q) return;
    const canFlip = q.type !== "single" || Boolean(state.picks[q.id]);

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      prevQuestion();
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      nextQuestion();
      return;
    }
    if (event.key === " " || (event.key === "Enter" && target === els.cardInner)) {
      if (!canFlip) return;
      event.preventDefault();
      toggleCard();
      return;
    }

    // A~F / 1~9 快速作答
    if (q.type === "single" && !state.picks[q.id] && Array.isArray(q.options)) {
      let index = OPTION_KEYS.indexOf(event.key.toUpperCase());
      if (index < 0 && /^[1-9]$/.test(event.key)) index = Number(event.key) - 1;
      if (index >= 0 && index < q.options.length) {
        const node = els.cardOptions.querySelectorAll(".card-option")[index];
        if (node) {
          event.preventDefault();
          answerOption(node, q);
        }
      }
    }
  });
}

function init() {
  bindEvents();
  updateCharCount();
  updateWrongCount();
  restoreSession();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
