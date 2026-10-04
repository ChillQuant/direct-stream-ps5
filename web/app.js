'use strict';
const $ = id => document.getElementById(id);
const escaped = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fragment = new URLSearchParams(location.hash.slice(1));
let token = fragment.get('session') || sessionStorage.getItem('ps5-session') || localStorage.getItem('ps5-session') || '';
if (fragment.has('session')) {
  sessionStorage.setItem('ps5-session', token);
  try { localStorage.setItem('ps5-session', token); } catch (_) {}
  history.replaceState(null, '', location.pathname);
}


let state = null;
let sourceKind = 'url';
let currentPage = 'transfers';
let toastTimer;
let editId;
let queueSignature = '';
let lastDiagnostic = '';
let logSignature = '';
let polling = false;
let matrixMode = 'quick';
let draggedJobId = null;
const selectedJobs = new Set();
let previousCompleted = new Set();
let soundEnabled = localStorage.getItem('ps5-sound') !== 'false';

const activeStates = ['starting', 'running', 'retrying', 'pausing', 'cancelling'];
const bytes = n => n == null ? 'Unknown size' : n < 1e6 ? `${(n/1e3).toFixed(0)} KB` : n < 1e9 ? `${(n/1e6).toFixed(1)} MB` : `${(n/1e9).toFixed(2)} GB`;
const duration = s => s == null || !Number.isFinite(s) ? '—' : s < 60 ? `${Math.ceil(s)}s` : s < 3600 ? `${Math.floor(s/60)}m ${Math.floor(s%60)}s` : `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m`;

function updateSoundIcon() {
  const el = $('sound-icon');
  if (el) el.innerHTML = `<use href="#${soundEnabled ? 'i-sound' : 'i-sound-mute'}"/>`;
}
updateSoundIcon();

function playPs5Chime() {
  if (!soundEnabled) return;
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const now = ctx.currentTime;
    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gain = ctx.createGain();
    
    // Harmonic PS5 chime tones
    osc1.type = 'sine';
    osc1.frequency.setValueAtTime(587.33, now); // D5
    osc2.type = 'sine';
    osc2.frequency.setValueAtTime(880.00, now + 0.12); // A5
    
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.2, now + 0.04);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.95);
    
    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(ctx.destination);
    
    osc1.start(now);
    osc1.stop(now + 0.35);
    osc2.start(now + 0.12);
    osc2.stop(now + 0.95);
    // ponytail: close temporary AudioContext after playback to prevent device leaks
    setTimeout(() => { ctx.close().catch(() => {}); }, 1100);
  } catch (e) {}
}

function toast(message, error=false) {
  clearTimeout(toastTimer);
  const t = $('toast');
  t.textContent = message;
  t.className = 'toast' + (error ? ' error' : '');
  t.hidden = false;
  toastTimer = setTimeout(() => { t.hidden = true; }, error ? 8500 : 4000);
}

async function api(path, data) {
  const opts = { headers: { 'X-Session-Token': token } };
  if (data !== undefined) {
    opts.method = 'POST';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(data);
  }
  const r = await fetch('/api/' + path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401) {
      sessionStorage.removeItem('ps5-session');
      try { localStorage.removeItem('ps5-session'); } catch (_) {}
      token = '';
    }
    throw new Error(body.error || `HTTP ${r.status}`);
  }
  return body;

}

async function perform(path, data, success) {
  try {
    const r = await api(path, data);
    if (success) toast(success);
    await poll();
    return r;
  } catch (e) {
    toast(e.message, true);
    return null;
  }
}

function page(name) {
  currentPage = name;
  document.querySelectorAll('.page').forEach(el => {
    el.hidden = el.id !== `page-${name}`;
  });
  document.querySelectorAll('.nav-item[data-page]').forEach(el => {
    el.classList.toggle('selected', el.dataset.page === name);
  });
  const crumb = $('crumb');
  if (crumb) {
    crumb.textContent = {
      transfers: 'Transfers',
      diagnostics: 'Diagnostics',
      files: 'Console files',
      activity: 'Activity',
      settings: 'Settings'
    }[name] || 'Transfers';
  }
  if (name === 'settings') {
    populateSettingsInputs();
  }
  if (name === 'files') {
    if (!lastFilesLoaded) {
      lastFilesLoaded = true;
      $('browse-form')?.requestSubmit();
    }
  }
}

function updatePipelineCalc() {
  const streams = parseInt($('page-setting-streams')?.value || '16', 10);
  const chunk = parseInt($('page-setting-chunk_mb')?.value || '8', 10);
  const buffer = parseInt($('page-setting-buffer_mb')?.value || '256', 10);
  const minRequired = streams * chunk;
  const slots = chunk > 0 ? Math.floor(buffer / chunk) : 0;
  const textEl = $('page-calc-text');
  const pillEl = $('page-pipeline-calc');
  if (textEl) {
    if (buffer < minRequired) {
      textEl.textContent = `⚠️ Buffer (${buffer} MiB) < required ${minRequired} MiB (${streams} streams × ${chunk} MiB chunk)`;
      if (pillEl) pillEl.classList.add('calc-warning');
    } else {
      textEl.textContent = `${streams} streams × ${chunk} MiB = ${minRequired} MiB min · ${buffer} MiB RAM (${slots} slots cushion)`;
      if (pillEl) pillEl.classList.remove('calc-warning');
    }
  }
}

function setSelectValueSafely(el, val) {
  if (!el) return;
  if (el.tagName === 'SELECT') {
    const strVal = String(val);
    const exists = Array.from(el.options).some(o => o.value === strVal);
    if (!exists && val !== undefined && val !== null && val !== '') {
      const opt = document.createElement('option');
      opt.value = strVal;
      opt.textContent = `${val} (Custom)`;
      el.appendChild(opt);
    }
    el.value = strVal;
  } else {
    el.value = val;
  }
}

function populateSettingsInputs() {
  if (!state) return;
  for (const [k, v] of Object.entries(state.settings)) {
    const input = $('setting-' + k);
    if (input) setSelectValueSafely(input, v);
    const pageInput = $('page-setting-' + k);
    if (pageInput && document.activeElement !== pageInput) setSelectValueSafely(pageInput, v);
  }
  updatePipelineCalc();
}

function selectKind(kind) {
  sourceKind = (kind === 'folder') ? 'local' : kind;
  document.querySelectorAll('[data-kind]').forEach(el => {
    el.classList.toggle('selected', el.dataset.kind === kind);
  });
  if ($('url-fields')) $('url-fields').hidden = sourceKind !== 'url';
  if ($('local-fields')) $('local-fields').hidden = sourceKind === 'url';
  if (kind === 'folder') {
    $('pick-folder')?.click();
  } else if (kind === 'local' && (!$('local-path')?.value)) {
    $('pick-file')?.click();
  }
}

function openAdd(kind='url') {
  selectKind(kind);
  if ($('add-error')) $('add-error').textContent = '';
  if ($('add-dialog')) $('add-dialog').showModal();
  setTimeout(() => {
    const target = $(kind === 'url' ? 'source-urls' : 'local-path');
    if (target) target.focus();
  }, 30);
}

function openSettings() {
  populateSettingsInputs();
  page('settings');
}

