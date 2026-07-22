/** Chat message display and SSE streaming. */

import { apiFetch, apiJson } from './api.js';
import { $, clearChildren, renderMarkdown, show, hide } from './utils.js';

let _cachedPresets = null;

async function _loadPresets() {
  if (_cachedPresets) return _cachedPresets;
  try {
    _cachedPresets = await apiJson('/conversations/presets');
  } catch (_) {
    _cachedPresets = [];
  }
  return _cachedPresets;
}

let currentConvId = null;
let isStreaming = false;
let abortController = null;  // 串流期間的 fetch 中斷控制器；「停止生成」時 abort

export function setCurrentConversation(convId) {
  currentConvId = convId;
}

export function getCurrentConversation() {
  return currentConvId;
}

export function isCurrentlyStreaming() {
  return isStreaming;
}

function updateInputWrapperState() {
  const chatArea = document.querySelector('.chat-area');
  const messages = document.querySelector('#messages');
  const hasMessages = messages ? messages.querySelector('.message') !== null : false;
  if (chatArea) chatArea.classList.toggle('chat-area--welcome', !hasMessages);
}

export async function loadMessages(convId) {
  const container = $('#messages');
  clearChildren(container);

  if (!convId) {
    await _renderWelcome();
    updateInputWrapperState();
    return;
  }

  try {
    const messages = await apiJson(`/conversations/${convId}/messages`);
    if (messages.length === 0) {
      await _renderWelcome();
      updateInputWrapperState();
      return;
    }
    messages.forEach((msg, i) => {
      appendMessage(msg.role, msg.content, {
        convId, index: i, ts: msg.timestamp, rating: msg.rating || '',
      });
    });
    scrollToBottom();
  } catch (err) {
    console.error('Failed to load messages:', err);
  }
  updateInputWrapperState();
}

async function _renderWelcome() {
  const grid = $('#suggestions-grid');
  if (!grid) return;
  clearChildren(grid);
  const presets = await _loadPresets();
  for (const p of presets) {
    const btn = document.createElement('button');
    btn.className = 'preset-btn';
    btn.textContent = p.label;
    btn.addEventListener('click', () => sendPreset(p.id));
    grid.appendChild(btn);
  }
}

export async function initPresetSelector() {
  const sel = $('#preset-select');
  if (!sel || sel.dataset.ready) return;
  const presets = await _loadPresets();
  for (const p of presets) {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.label;
    sel.appendChild(opt);
  }
  sel.addEventListener('change', () => {
    const id = sel.value;
    sel.value = '';
    if (id) sendPreset(id);
  });
  sel.dataset.ready = '1';
}

export async function sendPreset(presetId) {
  if (!currentConvId || isStreaming) return;
  const startedConvId = currentConvId;

  try {
    const data = await apiJson(`/conversations/${startedConvId}/preset`, {
      method: 'POST',
      body: { preset_id: presetId },
    });
    // 使用者已切到別的對話：訊息已存在後端，切回時由 loadMessages 載入
    if (currentConvId !== startedConvId) return;
    appendMessage('user', data.question);
    appendMessage('assistant', data.answer, {
      convId: startedConvId,
      index: data.assistant_index,
      ts: data.assistant_ts,
    });
  } catch (err) {
    if (currentConvId === startedConvId) {
      appendMessage('assistant', '發生錯誤，請稍後再試。');
    }
    console.error('Preset error:', err);
  }
}

export function appendMessage(role, content, opts = {}) {
  const div = document.createElement('div');
  div.className = `message ${role}`;

  const roleLabel = role === 'user' ? '你' : '系統';
  const bubbleContent = role === 'assistant' ? renderMarkdown(content) : escapeForBubble(content);
  const disclaimerHtml = role === 'assistant'
    ? '<div class="message-disclaimer">⚠ 本回答由 AI 生成，僅供參考；請以原始法規與主管機關公告為準。</div>'
    : '';

  div.innerHTML = `
    <span class="message-role">${roleLabel}</span>
    <div class="message-bubble msg-content">${bubbleContent}</div>
    ${disclaimerHtml}
  `;
  // 只有後端存過的 assistant 訊息才有評分列（錯誤氣泡等未存檔訊息沒有 index）
  if (role === 'assistant' && Number.isInteger(opts.index) && opts.convId) {
    attachRatingRow(div, opts.convId, opts.index, opts.ts || null, opts.rating || '');
  }
  $('#messages').appendChild(div);
  updateInputWrapperState();
  scrollToBottom();
  return div;
}

