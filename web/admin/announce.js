// Admin "Announce update": shows the release that is about to go out, keeps the
// list of tester device numbers, and sends the update push to testers or to
// everyone, after a confirmation. Same token handling as the push log. This
// page never lists devices: it only echoes the tester numbers typed here.

(function(){
  const TOKEN_KEY = 'inneroracle.adminToken';
  const PUSH_TEXT = 'New update: open InnerOracle to see what’s new.';
  const $ = id => document.getElementById(id);
  const unlockForm = $('unlock'), panel = $('panel'), forgetBtn = $('forget'), msg = $('msg');
  let state = null;

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
    if (!on){ say(''); $('token').focus(); }
  }

  // Returns { ok, status, data } or null when the token was rejected / no reply.
  async function call(path, method, body){
    let r;
    try {
      r = await fetch(path, { method: method || 'GET', cache: 'no-store', referrerPolicy: 'no-referrer',
        headers: Object.assign({ 'X-Admin-Token': getToken() }, body ? { 'Content-Type': 'application/json' } : {}),
        body: body ? JSON.stringify(body) : undefined });
    } catch { say('Could not reach the server.', true); return null; }
    if (r.status === 401 || r.status === 403){ setToken(''); showUnlocked(false); say('Token rejected.', true); return null; }
    let data = null;
    try { data = await r.json(); } catch {}
    return { ok: r.ok, status: r.status, data };
  }
  const detail = res => (res.data && typeof res.data.detail === 'string') ? res.data.detail : 'Request failed (HTTP ' + res.status + ').';

  const numbers = list => list.length ? list.map(n => '#' + n).join(', ') : 'none';
  const STAGE = { draft: 'Draft: no device can see it yet.', testers: 'Sent to testers only.', all: 'Announced to all devices.' };

  function render(){
    const box = $('release'), r = state.release;
    box.replaceChildren();
    if (!r){ box.append(node('p', 'adm-meta', 'There are no release notes (app/resources/releases.json).')); $('send').disabled = true; }
    else {
      const card = node('div', 'adm-card');
      card.append(node('h3', '', r.title), node('p', 'adm-meta', 'Version ' + r.version + (r.date ? ' · ' + r.date : '')));
      const ul = node('ul');
      for (const t of r.items || []) ul.append(node('li', '', t));
      card.append(ul);
      card.append(node('p', 'adm-meta', 'Stage: ' + (STAGE[r.stage] || r.stage)
        + (r.testers_sent_at ? ' Testers: ' + r.testers_sent_at + '.' : '')
        + (r.all_sent_at ? ' All: ' + r.all_sent_at + '.' : '')));
      card.append(node('p', 'adm-meta', 'Push text: “' + PUSH_TEXT + '”'));
      box.append(card);
      $('send').disabled = false;
    }
    $('testersNow').textContent = 'Testers: ' + numbers(state.testers || []);
    if (document.activeElement !== $('testers')) $('testers').value = (state.testers || []).join(', ');
  }

  async function load(){
    say('Loading…');
    const res = await call('/push/release');
    if (!res) return;
    if (!res.ok){ say(res.status === 404 ? 'Not found. Is push enabled on this server?' : detail(res), true); return; }
    state = res.data; render(); say('');
  }

  $('testersForm').addEventListener('submit', async e => {
    e.preventDefault();
    const parts = $('testers').value.split(/[\s,#]+/).filter(Boolean);
    if (parts.some(p => !/^\d{1,9}$/.test(p) || p === '0')){ say('Tester devices must be device numbers, e.g. 1, 3.', true); return; }
    const res = await call('/push/testers', 'PUT', { device_ids: parts.map(Number) });
    if (!res) return;
    if (!res.ok){ say(detail(res), true); return; }
    state = res.data; $('testers').blur(); render(); say('Saved. Testers: ' + numbers(state.testers) + '.');
  });

  $('sendForm').addEventListener('submit', async e => {
    e.preventDefault();
    const r = state && state.release;
    if (!r) return;
    const audience = new FormData($('sendForm')).get('audience');
    const who = audience === 'all' ? 'ALL devices' : 'tester devices (' + numbers(state.testers || []) + ')';
    const notes = [r.title + ' (version ' + r.version + ')'].concat((r.items || []).map(t => '• ' + t)).join('\n');
    if (!confirm('Send this to ' + who + '?\n\n' + notes + '\n\nPush: ' + PUSH_TEXT
      + (audience === 'all' ? '\n\nThis can be done only once for this release.' : ''))) return;
    $('send').disabled = true; say('Sending…');
    let res = await call('/push/announce', 'POST', { version: r.version, audience });
    if (res && !res.ok && res.data && res.data.needs_confirm &&
        confirm('This release was already sent to testers. Send it to them again, for re-testing?')){
      res = await call('/push/announce', 'POST', { version: r.version, audience, repeat: true });
    }
    $('send').disabled = false;
    if (!res) return;
    if (!res.ok){ say(detail(res), true); return; }
    const d = res.data;
    await load();
    say('Sent to ' + d.devices + ' device' + (d.devices === 1 ? '' : 's') + ' (' + d.delivered + ' delivered).');
  });

  unlockForm.addEventListener('submit', e => {
    e.preventDefault();
    const v = $('token').value.trim();
    $('token').value = '';
    if (!v) return;
    setToken(v); showUnlocked(true); load();
  });
  forgetBtn.onclick = () => { setToken(''); showUnlocked(false); };

  if (getToken()){ showUnlocked(true); load(); } else showUnlocked(false);
})();
