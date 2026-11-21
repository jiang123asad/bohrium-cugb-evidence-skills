"""Local evidence checks; Python 3.10+, optional jsonschema for full contract validation."""
import argparse
import csv
import hashlib
import json
import math
import re
import sys
import unicodedata
import zipfile
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

def finite_float(value):
    number=float(value)
    if not math.isfinite(number):
        raise ValueError(f'non-finite JSON number: {value}')
    return number

def load(path):
    return json.loads(read(path), parse_constant=reject_number, parse_float=finite_float,
                      object_pairs_hook=unique_object)

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

def literal_match(quote, excerpt):
    q, text = canonical(quote), canonical(excerpt)
    if not q:
        return False
    # Prevent a short quote like "5 mg" from verifying against "15 mg" or "3.5 mg".
    for match in re.finditer(re.escape(q), text):
        a, b = match.span()
        if q[0].isdigit() and a and (text[a-1].isdigit() or text[a-1] in '+−-'
                                   or (text[a-1]=='.' and a>1 and text[a-2].isdigit())):
            continue
        if q[-1].isdigit() and b<len(text) and (text[b].isdigit()
                or (text[b] in '.eE' and re.match(r'[.eE][+−-]?\d', text[b:]))):
            continue
        if q[-1].isascii() and q[-1].isalpha() and b<len(text) and text[b].isascii() and text[b].isalnum():
            continue
        if q[0].isascii() and q[0].isalpha() and a and text[a-1].isascii() and text[a-1].isalpha():
            continue
        return True
    return False

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

def relative_file(root, name):
    if not isinstance(name,str) or not name.strip() or '\\' in name or ':' in name:
        raise ValueError('use a relative forward-slash file path')
    partial=Path(name)
    if partial.is_absolute() or '..' in partial.parts:
        raise ValueError('use a path within the specified root')
    p=(root/partial).resolve()
    if not p.is_relative_to(root.resolve()) or not p.is_file():
        raise ValueError('not a regular file within the specified root')
    return p

def file_sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()

def layout_excerpt(p, r, indexes, digests):
    page=r['page']
    element=r['element_id']
    if type(page)!=int or page<1 or not isinstance(element,str):
        raise ValueError('provide a physical page number and element ID')
    if p not in digests:
        digests[p]=file_sha256(p)
    if p.suffix.lower()=='.pdf':
        matching=[idx for idx in indexes if idx.get('source_sha256')==digests[p]
                  and idx.get('backend','').startswith('pdfplumber ')]
        basis='pdf_extracted_text'
    elif p.suffix.lower()=='.json':
        matching=[idx for idx in indexes if idx.get('input_sha256')==digests[p]
                  and idx.get('backend')=='docling-json (stdlib)' and idx.get('origin_verified') is False]
        basis='docling_export_text_only'
    else:
        raise ValueError('page and element anchors require a PDF or Docling export')
    if not matching:
        raise ValueError('no compatible layout index matches the cited source bytes')
    candidates=[]
    for idx in matching:
        for row in idx['pages']:
            if row['pdf_page']!=page:
                continue
            candidates.extend(b['text'] for b in row['blocks'] if b['id']==element
                              and b.get('mapping_status')!='location_only'
                              and 'row' not in r and 'column' not in r)
            for table in row['tables']:
                if table['id']!=element:
                    continue
                a,b=r.get('row'),r.get('column')
                if (type(a)!=int or type(b)!=int or not 1<=a<=len(table['rows'])
                        or not 1<=b<=len(table['rows'][a-1])):
                    raise ValueError('table evidence needs valid one-based row and column')
                candidates.append(table['rows'][a-1][b-1])
    excerpts={canonical(t or '') for t in candidates}
    if len(excerpts)!=1:
        raise ValueError('element is missing or its text differs across indexes')
    return excerpts.pop(),basis