const _RATING_ICONS = {
  up: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg>',
  down: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/></svg>',
};

export function attachRatingRow(messageDiv, convId, messageIndex, messageTs, currentRating) {
  const row = document.createElement('div');
  row.className = 'message-rating';

  for (const rating of ['up', 'down']) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'rating-btn';
    btn.dataset.rating = rating;
    btn.title = rating === 'up' ? '這則回答有幫助' : '這則回答有問題';
    btn.setAttribute('aria-pressed', String(currentRating === rating));
    if (currentRating === rating) btn.classList.add('selected');
    btn.innerHTML = _RATING_ICONS[rating];
    btn.addEventListener('click', () => submitRating(row, convId, messageIndex, messageTs, rating));
    row.appendChild(btn);
  }
  messageDiv.appendChild(row);
  return row;
}

async function submitRating(row, convId, messageIndex, messageTs, rating) {
  const buttons = row.querySelectorAll('.rating-btn');
  const clicked = row.querySelector(`.rating-btn[data-rating="${rating}"]`);
  if (clicked.classList.contains('selected')) return; // 重複點同一邊：不重送
  buttons.forEach((b) => (b.disabled = true));
  try {
    const { apiJson } = await import('./api.js');
    await apiJson('/feedback/rating', {
      method: 'POST',
      body: { conv_id: convId, message_index: messageIndex, message_ts: messageTs, rating },
    });
    buttons.forEach((b) => {
      const selected = b.dataset.rating === rating;
      b.classList.toggle('selected', selected);
      b.setAttribute('aria-pressed', String(selected));
    });
  } catch (err) {
    console.error('Rating failed:', err);
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

function escapeForBubble(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

export async function sendMessage(question) {
  if (!currentConvId || isStreaming) return;
  if (!question.trim()) return;

  // 鎖定發問當下的對話；串流期間使用者可能切換對話，
  // 所有 DOM 更新都必須確認畫面仍停在這個對話，否則答案會渲染進錯的對話
  const startedConvId = currentConvId;
  const onConv = () => currentConvId === startedConvId;

  appendMessage('user', question);

  // Show progress indicator
  const progressEl = document.createElement('div');
  progressEl.className = 'progress-indicator';
  progressEl.id = 'progress';
  progressEl.innerHTML = '<div class="progress-dot"></div><span>思考中...</span>';
  $('#messages').appendChild(progressEl);
  scrollToBottom();

  isStreaming = true;
  abortController = new AbortController();
  updateSendButton();

  // try 外宣告，讓 abort/error 的 catch 也能存取已串流內容
  let streamingBubble = null;
  let streamedText = '';

  try {
    const res = await apiFetch(`/conversations/${currentConvId}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
      signal: abortController.signal,
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let assistantDiv = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep incomplete line

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const jsonStr = line.slice(6).trim();
        if (!jsonStr) continue;

        try {
          const event = JSON.parse(jsonStr);

          if (event.type === 'progress') {
            if (onConv()) {
              // 切走再切回時 progress 元件已被清掉，重新掛回
              if (!progressEl.isConnected && !streamingBubble) {
                $('#messages').appendChild(progressEl);
              }
              const progSpan = progressEl.querySelector('span');
              if (progSpan) {
                const text = event.eta_text
                  ? `${event.message}（${event.eta_text}）...`
                  : (event.message || '思考中...');
                progSpan.textContent = text;
              }
            }
          } else if (event.type === 'token') {
            streamedText += event.text;
            if (onConv()) {
              // 首個 token、或切走再切回導致 bubble 被清掉：重建 assistant bubble
              if (!streamingBubble || !streamingBubble.isConnected) {
                progressEl.remove();
                assistantDiv = appendMessage('assistant', '');
                streamingBubble = assistantDiv.querySelector('.msg-content');
              }
              // 串流期間即時渲染 Markdown（done 不再「跳一下」）。
              // renderMarkdown 對未收尾的語法（如尚未閉合的 **）容錯：當普通文字處理，
              // 收尾後自然成形，故中途渲染安全。
              streamingBubble.innerHTML = renderMarkdown(streamedText);
              scrollToBottom();
            }
          } else if (event.type === 'done') {
            progressEl.remove();
            if (onConv()) {
              if (streamingBubble && streamingBubble.isConnected) {
                // Re-render with markdown now that streaming is complete
                streamingBubble.innerHTML = renderMarkdown(event.answer);
                if (Number.isInteger(event.message_index)) {
                  attachRatingRow(assistantDiv, startedConvId, event.message_index, event.message_ts || null, '');
                }
              } else {
                // No tokens streamed (e.g. cache hit) or bubble was cleared: show full answer
                assistantDiv = appendMessage('assistant', event.answer, {
                  convId: startedConvId,
                  index: event.message_index,
                  ts: event.message_ts,
                });
              }
              scrollToBottom();
            }
            // 不在原對話：答案已由後端存入原對話，切回時由 loadMessages 載入
          } else if (event.type === 'error') {
            progressEl.remove();
            if (onConv()) {
              appendMessage('assistant', event.message || '發生錯誤，請稍後再試。');
            }
          }
        } catch {
          // ignore malformed JSON
        }
      }
    }
  } catch (err) {
    const prog = document.getElementById('progress');
    if (prog) prog.remove();
    if (err.name === 'AbortError') {
      // 使用者按「停止生成」：保留已串流內容，補上中斷標示；不視為錯誤。
      // 註：後端 pipeline 執行緒仍會跑完（同步 Gemini 呼叫無法中途中斷），
      // 此處僅停止前端顯示；答案不會存入對話（done 事件未送達）。
      if (onConv() && streamingBubble && streamingBubble.isConnected) {
        streamingBubble.innerHTML = renderMarkdown(streamedText + '\n\n_（已停止生成）_');
        scrollToBottom();
      }
    } else {
      if (onConv()) {
        appendMessage('assistant', '連線錯誤，請稍後再試。');
      }
      console.error('SSE error:', err);
    }
  } finally {
    isStreaming = false;
    abortController = null;
    updateSendButton();
  }
}

function updateSendButton() {
  const btn = $('#send-btn');
  const input = $('#chat-input');
  if (isStreaming) {
    // 串流期間按鈕變「停止生成」，永遠可點
    btn.textContent = '停止';
    btn.classList.add('btn-stop');
    btn.disabled = false;
  } else {
    btn.textContent = '傳送';
    btn.classList.remove('btn-stop');
    btn.disabled = !input.value.trim();
  }
}

function stopStreaming() {
  if (abortController) {
    abortController.abort();
  }
}

function scrollToBottom() {
  const container = $('#messages');
  container.scrollTop = container.scrollHeight;
}

export function initChat() {
  const form = $('#chat-form');
  const input = $('#chat-input');
  const btn = $('#send-btn');

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    // 串流期間按鈕為「停止」：攔截並中斷，不送新訊息
    if (isStreaming) {
      stopStreaming();
      return;
    }
    const q = input.value.trim();
    if (!q) return;
    input.value = '';
    input.style.height = 'auto';
    btn.disabled = true;
    sendMessage(q);
  });

  input.addEventListener('input', () => {
    // Auto-resize textarea
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 200) + 'px';
    updateSendButton();
  });

  // Enter to send, Shift+Enter for newline（串流期間 Enter 不動作，避免打字誤停）
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (isStreaming) return;
      form.dispatchEvent(new Event('submit'));
    }
  });
}
