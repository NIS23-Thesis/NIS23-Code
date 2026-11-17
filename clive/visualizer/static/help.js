'use strict';
// Presentation-friendly explanations live in contextual, on-demand dialogs.
// No network requests, telemetry changes, or FLAME runtime modifications.
(() => {
  const triggers = document.querySelectorAll('button[data-help]');
  if (!triggers.length) return;
  const dialog = document.createElement('dialog');
  dialog.className = 'clive-help-dialog';
  dialog.setAttribute('aria-label', 'Help');
  dialog.innerHTML = '<div class="clive-help-top"><span class="clive-help-label">CLIVE / HELP</span><button type="button" class="clive-help-close" aria-label="Close help">×</button></div><div class="clive-help-content"></div>';
  document.body.append(dialog);
  const content = dialog.querySelector('.clive-help-content');
  const close = dialog.querySelector('.clive-help-close');
  let opener = null;
  const dismiss = () => dialog.close();
  close.addEventListener('click', dismiss);
  dialog.addEventListener('click', event => { if (event.target === dialog) dismiss(); });
  dialog.addEventListener('close', () => { opener?.focus(); opener = null; });
  triggers.forEach(button => button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    const template = document.getElementById(`help-${button.dataset.help}`);
    if (!template) return;
    opener = button;
    content.replaceChildren(template.content.cloneNode(true));
    dialog.showModal();
    close.focus();
  }));
})();
