'use strict';
// Presentation enhancements use the existing API actions and live application state.
let transferFilter = 'all';
function matchesTransfer(job) {
  const query = $('queue-search').value.trim().toLowerCase();
  const statusMatches = transferFilter === 'all' || (transferFilter === 'pending'
    ? !['completed', 'cancelled', 'failed'].includes(job.state) : job.state === transferFilter);
  return statusMatches && String(job.name || job.source || '').toLowerCase().includes(query);
}
window.matchesTransfer = matchesTransfer;
window.filterTransferQueue = function () {
  if (!state) return;
  let count = 0;
  document.querySelectorAll('#queue-body tr[data-job-id]').forEach(tr => {
    const job = state.jobs.find(j => String(j.id) === tr.dataset.jobId);
    tr.hidden = !job || !matchesTransfer(job);
    if (!tr.hidden) count++;
  });
  $('queue-no-results').hidden = !state.jobs.length || count > 0;
};
window.updateExperience = function(s) {
  const openMenu = $('queue-action-menu');
  if (openMenu && !openMenu.hidden) {
    const menuJob = s.jobs.find(j => String(j.id) === openMenu.dataset.job);
    if (!menuJob || menuJob.state !== openMenu.dataset.jobState) window.closeQueueMenu?.();
  }
  const connected = s.connection.state === 'connected';
  const label = connected ? 'PS5 connected' : s.connection.state === 'error' ? 'Connection needs attention' : s.settings.host ? 'Connection not tested' : 'Set up your console';
  $('device-name').textContent = label;
  $('overview-connection').textContent = label;
  $('overview-destination').textContent = s.settings.folder || 'Choose a destination';
  $('overview-destination').title = s.settings.folder || '';
  const done = s.jobs.filter(j => j.state === 'completed').length;
  $('overview-completed').textContent = `${done} ${done === 1 ? 'transfer' : 'transfers'}`;
  $('welcome-panel').hidden = s.jobs.length > 0;
  $('transfer-launchpad').classList.toggle('has-transfers', s.jobs.length > 0);
  $('welcome-panel').querySelector('.welcome-copy p').textContent = connected ? 'Choose a file, folder or link. Review your destination, then start the queue.' : 'Connect your console to start sending files over your local network.';
  $('welcome-panel').querySelector('h2').innerHTML = connected ? 'Connected.<br>Ready for what’s next.' : 'One connection.<br>Everything within reach.';
  $('welcome-panel').querySelector('.welcome-kicker').textContent = connected ? 'READY TO TRANSFER' : 'GETTING STARTED';
  const setup = $('welcome-panel').querySelector('button');
  setup.firstChild.textContent = connected ? 'Check connection ' : 'Connect your console ';
  $('live-panel').hidden = !s.jobs.length;
  $('queue-tools').hidden = !s.jobs.length;
  document.querySelector('.queue-card').classList.toggle('is-empty', !s.jobs.length);
  $('queue-badge').hidden = !s.jobs.some(j => !['completed', 'cancelled'].includes(j.state));
};
document.querySelectorAll('[data-source]').forEach(button => {
  button.addEventListener('click', () => openAdd(button.dataset.source));
});
function changeQueueFilter() {
  // Avoid acting on selections that become invisible after searching or filtering.
  selectedJobs.clear();
  document.querySelectorAll('.row-select').forEach(input => { input.checked = false; });
  window.filterTransferQueue();
  updateBulkBar();
}
$('queue-search').addEventListener('input', changeQueueFilter);
document.querySelectorAll('[data-queue-filter]').forEach(button => {
  button.addEventListener('click', () => {
    transferFilter = button.dataset.queueFilter;
    document.querySelectorAll('[data-queue-filter]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    changeQueueFilter();
  });
});
$('help-open').addEventListener('click', () => $('guide-dialog').showModal());
let compact = false;
try { compact = localStorage.getItem('ps5-compact-view') === 'true'; } catch (_) {}
function setDensity(value) {
  compact = value;
  document.body.classList.toggle('compact', compact);
  $('density-toggle').setAttribute('aria-pressed', String(compact));
}
setDensity(compact);
$('density-toggle').addEventListener('click', () => {
  setDensity(!compact);
  try { localStorage.setItem('ps5-compact-view', String(compact)); } catch (_) {}
});
// Radio-like controls remain usable without a pointing device.
for (const selector of ['.settings-tabs-row [role=tab]', '.mode-toggle-group [role=radio]', '.strategy-selector-grid [role=radio]']) {
  const controls = [...document.querySelectorAll(selector)];
  controls.forEach((control, i) => control.addEventListener('keydown', e => {
    let next;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (i + 1) % controls.length;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (i + controls.length - 1) % controls.length;
    if (e.key === 'Home') next = 0;
    if (e.key === 'End') next = controls.length - 1;
    if (next !== undefined) { e.preventDefault(); controls[next].focus(); controls[next].click(); }
  }));
}
document.querySelectorAll('.nav-item').forEach(button => {
  if (button.classList.contains('selected')) button.setAttribute('aria-current', 'page');
});
document.querySelectorAll('[data-kind]').forEach(button => button.setAttribute('aria-pressed', String(button.classList.contains('selected'))));

// A single body-level menu avoids changing table layout or being clipped by it.
const queueMenu = $('queue-action-menu');
let queueMenuTrigger = null;
const queueActionPresentation = {
  up: ['Move up', 'Queue order', 'i-chevron-left'],
  down: ['Move down', 'Queue order', 'i-chevron-right'],
  merge_next: ['Merge with next part', 'Queue order', 'i-transfer'],
  edit: ['Update download link', 'Source', 'i-link'],
  set_extract_ps5: ['Extract on PS5', 'Archive handling', 'i-ps-storage'],
  set_extract_mac: ['Extract on this device', 'Archive handling', 'i-folder'],
  set_extract_none: ['Keep archive compressed', 'Archive handling', 'i-archive-crate'],
  prompt_password: ['Archive password…', 'Archive handling', 'i-key'],
  trigger_payload: ['Send extraction payload', 'Console tools', 'i-export'],
  view_unrar_log: ['View extraction log', 'Console tools', 'i-log'],
  view_error: ['View error details', 'Console tools', 'i-alert'],
  restart: ['Restart from beginning', 'Manage transfer', 'i-refresh'],
  cancel: ['Cancel transfer', 'Manage transfer', 'i-pause'],
  remove: ['Remove from queue', 'Manage transfer', 'i-close']
};
window.closeQueueMenu = function(restoreFocus = false) {
  queueMenu.hidden = true;
  const trigger = queueMenuTrigger;
  trigger?.setAttribute('aria-expanded', 'false');
  queueMenuTrigger = null;
  if (restoreFocus && trigger?.isConnected) trigger.focus();
};
function queueMenuItems() {
  return [...queueMenu.querySelectorAll('button[data-action]:not(:disabled)')];
}
function openQueueMenu(trigger) {
  if (queueMenuTrigger === trigger && !queueMenu.hidden) return window.closeQueueMenu(true);
  window.closeQueueMenu();
  const tr = trigger.closest('tr');
  const job = state?.jobs.find(j => String(j.id) === trigger.dataset.menuJob);
  const template = tr?.querySelector('template[data-menu-template]');
  if (!job || !template) return;
  queueMenu.replaceChildren();
  const title = document.createElement('div');
  title.className = 'queue-menu-title';
  const name = document.createElement('span');
  name.textContent = job.name;
  name.title = job.name;
  title.append(name);
  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'queue-menu-dismiss';
  dismiss.setAttribute('aria-label', 'Close transfer actions');
  dismiss.innerHTML = '<svg aria-hidden="true"><use href="#i-close"/></svg>';
  dismiss.addEventListener('click', () => window.closeQueueMenu(true));
  title.append(dismiss);
  queueMenu.append(title);
  const groups = new Map();
  const position = state.jobs.indexOf(job);
  const extractMode = job.decompress === false ? 'none' : job.extract_mode || (job.staged_extraction ? 'mac' : 'ps5');
  for (const source of template.content.querySelectorAll('button')) {
    const button = source.cloneNode(true);
    const action = button.dataset.action;
    const [label, groupName, icon] = queueActionPresentation[action] || [button.textContent, 'Actions', 'i-dots'];
    if (!groups.has(groupName)) {
      const group = document.createElement('div');
      group.className = 'queue-menu-group';
      group.setAttribute('role', 'group');
      group.setAttribute('aria-label', groupName);
      const heading = document.createElement('div');
      heading.className = 'queue-menu-heading';
      heading.textContent = groupName;
      group.append(heading);
      groups.set(groupName, group);
    }
    button.type = 'button';
    button.tabIndex = -1;
    button.setAttribute('role', 'menuitem');
    button.innerHTML = `<svg aria-hidden="true"><use href="#${icon}"/></svg><span>${escaped(label)}</span>`;
    if (action.startsWith('set_extract_')) {
      const selected = action === `set_extract_${extractMode}`;
      button.setAttribute('role', 'menuitemradio');
      button.setAttribute('aria-checked', String(selected));
      if (selected) button.insertAdjacentHTML('beforeend', '<svg class="menu-check" aria-hidden="true"><use href="#i-check"/></svg>');
    }
    if (action === 'remove' || action === 'cancel') button.classList.add('menu-destructive');
    if (action === 'up') button.disabled = position === 0;
    if (action === 'down') button.disabled = position === state.jobs.length - 1;
    groups.get(groupName).append(button);
  }
  for (const group of groups.values()) queueMenu.append(group);
  queueMenuTrigger = trigger;
  queueMenu.dataset.job = job.id;
  queueMenu.dataset.jobState = job.state;
  trigger.setAttribute('aria-expanded', 'true');
  queueMenu.hidden = false;
  positionQueueMenu();
  queueMenu.scrollTop = 0;
  queueMenuItems()[0]?.focus({preventScroll: true});
}
function positionQueueMenu() {
  if (queueMenu.hidden || !queueMenuTrigger?.isConnected) return;
  const rect = queueMenuTrigger.getBoundingClientRect();
  const margin = 12;
  const width = Math.min(292, innerWidth - margin * 2);
  queueMenu.style.width = `${width}px`;
  queueMenu.style.maxHeight = `${Math.max(100, innerHeight - margin * 2)}px`;
  const height = queueMenu.getBoundingClientRect().height;
  const below = innerHeight - rect.bottom - margin - 7;
  const top = height <= below ? rect.bottom + 7 : rect.top - height - 7;
  queueMenu.style.left = `${Math.max(margin, Math.min(innerWidth - width - margin, rect.right - width))}px`;
  queueMenu.style.top = `${Math.max(margin, Math.min(innerHeight - height - margin, top))}px`;
}
document.addEventListener('click', e => {
  const trigger = e.target.closest('[data-menu-job]');
  if (trigger) { openQueueMenu(trigger); return; }
  if (!queueMenu.hidden && !queueMenu.contains(e.target)) window.closeQueueMenu();
});
document.addEventListener('keydown', e => {
  const trigger = e.target.closest('[data-menu-job]');
  if (trigger && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
    e.preventDefault(); openQueueMenu(trigger);
    if (e.key === 'ArrowUp') queueMenuItems().at(-1)?.focus({preventScroll: true});
    return;
  }
  if (queueMenu.hidden) return;
  if (e.key === 'Escape') { e.preventDefault(); window.closeQueueMenu(true); return; }
  if (e.key === 'Tab') { window.closeQueueMenu(true); return; }
  if (!queueMenu.contains(e.target)) return;
  const items = queueMenuItems();
  const i = items.indexOf(document.activeElement);
  let next;
  if (e.key === 'ArrowDown') next = (i + 1) % items.length;
  if (e.key === 'ArrowUp') next = (i - 1 + items.length) % items.length;
  if (e.key === 'Home') next = 0;
  if (e.key === 'End') next = items.length - 1;
  if (next !== undefined) { e.preventDefault(); items[next]?.focus(); }
});
window.addEventListener('resize', () => window.closeQueueMenu());
window.addEventListener('scroll', e => {
  if (!queueMenu.hidden && !queueMenu.contains(e.target)) positionQueueMenu();
}, true);
