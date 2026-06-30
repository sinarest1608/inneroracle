// Conversation persistence (localStorage). Pure data layer — no DOM.

const STORE = 'inneroracle.conversations.v1';
const OLD = 'inneroracle.history.v1';

export function uid(){ return Date.now().toString(36) + Math.random().toString(36).slice(2,7); }

export function titleFrom(messages){
  const first = messages.find(m=>m.role==='user');
  const t = first ? first.content.trim() : 'New chat';
  return t.length > 42 ? t.slice(0,42) + '…' : t;
}

export function mkConv(messages){
  return { id: uid(), title: titleFrom(messages||[]), messages: messages||[], updatedAt: Date.now() };
}

function loadState(){
  try {
    const s = JSON.parse(localStorage.getItem(STORE));
    if (s && Array.isArray(s.conversations)) return s;
  } catch {}
  // migrate a single old conversation, if present
  let conversations = [];
  try {
    const old = JSON.parse(localStorage.getItem(OLD));
    if (Array.isArray(old) && old.length) { conversations = [mkConv(old)]; localStorage.removeItem(OLD); }
  } catch {}
  return { conversations, activeId: conversations[0]?.id || null };
}

// Single shared state object; modules mutate its properties (never reassign).
export const state = loadState();

export function save(){ localStorage.setItem(STORE, JSON.stringify(state)); }

export function activeConv(){
  let c = state.conversations.find(c=>c.id===state.activeId);
  if (!c){ c = mkConv([]); state.conversations.unshift(c); state.activeId = c.id; }
  return c;
}

export function pruneEmpty(keepId){
  state.conversations = state.conversations.filter(c=>c.messages.length>0 || c.id===keepId);
}