function graph(history) {
  const points = history.slice(-120);
  const max = Math.max(1e6, ...points.flatMap(p => [p.up, p.down]));
  const line = key => points.map((p, i) => `${i ? 'L' : 'M'}${(i / 119 * 640).toFixed(1)},${(76 - p[key] / max * 62).toFixed(1)}`).join(' ');
  $('chart-up').setAttribute('d', line('up'));
  $('chart-down').setAttribute('d', line('down'));
  const lastX = points.length ? (points.length - 1) / 119 * 640 : 0;
  $('chart-fill').setAttribute('d', points.length ? `${line('up')} L${lastX},82 L0,82 Z` : '');
}

function updateBulkBar() {
  const bar = $('bulk-bar');
  if (!bar) return;
  if (selectedJobs.size > 0) {
    bar.hidden = false;
    $('bulk-count').textContent = `${selectedJobs.size} selected`;
  } else {
    bar.hidden = true;
  }
  const selectAll = $('select-all');
  if (selectAll && state) {
    const valid = state.jobs.filter(j => j.state !== 'completed');
    selectAll.checked = valid.length > 0 && valid.every(j => selectedJobs.has(j.id));
  }
}

function row(job, index) {
  const pct = job.total ? Math.min(100, job.transferred / job.total * 100) : (job.state === 'completed' ? 100 : 0);
  const isActive = activeStates.includes(job.state);
  let primary = '';
  if (isActive) {
    primary = `<button class="button small outline" data-job="${job.id}" data-action="pause" ${job.state === 'pausing' || job.state === 'cancelling' ? 'disabled' : ''}>Pause</button>`;
  } else if (['paused', 'failed', 'cancelled'].includes(job.state)) {
    primary = `<button class="button small outline" data-job="${job.id}" data-action="resume">Resume</button>`;
  } else if (job.state === 'queued') {
    primary = `<button class="button small outline" data-job="${job.id}" data-action="pause">Pause</button>`;
  }

  let menu = `<button data-job="${job.id}" data-action="up">Move up ↑</button><button data-job="${job.id}" data-action="down">Move down ↓</button>`;
  if (job.kind === 'url' && !isActive && job.state !== 'completed') menu += `<button data-job="${job.id}" data-action="edit">Update link</button>`;
  if (!isActive && job.state !== 'completed') menu += `<button data-job="${job.id}" data-action="restart">Restart from zero</button>`;
  if (job.state !== 'completed' && job.state !== 'cancelled') menu += `<button data-job="${job.id}" data-action="cancel">Cancel transfer</button>`;
  if (!isActive) menu += `<button data-job="${job.id}" data-action="remove">Remove from queue</button>`;

  const isChecked = selectedJobs.has(job.id);
  const statusLabel = job.state === 'running' ? 'Transferring' : job.state === 'completed' ? 'Verified' : job.state[0].toUpperCase() + job.state.slice(1);
  const statusDotClass = (job.state === 'running' || job.state === 'completed') ? 'green' : job.state === 'failed' ? 'error' : '';
  const statusDotStyle = job.state === 'queued' ? 'background: #1668e3;' : job.state === 'paused' ? 'background: #f59e0b;' : '';

  return `<tr data-job-id="${job.id}" data-index="${index}" class="${isActive ? 'row-active' : ''}">
    <td class="col-chk"><input type="checkbox" class="row-select" data-id="${job.id}" ${isChecked ? 'checked' : ''}></td>
    <td class="col-num">${index + 1}</td>
    <td>
      <div class="queue-name">
        <svg><use href="#i-file"/></svg>
        <strong>${escaped(job.name)}</strong>
        ${job.kind === 'multipart' ? '<span style="font-size:10px; padding:2px 6px; border-radius:4px; background:rgba(37,99,235,0.18); color:#60a5fa; border:1px solid rgba(96,165,250,0.3); font-weight:600; margin-left:6px; letter-spacing:0.02em;">STITCHED</span>' : ''}
      </div>
    </td>
    <td>${job.total ? bytes(job.total) : '—'}</td>
    <td>
      <div class="row-progress">
        <div class="row-track"><div style="width: ${pct}%;"></div></div>
        <span>${pct.toFixed(0)}%</span>
      </div>
    </td>
    <td>
      <div class="status-cell">
        <span class="dot ${statusDotClass}" style="${statusDotStyle}"></span>
        <span>${statusLabel}</span>
      </div>
    </td>
    <td class="right">
      <div class="row-actions" style="display: inline-flex; align-items: center; justify-content: flex-end; gap: 6px;">
        ${primary}
        <details>
          <summary aria-label="More actions for ${escaped(job.name)}" class="icon-btn tiny">···</summary>
          <div class="action-menu">${menu}</div>
        </details>
      </div>
    </td>
  </tr>`;
}

function bindQueueEvents() {
  const tbody = $('queue-body');
  if (!tbody) return;

  // Row selection checkboxes
  tbody.querySelectorAll('.row-select').forEach(cb => {
    cb.addEventListener('change', e => {
      const id = e.target.dataset.id;
      if (e.target.checked) selectedJobs.add(id);
      else selectedJobs.delete(id);
      updateBulkBar();
    });
  });

  // Drag and drop reordering
  tbody.querySelectorAll('.grip-handle').forEach(handle => {
    handle.addEventListener('dragstart', e => {
      draggedJobId = handle.dataset.id;
      const tr = handle.closest('tr');
      if (tr) tr.classList.add('dragging');
      e.dataTransfer.setData('text/plain', draggedJobId);
      e.dataTransfer.effectAllowed = 'move';
    });
    handle.addEventListener('dragend', () => {
      tbody.querySelectorAll('tr').forEach(r => r.classList.remove('dragging', 'drag-over'));
      draggedJobId = null;
    });
  });

  tbody.querySelectorAll('tr').forEach(rowEl => {
    rowEl.addEventListener('dragover', e => {
      e.preventDefault();
      if (!draggedJobId || rowEl.dataset.jobId === draggedJobId) return;
      rowEl.classList.add('drag-over');
      e.dataTransfer.dropEffect = 'move';
    });
    rowEl.addEventListener('dragleave', () => {
      rowEl.classList.remove('drag-over');
    });
    rowEl.addEventListener('drop', async e => {
      e.preventDefault();
      rowEl.classList.remove('drag-over');
      if (!draggedJobId || rowEl.dataset.jobId === draggedJobId) return;
      const targetIndex = Number(rowEl.dataset.index);
      await perform('action', { action: 'reorder', id: draggedJobId, new_index: targetIndex });
    });
  });
}

