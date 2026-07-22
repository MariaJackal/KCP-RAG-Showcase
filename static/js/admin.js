/** Admin: data management — CSV exports with browser-local history + 回饋審核. */

import { apiFetch } from './api.js';
import { $ } from './utils.js';

const REVIEW_PAGE_SIZE = 10;

const EXPORTS = {
  questions:     { endpoint: '/questions/export',     prefix: 'questions_export',     label: '提問記錄' },
  feedback:      { endpoint: '/feedback/export',      prefix: 'feedback_export',      label: '意見回饋' },
  conversations: { endpoint: '/conversations/export', prefix: 'conversations_export', label: '對話記錄' },
};

const HISTORY_KEY = 'export_history';
const HISTORY_MAX = 30;

export function initAdmin() {
  const backBtn = $('#admin-back-btn');
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      window.dispatchEvent(new Event('admin:back'));
    });
  }

  const bindings = [
    ['#export-questions-btn', 'questions'],
    ['#export-feedback-btn', 'feedback'],
    ['#export-conversations-btn', 'conversations'],
  ];
  for (const [selector, kind] of bindings) {
    const btn = $(selector);
    if (btn) btn.addEventListener('click', () => exportCsv(kind, btn));
  }

  renderHistory();
  initTabs();
  initReview();
}

// ── 分頁籤 ──────────────────────────────────────────────────────────

function initTabs() {
  const tabExport = $('#admin-tab-export');
  const tabReview = $('#admin-tab-review');
  const secExport = $('#admin-export-section');
  const secReview = $('#admin-review-section');
  if (!tabExport || !tabReview) return;

  function activate(tab) {
    const exportActive = tab === 'export';
    tabExport.classList.toggle('active', exportActive);
    tabReview.classList.toggle('active', !exportActive);
    tabExport.setAttribute('aria-selected', String(exportActive));
    tabReview.setAttribute('aria-selected', String(!exportActive));
    secExport.hidden = !exportActive;
    secReview.hidden = exportActive;
    if (!exportActive && !reviewState.loaded) loadReviewRecords();
  }

  tabExport.addEventListener('click', () => activate('export'));
  tabReview.addEventListener('click', () => activate('review'));
}

// ── 回饋審核 ────────────────────────────────────────────────────────

const reviewState = { page: 1, total: 0, loaded: false };

function initReview() {
  const typeFilter = $('#review-type-filter');
  const unreviewedOnly = $('#review-unreviewed-only');
  const refreshBtn = $('#review-refresh-btn');
  if (!typeFilter) return;

  const reload = () => { reviewState.page = 1; loadReviewRecords(); };
  typeFilter.addEventListener('change', reload);
  unreviewedOnly.addEventListener('change', reload);
  refreshBtn.addEventListener('click', () => loadReviewRecords());

  $('#review-prev-btn').addEventListener('click', () => {
    if (reviewState.page > 1) { reviewState.page--; loadReviewRecords(); }
  });
  $('#review-next-btn').addEventListener('click', () => {
    const maxPage = Math.max(1, Math.ceil(reviewState.total / REVIEW_PAGE_SIZE));
    if (reviewState.page < maxPage) { reviewState.page++; loadReviewRecords(); }
  });
}

async function loadReviewRecords() {
  const list = $('#review-list');
  const empty = $('#review-empty');
  list.textContent = '載入中…';

  const params = new URLSearchParams({
    page: String(reviewState.page),
    page_size: String(REVIEW_PAGE_SIZE),
  });
  const type = $('#review-type-filter').value;
  if (type) params.set('type', type);
  if ($('#review-unreviewed-only').checked) params.set('unreviewed', 'true');

  try {
    const res = await apiFetch(`/feedback/admin/records?${params}`);
    if (!res.ok) {
      let message = `HTTP ${res.status}`;
      try {
        const json = await res.json();
        if (json.detail) message = json.detail;
      } catch (_) { /* not JSON */ }
      list.textContent = '';
      showReviewStatus(`載入失敗: ${message}`, 'error');
      return;
    }
    const data = await res.json();
    reviewState.total = data.total;
    reviewState.loaded = true;
    renderReviewList(data.items);
    empty.hidden = data.total > 0;
    updatePagination();
  } catch (err) {
    list.textContent = '';
    showReviewStatus(`載入失敗: ${err.message}`, 'error');
  }
}

