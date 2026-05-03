// 版本标记：用于在 DevTools Console 中确认浏览器加载到的是最新版本。
// 修复"切换会话再换回内容不显示 / 流式输出错误"问题。
console.info('[chat-ui] app.js loaded, build=2026-05-03-stream-buffer-v2');

const api = {

  async _readError(resp, fallback = '请求失败') {
    const text = await resp.text();
    let detail = text;
    try {
      const obj = JSON.parse(text);
      detail = obj?.detail || obj?.message || text;
    } catch (_) {
      // keep raw text
    }
    return `HTTP ${resp.status} ${resp.statusText || ''}`.trim() + (detail ? `: ${detail}` : `: ${fallback}`);
  },
  async getConversations() {
    const r = await fetch('/api/conversations');
    return r.json();
  },
  async searchConversations(q) {
    const r = await fetch(`/api/conversations/search?q=${encodeURIComponent(q)}`);
    return r.json();
  },
  async createConversation(title = '新对话') {
    const r = await fetch('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title })
    });
    return r.json();
  },
  async deleteConversation(id) {
    const r = await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
    return r.json();
  },
  async getMessages(id) {
    const r = await fetch(`/api/conversations/${id}/messages`);
    return r.json();
  },
  async sendChat(payload) {
    const r = await fetch('/api/chat/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!r.ok) throw new Error(await this._readError(r, '发送失败'));
    return r.json();
  },
  async streamChat(payload, onEvent, options = {}) {
    const resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: options.signal
    });
    if (!resp.ok || !resp.body) throw new Error(await this._readError(resp, '流式请求失败'));

    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    // 用 try/finally 保证 reader 在异常（如 abort）路径下也会被释放，
    // 避免浏览器层面 ReadableStream 资源泄漏。
    try {
      while (true) {
        let chunkResult;
        try {
          chunkResult = await reader.read();
        } catch (err) {
          // signal.abort() 会让 reader.read() 抛 DOMException，向上抛出由调用方区分静默处理。
          if (options.signal?.aborted) {
            const abortErr = new Error('aborted');
            abortErr.name = 'AbortError';
            throw abortErr;
          }
          throw err;
        }
        const { done, value } = chunkResult;
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() || '';

        for (const chunk of chunks) {
          const lines = chunk.split('\n');
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const raw = line.slice(6);
            try {
              const data = JSON.parse(raw);
              onEvent?.(data);
            } catch (_) {
              // ignore invalid event lines
            }
          }
        }
      }
    } finally {
      try {
        // cancel() 会使后续 read() 立即结束并解锁 stream，避免 reader 持有锁导致泄漏。
        await reader.cancel().catch(() => {});
      } catch (_) {
        // ignore
      }
      try {
        reader.releaseLock();
      } catch (_) {
        // already released or in invalid state
      }
    }
  },

  async uploadFile(file) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/upload/file', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await this._readError(r, '文件上传失败'));
    return r.json();
  },
  async uploadImage(file) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/upload/image', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await this._readError(r, '图片上传失败'));
    return r.json();
  },
  async uploadBackground(file) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/upload/background', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await this._readError(r, '背景上传失败'));
    return r.json();
  },
  async getModels() {
    const r = await fetch('/api/settings/models');
    if (!r.ok) throw new Error(await this._readError(r, '获取模型失败'));
    return r.json();
  },
  async updateModel(model) {
    const r = await fetch('/api/settings/model', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model })
    });
    if (!r.ok) throw new Error(await this._readError(r, '模型更新失败'));
    return r.json();
  },
  async getSystemPrompt() {
    const r = await fetch('/api/settings/system-prompt');
    if (!r.ok) throw new Error(await this._readError(r, '读取 system prompt 失败'));
    return r.json();
  },
  async updateSystemPrompt(content) {
    const r = await fetch('/api/settings/system-prompt', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content })
    });
    if (!r.ok) throw new Error(await this._readError(r, '保存 system prompt 失败'));
    return r.json();
  }
};

const state = {
  currentConversationId: null,
  pendingFileItems: [],
  pendingImages: [],
  selectedModel: '',
  llmTimeoutSec: 180,
  conversations: [],
  autoScrollEnabled: true,
  activeStream: null,
  streamRequestSeq: 0,
  // 记录每条流式请求"截至当前已收到的拼接内容"，按 conversation_id 索引。
  // 即使用户切走会话（detach 状态），后台仍在累积；切回时可立即恢复显示，
  // 不必等到流完全结束 / 不必依赖刷新。
  // 形如：{ [conversationId]: { requestId, content, done } }
  streamBuffers: {},
  sendStatus: {
    phase: 'idle',
    message: ''
  }
};