function render(s) {
  state = s;
  const jobs = s.jobs;
  const active = jobs.find(j => j.id === s.active);
  const m = s.metrics || {};
  const pending = jobs.filter(j => !['completed', 'cancelled'].includes(j.state)).length;

  // Sound chime detection on completed jobs
  const completedJobs = new Set(jobs.filter(j => j.state === 'completed').map(j => j.id));
  if (previousCompleted.size > 0) {
    for (const cid of completedJobs) {
      if (!previousCompleted.has(cid)) {
        playPs5Chime();
        break;
      }
    }
  }
  previousCompleted = completedJobs;

  if ($('queue-badge')) $('queue-badge').textContent = pending;
  if ($('queue-total')) $('queue-total').textContent = `${jobs.length} ${jobs.length === 1 ? 'item' : 'items'}`;
  if ($('queue-empty')) $('queue-empty').hidden = jobs.length > 0;
  if ($('completed-count')) $('completed-count').textContent = jobs.filter(j => j.state === 'completed').length + ' completed';

  if ($('sidebar-host')) $('sidebar-host').textContent = s.settings.host || 'Not configured';
  if ($('mobile-host')) $('mobile-host').textContent = s.settings.host || 'PS5 offline';
  if ($('connection-dot')) $('connection-dot').className = 'dot ' + (s.connection.state === 'connected' ? 'green' : s.connection.state === 'error' ? 'error' : '');
  if ($('mobile-connection-dot')) $('mobile-connection-dot').className = 'dot ' + (s.connection.state === 'connected' ? 'green' : s.connection.state === 'error' ? 'error' : '');
  const connText = $('connection-status-text');
  if (connText) {
    connText.textContent = s.connection.state === 'connected' ? 'Connected' : s.connection.state === 'error' ? 'Offline' : 'Checking…';
  }
  const connPill = $('connection-pill');
  if (connPill) {
    connPill.className = 'status-pill ' + (s.connection.state === 'connected' ? 'connected' : s.connection.state === 'error' ? 'error' : '');
  }
  if ($('target-host')) $('target-host').textContent = s.settings.host ? `${s.settings.host}:${s.settings.port}` : 'Add your PS5 address';
  if ($('target-folder')) $('target-folder').textContent = s.settings.folder;
  if ($('connection-message')) $('connection-message').textContent = s.connection.message;

  // Settings page connection indicator
  if ($('page-conn-dot')) $('page-conn-dot').className = 'dot ' + (s.connection.state === 'connected' ? 'green' : s.connection.state === 'error' ? 'error' : '');
  if ($('page-conn-status')) $('page-conn-status').textContent = s.connection.state === 'connected' ? 'Connected' : s.connection.state === 'error' ? 'Disconnected' : 'Checking…';
  if ($('page-conn-msg')) $('page-conn-msg').textContent = s.connection.state === 'connected' ? 'PS5 ready for transfers' : (s.connection.message || 'Check IP and network');

  if ($('pipeline-settings')) $('pipeline-settings').textContent = `${s.settings.streams} streams · ${s.settings.buffer_mb} MiB RAM`;
  if ($('source-mode')) $('source-mode').textContent = active?.kind === 'local' ? 'Local file · zero RAM copy' : (active?.kind === 'multipart' ? 'Multi-part stitch · direct PS5 stream' : 'Direct stream · async buffers');

  const lightbar = $('status-lightbar');
  if (lightbar) {
    if (active?.state === 'running') {
      lightbar.className = 'ps5-lightbar active-stream';
    } else if (active?.state === 'paused') {
      lightbar.className = 'ps5-lightbar paused-stream';
    } else {
      lightbar.className = 'ps5-lightbar';
    }
  }

  const livePause = $('live-pause-btn');
  const liveCancel = $('live-cancel-btn');
  if (livePause) {
    if (active && ['running', 'paused', 'pausing', 'cancelling'].includes(active.state)) {
      livePause.hidden = false;
      livePause.innerHTML = active.state === 'running'
        ? '<svg viewBox="0 0 24 24"><use href="#i-pause"/></svg>'
        : '<svg viewBox="0 0 24 24"><use href="#i-play"/></svg>';
      livePause.disabled = active.state === 'pausing' || active.state === 'cancelling';
      livePause.title = active.state === 'running' ? 'Pause transfer' : 'Resume transfer';
      livePause.onclick = () => perform('action', { action: active.state === 'running' ? 'pause' : 'resume', id: active.id });
    } else {
      livePause.hidden = true;
    }
  }
  if (liveCancel) {
    if (active && ['running', 'paused', 'pausing', 'cancelling'].includes(active.state)) {
      liveCancel.hidden = false;
      liveCancel.onclick = () => perform('action', { action: 'cancel', id: active.id });
    } else {
      liveCancel.hidden = true;
    }
  }

  const stateBadge = $('live-state');
  if (active) {
    if ($('active-name')) $('active-name').textContent = active.name;
    const st = active.state;
    if (stateBadge) {
      stateBadge.textContent = st === 'running' ? 'Transferring' : st === 'completed' ? 'Verified' : st[0].toUpperCase() + st.slice(1);
      stateBadge.className = 'status-badge ' + (st === 'running' ? 'transferring' : st);
    }
    const pct = active.total ? Math.min(100, active.transferred / active.total * 100) : 0;
    if ($('progress-fill')) $('progress-fill').style.width = pct + '%';
    if ($('main-progress')) $('main-progress').setAttribute('aria-valuenow', pct.toFixed(1));
    if ($('progress-percent')) $('progress-percent').textContent = active.total ? pct.toFixed(0) + '%' : '—';
    if ($('progress-bytes')) $('progress-bytes').textContent = `${bytes(active.transferred)} of ${bytes(active.total)}`;
    if ($('live-speed-heading')) $('live-speed-heading').hidden = false;
  } else {
    const next = jobs.find(j => j.state === 'queued');
    if (next) {
      if ($('active-name')) $('active-name').textContent = next.name;
      if (stateBadge) {
        stateBadge.textContent = 'Queued';
        stateBadge.className = 'status-badge queued';
      }
      if ($('progress-fill')) $('progress-fill').style.width = '0%';
      if ($('main-progress')) $('main-progress').setAttribute('aria-valuenow', '0');
      if ($('progress-percent')) $('progress-percent').textContent = '0%';
      if ($('progress-bytes')) $('progress-bytes').textContent = next.total ? `0 of ${bytes(next.total)}` : 'Ready to start';
    } else {
      if ($('active-name')) $('active-name').textContent = 'No active transfer';
      if (stateBadge) {
        stateBadge.textContent = jobs.length > 0 ? 'Paused' : 'Idle';
        stateBadge.className = 'status-badge idle';
      }
      if ($('progress-fill')) $('progress-fill').style.width = '0%';
      if ($('main-progress')) $('main-progress').setAttribute('aria-valuenow', '0');
      if ($('progress-percent')) $('progress-percent').textContent = '0%';
      if ($('progress-bytes')) $('progress-bytes').textContent = jobs.length > 0 ? 'All transfers completed' : 'Queue is empty';
    }
  }

  if ($('active-detail')) $('active-detail').textContent = active?.detail || (jobs.some(j => j.state === 'queued') ? 'Your queue is ready. Press Start queue to stream.' : 'Paste a download link above or drag package files directly onto this window.');

  const upMb = m.upload_bps != null ? (m.upload_bps / 1e6).toFixed(1) : '0.0';
  const downMb = m.download_bps != null ? (m.download_bps / 1e6).toFixed(1) : '0.0';
  if ($('upload-speed')) $('upload-speed').textContent = upMb;
  if ($('upload-speed-display')) $('upload-speed-display').textContent = upMb;
  if ($('download-speed')) $('download-speed').textContent = downMb;
  if ($('eta')) $('eta').textContent = active ? duration(m.eta) : '—';
  if ($('bottleneck')) $('bottleneck').textContent = m.bottleneck || 'Optimal path';

  if ($('buffer-value')) $('buffer-value').textContent = `${Math.max(0, (m.buffered || 0) / 1048576).toFixed(0)} / ${s.settings.buffer_mb} MiB`;
  if ($('buffer-fill')) $('buffer-fill').style.width = Math.min(100, (m.buffered || 0) / (s.settings.buffer_mb * 1048576) * 100) + '%';
  graph(s.history);

  const diagnosticBusy = s.diagnostic.state === 'running';
  $('test-connection').disabled = !!s.active || diagnosticBusy;
  $('benchmark').disabled = !!s.active || diagnosticBusy;
  $('benchmark-matrix').disabled = !!s.active || diagnosticBusy;
  $('stop-diagnostic').disabled = !diagnosticBusy;

  $('start-queue').disabled = !!s.running || diagnosticBusy || !jobs.some(j => j.state === 'queued');
  $('pause-all').disabled = !s.running && !s.active;
  $('clear-completed').disabled = !jobs.some(j => j.state === 'completed');

  const qSub = $('queue-subtitle');
  if (qSub) {
    qSub.textContent = s.running
      ? 'Direct queue streaming · one FTP upload at a time'
      : jobs.some(j => j.state === 'failed')
      ? 'Queue stopped after an error. Review the failed job.'
      : 'Add files now. Send them in order.';
  }

  const signature = JSON.stringify(jobs.map(j => [j.id, j.state, j.name, j.detail, j.total, Math.floor(j.transferred / 1e6)]));
  if (signature !== queueSignature && !$('queue-body').querySelector('details[open]') && !$('queue-body').contains(document.activeElement)) {
    queueSignature = signature;
    $('queue-body').innerHTML = jobs.map(row).join('');
    $('queue-body').querySelectorAll('[data-width]').forEach(el => {
      el.style.width = el.dataset.width + '%';
    });
    bindQueueEvents();
    updateBulkBar();
  }

  const d = s.diagnostic;
  const ds = JSON.stringify(d);
  if (ds !== lastDiagnostic) {
    lastDiagnostic = ds;
    if (d.kind === 'source') {
      const isRunning = d.state === 'running';
      const stopBtn = $('stop-diagnostic');
      if (stopBtn) stopBtn.hidden = !isRunning;
      const runBtn = $('benchmark-matrix');
      if (runBtn) {
        if (isRunning) {
          runBtn.disabled = true;
          runBtn.textContent = 'Testing…';
        } else {
          runBtn.disabled = false;
          runBtn.textContent = matrixMode === 'quick' ? 'Run Quick Scan' : 'Run Full Test';
        }
      }
      if (d.bps != null && $('diag-rate-val')) {
        $('diag-rate-val').textContent = `${(d.bps / 1e6).toFixed(1)} MB/s`;
      }
      const benchResult = $('benchmark-result');
      if (benchResult) {
        if (d.message) {
          benchResult.hidden = false;
          benchResult.innerHTML = `<strong>${d.bps != null ? (d.bps / 1e6).toFixed(1) : '—'} <small>MB/s</small></strong><span style="white-space:pre-line">${escaped(d.message)}</span>`;
        } else {
          benchResult.hidden = true;
        }
      }
      const applyBtn = $('apply-optimal-btn');
      if (applyBtn) {
        if (d.state === 'done' && d.best_config) {
          applyBtn.hidden = false;
          applyBtn.textContent = `Apply Recommended Settings: ${d.best_label || 'Optimal'}`;
          applyBtn.onclick = async () => {
            try {
              await api('settings', { ...state.settings, ...d.best_config });
              toast(`Applied settings: ${d.best_label}`);
              applyBtn.hidden = true;
              await poll();
            } catch (err) {
              toast(err.message, true);
            }
          };
        } else {
          applyBtn.hidden = true;
        }
      }
    }
    if (d.kind === 'files') {
      if ($('browse-message')) $('browse-message').textContent = d.message || 'Connected';
      if (d.files) {
        renderConsoleFiles(d.files, $('files-search')?.value.trim().toLowerCase() || '');
        const currentPath = d.folder || '/';
        if ($('browse-path')) $('browse-path').value = currentPath;
        updateBrowseHistory(currentPath);
      }
    }
    if (d.state === 'error') toast(d.message, true);
    if (d.kind === 'connection' && d.state === 'done') toast(d.message);
  }
}

