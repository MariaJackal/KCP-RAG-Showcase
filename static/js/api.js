/** Fetch wrapper with JWT auth header injection. */

const BASE = '/api';

function authHeaders() {
  const token = localStorage.getItem('token');
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

export async function apiFetch(path, opts = {}) {
  const url = BASE + path;
  const headers = { ...authHeaders(), ...(opts.headers || {}) };

  const res = await fetch(url, { ...opts, headers });

  if (res.status === 401) {
    localStorage.removeItem('token');
    localStorage.removeItem('role');
    window.dispatchEvent(new Event('auth:logout'));
    throw new Error('認證已過期，請重新登入');
  }

  return res;
}

export async function apiJson(path, opts = {}) {
  if (opts.body && typeof opts.body === 'object') {
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const res = await apiFetch(path, opts);
  if (!res.ok) {
    const text = await res.text();
    let message = text || `HTTP ${res.status}`;
    try {
      const json = JSON.parse(text);
      if (json.detail) message = json.detail;
    } catch (_) { /* not JSON, use raw text */ }
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
}
