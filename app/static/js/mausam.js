/* Mausam shared front-end helpers (vanilla JS). Exposed as window.Mausam. */
(function () {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ---------- API ----------
  function csrf() {
    const m = document.cookie.match(/(?:^|; )mausam_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : ($('meta[name="csrf-token"]') || {}).content || '';
  }

  async function api(method, url, data) {
    const opts = { method, headers: { 'Accept': 'application/json' }, credentials: 'same-origin' };
    if (method !== 'GET') opts.headers['X-CSRF-Token'] = csrf();
    if (data !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(data); }
    let resp;
    try { resp = await fetch(url, opts); }
    catch (e) { throw new Error('Could not reach Mausam. Check your internet connection and try again.'); }
    let json = null;
    try { json = await resp.json(); } catch (e) { /* not JSON */ }
    if (!resp.ok || !json || json.ok === false) {
      throw new Error((json && json.error) || `Request failed (HTTP ${resp.status}).`);
    }
    return json.data;
  }

  // ---------- formatting ----------
  function indian(n) {
    const s = Math.round(Math.abs(n)).toString();
    if (s.length <= 3) return s;
    const last3 = s.slice(-3);
    const rest = s.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',');
    return rest + ',' + last3;
  }
  function inr(v, signed) {
    if (v === null || v === undefined || isNaN(v)) return '–';
    const sign = signed ? (v >= 0 ? '+' : '−') : (v < 0 ? '−' : '');
    return `${sign}₹${indian(v)}`;
  }
  function pct(v, d = 2, arrow = true) {
    if (v === null || v === undefined || isNaN(v)) return '–';
    const sign = v >= 0 ? '+' : '−';
    return `${arrow ? (v >= 0 ? '▲ ' : '▼ ') : ''}${sign}${Math.abs(v).toFixed(d)}%`;
  }
  function pctHTML(v, d = 2) {
    if (v === null || v === undefined || isNaN(v)) return '<span class="num muted">–</span>';
    return `<span class="num ${v >= 0 ? 'gain' : 'loss'}">${pct(v, d)}</span>`;
  }
  function pts(v, d = 2) {
    if (v === null || v === undefined || isNaN(v)) return '–';
    return `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d)} pts`;
  }
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function date(iso, short) {
    if (!iso) return '–';
    const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
    return short ? `${d} ${MONTHS[m - 1]}` : `${d} ${MONTHS[m - 1]} ${y}`;
  }
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ---------- UI bits ----------
  function toast(message, type) {
    const host = $('#toasts');
    if (!host) return alert(message);
    const el = document.createElement('div');
    el.className = 'toast' + (type === 'error' ? ' error' : '');
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => el.remove(), type === 'error' ? 7000 : 4000);
  }

  function confirmDialog(title, body, okLabel = 'Confirm') {
    const dlg = $('#confirm-dialog');
    if (!dlg || !dlg.showModal) return Promise.resolve(window.confirm(`${title}\n\n${body}`));
    $('#confirm-title').textContent = title;
    $('#confirm-body').textContent = body;
    $('#confirm-ok').textContent = okLabel;
    dlg.showModal();
    return new Promise((resolve) => {
      const done = (v) => { dlg.close(); $('#confirm-ok').onclick = null; $('#confirm-cancel').onclick = null; resolve(v); };
      $('#confirm-ok').onclick = () => done(true);
      $('#confirm-cancel').onclick = () => done(false);
      dlg.addEventListener('cancel', () => resolve(false), { once: true });
    });
  }

  async function busy(btn, fn) {
    if (!btn) return fn();
    btn.setAttribute('aria-busy', 'true'); btn.disabled = true;
    try { return await fn(); }
    finally { btn.removeAttribute('aria-busy'); btn.disabled = false; }
  }

  function data(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function icons() { if (window.lucide) window.lucide.createIcons(); }

  // ---------- tooltips for finance terms ----------
  let tipEl = null;
  function showTip(target) {
    hideTip();
    const text = target.getAttribute('data-tip');
    if (!text) return;
    tipEl = document.createElement('div');
    tipEl.className = 'tooltip'; tipEl.setAttribute('role', 'tooltip'); tipEl.textContent = text;
    document.body.appendChild(tipEl);
    const r = target.getBoundingClientRect(); const t = tipEl.getBoundingClientRect();
    let left = Math.min(Math.max(8, r.left + r.width / 2 - t.width / 2), window.innerWidth - t.width - 8);
    let top = r.top - t.height - 8; if (top < 8) top = r.bottom + 8;
    tipEl.style.left = left + 'px'; tipEl.style.top = top + 'px';
  }
  function hideTip() { if (tipEl) { tipEl.remove(); tipEl = null; } }
  document.addEventListener('mouseover', (e) => { const t = e.target.closest('[data-tip]'); if (t) showTip(t); });
  document.addEventListener('mouseout', (e) => { if (e.target.closest('[data-tip]')) hideTip(); });
  document.addEventListener('focusin', (e) => { const t = e.target.closest('[data-tip]'); if (t) showTip(t); });
  document.addEventListener('focusout', hideTip);
  document.addEventListener('click', (e) => { const t = e.target.closest('.term'); if (t) { e.preventDefault(); tipEl ? hideTip() : showTip(t); } });
  window.addEventListener('scroll', hideTip, { passive: true });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideTip(); });

  // ---------- sortable tables ----------
  function sortable(table) {
    $$('th[data-sort]', table).forEach((th, idx) => {
      th.tabIndex = 0;
      const col = Array.from(th.parentNode.children).indexOf(th);
      const go = () => {
        const tbody = table.tBodies[0];
        const dir = th.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
        $$('th[data-sort]', table).forEach((h) => h.removeAttribute('aria-sort'));
        th.setAttribute('aria-sort', dir);
        const type = th.dataset.sort;
        const rows = Array.from(tbody.rows);
        rows.sort((a, b) => {
          const av = a.cells[col].dataset.value ?? a.cells[col].textContent.trim();
          const bv = b.cells[col].dataset.value ?? b.cells[col].textContent.trim();
          let cmp = type === 'num' ? (parseFloat(av) || -Infinity) - (parseFloat(bv) || -Infinity) : av.localeCompare(bv);
          return dir === 'ascending' ? cmp : -cmp;
        });
        rows.forEach((r) => tbody.appendChild(r));
      };
      th.addEventListener('click', go);
      th.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
  }

  // ---------- charts ----------
  const FALLBACK_COLOURS = ['#0E7C7B', '#D9822B', '#4C5FD5', '#8E4585', '#2A9DCC', '#6E8B3D', '#D1495B', '#5C6B80'];
  function palette() {
    return { ink: css('--ink'), slate: css('--slate'), line: css('--line'), monsoon: css('--monsoon'), saffron: css('--saffron'),
      plum: css('--plum'), gain: css('--gain'), loss: css('--loss'), surface: css('--surface'), fund: FALLBACK_COLOURS };
  }
  function alpha(hex, a) {
    const h = hex.replace('#', '');
    if (h.length !== 6) return hex;
    const n = parseInt(h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  function chartDefaults() {
    if (!window.Chart) return;
    const p = palette();
    Chart.defaults.font.family = css('--font-body') || 'Figtree, system-ui, sans-serif';
    Chart.defaults.font.size = 12;
    Chart.defaults.color = p.slate;
    Chart.defaults.borderColor = p.line;
    Chart.defaults.plugins.legend.labels.boxWidth = 10;
    Chart.defaults.plugins.legend.labels.boxHeight = 10;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.tooltip.backgroundColor = p.ink;
    Chart.defaults.plugins.tooltip.titleColor = p.surface;
    Chart.defaults.plugins.tooltip.bodyColor = p.surface;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
    Chart.defaults.elements.line.borderWidth = 2;
    Chart.defaults.elements.point.radius = 0;
    Chart.defaults.elements.point.hoverRadius = 4;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.animation.duration = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 400;
  }
  function timeScale(extra) {
    return Object.assign({ type: 'time', time: { tooltipFormat: 'd MMM yyyy' }, grid: { display: false },
      ticks: { maxTicksLimit: 7, autoSkip: true } }, extra || {});
  }
  const charts = {};
  function chart(id, config) {
    const el = document.getElementById(id);
    if (!el || !window.Chart) return null;
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(el, config);
    return charts[id];
  }
  function sparkline(canvas, points, colour) {
    if (!window.Chart || !canvas || !points || points.length < 2) return;
    const up = points[points.length - 1][1] >= points[0][1];
    const c = colour || (up ? palette().gain : palette().loss);
    new Chart(canvas, {
      type: 'line',
      data: { datasets: [{ data: points.map(([x, y]) => ({ x, y })), borderColor: c, borderWidth: 1.6, fill: true,
        backgroundColor: alpha(c, 0.10), tension: 0.3 }] },
      options: { animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { type: 'time', display: false }, y: { display: false } }, events: [] },
    });
  }

  // ---------- shell: theme, sidebar, mobile sheet ----------
  function initShell() {
    const root = document.documentElement;
    const themeBtn = $('#theme-btn');
    const setIcon = () => {
      const dark = root.getAttribute('data-theme') === 'dark' ||
        (!root.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
      if (themeBtn) themeBtn.innerHTML = `<i data-lucide="${dark ? 'sun' : 'moon'}" aria-hidden="true"></i>`;
      icons();
    };
    if (themeBtn) themeBtn.addEventListener('click', () => {
      const dark = root.getAttribute('data-theme') === 'dark' ||
        (!root.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
      const next = dark ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('mausam-theme', next); } catch (e) {}
      setIcon();
      chartDefaults();
      Object.values(charts).forEach((c) => c.update());
      document.dispatchEvent(new CustomEvent('mausam:theme'));
    });
    const shell = $('#shell');
    if (root.classList.contains('sidebar-collapsed') && shell) shell.classList.add('collapsed');
    const cb = $('#collapse-btn');
    if (cb) cb.addEventListener('click', () => {
      shell.classList.toggle('collapsed');
      try { localStorage.setItem('mausam-sidebar', shell.classList.contains('collapsed') ? 'collapsed' : 'open'); } catch (e) {}
    });
    const more = $('#more-btn'), sheet = $('#more-sheet');
    if (more && sheet) {
      more.addEventListener('click', () => { sheet.hidden = false; more.setAttribute('aria-expanded', 'true'); });
      sheet.addEventListener('click', (e) => { if (e.target.hasAttribute('data-close')) { sheet.hidden = true; more.setAttribute('aria-expanded', 'false'); } });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') sheet.hidden = true; });
    }
    setIcon();
  }

  chartDefaults();
  document.addEventListener('DOMContentLoaded', () => {
    initShell();
    $$('table[data-sortable]').forEach(sortable);
    icons();
  });

  window.Mausam = { $, $$, api, inr, pct, pctHTML, pts, date, esc, toast, confirm: confirmDialog, busy, data, icons,
    sortable, palette, alpha, chart, sparkline, timeScale, chartDefaults, FUND_COLOURS: FALLBACK_COLOURS };
})();
