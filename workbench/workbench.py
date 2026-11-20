#!/usr/bin/env python3
"""Local, read-only Obsidian research workbench and agent CLI (Python 3.9+)."""
import argparse
from collections import defaultdict, deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import secrets
import sqlite3
import stat
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlsplit

ASSETS = Path(__file__).resolve().parent/'ui'
ROOTS = ('BRAIN-MAP.md','Projects-MOC.md','Knowledge-MOC.md','Agents-MOC.md','Finance-MOC.md','Evolution-MOC.md','INDEX.md','SOUL.md')
MAX_NOTE = 768 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 5000
MAX_SECTIONS = 50000
MAX_DEPTH = 12
MAX_CHARS = 12000
STOP = set('a an the to for on by of in is as and or with was at how what where which current latest find'.split())
HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$')
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
WIKI = re.compile(r'(?<![\\!])\[\[([^\[\]\r\n]{1,640})\]\]')
MD = re.compile(r'(?<![\\!])\[[^\]\r\n]{1,200}\]\(([^)\r\n]{1,640})\)')
CODE = re.compile(r'(`+).*?\1')

class Stale(RuntimeError):
    """Current file no longer matches the indexed generation."""


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def fold(text):
    return unicodedata.normalize('NFKC', text).casefold()


def terms(text):
    return [t for t in re.findall(r'[^\W_]+', fold(text)) if t not in STOP]


def short(text, cap):
    return ''.join(c for c in text if c == '\t' or not unicodedata.category(c).startswith('C'))[:cap]


def parts_for(path):
    if not isinstance(path, str) or len(path) > 2048 or '\\' in path or '\0' in path:
        raise ValueError('Use an exact vault-relative Markdown path.')
    p = PurePosixPath(path)
    if p.is_absolute() or p.as_posix() != path or not p.parts or len(p.parts)>MAX_DEPTH or any(x.startswith('.') for x in p.parts) or p.suffix.lower() != '.md':
        raise ValueError('Use an exact vault-relative Markdown path.')
    return p.parts


def signature(s):
    return (s.st_dev,s.st_ino,s.st_mtime_ns,s.st_ctime_ns,s.st_size,s.st_nlink)


def note(root, path):
    parts = parts_for(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    d = os.open(root, flags)
    try:
        for part in parts[:-1]:
            child = os.open(part, flags, dir_fd=d)
            os.close(d)
            d = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=d)
        with os.fdopen(fd, 'rb') as stream:
            a = os.fstat(stream.fileno())
            if not stat.S_ISREG(a.st_mode) or a.st_nlink != 1 or a.st_size>MAX_NOTE:
                raise ValueError('Unsupported note file.')
            raw = stream.read(MAX_NOTE+1)
            b = os.fstat(stream.fileno())
            if len(raw)!=b.st_size or len(raw)>MAX_NOTE or signature(a)!=signature(b):
                raise Stale('A note changed as it was read. Refresh and retry.')
            return raw, raw.decode('utf-8-sig'), signature(b)
    finally:
        os.close(d)


def walk(root):
    found = {}; errors = []; excluded = 0; directories = 0
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Accessible vault folder not found.')
    for dirname, dirs, files in os.walk(root, followlinks=False, onerror=errors.append):
        directories += 1
        if directories>MAX_FILES:
            raise ValueError('Vault directory budget exceeded.')
        directory = Path(dirname)
        rel = directory.relative_to(root)
        kept = []
        for d in sorted(dirs):
            if d.startswith('.') or d in ('node_modules','__pycache__'):
                continue
            if len(rel.parts)+1 >= MAX_DEPTH or (directory/d).is_symlink():
                excluded += 1
            else:
                kept.append(d)
        dirs[:] = kept
        for filename in sorted(files):
            if filename.startswith('.') or not filename.lower().endswith('.md'):
                continue
            path = (rel/filename).as_posix()
            try:
                s = (directory/filename).lstat()
                if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>MAX_NOTE:
                    excluded += 1
                    continue
                parts_for(path)
                found[path] = signature(s)
            except (OSError, ValueError):
                errors.append(None)
            if len(found)+excluded+len(errors)>MAX_FILES:
                raise ValueError('Vault note budget exceeded.')
    return found, len(errors), excluded


