"use strict";
// Shared same-origin navigation and run-selection helpers.
(() => {
 const combined = location.pathname.startsWith('/topology/');
 const requested = new URLSearchParams(location.search).get('studio_port');
 const safePort = requested && /^\d{1,5}$/.test(requested) && Number(requested)>0 && Number(requested)<=65535 ? requested : '9767';
 const studioRoot = combined ? '/' : `${location.protocol}//${location.hostname}:${safePort}/`;
 for (const anchor of document.querySelectorAll('[data-studio-link]')) {
   anchor.href = studioRoot + (anchor.dataset.studioLink === 'history' ? '?view=history' : '');
 }
 const key = 'clive-selected-run-v1';
 window.CLIVERunSelection = {
   read() { try { return sessionStorage.getItem(key) || ''; } catch (_) { return ''; } },
   write(run) { try { if(run)sessionStorage.setItem(key,run); } catch (_) {} },
   label(run) { return run.name; },
 };
})();
