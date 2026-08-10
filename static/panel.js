(function () {
  const body = document.body;
  const open = document.querySelector('[data-nav-open]');
  const closeTargets = document.querySelectorAll('[data-nav-close]');
  if (open) open.addEventListener('click', () => body.classList.add('nav-open'));
  closeTargets.forEach((item) => item.addEventListener('click', () => body.classList.remove('nav-open')));
  const collapse = document.querySelector('[data-sidebar-collapse]');
  const collapseKey = 'myh.sidebar.collapsed';
  function syncSidebar() {
    const collapsed = localStorage.getItem(collapseKey) === '1';
    body.classList.toggle('sidebar-collapsed', collapsed);
    document.querySelectorAll('.nav-link').forEach((link) => link.title = collapsed ? (link.textContent || '').trim() : '');
  }
  if (collapse) collapse.addEventListener('click', () => { localStorage.setItem(collapseKey, body.classList.contains('sidebar-collapsed') ? '0' : '1'); syncSidebar(); });
  syncSidebar();

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
  const search = document.querySelector('[data-list-search]');
  const filter = document.querySelector('[data-list-filter]');
  function applyListFilters() {
    const query = search ? search.value.trim().toLowerCase() : '';
    const value = filter ? filter.value : '';
    document.querySelectorAll('[data-list-item]').forEach((item) => {
      const matchesSearch = !query || (item.dataset.search || '').toLowerCase().includes(query);
      const matchesFilter = !value || item.dataset.filter === value;
      item.hidden = !(matchesSearch && matchesFilter);
    });
  }
  if (search) search.addEventListener('input', applyListFilters);
  if (filter) filter.addEventListener('change', applyListFilters);
  const logSearch = document.querySelector('[data-log-search]');
  if (logSearch) logSearch.addEventListener('input', () => { const query=logSearch.value.toLowerCase(); document.querySelectorAll('.log-line').forEach(line => line.hidden = !line.textContent.toLowerCase().includes(query)); });
  const copyLog = document.querySelector('[data-copy-log]');
  if (copyLog) copyLog.addEventListener('click', async () => { const text=[...document.querySelectorAll('.log-line:not([hidden])')].map(line=>line.textContent).join('\n'); try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (_) { toast('Copy failed'); } });
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
  document.querySelectorAll('form').forEach((form) => form.addEventListener('submit', () => {
    if (form.dataset.confirm && form.dataset.confirmed !== 'true') return;
    const button = form.querySelector('button[type="submit"]');
    if (!button || button.disabled) return;
    button.disabled = true; button.dataset.originalText = button.textContent; button.textContent = button.dataset.loadingText || (document.documentElement.lang === 'en' ? 'Working…' : 'Виконується…');
  }));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') body.classList.remove('nav-open');
  });
})();
