"""Compile an isolated copy with the host TeX tools; emit actual diagnostics and a PDF on success."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from core import save

def run(cmd, cwd, timeout):
    p=subprocess.run(cmd,cwd=cwd,stdin=subprocess.DEVNULL,capture_output=True,
                     encoding='utf-8',errors='replace',timeout=timeout)
    return p.returncode,p.stdout+'\n'+p.stderr

def build(root, main, output, work, engine, build_base='root', require_resolved=False):
    entry=(root/main).resolve()
    if not entry.is_relative_to(root.resolve()) or entry.suffix.lower()!='.tex' or not entry.is_file():
        raise ValueError('main must identify a TeX entrypoint within project')
    work.mkdir(parents=True,exist_ok=True)
    output.mkdir(parents=True,exist_ok=True)
    stale=output/'compiled.pdf'
    if stale.exists():
        raise FileExistsError('use a clean report directory to avoid replacing a previous successful build')
    commands=[]
    records=[]
    version=None
    trace=''
    rc=1
    mode={'pdflatex':'-pdf','xelatex':'-xelatex','lualatex':'-lualatex'}
    with tempfile.TemporaryDirectory(prefix='compile-',dir=work) as tmp:
        sub=Path(tmp)/'project'
        shutil.copytree(root,sub,ignore=shutil.ignore_patterns('.git','*.aux','*.log','*.fls','*.fdb_latexmk','*.synctex.gz','latexmkrc','.latexmkrc'),
                        symlinks=True)
        if any(p.is_symlink() for p in sub.rglob('*')):
            raise ValueError('project includes a symlink; use local regular files for a standalone build')
        src=sub/entry.relative_to(root.resolve())
        if src.name.startswith('-'): raise ValueError('unexpected main filename')
        base=sub if build_base=='root' else src.parent
        relative='./'+src.relative_to(base).as_posix()
        f=base/(src.stem+'.pdf')
        # Delete only the copied target: exit 0 without a new PDF is not a successful build.
        if f.exists(): f.unlink()
        if shutil.which('latexmk'):
            cmd=['latexmk','-norc',mode[engine],'-interaction=nonstopmode','-halt-on-error',
                 '-file-line-error','-no-shell-escape','-outdir=.',relative]
        elif shutil.which('tectonic') and engine=='xelatex':
            cmd=['tectonic','--only-cached','--keep-logs','--outdir','.',relative]
        else:
            cmd=[]
        if cmd:
            commands.append(cmd)
            try:
                _,v=run([cmd[0],'--version'],base,12)
                version=next((l for l in v.splitlines() if l.strip()),None)
                rc,trace=run(cmd,base,150)
            except subprocess.TimeoutExpired as exc:
                trace=((exc.stdout or b'').decode('utf-8','replace') if isinstance(exc.stdout,bytes) else (exc.stdout or ''))+'\nBuild timed out'
                rc=124
            texlog=base/(src.stem+'.log')
            if texlog.is_file():
                trace+='\n--- TeX log ---\n'+texlog.read_text(encoding='utf-8',errors='replace')
            if rc==0 and f.is_file() and f.read_bytes()[:5]==b'%PDF-':
                shutil.copyfile(f,output/'compiled.pdf')
            elif rc==0:
                trace+='\nMissing newly generated PDF despite compiler exit code 0.'
        else:
            trace='No compatible latexmk or cached Tectonic tool found; use the platform compiler and capture its real output.'
        manifest={}
        for p in sorted(root.rglob('*')):
            if p.is_file() and p.suffix.lower() in ('.tex','.bib','.bst','.cls','.sty','.png','.jpeg','.jpg','.pdf','.eps','.svg','.csv'):
                manifest[p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    signals=re.compile(r'(^!|:\d+:\s|LaTeX Warning|Package .* Warning|overfull|underfull|undefined|missing|rerun|fatal|No compatible|timed out)',re.I)
    for i,line in enumerate(trace.splitlines(),1):
        if signals.search(line): records.append({'log_line':i,'message':line})
    (output/'compile.log').write_text(trace,encoding='utf-8')
    ok=(output/'compiled.pdf').is_file()
    final_log=trace.rsplit('--- TeX log ---',1)[-1]
    unresolved=bool(re.search(
        r'There were undefined (?:references|citations)|(?:Reference|Citation|Citations).+undefined|'
        r'Label\(s\) may have changed|Rerun to get (?:cross-references|citations) right|'
        r'(?:Please )?\(re\)run Biber|No file .+\.bbl\.',final_log,re.I))
    status='build_failed' if not ok else 'pdf_with_unresolved_references' if unresolved else 'built_without_known_reference_warnings'
    report={'built_pdf':ok,'status':status,'engine_requested':engine,'tool_version':version,'commands':commands,'return_code':rc if commands else None,
            'diagnostics':records,'input_sha256':manifest,'build_base':build_base,
            'compiled_sha256':hashlib.sha256((output/'compiled.pdf').read_bytes()).hexdigest() if ok else None,
            'unresolved_final_log':unresolved,'strict_reference_check':require_resolved,
            'artifacts':['compile-report.json','compile.log']+(['compiled.pdf'] if ok else [])}
    save(output/'compile-report.json',report)
    return ok and (not require_resolved or not unresolved)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('project',type=Path);p.add_argument('main',type=Path)
    p.add_argument('output',type=Path);p.add_argument('work',type=Path)
    p.add_argument('--engine',choices=('pdflatex','xelatex','lualatex'),default='pdflatex')
    p.add_argument('--build-base',choices=('root','entry'),default='root')
    p.add_argument('--require-resolved',action='store_true',help='fail acceptance on known unresolved-reference warnings; preserve PDF and logs')
    a=p.parse_args()
    project,output,work=(x.resolve() for x in (a.project,a.output,a.work))
    if output.is_relative_to(project) or work.is_relative_to(project):
        p.error('report and build scratch directories must be outside the source project')
    return 0 if build(project,a.main,output,work,a.engine,a.build_base,a.require_resolved) else 2

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (OSError,ValueError,KeyError) as exc:
        print(json.dumps({'ok':False,'error':type(exc).__name__,'detail':str(exc)},ensure_ascii=False))
        sys.exit(2)
