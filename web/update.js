// Updates reach an open app by themselves. The page knows which build it was
// served from (stamped into index.html); whenever the app comes back to the
// foreground it asks /version, and if a newer build is deployed it reloads
// quietly, unless that would lose something the person is in the middle of.

const meta = name => { const m = document.querySelector(`meta[name="${name}"]`); return m ? m.content : ''; };
export const BUILD = meta('app-build');
export const VERSION = meta('app-version');

const MIN_GAP = 60 * 1000;         // don't ask more than once a minute
const RELOADED = 'inneroracle.reloadedFor';
let last = 0, busy = ()=> false;
let info = null;                   // the last /version reply

export async function serverVersion(){
  if (info) return info;
  try { const r = await fetch('/version', { cache:'no-store' }); if (r.ok) info = await r.json(); } catch {}
  return info;
}

async function check(){
  if (document.visibilityState !== 'visible' || !BUILD || Date.now() - last < MIN_GAP) return;
  last = Date.now();
  info = null;
  const v = await serverVersion();
  if (!v || typeof v.build !== 'string' || v.build === BUILD || busy()) return;
  // One reload per build: if it somehow doesn't take, don't loop.
  try { if (sessionStorage.getItem(RELOADED) === v.build) return; sessionStorage.setItem(RELOADED, v.build); } catch {}
  location.reload();
}

export function initUpdateCheck(isBusy){
  busy = isBusy || busy;
  const el = document.getElementById('appVersion');
  if (el && VERSION) el.textContent = ' · v' + VERSION;
  document.addEventListener('visibilitychange', check);
  window.addEventListener('pageshow', e => { if (e.persisted) check(); });
}
