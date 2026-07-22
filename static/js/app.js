/** Entry point - view routing and initialization. */

import { $, show, hide } from './utils.js';
import { initAuth, isLoggedIn, getRole } from './auth.js';
import { initChat, initPresetSelector } from './chat.js';
import { initSidebar, loadConversations } from './sidebar.js';
import { initPersonas } from './persona.js';
import { initAdmin } from './admin.js';

function showView(name) {
  hide($('#login-view'));
  hide($('#main-view'));
  hide($('#admin-view'));
  show($(`#${name}-view`));
}

async function enterMain() {
  showView('main');
  const adminLinkBtn = $('#admin-link-btn');
  if (adminLinkBtn) adminLinkBtn.hidden = getRole() !== 'admin';
  await initPersonas();
  await loadConversations();
  await initPresetSelector();
  $('#chat-input').focus();
}

function enterAdmin() {
  showView('admin');
}

function enterLogin() {
  showView('login');
}

// Initialize
initAuth({
  onLogin: () => {
    if (getRole() === 'admin') {
      enterAdmin();
    } else {
      enterMain();
    }
  },
  onLogoutCb: () => enterLogin(),
});

initChat();

initSidebar({
  onChanged: () => {},
});

initAdmin();

// 法規清單 modal
const lawListBtn = $('#law-list-btn');
const lawListModal = $('#law-list-modal');

if (lawListBtn) {
  lawListBtn.addEventListener('click', () => {
    lawListModal.hidden = false;
    document.body.classList.add('modal-open');
  });
}

if (lawListModal) {
  lawListModal.addEventListener('click', (e) => {
    if (e.target === lawListModal) {
      lawListModal.hidden = true;
      document.body.classList.remove('modal-open');
    }
  });
}

// 意見回饋 modal
const feedbackBtn   = $('#feedback-btn');
const feedbackModal = $('#feedback-modal');

function closeFeedbackModal() {
  feedbackModal.hidden = true;
  document.body.classList.remove('modal-open');
  $('#feedback-type').value = '';
  $('#feedback-content').value = '';
  const msg = $('#feedback-msg');
  msg.hidden = true;
  msg.className = 'feedback-msg';
}

function showFeedbackMsg(text, type) {
  const msg = $('#feedback-msg');
  msg.textContent = text;
  msg.className = `feedback-msg ${type}`;
  msg.hidden = false;
}

if (feedbackBtn) {
  feedbackBtn.addEventListener('click', () => {
    feedbackModal.hidden = false;
    document.body.classList.add('modal-open');
  });
}

if (feedbackModal) {
  feedbackModal.addEventListener('click', (e) => {
    if (e.target === feedbackModal) closeFeedbackModal();
  });
}

const feedbackCancelBtn = $('#feedback-cancel-btn');
if (feedbackCancelBtn) {
  feedbackCancelBtn.addEventListener('click', closeFeedbackModal);
}

const feedbackSubmitBtn = $('#feedback-submit-btn');
if (feedbackSubmitBtn) {
  feedbackSubmitBtn.addEventListener('click', async () => {
    const type    = $('#feedback-type').value;
    const content = $('#feedback-content').value.trim();

    if (!type)    { showFeedbackMsg('請選擇問題類型', 'error'); return; }
    if (!content) { showFeedbackMsg('請輸入回饋內容', 'error'); return; }

    feedbackSubmitBtn.disabled = true;
    try {
      const { apiFetch } = await import('./api.js');
      // 帶目前開啟的對話 id，後端自動附上該對話最後一組問答（無對話則為 null）
      const { getCurrentConversation } = await import('./chat.js');
      const res = await apiFetch('/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, content, conv_id: getCurrentConversation() }),
      });
      if (!res.ok) {
        const text = await res.text();
        let detail = `HTTP ${res.status}`;
        try { const j = JSON.parse(text); if (j.detail) detail = j.detail; } catch (_) {}
        showFeedbackMsg(`送出失敗：${detail}`, 'error');
        return;
      }
      showFeedbackMsg('感謝您的回饋！', 'success');
      setTimeout(closeFeedbackModal, 1200);
    } catch (e) {
      showFeedbackMsg(`送出失敗：${e.message}`, 'error');
    } finally {
      feedbackSubmitBtn.disabled = false;
    }
  });
}

// Admin back to chat
window.addEventListener('admin:back', () => enterMain());

// Chat → admin (admin only; button hidden for non-admin)
const adminLinkBtn = $('#admin-link-btn');
if (adminLinkBtn) {
  adminLinkBtn.addEventListener('click', () => enterAdmin());
}

// Mobile sidebar toggle
const sidebarToggle = $('#sidebar-toggle');
const sidebarEl = $('#sidebar');
const sidebarOverlay = $('#sidebar-overlay');

function closeSidebar() {
  if (sidebarEl) sidebarEl.classList.remove('open');
  if (sidebarOverlay) sidebarOverlay.classList.remove('active');
}

if (sidebarToggle) {
  sidebarToggle.addEventListener('click', () => {
    sidebarEl.classList.toggle('open');
    sidebarOverlay.classList.toggle('active');
  });
}

if (sidebarOverlay) {
  sidebarOverlay.addEventListener('click', closeSidebar);
}

// Close sidebar when a conversation is selected (mobile UX)
if (sidebarEl) {
  sidebarEl.addEventListener('click', (e) => {
    if (e.target.closest('.conversation-list li')) {
      closeSidebar();
    }
  });
}

// Check existing auth state on page load
if (isLoggedIn()) {
  if (getRole() === 'admin') {
    enterAdmin();
  } else {
    enterMain();
  }
} else {
  enterLogin();
}
