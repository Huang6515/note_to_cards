"use strict";

const $ = (id) => document.getElementById(id);

const els = {
  markdown: $("markdown"),
  generate: $("btn-generate"),
  clear: $("btn-clear"),
  fill: $("btn-fill"),
  status: $("status"),
  result: $("result"),
  outline: $("outline"),
  cardInner: $("card-inner"),
  cardType: $("card-type"),
  cardQuestion: $("card-question"),
  cardOptions: $("card-options"),
  cardHint: $("card-hint"),
  cardAnswer: $("card-answer"),
  cardExplanation: $("card-explanation"),
  prev: $("btn-prev"),
  next: $("btn-next"),
  counter: $("card-counter"),
  progress: $("card-progress"),
};

const OPTION_KEYS = ["A", "B", "C", "D", "E", "F"];

let questions = [];
let currentIndex = 0;
let answered = false;   // 当前题是否已作答
let flipTimer = null;   // 0.8s 延时翻转定时器

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

function setStatus(message, type) {
  if (!message) {
    els.status.hidden = true;
    els.status.textContent = "";
    els.status.className = "status";
    return;
  }
  els.status.hidden = false;
  els.status.className = "status " + (type || "loading");
  if (type === "loading") {
    els.status.innerHTML = '<span class="spinner"></span>' + escapeHtml(message);
  } else {
    els.status.textContent = message;
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ---------- 提纲渲染 ---------- */
function renderOutline(outline) {
  const container = els.outline;
  container.innerHTML = "";

  if (!outline || !outline.sections || outline.sections.length === 0) {
    container.innerHTML = '<div class="outline-empty">暂无提纲</div>';
    return;
  }

  const title = document.createElement("div");
  title.className = "outline-title";
  title.textContent = outline.title || "复习提纲";
  container.appendChild(title);

  const tree = document.createElement("ul");
  tree.className = "outline-tree";
  outline.sections.forEach((section) => tree.appendChild(buildOutlineNode(section)));
  container.appendChild(tree);
}

function buildOutlineNode(section) {
  const li = document.createElement("li");
  li.className = "outline-node";

  const head = document.createElement("div");
  head.className = "outline-node-head";

  const bullet = document.createElement("span");
  bullet.className = "outline-bullet";
  head.appendChild(bullet);

  const title = document.createElement("span");
  title.className = "outline-node-title";
  title.textContent = section.title || "";
  head.appendChild(title);

  li.appendChild(head);

  (section.points || []).forEach((point) => {
    const p = document.createElement("span");
    p.className = "outline-point";
    p.textContent = point;
    li.appendChild(p);
  });

  if (section.children && section.children.length > 0) {
    const childrenUl = document.createElement("ul");
    childrenUl.className = "outline-children";
    section.children.forEach((child) => childrenUl.appendChild(buildOutlineNode(child)));
    li.appendChild(childrenUl);
  }

  return li;
}

/* ---------- 卡片渲染 ---------- */
function renderQuestion(index) {
  const q = questions[index];
  if (!q) return;

  resetFlipTimer();
  answered = false;
  els.cardInner.classList.remove("flipped");

  const isSingle = q.type !== "qa";
  els.cardType.textContent = isSingle ? "单选题" : "问答题";
  els.cardQuestion.textContent = q.question || "";

  // 重置滚动位置，切换题目时从顶部开始
  els.cardOptions.scrollTop = 0;
  els.cardExplanation.scrollTop = 0;

  if (isSingle && Array.isArray(q.options) && q.options.length > 0) {
    els.cardOptions.hidden = false;
    els.cardHint.hidden = true;
    els.cardOptions.innerHTML = "";

    q.options.forEach((opt, i) => {
      const li = document.createElement("li");
      li.className = "card-option";
      li.dataset.option = opt;
      li.setAttribute("role", "button");
      li.setAttribute("tabindex", "0");
      li.setAttribute("aria-label", "选项 " + (OPTION_KEYS[i] || String(i + 1)));

      const key = document.createElement("span");
      key.className = "option-key";
      key.textContent = OPTION_KEYS[i] || String(i + 1);

      const text = document.createElement("span");
      text.className = "option-text";
      text.textContent = opt;

      li.appendChild(key);
      li.appendChild(text);

      li.addEventListener("click", () => answerOption(li, q));
      li.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          answerOption(li, q);
        }
      });

      els.cardOptions.appendChild(li);
    });
  } else {
    els.cardOptions.hidden = true;
    els.cardOptions.innerHTML = "";
    els.cardHint.hidden = false;
  }

  els.cardAnswer.textContent = q.answer || "";
  els.cardExplanation.textContent = q.explanation || "（无解析）";

  els.counter.textContent = `${index + 1} / ${questions.length}`;
  els.progress.textContent = `共 ${questions.length} 题`;
  els.prev.disabled = index === 0;
  els.next.disabled = index === questions.length - 1;
}