let currentLogFilter = 'all';
let logSearchQuery = '';

function renderLogs(logs) {
  const container = $('log-list');
  if (!container || !logs) return;
  
  const jobs = state ? state.jobs : [];
  const completedJobsCount = jobs.filter(j => j.state === 'completed').length;
  const errorsCount = logs.filter(l => l.level === 'error').length;
  const connectionsCount = logs.filter(l => l.message && l.message.toLowerCase().includes('connect')).length || (state && state.connection.state === 'connected' ? 1 : 0);
  const totalEventsCount = logs.length;
  
  if ($('act-metric-completed')) $('act-metric-completed').textContent = completedJobsCount;
  if ($('act-metric-errors')) $('act-metric-errors').textContent = errorsCount;
  if ($('act-metric-connections')) $('act-metric-connections').textContent = connectionsCount;
  if ($('act-metric-events')) $('act-metric-events').textContent = totalEventsCount;
  
  if ($('count-all')) $('count-all').textContent = totalEventsCount;
  if ($('count-transfers')) $('count-transfers').textContent = logs.filter(l => l.message && (l.message.toLowerCase().includes('transfer') || l.message.toLowerCase().includes('download') || l.message.toLowerCase().includes('upload'))).length;
  if ($('count-connections')) $('count-connections').textContent = connectionsCount;
  if ($('count-errors')) $('count-errors').textContent = errorsCount;

  let filtered = logs;
  if (currentLogFilter === 'transfer') {
    filtered = filtered.filter(l => l.message && (l.message.toLowerCase().includes('transfer') || l.message.toLowerCase().includes('download') || l.message.toLowerCase().includes('upload')));
  } else if (currentLogFilter === 'connection') {
    filtered = filtered.filter(l => l.message && l.message.toLowerCase().includes('connect'));
  } else if (currentLogFilter === 'error') {
    filtered = filtered.filter(l => l.level === 'error');
  }
  
  if (logSearchQuery) {
    const q = logSearchQuery.toLowerCase();
    filtered = filtered.filter(l => l.message.toLowerCase().includes(q) || l.time.toLowerCase().includes(q));
  }

  if (filtered.length === 0) {
    container.innerHTML = `<tr><td colspan="3" style="text-align: center; padding: 24px; color: var(--muted);">No activity matches the current view.</td></tr>`;
    return;
  }

  container.innerHTML = filtered.map(l => {
    let typeName = 'System';
    let dotClass = '';
    const msg = l.message.toLowerCase();
    if (msg.includes('transfer') || msg.includes('download') || msg.includes('upload')) {
      typeName = 'Transfer';
      dotClass = l.level === 'error' ? 'error' : 'green';
    } else if (msg.includes('connect')) {
      typeName = 'Connection';
      dotClass = 'green';
    } else if (msg.includes('diagnostic') || msg.includes('speed') || msg.includes('benchmark')) {
      typeName = 'Diagnostics';
      dotClass = 'blue';
    } else if (msg.includes('file')) {
      typeName = 'Console files';
    }

    let title = l.message;
    let sub = '';
    if (l.message.includes(' - ')) {
      const parts = l.message.split(' - ');
      title = parts[0];
      sub = parts.slice(1).join(' - ');
    } else if (l.message.includes(': ')) {
      const parts = l.message.split(': ');
      title = parts[0];
      sub = parts.slice(1).join(': ');
    }

    return `<tr>
      <td style="color: var(--muted); font-size: 13px;"><time>${escaped(l.time)}</time></td>
      <td>
        <div class="status-cell">
          <span class="dot ${dotClass}" style="${dotClass === 'blue' ? 'background: #1668e3;' : ''}"></span>
          <span>${typeName}</span>
        </div>
      </td>
      <td>
        <div class="log-item-message">
          <strong class="log-item-title">${escaped(title)}</strong>
          ${sub ? `<span class="log-item-sub">${escaped(sub)}</span>` : ''}
        </div>
      </td>
    </tr>`;
  }).join('');
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    render(await api('state'));
    $('offline-banner').hidden = true;
  } catch (e) {
    $('offline-banner').hidden = false;
    $('offline-banner').textContent = token
      ? `Connection to the app was lost. ${e.message}`
      : 'Launch DIRECT STREAM FOR PLAYSTATION 5 from its launcher (or run "ps5" in Termux) to connect.';

  } finally {
    polling = false;
  }
}

