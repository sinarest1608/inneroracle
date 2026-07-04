// Chat: stream an answer from /chat into a message bubble, persist the turn,
// and the tap-a-citation -> shloka panel behaviour.

import { stream, el, scrollDown, escapeHtml, addMsg } from './dom.js';
import { renderMarkdown } from './markdown.js';
import { activeConv, save, titleFrom } from './store.js';
import { renderSidebar } from './views.js';
import { reverify } from './turnstile.js';

// POST /chat; if the human cookie expired (401), re-run Turnstile once and retry.
async function chatFetch(payload) {
  const opts = { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) };
  let res = await fetch('/chat', opts);
  if (res.status === 401) { await reverify(); res = await fetch('/chat', opts); }
  return res;
}

async function fetchVerse(id, mount){
  if (mount.dataset.loaded){ mount.style.display = mount.style.display==='none'?'block':'none'; return; }
  const r = await fetch('/verse/'+encodeURIComponent(id));
  if (!r.ok){ mount.innerHTML='<div class="shloka">Verse not found.</div>'; mount.dataset.loaded='1'; return; }
  const v = await r.json();
  let h='<div class="shloka">';
  if (v.sanskrit) h+=`<div class="dev">${escapeHtml(v.sanskrit)}</div>`;
  if (v.transliteration) h+=`<div class="tl">${escapeHtml(v.transliteration)}</div>`;
  if (v.translation) h+=`<div class="tr">${escapeHtml(v.translation)}</div>`;
  h+='</div>';
  mount.innerHTML=h; mount.dataset.loaded='1'; scrollDown();
}

// Wire tap-on-citation to load the original shloka inline.
export function initCitations(){
  stream.addEventListener('click', e=>{
    const c = e.target.closest('.cite'); if(!c) return;
    let panel = c.closest('.msg').querySelector('.shloka-mount[data-for="'+c.dataset.id+'"]');
    if (!panel){ panel = el('shloka-mount'); panel.dataset.for=c.dataset.id; c.closest('.bubble').after(panel); }
    fetchVerse(c.dataset.id, panel);
  });
}

export async function ask(question, book){
  const conv = activeConv();
  const emptyEl = stream.querySelector('.empty'); if(emptyEl) emptyEl.remove();
  addMsg('user','You').bubble.textContent = question;

  const { m: aMsg, bubble } = addMsg('assistant','InnerOracle');
  const thinking = el('thinking','<span></span><span></span><span></span>');
  bubble.appendChild(thinking);

  const history = conv.messages.map(m=>({role:m.role, content:m.content}));

  let answer='', started=false;
  const res = await chatFetch({question, history, book: book || null});
  if (!res.ok || !res.body) { bubble.textContent = 'Verification needed — please refresh and try again.'; return; }
  const reader = res.body.getReader(); const dec = new TextDecoder(); let buf='';
  while(true){
    const {value, done} = await reader.read(); if(done) break;
    buf += dec.decode(value, {stream:true});
    const parts = buf.split('\n\n'); buf = parts.pop();
    for(const p of parts){
      const line = p.split('\n').find(l=>l.startsWith('data:')); if(!line) continue;
      const ev = JSON.parse(line.slice(5).trim());
      if(ev.type==='token'){
        if(!started){ bubble.textContent=''; started=true; }
        answer+=ev.text; bubble.textContent=answer; scrollDown();
      } else if(ev.type==='verification'){
        const ok = ev.grounded;
        aMsg.appendChild(el('badge '+(ok?'ok':'warn'),
          ok ? '✓ grounded in the texts'
             : '⚠ check sources'+(ev.invalid_citations.length?` (unknown: ${ev.invalid_citations.join(', ')})`:'')));
      }
    }
  }
  if(!started) bubble.textContent='';
  bubble.innerHTML = renderMarkdown(answer);
  scrollDown();

  conv.messages.push({role:'user', content:question});
  conv.messages.push({role:'assistant', content:answer});
  conv.title = titleFrom(conv.messages);
  conv.updatedAt = Date.now();
  save(); renderSidebar();
}
