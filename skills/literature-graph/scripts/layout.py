"""PDF evidence extraction via pdfplumber; preserves coordinates and text blocks."""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from core import save

def order(blocks, width):
    if not blocks:
        return [], None
    # Prefer a gutter with substantial overlapping left/right text bands and few cutters.
    options=[]
    for fraction in (0.43, 0.46, 0.48, 0.50, 0.52, 0.54, 0.57):
        x=width*fraction
        left=[b for b in blocks if b['bbox'][2] < x-4 and len(b['text'])>=18]
        right=[b for b in blocks if b['bbox'][0] > x+4 and len(b['text'])>=18]
        if len(left)<2 or len(right)<2:
            continue
        top=max(min(b['bbox'][1] for b in g) for g in (left,right))
        bot=min(max(b['bbox'][3] for b in g) for g in (left,right))
        if bot-top<18:
            continue
        cross=sum(b['bbox'][0]<x-4 and b['bbox'][2]>x+4 and top<b['bbox'][1]<bot
                  for b in blocks)
        options.append((min(len(left),len(right))-cross*1.5+(bot-top)/3000, -abs(fraction-.50),x))
    xs=max(options) if options else None
    by_y=lambda b:(round(b['bbox'][1],1),b['bbox'][0])
    if not xs or xs[0]<1.5:
        return sorted(blocks,key=by_y),None
    x=xs[-1]
    full=[b for b in blocks if b['bbox'][0]<x-4 and b['bbox'][2]>x+4]
    columns=[b for b in blocks if b not in full]
    result=[]
    for b in sorted(full,key=by_y):
        band=[c for c in columns if c['bbox'][1]<b['bbox'][1]]
        result.extend(sorted(band,key=lambda c:(c['bbox'][0]>=x-4,*by_y(c))))
        columns=[c for c in columns if c not in band]
        result.append(b)
    result.extend(sorted(columns,key=lambda c:(c['bbox'][0]>=x-4,*by_y(c))))
    return result,round(x,2)

def kind(text):
    t=' '.join(text.split())
    if re.match(r'^(?:fig(?:ure)?\.?\s*\d|图\s*\d)',t,re.I): return 'figure_caption_candidate'
    if re.match(r'^(?:tab(?:le)?\.?\s*\d|表\s*\d)',t,re.I): return 'table_caption_candidate'
    if re.fullmatch(r'(?:abstract|references|bibliography|摘要|参考文献|acknowledg(?:e)?ments)[:：]?',t,re.I):
        return 'heading_candidate'
    if len(t)<100 and '\n' not in text and re.match(r'^\d+(?:\.\d+)*\.?\s+\D',t): return 'heading_candidate'
    return 'text'

def box(obj):
    return [round(float(obj[k]),2) for k in ('x0','top','x1','bottom')]

def extract(path, chosen=None, with_tables=False, password=''):
    import pdfplumber
    pages=[]
    counts=Counter()
    with pdfplumber.open(path,laparams={},password=password) as doc:
        for p in doc.pages:
            if chosen and p.page_number not in chosen: continue
            blocks=[]
            for o in p.objects.get('textboxhorizontal',[]):
                t=(o.get('text') or '').rstrip('\n')
                if t.strip():
                    b=box(o)
                    blocks.append({'text':t,'bbox':b,'kind':kind(t),'method':'pdfminer_textbox'})
            if not blocks and p.chars:
                for l in p.extract_text_lines():
                    blocks.append({'text':l['text'],'bbox':box(l),'kind':kind(l['text']),'method':'pdfplumber_line'})
            for b in blocks:
                role='top' if b['bbox'][3]<p.height*.09 else 'bottom' if b['bbox'][1]>p.height*.91 else None
                if role and len(b['text'])<135:
                    b['_margin']=(role,re.sub(r'\d+', '#',' '.join(b['text'].casefold().split())))
            counts.update({b['_margin'] for b in blocks if '_margin'in b})
            tt=[]
            errors=[]
            if with_tables:
                try:
                    for t in p.find_tables():
                        tt.append({'bbox':list(t.bbox),'cell_bboxes':[list(c) for c in t.cells],
                                   'rows':t.extract(),'method':'pdfplumber_lines'})
                except (ValueError,TypeError) as e:
                    errors.append('table pass: '+str(e))
            pages.append({'pdf_page':p.page_number,'page_label':getattr(p,'page_label',None),
                          'width':p.width,'height':p.height,'blocks':blocks,'tables':tt,'warnings':errors})
            p.close()
    for p in pages:
        body,margin=[],[]
        for b in p['blocks']:
            k=b.pop('_margin',None)
            (margin if k and counts[k]>=2 else body).append(b)
        arranged,gutter=order(body,p['width'])
        p['blocks']=arranged+margin
        p['gutter_pt']=gutter
        letters=sum(c.isalnum() for b in body for c in b['text'])
        p['needs_visual_review']=letters<25 or any('\ufffd' in b['text'] or '(cid:' in b['text'] for b in body)
        if p['needs_visual_review']:
            p['warnings'].append('inspect rendered page; OCR only if image text is actually present')
        for i,b in enumerate(p['blocks'],1):
            b['id']=f"P{p['pdf_page']:04d}-B{i:03d}"
            if i>len(arranged):
                b['original_kind']=b['kind']
                b['kind']='repeated_margin_candidate'
        for i,t in enumerate(p['tables'],1):
            t['id']=f"P{p['pdf_page']:04d}-T{i:03d}"
    return {'source':path.name,'bbox_convention':'x0, top, x1, bottom (PDF pt, top-left)',
            'backend':f'pdfplumber {pdfplumber.__version__}','pages':pages}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('pdf',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--pages',type=int,nargs='+');p.add_argument('--tables',action='store_true')
    a=p.parse_args()
    try:
        data=extract(a.pdf,a.pages,a.tables)
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__,'detail':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2
    save(a.output,data)
    return 0

if __name__=='__main__':
    sys.exit(main())
