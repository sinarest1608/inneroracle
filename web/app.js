// Entry point: wire the modules together and boot.

import { input, form, sendBtn, overlay, addMsg } from './dom.js';
import { initTheme } from './theme.js';
import { refresh, newChat, openSidebar, closeSidebar, clearAll } from './views.js';
import { ask, initCitations } from './chat.js';
import { initPush } from './push.js';
import { openVerseLink } from './verselink.js';

initTheme();

// ---- book selector + about popover ----
let book = '';
document.getElementById('pills').addEventListener('click', e=>{
  const p = e.target.closest('.pill'); if(!p) return;
  document.querySelectorAll('.pill').forEach(x=>x.classList.remove('active'));
  p.classList.add('active'); book = p.dataset.book;
});
const aboutPop = document.getElementById('aboutPop');
document.getElementById('aboutBtn').onclick = (e)=>{ e.stopPropagation(); aboutPop.hidden = !aboutPop.hidden; };
document.addEventListener('click', e=>{ if(!e.target.closest('.about')) aboutPop.hidden = true; });

// ---- sidebar drawer ----
document.getElementById('hamburger').onclick = openSidebar;
overlay.onclick = closeSidebar;
document.getElementById('newChat').onclick = newChat;
document.getElementById('clearAll').onclick = clearAll;

initCitations();

// ---- submit ----
form.addEventListener('submit', async e=>{
  e.preventDefault();
  const q = input.value.trim(); if(!q) return;
  input.value=''; sendBtn.disabled=true; input.disabled=true;
  try { await ask(q, book); }
  catch(err){ addMsg('assistant','InnerOracle').bubble.textContent='Error: '+String(err); }
  finally { sendBtn.disabled=false; input.disabled=false; input.focus(); }
});

// ---- boot ----
refresh();
input.focus();
openVerseLink();
initPush();