def quotes(root, path, indexes=()):
    out=[]
    digests={}
    for i,r in enumerate(load(path)):
        try:
            p=relative_file(root,r['file'])
            basis='source_lines'
            if p.suffix.lower()=='.pdf' or 'element_id' in r:
                excerpt,basis=layout_excerpt(p,r,indexes,digests)
            else:
                a,b=r['start'],r['end']
                if type(a)!=int or type(b)!=int or not 1<=a<=b:
                    raise ValueError('invalid line range')
                lines=read(p).splitlines()
                if b>len(lines):
                    raise ValueError('line range exceeds source')
                excerpt=canonical(' '.join(lines[a-1:b]))
            ok=literal_match(r['quote'],excerpt)
            out.append({'index':i,'verified':ok,'reason':None if ok else 'quote_not_in_range','basis':basis})
        except (OSError,ValueError,TypeError,KeyError,AttributeError) as e:
            out.append({'index':i,'verified':False,'reason':type(e).__name__,'detail':str(e)})
    return out

def check_artifacts(root, names):
    """Check explicitly required files, independent of the result's shape."""
    checked=[]
    for name in dict.fromkeys(names):
        record={'path':name,'ok':False}
        try:
            p=relative_file(root,name)
            size=p.stat().st_size
            if size==0:
                raise ValueError('empty file')
            ext=p.suffix.lower()
            method='readable_nonempty'
            if ext=='.json':
                load(p)
                method='strict_json'
            elif ext in {'.docx','.xlsx','.pptx','.zip'}:
                with zipfile.ZipFile(p) as archive:
                    members=set(archive.namelist())
                    required={'.docx':'word/document.xml','.xlsx':'xl/workbook.xml',
                              '.pptx':'ppt/presentation.xml'}.get(ext)
                    if not members or (required and not {required,'[Content_Types].xml'}<=members):
                        raise ValueError('archive lacks expected document entries')
                    if archive.testzip() is not None:
                        raise ValueError('archive member failed CRC')
                method='zip_crc_and_expected_entries'
            elif ext=='.pdf':
                try:
                    import pdfplumber
                except ImportError:
                    with p.open('rb') as f:
                        if not f.read(8).startswith(b'%PDF-'):
                            raise ValueError('missing PDF header')
                        f.seek(max(0,size-2048))
                        if b'%%EOF' not in f.read():
                            raise ValueError('missing PDF end marker')
                    method='pdf_markers_only'
                else:
                    with pdfplumber.open(p) as doc:
                        if not doc.pages:
                            raise ValueError('PDF has no pages')
                    method='pdf_page_tree'
            elif ext in TEXT or ext=='.trig':
                if not read(p).strip():
                    raise ValueError('blank text file')
                method='nonblank_utf8'
            record.update(ok=True,bytes=size,sha256=file_sha256(p),check=method)
        except Exception as exc:
            record['issue']=f'{type(exc).__name__}: {exc}'
        checked.append(record)
    return checked

TYPE_CHECKS={'object':lambda x:isinstance(x,dict),'array':lambda x:isinstance(x,list),
             'number':lambda x:type(x) in (int,float) and math.isfinite(x),
             'integer':lambda x:type(x) in (int,float) and math.isfinite(x) and x==int(x),
             'boolean':lambda x:type(x)==bool,'null':lambda x:x is None,'string':lambda x:isinstance(x,str)}

SCHEMA_KEYS={'$schema','$ref','$defs','definitions','properties','required','type','items',
             'allOf','anyOf','oneOf','not','if','then','else','additionalProperties','patternProperties',
             'dependentRequired','unevaluatedProperties','enum','const','minProperties','maxProperties'}

