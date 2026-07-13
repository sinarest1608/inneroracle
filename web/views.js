// View layer: render the active conversation + the sidebar list, and the
// conversation-navigation actions (switch / new / delete) + drawer toggle.

import { stream, convsEl, input, form, el, escapeHtml, addMsg, sidebar, overlay } from './dom.js';
import { state, save, mkConv, activeConv, pruneEmpty } from './store.js';
import { renderMarkdown } from './markdown.js';
import { pick3 } from './suggestions.js';

export function openSidebar(){ sidebar.classList.add('open'); overlay.classList.add('show'); }
export function closeSidebar(){ sidebar.classList.remove('open'); overlay.classList.remove('show'); }

function renderEmpty(){
  const e = el('empty',
    `<div class="hero">What's on your mind <span class="dim">today?</span></div>
     <div class="sub">Answers come only from the two texts, with tappable citations to the original shloka.</div>`);
  pick3().forEach(x=>{ const c=el('ex'); c.textContent=x; c.onclick=()=>{ input.value=x; form.requestSubmit(); }; e.appendChild(c); });
  stream.appendChild(e);
}

function renderActive(){
  stream.innerHTML='';
  const c = activeConv();
  if (!c.messages.length){ renderEmpty(); return; }
  for (const turn of c.messages){
    const who = turn.role==='user' ? 'You' : 'InnerOracle';
    const { bubble } = addMsg(turn.role==='user'?'user':'assistant', who);
    bubble.innerHTML = turn.role==='user' ? escapeHtml(turn.content) : renderMarkdown(turn.content);
  }
}

export function renderSidebar(){
  convsEl.innerHTML='';
  const list = state.conversations.filter(c=>c.messages.length>0).sort((a,b)=>b.updatedAt-a.updatedAt);
  for (const c of list){
    const row = el('conv' + (c.id===state.activeId?' active':''));
    const t = el('t'); t.textContent = c.title; t.title = c.title;
    const del = document.createElement('button'); del.className='del'; del.textContent='✕';
    del.title='Delete'; del.onclick=(e)=>{ e.stopPropagation(); deleteConv(c.id); };
    row.append(t, del);
    row.onclick=()=>switchTo(c.id);
    convsEl.appendChild(row);
  }
}

export function refresh(){ renderActive(); renderSidebar(); }

export function switchTo(id){ pruneEmpty(id); state.activeId=id; save(); refresh(); closeSidebar(); }

export function newChat(){
  pruneEmpty(null);
  const c = mkConv([]); state.conversations.unshift(c); state.activeId=c.id;
  save(); refresh(); input.focus(); closeSidebar();
}

export function deleteConv(id){
  state.conversations = state.conversations.filter(c=>c.id!==id);
  if (state.activeId===id) state.activeId = state.conversations[0]?.id || null;
  save(); refresh();
}

// Wipe every conversation from localStorage — questions here can be personal,
// so give shared-device users a one-tap way out.
export function clearAll(){
  if (!confirm('Delete all conversations? This cannot be undone.')) return;
  state.conversations = [];
  state.activeId = null;
  save(); refresh(); closeSidebar();
}