// UI 可配置项（后续可扩展为设置页动态修改）
const uiConfig = {
  sidebarToggle: {
    collapsed: '☰',
    expanded: '⇔',
  },
  panelToggle: {
    collapsed: '展开',
    expanded: '折叠',
  },
};

const el = {
  layout: document.querySelector('.layout'),
  convList: document.getElementById('conv-list'),
  convSearch: document.getElementById('conv-search'),
  messages: document.getElementById('messages'),
  input: document.getElementById('input'),
  send: document.getElementById('send'),
  newConv: document.getElementById('new-conv'),
  deleteConv: document.getElementById('delete-conv'),
  fileInput: document.getElementById('file-input'),
  pending: document.getElementById('pending'),
  bgUrl: document.getElementById('bg-url'),
  bgFile: document.getElementById('bg-file'),
  applyBg: document.getElementById('apply-bg'),
  clearBg: document.getElementById('clear-bg'),
  modelSelect: document.getElementById('model-select'),
  streamToggle: document.getElementById('stream-toggle'),
  systemPrompt: document.getElementById('system-prompt'),
  saveSystemPrompt: document.getElementById('save-system-prompt'),
  toggleSidebar: document.getElementById('toggle-sidebar'),
  bgPanel: document.getElementById('bg-panel'),
  systemPanel: document.getElementById('system-panel'),
  toggleBgPanel: document.getElementById('toggle-bg-panel'),
  toggleSystemPanel: document.getElementById('toggle-system-panel'),
  chatPanel: document.querySelector('.chat-panel')
};

function applySidebarCollapsed(collapsed) {
  el.layout.classList.toggle('sidebar-collapsed', !!collapsed);
  el.toggleSidebar.textContent = collapsed
    ? uiConfig.sidebarToggle.collapsed
    : uiConfig.sidebarToggle.expanded;
  localStorage.setItem('sidebar_collapsed', collapsed ? '1' : '0');
}

function applyPanelCollapsed(panelEl, toggleBtn, key, collapsed) {
  panelEl.classList.toggle('collapsed', !!collapsed);
  toggleBtn.textContent = collapsed
    ? uiConfig.panelToggle.collapsed
    : uiConfig.panelToggle.expanded;
  localStorage.setItem(key, collapsed ? '1' : '0');
}

const md = window.markdownit({
  breaks: true,
  linkify: true,
  html: false
});

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function splitMathSegments(input) {
  const text = String(input || '');
  const regex = /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^$\n]+\$)/g;
  const placeholders = [];
  const parts = [];
  let last = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    parts.push(text.slice(last, match.index));

    const full = match[0];
    const token = `@@MATH_TOKEN_${placeholders.length}@@`;

    if (full.startsWith('$$') && full.endsWith('$$')) {
      placeholders.push({ token, display: true, value: full.slice(2, -2).trim() });
    } else if (full.startsWith('\\[') && full.endsWith('\\]')) {
      placeholders.push({ token, display: true, value: full.slice(2, -2).trim() });
    } else if (full.startsWith('\\(') && full.endsWith('\\)')) {
      placeholders.push({ token, display: false, value: full.slice(2, -2).trim() });
    } else if (full.startsWith('$') && full.endsWith('$')) {
      placeholders.push({ token, display: false, value: full.slice(1, -1).trim() });
    } else {
      placeholders.push({ token, display: false, value: full });
    }
    parts.push(token);
    last = regex.lastIndex;
  }

  parts.push(text.slice(last));
  return { textWithTokens: parts.join(''), placeholders };
}

function renderRichContent(container, rawText) {
  const safeRaw = typeof rawText === 'string' ? rawText : '';
  const { textWithTokens, placeholders } = splitMathSegments(safeRaw);
  let html = DOMPurify.sanitize(md.render(textWithTokens));

  for (const item of placeholders) {
    let mathHtml = '';
    try {
      mathHtml = katex.renderToString(item.value, {
        displayMode: !!item.display,
        throwOnError: false,
      });
    } catch (_) {
      const wrapped = item.display
        ? `\\[${item.value}\\]`
        : `\\(${item.value}\\)`;
      mathHtml = `<code>${escapeHtml(wrapped)}</code>`;
    }

    html = html.replaceAll(item.token, mathHtml);
  }

  container.innerHTML = html;
}