function updatePagination() {
  const maxPage = Math.max(1, Math.ceil(reviewState.total / REVIEW_PAGE_SIZE));
  $('#review-prev-btn').disabled = reviewState.page <= 1;
  $('#review-next-btn').disabled = reviewState.page >= maxPage;
  $('#review-page-info').textContent =
    `第 ${reviewState.page} / ${maxPage} 頁（共 ${reviewState.total} 筆）`;
}

function renderReviewList(items) {
  const list = $('#review-list');
  list.textContent = '';
  for (const item of items) list.appendChild(buildReviewCard(item));
}

function buildReviewCard(item) {
  const card = document.createElement('div');
  card.className = 'review-card';

  // 摘要列（點擊展開）
  const summary = document.createElement('button');
  summary.className = 'review-card-summary';
  summary.type = 'button';

  const chip = document.createElement('span');
  chip.className = `review-type-chip ${item.type === '倒讚' ? 'down' : ''}`;
  chip.textContent = item.type;
  summary.appendChild(chip);

  const time = document.createElement('span');
  time.className = 'review-card-time';
  time.textContent = (item.ts || '').slice(0, 16).replace('T', ' ');
  summary.appendChild(time);

  const q = document.createElement('span');
  q.className = 'review-card-question';
  q.textContent = item.question || item.content || '（無問題內容）';
  summary.appendChild(q);

  const badge = document.createElement('span');
  badge.className = `review-badge ${item.review ? 'done' : ''}`;
  badge.textContent = item.review ? '已標註' : '未標註';
  summary.appendChild(badge);

  card.appendChild(summary);

  // 展開區：問答全文 + 標註表單
  const detail = document.createElement('div');
  detail.className = 'review-card-detail';
  detail.hidden = true;

  if (item.content) {
    detail.appendChild(buildDetailBlock('回饋內容', item.content));
  }
  detail.appendChild(buildDetailBlock('使用者問題', item.question || '（無）'));
  detail.appendChild(buildDetailBlock('系統答案', item.answer || '（無）'));
  detail.appendChild(buildReviewForm(item));

  card.appendChild(detail);

  summary.addEventListener('click', () => { detail.hidden = !detail.hidden; });
  return card;
}

function buildDetailBlock(label, text) {
  const block = document.createElement('div');
  block.className = 'review-detail-block';
  const h = document.createElement('div');
  h.className = 'review-detail-label';
  h.textContent = label;
  block.appendChild(h);
  const body = document.createElement('div');
  body.className = 'review-detail-text';
  body.textContent = text;
  block.appendChild(body);
  return block;
}

function buildReviewForm(item) {
  const form = document.createElement('div');
  form.className = 'review-form';

  const h = document.createElement('div');
  h.className = 'review-detail-label';
  h.textContent = '標註（匯出 golden set 候選用）';
  form.appendChild(h);

  const laws = document.createElement('textarea');
  laws.className = 'review-input review-laws-input';
  laws.placeholder = '正確法條，一行一條：法規名稱 第N條\n例：道路交通管理處罰條例 第35條';
  laws.rows = 2;
  laws.value = item.review?.correct_laws || '';
  form.appendChild(laws);

  const row = document.createElement('div');
  row.className = 'review-form-row';

  const category = document.createElement('input');
  category.className = 'review-input';
  category.placeholder = '分類（如：酒駕）';
  category.value = item.review?.category || '';
  row.appendChild(category);

  const note = document.createElement('input');
  note.className = 'review-input';
  note.placeholder = '備註';
  note.value = item.review?.note || '';
  row.appendChild(note);

  const saveBtn = document.createElement('button');
  saveBtn.className = 'btn btn-secondary';
  saveBtn.textContent = '儲存標註';
  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    try {
      const res = await apiFetch('/feedback/admin/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          record_id: item.record_id,
          correct_laws: laws.value.trim(),
          category: category.value.trim(),
          note: note.value.trim(),
        }),
      });
      if (!res.ok) {
        let message = `HTTP ${res.status}`;
        try {
          const json = await res.json();
          if (json.detail) message = json.detail;
        } catch (_) { /* not JSON */ }
        showReviewStatus(`儲存失敗: ${message}`, 'error');
        return;
      }
      showReviewStatus('標註已儲存', 'success');
      const badge = form.closest('.review-card').querySelector('.review-badge');
      badge.textContent = '已標註';
      badge.classList.add('done');
    } catch (err) {
      showReviewStatus(`儲存失敗: ${err.message}`, 'error');
    } finally {
      saveBtn.disabled = false;
    }
  });
  row.appendChild(saveBtn);

  form.appendChild(row);
  return form;
}

