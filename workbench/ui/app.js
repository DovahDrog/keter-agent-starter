'use strict';
const $ = id => document.getElementById(id);
const key = location.hash ? location.hash.slice(1) : '';
if (key) history.replaceState(null, '', location.pathname + location.search);
const show = (id, value) => { $(id).textContent = value; };
let searchSeq = 0, readSeq = 0, pathSeq = 0, healthSeq = 0;
let note = null, refreshing = false, targets = new Set();
async function api(route, args={}, method='GET') {
  if (!key) throw new Error('Open the private address printed by the server to unlock a new session.');
  const suffix = new URLSearchParams(args).toString();
  const response = await fetch(`/api/${route}${suffix?'?'+suffix:''}`, {method,headers:{'X-Workbench-Token':key},cache:'no-store'});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Request could not be completed.');
  return value;
}
const el = (tag, content, cls) => {
  const node = document.createElement(tag); node.textContent = content;
  if (cls) node.className = cls;
  return node;
};
function track(path) {
  if (targets.has(path) || targets.size >= 64) return;
  targets.add(path); const option = document.createElement('option'); option.value = path; $('known-notes').append(option);
}
function choices(id, items, empty, onClick, subtitle) {
  const list = $(id); list.replaceChildren();
  for (const item of items) {
    const li = document.createElement('li'), button = el('button', item.title || item.path, 'choice');
    button.type = 'button'; button.append(el('span', subtitle ? subtitle(item) : item.path));
    button.addEventListener('click', () => onClick(item));
    li.append(button); list.append(li); track(item.path);
    if (note && item.path===note.path) button.setAttribute('aria-current','true');
    button.dataset.note = item.path;
  }
  if (!items.length && empty) list.append(el('li', empty, 'small'));
}
function healthCard(box, name, detail, bad=false) {
  const card = el('div','','card'); card.append(el('strong',name,bad?'attention':'good'),el('p',detail,'small')); box.append(card);
}
async function health() {
  const n=++healthSeq;
  try {
    const h=await api('health'); if (n!==healthSeq) return;
    const box=$('health'); box.replaceChildren();
    const i=h.index, g=h.graph, m=h.maintenance;

    healthCard(box,i.drift?'Index needs refresh':i.skipped?'Index has exclusions':'Index matches file inventory',`${i.notes} notes · ${i.sections} sections · ${i.skipped} skipped · ${i.build_ms} ms to build`,Boolean(i.drift || i.skipped));
    const warnings=g.detected_missing+g.detected_ambiguous+(g.detected_outside || 0);
    const graphBad=Boolean(i.drift || i.skipped || g.missing_roots.length || warnings || g.unreachable);
    healthCard(box,i.drift?'Link snapshot is stale':graphBad?'Link checks need attention':'Indexed links checked',`${g.edges} links · ${warnings} unresolved · ${g.unreachable===null?"no starting map found":g.unreachable+" unreachable"}${g.missing_roots.length?" · "+g.missing_roots.length+" expected maps missing or empty":""}. Limited parser; not an Obsidian sync check.`,graphBad);
    if (g.warning_examples.length && !i.drift) {
      const list=el('ul','','list'); list.id='health-warnings'; list.setAttribute('aria-label','Link warning examples'); box.append(list);
      choices('health-warnings',g.warning_examples.map(w=>({...w,path:w.source,title:w.source})),null,w=>openNote(w.source,w.line),w=>` · L${w.line} · ${w.kind}: ${w.target}`);
    }
    const pending=m.pending===null?'writeback count unknown':`${m.pending} pending writeback`;
    if(m.status==='not configured') healthCard(box,'No maintenance receipt configured for this vault','Local index and links can still be checked.');
    else healthCard(box,!m.same_run?'Maintenance receipt unavailable':m.stale?'Maintenance receipt is stale':`Maintenance receipt: ${m.status}`,`${pending}. ${m.checked_at || 'No verified timestamp'}. Recorded receipt only; no sync ran here.`,!m.same_run || m.stale || m.status!=='green' || m.pending!==0);
    choices('roots',h.roots,'No starting maps found.',x=>openNote(x.path));
  } catch (e) { if (n===healthSeq) show('health',e.message); }
}
async function search() {
  const n=++searchSeq, q=$('query').value.trim();
  if (!q) return;
  show('search-meta','Searching locally…'); $('results').replaceChildren();
  try {
    const r=await api('search',{q,folder:$('folder').value.trim()}); if (n!==searchSeq) return;
    show('search-meta',`${r.hits.length} candidates · ${r.ms} ms · ${r.mode}`);
    choices('results',r.hits,'No matching notes.',x=>openNote(x.path,x.line,0,true,x.sha256),x=>`${x.path}${x.heading?' · '+x.heading:''}`);
  } catch(e){ if(n===searchSeq) show('search-meta',e.message); }
}
function showNote(x) {
  note=x; track(x.path);
  $('welcome').hidden=true; $('reader').hidden=false; $('connections').hidden=false;
  show('note-path',x.path); show('note-title',x.title);
  show('evidence',`Lines ${x.start_line}–${x.end_line} · SHA‑256 ${x.sha256.slice(0,12)} · ${x.bytes} bytes`);
  $('evidence').title=x.sha256;
  show('excerpt',x.text || 'This note is empty.'); $('next').hidden=!x.next;
  const select=$('outline');select.replaceChildren();
  const first=document.createElement('option');first.value='1';first.textContent='Start of note';select.append(first);
  for(const h of x.outline){ const o=document.createElement('option');o.value=String(h.line);o.textContent=`${'— '.repeat(Math.max(h.level-1,0))}${h.name} · L${h.line}`; select.append(o); }
  const good=x.outline.filter(y=>y.line<=x.start_line).pop(); select.value=good?String(good.line):'1';
  choices('outlinks',x.outlinks,'No indexed outgoing links.',i=>openNote(i.path));
  choices('backlinks',x.backlinks,'No indexed backlinks.',i=>openNote(i.path,i.line),i=>`${i.path} · L${i.line}`);
  $('issues').replaceChildren(...x.unresolved.map(i=>el('li',`L${i.line} · ${i.kind}: ${i.target}`,'small')));
  if (!x.unresolved.length) $('issues').append(el('li','None detected.','small'));
  for(const b of document.querySelectorAll('button[data-note]')){if(b.dataset.note===x.path)b.setAttribute('aria-current','true');else b.removeAttribute('aria-current');}
}
async function openNote(path,line=1,offset=0,focus=true,expected='') {
  const n=++readSeq; show('notice','Verifying note against its current bytes…');
  try {
    const x=await api('read',{path,line,offset}); if(n!==readSeq)return;
    if (expected && x.sha256!==expected) throw new Error('This result belongs to a previous index. Search again before citing it.');
    showNote(x); show('notice','Exact source verified.'); if(focus) $('note-title').focus();
  } catch(e) { if(n===readSeq){ note=null; $('reader').hidden=true; $('connections').hidden=true; $('welcome').hidden=false; show('notice',e.message); } }
}
async function pathway(){
  const n=++pathSeq; $('path-result').replaceChildren(); show('path-note','Tracing local directed links…');
  try{
    const r=await api('path',{from:$('path-start').value.trim(),to:$('path-end').value.trim()}); if(n!==pathSeq)return;
    show('path-note',r.path.length?`${r.hops} verified directed ${r.hops===1?'link':'links'}.`:(r.scope || 'No directed pathway found.'));
    for(let i=0;i<r.path.length;i++){
      const p=r.path[i],li=document.createElement('li'),b=el('button',p,'choice');b.type='button';
      if(r.edges[i]) b.append(el('span',`Link from line ${r.edges[i].line}`));
      b.addEventListener('click',()=>openNote(p,r.edges[i]?.line || 1));li.append(b);$('path-result').append(li);
    }
  }catch(e){if(n===pathSeq)show('path-note',e.message);}
}
async function refresh(){
  if(refreshing)return;
  refreshing=true; $('refresh').disabled=true; ++searchSeq; ++readSeq; ++pathSeq;
  const previous=note;
  $('path-result').replaceChildren(); show('path-note',''); show('notice','Rebuilding the in-memory index…');
  try{
    const r=await api('refresh',{},'POST');
    $('results').replaceChildren(); show('search-meta','Index refreshed.');
    if(previous){ note=null; $('reader').hidden=true; $('connections').hidden=true; $('welcome').hidden=false; }
    show('notice',`Index refreshed · ${r.notes} notes. Reopen a result to verify it.`);
    await health(); if($('query').value.trim()) await search();
  }catch(e){show('notice',e.message);}
  finally{refreshing=false; $('refresh').disabled=false;}
}
$('search-form').addEventListener('submit',e=>{e.preventDefault();void search();});
$('path-form').addEventListener('submit',e=>{e.preventDefault();void pathway();});
$('refresh').addEventListener('click',refresh); $('check').addEventListener('click',health);
$('outline').addEventListener('change',e=>{if(note)void openNote(note.path,Number(e.target.value),0,false,note.sha256);});
$('next').addEventListener('click',()=>{if(note?.next)void openNote(note.path,note.next[0],note.next[1],false,note.sha256);});
$('start').addEventListener('click',()=>{if(note){ $('path-start').value=note.path; $('path-end').focus(); }});
$('finish').addEventListener('click',()=>{if(note){ $('path-end').value=note.path; $('path-start').focus(); }});
$('copy').addEventListener('click',async()=>{
  if(!note)return;
  const x=note;
  try{await navigator.clipboard.writeText(`${x.path}:L${x.start_line}–L${x.end_line} (SHA‑256 ${x.sha256})`);show('notice','Source reference copied; no note text copied.');}
  catch {show('notice','Clipboard unavailable; the exact path and line range are shown above.');}
});
void health();