def metadata(text):
    lines=text.splitlines(); start=0; aliases=[]; title=''
    if lines and lines[0].strip()=='---':
        end=next((i for i,line in enumerate(lines[1:100],1) if line.strip()=='---'),0)
        if end:
            start=end+1; active=False
            for s in lines[1:end]:
                if s.startswith('title:'):
                    title=short(s.partition(':')[2].strip(' \t\"\''),160)
                if s.startswith('aliases:'):
                    a=s.partition(':')[2].strip(); active=not a
                    if a:
                        if a.startswith('[') and a.endswith(']'):a=a[1:-1]
                        aliases.extend(v.strip(' \t\"\'') for v in a.split(','))
                elif active and s.lstrip().startswith('- '):
                    aliases.append(s.lstrip()[2:].strip(' \t\"\''))
                elif s and not s.startswith((' ','\t','#')):
                    active=False
    outline=[]; sections=[]; ref=[]; body=[]; hidden=False; marker=None; row=start+1; current=''
    for i, line in enumerate(lines[start:], start+1):
        f=FENCE.match(line)
        if f:
            m,tail=f.groups()
            if marker is None: marker=m
            elif m[0]==marker[0] and len(m)>=len(marker) and not tail.strip(): marker=None
            continue
        if marker: continue
        # Obsidian %% comments, including comments spanning several lines.
        if '%%' in line or hidden:
            pieces=[]
            for piece in line.split('%%'):
                if not hidden:pieces.append(piece)
                hidden=not hidden
            hidden=not hidden
            line=''.join(pieces)
        line=CODE.sub('',line)
        h=HEADING.match(line)
        if h:
            if body or current:sections.append((row,current,'\n'.join(body)))
            body=[]; row=i; current=short(h[2].strip(),160)
            outline.append({'line':i,'level':len(h[1]),'name':current})
        else:
            body.append(line)
        ref.extend((i,'wiki',m[1]) for m in WIKI.finditer(line))
        ref.extend((i,'md',m[1]) for m in MD.finditer(line))
    if body or current or not sections:sections.append((row,current,'\n'.join(body)))
    return {'outline':outline, 'sections':sections, 'refs':ref, 'title':title, 'aliases':[short(a,160) for a in aliases[:24] if a]}


def resolve(source, kind, raw, exact, shortnames):
    target=raw.split('|',1)[0].strip() if kind=='wiki' else unquote(raw.split(' ',1)[0])
    target=target.partition('#')[0]
    if not target or target.startswith(('/', '\\','<')) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:',target):
        return None, None
    if PurePosixPath(target).suffix.lower() in ('.png','.jpeg','.jpg','.gif','.svg','.pdf','.webp','.mov','.mp4','.mp3','.wav','.canvas','.webm','.zip','.css','.json','.py','.ts','.js'):
        return None,None
    if not target.lower().endswith('.md'): target += '.md'
    if target.startswith('../') and posixpath.normpath(posixpath.join(posixpath.dirname(source),target)).startswith('../'):
        return None,'outside'
    if '/' not in target and kind=='wiki':
        # An exact vault-relative root note is stronger evidence than a basename match.
        direct=exact.get(fold(target),())
        matches=shortnames.get(fold(PurePosixPath(target).stem),())
        same_case=tuple(p for p in matches if PurePosixPath(p).name==target)
        candidates=(target,) if target in direct else same_case or matches
    else:
        primary=posixpath.normpath(posixpath.join(posixpath.dirname(source),target))
        secondary=posixpath.normpath(target)
        keys=(primary,secondary) if kind=='md' or target.startswith('.') else (secondary,primary)
        if target.startswith('.') and (primary=='..' or primary.startswith('../')):
            return None,'outside'
        candidates=next((exact[k] for k in map(fold,keys) if k in exact),())
    if len(candidates)==1:return candidates[0],None
    return None, 'ambiguous' if candidates else 'missing'


def page_text(text, line, offset, size):
    lines=text.splitlines(keepends=True)
    if not 1<=line<=max(len(lines),1) or not 0<=offset<=MAX_NOTE or not 1<=size<=MAX_CHARS:
        raise ValueError('Invalid excerpt range.')
    if not lines:
        if offset:raise ValueError('Invalid excerpt offset.')
        return '',1,None
    if offset>=len(lines[line-1]):raise ValueError('Invalid excerpt offset.')
    chunks=[]; remaining=size; end=line
    for i in range(line-1,min(len(lines),line+249)):
        s=lines[i]; start=offset if i==line-1 else 0; left=len(s)-start
        chunks.append(s[start:start+remaining]); end=i+1
        if left>remaining:
            return ''.join(chunks), end, [i+1,start+remaining]
        remaining-=left
        if not remaining:break
    return ''.join(chunks),end,[end+1,0] if end<len(lines) else None