// ponytail: adaptive polling backs off when tab is idle/hidden; wakes instantly on focus
async function tick() {
  await poll();
  const isBusy = state && (state.running || state.active || state.diagnostic?.state === 'running');
  const delay = document.hidden ? (isBusy ? 2000 : 5000) : 750;
  setTimeout(tick, delay);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) poll();
});

async function jobAction(action, id) {
  const job = state.jobs.find(j => j.id === id);
  if (action === 'edit') {
    editId = id;
    $('edit-source').value = job.source;
    $('edit-error').textContent = '';
    $('edit-dialog').showModal();
    return;
  }
  if (action === 'restart' && !confirm('Start from zero in a new partial file? The old partial stays on PS5 and may use disk space.')) return;
  if (action === 'remove' && job.transferred && !confirm('Remove this job? Its partial file will remain on PS5, and this app will no longer have its resume history.')) return;
  if (action === 'cancel' && !confirm('Cancel this transfer? Any partial file will stay on PS5.')) return;

  const r = await perform('action', { action, id });
  if (r && ['resume', 'restart'].includes(action)) toast('Queued. Press Start queue to begin if the queue is paused.');
}

// Global click delegation
document.addEventListener('click', async e => {
  const el = e.target.closest('button,a.brand');
  if (!el) return;
  if (el.classList.contains('brand')) {
    e.preventDefault();
    page('transfers');
  }
  if (el.dataset.page) page(el.dataset.page);
  if (el.hasAttribute('data-add')) openAdd();
  if (el.hasAttribute('data-open-settings')) openSettings();
  if (el.dataset.close) $(el.dataset.close).close();
  if (el.dataset.kind) selectKind(el.dataset.kind);
  if (el.dataset.action) {
    el.closest('details')?.removeAttribute('open');
    await jobAction(el.dataset.action, el.dataset.job);
    el.blur();
    await poll();
  }
  if (el.dataset.folder) {
    $('browse-path').value = $('browse-path').value.replace(/\/$/, '') + '/' + el.dataset.folder;
    $('browse-form').requestSubmit();
  }
});

function parseMultipartInfo(filename) {
  if (!filename || typeof filename !== 'string') return null;
  const name = filename.split('/').pop().split('\\').pop();
  let m = name.match(/^(.*?\.pkg)\.(\d{1,4})$/i);
  if (m) return { base: m[1], num: parseInt(m[2], 10) };
  m = name.match(/^(.*?)\.(\d{2,4})$/i);
  if (m) {
    let base = m[1];
    if (!base.toLowerCase().endsWith('.pkg')) base += '.pkg';
    return { base, num: parseInt(m[2], 10) };
  }
  m = name.match(/^(.*?)[._-]part(\d{1,4})\.pkg$/i);
  if (m) return { base: m[1] + '.pkg', num: parseInt(m[2], 10) };
  m = name.match(/^(.*?\.pkg)[._-]part(\d{1,4})$/i);
  if (m) return { base: m[1], num: parseInt(m[2], 10) };
  m = name.match(/^(.*?)[_.](\d{1,3})\.pkg$/i);
  if (m) return { base: m[1] + '.pkg', num: parseInt(m[2], 10) };
  return null;
}

function detectMultipartSequence(items) {
  if (!items || items.length <= 1) return [false, '', items];
  const parsed = [];
  const baseNames = new Set();
  for (const it of items) {
    let raw = it.name || '';
    if (!raw && it.source) {
      try {
        const u = new URL(it.source);
        raw = decodeURIComponent(u.pathname.split('/').pop());
      } catch (e) {
        raw = it.source.split('/').pop().split('\\').pop();
      }
    }
    const info = parseMultipartInfo(raw);
    if (!info) return [false, '', items];
    baseNames.add(info.base.toLowerCase());
    parsed.push({ num: info.num, item: it, base: info.base });
  }
  if (baseNames.size !== 1) return [false, '', items];
  parsed.sort((a, b) => a.num - b.num);
  return [true, parsed[0].base, parsed.map(p => p.item)];
}

const updateAddPreview = () => {
  const isUrl = sourceKind === 'url';
  const val = isUrl ? $('source-urls')?.value.trim() : $('local-path')?.value.trim();
  if (!val) {
    if ($('preview-filename')) $('preview-filename').textContent = 'No game file selected';
    if ($('preview-filesize')) $('preview-filesize').textContent = 'Enter direct link or choose file';
    return;
  }
  const lines = val.split('\n').map(x => x.trim()).filter(Boolean);
  if (lines.length > 1) {
    const items = lines.map(s => ({ source: s }));
    const [isMulti, mergedName] = detectMultipartSequence(items);
    if (isMulti) {
      if ($('preview-filename')) $('preview-filename').textContent = `⚡ Multi-Part Sequence (${lines.length} parts)`;
      if ($('preview-filesize')) $('preview-filesize').textContent = `Auto-stitching into ${mergedName} on PS5 (0 GB disk space used)`;
      const ext = mergedName.toLowerCase().split('.').pop();
      if (['ffpfsc', 'exfat', 'ufs'].includes(ext)) {
        if ($('modal-dest-folder')) $('modal-dest-folder').value = '/data/ShadowMount';
      }
      return;
    } else {
      if ($('preview-filename')) $('preview-filename').textContent = `${lines.length} individual files`;
      if ($('preview-filesize')) $('preview-filesize').textContent = 'Queued as separate transfers';
      return;
    }
  }
  const first = lines[0] || '';
  let name = '';
  if (isUrl) {
    try {
      const u = new URL(first);
      name = decodeURIComponent(u.pathname.split('/').pop()) || 'download.bin';
    } catch {
      name = first;
    }
  } else {
    name = first.split('/').pop() || 'local.bin';
  }
  if ($('preview-filename')) $('preview-filename').textContent = name;

  const ext = name.toLowerCase().split('?')[0].split('.').pop();
  let formatLabel = isUrl ? 'Direct link · Ready to stream' : 'Local file · Ready to stream';
  let smartDest = null;

  if (['ffpfsc', 'exfat', 'ufs'].includes(ext)) {
    const extUpper = ext.toUpperCase();
    formatLabel = isUrl ? `Direct link · PS5 Native ${extUpper} Disk Image` : `Local file · PS5 Native ${extUpper} Disk Image`;
    smartDest = '/data/ShadowMount';
  } else if (ext === 'pkg') {
    formatLabel = isUrl ? 'Direct link · PlayStation Package (.pkg)' : 'Local file · PlayStation Package (.pkg)';
    smartDest = '/data/pkg';
  } else if (['zip', 'rar', '7z'].includes(ext)) {
    formatLabel = isUrl ? 'Direct link · Streaming Archive' : 'Local file · Archive';
  } else if (['iso', 'bin', 'img'].includes(ext)) {
    formatLabel = isUrl ? 'Direct link · Disc Image' : 'Local file · Disc Image';
  }

  if ($('preview-filesize')) $('preview-filesize').textContent = formatLabel;

  // Auto-route destination if user hasn't explicitly set a custom folder
  const currentDest = $('modal-dest-folder')?.value.trim();
  if (smartDest && (!currentDest || currentDest === '/data/PS5Direct' || currentDest === '/data/ShadowMount' || currentDest === '/data/pkg')) {
    if ($('modal-dest-folder')) $('modal-dest-folder').value = smartDest;
  }
};

