/* enjambre dashboard. No build step, no framework, no innerHTML with data:
   titles and results come from agents and are treated as untrusted text. */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const state = {
    token: '', overview: null, tasks: [], traces: [], tab: 'swarm', drawer: null,
    graph: null, graphData: null, focus: null, neighbors: new Set(), hidden: new Set(), alive: new Set(),
  };
  try { state.token = sessionStorage.getItem('enjambre-token') || ''; } catch (_) { /* storage blocked */ }

  // ------------------------------------------------------------------ helpers
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'style') el.style.cssText = value;
      else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  const fill = (el, ...children) => el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const now = () => Date.now() / 1000;
  function ago(ts) {
    if (!ts) return '';
    const s = Math.max(0, now() - ts);
    if (s < 60) return `${Math.floor(s)}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${Math.floor(s / 3600)}h`;
    return `${Math.floor(s / 86400)}d`;
  }
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  const shortWorker = (w) => (w || '').replace(/^scheduler\//, '');
  function toast(message) {
    const el = $('#toast');
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  async function api(path, options = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const res = await fetch(path, { ...options, headers });
    if (res.status === 401) { askToken(); throw new Error('token required'); }
    const data = await res.json().catch(() => null);
    if (!res.ok && res.status !== 409) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  function askToken() {
    const dialog = $('#token-dialog');
    if (dialog.open) return;
    $('#token-input').value = '';
    dialog.showModal();
  }
  $('#token-dialog').addEventListener('close', () => {
    state.token = $('#token-input').value.trim();
    try { sessionStorage.setItem('enjambre-token', state.token); } catch (_) { /* storage blocked */ }
    refresh();
  });

  // ------------------------------------------------------------------- render
  function setLive(ok, why) {
    const live = $('#live');
    live.classList.toggle('off', !ok);
    live.lastElementChild.textContent = ok ? 'live' : 'offline';
    live.title = ok ? 'Connected to the kernel' : `Disconnected: ${why || ''}`;
  }

  function renderHeader() {
    const ov = state.overview;
    $('#swarm-name').textContent = ov.name;
    $('#version').textContent = `enjambre ${ov.version}`;
    document.title = `${ov.name} · enjambre`;
    const sky = $('#sky');
    if (ov.router) {
      sky.hidden = false;
      sky.textContent = ov.router.solar ? '☀ solar window open' : '☾ no sun';
      sky.classList.toggle('sun', !!ov.router.solar);
    }
  }

  function renderKpis() {
    const t = state.overview.stats.tasks;
    const done = state.tasks.filter((x) => x.status === 'done');
    const verified = done.filter((x) => x.verification === 'verified').length;
    const items = [
      ['queued', t.pending, ''], ['running', t.running, 'running'], ['done', t.done, 'done'],
      ['failed · dead', t.failed + t.dead, 'failed'], ['cancelled', t.cancelled, ''],
      ['proof verified', done.length ? `${Math.round((verified / done.length) * 100)}%` : '—', 'done'],
    ];
    $('#kpis').replaceChildren(...items.map(([label, value, cls]) => h('div', { class: `kpi ${cls}` }, h('small', null, label), h('b', null, value))));
  }

  const COLUMNS = [
    ['Queued', ['pending'], 'var(--pending)'],
    ['Running', ['running'], 'var(--running)'],
    ['Done', ['done'], 'var(--done)'],
    ['Stopped', ['failed', 'dead', 'cancelled'], 'var(--failed)'],
  ];

  function taskCard(t) {
    const who = t.assignee ? shortWorker(t.assignee) : (t.agent || 'router');
    const chips = [];
    if (t.status === 'pending' && t.blocked_by.length) chips.push(h('span', { class: 'chip' }, `waits for ${t.blocked_by.length}`));
    if (t.operation) chips.push(h('span', { class: 'chip op' }, t.operation));
    if (t.verification === 'verified') chips.push(h('span', { class: 'chip ok' }, 'proof ✓'));
    if (t.verification === 'rejected') chips.push(h('span', { class: 'chip bad' }, 'proof ✗'));
    if (t.error_code && t.status !== 'done') chips.push(h('span', { class: t.status === 'pending' ? 'chip warn' : 'chip bad' }, t.error_code));
    return h('button', { class: `task s-${t.status}`, type: 'button', onclick: () => openTask(t.id) },
      h('div', { class: 'task-top' },
        h('span', { class: `prio p${t.priority}` }, `P${t.priority}`),
        h('span', { class: 'who' }, who),
        t.attempts > 1 || (t.status === 'pending' && t.attempts > 0) ? h('span', { class: 'retry' }, `try ${t.attempts}/${t.max_attempts}`) : null),
      h('div', { class: 'task-title' }, t.title),
      h('div', { class: 'task-meta' }, chips, h('span', { class: 'time' }, ago(t.updated_at))));
  }

  function renderBoard() {
    const columns = COLUMNS.map(([label, statuses, color]) => {
      let items = state.tasks.filter((t) => statuses.includes(t.status));
      if (statuses[0] === 'pending') items.sort((a, b) => a.priority - b.priority || a.created_at - b.created_at);
      const shown = items.slice(0, 14);
      return h('div', { class: 'column' },
        h('div', { class: 'column-head' }, h('span', { class: 'dot', style: `background:${color}` }), h('b', null, label), h('span', null, items.length)),
        h('div', { class: 'column-body' },
          shown.length ? shown.map(taskCard) : h('div', { class: 'empty' }, 'nothing here'),
          items.length > shown.length ? h('div', { class: 'more' }, `+${items.length - shown.length} more`) : null));
    });
    $('#columns').replaceChildren(...columns);
  }

  function renderAgents() {
    const ov = state.overview;
    const procs = new Map(ov.processes.map((p) => [p.id, p]));
    const ranking = new Map(((ov.router && ov.router.ranking) || []).map((r) => [r.agent, r]));
    const pick = ov.router && ov.router.agent;
    $('#router-pick').textContent = pick ? `router → ${pick}` : '';
    const select = $('#enqueue select[name="agent"]');
    const wanted = ['', ...ov.agents.map((a) => a.id)];
    if ([...select.options].map((o) => o.value).join() !== wanted.join()) {
      select.replaceChildren(h('option', { value: '' }, 'router picks'), ...ov.agents.map((a) => h('option', { value: a.id }, a.name)));
    }
    $('#agents').replaceChildren(...ov.agents.map((a) => {
      const p = procs.get(a.id);
      const fit = ov.fitness[`scheduler/${a.id}`] || ov.fitness[a.id];
      const rank = ranking.get(a.id);
      const energy = a.energy === 'solar' ? h('span', { class: 'energy-solar', title: 'solar powered' }, '☀') : a.energy === 'grid' ? h('span', { title: 'grid' }, '⌁') : h('span', { title: 'always on' }, '∞');
      const sub = [a.host || a.adapter, fit && fit.success_rate !== null ? `${Math.round(fit.success_rate * 100)}% ok` : 'no runs yet', fit && fit.median_ms ? `${(fit.median_ms / 1000).toFixed(1)}s` : null].filter(Boolean).join(' · ');
      return h('div', { class: `agent${a.id === pick ? ' pick' : ''}` },
        h('div', { class: 'avatar' }, a.name.slice(0, 1).toUpperCase(), h('span', { class: `state ${p ? p.state : ''}`, title: p ? p.state : 'never seen' })),
        h('div', { class: 'agent-main' },
          h('div', { class: 'agent-name' }, a.name, energy, a.route ? null : h('span', { class: 'chip' }, 'pinned only')),
          p && p.task ? h('div', { class: 'agent-task' }, p.task) : h('div', { class: 'agent-sub' }, sub)),
        rank ? h('div', { class: 'score', title: Object.entries(rank.parts).map(([k, v]) => `${k} ${v}`).join(' · ') },
          h('b', null, rank.score),
          h('div', { class: 'bars' }, Object.values(rank.parts).map((v) => h('i', { style: `height:${4 + v * 0.14}px` })))) : h('div', { class: 'score muted small' }, '—'));
    }));
  }

  function renderLeases() {
    const leases = state.overview.leases;
    if (!leases.length) { $('#leases').replaceChildren(h('div', { class: 'empty' }, 'no resources declared')); return; }
    $('#leases').replaceChildren(...leases.map((l) => {
      if (l.free) return h('div', { class: 'lease' }, h('code', null, l.resource), h('div', { class: 'track' }), h('span', { class: 'free' }, 'free'));
      const total = Math.max(1, l.expires_at - l.since);
      const pct = Math.max(0, Math.min(100, (l.remaining_s / total) * 100));
      return h('div', { class: 'lease', title: l.purpose || '' },
        h('code', null, l.resource),
        h('div', { class: 'track' }, h('div', { class: 'fill', style: `width:${pct}%` })),
        h('span', null, `${l.holder} · ${Math.ceil(l.remaining_s / 60)}m`));
    }));
  }

  function renderTrace() {
    const titles = new Map(state.tasks.map((t) => [t.id, t.title]));
    const rows = state.traces.slice(-40).reverse();
    $('#trace').replaceChildren(...rows.map((tr) => h('li', { onclick: () => openTask(tr.task_id), title: tr.detail },
      h('time', null, clock(tr.at)),
      h('span', { class: `ev ${tr.event}` }, tr.event),
      h('span', { class: 'what' }, titles.get(tr.task_id) || tr.task_id, tr.actor ? h('span', { class: 'muted' }, ` · ${shortWorker(tr.actor)}`) : null))));
  }

  function render() {
    renderHeader();
    renderKpis();
    renderBoard();
    renderAgents();
    renderLeases();
    renderTrace();
  }

  async function refresh() {
    try {
      const [overview, tasks, traces] = await Promise.all([api('/api/overview'), api('/api/tasks?limit=300'), api('/api/traces?limit=80')]);
      Object.assign(state, { overview, tasks, traces });
      setLive(true);
      render();
      if (state.drawer) openTask(state.drawer, true);
    } catch (err) {
      setLive(false, err.message);
    }
  }

  // ------------------------------------------------------------------- drawer
  async function openTask(id, quiet) {
    let task;
    try { task = await api(`/api/tasks/${encodeURIComponent(id)}`); } catch (err) { if (!quiet) toast(err.message); return; }
    state.drawer = id;
    const titles = new Map(state.tasks.map((t) => [t.id, t.title]));
    const drawer = $('#drawer');
    const row = (label, value) => (value === '' || value === null || value === undefined ? null : [h('dt', null, label), h('dd', null, value)]);
    const actions = [];
    if (['failed', 'dead', 'cancelled'].includes(task.status)) actions.push(h('button', { class: 'btn', onclick: () => act(id, 'retry') }, 'Retry'));
    if (['pending', 'running'].includes(task.status) && !task.cancel_requested) actions.push(h('button', { class: 'btn danger', onclick: () => act(id, 'cancel') }, 'Cancel'));
    fill(drawer,
      h('button', { class: 'btn ghost close', onclick: closeDrawer, 'aria-label': 'Close' }, '✕'),
      h('span', { class: `chip ${task.status === 'done' ? 'ok' : ['failed', 'dead'].includes(task.status) ? 'bad' : ''}` }, task.status),
      h('h3', null, task.title),
      task.detail ? h('p', { class: 'muted' }, task.detail) : null,
      h('dl', { class: 'meta' },
        row('id', h('code', null, task.id)),
        row('agent', task.agent || 'router'),
        row('worker', shortWorker(task.assignee)),
        row('attempts', `${task.attempts} / ${task.max_attempts}`),
        row('priority', `P${task.priority}`),
        row('operation', task.operation),
        row('project', task.project),
        row('proof', task.proof ? h('code', null, task.proof) : ''),
        row('verification', task.verification === 'n/a' ? '' : `${task.verification}${task.verification_detail ? ` — ${task.verification_detail}` : ''}`),
        row('error', task.error_code),
        row('after', task.depends_on.length ? task.depends_on.map((d) => h('button', { class: 'linklike', onclick: () => openTask(d) }, titles.get(d) || d)) : '')),
      actions.length ? h('div', { class: 'actions' }, actions) : null,
      task.result ? h('pre', { class: 'result' }, task.result) : null,
      h('h4', null, 'Trace'),
      h('ol', { class: 'timeline' }, task.traces.map((tr) => h('li', { class: tr.event },
        h('b', null, tr.event), ' ', h('span', { class: 'muted' }, `${clock(tr.at)}${tr.actor ? ` · ${shortWorker(tr.actor)}` : ''}`),
        tr.detail ? h('div', { class: 'muted' }, tr.detail) : null))));
    drawer.hidden = false;
  }
  function closeDrawer() { state.drawer = null; $('#drawer').hidden = true; }
  async function act(id, action) {
    try {
      const res = await api(`/api/tasks/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: JSON.stringify({ actor: 'dashboard' }) });
      toast(res.ok ? `${action} ✓` : (res.reason || 'refused'));
      refresh();
    } catch (err) { toast(err.message); }
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeDrawer(); clearFocus(); } });

  $('#enqueue').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = { title: form.title.value.trim(), agent: form.agent.value, priority: Number(form.priority.value), creator: 'dashboard' };
    if (!body.title) return;
    try {
      const res = await api('/api/tasks', { method: 'POST', body: JSON.stringify(body) });
      if (res.ok) { form.title.value = ''; toast('queued'); refresh(); } else toast(res.reason || 'refused');
    } catch (err) { toast(err.message); }
  });

  // --------------------------------------------------------------------- tabs
  document.querySelectorAll('.tabs button').forEach((btn) => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));
  function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    $('#tab-swarm').hidden = tab !== 'swarm';
    $('#tab-memory').hidden = tab !== 'memory';
    if (tab === 'memory') openMemory(); else if (state.graph) state.graph.pauseAnimation();
    if (tab === 'swarm') refresh();
  }

  // ------------------------------------------------------------------- memory
  const idOf = (x) => (typeof x === 'object' ? x.id : x);

  function loadGraphLibrary() {
    if (window.ForceGraph3D) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = h('script', { src: 'vendor/3d-force-graph.min.js' });
      script.onload = resolve;
      script.onerror = () => reject(new Error('could not load the 3D library'));
      document.head.append(script);
    });
  }
  function webgl() {
    try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); } catch (_) { return false; }
  }
  function emptyMemory(message) {
    const el = $('#memory-empty');
    el.replaceChildren(h('div', null, h('h3', null, 'Memory'), h('p', null, message)));
    el.hidden = false;
  }

  function nodeColor(n) {
    if (state.focus && n.id !== state.focus && !state.neighbors.has(n.id)) return 'rgba(90,100,125,0.35)';
    return n.color;
  }
  function linkTouchesFocus(l) { return state.focus && (idOf(l.source) === state.focus || idOf(l.target) === state.focus); }
  function linkAlive(l) { return state.alive.has(idOf(l.source)) || state.alive.has(idOf(l.target)); }

  async function openMemory() {
    if (state.graph) { state.graph.resumeAnimation(); return; }
    if (!state.overview) await refresh();
    if (!state.overview || !state.overview.memory) { emptyMemory('This swarm has no memory folder. Set memory.dir in swarm.yaml and write a few markdown notes with [[links]].'); return; }
    if (!webgl()) { emptyMemory('Your browser has no WebGL, so the 3D graph cannot render. Search still works through the API.'); return; }
    try {
      await loadGraphLibrary();
      state.graphData = await api('/api/memory/graph');
    } catch (err) { emptyMemory(err.message); return; }
    const data = state.graphData;
    const el = $('#graph');
    const graph = window.ForceGraph3D({ controlType: 'orbit' })(el)
      .backgroundColor('#04060c')
      .showNavInfo(false)
      .nodeLabel((n) => `<div class="tip"><b>${esc(n.label)}</b><br><span>${esc(n.group)} · ${n.degree} link${n.degree === 1 ? '' : 's'}</span></div>`)
      .nodeColor(nodeColor)
      .nodeVal((n) => (n.group === 'missing' ? 1 : 2 + Math.min(n.degree, 14) * 1.1))
      .nodeOpacity(0.95)
      .nodeResolution(18)
      .nodeVisibility((n) => !state.hidden.has(n.group))
      .linkVisibility((l) => !state.hidden.has(state.byId.get(idOf(l.source))?.group) && !state.hidden.has(state.byId.get(idOf(l.target))?.group))
      .linkColor((l) => (linkTouchesFocus(l) ? '#8af7cf' : 'rgba(150,170,210,0.32)'))
      .linkWidth((l) => (linkTouchesFocus(l) ? 1.6 : 0.35))
      .linkOpacity(0.55)
      .linkDirectionalParticles((l) => (linkTouchesFocus(l) ? 4 : linkAlive(l) ? 1 : 0))
      .linkDirectionalParticleColor(() => '#8af7cf')
      .linkDirectionalParticleWidth(2.2)
      .linkDirectionalParticleSpeed(0.005)
      .onNodeClick(focusNode)
      .onBackgroundClick(clearFocus)
      .cooldownTime(5000)
      .onEngineStop(() => {
        if (state.fitted || !state.graph) return;
        state.fitted = true;
        state.graph.zoomToFit(700, 90);
      })
      .graphData({ nodes: data.nodes.map((n) => ({ ...n })), links: data.links.map((l) => ({ ...l })) });
    state.graph = graph;
    state.byId = new Map(graph.graphData().nodes.map((n) => [n.id, n]));
    const size = () => graph.width(el.clientWidth).height(el.clientHeight);
    new ResizeObserver(size).observe(el);
    size();
    renderLegend();
    const s = data.stats;
    $('#memory-stats').textContent = `${s.notes} notes · ${s.links} links · ${s.missing} not written yet`;
    refreshPulse();
    setInterval(() => { if (state.tab === 'memory' && !document.hidden) refreshPulse(); }, 30000);
  }

  function repaint() {
    const g = state.graph;
    g.nodeColor(g.nodeColor()).linkColor(g.linkColor()).linkWidth(g.linkWidth())
      .linkDirectionalParticles(g.linkDirectionalParticles()).nodeVisibility(g.nodeVisibility()).linkVisibility(g.linkVisibility());
  }

  async function refreshPulse() {
    try {
      const pulse = await api('/api/memory/pulse?minutes=240');
      state.alive = new Set(Object.keys(pulse.alive));
      if (state.graph) repaint();
    } catch (_) { /* the graph stays usable without pulse */ }
  }

  function renderLegend() {
    $('#memory-legend').replaceChildren(...state.graphData.groups.map((g) => h('button', {
      type: 'button', 'aria-pressed': String(!state.hidden.has(g.name)),
      onclick: (e) => {
        if (state.hidden.has(g.name)) state.hidden.delete(g.name); else state.hidden.add(g.name);
        e.currentTarget.setAttribute('aria-pressed', String(!state.hidden.has(g.name)));
        repaint();
      },
    }, h('i', { style: `background:${g.color}` }), `${g.name} ${g.count}`)));
  }

  function focusNode(node) {
    if (!state.graph || !node) return;
    const n = typeof node === 'string' ? state.byId.get(node) : node;
    if (!n) return;
    state.focus = n.id;
    state.neighbors = new Set();
    for (const l of state.graph.graphData().links) {
      if (idOf(l.source) === n.id) state.neighbors.add(idOf(l.target));
      if (idOf(l.target) === n.id) state.neighbors.add(idOf(l.source));
    }
    repaint();
    const distance = 110;
    const r = Math.hypot(n.x || 1, n.y || 1, n.z || 1);
    const k = 1 + distance / r;
    state.graph.cameraPosition({ x: (n.x || 1) * k, y: (n.y || 1) * k, z: (n.z || 1) * k }, n, 900);
    const inspector = $('#memory-inspector');
    fill(inspector,
      h('button', { class: 'btn ghost close', style: 'float:right', onclick: clearFocus, 'aria-label': 'Close' }, '✕'),
      h('span', { class: 'chip', style: `color:${n.color};border-color:${n.color}` }, n.group),
      h('h3', null, n.label),
      n.description ? h('p', { class: 'muted' }, n.description) : null,
      n.src ? h('p', { class: 'small' }, h('code', null, n.src)) : h('p', { class: 'small muted' }, 'Nobody wrote this note yet. Create it and the red dot turns into knowledge.'),
      n.tags && n.tags.length ? h('p', null, n.tags.map((t) => h('span', { class: 'chip' }, `#${t}`))) : null,
      h('div', { class: 'neighbors' }, [...state.neighbors].map((id) => state.byId.get(id)).filter(Boolean)
        .map((m) => h('button', { type: 'button', onclick: () => focusNode(m) }, m.label))));
    inspector.hidden = false;
  }

  function clearFocus() {
    if (!state.focus) return;
    state.focus = null;
    state.neighbors = new Set();
    $('#memory-inspector').hidden = true;
    if (state.graph) repaint();
  }

  let searchTimer;
  $('#memory-search').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    searchTimer = setTimeout(async () => {
      if (q.length < 3) { $('#memory-results').replaceChildren(); return; }
      try {
        const res = await api(`/api/memory/query?q=${encodeURIComponent(q)}&limit=8`);
        $('#memory-results').replaceChildren(...res.results.map((r) => h('li', null, h('button', { type: 'button', onclick: () => focusNode(r.id) },
          h('b', null, r.label), h('span', null, r.description || r.group)))));
      } catch (err) { toast(err.message); }
    }, 220);
  });

  // --------------------------------------------------------------------- boot
  refresh();
  setInterval(() => { if (!document.hidden && state.tab === 'swarm') refresh(); }, 2000);
})();