function renderPending() {
  const fileCount = state.pendingFileItems.length;
  const imageCount = state.pendingImages.length;
  const statusClass = state.sendStatus.phase === 'error'
    ? 'is-error'
    : state.sendStatus.phase === 'sending'
      ? 'is-sending'
      : '';

  const fileItems = state.pendingFileItems
    .map((f, idx) => (
      `<span class="pending-chip">📄 ${escapeHtml(f.filename || `文件${idx + 1}`)} <button class="chip-remove" data-type="file" data-index="${idx}">×</button></span>`
    ))
    .join('');

  const imageItems = state.pendingImages
    .map((img, idx) => (
      `<span class="pending-chip">🖼️ ${escapeHtml(img.filename || `图片${idx + 1}`)} <button class="chip-remove" data-type="image" data-index="${idx}">×</button></span>`
    ))
    .join('');

  el.pending.innerHTML = `
    <div class="pending-row">待发送附件：文件 ${fileCount} 个，图片 ${imageCount} 张</div>
    <div class="pending-items">${fileItems}${imageItems}${fileCount + imageCount === 0 ? '<span class="pending-empty">（无）</span>' : ''}</div>
    <div class="send-status ${statusClass}">${escapeHtml(state.sendStatus.message || '')}</div>
  `;
}

function setSendStatus(phase, message = '') {
  state.sendStatus = { phase, message };
  renderPending();
}

function isNearBottom(threshold = 40) {
  const distance = el.messages.scrollHeight - el.messages.scrollTop - el.messages.clientHeight;
  return distance <= threshold;
}

function maybeScrollToBottom(force = false) {
  if (force || state.autoScrollEnabled) {
    el.messages.scrollTop = el.messages.scrollHeight;
  }
}

function bindMessagesAutoScrollTracking() {
  el.messages.addEventListener('scroll', () => {
    state.autoScrollEnabled = isNearBottom();
  });
}

function cancelActiveStream() {
  if (state.activeStream?.controller) {
    try {
      state.activeStream.controller.abort();
    } catch (_) {
      // ignore
    }
  }
  state.activeStream = null;
}

/**
 * "脱离"当前流但不中止它：清空 activeStream 引用，让前端不再渲染它的事件，
 * 但服务端仍可继续完成生成并落库（即使前端切换了会话/模型也能保住答案）。
 * 用法：切换会话/切换模型时调用，避免误杀正在产出的回复。
 */
function detachActiveStream() {
  // 注意：不调用 controller.abort()，让 fetch/SSE 连接继续走完。
  // 后续到达的 evt 在 send 主循环里会被 isActiveStreamRequest() 过滤，
  // 因为我们已经把 state.activeStream 置 null。
  state.activeStream = null;
}

function hasActiveStream() {
  return !!state.activeStream;
}

function activeStreamConversationId() {
  return state.activeStream ? state.activeStream.conversationId : null;
}


function isActiveStreamRequest(requestId, conversationId) {
  return !!state.activeStream
    && state.activeStream.requestId === requestId
    && state.activeStream.conversationId === conversationId;
}

function renderMessages(messages) {
  el.messages.innerHTML = '';
  messages.forEach(m => {
    const wrap = document.createElement('div');
    wrap.className = `msg-block ${m.role}`;

    const div = document.createElement('div');
    div.className = `msg ${m.role}`;
    if (m.role === 'assistant') {
      renderAssistantContent(div, m.content);
    } else {
      renderRichContent(div, m.content);
    }
    wrap.appendChild(div);

    if (m.role === 'assistant') {
      const latencyMs = parseLatencyMs(m.attachments);
      if (latencyMs > 0) {
        const latency = document.createElement('div');
        latency.className = 'msg-latency';
        latency.textContent = `回答用时 ${(latencyMs / 1000).toFixed(2)}s`;
        wrap.appendChild(latency);
      }
    }

    el.messages.appendChild(wrap);
  });
  state.autoScrollEnabled = true;
  maybeScrollToBottom(true);
}

function parseLatencyMs(attachments) {
  const text = String(attachments || '');
  const m = text.match(/latency_ms=(\d+)/);
  if (!m) return 0;
  return Number(m[1] || 0);
}

function appendOrUpdateStreamingAssistant(text) {
  let wrap = el.messages.querySelector('.msg-block.assistant.streaming');
  let div = wrap ? wrap.querySelector('.msg.assistant.streaming') : null;

  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'msg-block assistant streaming';

    div = document.createElement('div');
    div.className = 'msg assistant streaming';
    wrap.appendChild(div);

    el.messages.appendChild(wrap);
  }

  renderAssistantContent(div, text);
  maybeScrollToBottom();
}

function setStreamingAssistantLatency(latencyMs) {
  if (!latencyMs || latencyMs <= 0) return;
  const wrap = el.messages.querySelector('.msg-block.assistant.streaming');
  if (!wrap) return;

  let latency = wrap.querySelector('.msg-latency');
  if (!latency) {
    latency = document.createElement('div');
    latency.className = 'msg-latency';
    wrap.appendChild(latency);
  }
  latency.textContent = `回答用时 ${(latencyMs / 1000).toFixed(2)}s`;
}