$('source-urls')?.addEventListener('input', updateAddPreview);
$('local-path')?.addEventListener('input', updateAddPreview);

// Forms
$('add-form').addEventListener('submit', async e => {
  e.preventDefault();
  const raw = sourceKind === 'url' ? $('source-urls').value : $('local-path').value;
  const sources = raw.split('\n').map(x => x.trim()).filter(Boolean);
  const items = sources.map(source => ({ source, name: $('file-name').value.trim() }));
  const [isMulti, mergedName] = detectMultipartSequence(items);
  const willStitch = isMulti && ($('combine-multipart')?.checked !== false);

  if (sources.length > 1 && $('file-name').value.trim() && !willStitch) {
    $('add-error').textContent = 'Leave Save as blank when adding multiple individual files.';
    return;
  }
  const destFolder = $('modal-dest-folder')?.value.trim();
  const button = e.submitter;
  button.disabled = true;
  try {
    const r = await api('jobs', {
      kind: sourceKind,
      folder: destFolder,
      items: items.map(it => ({ ...it, folder: destFolder })),
      name: $('file-name').value.trim() || (willStitch ? mergedName : ''),
      combine_multipart: willStitch,
      overwrite: $('overwrite').checked
    });
    $('add-dialog').close();
    $('source-urls').value = '';
    $('local-path').value = '';
    $('file-name').value = '';
    $('overwrite').checked = false;
    toast(`${r.count} transfer${r.count === 1 ? '' : 's'} added to queue`);
    page('transfers');
    await poll();
    if (!state.settings.host) openSettings();
  } catch (err) {
    $('add-error').textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

$('settings-form').addEventListener('submit', async e => {
  e.preventDefault();
  const data = {};
  for (const key of Object.keys(state.settings)) {
    const el = $('setting-' + key);
    if (el) data[key] = el.value;
  }
  data.password = $('setting-password')?.value || '';
  try {
    await api('settings', data);
    $('settings-dialog').close();
    toast('Settings saved');
    await poll();
    $('browse-path').value = state.settings.folder;
  } catch (err) {
    $('settings-error').textContent = err.message;
  }
});

const settingsPageForm = $('settings-page-form');
if (settingsPageForm) {
  settingsPageForm.addEventListener('submit', async e => {
    e.preventDefault();
    const data = {
      host: $('page-setting-host').value.trim(),
      port: $('page-setting-port').value.trim(),
      folder: $('page-setting-folder').value.trim(),
      streams: $('page-setting-streams').value,
      chunk_mb: $('page-setting-chunk_mb')?.value || state?.settings?.chunk_mb || '8',
      buffer_mb: $('page-setting-buffer_mb').value,
      limit_mbps: $('page-setting-limit_mbps')?.value || '0',
      retries: $('page-setting-retries')?.value || '3',
      password: ''
    };
    try {
      await api('settings', data);
      toast('Settings saved');
      await poll();
      $('browse-path').value = state.settings.folder;
    } catch (err) {
      toast(err.message, true);
    }
  });

  ['page-setting-streams', 'page-setting-chunk_mb', 'page-setting-buffer_mb'].forEach(id => {
    $(id)?.addEventListener('change', updatePipelineCalc);
    $(id)?.addEventListener('input', updatePipelineCalc);
  });
}

const resetDefaultsBtn = $('settings-reset-defaults');
if (resetDefaultsBtn) {
  resetDefaultsBtn.addEventListener('click', () => {
    if ($('page-setting-streams')) $('page-setting-streams').value = '16';
    if ($('page-setting-chunk_mb')) $('page-setting-chunk_mb').value = '8';
    if ($('page-setting-buffer_mb')) $('page-setting-buffer_mb').value = '256';
    if ($('page-setting-limit_mbps')) $('page-setting-limit_mbps').value = '0';
    if ($('page-setting-retries')) $('page-setting-retries').value = '3';
    if ($('page-setting-folder')) $('page-setting-folder').value = '/data/PS5Direct';
    updatePipelineCalc();
    toast('Settings reset to recommended defaults (16 streams · 8 MiB chunk · 256 MiB RAM)');
  });
}

document.querySelectorAll('[data-settings-tab]').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('[data-settings-tab]').forEach(t => {
      t.classList.remove('selected');
      t.setAttribute('aria-selected', 'false');
    });
    tab.classList.add('selected');
    tab.setAttribute('aria-selected', 'true');
    const tabName = tab.dataset.settingsTab;
    const connSec = $('settings-connection-section');
    const transSec = $('settings-transfers-section');
    if (tabName === 'connection') {
      if (connSec) connSec.hidden = false;
      if (transSec) transSec.hidden = true;
    } else if (tabName === 'transfers') {
      if (connSec) connSec.hidden = true;
      if (transSec) transSec.hidden = false;
    } else {
      if (connSec) connSec.hidden = false;
      if (transSec) transSec.hidden = false;
    }
  });
});

document.querySelectorAll('[data-log-filter]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-log-filter]').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    currentLogFilter = btn.dataset.logFilter;
    if (state && state.logs) renderLogs(state.logs);
  });
});

const actSearchInput = $('activity-search-input');
if (actSearchInput) {
  actSearchInput.addEventListener('input', e => {
    logSearchQuery = e.target.value.trim();
    if (state && state.logs) renderLogs(state.logs);
  });
}

const rootBtn = $('root-folder-btn');
if (rootBtn) {
  rootBtn.addEventListener('click', () => {
    $('browse-path').value = '/';
    $('browse-form')?.requestSubmit();
  });
}

$('edit-form').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    await api('edit', { id: editId, source: $('edit-source').value.trim() });
    $('edit-dialog').close();
    toast('Download link updated');
    await poll();
  } catch (err) {
    $('edit-error').textContent = err.message;
  }
});

