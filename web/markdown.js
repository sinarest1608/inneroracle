// Markdown rendering + citation linking. Uses the globals from the vendored
// marked / dompurify scripts (loaded before this module).

window.marked.setOptions({ breaks:true });

// Turn citations into clickable spans, including grouped ones like [AG 15.20, AG 2.12].
export function linkify(html){
  return html.replace(/\[([^\]]*(?:BG|AG)[ \-]?\d+\.[\w\-]+[^\]]*)\]/g, (m, inner)=>{
    const linked = inner.replace(/(BG|AG)[ \-]?(\d+\.[\w\-]+)/g,
      (mm,b,n)=>`<span class="cite" data-id="${b}-${n}">${b} ${n}</span>`);
    return '['+linked+']';
  });
}

export function renderMarkdown(text){
  return linkify(window.DOMPurify.sanitize(window.marked.parse(text)));
}
