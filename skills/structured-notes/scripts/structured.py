"""Deterministic export and domain checks for evidence-backed science artifacts."""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

from core import load, save, canonical, delta

def ids(records):
    result={}
    for rec in records:
        k=rec['id']
        if not isinstance(k,str) or not k.strip() or k in result:
            raise ValueError(f'missing or duplicate id: {k!r}')
        result[k]=rec
    return result

def require(rec,*keys):
    for k in keys:
        if not rec.get(k):
            raise ValueError(f"{rec.get('id','?')}: missing {k}")

def translation(data):
    segments=ids(data['segments'])
    aligned=ids(data['aligned'])
    glossary=data.get('glossary',[])
    issues=[]
    for k,r in segments.items():
        if k not in aligned:
            issues.append({'id':k,'issue':'not_translated'})
            continue
        src=canonical(r['source'])
        dst=canonical(aligned[k]['target'])
        if not dst: issues.append({'id':k,'issue':'empty_target'})
        diff=delta(src,dst)
        if any(diff.values()):
            issues.append({'id':k,'issue':'protected_token_difference','delta':diff})
        for t in glossary:
            source,target=t['source'],t['target']
            if not source or not target: continue
            pat=(r'(?<!\w)'+re.escape(source)+r'(?!\w)' if source.isascii() else re.escape(source))
            target_pat=(r'(?<!\w)'+re.escape(target)+r'(?!\w)' if target.isascii() else re.escape(target))
            if re.search(pat,src,re.I) and not re.search(target_pat,dst,re.I):
                issues.append({'id':k,'issue':'glossary_review','source_term':source,'target_term':target})
    issues.extend({'id':k,'issue':'unknown_source_id'} for k in aligned.keys()-segments.keys())
    return {'expected':len(segments),'aligned':len(aligned.keys()&segments.keys()),'issues':issues}

def notes(data):
    index=ids(data['sections'])
    notes=ids(data['notes'])
    reported=defaultdict(list)
    for k,n in notes.items():
        require(n,'section_id','category','claim','evidence')
        for s in n['evidence']:
            require(s,'file','locator','quote')
        if n['section_id'] not in index:
            raise ValueError(f'unknown section for {k}')
        reported[n['category']].append(k)
    requested=data.get('required_categories',[])
    return {'section_order':list(index),'category_index':dict(reported),
            'uncovered':[k for k in requested if k not in reported],
            'unnoted_sections':[k for k in index if all(n['section_id']!=k for n in notes.values())]}

def urikey(*parts):
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:32]

def nanopub(data, out):
    from rdflib import Dataset, Namespace, URIRef, Literal, RDF
    from rdflib.namespace import DCTERMS, PROV
    np=Namespace('http://www.nanopub.org/nschema#')
    ev=Namespace('https://w3id.org/scholarly-evidence-local/v1#')
    ds=Dataset()
    for prefix,ns in [('np',np),('ev',ev),('prov',PROV),('dct',DCTERMS)]:
        ds.bind(prefix,ns)
    records=ids(data['claims'])
    if not records:
        raise ValueError('no supported claims to serialize')
    manifest={}
    for key,c in records.items():
        require(c,'statement','evidence')
        source=c['evidence']
        require(source,'file','locator','quote')
        doc_hash=urikey(data.get('scope_id','local'),source['file'],source.get('doi',''),source.get('version',''))
        stem=URIRef('urn:scholarly-evidence:nanopub:'+urikey(doc_hash,key,c['statement'],source['locator'],source['quote']))
        uri=lambda x: URIRef(str(stem)+':'+x)
        head,ass,prv,pub=(uri(x) for x in ('head','assertion','provenance','pubinfo'))
        item,passage,paper=(uri('claim'),uri('passage'),URIRef('urn:scholarly-evidence:source:'+doc_hash))
        h,a,p,u=(ds.graph(g) for g in (head,ass,prv,pub))
        for pred,obj in [(RDF.type,np.Nanopublication),(np.hasAssertion,ass),(np.hasProvenance,prv),(np.hasPublicationInfo,pub)]:
            h.add((stem,pred,obj))
        a.add((item,RDF.type,ev.Assertion))
        a.add((item,RDF.value,Literal(c['statement'])))
        for tag in ('qualifiers','scope','evidence_strength'):
            if c.get(tag): a.add((item,ev[tag],Literal(json.dumps(c[tag],ensure_ascii=False) if not isinstance(c[tag],str) else c[tag])))
        p.add((ass,PROV.wasDerivedFrom,passage))
        p.add((passage,PROV.wasDerivedFrom,paper))
        p.add((passage,RDF.value,Literal(source['quote'])))
        p.add((passage,ev.locator,Literal(source['locator'])))
        p.add((paper,ev.inputFile,Literal(source['file'])))
        for key2,pred in [('title',DCTERMS.title),('year',DCTERMS.issued),('version',ev.version),('doi',DCTERMS.identifier)]:
            if source.get(key2): p.add((paper,pred,Literal(source[key2])))
        for author in source.get('authors',[]): p.add((paper,DCTERMS.creator,Literal(author)))
        u.add((stem,ev.publicationStatus,Literal('unsigned local artifact')))
        u.add((stem,ev.generator,Literal('scholarly-evidence-nanopub/1.0')))
        if data.get('created'): u.add((stem,DCTERMS.created,Literal(data['created'])))
        if data.get('creator'): u.add((stem,DCTERMS.creator,Literal(data['creator'])))
        manifest[key]={'nanopub':str(stem),'head':str(head),'assertion':str(ass),'provenance':str(prv),'pubinfo':str(pub)}
    payload=ds.serialize(format='trig')
    reopened=Dataset()
    reopened.parse(data=payload,format='trig')
    named={str(g) for _,_,_,g in reopened.quads((None,None,None,None))}
    expected={row[k] for row in manifest.values() for k in ('head','assertion','provenance','pubinfo')}
    if named!=expected or len(ds)!=len(reopened):
        raise ValueError('TriG roundtrip mismatch')
    out.write_text(payload,encoding='utf-8')
    return {'nanopublications':manifest,'named_graphs':len(named),'artifact':out.name,'published':False}