def exact_excerpt(root,path,line=1,offset=0,chars=6000,expected=None):
    """CLI read: only the chosen file, optionally pinned to a search digest."""
    if expected is not None and not re.fullmatch(r'[0-9a-f]{64}',expected):
        raise ValueError('Use the exact lower-case SHA-256 returned by search.')
    root=Path(root).expanduser().absolute()
    if root.is_symlink():raise ValueError('Linked vault root not accepted.')
    try:raw,text,_=note(root.resolve(strict=True),path)
    except FileNotFoundError as e:raise Stale('Exact note unavailable or moved.') from e
    digest=hashlib.sha256(raw).hexdigest()
    if expected is not None and not hmac.compare_digest(digest,expected):
        raise Stale('Note no longer matches the search digest. Search again before citing it.')
    snippet,end,next_=page_text(text,line,offset,chars)
    return {'path':path,'sha256':digest,'bytes':len(raw),'start_line':line,'end_line':end,'offset':offset,'text':snippet,'more':next_ is not None,'next':next_}


def small_json(path):
    try:
        fd=os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as f:
            s=os.fstat(f.fileno()); cap=1024*1024
            if not stat.S_ISREG(s.st_mode) or s.st_size>cap:return {}
            data=f.read(cap+1)
            if len(data)>cap or signature(s)!=signature(os.fstat(f.fileno())):return {}
        x=json.loads(data); return x if isinstance(x,dict) else {}
    except (ValueError,OSError,RecursionError):return {}


