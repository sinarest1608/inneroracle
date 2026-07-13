// Markdown rendering + citation linking. Uses the globals from the vendored
// marked / dompurify scripts (loaded before this module).
//
// Order matters: sanitize FIRST, then linkify on the parsed DOM. Building the
// cite spans as real elements on text nodes (rather than regex-splicing markup
// into the sanitized HTML string) means we can never re-introduce markup in an
// attribute or break what DOMPurify approved.

window.marked.setOptions({ breaks:true });

// Grouped citations too, e.g. [AG 15.20, AG 2.12].
const GROUP_RE = /\[([^\]]*(?:BG|AG)[ \-]?\d+\.[\w\-]+[^\]]*)\]/g;
const TOKEN_RE = /(BG|AG)[ \-]?(\d+\.[\w\-]+)/g;

function citeSpan(book, num){
  const s = document.createElement('span');
  s.className = 'cite';
  s.dataset.id = `${book}-${num}`;
  s.textContent = `${book} ${num}`;
  return s;
}

// Replace citation patterns inside one text node with text + cite spans.
function linkifyTextNode(node){
  const text = node.nodeValue;
  GROUP_RE.lastIndex = 0;
  let m, last = 0;
  const frag = document.createDocumentFragment();
  while((m = GROUP_RE.exec(text))){
    frag.appendChild(document.createTextNode(text.slice(last, m.index) + '['));
    const inner = m[1];
    TOKEN_RE.lastIndex = 0;
    let t, tLast = 0;
    while((t = TOKEN_RE.exec(inner))){
      if (t.index > tLast) frag.appendChild(document.createTextNode(inner.slice(tLast, t.index)));
      frag.appendChild(citeSpan(t[1], t[2]));
      tLast = TOKEN_RE.lastIndex;
    }
    if (tLast < inner.length) frag.appendChild(document.createTextNode(inner.slice(tLast)));
    frag.appendChild(document.createTextNode(']'));
    last = GROUP_RE.lastIndex;
  }
  if (!last) return;
  frag.appendChild(document.createTextNode(text.slice(last)));
  node.replaceWith(frag);
}

function linkify(root){
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(linkifyTextNode);
}

export function renderMarkdown(text){
  const tpl = document.createElement('template');
  tpl.innerHTML = window.DOMPurify.sanitize(window.marked.parse(text));
  linkify(tpl.content);
  return tpl.innerHTML;
}
