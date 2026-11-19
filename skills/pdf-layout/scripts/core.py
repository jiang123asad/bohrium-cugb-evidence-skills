"""Local evidence index and result contract checks; Python 3.10+, standard library."""
import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

TEXT = {'.txt','.md','.markdown','.json','.jsonl','.csv','.tsv','.yaml','.yml',
        '.tex','.bib','.ris','.py','.r','.toml','.ini','.log','.xml','.html','.sh',
        '.cfg','.conf','.rst','.m','.ipynb','.enw','.c','.cpp','.js','.jl','.xlsx','.docx'}

def read(path):
    return path.read_text(encoding='utf-8-sig')

def reject_number(value):
    raise ValueError(f'invalid JSON number: {value}')

def unique_object(pairs):
    obj={}
    for key,value in pairs:
        if key in obj:
            raise ValueError(f'duplicate JSON key: {key}')
        obj[key]=value
    return obj

def load(path):
    return json.loads(read(path), parse_constant=reject_number, object_pairs_hook=unique_object)

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    tmp.replace(path)

def inventory(root):
    return [{'file':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'ext':p.suffix.lower()}
            for p in sorted(root.rglob('*')) if p.is_file() and p.resolve().is_relative_to(root.resolve())]

def canonical(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC',text)).strip()

def rows(root):
    for item in inventory(root):
        p = root / item['file']
        if p.suffix.lower() not in TEXT - {'.xlsx','.docx','.ipynb'} or item['bytes'] > 12_000_000:
            continue
        if item['file'] in ('case.json','task.md'):
            continue
        try:
            data=read(p)
        except (OSError, UnicodeError):
            continue
        if p.suffix.lower() in {'.csv','.tsv'}:
            rr=csv.reader(data.splitlines(keepends=True), delimiter='\t' if p.suffix.lower()=='.tsv' else ',')
            prev=0
            for cells in rr:
                start=prev+1
                prev=rr.line_num
                yield {'file':item['file'],'line':start,'end':prev,'text':' | '.join(cells)}
        else:
            lines=data.splitlines()
            for i,line in enumerate(lines):
                if line.strip():
                    yield {'file':item['file'],'line':i+1,'end':i+1,'text':line}

def search(root, query, limit):
    q=canonical(query).casefold()
    ranked=[]
    for row in rows(root):
        t=canonical(row['text']).casefold()
        terms=[x for x in re.findall(r'[A-Za-z0-9]+|[\u3400-\u9fff]+',q) if len(x)>1]
        count=sum(x in t for x in terms)
        score=(10+count if q and q in t else count)
        if score:
            ranked.append((score,row))
    ranked.sort(key=lambda x:(-x[0],len(x[1]['text']),x[1]['file'], x[1]['line']))
    return [x[1] for x in ranked[:limit]]

def quotes(root, path):
    out=[]
    for i,r in enumerate(load(path)):
        try:
            p=(root/r['file']).resolve()
            a,b=r['start'],r['end']
            assert p.is_relative_to(root.resolve()) and type(a)==type(b)==int and 1<=a<=b
            lines=read(p).splitlines()
            assert b<=len(lines)
            quote=canonical(r['quote'])
            ok=bool(quote) and quote in canonical(' '.join(lines[a-1:b]))
            out.append({'index':i,'verified':ok,'reason':None if ok else 'quote_not_in_range'})
        except (OSError,ValueError,AssertionError,TypeError,KeyError) as e:
            out.append({'index':i,'verified':False,'reason':type(e).__name__})
    return out

def contract_fields(contract):
    if isinstance(contract,list):
        return {r if isinstance(r,str) else (r.get('name') or r.get('field') or r.get('key'))
                for r in contract if isinstance(r,(str,dict))} - {None}
    if isinstance(contract,dict):
        for key in ('required','required_fields','fields','output_fields'):
            if isinstance(contract.get(key),(dict,list)):
                return contract_fields(contract[key])
        if 'properties' in contract and isinstance(contract['properties'],dict):
            return set(contract['properties'])
        return set(contract)-{'type','description','$schema','additionalProperties',
                               'required_artifacts','artifacts','notes','files','format','output_path'}
    return set()

def check(case, result):
    if not isinstance(case,dict) or not isinstance(result,dict):
        return ['case and result must be JSON objects']
    issues=[]
    if 'case_id' not in result or ('case_id' in case and case['case_id']!=result['case_id']):
        issues.append('case_id missing or differs from case.json')
    contract=case.get('output_contract',{})
    props=contract.get('properties',{}) if isinstance(contract,dict) else {}
    checks={'object':lambda x:isinstance(x,dict),'array':lambda x:isinstance(x,list),
            'number':lambda x:type(x) in (int,float),'integer':lambda x:type(x)==int,
            'boolean':lambda x:type(x)==bool,'null':lambda x:x is None,'string':lambda x:isinstance(x,str)}
    for k in sorted(x for x in contract_fields(contract) if isinstance(x,str)):
        if k not in result:
            issues.append(f'missing field: {k}')
        elif isinstance(props.get(k),dict):
            s=props[k]
            ts=s.get('type',[]); ts=[ts] if isinstance(ts,str) else ts
            if ts and all(t in checks for t in ts) and not any(checks[t](result[k]) for t in ts):
                issues.append(f'{k}: expected {ts}')
            if 'enum' in s and result[k] not in s['enum']:
                issues.append(f'{k}: value outside allowed enum')
    return issues

def protected(text):
    """Diagnostics only: scientific tokens; identifiers can be intentionally renumbered."""
    text=unicodedata.normalize('NFC',text)
    pattern=r'\$\$[^$]+\$\$|(?<!\$)\$[^$\n]+\$|\\\([^\n]*?\\\)|\b(?:Fig(?:ure)?|Tab(?:le)?|Eq(?:uation)?)\.?\s*\(?\d+[A-Za-z]?\)?|\[\d+(?:\s*[,–-]\s*\d+)*\]|(?<![\w.])(?<!\d)[+−-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+−-]?\d+)?(?:\s?(?:%|°[CF]|‰|±))?'
    # CJK prose commonly has no space between the word and an Arabic numeral.
    text=re.sub(r'(?<=[\u3400-\u9fff])(?=[+−-]?\d)', ' ', text)
    return Counter(re.sub(r'\s+', ' ', m.group()).strip().replace('−','-') for m in re.finditer(pattern,text))

def delta(before, after):
    a,b=protected(before),protected(after)
    return {'missing':dict(a-b),'added':dict(b-a)}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest='cmd',required=True)
    i=subs.add_parser('inventory'); i.add_argument('root',type=Path)
    s=subs.add_parser('search'); s.add_argument('root',type=Path); s.add_argument('query'); s.add_argument('--limit',type=int,default=20)
    q=subs.add_parser('quotes'); q.add_argument('root',type=Path); q.add_argument('claims',type=Path)
    c=subs.add_parser('check'); c.add_argument('case',type=Path); c.add_argument('result',type=Path)
    d=subs.add_parser('compare'); d.add_argument('before',type=Path); d.add_argument('after',type=Path)
    a=p.parse_args()
    if a.cmd=='inventory': data=inventory(a.root)
    elif a.cmd=='search': data=search(a.root,a.query,a.limit)
    elif a.cmd=='quotes': data=quotes(a.root,a.claims)
    elif a.cmd=='compare': data=delta(read(a.before),read(a.after))
    else:
        issues=check(load(a.case),load(a.result))
        print(json.dumps({'ok':not issues,'issues':issues},ensure_ascii=False))
        return bool(issues)
    print(json.dumps(data,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
