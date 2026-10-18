"""Shared SciPost-style reports and managed external rendering dependencies."""
from pathlib import Path
import hashlib,importlib.util,json,os,platform,shutil,subprocess,sys,tarfile,tempfile
from .installation import assets,identifier,write_json
from .project_layout import folder
from .storage import atomic_bytes

BLENDER_VERSION='4.5.3'
BLENDER_LINUX_SHA256='975c58fcb244273838534bba771e64ad87739216b0f9b39a888531a49a72d845'

def reporting_tools(config):
    blender=config.blender_executable or shutil.which('blender')
    if not blender and sys.platform=='darwin' and Path('/Applications/Blender.app/Contents/MacOS/Blender').is_file():blender='/Applications/Blender.app/Contents/MacOS/Blender'
    tools={name:shutil.which(name) for name in ('pdflatex','latexmk','bibtex','dvisvgm','pdftoppm')}
    tools['blender']=blender
    python={name:importlib.util.find_spec(name) is not None for name in ('matplotlib','cairosvg')}
    return dict(ready=all(tools.values()) and all(python.values()),executables=tools,python=python)

def provision_reporting(config):
    """Standard setup provisions reporting; --minimal explicitly disables it."""
    if not config.reporting_enabled:return dict(enabled=False)
    import httpx
    root=Path(config.data_root)/'installation/reporting';root.mkdir(parents=True,exist_ok=True)
    tools=reporting_tools(config)
    if not tools['executables']['blender']:
        if sys.platform=='linux' and platform.machine() in ('x86_64','amd64'):
            name=f'blender-{BLENDER_VERSION}-linux-x64';archive=root/(name+'.tar.xz')
            url='https://download.blender.org/release/Blender4.5/'+archive.name
            if not archive.exists():
                with httpx.stream('GET',url,follow_redirects=True,timeout=120) as response:
                    response.raise_for_status()
                    with archive.with_suffix('.part').open('wb') as stream:
                        for chunk in response.iter_bytes():stream.write(chunk)
                archive.with_suffix('.part').replace(archive)
            if hashlib.sha256(archive.read_bytes()).hexdigest()!=BLENDER_LINUX_SHA256:raise ValueError('Blender release checksum mismatch')
            with tarfile.open(archive) as tar:tar.extractall(root,filter='data')
            config.blender_executable=str(root/name/'blender')
        elif sys.platform=='darwin' and shutil.which('brew'):
            subprocess.run(['brew','install','--cask','blender'],check=True)
            config.blender_executable='/Applications/Blender.app/Contents/MacOS/Blender'
        else:raise ValueError('Install Blender and configure blender_executable for this platform')
    missing_python=[name for name,ready in tools['python'].items() if not ready]
    if missing_python:
        command=[sys.executable,'-m','pip','install','matplotlib>=3.8,<4','cairosvg>=2.7,<3']
        if not importlib.util.find_spec('pip') and shutil.which('uv'):command=['uv','pip','install','--python',sys.executable,*command[4:]]
        subprocess.run(command,check=True)
    if any(not tools['executables'][name] for name in ('pdflatex','latexmk','bibtex','dvisvgm','pdftoppm')):
        if sys.platform=='darwin' and shutil.which('brew'):subprocess.run(['brew','install','--cask','mactex-no-gui'],check=True)
        elif sys.platform=='linux' and shutil.which('apt-get'):
            prefix=[] if os.geteuid()==0 else ['sudo']
            subprocess.run([*prefix,'apt-get','install','-y','texlive-latex-extra','texlive-fonts-recommended','texlive-science','dvisvgm','latexmk','poppler-utils'],check=True)
        else:raise ValueError('Install a TeX distribution, latexmk, dvisvgm and Poppler, then rerun setup')
    result=reporting_tools(config)
    blender=result['executables']['blender']
    if blender:
        result['blender_version']=subprocess.check_output([blender,'--version'],text=True,timeout=30).splitlines()[0]
        config.blender_executable=blender
    write_json(root/'toolchain.json',result)
    if not result['ready']:raise ValueError('Reporting dependencies remain unavailable; see toolchain.json')
    return result

SECTIONS={
    'paper':['Introduction','Model and assumptions','Methods','Results','Discussion','Conclusion'],
    'research':['Research question','Evidence and assumptions','Methods','Results','Interpretation','Remaining questions'],
    'progress':['Objectives','Work completed','Current results','Open questions','Next steps'],
    'proof':['Problem and assumptions','Related results','Main statements','Proof','Failed approaches','Conclusion'],
}

def create_report(workspace,binding,name,kind='research'):
    name=identifier(name)
    if kind not in SECTIONS:raise ValueError('Unknown report kind')
    destination=folder(workspace,binding,'reports')/name
    if destination.exists() and any(destination.iterdir()):raise ValueError('Report exists; edit it or choose a new name')
    destination.mkdir(parents=True,exist_ok=True)
    template=(assets()/'skills/_shared/reporting/scipost-report.tex').read_text()
    body='\n\n'.join('\\section{'+section+'}\nDescribe the '+section.lower()+' using the actual evidence and results.' for section in SECTIONS[kind])
    text=template.replace('NIMA_REPORT_TITLE',name.replace('_',r'\_')).replace('NIMA_REPORT_BODY',body)
    atomic_bytes(destination/'main.tex',text.encode());atomic_bytes(destination/'references.bib',b'% Add only references actually cited in this report.\n')
    write_json(destination/'report.json',dict(kind=kind,name=name,corpus_id=binding['corpus_id'],project_id=binding['project_id']))
    return dict(directory=str(destination.resolve()),source=str((destination/'main.tex').resolve()),kind=kind)

def build_report(directory):
    root=Path(directory).resolve();command=['latexmk','-pdf','-interaction=nonstopmode','-halt-on-error','-no-shell-escape','main.tex']
    result=subprocess.run(command,cwd=root,capture_output=True,text=True)
    atomic_bytes(root/'build.log',(result.stdout+result.stderr).encode())
    if result.returncode:raise ValueError('Report compilation failed; inspect '+str(root/'build.log'))
    pdf=root/'main.pdf';log=(root/'main.log').read_text()
    checks=dict(compiled=pdf.is_file(),unresolved_references='undefined' in log,overfull_boxes='Overfull' in log)
    write_json(root/'build-receipt.json',dict(command=command,pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),checks=checks))
    return dict(pdf=str(pdf),checks=checks)

def report_command(args,config):
    if args.action=='doctor':return reporting_tools(config)
    from .release_cli import workspace_binding
    root,binding=workspace_binding(config,args.path)
    if args.action=='init':return create_report(root,binding,args.name,args.kind)
    return build_report(folder(root,binding,'reports')/identifier(args.name))