function ensureWaitingAssistant() {
  let waiting = el.messages.querySelector('.msg-block.assistant.waiting');
  if (waiting) return;
  waiting = document.createElement('div');
  waiting.className = 'msg-block assistant waiting';
  waiting.innerHTML = '<div class="msg assistant waiting-msg"><span class="dotting">消息已发送，思考中</span></div>';
  el.messages.appendChild(waiting);
  maybeScrollToBottom();
}

function setWaitingAssistantText(text) {
  const waiting = el.messages.querySelector('.msg-block.assistant.waiting .waiting-msg .dotting');
  if (waiting) waiting.textContent = text || '消息已发送，思考中';
}

function removeWaitingAssistant() {
  const waiting = el.messages.querySelector('.msg-block.assistant.waiting');
  if (waiting) waiting.remove();
}

function appendErrorMessage(text) {
  const wrap = document.createElement('div');
  wrap.className = 'msg-block assistant';

  const div = document.createElement('div');
  div.className = 'msg assistant error';
  div.textContent = text;
  wrap.appendChild(div);

  el.messages.appendChild(wrap);
  maybeScrollToBottom();
}

function extractThinkingParts(rawText) {
  let rest = String(rawText || '');
  const thoughts = [];

  rest = rest.replace(/<(?:think|thinking|reasoning|reasoning_content|thought|cot)>([\s\S]*?)<\/(?:think|thinking|reasoning|reasoning_content|thought|cot)>/gi, (_, p1) => {
    const t = String(p1 || '').trim();
    if (t) thoughts.push(t);
    return '';
  });

  rest = rest.replace(/```(?:thinking|reasoning|thought|思考)[^\n]*\n([\s\S]*?)```/gi, (_, p1) => {
    const t = String(p1 || '').trim();
    if (t) thoughts.push(t);
    return '';
  });

  const mergedThought = thoughts.filter(Boolean).join('\n\n---\n\n');

  return {
    thought: mergedThought,
    main: rest.trim()
  };
}

function renderAssistantContent(container, rawText) {
  const { thought, main } = extractThinkingParts(rawText);
  container.innerHTML = '';

  if (thought) {
    const details = document.createElement('details');
    details.className = 'assistant-thought';

    const summary = document.createElement('summary');
    summary.textContent = '思考过程';
    details.appendChild(summary);

    const body = document.createElement('div');
    body.className = 'assistant-thought-body';
    renderRichContent(body, thought);
    details.appendChild(body);

    container.appendChild(details);
  }

  const mainDiv = document.createElement('div');
  mainDiv.className = 'assistant-main';
  renderRichContent(mainDiv, main || (thought ? '（已隐藏思考过程）' : ''));
  container.appendChild(mainDiv);
}

function createIdempotencyKey() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function extractErrorMessage(err) {
  const raw = err?.message || String(err || '未知错误');
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      if (typeof parsed.detail === 'string' && parsed.detail) return parsed.detail;
      if (typeof parsed.message === 'string' && parsed.message) return parsed.message;
    }
  } catch (_) {
    // keep raw
  }
  return raw;
}

