"""Physical-size scientific typography and reproducible vector/hybrid exports."""
import hashlib,json
from pathlib import Path

def global_plot_defs():
    from matplotlib import pyplot as plt
    plt.rcParams.update({'text.usetex':True,'font.family':'serif','font.serif':['Computer Modern'],
        'font.size':14,'axes.titlesize':15,'axes.labelsize':15,'xtick.labelsize':14,'ytick.labelsize':14,
        'legend.fontsize':15,'figure.titlesize':15,'figure.facecolor':'white','axes.facecolor':'white',
        'axes.linewidth':1.2,'lines.linewidth':1.6,'svg.fonttype':'none','pdf.fonttype':42})
    return plt

def scientific_canvas(width_mm=183,height_mm=100,**kwargs):
    plt=global_plot_defs()
    if width_mm<=0 or height_mm<=0:raise ValueError('Choose positive final physical dimensions')
    return plt.subplots(figsize=(width_mm/25.4,height_mm/25.4),**kwargs)

def export_figure(figure,directory,companion,*,source_files=(),data_files=(),minimum_font_pt=14):
    from matplotlib.text import Text
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    if not companion.strip():raise ValueError('A figure companion is required')
    figure.canvas.draw()
    small=[t.get_text() for t in figure.findobj(Text) if t.get_visible() and t.get_text().strip() and t.get_fontsize()<minimum_font_pt-.01]
    if small:raise ValueError('Text falls below the final-size font minimum: '+repr(small))
    # Never crop/rescale the canvas after choosing final dimensions.
    for extension in ('svg','pdf','png'):figure.savefig(root/('figure.'+extension),format=extension,dpi=600,facecolor='white',transparent=False)
    (root/'figure.md').write_text(companion)
    hashes={}
    import shutil
    for role,files in (('source',source_files),('data',data_files)):
        for value in files:
            path=Path(value);target=root/role/path.name;target.parent.mkdir(exist_ok=True)
            if target.resolve()!=path.resolve():shutil.copy2(path,target)
            hashes[str(target.relative_to(root))]=hashlib.sha256(target.read_bytes()).hexdigest()
    result=dict(width_mm=figure.get_figwidth()*25.4,height_mm=figure.get_figheight()*25.4,minimum_font_pt=minimum_font_pt,
        latex=True,typography='Computer Modern',source_hashes=hashes,format='vector text/arrows/plots; embedded raster only for image artists')
    (root/'figure-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
