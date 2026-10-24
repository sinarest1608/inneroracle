// "🔔 Daily verse" in the sidebar: service-worker registration, the opt-in /
// turn-off flows, and the quiet on-open re-registration check. Device record
// lives in IndexedDB via self.pushStore (pushstore.js, shared with sw.js).

const ps = self.pushStore;
const body = document.getElementById('dailyBody');
const note = document.getElementById('dailyNote');
const MSG_FAIL = "Couldn't turn on notifications. Try again later.";
const MSG_429 = 'Too many sign-ups right now. Try again in an hour.';

let vapidKey = null;   // fetched up front so the tap handler can prompt immediately

const today = ()=> new Date().toLocaleDateString('en-CA');   // YYYY-MM-DD, local
const isIOS = ()=> /iPad|iPhone|iPod/.test(navigator.userAgent) ||
  (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
const standalone = ()=> matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
const supported = ()=> window.isSecureContext && !!ps && 'serviceWorker' in navigator &&
  'PushManager' in window && 'Notification' in window && 'indexedDB' in window;

function post(path, data, headers){
  return fetch(path, { method:'POST',
    headers: Object.assign({ 'Content-Type':'application/json' }, headers || {}),
    body: data === undefined ? undefined : JSON.stringify(data) });
}
const record = (r, b)=> ({ device_id: r.device_id, device_secret: r.device_secret,
  endpoint: b.endpoint, p256dh: b.keys.p256dh, auth: b.keys.auth, last_check_date: today() });

// ---- rendering (DOM APIs only) ----
function node(tag, cls, text){
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function render(kind, opts = {}){
  body.replaceChildren();
  note.hidden = kind === 'unavailable' || kind === 'install';
  if (kind === 'unavailable'){
    body.append(node('p', 'daily-status', opts.text || 'Not available in this browser or a private window.'));
  } else if (kind === 'checking'){
    body.append(node('p', 'daily-status', 'Checking…'));
  } else if (kind === 'install'){
    body.append(node('p', 'daily-status',
      'Add to Home Screen first: Share → Add to Home Screen, then open from the icon.'));
  } else if (kind === 'enabled'){
    const row = node('div', 'daily-row');
    const b = node('button', 'daily-btn', 'Turn off'); b.onclick = turnOff;
    row.append(node('span', 'daily-status on', 'On · Device #' + opts.device), b);
    body.append(row);
  } else {   // 'off'
    const row = node('div', 'daily-row');
    const b = node('button', 'daily-btn primary', 'Enable'); b.onclick = enable;
    row.append(node('span', 'daily-status', 'A verse each morning and evening.'), b);
    body.append(row);
  }
  if (opts.error) body.append(node('p', 'daily-err', opts.error));
}
function busy(text){
  const b = body.querySelector('button'); if (b){ b.disabled = true; b.textContent = text; }
}

async function swReg(){ return navigator.serviceWorker.ready; }

// ---- enable (runs inside the tap handler) ----
async function enable(){
  if (!vapidKey){ render('off', { error: MSG_FAIL }); return; }
  busy('Enabling…');
  const perm = await Notification.requestPermission();
  if (perm !== 'granted'){
    render('off', { error: perm === 'denied'
      ? 'Notifications are blocked for this site. Allow them in settings to turn this on.'
      : MSG_FAIL });
    return;
  }
  let sub;
  try {
    const reg = await swReg();
    sub = await reg.pushManager.subscribe({ userVisibleOnly:true, applicationServerKey: ps.keyBytes(vapidKey) });
  } catch { render('off', { error: MSG_FAIL }); return; }
  const b = ps.subBody(sub);
  let r;
  try { r = await post('/push/subscribe', b); } catch { r = null; }
  if (r && r.ok){
    try {
      const d = record(await r.json(), b);
      await ps.put(d);
      render('enabled', { device: d.device_id });
    } catch { await sub.unsubscribe().catch(()=>{}); render('off', { error: MSG_FAIL }); }
    return;
  }
  await sub.unsubscribe().catch(()=>{});
  render('off', { error: r && r.status === 429 ? MSG_429 : MSG_FAIL });
}

// ---- turn off ----
async function turnOff(){
  busy('Turning off…');
  const saved = await ps.get().catch(()=>null);
  try { const sub = await (await swReg()).pushManager.getSubscription(); if (sub) await sub.unsubscribe(); } catch {}
  if (saved){ try { await post('/push/unsubscribe', undefined, ps.deviceHeaders(saved)); } catch {} }
  await ps.clear().catch(()=>{});
  render('off');
}

// ---- on-open re-registration check (permission already granted; never prompts) ----
// Row 3 of the table: the browser has a subscription we have no valid record
// for. The server rotates the secret for a known endpoint+keys, or treats it as
// a new sign-up (with a confirmation push) if it lost the device.
async function reregister(sub){
  await ps.clear();
  const b = ps.subBody(sub);
  let r;
  try { r = await post('/push/subscribe', b); } catch { return { kind:'off' }; }  // offline: retry next open
  if (r.ok){ const d = record(await r.json(), b); await ps.put(d); return { kind:'enabled', device: d.device_id }; }
  if (r.status === 429 || r.status >= 500) return { kind:'off' };              // transient: retry next open
  await sub.unsubscribe().catch(()=>{});                                       // 409/422: give up cleanly
  return { kind:'off' };
}

async function check(){
  const reg = await swReg();
  const sub = await reg.pushManager.getSubscription();
  const saved = await ps.get();
  if (sub && saved && saved.endpoint === sub.endpoint){
    if (saved.last_check_date === today()) return { kind:'enabled', device: saved.device_id };
    const r = await fetch('/push/device/check', { headers: ps.deviceHeaders(saved) });
    if (r.status === 204){ await ps.put(Object.assign({}, saved, { last_check_date: today() })); return { kind:'enabled', device: saved.device_id }; }
    if (r.status === 401 || r.status === 404) return reregister(sub);
    return { kind:'enabled', device: saved.device_id };                       // 5xx etc.: try tomorrow
  }
  if (sub && saved){
    const b = ps.subBody(sub);
    const r = await post('/push/device/update', b, ps.deviceHeaders(saved));
    if (r.ok){ await ps.put(Object.assign({}, saved, { endpoint: b.endpoint, p256dh: b.keys.p256dh, auth: b.keys.auth, last_check_date: today() })); return { kind:'enabled', device: saved.device_id }; }
    if (r.status === 409){ await sub.unsubscribe().catch(()=>{}); await ps.clear(); return { kind:'off' }; }
    if (r.status === 401 || r.status === 404) return reregister(sub);
    return { kind:'enabled', device: saved.device_id };
  }
  if (sub) return reregister(sub);
  if (saved){
    try { await post('/push/unsubscribe', undefined, ps.deviceHeaders(saved)); } catch {}
    await ps.clear();
  }
  return { kind:'off' };
}

export async function initPush(){
  // Opening the app counts as seeing the verse: clear the app-icon badge.
  if ('clearAppBadge' in navigator) navigator.clearAppBadge().catch(()=>{});
  if (!body) return;
  if (isIOS() && !standalone()){ render('install'); return; }
  if (!supported()){ render('unavailable'); return; }
  try { await navigator.serviceWorker.register('/ui/sw.js', { scope:'/ui/' }); }
  catch { render('unavailable'); return; }
  try {
    const r = await fetch('/push/vapid-public-key');
    if (r.ok) vapidKey = (await r.json()).key;
  } catch {}
  if (!vapidKey){ render('unavailable', { text: 'Daily verse isn’t available right now.' }); return; }
  if (Notification.permission !== 'granted'){
    render('off', Notification.permission === 'denied'
      ? { error: 'Notifications are blocked for this site in your browser settings.' } : {});
    return;
  }
  render('checking');
  try { const s = await check(); render(s.kind, s); }
  catch { const d = await ps.get().catch(()=>null); render(d ? 'enabled' : 'off', { device: d && d.device_id }); }
}