async function refreshConversations(forcedItems = null, options = {}) {
  let items = [];
  try {
    items = forcedItems ?? await api.getConversations();
  } catch (err) {
    console.warn('加载会话列表失败', err);
    if (!options.noRetry) {
      setTimeout(() => refreshConversations(null, { noRetry: true }), 1200);
    }
    return;
  }
  if (!Array.isArray(items)) {
    items = [];
  }
  state.conversations = items;
  el.convList.innerHTML = '';

  items.forEach(c => {
    const li = document.createElement('li');
    li.textContent = c.title || `会话 ${c.id}`;
    if (c.id === state.currentConversationId) li.classList.add('active');
    li.onclick = async () => {
      // 切换会话时：
      //   - 如果当前活跃流属于"目标会话"（用户切回正在产出的会话），保留它；
      //   - 否则 detach（不 abort）：让服务端继续完成生成并落库，
      //     用户切回原会话时可以通过 getMessages 拿到完整答案。
      if (hasActiveStream() && activeStreamConversationId() !== c.id) {
        detachActiveStream();
      }
      state.currentConversationId = c.id;
      await refreshConversations(state.conversations);
      const msgs = await api.getMessages(c.id);
      renderMessages(msgs);

      // 切回有"在途流"的会话：
      // 关键：buffer 在 send 主流程开始时就已经创建（即使 detach，它仍存在），
      // 所以不能依赖 hasActiveStream()（detach 后为 false）。
      // 用 buffer 是否存在 + done 标记来判断"该会话是否仍在流式生成中"。
      const buf = state.streamBuffers[c.id];
      if (buf && !buf.done) {
        if (buf.content) {
          // 有累积内容：直接续显
          appendOrUpdateStreamingAssistant(buf.content);
        } else {
          // 还没有任何 delta（模型可能在思考阶段）：补一个等待气泡
          ensureWaitingAssistant();
          setWaitingAssistantText('该会话仍有未完成的回复，正在恢复...');
        }
        // 重新关联 activeStream 引用，让后续到达的 delta 能在主回调里识别为"可见"。
        // 重要：state.activeStream 重新指向这条流后，isActiveStreamRequest 会再次成立，
        // 心跳/状态消息也会刷回 UI。但要确保 conversationId 与 requestConversationId 一致。
        // 这里我们只能恢复"会话维度"——requestId 和 controller 仍由原 send 主流程持有；
        // 简化做法：只要切回时存在 buffer，就把 controller 留空（无需中止能力，
        // 因为同会话二次发送仍走 confirm + cancelActiveStream 路径，cancelActiveStream
        // 会在 controller 缺失时退化为只清空引用）。
        // 这里**不**重新 attach activeStream（避免与原 send 主流程争抢 controller）。
        // 真正驱动渲染的逻辑是 delta 回调里的 `state.currentConversationId === requestConversationId`，
        // 它已经满足。
      }

    };

    el.convList.appendChild(li);
  });


  if (!state.currentConversationId && items.length > 0) {
    state.currentConversationId = items[0].id;
    const msgs = await api.getMessages(state.currentConversationId);
    renderMessages(msgs);
    await refreshConversations(items);
  }
}

async function refreshModels() {
  const data = await api.getModels();
  const models = Array.isArray(data.models) ? data.models : [];
  const current = data.current || '';
  state.selectedModel = current;
  const timeoutSec = Number(data.timeout_sec || 0);
  if (Number.isFinite(timeoutSec) && timeoutSec > 0) {
    state.llmTimeoutSec = timeoutSec;
  }

  el.modelSelect.innerHTML = '';
  if (models.length === 0) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = current || '默认模型';
    el.modelSelect.appendChild(opt);
    el.modelSelect.disabled = true;
    return;
  }

  el.modelSelect.disabled = false;
  models.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    if (m === current) opt.selected = true;
    el.modelSelect.appendChild(opt);
  });
}

async function refreshSystemPrompt() {
  try {
    const data = await api.getSystemPrompt();
    el.systemPrompt.value = data.content || '';
  } catch (err) {
    console.warn('加载 System Prompt 失败', err);
  }
}

async function ensureConversation() {
  if (state.currentConversationId) return;
  const c = await api.createConversation('新对话');
  state.currentConversationId = c.id;
  await refreshConversations();
}

async function handleFileSelection(files) {
  if (!files || files.length === 0) return;

  for (const file of files) {
    try {
      if (file.type.startsWith('image/')) {
        const data = await api.uploadImage(file);
        state.pendingImages.push(data);
      } else {
        const data = await api.uploadFile(file);
        state.pendingFileItems.push({
          filename: data.filename || file.name,
          extracted_text: data.extracted_text || ''
        });
      }
      renderPending();
    } catch (err) {
      const msg = extractErrorMessage(err);
      setSendStatus('error', `上传失败(${file.name}): ${msg}`);
    }
  }
}

el.pending.addEventListener('click', (e) => {
  const target = e.target instanceof Element ? e.target : null;
  const btn = target ? target.closest('.chip-remove') : null;
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const type = btn.dataset.type;
  const index = Number(btn.dataset.index || -1);
  if (index < 0) return;

  if (type === 'file') {
    state.pendingFileItems.splice(index, 1);
  } else if (type === 'image') {
    state.pendingImages.splice(index, 1);
  }
  renderPending();
});

function applyBackground(value) {
  const bg = (value || '').trim();
  if (!bg) {
    el.chatPanel.style.setProperty('--chat-bg', 'none');
    localStorage.removeItem('chat_bg');
    return;
  }
  el.chatPanel.style.setProperty('--chat-bg', `url("${bg}")`);
  try {
    localStorage.setItem('chat_bg', bg);
  } catch (e) {
    console.warn('背景图过大，无法持久化到 localStorage，但已应用到当前页面。', e);
  }
}

el.newConv.onclick = async () => {
  const c = await api.createConversation('新对话');
  state.currentConversationId = c.id;
  renderMessages([]);
  await refreshConversations();
};