// Pickers
$('pick-file').addEventListener('click', async () => {
  const b = $('pick-file');
  b.disabled = true;
  try {
    const r = await api('pick', { type: 'file' });
    if (r.paths && r.paths.length > 0) {
      $('local-path').value = r.paths.join('\n');
    } else if (r.path) {
      $('local-path').value = r.path;
    } else if (r.picker_unsupported) {
      toast('File picker dialog not available in terminal/mobile mode. Enter or paste the file path directly.', true);
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    b.disabled = false;
  }
});

$('pick-folder').addEventListener('click', async () => {
  const b = $('pick-folder');
  b.disabled = true;
  try {
    const r = await api('pick', { type: 'folder' });
    if (r.paths && r.paths.length > 0) {
      $('local-path').value = r.paths.join('\n');
      toast(`Found ${r.paths.length} packages in folder`);
    } else if (r.path) {
      $('local-path').value = r.path;
    } else if (r.picker_unsupported) {
      toast('Folder picker dialog not available in terminal/mobile mode. Enter or paste the folder path directly.', true);
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    b.disabled = false;
  }
});

$('add-local').addEventListener('click', () => openAdd('local'));
$('add-local-folder').addEventListener('click', () => {
  openAdd('local');
  $('pick-folder').click();
});

// Quick Stream Command Bar
async function quickAddUrl(url) {
  if (!url || !url.trim()) return;
  url = url.trim();
  try {
    const r = await api('jobs', {
      kind: 'url',
      items: [{ source: url, name: '' }],
      overwrite: false
    });
    $('quick-url').value = '';
    toast(`${r.count || 1} transfer added to queue`);
    await poll();
    if (!state.settings.host) openSettings();
  } catch (err) {
    toast(err.message, true);
  }
}
const quickBtn = $('quick-add-btn');
if (quickBtn) quickBtn.addEventListener('click', () => quickAddUrl($('quick-url').value));
const quickInput = $('quick-url');
if (quickInput) {
  quickInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      quickAddUrl(quickInput.value);
    }
  });
}
const quickFileBtn = $('quick-browse-file');
if (quickFileBtn) {
  quickFileBtn.addEventListener('click', () => {
    openAdd('local');
    $('pick-file').click();
  });
}
const quickFolderBtn = $('quick-browse-folder');
if (quickFolderBtn) {
  quickFolderBtn.addEventListener('click', () => {
    openAdd('local');
    $('pick-folder').click();
  });
}

// Queue Actions
$('test-connection').addEventListener('click', () => perform('diagnostic', { kind: 'connection' }));
$('start-queue').addEventListener('click', async () => {
  if (!state.settings.host) {
    openSettings();
    return;
  }
  await perform('action', { action: 'start' });
});
$('pause-all').addEventListener('click', () => perform('action', { action: 'pause_all' }));
$('clear-completed').addEventListener('click', () => perform('action', { action: 'clear_completed' }));

// Link validation action
$('validate-links').addEventListener('click', async () => {
  await perform('action', { action: 'validate_links' }, 'Validating queued download links…');
});

// Bulk action controls
$('select-all').addEventListener('change', e => {
  if (!state) return;
  const checked = e.target.checked;
  selectedJobs.clear();
  if (checked) {
    state.jobs.forEach(j => {
      if (j.state !== 'completed') selectedJobs.add(j.id);
    });
  }
  document.querySelectorAll('.row-select').forEach(cb => { cb.checked = checked; });
  updateBulkBar();
});

$('bulk-pause').addEventListener('click', async () => {
  await perform('action', { action: 'bulk_pause', ids: Array.from(selectedJobs) }, 'Paused selected transfers');
  selectedJobs.clear();
  updateBulkBar();
});

$('bulk-resume').addEventListener('click', async () => {
  await perform('action', { action: 'bulk_resume', ids: Array.from(selectedJobs) }, 'Resumed selected transfers');
  selectedJobs.clear();
  updateBulkBar();
});

$('bulk-remove').addEventListener('click', async () => {
  if (!confirm(`Remove ${selectedJobs.size} transfers from the queue?`)) return;
  await perform('action', { action: 'bulk_remove', ids: Array.from(selectedJobs) }, 'Removed selected transfers');
  selectedJobs.clear();
  updateBulkBar();
});

$('bulk-cancel').addEventListener('click', () => {
  selectedJobs.clear();
  updateBulkBar();
  document.querySelectorAll('.row-select').forEach(cb => { cb.checked = false; });
});

// Diagnostics & Benchmark modes
function setBenchmarkMode(mode) {
  matrixMode = mode;
  const isQuick = mode === 'quick';
  const quickBtn = $('matrix-mode-quick');
  const fullBtn = $('matrix-mode-full');
  if (quickBtn) {
    quickBtn.classList.toggle('selected', isQuick);
    quickBtn.setAttribute('aria-checked', isQuick ? 'true' : 'false');
  }
  if (fullBtn) {
    fullBtn.classList.toggle('selected', !isQuick);
    fullBtn.setAttribute('aria-checked', !isQuick ? 'true' : 'false');
  }
  const runBtn = $('benchmark-matrix');
  if (runBtn && !runBtn.disabled) {
    runBtn.textContent = isQuick ? 'Run Quick Scan' : 'Run Full Test';
  }
  const badge = $('benchmark-mode-badge');
  if (badge) {
    badge.textContent = isQuick ? 'Quick scan (~25s)' : 'Full test (~60s)';
  }
}

$('matrix-mode-quick')?.addEventListener('click', () => setBenchmarkMode('quick'));
$('matrix-mode-full')?.addEventListener('click', () => setBenchmarkMode('full'));

$('benchmark').addEventListener('click', () => {
  perform('diagnostic', { kind: 'source', source: $('benchmark-url').value.trim() });
});

$('benchmark-matrix').addEventListener('click', () => {
  perform('diagnostic', {
    kind: 'source',
    matrix: true,
    full: matrixMode === 'full',
    source: $('benchmark-url').value.trim()
  });
});

$('stop-diagnostic').addEventListener('click', () => perform('stop-diagnostic', {}));

$('browse-form').addEventListener('submit', e => {
  e.preventDefault();
  perform('diagnostic', { kind: 'files', folder: $('browse-path').value.trim() });
});

$('parent-folder').addEventListener('click', () => {
  $('browse-path').value = $('browse-path').value.replace(/\/$/, '').split('/').slice(0, -1).join('/') || '/';
  $('browse-form').requestSubmit();
});

// Performance Presets
$('preset-conservative')?.addEventListener('click', () => {
  for (const [k, v] of Object.entries({ streams: 4, buffer_mb: 64, chunk_mb: 4, limit_mbps: 0 })) {
    const el = $('setting-' + k);
    if (el) el.value = v;
    const pageEl = $('page-setting-' + k);
    if (pageEl) pageEl.value = v;
  }
  updatePipelineCalc();
  toast('Applied Conservative preset (4 streams · 4 MiB chunk · 64 MiB RAM)');
});
$('preset-balanced')?.addEventListener('click', () => {
  for (const [k, v] of Object.entries({ streams: 8, buffer_mb: 128, chunk_mb: 8, limit_mbps: 0 })) {
    const el = $('setting-' + k);
    if (el) el.value = v;
    const pageEl = $('page-setting-' + k);
    if (pageEl) pageEl.value = v;
  }
  updatePipelineCalc();
  toast('Applied Balanced preset (8 streams · 8 MiB chunk · 128 MiB RAM)');
});
$('preset-fast')?.addEventListener('click', () => {
  for (const [k, v] of Object.entries({ streams: 16, buffer_mb: 256, chunk_mb: 8, limit_mbps: 0 })) {
    const el = $('setting-' + k);
    if (el) el.value = v;
    const pageEl = $('page-setting-' + k);
    if (pageEl) pageEl.value = v;
  }
  updatePipelineCalc();
  toast('Applied Turbo preset (16 streams · 8 MiB chunk · 256 MiB RAM)');
});
$('preset-max')?.addEventListener('click', () => {
  for (const [k, v] of Object.entries({ streams: 16, buffer_mb: 512, chunk_mb: 32, limit_mbps: 0 })) {
    const el = $('setting-' + k);
    if (el) el.value = v;
    const pageEl = $('page-setting-' + k);
    if (pageEl) pageEl.value = v;
  }
  updatePipelineCalc();
  toast('Applied Max Saturation preset (16 streams · 32 MiB chunk · 512 MiB RAM)');
});

