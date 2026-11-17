// Unread / History / Favourites: the daily verses this device was sent.
// The server keeps the list per device (GET /push/inbox); this module shows it
// from the sidebar, keeps the unread count and the app-icon badge in step, and
// lends the verse card its star. All server text goes in via textContent.

import { stream, chat, input } from './dom.js';
import { activeConv } from './store.js';
import { newChat, closeSidebar } from './views.js';
import { VERSE_ID, refText } from './verseref.js';

const ps = self.pushStore;
const nav = document.getElementById('inboxNav');
const countEl = document.getElementById('unreadCount');

const VIEWS = {
  unread:  { title: 'Unread',     empty: 'Nothing unread.',
             keep: e => !e.opened },
  all:     { title: 'History',    empty: 'No verses yet. Each one sent to this device will be listed here.',
             keep: () => true },
  starred: { title: 'Favourites', empty: 'No favourites yet. Star a verse to keep it here.',
             keep: e => e.starred },
};

let entries = [];
let openVerse = ()=>{};
let queue = Promise.resolve();   // one request at a time, so unread counts apply in order

function node(tag, cls, text){
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function setBadge(n){
  try {
    if (n > 0 && 'setAppBadge' in navigator) navigator.setAppBadge(n).catch(()=>{});
    else if ('clearAppBadge' in navigator) navigator.clearAppBadge().catch(()=>{});
  } catch {}
}

function setUnread(n){
  if (!Number.isInteger(n) || n < 0) return;
  if (countEl) countEl.textContent = n ? ` (${n})` : '';
  setBadge(n);
}

// Device-authenticated call; null when this device isn't signed up or it fails.
function call(path, method = 'GET'){
  const run = async ()=>{
    try {
      const saved = ps && await ps.get();
      if (!saved) return null;
      const r = await fetch(path, { method, headers: ps.deviceHeaders(saved) });
      if (!r.ok) return null;
      const d = await r.json();
      setUnread(d.unread);
      return d;
    } catch { return null; }
  };
  return (queue = queue.then(run, run));
}

function valid(e){
  return e && Number.isInteger(e.id) && typeof e.verse_id === 'string' && VERSE_ID.test(e.verse_id);
}

export async function loadInbox(){
  const d = await call('/push/inbox');
  if (d && Array.isArray(d.entries)) entries = d.entries.filter(valid);
  return entries;
}

// The entry a notification or list row pointed at; failing that, the latest
// send of this verse.
export function findEntry(entryId, verseId){
  return entries.find(e => e.id === entryId && e.verse_id === verseId) ||
         entries.find(e => e.verse_id === verseId) || null;
}

export function markOpened(entry){
  if (!entry || entry.opened) return Promise.resolve();
  entry.opened = true;
  return call(`/push/inbox/${entry.id}/open`, 'POST');
}

export function starButton(entry, onChange){
  const b = node('button', 'star');
  b.type = 'button';
  b.dataset.hint = 'favourites-star';
  const paint = ()=>{
    b.textContent = entry.starred ? '★' : '☆';
    b.setAttribute('aria-pressed', String(!!entry.starred));
    b.setAttribute('aria-label', entry.starred ? 'Remove from favourites' : 'Add to favourites');
    b.title = entry.starred ? 'Remove from favourites' : 'Add to favourites';
  };
  paint();
  b.onclick = async ev=>{
    ev.stopPropagation();
    b.disabled = true;
    const d = await call(`/push/inbox/${entry.id}/star`, entry.starred ? 'DELETE' : 'POST');
    if (d) entry.starred = !!d.starred;
    b.disabled = false;
    paint();
    if (d && onChange) onChange();
  };
  return b;
}

// ---- the lists (rendered at the top of the main column, like the verse card) ----

function when(e){
  const t = String(e.time || '').slice(0, 5);
  const d = new Date(`${e.date}T${/^\d\d:\d\d$/.test(t) ? t : '12:00'}`);
  if (isNaN(d)) return e.date;
  const day = d.toLocaleDateString(undefined, { weekday:'short', day:'numeric', month:'short' });
  const h = d.getHours();
  return `${day} · ${h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening'}`;
}

function panel(view){
  const v = VIEWS[view], list = entries.filter(v.keep);
  const p = node('section', 'inbox');
  p.dataset.view = view;
  const head = node('div', 'inbox-head');
  const title = node('h2', 'inbox-title');
  title.append(node('span', 'danda', '॥'), document.createTextNode(` ${v.title} `), node('span', 'danda', '॥'));
  head.append(title);
  if (view === 'unread' && list.length){
    const all = node('button', 'inbox-all', 'Mark all as read');
    all.type = 'button';
    all.onclick = async ()=>{
      all.disabled = true;
      if (await call('/push/inbox/open-all', 'POST')) entries.forEach(e => { e.opened = true; });
      redraw();
    };
    head.append(all);
  }
  p.append(head);
  if (!list.length){ p.append(node('p', 'inbox-empty', v.empty)); return p; }

  const ul = node('ul', 'inbox-list');
  for (const e of list){
    const li = node('li', 'inbox-item' + (e.opened ? '' : ' unread'));
    const open = node('button', 'inbox-open');
    open.type = 'button';
    const meta = node('span', 'inbox-meta');
    meta.append(node('span', 'inbox-ref', refText(e.verse_id)), document.createTextNode(' · ' + when(e)));
    if (typeof e.festival === 'string' && e.festival) meta.append(node('span', 'inbox-fest', ' · ' + e.festival));
    if (!e.opened && view !== 'unread') meta.append(node('span', 'inbox-new', ' · unread'));
    open.append(meta, node('span', 'inbox-quote', typeof e.quote === 'string' ? e.quote : ''));
    open.onclick = ()=> openVerse(e.verse_id, e.id);
    li.append(open, starButton(e, ()=>{ if (view === 'starred') redraw(); }));
    ul.append(li);
  }
  p.append(ul);
  return p;
}

function redraw(){
  const cur = stream.querySelector('.inbox');
  if (cur) cur.replaceWith(panel(cur.dataset.view));
}

async function showList(view){
  closeSidebar();
  await loadInbox();
  if (activeConv().messages.length) newChat();          // start clean, as the verse card does
  input.blur();                                          // don't raise the keyboard over a list
  stream.querySelectorAll('.empty, .verse-card, .inbox').forEach(n => n.remove());
  stream.prepend(panel(view));
  chat.scrollTop = 0;
  document.dispatchEvent(new Event('inneroracle:view'));   // lets a waiting hint find its target
}

// Called by push.js whenever the daily-verse state settles: the lists only
// exist for a device that is signed up.
export function setInboxEnabled(on){
  if (nav) nav.hidden = !on;
  if (on) loadInbox();
  else { entries = []; if (countEl) countEl.textContent = ''; setBadge(0); }
}

export function initInbox(opts){
  openVerse = opts.openVerse;
  if (nav) nav.addEventListener('click', e=>{
    const b = e.target.closest('button[data-view]');
    if (b && VIEWS[b.dataset.view]) showList(b.dataset.view);
  });
}
