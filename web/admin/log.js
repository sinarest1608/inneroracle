// Admin push-log viewer. The token lives in sessionStorage and is only ever
// sent as the X-Admin-Token header (never in a URL). Everything the server
// returns is rendered with textContent / DOM APIs.

(function(){
  const TOKEN_KEY = 'inneroracle.adminToken';
  const $ = id => document.getElementById(id);
  const unlockForm = $('unlock'), panel = $('panel'), forgetBtn = $('forget');
  const filters = $('filters'), out = $('out'), msg = $('msg'), csvBtn = $('csv');
  let tab = 'log';

  // Same day/night choice as the chat UI, if one was saved.
  try { const t = localStorage.getItem('inneroracle.theme'); if (t) document.documentElement.dataset.theme = t; } catch {}

  const getToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; } };
  const setToken = v => { try { v ? sessionStorage.setItem(TOKEN_KEY, v) : sessionStorage.removeItem(TOKEN_KEY); } catch {} };

  function node(tag, cls, text){
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function say(text, err){ msg.hidden = !text; msg.textContent = text || ''; msg.className = 'adm-msg' + (err ? ' err' : ''); }

  function showUnlocked(on){
    unlockForm.hidden = on; panel.hidden = !on; forgetBtn.hidden = !on;
    if (!on){ out.replaceChildren(); say(''); $('token').focus(); }
  }

  // ---- query + fetch ----
  function query(extra){
    const p = new URLSearchParams();
    for (const el of filters.elements){
      if (!el.name || !el.value.trim()) continue;
      const label = el.closest('label');
      if (label && !label.dataset.for.split(' ').includes(tab)) continue;
      p.set(el.name, el.value.trim());
    }
    if (extra) for (const k in extra) p.set(k, extra[k]);
    const q = p.toString();
    return '/push/' + tab + (q ? '?' + q : '');
  }

  async function call(url){
    const r = await fetch(url, { headers: { 'X-Admin-Token': getToken() }, cache: 'no-store', referrerPolicy: 'no-referrer' });
    if (r.status === 401 || r.status === 403){ setToken(''); showUnlocked(false); say('Token rejected.', true); return null; }
    if (r.status === 404){ say('Not found. Is push enabled on this server?', true); return null; }
    if (!r.ok){ say('Request failed (HTTP ' + r.status + ').', true); return null; }
    return r;
  }

  // ---- rendering ----
  const pad = n => String(n).padStart(2, '0');
  function splitTime(v){
    const d = new Date(v);
    if (typeof v !== 'string' || isNaN(d)) return [v, ''];
    return [d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()),
            pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds()) + ' UTC'];
  }
  const fmt = v => (v === null || v === undefined || v === '') ? '—'
    : (typeof v === 'object' ? JSON.stringify(v) : String(v));

  // Deliveries get the plan's column layout; anything else is shown generically.
  const DELIVERY_COLS = [
    ['Date', r => r.local_date || splitTime(r.sent_at)[0]], ['Time', r => r.local_time || splitTime(r.sent_at)[1]],
    ['Device #', r => r.device_id], ['Kind', r => r.kind], ['Verse', r => r.verse_id],
    ['Speaker', r => r.speaker ? r.speaker + (r.speaker_source ? ' (' + r.speaker_source + ')' : '') : (r.speaker_source ? '(' + r.speaker_source + ')' : '')],
    ['Message', r => [r.title, r.body].filter(Boolean).join(' — '), 'msgcell'],
    ['Quote', r => r.quote_source], ['Status', r => r.status], ['HTTP', r => r.http_status], ['Error', r => r.error_type],
  ];
  const SIGNUP_COLS = [
    ['Date', r => r.local_date || splitTime(r.at)[0]], ['Time', r => r.local_time || splitTime(r.at)[1]], ['Result', r => r.result],
    ['IP', r => r.ip || 'hashed', r => (!r.ip || r.ip === 'hashed') ? 'dim' : ''], ['Charged', r => r.charged === undefined ? '' : (r.charged ? 'yes' : 'no')],
  ];

  function table(rows, cols){
    if (!rows.length) return node('p', 'adm-meta', 'No rows.');
    if (!cols){
      const keys = [];
      for (const r of rows) for (const k of Object.keys(r || {})) if (!keys.includes(k)) keys.push(k);
      cols = keys.map(k => [k, r => r[k]]);
    }
    const t = node('table'), head = node('tr'), tb = node('tbody');
    for (const [h] of cols) head.append(node('th', '', h));
    const th = node('thead'); th.append(head); t.append(th, tb);
    for (const r of rows){
      const tr = node('tr');
      for (const [, get, cls] of cols){
        const c = typeof cls === 'function' ? cls(r) : cls;
        tr.append(node('td', c || '', fmt(get(r || {}))));
      }
      tb.append(tr);
    }
    const wrap = node('div', 'adm-scroll'); wrap.append(t); return wrap;
  }

  function colsFor(key, rows){
    const r = rows[0] || {};
    if (tab === 'log' && 'sent_at' in r) return DELIVERY_COLS;
    if (tab === 'signups' && 'at' in r && 'result' in r && !('count' in r)) return SIGNUP_COLS;
    return null;
  }

  const TITLES = { rows:'', entries:'', items:'', deliveries:'', attempts:'Sign-up attempts', signups:'Sign-up attempts',
    rejections:'Hourly rejections (counts)', actions:'', admin_actions:'' };

  function render(data){
    out.replaceChildren();
    if (Array.isArray(data)){ out.append(table(data, colsFor('', data))); return; }
    if (!data || typeof data !== 'object'){ out.append(node('p', 'adm-meta', fmt(data))); return; }
    for (const [k, v] of Object.entries(data)){
      if (Array.isArray(v)){
        const title = k in TITLES ? TITLES[k] : k.replace(/_/g, ' ');
        if (title) out.append(node('h2', '', title));
        out.append(table(v, colsFor(k, v)));
      } else {
        out.append(node('p', 'adm-meta', k.replace(/_/g, ' ') + ': ' + fmt(v)));
      }
    }
  }

  async function load(){
    say('Loading…');
    try {
      const r = await call(query());
      if (!r) return;
      render(await r.json()); say('');
    } catch { say('Could not reach the server.', true); }
  }

  async function downloadCsv(){
    csvBtn.disabled = true;
    try {
      const r = await call(query({ format: 'csv' }));
      if (!r) return;
      const url = URL.createObjectURL(await r.blob());
      const a = node('a'); a.href = url;
      a.download = 'push-' + tab + '-' + new Date().toISOString().slice(0, 10) + '.csv';
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { say('Download failed.', true); }
    finally { csvBtn.disabled = false; }
  }

  function selectTab(name){
    tab = name;
    for (const b of $('tabs').querySelectorAll('button')) b.classList.toggle('active', b.dataset.tab === name);
    for (const el of filters.querySelectorAll('[data-for]')) el.hidden = !el.dataset.for.split(' ').includes(name);
    out.replaceChildren();
    load();
  }

  // ---- wiring ----
  unlockForm.addEventListener('submit', e => {
    e.preventDefault();
    const v = $('token').value.trim();
    $('token').value = '';
    if (!v) return;
    setToken(v); showUnlocked(true); selectTab(tab);
  });
  forgetBtn.onclick = () => { setToken(''); showUnlocked(false); };
  $('tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) selectTab(b.dataset.tab); });
  filters.addEventListener('submit', e => { e.preventDefault(); load(); });
  csvBtn.onclick = downloadCsv;

  if (getToken()){ showUnlocked(true); selectTab(tab); } else showUnlocked(false);
})();