el.deleteConv.onclick = async () => {
  if (!state.currentConversationId) return;
  cancelActiveStream();
  await api.deleteConversation(state.currentConversationId);
  state.currentConversationId = null;
  renderMessages([]);
  await refreshConversations();
};

el.convSearch.addEventListener('input', async () => {
  const q = el.convSearch.value.trim();
  if (!q) {
    await refreshConversations();
    return;
  }
  try {
    const rows = await api.searchConversations(q);
    await refreshConversations(rows);
  } catch (err) {
    console.warn('搜索失败', err);
  }
});

el.modelSelect.onchange = async () => {
  const model = el.modelSelect.value;
  if (!model) return;
  // 切换模型不应中止当前正在进行的回复——它带的是旧模型的 payload，
  // 让它跑完并落库。新模型只对"下一次发送"生效。
  const wasStreaming = hasActiveStream();
  try {
    const data = await api.updateModel(model);
    state.selectedModel = data.model;
    if (wasStreaming) {
      setSendStatus('sending', `已切换默认模型为 ${data.model}，将在下一次发送时生效（当前回复继续使用旧模型）`);
    }
  } catch (err) {
    alert('模型切换失败: ' + err.message);
    await refreshModels();
  }
};


el.saveSystemPrompt.onclick = async () => {
  const content = el.systemPrompt.value || '';
  try {
    await api.updateSystemPrompt(content);
    alert('System Prompt 已保存');
  } catch (err) {
    alert('保存 System Prompt 失败: ' + err.message);
  }
};

el.toggleSidebar.onclick = () => {
  const collapsed = !el.layout.classList.contains('sidebar-collapsed');
  applySidebarCollapsed(collapsed);
};

el.toggleBgPanel.onclick = () => {
  const collapsed = !el.bgPanel.classList.contains('collapsed');
  applyPanelCollapsed(el.bgPanel, el.toggleBgPanel, 'bg_panel_collapsed', collapsed);
};

el.toggleSystemPanel.onclick = () => {
  const collapsed = !el.systemPanel.classList.contains('collapsed');
  applyPanelCollapsed(el.systemPanel, el.toggleSystemPanel, 'system_panel_collapsed', collapsed);
};

el.fileInput.onchange = async (e) => {
  const files = Array.from(e.target.files || []);
  await handleFileSelection(files);
  e.target.value = '';
};

el.input.addEventListener('paste', async (event) => {
  const items = event.clipboardData?.items || [];
  const pastedImages = [];
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      event.preventDefault();
      const blob = item.getAsFile();
      if (!blob) continue;
      const file = new File([blob], `pasted-${Date.now()}.png`, { type: blob.type || 'image/png' });
      pastedImages.push(file);
    }
  }
  await handleFileSelection(pastedImages);
});