// Console Files helpers
let lastLoadedFiles = [];
let lastFilesLoaded = false;
let browseHistory = [];
let browseHistoryIndex = -1;

function updateBrowseHistory(path) {
  if (browseHistoryIndex === -1 || browseHistory[browseHistoryIndex] !== path) {
    browseHistory = browseHistory.slice(0, browseHistoryIndex + 1);
    browseHistory.push(path);
    browseHistoryIndex = browseHistory.length - 1;
  }
  updateBrowseNavButtons();
}

function updateBrowseNavButtons() {
  const backBtn = $('files-back-btn');
  const fwdBtn = $('files-fwd-btn');
  if (backBtn) backBtn.disabled = browseHistoryIndex <= 0;
  if (fwdBtn) fwdBtn.disabled = browseHistoryIndex >= browseHistory.length - 1;
}

$('files-back-btn')?.addEventListener('click', () => {
  if (browseHistoryIndex > 0) {
    browseHistoryIndex--;
    const prev = browseHistory[browseHistoryIndex];
    if ($('browse-path')) $('browse-path').value = prev;
    $('browse-form')?.requestSubmit();
    updateBrowseNavButtons();
  }
});

$('files-fwd-btn')?.addEventListener('click', () => {
  if (browseHistoryIndex < browseHistory.length - 1) {
    browseHistoryIndex++;
    const next = browseHistory[browseHistoryIndex];
    if ($('browse-path')) $('browse-path').value = next;
    $('browse-form')?.requestSubmit();
    updateBrowseNavButtons();
  }
});

function renderConsoleFiles(files, filter = '') {
  lastLoadedFiles = files || [];
  const tbody = $('files-body');
  if (!tbody) return;
  let list = lastLoadedFiles;
  if (filter) {
    list = list.filter(f => f.name.toLowerCase().includes(filter));
  }
  if (list.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; padding: 24px; color: var(--muted);">${filter ? 'No matching files found.' : 'No files in this directory.'}</td></tr>`;
    return;
  }
  const curFolder = ($('browse-path')?.value || '/').replace(/\/$/, '');
  tbody.innerHTML = list.map(f => {
    const fullPath = curFolder === '' ? `/${f.name}` : `${curFolder}/${f.name}`;
    return `<tr>
    <td><div class="file-cell">${f.type === 'dir' ? `<button type="button" class="folder-button text-button" data-folder="${escaped(f.name)}"><span class="folder-icon">📁</span><strong>${escaped(f.name)}</strong></button>` : `<svg class="file-icon"><use href="#i-file"/></svg><strong>${escaped(f.name)}</strong>`}</div></td>
    <td>${f.type === 'dir' ? 'Folder' : f.name.endsWith('.pkg') ? 'Package' : f.name.endsWith('.ps5part') ? 'Partial' : 'File'}</td>
    <td>${f.size !== '' ? bytes(Number(f.size)) : '—'}</td>
    <td>${f.time || '—'}</td>
    <td class="right"><button type="button" class="icon-btn tiny file-copy-path-btn" data-path="${escaped(fullPath)}" title="Copy remote path">Copy</button></td>
  </tr>`;
  }).join('');
}

$('files-body')?.addEventListener('click', e => {
  const folderBtn = e.target.closest('.folder-button');
  if (folderBtn) {
    const folderName = folderBtn.dataset.folder;
    if (!folderName) return;
    const cur = ($('browse-path')?.value || '/').replace(/\/$/, '');
    const target = cur === '' ? `/${folderName}` : `${cur}/${folderName}`;
    if ($('browse-path')) $('browse-path').value = target;
    $('browse-form')?.requestSubmit();
    return;
  }
  const copyBtn = e.target.closest('.file-copy-path-btn');
  if (copyBtn) {
    const path = copyBtn.dataset.path;
    if (path) {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(path).then(() => toast(`Copied path: ${path}`)).catch(() => toast(`Path: ${path}`));
      } else {
        toast(`Path: ${path}`);
      }
    }
  }
});

$('files-refresh-btn')?.addEventListener('click', () => {
  $('browse-form')?.requestSubmit();
});

$('files-search')?.addEventListener('input', e => {
  renderConsoleFiles(lastLoadedFiles, e.target.value.trim().toLowerCase());
});

$('change-preview-file')?.addEventListener('click', () => {
  if (sourceKind === 'url') {
    $('source-urls')?.focus();
  } else {
    $('pick-file')?.click();
  }
});

// Log export
$('export-log')?.addEventListener('click', () => {
  const text = state.logs.map(l => `[${l.time}] ${l.level.toUpperCase()} ${l.message}`).join('\n');
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = 'Direct-Stream-PlayStation-5-Activity.txt';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

// Sound toggle
$('sound-toggle')?.addEventListener('click', () => {
  soundEnabled = !soundEnabled;
  localStorage.setItem('ps5-sound', soundEnabled);
  updateSoundIcon();
  if (soundEnabled) playPs5Chime();
  toast(soundEnabled ? 'PlayStation 5 notification chimes enabled' : 'Chimes muted');
});

// Quit
$('quit')?.addEventListener('click', async () => {
  if (!confirm(state?.active ? 'Pause the active transfer and quit DIRECT STREAM FOR PLAYSTATION 5? Partial data will remain on PS5.' : 'Quit DIRECT STREAM FOR PLAYSTATION 5? Closing the browser tab alone leaves the server running.')) return;
  const r = await perform('shutdown', {});
  if (r) {
    $('offline-banner').hidden = false;
    $('offline-banner').textContent = 'DIRECT STREAM FOR PLAYSTATION 5 has stopped. You can close this tab.';
  }
});
$('mobile-quit')?.addEventListener('click', () => $('quit')?.click());

// Global Drag & Drop for packages onto browser
window.addEventListener('dragenter', e => {
  if (e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files')) {
    $('drop-overlay').hidden = false;
  }
});
$('drop-overlay').addEventListener('dragover', e => {
  e.preventDefault();
});
$('drop-overlay').addEventListener('dragleave', e => {
  if (e.target === $('drop-overlay')) {
    $('drop-overlay').hidden = true;
  }
});
$('drop-overlay').addEventListener('drop', e => {
  e.preventDefault();
  $('drop-overlay').hidden = true;
  const files = Array.from(e.dataTransfer.files || []);
  if (files.length > 0) {
    openAdd('local');
    const names = files.map(f => f.path || f.name).join('\n');
    $('local-path').value = names;
    toast(`Dropped ${files.length} file(s) into New Transfer`);
  }
});

// Keyboard shortcuts
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (!document.querySelector('dialog[open]')) openAdd();
  }
});

// Close open dropdowns when clicking outside
document.addEventListener('click', e => {
  document.querySelectorAll('details[open]').forEach(el => {
    if (!el.contains(e.target)) el.removeAttribute('open');
  });
});

// Position action menu
document.addEventListener('toggle', e => {
  const el = e.target;
  if (el.tagName !== 'DETAILS' || !el.open) return;
  const rect = el.getBoundingClientRect();
  const menu = el.querySelector('.action-menu');
  if (!menu) return;
  menu.style.setProperty('--menu-x', Math.max(8, Math.min(innerWidth - 154, rect.right - 146)) + 'px');
  menu.style.setProperty('--menu-y', Math.max(8, Math.min(innerHeight - menu.offsetHeight - 8, rect.bottom + 4)) + 'px');
}, true);

// Start polling
tick();
