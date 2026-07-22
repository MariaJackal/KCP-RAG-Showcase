/** Login / logout and token management. */

import { apiJson } from './api.js';
import { $, on } from './utils.js';

let _onLoginSuccess = null;
let _onLogout = null;

export function initAuth({ onLogin, onLogoutCb }) {
  _onLoginSuccess = onLogin;
  _onLogout = onLogoutCb;
  on($('#logout-btn'), 'click', logout);
  window.addEventListener('auth:logout', logout);
  _initPasswordForm();
}

export function isLoggedIn() {
  return !!localStorage.getItem('token');
}

export function getRole() {
  return localStorage.getItem('role') || 'user';
}

function _initPasswordForm() {
  const form = $('#login-form');
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const errorEl = $('#login-error');
    errorEl.hidden = true;

    const password = $('#password-input').value;
    const loginBtn = $('#login-btn');
    loginBtn.disabled = true;

    try {
      // 直接用原生 fetch，避開 apiFetch 的 401 自動登出攔截
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) {
        errorEl.textContent = '密碼錯誤，請再試一次。';
        errorEl.hidden = false;
        return;
      }
      const data = await res.json();
      localStorage.setItem('token', data.token);
      localStorage.setItem('role', data.role);
      $('#password-input').value = '';
      if (_onLoginSuccess) _onLoginSuccess();
    } catch (_) {
      errorEl.textContent = '登入失敗，請重新整理後再試。';
      errorEl.hidden = false;
    } finally {
      loginBtn.disabled = false;
    }
  });
}

function logout() {
  localStorage.removeItem('token');
  localStorage.removeItem('role');
  if (_onLogout) _onLogout();
}
