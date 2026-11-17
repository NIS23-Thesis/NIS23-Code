/* Visual theme: visual-only state; no emulation or FLAME configuration changes. */
(() => {
  'use strict';
  const STORAGE_KEY = 'clive-color-theme-v1';
  const params = new URLSearchParams(window.location.search);
  const incoming = params.get('theme');
  let stored = null;
  try { stored = window.localStorage.getItem(STORAGE_KEY); } catch (_) { /* private browsing */ }
  const theme = (incoming === 'dark' || incoming === 'light') ? incoming :
    (stored === 'dark' || stored === 'light') ? stored : 'dark';
  let current = theme;
  function save(value) {
    try { window.localStorage.setItem(STORAGE_KEY, value); } catch (_) { /* storage optional */ }
  }
  function syncButton() {
    const button = document.getElementById('cliveThemeToggle');
    if (!button) return;
    const light = current === 'light';
    button.setAttribute('aria-pressed', String(light));
    button.setAttribute('aria-label', light ? 'Switch to dark mode' : 'Switch to light mode');
    button.setAttribute('title', light ? 'Switch to dark mode' : 'Switch to light mode');
    button.querySelector('.theme-icon').textContent = light ? '☾' : '☀';
    button.querySelector('.theme-label').textContent = light ? 'Dark mode' : 'Light mode';
  }
  function apply(value, announce = false) {
    current = value;
    document.documentElement.dataset.theme = value;
    document.documentElement.style.colorScheme = value;
    save(value);
    syncButton();
    if (announce) window.dispatchEvent(new CustomEvent('clive-theme-change', {detail:{theme:value}}));
  }
  apply(theme);
  document.addEventListener('DOMContentLoaded', () => {
    syncButton();
    document.getElementById('cliveThemeToggle')?.addEventListener('click', () => {
      apply(current === 'light' ? 'dark' : 'light', true);
    });
  });
  // Studio and the standalone visualizer may use different local ports.
  // Keep the user's chosen mode when navigating between those origins.
  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target.closest('a[href]') : null;
    if (!target || target.hasAttribute('download')) return;
    let url;
    try { url = new URL(target.href, location.href); } catch (_) { return; }
    if (!/^https?:$/.test(url.protocol) || url.hostname !== location.hostname || url.port === location.port) return;
    url.searchParams.set('theme', current);
    target.href = url.href;
  }, true);
  window.CLIVETheme = Object.freeze({
    get: () => current,
    isLight: () => current === 'light',
    set: (value) => { if (value === 'light' || value === 'dark') apply(value, true); },
    canvas: (dark, light) => current === 'light' ? light : dark,
  });
})();
