// Chat: stream an answer from /chat into a message bubble, persist the turn,
// and the tap-a-citation -> shloka panel behaviour.

import { stream, el, scrollDown, escapeHtml, addMsg } from './dom.js';
import { renderMarkdown } from './markdown.js';
import { activeConv, save, titleFrom } from './store.js';
import { renderSidebar } from './views.js';

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

// "Thinking" indicator shown while we retrieve + wait for the first token:
// a single calm, breathing label beside the pulsing dots. Returns stop().
function startThinking(bubble){
  const node = el('thinking',
    '<span class="thinking-label">Consulting the texts…</span>'
    + '<span class="dots"><span></span><span></span><span></span></span>');
  bubble.appendChild(node);
  return ()=>{};
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
  startThinking(bubble);

  const history = conv.messages.map(m=>({role:m.role, content:m.content}));

  // Keep "thinking" on screen until the first token arrives AND for at least
  // MIN_THINKING ms, so it's always perceptible (never a flash, never a blank gap).
  const MIN_THINKING = 650;
  const t0 = performance.now();
  let answer='', gotFirst=false, revealed=false;
  const reveal = ()=>{ if(!revealed){ revealed=true; bubble.textContent=answer; scrollDown(); } };
  const sleep = (ms)=> new Promise(r=>setTimeout(r, ms));

  const res = await fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({question, history, book: book || null})});
  const reader = res.body.getReader(); const dec = new TextDecoder(); let buf='';
  while(true){
    const {value, done} = await reader.read(); if(done) break;
    buf += dec.decode(value, {stream:true});
    const parts = buf.split('\n\n'); buf = parts.pop();
    for(const p of parts){
      const line = p.split('\n').find(l=>l.startsWith('data:')); if(!line) continue;
      const ev = JSON.parse(line.slice(5).trim());
      if(ev.type==='token'){
        answer+=ev.text;
        if(!gotFirst){                          // first token: reveal once MIN has elapsed
          gotFirst=true;
          setTimeout(reveal, Math.max(0, MIN_THINKING - (performance.now() - t0)));
        }
        if(revealed){ bubble.textContent=answer; scrollDown(); }
      } else if(ev.type==='verification'){
        const ok = ev.grounded;
        aMsg.appendChild(el('badge '+(ok?'ok':'warn'),
          ok ? '✓ grounded in the texts'
             : '⚠ check sources'+(ev.invalid_citations.length?` (unknown: ${ev.invalid_citations.join(', ')})`:'')));
      }
    }
  }

  // Ensure the minimum has passed (covers very fast/short answers), then render.
  const remaining = MIN_THINKING - (performance.now() - t0);
  if (gotFirst && remaining > 0) await sleep(remaining);
  reveal();
  bubble.innerHTML = renderMarkdown(answer);
  scrollDown();

  conv.messages.push({role:'user', content:question});
  conv.messages.push({role:'assistant', content:answer});
  conv.title = titleFrom(conv.messages);
  conv.updatedAt = Date.now();
  save(); renderSidebar();
}