def contract_schema(contract):
    if isinstance(contract,bool):
        return contract
    if isinstance(contract,dict) and SCHEMA_KEYS.intersection(contract):
        return contract
    if isinstance(contract,dict):
        for key in ('schema','json_schema','required_fields','fields','output_fields'):
            if isinstance(contract.get(key),(dict,list)):
                return contract_schema(contract[key])
        meta={'description','required_artifacts','artifacts','notes','files','format','output_path'}
        entries=[{**(v if isinstance(v,dict) else {}),'name':k} for k,v in contract.items() if k not in meta]
    elif isinstance(contract,list):
        entries=[{'name':r} if isinstance(r,str) else r for r in contract]
    else:
        raise ValueError('output_contract must be a schema or field list')
    properties={}
    required=[]
    for entry in entries:
        if not isinstance(entry,dict):
            raise ValueError('invalid contract field descriptor')
        name=entry.get('name') or entry.get('field') or entry.get('key')
        if not isinstance(name,str) or not name:
            raise ValueError('contract field has no name')
        if name in properties:
            raise ValueError(f'duplicate contract field: {name}')
        flag=entry.get('required',True)
        if not isinstance(flag,(bool,list)):
            raise ValueError(f'{name}: required must be a boolean flag or a nested list')
        properties[name]={k:v for k,v in entry.items()
                          if k not in {'name','field','key'} and (k!='required' or isinstance(flag,list))}
        if flag is not False:
            required.append(name)
    return {'type':'object','properties':properties,'required':required}

def basic_validate(value, schema, path, issues):
    if isinstance(schema,bool):
        if not schema: issues.append(f'{path}: forbidden by schema')
        return
    safe={'type','required','properties','items','description','title','default','examples','$schema','$comment'}
    unsupported=set(schema)-safe
    if unsupported:
        issues.append(f'{path}: unvalidated constraints {sorted(unsupported)}; install jsonschema>=4.23,<5')
    ts=schema.get('type',[]); ts=[ts] if isinstance(ts,str) else ts
    if any(t not in TYPE_CHECKS for t in ts):
        issues.append(f'{path}: invalid type declaration {ts}')
        return
    if ts and not any(TYPE_CHECKS[t](value) for t in ts):
        issues.append(f'{path}: expected {ts}')
        return
    if isinstance(value,dict):
        for req in schema.get('required',[]):
            if req not in value: issues.append(f'{path}.{req}: missing required field')
        for key,sub in schema.get('properties',{}).items():
            if key in value: basic_validate(value[key],sub,f'{path}.{key}',issues)
    if isinstance(value,list) and 'items' in schema:
        if not isinstance(schema['items'],(dict,bool)):
            issues.append(f'{path}: tuple items need jsonschema')
        else:
            for i,item in enumerate(value):
                basic_validate(item,schema['items'],f'{path}[{i}]',issues)

def external_ref(schema):
    if isinstance(schema,dict):
        for key,value in schema.items():
            if key in {'$ref','$dynamicRef','$recursiveRef'} and isinstance(value,str) and not value.startswith('#'):
                return True
            if external_ref(value): return True
    elif isinstance(schema,list):
        return any(external_ref(value) for value in schema)
    return False

def check(case, result):
    if not isinstance(case,dict) or not isinstance(result,dict):
        return ['case and result must be JSON objects']
    issues=[]
    if 'case_id' not in result or ('case_id' in case and case['case_id']!=result['case_id']):
        issues.append('case_id missing or differs from case.json')
    if 'output_contract' not in case:
        return issues+['output_contract missing; read task.md and record manual verification']
    try:
        schema=contract_schema(case['output_contract'])
        if external_ref(schema):
            return issues+['external schema reference not fetched; supply an inline schema']
        try:
            from jsonschema.validators import validator_for
            from referencing import Registry
        except ImportError:
            basic_validate(result,schema,'$',issues)
        else:
            cls=validator_for(schema)
            cls.check_schema(schema)
            for error in cls(schema,format_checker=cls.FORMAT_CHECKER,registry=Registry()).iter_errors(result):
                path='$'+''.join(f'[{i}]' if isinstance(i,int) else f'.{i}' for i in error.absolute_path)
                issues.append(f'{path}: {error.message}')
    except (ValueError,TypeError,KeyError) as exc:
        issues.append(f'cannot validate contract: {exc}')
    except Exception as exc:
        issues.append(f'schema validation unavailable: {type(exc).__name__}: {exc}')
    return issues

