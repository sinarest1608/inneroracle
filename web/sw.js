// InnerOracle service worker: shows the daily-verse push and opens taps under
// /ui/ only. No fetch handler and no caching, by design.

if (typeof importScripts === 'function') importScripts('/ui/pushstore.js');

// `origin` is a parameter only so the regression page can pin it; the worker
// always uses its own origin.
function safeUrl(raw, origin = self.location.origin) {
  try {
    const u = new URL(raw || '/ui/', origin);
    if (u.origin === origin && u.pathname.startsWith('/ui/')) return u.href;
  } catch (_) {}
  return new URL('/ui/', origin).href;
}

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) {}
  const text = v => (typeof v === 'string' ? v.slice(0, 200) : '');
  e.waitUntil(Promise.all([
    self.registration.showNotification(text(d.title) || 'InnerOracle', {
      body: text(d.body) || 'A verse is waiting for you.',
      icon: '/ui/icons/icon-192.png',
      badge: '/ui/icons/badge-96.png',   // monochrome: Android status bar
      tag: 'daily-verse',
      data: { url: safeUrl(d.url) }
    }),
    setBadge(true)
  ]));
});

// App-icon badge (Dock / Home Screen) for installed apps where supported;
// a no-op everywhere else.
function setBadge(on) {
  const n = self.navigator;
  if (!n || !('setAppBadge' in n)) return Promise.resolve();
  return (on ? n.setAppBadge(1) : n.clearAppBadge()).catch(() => {});
}

self.addEventListener('notificationclick', e => {
  e.notification.close();
  setBadge(false);
  const url = safeUrl(e.notification.data && e.notification.data.url);
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) { if ('focus' in c) { c.navigate(url); return c.focus(); } }
    return clients.openWindow(url);
  }));
});

// Where supported: read device headers from IndexedDB, re-subscribe, then
// POST /push/device/update. Safari rarely fires this; the on-open check is the safety net.
self.addEventListener('pushsubscriptionchange', e => {
  e.waitUntil((async () => {
    const ps = self.pushStore, saved = await ps.get();
    if (!saved) return;
    let sub = e.newSubscription;
    if (!sub) {
      let key = e.oldSubscription && e.oldSubscription.options &&
                e.oldSubscription.options.applicationServerKey;
      if (!key) {
        const r = await fetch('/push/vapid-public-key');
        if (!r.ok) return;
        key = ps.keyBytes((await r.json()).key);
      }
      sub = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    }
    const body = ps.subBody(sub);
    const r = await fetch('/push/device/update', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, ps.deviceHeaders(saved)),
      body: JSON.stringify(body)
    });
    if (r.ok) await ps.put(Object.assign({}, saved, { endpoint: body.endpoint, p256dh: body.keys.p256dh, auth: body.keys.auth }));
    // 409/401/404: leave it; the on-open check reconciles with the user present.
  })().catch(() => {}));
});
