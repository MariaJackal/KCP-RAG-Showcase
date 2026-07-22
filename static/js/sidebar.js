/** Conversation list in sidebar. */

import { apiJson } from './api.js';
import { $, clearChildren } from './utils.js';
import { setCurrentConversation, loadMessages, getCurrentConversation } from './chat.js';

let conversations = [];
let onConversationChanged = null;

export function initSidebar({ onChanged }) {
  onConversationChanged = onChanged;
  $('#new-chat-btn').addEventListener('click', createConversation);
}

export async function loadConversations() {
  try {
    conversations = await apiJson('/conversations');
    renderList();
    // Select first conversation if none is active
    if (conversations.length > 0 && !getCurrentConversation()) {
      await selectConversation(conversations[0].id);
    } else if (conversations.length === 0) {
      await createConversation();
    }
  } catch (err) {
    console.error('Failed to load conversations:', err);
  }
}

export async function createConversation() {
  const personaId = $('#persona-select').value || 'traffic';
  try {
    const conv = await apiJson('/conversations', {
      method: 'POST',
      body: { persona_id: personaId },
    });
    conversations.unshift(conv);
    renderList();
    await selectConversation(conv.id);
  } catch (err) {
    console.error('Failed to create conversation:', err);
  }
}

async function selectConversation(convId) {
  setCurrentConversation(convId);
  highlightActive(convId);

  const conv = conversations.find(c => c.id === convId);
  if (conv) {
    $('#chat-title').textContent = conv.title;
    // Sync persona selector
    $('#persona-select').value = conv.persona_id;
  }

  await loadMessages(convId);
  if (onConversationChanged) onConversationChanged(convId);
}

async function deleteConversation(convId, e) {
  e.stopPropagation();
  if (conversations.length <= 1) return; // keep at least one

  try {
    await apiJson(`/conversations/${convId}`, { method: 'DELETE' });
    conversations = conversations.filter(c => c.id !== convId);
    renderList();

    if (getCurrentConversation() === convId) {
      if (conversations.length > 0) {
        await selectConversation(conversations[0].id);
      }
    }
  } catch (err) {
    console.error('Failed to delete conversation:', err);
  }
}

function renderList() {
  const list = $('#conversation-list');
  clearChildren(list);

  for (const conv of conversations) {
    const li = document.createElement('li');
    li.className = 'conv-item';
    if (conv.id === getCurrentConversation()) li.classList.add('active');
    li.dataset.id = conv.id;

    const titleSpan = document.createElement('span');
    titleSpan.className = 'conv-item-title';
    titleSpan.textContent = conv.title;

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'conv-item-delete';
    deleteBtn.textContent = '\u00d7';
    deleteBtn.title = '刪除';
    deleteBtn.addEventListener('click', (e) => deleteConversation(conv.id, e));

    li.appendChild(titleSpan);
    li.appendChild(deleteBtn);
    li.addEventListener('click', () => selectConversation(conv.id));
    list.appendChild(li);
  }
}

function highlightActive(convId) {
  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === convId);
  });
}

export async function refreshConversations() {
  try {
    conversations = await apiJson('/conversations');
    renderList();
  } catch (err) {
    console.error('Failed to refresh conversations:', err);
  }
}
