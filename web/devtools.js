// Local testing only: a small block of test buttons in the sidebar. app.js
// loads this module only when /version says the server runs with LOCAL_DEV=1,
// and the /dev endpoints it calls don't exist anywhere else.

import { resetWhatsNew } from './whatsnew.js';

function node(tag, cls, text){
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

export function initDevTools(){
  const daily = document.getElementById('daily');
  if (!daily || document.getElementById('devTools')) return;
  const box = node('section', 'daily dev-tools'); box.id = 'devTools';
  const out = node('p', 'daily-status', '');
  const call = (label, path)=>{
    const b = node('button', 'daily-btn', label); b.type = 'button';
    b.onclick = async ()=>{
      b.disabled = true; out.textContent = '…';
      try {
        const d = await (await fetch(path, { method:'POST' })).json();
        out.textContent = d.devices === 0 ? 'No device to send to: turn on Daily verse above first.'
          : d.devices && !d.delivered ? 'Sent, but the push service refused it. ' + JSON.stringify(d)
          : JSON.stringify(d);
      }
      catch { out.textContent = 'Request failed.'; }
      b.disabled = false;
    };
    return b;
  };
  const reset = node('button', 'daily-btn', 'Reset what’s new + hints'); reset.type = 'button';
  reset.onclick = ()=>{ resetWhatsNew(); location.reload(); };
  // The pop-up only appears for a release that has been announced. This marks
  // the latest one announced on this local server (no push), then shows it.
  const preview = node('button', 'daily-btn', 'Preview what’s new'); preview.type = 'button';
  preview.onclick = async ()=>{
    preview.disabled = true;
    try {
      const r = await fetch('/dev/release-all', { method:'POST' });
      if (!r.ok) throw 0;
      resetWhatsNew(); location.reload();
    } catch { out.textContent = 'Could not release locally (restart scripts/run_local.sh?).'; preview.disabled = false; }
  };
  box.append(node('div', 'daily-head', 'Local testing'),
    call('Send test push now', '/dev/test-push'),
    call('Pretend today is a festival', '/dev/festival-today'),
    preview, reset, out);
  daily.after(box);
}
