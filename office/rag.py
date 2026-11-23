import hashlib
import html
import json
import math
import re
from urllib.parse import urlencode, quote
from .models import fetch_json
from .store import now,pack

def normalized(s):
    return ' '.join(s.split()).casefold()

def ingest(c, scope, title, body, origin='internal', locator=''):
    body=body.strip(); title=title.strip()[:160]
    if not title or not body: raise ValueError('Cần tên và nội dung tài liệu.')
    if len(body)>300_000: raise ValueError('Tài liệu vượt 300.000 ký tự.')
    locator=(locator or title.casefold())[:900]
    digest=hashlib.sha256(body.encode()).hexdigest()
    old=c.execute('SELECT id,version,hash FROM sources WHERE scope=? AND locator=? AND active=1',(scope,locator)).fetchone()
    if old and old['hash']==digest: return old['id']
    if old: c.execute('UPDATE sources SET active=0 WHERE id=?',(old['id'],))
    ident=c.execute('INSERT INTO sources(scope,title,origin,locator,hash,version,active,created) VALUES(?,?,?,?,?,?,1,?)',
                 (scope,title,origin,locator,digest,old['version']+1 if old else 1,now())).lastrowid
    words=re.findall(r'\S+',body); segments=[]; buf=[]; size=0
    for word in words:
        for i in range(0,len(word),650):
            part=word[i:i+650]
            if size+len(part)>750 and buf:
                segments.append(' '.join(buf));buf=buf[-10:] if len(buf)>14 else [];size=sum(len(w)+1 for w in buf)
            buf.append(part);size+=len(part)+1
    if buf: segments.append(' '.join(buf))
    for n,part in enumerate(segments):
        idx=c.execute('INSERT INTO chunks(source,seq,body) VALUES(?,?,?)',(ident,n,part)).lastrowid
        c.execute('INSERT INTO search(rowid,body) VALUES(?,?)',(idx,title+' '+part))
    return ident

def search(store, case_id, query, models=None, limit=4):
    words=list(dict.fromkeys(w for w in re.findall(r'[^\W_]+',query.casefold()) if len(w)>1))[:32]
    with store.db() as c:
        store.case(c,case_id)
        lexical=[]
        if words:
            lexical=[dict(r) for r in c.execute("""SELECT k.id,k.body,s.title,s.scope FROM search
                JOIN chunks k ON k.id=search.rowid JOIN sources s ON s.id=k.source
                WHERE search MATCH ? AND s.scope IN ('company',?) AND s.active=1 ORDER BY rank,k.id LIMIT 40""",
                (' OR '.join('"'+w+'"' for w in words),case_id))]
        if not models or not models.embed_model:
            return lexical[:limit],''
        pool=[dict(r) for r in c.execute("""SELECT k.id,k.body,s.title,s.scope FROM chunks k JOIN sources s ON k.source=s.id
                       WHERE s.scope IN ('company',?) AND s.active=1 ORDER BY k.id""",(case_id,))]
    ranks={r['id']:1/(60+n) for n,r in enumerate(lexical,1)}
    note=''
    if models and models.embed_model and pool:
        try:
            with store.db() as c:
                vecs={r[0]:json.loads(r[1]) for r in c.execute("""SELECT v.chunk,v.vector FROM vectors v JOIN chunks k ON k.id=v.chunk
                    JOIN sources s ON s.id=k.source WHERE v.model=? AND s.scope IN ('company',?) AND s.active=1""",(models.embed_model,case_id))}
            missing=[r for r in pool if r['id'] not in vecs][:24]
            vectors=models.embed([query[:750]]+[r['body'] for r in missing])
            with store.db(True) as c:
                for r,v in zip(missing,vectors[1:]):
                    c.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?)',(r['id'],models.embed_model,pack(v)));vecs[r['id']]=v
            q=vectors[0]
            def cosine(v):
                if len(q)!=len(v): return -1
                return sum(a*b for a,b in zip(q,v))/max(1e-12,math.sqrt(sum(a*a for a in q)*sum(b*b for b in v)))
            semantic=sorted(((cosine(v),i) for i,v in vecs.items()), reverse=True)
            for n,(score,i) in enumerate(semantic[:40],1):
                if score>0: ranks[i]=ranks.get(i,0)+1/(60+n)
        except (ValueError,RuntimeError) as exc:
            note='Embedding tạm lỗi, dùng BM25. '+str(exc)[:180]
    selected=sorted((r for r in pool if r['id'] in ranks), key=lambda r:-ranks[r['id']])[:limit]
    return selected,note

def clean_markup(text):
    return ' '.join(html.unescape(re.sub(r'<[^>]*>',' ',text or '')).split())

def public_collect(queries):
    docs=[];warnings=[]
    for query in queries[:2]:
        for lang in ('vi','en'):
            try:
                params={'action':'query','format':'json','formatversion':2,'generator':'search','gsrsearch':query,'gsrlimit':2,
                        'gsrnamespace':0,'prop':'extracts|info','inprop':'url','exintro':1,'explaintext':1,'exchars':2400}
                url=f'https://{lang}.wikipedia.org/w/api.php?'+urlencode(params)
                data=fetch_json(url,timeout=8)
                if 'error' in data: raise ValueError(str(data['error']))
                for page in data.get('query',{}).get('pages',[]):
                    body=page.get('extract','').strip()
                    if len(body)>65:
                        docs.append((page['title'],body,'Wikipedia '+lang,page.get('fullurl') or f'https://{lang}.wikipedia.org/?curid='+str(page['pageid'])))
            except (ValueError,RuntimeError) as exc: warnings.append(f'Wikipedia {lang}: {exc}')
        try:
            url='https://api.crossref.org/works?'+urlencode({'query.bibliographic':query,'rows':3,'select':'title,abstract,DOI,URL'})
            data=fetch_json(url,timeout=8)
            for item in data.get('message',{}).get('items',[]):
                abstract=clean_markup(item.get('abstract',''))
                if abstract and item.get('title') and item.get('DOI'):
                    docs.append((item['title'][0],abstract[:6000],'Crossref · abstract','https://doi.org/'+quote(item['DOI'],safe='/')))
        except (ValueError,RuntimeError) as exc: warnings.append('Crossref: '+str(exc))
    return list({d[3]:d for d in docs}.values()),warnings
