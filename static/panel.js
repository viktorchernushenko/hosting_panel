(function () {
  const body = document.body;
  const open = document.querySelector('[data-nav-open]');
  const closeTargets = document.querySelectorAll('[data-nav-close]');
  const sidebar = document.getElementById('app-navigation');
  let drawerReturnFocus = null;
  function openDrawer() {
    drawerReturnFocus = document.activeElement;
    body.classList.add('nav-open');
    if (sidebar) sidebar.setAttribute('aria-modal', 'true');
    const firstLink = sidebar && sidebar.querySelector('a,button');
    if (firstLink) firstLink.focus();
  }
  function closeDrawer() {
    body.classList.remove('nav-open');
    if (sidebar) sidebar.removeAttribute('aria-modal');
    if (drawerReturnFocus && typeof drawerReturnFocus.focus === 'function') drawerReturnFocus.focus();
    drawerReturnFocus = null;
  }
  if (open) open.addEventListener('click', openDrawer);
  closeTargets.forEach((item) => item.addEventListener('click', closeDrawer));
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
  const databaseModal = document.getElementById('database-create-modal');
  let databaseModalReturnFocus = null;
  function closeDatabaseModal() {
    if (!databaseModal) return;
    databaseModal.classList.remove('open'); databaseModal.setAttribute('aria-hidden', 'true'); databaseModal.setAttribute('inert', '');
    if (databaseModalReturnFocus) databaseModalReturnFocus.focus();
    databaseModalReturnFocus = null;
  }
  document.querySelectorAll('[data-open-database-modal]').forEach((button) => button.addEventListener('click', () => {
    if (!databaseModal) return;
    databaseModalReturnFocus = button; databaseModal.removeAttribute('inert'); databaseModal.setAttribute('aria-hidden', 'false'); databaseModal.classList.add('open');
    const first = databaseModal.querySelector('select,input:not([type=hidden]),button'); if (first) first.focus();
  }));
  document.querySelectorAll('[data-close-database-modal]').forEach((button) => button.addEventListener('click', closeDatabaseModal));
  if (databaseModal) databaseModal.addEventListener('click', (event) => { if (event.target === databaseModal) closeDatabaseModal(); });
  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const value = button.dataset.copy || '';
      try { await navigator.clipboard.writeText(value); toast(button.dataset.copied || 'Copied'); }
      catch (_) { toast(button.dataset.copyError || 'Copy failed'); }
    });
  });
  const search = document.querySelector('[data-list-search]');
  const filters = Array.from(document.querySelectorAll('[data-list-filter]'));
  function applyListFilters() {
    const query = search ? search.value.trim().toLowerCase() : '';
    document.querySelectorAll('[data-list-item]').forEach((item) => {
      const matchesSearch = !query || (item.dataset.search || '').toLowerCase().includes(query);
      const matchesFilters = filters.every((filter) => {
        const value = filter.value;
        const key = filter.dataset.listFilter || 'filter';
        return !value || item.dataset[key] === value;
      });
      item.hidden = !(matchesSearch && matchesFilters);
    });
  }
  if (search) search.addEventListener('input', applyListFilters);
  filters.forEach((filter) => filter.addEventListener('change', applyListFilters));
  const logSearch = document.querySelector('[data-log-search]');
  if (logSearch) logSearch.addEventListener('input', () => { const query=logSearch.value.toLowerCase(); document.querySelectorAll('.log-line').forEach(line => line.hidden = !line.textContent.toLowerCase().includes(query)); });
  const copyLog = document.querySelector('[data-copy-log]');
  if (copyLog) copyLog.addEventListener('click', async () => { const text=[...document.querySelectorAll('.log-line:not([hidden])')].map(line=>line.textContent).join('\n'); try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (_) { toast('Copy failed'); } });
  const confirmDialog = document.getElementById('confirm-dialog');
  const confirmText = document.getElementById('confirm-dialog-text');
  const confirmAccept = document.getElementById('confirm-dialog-accept');
  let pendingForm = null;
  let pendingAction = null;
  window.myhConfirm = (message, action) => {
    if (!confirmDialog || typeof action !== 'function') return;
    pendingForm = null; pendingAction = action;
    if (confirmText) confirmText.textContent = message;
    confirmDialog.showModal();
  };
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (form.dataset.confirmed === 'true' || !confirmDialog) return;
      event.preventDefault(); pendingForm = form;
      if (confirmText) confirmText.textContent = form.dataset.confirm;
      confirmDialog.showModal();
    });
  });
  if (confirmAccept) confirmAccept.addEventListener('click', () => {
    if (pendingForm) {
      pendingForm.dataset.confirmed = 'true'; confirmDialog.close(); pendingForm.requestSubmit();
    } else if (pendingAction) {
      const action = pendingAction; confirmDialog.close(); action();
    }
  });
  document.querySelectorAll('[data-confirm-cancel]').forEach((button) => button.addEventListener('click', () => confirmDialog && confirmDialog.close()));
  if (confirmDialog) confirmDialog.addEventListener('close', () => { pendingForm = null; pendingAction = null; });
  document.querySelectorAll('form').forEach((form) => form.addEventListener('submit', () => {
    if (form.dataset.confirm && form.dataset.confirmed !== 'true') return;
    const button = form.querySelector('button[type="submit"]');
    if (!button || button.disabled) return;
    button.disabled = true; button.dataset.originalText = button.textContent; button.textContent = button.dataset.loadingText || (document.documentElement.lang === 'en' ? 'Working…' : 'Виконується…');
  }));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && databaseModal && databaseModal.classList.contains('open')) closeDatabaseModal();
    else if (event.key === 'Escape' && body.classList.contains('nav-open')) closeDrawer();
    if (event.key === 'Tab' && body.classList.contains('nav-open') && sidebar) {
      const focusable = Array.from(sidebar.querySelectorAll('a,button:not([disabled])'));
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
})();