el.send.onclick = async () => {
  const text = el.input.value.trim();
  if (!text) return;

  let requestConversationId = null;
  let requestId = null;
  let hasError = false;

  try {
    // 处理"已有活跃流时再次发送"的边界情况：
    //   - 当前会话的流：要 abort 才能让新消息发出去（用户语义就是"覆盖上一次"）；
    //     但 abort 之前后端已加 CancelledError 兜底，会落库 partial，避免回答消失。
    //   - 别的会话的流：detach（后端继续跑完落库），新发送不影响它。
    if (hasActiveStream()) {
      const activeConv = activeStreamConversationId();
      if (activeConv === state.currentConversationId) {
        // 同会话：用户在同一个对话里再次发问，确认后中止旧流（旧流的 partial 会被服务端落库）。
        const ok = window.confirm('当前回复仍在生成中。继续发送将中断当前回复（已生成的部分会保留），是否继续？');
        if (!ok) {
          el.send.disabled = false;
          return;
        }
        cancelActiveStream();
      } else {
        // 跨会话：让旧流继续在后台完成，新会话的发送独立进行。
        detachActiveStream();
      }
    }
    setSendStatus('sending', '正在发送消息到服务器...');
    el.send.disabled = true;
    await ensureConversation();
    requestConversationId = state.currentConversationId;


    const existing = await api.getMessages(requestConversationId);
    renderMessages([...existing, { role: 'user', content: text }]);
    state.autoScrollEnabled = true;
    maybeScrollToBottom(true);
    ensureWaitingAssistant();
    setWaitingAssistantText('消息已送达，等待模型响应');
    el.input.value = '';

    const payload = {
      conversation_id: requestConversationId,
      message: text,
      file_contexts: state.pendingFileItems.map(x => x.extracted_text),
      images: state.pendingImages,
      model: state.selectedModel || null,
      idempotency_key: createIdempotencyKey(),
    };

    if (el.streamToggle.checked) {
      let assembled = '';
      let streamError = '';
      let streamGotDone = false;
      let streamGotDelta = false;
      let streamGotHeartbeat = false;
      let streamEmptyReply = false;
      const streamStartedAt = Date.now();
      // 真正的"模型有动作"时间戳：仅 delta / done 算活跃；心跳只表示连接活着，不代表模型在产出内容。
      let streamLastDeltaAt = streamStartedAt;
      const timeoutSec = Number(state.llmTimeoutSec || 0);
      const streamTimeoutMs = Number.isFinite(timeoutSec) && timeoutSec > 0 ? timeoutSec * 1000 : 180000;
      const totalSec = Math.max(1, Math.floor(streamTimeoutMs / 1000));
      let waitingTicker = null;

      const updateWaitingText = () => {
        if (streamGotDelta || streamGotDone) return;
        const elapsedSec = Math.max(0, Math.floor((Date.now() - streamStartedAt) / 1000));
        const hint = streamGotHeartbeat ? '已收到心跳' : '等待响应';
        setWaitingAssistantText(`消息已送达，模型思考中（已等待 ${elapsedSec}s / ${totalSec}s，${hint}）`);
      };

      requestId = ++state.streamRequestSeq;
      const controller = new AbortController();
      state.activeStream = {
        requestId,
        conversationId: requestConversationId,
        controller
      };
      // 即使 detach，仍要让"切回该会话"时能识别出"该会话有在途流"。
      // 为此预先创建空缓冲（content=''），后续 delta 累加；
      // 用 done=false 表示"仍在运行"。
      state.streamBuffers[requestConversationId] = {
        requestId,
        content: '',
        done: false,
      };

      updateWaitingText();
      waitingTicker = setInterval(updateWaitingText, 1000);


      try {
        await api.streamChat(
          payload,
          (evt) => {
            // 关键：状态变量（streamGotDone/streamGotDelta/streamError 等）必须在
            // detach 之后**也**继续累积，否则 stream 自然结束时主流程会把"已正常完成"
            // 误判为"未收到模型有效输出"并抛错，从而跳过后续 refreshMessages，
            // 导致用户切回会话后看不到答案。
            //   - 渲染（DOM 操作）需要"会话仍是当前会话且仍是当前活跃流"才能做；
            //   - 状态累积仍要做（不依赖 isActiveStreamRequest）。
            const isVisibleStream = isActiveStreamRequest(requestId, requestConversationId)
              && state.currentConversationId === requestConversationId;

            if (evt.event === 'meta') {
              if (isVisibleStream) {
                setSendStatus('sending', `已发送，模型 ${evt.model || state.selectedModel || '默认'} 正在思考...`);
              }
              return;
            }
            if (evt.event === 'heartbeat') {
              streamGotHeartbeat = true;
              if (isVisibleStream) {
                setSendStatus('sending', '模型仍在思考中（已收到心跳）...');
                updateWaitingText();
              }
              return;
            }
            if (evt.event === 'delta') {
              streamGotDelta = true;
              streamLastDeltaAt = Date.now();
              assembled += evt.delta || '';
              // 同步缓冲：让用户切回该会话时能立即看到截至此刻的内容
              state.streamBuffers[requestConversationId] = {
                requestId,
                content: assembled,
                done: false,
              };
              // 当前是可见会话：直接渲染（含切回到该会话后从缓冲恢复后再追加 delta 的情形）
              if (state.currentConversationId === requestConversationId) {
                removeWaitingAssistant();
                setSendStatus('sending', '模型回复中...');
                appendOrUpdateStreamingAssistant(assembled);
              }
              return;
            }
            if (evt.event === 'done') {
              streamGotDone = true;
              streamLastDeltaAt = Date.now();
              const doneReply = typeof evt.assistant_reply === 'string' ? evt.assistant_reply : '';
              // 标记缓冲为 done（保留 content 让后续切回时仍可读到，
              // 但主流程的 finally 阶段会做 cleanup）
              if (state.streamBuffers[requestConversationId]) {
                state.streamBuffers[requestConversationId].done = true;
              }
              if (isVisibleStream) {
                setStreamingAssistantLatency(Number(evt.latency_ms || 0));
                if (!assembled.trim() && !doneReply.trim()) {
                  streamEmptyReply = true;
                } else {
                  removeWaitingAssistant();
                }
              } else {
                // 即使不可见也要标记 empty_reply，让主流程能正确把"detach 期间空回复"识别为错误。
                if (!assembled.trim() && !doneReply.trim()) {
                  streamEmptyReply = true;
                }
              }
              return;
            }

            if (evt.event === 'error') {
              streamError = evt.detail || '流式输出失败';
            }
          },
          { signal: controller.signal }
        );
      } finally {
        if (waitingTicker) {
          clearInterval(waitingTicker);
          waitingTicker = null;
        }
      }


      if (!streamError && streamEmptyReply) {
        const waitedSec = Math.max(0, Math.floor((Date.now() - streamStartedAt) / 1000));
        streamError = `模型返回空内容（已等待 ${waitedSec}s），请重试或切换模型`;
      }

      if (!streamGotDone && !streamError) {
        if (streamGotDelta) {
          streamError = '流式连接异常结束，请重试';
        } else if (streamGotHeartbeat) {
          streamError = '流式连接中断（思考未完成），请重试';
        } else {
          streamError = '未收到模型有效输出，请重试或切换模型';
        }
      }
      if (streamError) throw new Error(streamError);

    } else {
      const timeoutSec = Number(state.llmTimeoutSec || 0);
      const totalSec = Number.isFinite(timeoutSec) && timeoutSec > 0 ? Math.floor(timeoutSec) : 0;
      const startedAt = Date.now();
      let waitingTicker = null;

      const updateWaitingText = () => {
        const elapsedSec = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
        if (totalSec) {
          setWaitingAssistantText(`消息已送达，等待完整回复（已等待 ${elapsedSec}s / ${totalSec}s）`);
        } else {
          setWaitingAssistantText(`消息已送达，等待完整回复（已等待 ${elapsedSec}s）`);
        }
      };

      updateWaitingText();
      waitingTicker = setInterval(updateWaitingText, 1000);
      try {
        await api.sendChat(payload);
      } finally {
        if (waitingTicker) {
          clearInterval(waitingTicker);
          waitingTicker = null;
        }
      }
    }

    if (state.currentConversationId === requestConversationId) {
      removeWaitingAssistant();
    }

    try {
      const refreshed = await api.getMessages(requestConversationId);
      if (state.currentConversationId === requestConversationId) {
        renderMessages(refreshed);
      }
      await refreshConversations();
    } catch (refreshErr) {
      if (state.currentConversationId === requestConversationId) {
        const refreshMsg = extractErrorMessage(refreshErr);
        setSendStatus('error', `回复已返回，但刷新消息列表失败：${refreshMsg}`);
        appendErrorMessage(`后处理失败：${refreshMsg}`);
        hasError = true;
      }
    }

    state.pendingFileItems = [];
    state.pendingImages = [];
  } catch (err) {
    if (err?.name === 'AbortError') {
      // 主动中断，静默处理
    } else if (state.currentConversationId === requestConversationId) {
      removeWaitingAssistant();
      const msg = extractErrorMessage(err);
      appendErrorMessage(`发送失败：${msg}`);
      setSendStatus('error', `发送失败：${msg}`);
      hasError = true;
    }
  } finally {
    if (requestId !== null && state.activeStream?.requestId === requestId) {
      state.activeStream = null;
    }
    // 清理 streamBuffers：流已经结束，缓冲不再需要保留（最终内容已经/将要从 DB 拉取）
    if (requestConversationId !== null) {
      const buf = state.streamBuffers[requestConversationId];
      if (buf && buf.requestId === requestId) {
        delete state.streamBuffers[requestConversationId];
      }
    }
    if (!hasError && state.sendStatus.phase === 'sending') {
      setSendStatus('idle', '');
    }
    el.send.disabled = false;
  }
};


