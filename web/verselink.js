// Deep link /ui/?verse=<id> (the notification tap target): show that verse as
// a card above the chat, with today's reflection when it is today's verse, and
// an "Ask about this verse" button. All server text goes in via textContent.

import { stream, input, scrollDown } from './dom.js';
import { activeConv } from './store.js';
import { newChat } from './views.js';

const VERSE_ID = /^(BG|AG)-\d{1,2}\.\d{1,3}(-\d{1,3})?$/;
const BOOKS = { BG: ['bhagavad_gita', 'Bhagavad Gita'], AG: ['ashtavakra_gita', 'Ashtavakra Gita'] };

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

function card(id, v, today){
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
    input.focus();
  };
  c.append(ask);
  return c;
}

export async function openVerseLink(){
  let id;
  try { id = new URLSearchParams(location.search).get('verse'); } catch { return; }
  if (!id || !VERSE_ID.test(id)) return;
  const [v, votd] = await Promise.all([
    getJson('/verse/' + encodeURIComponent(id)),
    getJson('/verse-of-the-day'),
  ]);
  // Only label a real paraphrase as one; the fallback quote is just the translation.
  // Any of today's sends so far (morning, evening) — not just the latest — so a
  // morning notification opened in the evening still shows its reflection.
  const sends = votd ? (Array.isArray(votd.slots) && votd.slots.length ? votd.slots : [votd]) : [];
  const today = sends.find(s => s && s.verse_id === id && str(s.quote) && s.quote_source !== 'fallback') || null;
  if (activeConv().messages.length) newChat();          // start clean so the card is in view
  const e = stream.querySelector('.empty'); if (e) e.remove();
  stream.prepend(card(id, v, today));
  scrollDown();
}
