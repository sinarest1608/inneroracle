// "What's new": a themed pop-up shown once per device per release, and one-time
// hints that point at the new parts of the app. Which release this device may
// see is the server's call (GET /releases/latest: nothing until the owner has
// announced it to this device), so a deploy by itself shows nothing. What has
// been seen is kept on the device, in localStorage.

import { sidebar } from './dom.js';
import { openSidebar } from './views.js';
import { VERSION } from './update.js';

const KEY = 'inneroracle.whatsnew.v1';
const ps = self.pushStore;
const item = document.getElementById('whatsNew');

let release = null;     // the newest release this device may see
let tour = null;        // the hint walk in progress

function node(tag, cls, text){
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

// ---- what this device has seen ----
function load(){
  try {
    const s = JSON.parse(localStorage.getItem(KEY));
    if (s && typeof s === 'object') return { seen: typeof s.seen === 'string' ? s.seen : '', hints: Array.isArray(s.hints) ? s.hints.filter(h => typeof h === 'string') : [] };
  } catch {}
  return null;
}
function save(s){ try { localStorage.setItem(KEY, JSON.stringify(s)); } catch {} }

// A device that has never run the app has nothing "new" to be told about.
async function usedBefore(){
  try { for (let i = 0; i < localStorage.length; i++) if (localStorage.key(i).startsWith('inneroracle.')) return true; } catch {}
  try { return !!(ps && await ps.get()); } catch { return false; }
}

async function fetchRelease(){
  let headers = {};
  try { const saved = ps && await ps.get(); if (saved) headers = ps.deviceHeaders(saved); } catch {}
  try {
    const r = await fetch('/releases/latest', { headers, cache:'no-store' });
    if (!r.ok) return null;
    const rel = (await r.json()).release;
    if (!rel || typeof rel.version !== 'string' || typeof rel.title !== 'string') return null;
    rel.items = Array.isArray(rel.items) ? rel.items.filter(t => typeof t === 'string') : [];
    rel.hints = Array.isArray(rel.hints) ? rel.hints.filter(h => h && /^[a-z0-9-]+$/.test(h.id) && /^[a-z0-9-]+$/.test(h.target) && typeof h.text === 'string') : [];
    return rel;
  } catch { return null; }
}

// ---- the pop-up ----
function closePopup(){
  const p = document.querySelector('.wn-veil');
  if (p) p.remove();
}

function showPopup(){
  closePopup();
  const veil = node('div', 'wn-veil');
  const box = node('section', 'wn');
  box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-labelledby', 'wnTitle');
  const mark = node('div', 'wn-mark');
  mark.append(node('span', 'danda', '॥'), document.createTextNode(' What’s new '), node('span', 'danda', '॥'));
  const title = node('h2', 'wn-title', release.title); title.id = 'wnTitle';
  const list = node('ul', 'wn-list');
  for (const t of release.items) list.append(node('li', '', t));
  const row = node('div', 'wn-actions');
  const done = ()=>{ const s = load() || { seen:'', hints:[] }; s.seen = release.version; save(s); closePopup(); };
  if (release.hints.length){
    const show = node('button', 'wn-btn primary', 'Show me'); show.type = 'button';
    show.onclick = ()=>{ done(); startTour({ fromPopup:true }); };
    row.append(show);
  }
  const ok = node('button', 'wn-btn', 'Got it'); ok.type = 'button'; ok.onclick = done;
  row.append(ok);
  box.append(mark, title, list, row);
  veil.append(box);
  veil.addEventListener('keydown', e => { if (e.key === 'Escape') done(); });
  document.body.append(veil);
  ok.focus();
}

// ---- hints ----
const target = h => document.querySelector(`[data-hint="${h.target}"]`);

// Really on screen: laid out, inside the viewport, and not under a closed drawer.
function onScreen(el){
  if (!el || !el.getClientRects().length) return false;
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return false;
  return r.right > 8 && r.bottom > 8 && r.left < innerWidth - 8 && r.top < innerHeight - 8;
}

function endTour(){
  if (!tour) return;
  if (tour.el) tour.el.classList.remove('hint-target');
  if (tour.box) tour.box.remove();
  removeEventListener('resize', place);
  tour = null;
}

function place(){
  if (!tour || !tour.el || !tour.box) return;
  if (!onScreen(tour.el)){ next(); return; }
  const r = tour.el.getBoundingClientRect(), b = tour.box;
  const w = b.offsetWidth, h = b.offsetHeight, gap = 10;
  const below = r.bottom + gap + h <= innerHeight - 8;
  const top = below ? r.bottom + gap : Math.max(8, r.top - gap - h);
  const left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8);
  b.style.top = top + 'px'; b.style.left = left + 'px';
  b.classList.toggle('above', !below);
  b.style.setProperty('--tip-x', Math.min(Math.max(14, r.left + r.width / 2 - left), w - 14) + 'px');
}