el.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    el.send.click();
  }
});

el.applyBg.onclick = () => {
  applyBackground(el.bgUrl.value);
};

el.clearBg.onclick = () => {
  el.bgUrl.value = '';
  applyBackground('');
};

el.bgFile.onchange = async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    alert('请选择图片文件');
    return;
  }
  try {
    const data = await api.uploadBackground(file);
    el.bgUrl.value = data.url;
    applyBackground(data.url);
  } catch (err) {
    alert('背景图片上传失败: ' + err.message);
  }
  e.target.value = '';
};

(async function init() {
  bindMessagesAutoScrollTracking();
  applySidebarCollapsed(localStorage.getItem('sidebar_collapsed') === '1');
  applyPanelCollapsed(el.bgPanel, el.toggleBgPanel, 'bg_panel_collapsed', localStorage.getItem('bg_panel_collapsed') === '1');
  applyPanelCollapsed(
    el.systemPanel,
    el.toggleSystemPanel,
    'system_panel_collapsed',
    localStorage.getItem('system_panel_collapsed') === '1'
  );

  const saved = localStorage.getItem('chat_bg');
  if (saved) {
    el.bgUrl.value = saved;
    applyBackground(saved);
  }
  renderPending();
  await refreshModels();
  await refreshSystemPrompt();
  await refreshConversations();
})();