def graph(data):
    nodes,edges=ids(data['nodes']),ids(data['edges'])
    adjacency={k:{'in':[],'out':[]} for k in nodes}
    labels=defaultdict(set)
    for k,n in nodes.items():
        require(n,'label','type','evidence')
        if not isinstance(n['evidence'],list): raise ValueError(f'{k}: evidence must be a list')
        for e in n['evidence']: require(e,'file','locator','quote')
        for alias in [n['label'],*n.get('aliases',[])]:
            labels[canonical(alias).casefold()].add(k)
    for k,e in edges.items():
        require(e,'source','target','predicate','evidence')
        if not isinstance(e['evidence'],list): raise ValueError(f'{k}: evidence must be a list')
        for s in e['evidence']: require(s,'file','locator','quote')
        if e['source'] not in nodes or e['target'] not in nodes:
            raise ValueError(f'{k}: dangling endpoint')
        adjacency[e['source']]['out'].append(k)
        adjacency[e['target']]['in'].append(k)
    return {'nodes':list(nodes.values()),'edges':list(edges.values()),'adjacency':adjacency,
            'alias_index':{k:sorted(v) for k,v in labels.items()},
            'ambiguous_aliases':{k:sorted(v) for k,v in labels.items() if len(v)>1}}

def normdoi(raw):
    d=unquote(re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)','',raw.strip(),flags=re.I))
    return d.casefold() if re.fullmatch(r'10\.\d{4,9}/\S+',d) else ''

def bib(path):
    from pybtex.database import parse_file
    db=parse_file(str(path),bib_format='bibtex')
    by_doi=defaultdict(list)
    entries=[]
    for k,e in db.entries.items():
        record={'id':k,'type':e.type,'fields':dict(e.fields),'persons':{r:[str(x) for x in people] for r,people in e.persons.items()}}
        doi=normdoi(e.fields.get('doi',''))
        if doi: by_doi[doi].append(k)
        entries.append(record)
    return {'entries':entries,'same_doi_candidates':{k:v for k,v in by_doi.items() if len(v)>1}}

def citations(data):
    """Map ordered authored citation clusters using stable reference keys, never by blind regex."""
    refs=ids(data['references'])
    remap=data.get('confirmed_aliases',{})
    if any(k not in refs or v not in refs or k==v or v in remap for k,v in remap.items()):
        raise ValueError('alias map must be direct and use existing reference IDs')
    numbers={}
    out=[]
    for k,cluster in ids(data['clusters']).items():
        seq=[]
        for raw in cluster['reference_ids']:
            ref=remap.get(raw,raw)
            if ref not in refs: raise ValueError(f'{k}: unknown reference {ref}')
            if ref not in numbers: numbers[ref]=len(numbers)+1
            if numbers[ref] not in seq: seq.append(numbers[ref])
        out.append({'id':k,'source_keys':cluster['reference_ids'],'numbers':sorted(seq)})
    return {'clusters':out,'bibliography':[dict(refs[k],number=n) for k,n in numbers.items()],
            'unused_canonical_ids':[k for k in refs if k not in remap and k not in numbers]}

def reconcile(data):
    """Flag differing values without resolving experiment identity or source authority."""
    slots=defaultdict(list)
    for f in data['facts']:
        require(f,'run','parameter','file','locator')
        if 'value' not in f: raise ValueError('value field missing')
        slots[(f['run'],f['parameter'])].append(f)
    disagreements=[]
    for (run,param),facts in slots.items():
        if len({(json.dumps(f['value'],sort_keys=True,ensure_ascii=False),f.get('unit','')) for f in facts})>1:
            disagreements.append({'run':run,'parameter':param,'observations':facts})
    return {'facts':data['facts'],'groups':len(slots),'review_conflicts':disagreements}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['translation','notes','nanopub','graph','bib','citations','reconcile'])
    p.add_argument('input',type=Path); p.add_argument('output',type=Path)
    p.add_argument('--trig',type=Path)
    a=p.parse_args()
    if a.mode=='bib':
        result=bib(a.input)
    else:
        data=load(a.input)
        if a.mode=='nanopub':
            if not a.trig: p.error('nanopub requires --trig')
            a.trig.parent.mkdir(parents=True,exist_ok=True)
            result=nanopub(data,a.trig)
        else:
            result=globals()[a.mode](data)
    save(a.output,result)

if __name__=='__main__':
    sys.exit(main())