function showReviewStatus(message, type) {
  const el = $('#review-status');
  if (!el) return;
  el.textContent = message;
  el.className = 'upload-status';
  if (type) el.classList.add(type);
  el.hidden = false;
  if (type === 'success') {
    setTimeout(() => { el.hidden = true; }, 5000);
  }
}

async function exportCsv(kind, btn) {
  const cfg = EXPORTS[kind];
  btn.disabled = true;

  const today = new Date().toISOString().slice(0, 10);
  const filename = `${cfg.prefix}_${today}.csv`;

  try {
    const res = await apiFetch(cfg.endpoint);
    if (!res.ok) {
      const text = await res.text();
      let message = `HTTP ${res.status}`;
      try {
        const json = JSON.parse(text);
        if (json.detail) message = json.detail;
      } catch (_) { /* not JSON */ }
      showStatus(`匯出失敗: ${message}`, 'error');
      addHistory({ ts: Date.now(), label: cfg.label, filename, rows: null, size: null, ok: false });
      return;
    }

    const blob = await res.blob();
    const rows = countCsvRows(await blob.text());

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    showStatus('匯出完成', 'success');
    addHistory({ ts: Date.now(), label: cfg.label, filename, rows, size: blob.size, ok: true });
  } catch (err) {
    showStatus(`匯出失敗: ${err.message}`, 'error');
    addHistory({ ts: Date.now(), label: cfg.label, filename, rows: null, size: null, ok: false });
  } finally {
    btn.disabled = false;
  }
}

/** 引號感知的 CSV 資料列數（扣除標題列）；欄位內的換行不計為新列。 */
function countCsvRows(text) {
  let rows = 0;
  let inQuote = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') inQuote = !inQuote;
    else if (c === '\n' && !inQuote) rows++;
  }
  if (text.length && !text.endsWith('\n')) rows++;
  return Math.max(0, rows - 1);
}

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
  } catch (_) {
    return [];
  }
}

function addHistory(entry) {
  const list = [entry, ...loadHistory()].slice(0, HISTORY_MAX);
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
  } catch (_) { /* storage full/blocked — history is best-effort */ }
  renderHistory();
}

function formatTs(ts) {
  const d = new Date(ts);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatSize(bytes) {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderHistory() {
  const body = $('#export-history-body');
  const empty = $('#export-history-empty');
  if (!body) return;

  const list = loadHistory();
  body.textContent = '';
  if (empty) empty.hidden = list.length > 0;

  for (const e of list) {
    const tr = document.createElement('tr');

    const tdTime = document.createElement('td');
    tdTime.textContent = formatTs(e.ts);
    tr.appendChild(tdTime);

    const tdType = document.createElement('td');
    const chip = document.createElement('span');
    chip.className = 'export-type-chip';
    chip.textContent = e.label;
    tdType.appendChild(chip);
    tr.appendChild(tdType);

    const tdFile = document.createElement('td');
    tdFile.textContent = e.filename;
    tdFile.className = 'export-td-file';
    tr.appendChild(tdFile);

    const tdRows = document.createElement('td');
    tdRows.textContent = e.rows == null ? '—' : String(e.rows);
    tr.appendChild(tdRows);

    const tdSize = document.createElement('td');
    tdSize.textContent = formatSize(e.size);
    tr.appendChild(tdSize);

    const tdStatus = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = `export-badge ${e.ok ? 'success' : 'error'}`;
    badge.textContent = e.ok ? '已完成' : '失敗';
    tdStatus.appendChild(badge);
    tr.appendChild(tdStatus);

    body.appendChild(tr);
  }
}

function showStatus(message, type) {
  const el = $('#upload-status');
  if (!el) return;

  el.textContent = message;
  el.className = 'upload-status';
  if (type) el.classList.add(type);
  el.hidden = false;

  if (type === 'success') {
    setTimeout(() => { el.hidden = true; }, 5000);
  }
}
