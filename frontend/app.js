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
  async streamChat(payload, onEvent) {
    const resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!resp.ok || !resp.body) throw new Error(await this._readError(resp, '流式请求失败'));

    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
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
  conversations: [],
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
  el.messages.scrollTop = el.messages.scrollHeight;
}

function parseLatencyMs(attachments) {
  const text = String(attachments || '');
  const m = text.match(/latency_ms=(\d+)/);
  if (!m) return 0;
  return Number(m[1] || 0);
}

function appendOrUpdateStreamingAssistant(text) {
  let div = el.messages.querySelector('.msg.assistant.streaming');
  if (!div) {
    div = document.createElement('div');
    div.className = 'msg assistant streaming';
    el.messages.appendChild(div);
  }
  renderAssistantContent(div, text);
  el.messages.scrollTop = el.messages.scrollHeight;
}

function ensureWaitingAssistant() {
  let waiting = el.messages.querySelector('.msg-block.assistant.waiting');
  if (waiting) return;
  waiting = document.createElement('div');
  waiting.className = 'msg-block assistant waiting';
  waiting.innerHTML = '<div class="msg assistant waiting-msg"><span class="dotting">消息已发送，思考中</span></div>';
  el.messages.appendChild(waiting);
  el.messages.scrollTop = el.messages.scrollHeight;
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
  el.messages.scrollTop = el.messages.scrollHeight;
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

  return {
    thoughts,
    main: rest.trim()
  };
}

function renderAssistantContent(container, rawText) {
  const { thoughts, main } = extractThinkingParts(rawText);
  container.innerHTML = '';

  thoughts.forEach((t, idx) => {
    const details = document.createElement('details');
    details.className = 'assistant-thought';
    if (idx === thoughts.length - 1) details.open = false;

    const summary = document.createElement('summary');
    summary.textContent = `思考过程 ${idx + 1}`;
    details.appendChild(summary);

    const body = document.createElement('div');
    body.className = 'assistant-thought-body';
    renderRichContent(body, t);
    details.appendChild(body);

    container.appendChild(details);
  });

  const mainDiv = document.createElement('div');
  mainDiv.className = 'assistant-main';
  renderRichContent(mainDiv, main || (thoughts.length > 0 ? '（已隐藏思考过程）' : ''));
  container.appendChild(mainDiv);
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

async function refreshConversations(forcedItems = null) {
  const items = forcedItems || await api.getConversations();
  state.conversations = items;
  el.convList.innerHTML = '';

  items.forEach(c => {
    const li = document.createElement('li');
    li.textContent = c.title || `会话 ${c.id}`;
    if (c.id === state.currentConversationId) li.classList.add('active');
    li.onclick = async () => {
      state.currentConversationId = c.id;
      await refreshConversations(state.conversations);
      const msgs = await api.getMessages(c.id);
      renderMessages(msgs);
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
  try {
    const data = await api.updateModel(model);
    state.selectedModel = data.model;
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

  try {
    setSendStatus('sending', '正在发送消息到服务器...');
    el.send.disabled = true;
    await ensureConversation();

    const existing = await api.getMessages(state.currentConversationId);
    renderMessages([...existing, { role: 'user', content: text }]);
    ensureWaitingAssistant();
    setWaitingAssistantText('消息已送达，等待模型响应');
    el.input.value = '';

    const payload = {
      conversation_id: state.currentConversationId,
      message: text,
      file_contexts: state.pendingFileItems.map(x => x.extracted_text),
      images: state.pendingImages,
      model: state.selectedModel || null,
    };

    if (el.streamToggle.checked) {
      let assembled = '';
      let streamError = '';
      await api.streamChat(payload, (evt) => {
        if (evt.event === 'meta') {
          setSendStatus('sending', `已发送，模型 ${evt.model || state.selectedModel || '默认'} 正在思考...`);
          return;
        }
        if (evt.event === 'delta') {
          removeWaitingAssistant();
          setSendStatus('sending', '模型回复中...');
          assembled += evt.delta || '';
          appendOrUpdateStreamingAssistant(assembled);
        } else if (evt.event === 'error') {
          streamError = evt.detail || '流式输出失败';
        }
      });
      if (streamError) throw new Error(streamError);
    } else {
      setWaitingAssistantText('消息已送达，等待完整回复');
      await api.sendChat(payload);
    }

    removeWaitingAssistant();
    try {
      const refreshed = await api.getMessages(state.currentConversationId);
      renderMessages(refreshed);
      await refreshConversations();
    } catch (refreshErr) {
      const refreshMsg = extractErrorMessage(refreshErr);
      setSendStatus('error', `回复已返回，但刷新消息列表失败：${refreshMsg}`);
      appendErrorMessage(`后处理失败：${refreshMsg}`);
    }

    state.pendingFileItems = [];
    state.pendingImages = [];
    setSendStatus('idle', '');
  } catch (err) {
    removeWaitingAssistant();
    const msg = extractErrorMessage(err);
    appendErrorMessage(`发送失败：${msg}`);
    setSendStatus('error', `发送失败：${msg}`);
  } finally {
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