function resetFlipTimer() {
  if (flipTimer) {
    clearTimeout(flipTimer);
    flipTimer = null;
  }
}

/* 作答：即时对错反馈，0.8s 停顿后翻转 */
function answerOption(clicked, q) {
  if (answered) return;
  answered = true;

  const listItems = els.cardOptions.querySelectorAll(".card-option");
  const isCorrect = clicked.dataset.option === q.answer;

  listItems.forEach((li) => {
    // 正确选项始终高亮为绿色
    if (li.dataset.option === q.answer) {
      li.classList.add("correct");
    }
    // 点错的选项标红，其余干扰项淡出
    if (!isCorrect) {
      if (li === clicked) {
        li.classList.add("wrong");
      } else if (li.dataset.option !== q.answer) {
        li.classList.add("dimmed");
      }
    }
  });

  flipTimer = setTimeout(() => {
    els.cardInner.classList.add("flipped");
  }, 800);
}

function toggleCard() {
  els.cardInner.classList.toggle("flipped");
}

/* ---------- 生成 ---------- */
async function generate() {
  const markdown = els.markdown.value.trim();
  if (!markdown) {
    setStatus("请先粘贴 Markdown 笔记内容", "error");
    return;
  }

  els.generate.disabled = true;
  setStatus("正在调用 AI 模型生成复习内容，请稍候…", "loading");
  els.result.hidden = true;

  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown }),
    });

    if (!res.ok) {
      throw new Error("服务返回错误：" + res.status);
    }

    const data = await res.json();
    renderOutline(data.outline);

    questions = Array.isArray(data.questions) ? data.questions : [];
    currentIndex = 0;

    if (questions.length === 0) {
      setStatus("生成成功，但没有解析出题目，请补充更详细的笔记。", "error");
      els.result.hidden = false;
      return;
    }

    renderQuestion(currentIndex);
    els.result.hidden = false;
    els.result.scrollIntoView({ behavior: "smooth", block: "start" });

    let msg = "生成完成";
    if (data.source === "sample") {
      msg = "未配置 AI 模型，已展示内置示例数据（配置 LLM_API_KEY 后可启用真实模型）";
    }
    setStatus(msg, "");
  } catch (err) {
    setStatus("生成失败：" + err.message, "error");
  } finally {
    els.generate.disabled = false;
  }
}

function prevQuestion() {
  if (currentIndex > 0) {
    currentIndex -= 1;
    renderQuestion(currentIndex);
  }
}

function nextQuestion() {
  if (currentIndex < questions.length - 1) {
    currentIndex += 1;
    renderQuestion(currentIndex);
  }
}

/* ---------- 事件绑定 ---------- */
els.generate.addEventListener("click", generate);

els.clear.addEventListener("click", () => {
  els.markdown.value = "";
  setStatus("");
  els.markdown.focus();
});

els.fill.addEventListener("click", () => {
  els.markdown.value = SAMPLE_TEXT;
  setStatus("");
});

// 卡片点击：问答翻面 / 已作答单选翻面 / 背面翻回
els.cardInner.addEventListener("click", (e) => {
  if (e.target.closest(".card-option")) return; // 选项点击由其自身处理
  const q = questions[currentIndex];
  if (!q) return;
  const isSingle = q.type !== "qa";
  if (isSingle && !answered) return; // 单选未作答，点空白不翻转
  toggleCard();
});

els.cardInner.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    const q = questions[currentIndex];
    if (!q) return;
    const isSingle = q.type !== "qa";
    if (isSingle && !answered) return;
    toggleCard();
  }
});

els.prev.addEventListener("click", prevQuestion);
els.next.addEventListener("click", nextQuestion);

/* 键盘快捷键：← → 切换题目，空格 翻转 */
document.addEventListener("keydown", (e) => {
  const target = e.target;
  if (target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT")) return;

  if (questions.length === 0) return;

  if (e.key === "ArrowLeft") {
    e.preventDefault();
    prevQuestion();
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    nextQuestion();
  } else if (e.key === " ") {
    e.preventDefault();
    const q = questions[currentIndex];
    const isSingle = q && q.type !== "qa";
    if (isSingle && !answered) return;
    toggleCard();
  }
});