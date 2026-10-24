// Device record for the daily-verse push, kept in IndexedDB so both the page
// and the service worker (via importScripts) can read it. Classic script:
// exposes self.pushStore. One record: { device_id, device_secret, endpoint,
// p256dh, auth, last_check_date }.

(function(){
  const DB = 'inneroracle-push', OS = 'device', KEY = 'current';

  function open(){
    return new Promise((res, rej)=>{
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = ()=> r.result.createObjectStore(OS);
      r.onsuccess = ()=> res(r.result);
      r.onerror = ()=> rej(r.error);
    });
  }
  async function tx(mode, fn){
    const db = await open();
    try {
      return await new Promise((res, rej)=>{
        const t = db.transaction(OS, mode), req = fn(t.objectStore(OS));
        t.oncomplete = ()=> res(req && req.result);
        t.onerror = t.onabort = ()=> rej(t.error);
      });
    } finally { db.close(); }
  }

  // Base64url VAPID key -> bytes for pushManager.subscribe().
  function keyBytes(b64){
    const s = (b64 + '='.repeat((4 - b64.length % 4) % 4)).replace(/-/g,'+').replace(/_/g,'/');
    return Uint8Array.from(atob(s), c=>c.charCodeAt(0));
  }

  // Subscription -> the exact body the API takes (and the fields we store).
  function subBody(sub){
    const j = sub.toJSON();
    return { endpoint: j.endpoint, keys: { p256dh: j.keys.p256dh, auth: j.keys.auth } };
  }

  function deviceHeaders(d){
    return { 'X-Device-Id': String(d.device_id), 'X-Device-Secret': d.device_secret };
  }

  self.pushStore = {
    get: ()=> tx('readonly', s=>s.get(KEY)).then(v=>v || null),
    put: v=> tx('readwrite', s=>s.put(v, KEY)),
    clear: ()=> tx('readwrite', s=>s.delete(KEY)),
    keyBytes, subBody, deviceHeaders,
  };
})();
