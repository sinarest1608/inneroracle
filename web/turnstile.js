// Invisible Cloudflare Turnstile human-check. Fetches the public site key, runs
// the widget in the background, and exchanges the token for a signed `io_human`
// cookie via /verify-human. If the server has no Turnstile configured (dev), this
// is a no-op. Real users almost never see a challenge (interaction-only).

let ready = null;      // Promise<bool> — resolves once a verification attempt settles
let resolvePending = null;

function settle(ok) { if (resolvePending) { resolvePending(ok); resolvePending = null; } }

function waitForApi() {
  return new Promise((res) => {
    if (window.turnstile) return res();
    const iv = setInterval(() => { if (window.turnstile) { clearInterval(iv); res(); } }, 100);
    setTimeout(() => { clearInterval(iv); res(); }, 8000);
  });
}

async function postToken(token) {
  try {
    const r = await fetch('/verify-human', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    return r.ok;
  } catch { return false; }
}

async function init() {
  let key = '';
  try { key = (await (await fetch('/pubconfig')).json()).turnstile_site_key || ''; } catch {}
  if (!key) return true;                       // gate disabled server-side
  await waitForApi();
  if (!window.turnstile) return false;
  return new Promise((resolve) => {
    resolvePending = resolve;
    window.turnstile.render('#turnstile', {
      sitekey: key,
      appearance: 'interaction-only',
      callback: async (t) => settle(await postToken(t)),
      'error-callback': () => settle(false),
      'expired-callback': () => settle(false),
    });
  });
}

// Ensure the human cookie is set (idempotent; safe to call on boot).
export function ensureHuman() { if (!ready) ready = init(); return ready; }

// Force a fresh check — used after a /chat 401 (cookie expired).
export function reverify() {
  if (!window.turnstile) { ready = null; return ensureHuman(); }
  return new Promise((resolve) => {
    resolvePending = resolve;
    try { window.turnstile.reset(); } catch { resolve(false); }
  });
}
