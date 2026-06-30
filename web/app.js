// ---------- theme ----------
const THEME_KEY = 'inneroracle.theme';
function applyTheme(t){
  document.documentElement.dataset.theme = t;
  document.getElementById('theme').textContent = t === 'dark' ? '☀️' : '🌙';
}
applyTheme(localStorage.getItem(THEME_KEY) ||
  (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
document.getElementById('theme').onclick = ()=>{
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem(THEME_KEY, next); applyTheme(next);
};

// ---------- elements ----------
const stream = document.getElementById('stream');
const chat = document.getElementById('chat');
const form = document.getElementById('form');
const input = document.getElementById('q');
const sendBtn = document.getElementById('send');
const convsEl = document.getElementById('convs');
const sidebar = document.getElementById('sidebar');
const overlay = document.getElementById('overlay');

// ---------- conversation store ----------
const STORE = 'inneroracle.conversations.v1';
const OLD = 'inneroracle.history.v1';
let state = loadState();

function loadState(){
  try {
    const s = JSON.parse(localStorage.getItem(STORE));
    if (s && Array.isArray(s.conversations)) return s;
  } catch {}
  let conversations = [];
  try {
    const old = JSON.parse(localStorage.getItem(OLD));
    if (Array.isArray(old) && old.length) { conversations = [mkConv(old)]; localStorage.removeItem(OLD); }
  } catch {}
  return { conversations, activeId: conversations[0]?.id || null };
}
function save(){ localStorage.setItem(STORE, JSON.stringify(state)); }
function uid(){ return Date.now().toString(36) + Math.random().toString(36).slice(2,7); }
function titleFrom(messages){
  const first = messages.find(m=>m.role==='user');
  const t = first ? first.content.trim() : 'New chat';
  return t.length > 42 ? t.slice(0,42) + '…' : t;
}
function mkConv(messages){ return { id: uid(), title: titleFrom(messages||[]), messages: messages||[], updatedAt: Date.now() }; }
function activeConv(){
  let c = state.conversations.find(c=>c.id===state.activeId);
  if (!c){ c = mkConv([]); state.conversations.unshift(c); state.activeId = c.id; }
  return c;
}
function pruneEmpty(keepId){ state.conversations = state.conversations.filter(c=>c.messages.length>0 || c.id===keepId); }

// ---------- helpers ----------
function el(cls, html){ const d=document.createElement('div'); d.className=cls; if(html!==undefined) d.innerHTML=html; return d; }
function scrollDown(){ chat.scrollTop = chat.scrollHeight; }
function escapeHtml(s){ return s.replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
marked.setOptions({ breaks:true });
function linkify(html){
  return html.replace(/\[([^\]]*(?:BG|AG)[ \-]?\d+\.[\w\-]+[^\]]*)\]/g, (m, inner)=>{
    const linked = inner.replace(/(BG|AG)[ \-]?(\d+\.[\w\-]+)/g,
      (mm,b,n)=>`<span class="cite" data-id="${b}-${n}">${b} ${n}</span>`);
    return '['+linked+']';
  });
}
function renderMarkdown(text){ return linkify(DOMPurify.sanitize(marked.parse(text))); }
function addMsg(role, who){
  const m = el('msg '+role, `<div class="who">${who}</div>`);
  const bubble = el('bubble',''); m.appendChild(bubble);
  stream.appendChild(m); scrollDown();
  return { m, bubble };
}

// ---------- suggestions ----------
const SUGGESTIONS = [
  'How do I deal with anxiety about results?', 'What is the self?',
  'How should I handle anger?', 'How do I find peace in difficult times?',
  'What does it mean to act without attachment?', 'How do I face the fear of failure?',
  'What is true freedom?', 'How should I cope with loss and grief?',
  'How do I quiet a restless mind?', 'What is my duty when I feel torn?',
  'How do I let go of control?', 'How do I overcome craving and desire?',
  'How can I be content with what I have?', 'What is the path to lasting happiness?',
  'How do I stay steady amid praise and blame?',
];
function pick3(){
  const a = SUGGESTIONS.slice();
  for (let i=a.length-1;i>0;i--){ const j=(Math.random()*(i+1))|0; [a[i],a[j]]=[a[j],a[i]]; }
  return a.slice(0,3);
}
function renderEmpty(){
  const e = el('empty',
    `<div class="hero">What's on your mind <span class="dim">today?</span></div>
     <div class="sub">Answers come only from the two texts, with tappable citations to the original shloka.</div>`);
  pick3().forEach(x=>{ const c=el('ex'); c.textContent=x; c.onclick=()=>{ input.value=x; form.requestSubmit(); }; e.appendChild(c); });
  stream.appendChild(e);
}

// ---------- render conversation + sidebar ----------
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
function renderSidebar(){
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
function refresh(){ renderActive(); renderSidebar(); }

function switchTo(id){ pruneEmpty(id); state.activeId=id; save(); refresh(); closeSidebar(); }
function newChat(){
  pruneEmpty(null);
  const c = mkConv([]); state.conversations.unshift(c); state.activeId=c.id;
  save(); refresh(); input.focus(); closeSidebar();
}
function deleteConv(id){
  state.conversations = state.conversations.filter(c=>c.id!==id);
  if (state.activeId===id) state.activeId = state.conversations[0]?.id || null;
  save(); refresh();
}

// ---------- book selector + about ----------
let book = '';
document.getElementById('pills').addEventListener('click', e=>{
  const p = e.target.closest('.pill'); if(!p) return;
  document.querySelectorAll('.pill').forEach(x=>x.classList.remove('active'));
  p.classList.add('active'); book = p.dataset.book;
});
const aboutPop = document.getElementById('aboutPop');
document.getElementById('aboutBtn').onclick = (e)=>{ e.stopPropagation(); aboutPop.hidden = !aboutPop.hidden; };
document.addEventListener('click', e=>{ if(!e.target.closest('.about')) aboutPop.hidden = true; });

// ---------- sidebar toggle ----------
function openSidebar(){ sidebar.classList.add('open'); overlay.classList.add('show'); }
function closeSidebar(){ sidebar.classList.remove('open'); overlay.classList.remove('show'); }
document.getElementById('hamburger').onclick = openSidebar;
overlay.onclick = closeSidebar;
document.getElementById('newChat').onclick = newChat;

// ---------- shloka on demand ----------
async function fetchVerse(id, mount){
  if(mount.dataset.loaded){ mount.style.display = mount.style.display==='none'?'block':'none'; return; }
  const r = await fetch('/verse/'+encodeURIComponent(id));
  if(!r.ok){ mount.innerHTML='<div class="shloka">Verse not found.</div>'; mount.dataset.loaded='1'; return; }
  const v = await r.json();
  let h='<div class="shloka">';
  if(v.sanskrit) h+=`<div class="dev">${escapeHtml(v.sanskrit)}</div>`;
  if(v.transliteration) h+=`<div class="tl">${escapeHtml(v.transliteration)}</div>`;
  if(v.translation) h+=`<div class="tr">${escapeHtml(v.translation)}</div>`;
  h+='</div>';
  mount.innerHTML=h; mount.dataset.loaded='1'; scrollDown();
}
stream.addEventListener('click', e=>{
  const c = e.target.closest('.cite'); if(!c) return;
  let panel = c.closest('.msg').querySelector('.shloka-mount[data-for="'+c.dataset.id+'"]');
  if(!panel){ panel = el('shloka-mount'); panel.dataset.for=c.dataset.id; c.closest('.bubble').after(panel); }
  fetchVerse(c.dataset.id, panel);
});

// ---------- ask ----------
async function ask(question){
  const conv = activeConv();
  const emptyEl = stream.querySelector('.empty'); if(emptyEl) emptyEl.remove();
  addMsg('user','You').bubble.textContent = question;

  const { m: aMsg, bubble } = addMsg('assistant','InnerOracle');
  const thinking = el('thinking','<span></span><span></span><span></span>');
  bubble.appendChild(thinking);

  const history = conv.messages.map(m=>({role:m.role, content:m.content}));

  let answer='', started=false;
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

form.addEventListener('submit', async e=>{
  e.preventDefault();
  const q = input.value.trim(); if(!q) return;
  input.value=''; sendBtn.disabled=true; input.disabled=true;
  try { await ask(q); }
  catch(err){ addMsg('assistant','InnerOracle').bubble.textContent='Error: '+String(err); }
  finally { sendBtn.disabled=false; input.disabled=false; input.focus(); }
});

// ---------- boot ----------
refresh();
input.focus();
