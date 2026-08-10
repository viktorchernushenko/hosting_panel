(function () {
  const body = document.body;
  const open = document.querySelector('[data-nav-open]');
  const closeTargets = document.querySelectorAll('[data-nav-close]');
  if (open) open.addEventListener('click', () => body.classList.add('nav-open'));
  closeTargets.forEach((item) => item.addEventListener('click', () => body.classList.remove('nav-open')));

  const region = document.querySelector('.toast-region');
  function toast(message) {
    if (!region) return;
    const item = document.createElement('div');
    item.className = 'ui-toast'; item.setAttribute('role', 'status'); item.textContent = message;
    region.appendChild(item); window.setTimeout(() => item.remove(), 2200);
  }
  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const value = button.dataset.copy || '';
      try { await navigator.clipboard.writeText(value); toast(button.dataset.copied || 'Copied'); }
      catch (_) { toast(button.dataset.copyError || 'Copy failed'); }
    });
  });
  const confirmDialog = document.getElementById('confirm-dialog');
  const confirmText = document.getElementById('confirm-dialog-text');
  const confirmAccept = document.getElementById('confirm-dialog-accept');
  let pendingForm = null;
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (form.dataset.confirmed === 'true' || !confirmDialog) return;
      event.preventDefault(); pendingForm = form;
      if (confirmText) confirmText.textContent = form.dataset.confirm;
      confirmDialog.showModal();
    });
  });
  if (confirmAccept) confirmAccept.addEventListener('click', () => {
    if (!pendingForm) return;
    pendingForm.dataset.confirmed = 'true'; confirmDialog.close(); pendingForm.requestSubmit();
  });
  document.querySelectorAll('[data-confirm-cancel]').forEach((button) => button.addEventListener('click', () => confirmDialog && confirmDialog.close()));
  if (confirmDialog) confirmDialog.addEventListener('close', () => { pendingForm = null; });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') body.classList.remove('nav-open');
  });
})();
