// InnerOracle service worker: shows the daily-verse push and opens taps under
// /ui/ only. No fetch handler and no caching, by design. A daily verse carries
// its inbox entry id and the device's unread count: the count goes on the app
// icon, and a tap marks that entry opened.

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

// A new worker takes over at once, so a deploy never waits for every tab and
// home-screen window to close first.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) {}
  const text = v => (typeof v === 'string' ? v.slice(0, 200) : '');
  const entry = Number.isInteger(d.inbox_id) && d.inbox_id > 0 ? d.inbox_id : null;
  e.waitUntil(Promise.all([
    self.registration.showNotification(text(d.title) || 'InnerOracle', {
      body: text(d.body) || 'A verse is waiting for you.',
      icon: '/ui/icons/icon-192.png',
      badge: '/ui/icons/badge-96.png',   // monochrome: Android status bar
      // Same tag replaces the earlier notification; an update announcement has
      // its own, so it never pushes an unread verse out of the tray.
      tag: d.tag === 'update' ? 'update' : 'daily-verse',
      data: { url: safeUrl(d.url), entry }
    }),
    setBadge(d.unread)   // sign-up and test pushes carry no count: badge untouched
  ]));
});

// App-icon badge (Dock / Home Screen) = unread verses, for installed apps where
// supported; a no-op everywhere else and for anything that isn't a count.
function setBadge(count) {
  const n = self.navigator;
  if (!n || !('setAppBadge' in n) || !Number.isInteger(count) || count < 0) return Promise.resolve();
  return (count ? n.setAppBadge(count) : n.clearAppBadge()).catch(() => {});
}

// Mark the tapped entry opened right away, so it leaves Unread even if the page
// is slow to load. The page does the same when it shows the verse.
async function markOpened(entry) {
  const saved = await self.pushStore.get();
  if (!saved) return;
  const r = await fetch('/push/inbox/' + entry + '/open',
    { method: 'POST', headers: self.pushStore.deviceHeaders(saved) });
  if (r.ok) await setBadge((await r.json()).unread);
}

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const data = e.notification.data || {};
  let url = safeUrl(data.url);
  const jobs = [];
  if (Number.isInteger(data.entry) && data.entry > 0) {
    const u = new URL(url);
    u.searchParams.set('entry', data.entry);
    url = u.href;
    jobs.push(markOpened(data.entry).catch(() => {}));
  }
  jobs.push(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) { if ('focus' in c) { c.navigate(url); return c.focus(); } }
    return clients.openWindow(url);
  }));
  e.waitUntil(Promise.all(jobs));
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