class Index:
    def __init__(self, root, health=None, sync=None, expect_maps=False):
        candidate=Path(root).expanduser().absolute()
        if candidate.is_symlink() or not candidate.is_dir():raise ValueError('Accessible vault folder not found.')
        self.root=candidate.resolve(strict=True)
        self.health_file=Path(health) if health is not None else None
        self.sync_file=Path(sync) if sync is not None else None
        self.expect_maps=expect_maps
        self.lock=threading.RLock(); self.build_lock=threading.Lock();self.db=None
        self.rebuild()

    def close(self):
        with self.build_lock, self.lock:
            if self.db is not None:self.db.close();self.db=None

    def rebuild(self):
        if not self.build_lock.acquire(blocking=False):raise Stale('A refresh is already running.')
        try:
            return self._rebuild()
        finally:self.build_lock.release()

    def _rebuild(self):
        t=time.perf_counter(); inventory,errors,excluded=walk(self.root)
        db=sqlite3.connect(':memory:',check_same_thread=False)
        data={}; refs={}; exact=defaultdict(list); names=defaultdict(list); fingerprint=hashlib.sha256(); total=0; sections=0
        try:
            db.execute('PRAGMA temp_store=MEMORY'); db.execute('CREATE VIRTUAL TABLE search USING fts5(path UNINDEXED, line UNINDEXED, title, aliases, heading, body, tokenize="unicode61 remove_diacritics 2")')
            for path, sig in sorted(inventory.items()):
                try:raw,text,seen=note(self.root,path)
                except (OSError,ValueError,Stale):errors+=1;continue
                if seen!=sig:raise Stale('Vault changed during index construction; refresh again.')
                total+=len(raw)
                if total>MAX_TOTAL:raise ValueError('Index byte budget exceeded.')
                info=metadata(text); sha=hashlib.sha256(raw).hexdigest(); fingerprint.update(path.encode()+b'\0'+sha.encode()+b'\n')
                p=PurePosixPath(path); title=info['title'] or p.stem
                data[path]={'title':title,'path':path,'sha256':sha,'outline':info['outline'],'bytes':len(raw),'nonempty':bool(text.strip())}; refs[path]=info['refs']
                exact[fold(path)].append(path); names[fold(p.stem)].append(path)
                sections+=len(info['sections'])
                if sections>MAX_SECTIONS:raise ValueError('Index section budget exceeded.')
                db.executemany('INSERT INTO search VALUES (?,?,?,?,?,?)',((path,row,p.stem+' '+title,' '.join(info['aliases']),heading,body) for row,heading,body in info['sections']))
            db.commit()
            if not data:raise ValueError('No readable Markdown notes in this vault.')
            after,after_errors,after_excluded=walk(self.root)
            if after!=inventory or after_excluded!=excluded:raise Stale('Vault changed during index construction; refresh again.')
            out=defaultdict(dict); back=defaultdict(dict); problem=defaultdict(list)
            warning_counts=defaultdict(int); warning_examples=[]
            for path, links in refs.items():
                for line,kind,raw in links:
                    dst,issue=resolve(path,kind,raw,exact,names)
                    if dst and path!=dst:
                        out[path].setdefault(dst,line);back[dst].setdefault(path,line)
                    elif issue:
                        warning_counts[issue]+=1
                        detail={'line':line,'kind':issue,'target':short(raw,150)}
                        if len(problem[path])<24:problem[path].append(detail)
                        if len(warning_examples)<24:warning_examples.append({'source':path,**detail})
            roots=[r for r in ROOTS if r in data and data[r]['nonempty']]
            reached=set(roots); todo=deque(roots)
            while todo:
                for dst in out.get(todo.popleft(),{}):
                    if dst not in reached:reached.add(dst);todo.append(dst)
            graph={'edges':sum(map(len,out.values())),'detected_missing':warning_counts['missing'],'detected_ambiguous':warning_counts['ambiguous'],'detected_outside':warning_counts['outside'],'warning_examples':warning_examples,'unreachable':len(data)-len(reached) if roots else None,'missing_roots':list(r for r in ROOTS if r not in roots) if self.expect_maps else [],'scope':'Conservative wiki/Markdown note-link parser; not Obsidian anchor or plugin validation.'}
            built={'id':fingerprint.hexdigest(),'built_at':utc(),'build_ms':round((time.perf_counter()-t)*1000,1),'notes':len(data),'sections':sections,'skipped':max(errors,after_errors)+excluded,'bytes_read':total}
            with self.lock:
                old=self.db
                self.db,self.data,self.out,self.back,self.problem,self.inventory,self.graph,self.built=db,data,out,back,problem,inventory,graph,built
                self.id=built['id'];self.excluded=excluded
                if old is not None:old.close()
            db=None
            return dict(built)
        finally:
            if db is not None:db.close()

    def search(self, query, limit=8, folder=None):
        begin=time.perf_counter()
        if not isinstance(query,str) or not 1<=len(query)<=240 or not 1<=limit<=16:raise ValueError('Enter a search of up to 240 characters.')
        ordered=terms(query); tokens=list(dict.fromkeys(ordered))
        if not tokens:return {'hits':[],'mode':'no searchable terms','ms':0}
        if len(tokens)>16:raise ValueError('Use 16 or fewer distinctive words.')
        if folder is not None and (not folder or len(folder)>240 or folder.startswith(('/','.')) or any(p in ('','.','..') for p in folder.split('/'))):raise ValueError('Use a vault-relative folder.')
        joined=' '.join(ordered); query_set=set(tokens)
        def match(neg):return (' OR ' if neg else ' AND ').join('"'+w+'"' for w in tokens)
        def candidate(value):
            sql='SELECT path,line,heading,bm25(search,0,0,10,12,6,1) FROM search WHERE search MATCH ?'
            args=[value]
            if folder is not None:sql+=' AND (substr(path,1,?)=? AND instr(path,\'/\')>0)';args.extend([len(folder)+1,folder+'/'])
            return self.db.execute(sql+' ORDER BY 4 ASC, path ASC, line ASC LIMIT 320',args).fetchall()
        with self.lock:
            rows=candidate(match(False)); mode='all terms' if rows else 'some terms'
            if not rows and len(tokens)>1:rows=candidate(match(True))
            best={}
            for path,line,heading,weight in rows:
                p=PurePosixPath(path); stem=terms(p.stem); structural=query_set & set(stem+terms(self.data[path]['title'])); h=query_set & set(terms(heading));
                score=-weight + 3*len(structural)+1.5*len(h)+35*(joined==' '.join(stem))-5*(path=='INDEX.md' and joined!='index')
                row={'path':path,'title':self.data[path]['title'],'line':int(line),'heading':heading,'score':round(score,3),'sha256':self.data[path]['sha256']}
                if path not in best or row['score']>best[path]['score']:best[path]=row
            hits=sorted(best.values(),key=lambda r:(-r['score'],fold(r['path']),r['path']))[:limit]; generation=self.id
        return {'hits':hits,'mode':mode,'ms':round((time.perf_counter()-begin)*1000,2),'generation':generation}

    def _verified(self, path):
        parts_for(path)
        if path not in self.data:raise KeyError('Exact path not in this index.')
        obj=self.data[path]
        try:raw,text,_=note(self.root,path)
        except (OSError,ValueError) as e:raise Stale('Note unavailable or moved. Refresh the index.') from e
        if hashlib.sha256(raw).hexdigest()!=obj['sha256']:
            raise Stale('Note changed. Refresh the index before citing it.')
        return obj,text

    def read(self,path,line=1,offset=0,chars=6000):
        with self.lock:
            obj,text=self._verified(path)
            excerpt,end,next_=page_text(text,line,offset,chars)
            ancestors=[x['name'] for x in obj['outline'] if x['line']<=line]
            def edges(values):return [{'path':p,'title':self.data[p]['title'],'line':n} for p,n in list(sorted(values.items(), key=lambda x:(x[1],x[0])))[:32]]
            out=self.out.get(path,{}); back=self.back.get(path,{})
            return {**obj,'outline':obj['outline'][:180],'outline_count':len(obj['outline']),'heading':ancestors[-1] if ancestors else '', 'start_line':line,'end_line':end,'offset':offset,'text':excerpt,'more':next_ is not None,'next':next_,'outlinks':edges(out),'backlinks':edges(back),'outlink_count':len(out),'backlink_count':len(back),'unresolved':self.problem.get(path,[])[:24],'generation':self.id}

    def path(self,start,end,hops=5):
        if not 1<=hops<=8:raise ValueError('Hop count must be 1–8.')
        with self.lock:
            current,errors,excluded=walk(self.root)
            if current!=self.inventory or errors or excluded!=self.excluded or self.built['skipped']:
                raise Stale('Index changed or has exclusions. Refresh and check health before tracing pathways.')
            self._verified(start)
            if start!=end:self._verified(end)
            seen={start}; work=deque([[start]])
            while work:
                seq=work.popleft(); node=seq[-1]
                if node==end:
                    for p in seq[1:-1]:self._verified(p)
                    return {'path':seq,'hops':len(seq)-1,'edges':[{'source':a,'target':b,'line':self.out[a][b]} for a,b in zip(seq,seq[1:])],'generation':self.id}
                if len(seq)>hops:continue
                for link in sorted(self.out.get(node,{})):
                    if link not in seen:seen.add(link);work.append(seq+[link])

            return {'path':[],'hops':None,'edges':[],'generation':self.id,'scope':f'No directed route within {hops} indexed hops.'}

    def health(self):
        configured=self.health_file is not None and self.sync_file is not None
        a,b=(small_json(self.health_file),small_json(self.sync_file)) if configured else ({},{})
        same=bool(isinstance(a.get('runId'),str) and a['runId'] and a['runId']==b.get('runId'))
        current, errors, excluded=walk(self.root)
        status=a.get('status'); status='not configured' if not configured else status if status in ('green','degraded','blocked') else 'unknown'
        stamp=a.get('checkedAtLocal') or a.get('checkedAt'); age=None
        if isinstance(stamp,str) and len(stamp)<64:
            try:age=round((datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))).total_seconds())
            except (ValueError,TypeError):pass
        entries=b.get('files'); complete=isinstance(entries,list) and all(isinstance(x,dict) and isinstance(x.get('action'),str) for x in entries)
        pending=sum(x['action']=='would-sync' for x in entries) if same and complete else None
        with self.lock:
            drift=bool(current!=self.inventory or errors or excluded!=self.excluded)
            return {'index':{**self.built,'drift':drift,'scan_errors':errors,'filesystem_exclusions':excluded},'graph':dict(self.graph),'maintenance':{'source':'recorded local gate receipts, not a fresh sync','status':status,'same_run':same,'pending':pending,'checked_at':stamp if age is not None else None,'age_seconds':age,'stale':age is None or age<0 or age>86400,'mode':b.get('mode') if same and b.get('mode') in ('apply','dry-run') else None},'roots':[{'title':self.data[r]['title'],'path':r} for r in ROOTS if r in self.data]}


class LocalServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self, port, handler):
        self.slots=threading.BoundedSemaphore(12)
        super().__init__(('127.0.0.1',port),handler)
    def process_request(self,socket,address):
        if not self.slots.acquire(blocking=False):self.shutdown_request(socket);return
        try:super().process_request(socket,address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,*args):
        try:super().process_request_thread(*args)
        finally:self.slots.release()
    def get_request(self):
        sock,address=super().get_request();sock.settimeout(8);return sock,address


def make_server(index, port=0, key=None):
    key=key or secrets.token_urlsafe(32)
    files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
    static={path:((ASSETS/name).read_bytes(),ctype) for path,(name,ctype) in files.items()}
    class Handler(BaseHTTPRequestHandler):
        server_version='NeuralWorkbench'
        sys_version=''
        def log_message(self,*args):pass
        def reply(self, code, data, mime='application/json; charset=utf-8'):
            if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=True,separators=(',',':')).encode()
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cross-Origin-Resource-Policy','same-origin')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers();self.wfile.write(data)
        def send_error(self, code, message=None, explain=None):
            self.reply(code,{'error':'request rejected'})
        def dispatch(self):
            host=f'127.0.0.1:{self.server.server_port}'; origin='http://'+host
            if self.headers.get_all('Host')!=[host] or self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none') or self.headers.get_all('Origin',[]) not in ([],[origin]):
                return self.reply(403,{'error':'local origin required'})
            if len(self.path)>7000 or not self.path.startswith('/') or self.path.startswith('//'):
                return self.reply(400,{'error':'invalid address'})
            route=urlsplit(self.path); method=self.command
            if method=='GET' and route.path in static:
                return self.reply(200,*static[route.path])
            token=self.headers.get('X-Workbench-Token','')
            if not token.isascii() or not hmac.compare_digest(token,key) or (method=='POST' and self.headers.get('Origin')!=origin):
                return self.reply(403,{'error':'open the private launcher to unlock this session'})
            try:
                q=parse_qs(route.query,keep_blank_values=True,max_num_fields=8)
                if any(len(v)!=1 for v in q.values()):raise ValueError()
                v=lambda name,default=None:q.get(name,[default])[0]
                if method=='GET' and route.path=='/api/health':data=index.health()
                elif method=='GET' and route.path=='/api/search':data=index.search(v('q',''),int(v('limit','8')),v('folder') or None)
                elif method=='GET' and route.path=='/api/read':data=index.read(v('path'),int(v('line','1')),int(v('offset','0')),int(v('chars','6000')))
                elif method=='GET' and route.path=='/api/path':data=index.path(v('from'),v('to'),int(v('hops','5')))
                elif method=='POST' and route.path=='/api/refresh':
                    if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length','0')!='0':return self.reply(400,{'error':'body not accepted'})
                    data=index.rebuild()
                else:return self.reply(404,{'error':'not found'})
                self.reply(200,data)
            except Stale as e:self.reply(409,{'error':str(e)})
            except KeyError:self.reply(404,{'error':'note not indexed'})
            except (ValueError,TypeError):self.reply(400,{'error':'invalid input or index limit exceeded'})
            except (OSError,sqlite3.Error):self.reply(503,{'error':'local source currently unavailable'})
        do_GET=dispatch
        do_POST=dispatch
    server=LocalServer(port,Handler);server.private_url=f'http://127.0.0.1:{server.server_port}/#'+key
    return server


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--vault',type=Path,required=True,help='Only index this explicit Markdown folder.');p.add_argument('--expect-maps',action='store_true',help='Flag missing starter-style maps.')
    sub=p.add_subparsers(dest='mode',required=True)
    q=sub.add_parser('search');q.add_argument('query');q.add_argument('--limit',type=int,default=8);q.add_argument('--folder')
    r=sub.add_parser('read');r.add_argument('path');r.add_argument('--line',type=int,default=1);r.add_argument('--offset',type=int,default=0);r.add_argument('--chars',type=int,default=6000);r.add_argument('--sha256',help='Require the exact digest returned by search')
    g=sub.add_parser('path');g.add_argument('start');g.add_argument('end');g.add_argument('--hops',type=int,default=5)
    sub.add_parser('health')
    sub.add_parser('serve')
    args=p.parse_args(argv); ix=None
    try:
        if args.mode=='read':
            out=exact_excerpt(args.vault,args.path,args.line,args.offset,args.chars,args.sha256)
            print(json.dumps(out,ensure_ascii=False,indent=2));return 0
        ix=Index(args.vault,expect_maps=args.expect_maps)
        if args.mode=='serve':
            with make_server(ix) as server:
                print(server.private_url,flush=True)
                try:server.serve_forever()
                except KeyboardInterrupt:pass
            return 0
        if args.mode=='search':out=ix.search(args.query,args.limit,args.folder)
        elif args.mode=='path':out=ix.path(args.start,args.end,args.hops)
        else:out=ix.health()
        print(json.dumps(out,ensure_ascii=False,indent=2));return 0
    except (ValueError,KeyError,OSError,Stale,sqlite3.Error) as e:
        print(f'workbench: {e}',file=sys.stderr);return 2
    finally:
        if ix is not None:ix.close()

if __name__=='__main__':raise SystemExit(main())
