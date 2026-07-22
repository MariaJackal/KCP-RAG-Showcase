/** Persona selector management. */

import { apiJson } from './api.js';
import { $ } from './utils.js';
import { getCurrentConversation } from './chat.js';

export async function initPersonas() {
  const select = $('#persona-select');

  try {
    const personas = await apiJson('/personas');
    select.innerHTML = '';
    for (const p of personas) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.display_name;
      select.appendChild(opt);
    }
  } catch (err) {
    console.error('Failed to load personas:', err);
  }

  select.addEventListener('change', async () => {
    const convId = getCurrentConversation();
    if (!convId) return;

    try {
      await apiJson(`/conversations/${convId}/persona`, {
        method: 'PATCH',
        body: { persona_id: select.value },
      });
    } catch (err) {
      console.error('Failed to update persona:', err);
    }
  });
}