def result_delivery(case, result, directory, explicit=()):
    """Validate files named by explicit declarations; never infer paths from prose."""
    issues=check(case,result)
    names=list(explicit)
    contract=case.get('output_contract') if isinstance(case,dict) else None
    if isinstance(contract,dict) and 'required_artifacts' in contract:
        entries=contract['required_artifacts']
        if not isinstance(entries,list):
            issues.append('required_artifacts needs manual interpretation: pass concrete output paths via --artifact')
        else:
            for i,entry in enumerate(entries):
                path=entry if isinstance(entry,str) else entry.get('path') if isinstance(entry,dict) else None
                if not isinstance(path,str) or not path.strip():
                    issues.append(f'required_artifacts[{i}]: no concrete path; review task.md')
                else:
                    names.append(path)
    artifacts=check_artifacts(directory,names)
    issues.extend(f"{r['path']}: {r['issue']}" for r in artifacts if not r['ok'])
    return {'ok':not issues,'issues':issues,'artifacts':artifacts}

def protected(text):
    """Diagnostics only: scientific tokens; identifiers can be intentionally renumbered."""
    text=unicodedata.normalize('NFC',text)
    number=r'(?<![\w.])[+−-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+−-]?\d+)?'
    unit=r'(?:kg|mg|[μµu]g|ng|g|km|cm|mm|[μµu]m|nm|m|ms|[μµu]s|ns|s|min|h|mL|[μµu]L|L|mmol|[μµu]mol|mol|kPa|MPa|GPa|Pa|kHz|MHz|GHz|Hz|mM|[μµu]M|M|K|eV|keV|MeV|J|W|V|A)(?:[²³]|\^?[−-]?\d+)?'
    quantity=number+r'\s*(?:%|‰|°\s*[CF]|'+unit+r'(?:\s*[/·]\s*'+unit+r')*)(?![A-Za-z])'
    pattern=(r'\$\$[^$]+\$\$|(?<!\$)\$[^$\n]+\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|'
             r'\b(?:Fig(?:ure)?|Tab(?:le)?|Eq(?:uation)?)\.?\s*\(?\d+[A-Za-z]?\)?|'
             r'\[\d+(?:\s*[,–-]\s*\d+)*\]|'+quantity+'|'+number)
    text=re.sub(r'(?<=[\u3400-\u9fff])(?=[+−-]?\d)', ' ', text)
    return Counter(re.sub(r'\s+', '', m.group()).replace('−','-').replace('µ','μ')
                   for m in re.finditer(pattern,text))

def delta(before, after):
    a,b=protected(before),protected(after)
    return {'missing':dict(a-b),'added':dict(b-a)}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest='cmd',required=True)
    i=subs.add_parser('inventory'); i.add_argument('root',type=Path)
    s=subs.add_parser('search'); s.add_argument('root',type=Path); s.add_argument('query'); s.add_argument('--limit',type=int,default=20)
    q=subs.add_parser('quotes'); q.add_argument('root',type=Path); q.add_argument('claims',type=Path)
    q.add_argument('--layout-index',type=Path,action='append',default=[])
    c=subs.add_parser('check'); c.add_argument('case',type=Path); c.add_argument('result',type=Path)
    c.add_argument('--artifact',action='append',default=[],help='required path relative to the result directory; repeat per file')
    d=subs.add_parser('compare'); d.add_argument('before',type=Path); d.add_argument('after',type=Path)
    a=p.parse_args()
    if a.cmd=='inventory': data=inventory(a.root)
    elif a.cmd=='search': data=search(a.root,a.query,a.limit)
    elif a.cmd=='quotes': data=quotes(a.root,a.claims,[load(p) for p in a.layout_index])
    elif a.cmd=='compare': data=delta(read(a.before),read(a.after))
    else:
        report=result_delivery(load(a.case),load(a.result),a.result.parent,a.artifact)
        print(json.dumps(report,ensure_ascii=False))
        return not report['ok']
    print(json.dumps(data,ensure_ascii=False,indent=2))
    return int(a.cmd=='quotes' and any(not r['verified'] for r in data))

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'ok':False,'error':type(exc).__name__,'detail':str(exc)},ensure_ascii=False))
        sys.exit(2)