function next(){
  if (!tour) return;
  if (tour.el) tour.el.classList.remove('hint-target');
  if (tour.box) tour.box.remove();
  tour.el = tour.box = null;
  // The next hint whose target is on screen; the rest are skipped, not shown
  // pointing at nothing (and stay unseen, so they can appear another time).
  let h;
  while ((h = tour.queue.shift())) if (onScreen(target(h))) break;
  if (!h){ endTour(); return; }

  const s = load() || { seen: release.version, hints: [] };
  if (!h.transient && !s.hints.includes(h.id)){ s.hints.push(h.id); save(s); }

  const el = target(h), more = tour.queue.some(q => onScreen(target(q)));
  const box = node('div', 'hint');
  box.setAttribute('role', 'dialog'); box.setAttribute('aria-live', 'polite');
  box.append(node('p', 'hint-text', h.text));
  const row = node('div', 'hint-actions');
  const go = node('button', 'hint-next', more ? 'Next' : 'Done'); go.type = 'button';
  row.append(go);
  if (more){
    const skip = node('button', 'hint-skip', 'Skip'); skip.type = 'button';
    skip.onclick = e => { e.stopPropagation(); endTour(); };
    row.append(skip);
  }
  box.append(row);
  box.onclick = e => { e.stopPropagation(); next(); };     // tap anywhere on it = next
  document.body.append(box);
  el.classList.add('hint-target');
  tour.el = el; tour.box = box;
  place();
  go.focus({ preventScroll:true });
}

// replay: every hint again (from the sidebar item); otherwise only unseen ones.
function startTour({ replay = false, fromPopup = false } = {}){
  if (!release || tour) return;
  const seen = (load() || { hints: [] }).hints;
  let queue = release.hints.filter(h => replay || !seen.includes(h.id));
  if (!queue.length) return;
  // The lists only exist once Daily verse is on. Asked to be shown around
  // without it, point at the switch instead of doing nothing.
  const nav = document.getElementById('inboxNav');
  if ((fromPopup || replay) && nav && nav.hidden && document.querySelector('[data-hint="daily-verse"]')){
    queue = [{ id: 'daily-verse-off', target: 'daily-verse', transient: true,
               text: 'Turn on Daily verse first: Unread, History and Favourites appear here once it’s on.' }];
    if (fromPopup) openSidebar();
  }
  // From the pop-up the drawer is closed: open it if a hint lives in there and
  // could be shown (its target exists and isn't hidden, i.e. notifications are on).
  if (fromPopup && queue.some(h => { const el = target(h); return el && sidebar.contains(el) && !el.closest('[hidden]'); })) openSidebar();
  tour = { queue, el: null, box: null };
  addEventListener('resize', place);
  // Let the drawer finish sliding in before measuring where things are.
  setTimeout(next, fromPopup ? 340 : 0);
}

// A hint whose target wasn't around earlier (the star lives in the lists) gets
// its turn when that part of the app first appears.
function pendingHints(){
  const s = load();
  if (!release || tour || !s || s.seen !== release.version || document.querySelector('.wn-veil')) return;
  startTour();
}

// Back to "has seen nothing" (not to "brand-new install", which is shown nothing).
export function resetWhatsNew(){ save({ seen: '', hints: [] }); }

export async function initWhatsNew(){
  const known = load();
  const used = known ? true : await usedBefore();
  release = await fetchRelease();
  if (!known && !used){
    // Brand-new install: nothing here is "new" to them. Mark the current
    // version seen, including one that hasn't been announced yet.
    save({ seen: VERSION || (release ? release.version : ''), hints: release ? release.hints.map(h => h.id) : [] });
  } else if (!known){
    save({ seen: '', hints: [] });
  }
  if (!release) return;
  if (item){
    item.hidden = false;
    item.onclick = ()=> (release.hints.length ? startTour({ replay:true }) : showPopup());
  }
  const s = load();
  if (s && s.seen !== release.version) showPopup();
  document.addEventListener('inneroracle:view', ()=> setTimeout(pendingHints, 350));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') endTour(); });
}

export const whatsNewOpen = ()=> !!(tour || document.querySelector('.wn-veil'));
