// Admin verse stats: totals per daily verse (sent / opened / starred), sortable
// by open rate and by starred. Same token handling as the push log: kept in
// sessionStorage, sent only as the X-Admin-Token header. Server text is
// rendered with textContent.

(function(){
  const TOKEN_KEY = 'inneroracle.adminToken';
  const $ = id => document.getElementById(id);
  const unlockForm = $('unlock'), panel = $('panel'), forgetBtn = $('forget');
  const filters = $('filters'), out = $('out'), msg = $('msg');
  const BOOKS = { BG: 'Bhagavad Gita', AG: 'Ashtavakra Gita' };
  let rows = [], sort = 'date';

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

  const ref = id => { const s = String(id || ''); const b = BOOKS[s.slice(0, 2)]; return b ? b + ' ' + s.slice(3) : s; };
  const rate = r => r.sent > 0 ? r.opened / r.sent : null;   // worked out here, never stored
  const pct = v => v === null ? '—' : Math.round(v * 100) + '%';

  const SORTS = {
    date:    (a, b) => (b.date + b.time).localeCompare(a.date + a.time),
    rate:    (a, b) => (rate(b) ?? -1) - (rate(a) ?? -1) || b.sent - a.sent,
    starred: (a, b) => b.starred - a.starred || (rate(b) ?? -1) - (rate(a) ?? -1),
  };
  const COLS = [
    ['Date', r => r.date + (r.time ? ' ' + r.time : ''), '', 'date'],
    ['Verse', r => ref(r.verse_id)],
    ['Quote', r => r.quote || '—', 'msgcell'],
    ['Festival', r => r.festival || ''],
    ['Sent', r => r.sent, 'num'], ['Opened', r => r.opened, 'num'],
    ['Open rate', r => pct(rate(r)), 'num', 'rate'],
    ['Starred', r => r.starred, 'num', 'starred'],
  ];

  function render(){
    out.replaceChildren();
    if (!rows.length){ out.append(node('p', 'adm-meta', 'No verses sent in this period.')); return; }
    const cols = COLS.filter(c => c[0] !== 'Festival' || rows.some(r => r.festival));
    const t = node('table'), head = node('tr'), tb = node('tbody');
    for (const [h, , cls, key] of cols){
      const th = node('th', cls === 'num' ? 'num' : '');
      if (key){
        const b = node('button', '', h + (sort === key ? ' ↓' : ''));
        b.type = 'button'; b.title = 'Sort by ' + h.toLowerCase();
        b.onclick = () => { sort = key; render(); };
        th.append(b);
      } else th.textContent = h;
      head.append(th);
    }
    const thead = node('thead'); thead.append(head); t.append(thead, tb);
    for (const r of rows.slice().sort(SORTS[sort])){
      const tr = node('tr');
      for (const [, get, cls] of cols) tr.append(node('td', cls || '', get(r)));
      tb.append(tr);
    }
    const wrap = node('div', 'adm-scroll'); wrap.append(t); out.append(wrap);
  }

  async function load(){
    say('Loading…');
    const days = Math.min(3650, Math.max(1, parseInt(filters.elements.days.value, 10) || 90));
    try {
      const r = await fetch('/push/stats?days=' + days, { headers: { 'X-Admin-Token': getToken() }, cache: 'no-store', referrerPolicy: 'no-referrer' });
      if (r.status === 401 || r.status === 403){ setToken(''); showUnlocked(false); say('Token rejected.', true); return; }
      if (r.status === 404){ say('Not found. Is push enabled on this server?', true); return; }
      if (!r.ok){ say('Request failed (HTTP ' + r.status + ').', true); return; }
      const d = await r.json();
      rows = Array.isArray(d.stats) ? d.stats.filter(x => x && typeof x === 'object') : [];
      render(); say('');
    } catch { say('Could not reach the server.', true); }
  }

  unlockForm.addEventListener('submit', e => {
    e.preventDefault();
    const v = $('token').value.trim();
    $('token').value = '';
    if (!v) return;
    setToken(v); showUnlocked(true); load();
  });
  forgetBtn.onclick = () => { setToken(''); showUnlocked(false); };
  filters.addEventListener('submit', e => { e.preventDefault(); load(); });

  if (getToken()){ showUnlocked(true); load(); } else showUnlocked(false);
})();
