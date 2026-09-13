/* Minimal markdown renderer.
   CDNs are blocked on a sealed machine, and bundling marked.js is another
   dependency. We only need what the model actually emits: headings, bold,
   italic, code, lists, tables, quotes, rules and citations.
   Escaping happens FIRST, so model output can never inject HTML. */

export function esc(s){
  return String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function inline(s){
  return s
    .replace(/`([^`]+)`/g, (m,c)=>`<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[(\d{1,2})\]/g,
      (m,n)=>`<a class="cit" data-n="${n}" title="Jump to source ${n}">[${n}]</a>`);
}

export function render(src){
  const lines = esc(src).split("\n");
  let out = "", i = 0;

  const para = buf => { if(buf.length) out += `<p>${inline(buf.join(" "))}</p>`; };

  while(i < lines.length){
    const L = lines[i];

    // fenced code
    if(/^```/.test(L)){
      const body = [];
      i++;
      while(i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out += `<pre><code>${body.join("\n")}</code></pre>`;
      continue;
    }
    // heading
    const h = L.match(/^(#{1,3})\s+(.*)$/);
    if(h){ const n = h[1].length; out += `<h${n}>${inline(h[2])}</h${n}>`; i++; continue; }
    // hr
    if(/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(L)){ out += "<hr>"; i++; continue; }
    // blockquote
    if(/^&gt;\s?/.test(L)){
      const b = [];
      while(i < lines.length && /^&gt;\s?/.test(lines[i])) b.push(lines[i++].replace(/^&gt;\s?/,""));
      out += `<blockquote>${inline(b.join(" "))}</blockquote>`;
      continue;
    }
    // table — header | --- | rows
    if(L.includes("|") && /^\s*\|?[\s:-]*-[\s|:-]*$/.test(lines[i+1] || "")){
      const cells = r => r.replace(/^\||\|$/g,"").split("|").map(c=>c.trim());
      const head = cells(L);
      i += 2;
      const rows = [];
      while(i < lines.length && lines[i].includes("|")) rows.push(cells(lines[i++]));
      out += "<table><thead><tr>" + head.map(c=>`<th>${inline(c)}</th>`).join("")
           + "</tr></thead><tbody>"
           + rows.map(r=>"<tr>"+r.map(c=>`<td>${inline(c)}</td>`).join("")+"</tr>").join("")
           + "</tbody></table>";
      continue;
    }
    // lists
    if(/^\s*([-*+]|\d+\.)\s+/.test(L)){
      const ord = /^\s*\d+\./.test(L);
      const items = [];
      while(i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i]))
        items.push(lines[i++].replace(/^\s*([-*+]|\d+\.)\s+/,""));
      out += `<${ord?"ol":"ul"}>` + items.map(t=>`<li>${inline(t)}</li>`).join("")
           + `</${ord?"ol":"ul"}>`;
      continue;
    }
    // paragraph
    if(!L.trim()){ i++; continue; }
    const buf = [];
    while(i < lines.length && lines[i].trim() &&
          !/^```|^#{1,3}\s|^\s*([-*+]|\d+\.)\s|^&gt;/.test(lines[i]))
      buf.push(lines[i++]);
    para(buf);
  }
  return out;
}
