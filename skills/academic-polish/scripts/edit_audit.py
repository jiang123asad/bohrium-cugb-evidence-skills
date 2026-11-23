"""Audit protected tokens and review flags in independently aligned editing units."""
import argparse
import re
from collections import Counter
from pathlib import Path

from core import delta, load, save


SIGNALS = {
    'uncertainty': r'\b(?:may|might|could|possibly|likely|suggest(?:s|ed)?)\b|可能|或许|提示',
    'negation': r"\b(?:not|no|never|neither|without|cannot|\w+n't)\b|未|不|无",
    'scope': r'\b(?:only|except|unless|among|at least|at most)\b|仅|除非|至少|至多',
    'association': r'\b(?:correlat\w*|associat\w*)\b|相关|关联',
    'causation': r'\b(?:caus(?:e|es|ed|ing|al)|led to|leads to|resulted in)\b|导致|致使|造成',
}


def audit(data):
    rows, seen = [], set()
    for entry in data['units']:
        key=entry['id']
        if not isinstance(key,str) or not key.strip() or key in seen:
            raise ValueError('units require unique nonempty IDs')
        seen.add(key)
        a,b=entry['before'],entry['after']
        protected=delta(a,b)
        review=[]
        for kind,pattern in SIGNALS.items():
            before=Counter(x.group().casefold() for x in re.finditer(pattern,a,re.I))
            after=Counter(x.group().casefold() for x in re.finditer(pattern,b,re.I))
            if before!=after:
                review.append(kind)
        if a.strip() and not b.strip():
            review.append('blank_replacement')
        if a!=b and not entry.get('reason'):
            review.append('missing_change_reason')
        if not entry.get('locator'):
            review.append('missing_locator')
        rows.append({'id':key,'locator':entry.get('locator'),'protected_delta':protected,'review_flags':review,
                     'needs_review':bool(review or any(protected.values()))})
    return {'units':rows,'review_count':sum(r['needs_review'] for r in rows),
            'scope':'Mechanical review cues only. Re-read each flagged source and edit; no semantic equivalence claim.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    save(args.output,audit(load(args.input)))
