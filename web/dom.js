// Cached DOM element references + small DOM helpers shared across modules.

export const stream = document.getElementById('stream');
export const chat = document.getElementById('chat');
export const form = document.getElementById('form');
export const input = document.getElementById('q');
export const sendBtn = document.getElementById('send');
export const convsEl = document.getElementById('convs');
export const sidebar = document.getElementById('sidebar');
export const overlay = document.getElementById('overlay');

export function el(cls, html){
  const d = document.createElement('div');
  d.className = cls;
  if (html !== undefined) d.innerHTML = html;
  return d;
}
export function scrollDown(){ chat.scrollTop = chat.scrollHeight; }
export function escapeHtml(s){ return s.replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

// Append a labelled message row and return its element + (empty) bubble.
export function addMsg(role, who){
  const m = el('msg '+role, `<div class="who">${who}</div>`);
  const bubble = el('bubble',''); m.appendChild(bubble);
  stream.appendChild(m); scrollDown();
  return { m, bubble };
}
