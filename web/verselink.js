// Deep link /ui/?verse=<id>[&entry=<inbox id>] (the notification tap target):
// show that verse as a card above the chat, with today's reflection when it is
// today's verse, and an "Ask about this verse" button. If the verse is in this
// device's inbox, opening the card marks it read and the card carries its star.
// All server text goes in via textContent.

import { stream, input, scrollDown } from './dom.js';
import { activeConv } from './store.js';
import { newChat } from './views.js';
import { pinVerse } from './chat.js';
import { VERSE_ID, BOOKS } from './verseref.js';
import { loadInbox, findEntry, markOpened, starButton } from './inbox.js';

function node(tag, cls, text){
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text) n.textContent = text;
  return n;
}
const str = v => (typeof v === 'string' ? v.trim() : '');

async function getJson(url){
  try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch { return null; }
}

function card(id, v, today, entry){
  const [bookKey, bookName] = BOOKS[id.slice(0, 2)];
  const c = node('article', 'verse-card');

  if (today){
    const t = node('div', 'vc-today');
    t.append(node('div', 'vc-label', 'Today’s reflection (paraphrase)'));
    if (str(today.title)) t.append(node('div', 'vc-title', str(today.title)));
    t.append(node('p', 'vc-quote', str(today.quote)));
    c.append(t);
  }

  const ref = node('div', 'vc-ref');
  ref.append(node('span', 'danda', '॥'), document.createTextNode(` ${bookName} ${id.slice(3)} `), node('span', 'danda', '॥'));
  if (entry) ref.append(starButton(entry));
  c.append(ref);

  if (!v){
    c.append(node('p', 'vc-missing', 'Verse not found.'));
    return c;
  }
  const s = node('div', 'shloka');
  if (str(v.sanskrit)) s.append(node('div', 'dev', str(v.sanskrit)));
  if (str(v.transliteration)) s.append(node('div', 'tl', str(v.transliteration)));
  if (str(v.translation)) s.append(node('div', 'tr', str(v.translation)));
  c.append(s);

  if (str(v.commentary)){
    const d = node('details', 'vc-comm');
    d.append(node('summary', '', 'Commentary'));
    for (const para of str(v.commentary).split(/\n\s*\n/)) if (para.trim()) d.append(node('p', '', para.trim()));
    c.append(d);
  }

  const ask = node('button', 'vc-ask', 'Ask about this verse');
  ask.type = 'button';
  ask.onclick = ()=>{
    const pill = document.querySelector(`.pill[data-book="${bookKey}"]`);   // fixed key, not server text
    if (pill) pill.click();
    input.value = `What does ${bookName} ${id.slice(3)} teach, and how can I live it today?`;
    pinVerse(id);   // id passed the verse-id regex in openVerseLink()
    input.focus();
  };
  c.append(ask);
  return c;
}

export function openVerseLink(){
  let q;
  try { q = new URLSearchParams(location.search); } catch { return; }
  const entry = Number(q.get('entry'));
  return showVerse(q.get('verse'), Number.isInteger(entry) && entry > 0 ? entry : null);
}

// Also the tap target of the Unread / History / Favourites rows (inbox.js).
export async function showVerse(id, entryId){
  if (!id || !VERSE_ID.test(id)) return;
  const [v, votd] = await Promise.all([
    getJson('/verse/' + encodeURIComponent(id)),
    getJson('/verse-of-the-day'),
    loadInbox(),
  ]);
  const entry = findEntry(entryId, id);
  // Only label a real paraphrase as one; the fallback quote is just the translation.
  // Any of today's sends so far (morning, evening) — not just the latest — so a
  // morning notification opened in the evening still shows its reflection.
  const sends = votd ? (Array.isArray(votd.slots) && votd.slots.length ? votd.slots : [votd]) : [];
  const today = sends.find(s => s && s.verse_id === id && str(s.quote) && s.quote_source !== 'fallback') || null;
  if (activeConv().messages.length) newChat();          // start clean so the card is in view
  stream.querySelectorAll('.empty, .verse-card, .inbox').forEach(n => n.remove());
  stream.prepend(card(id, v, today, entry));
  scrollDown();
  markOpened(entry);
  document.dispatchEvent(new Event('inneroracle:view'));
}
