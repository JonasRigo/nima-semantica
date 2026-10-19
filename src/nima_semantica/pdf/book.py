"""Resumable page-range normalization with original-PDF locators."""
from pathlib import Path
import copy,json,re
from .support import canonical_json,sha256_bytes,sha256_file

def merge_batches(parts,source_hash,runtime):
    texts=[];diagnostics=[];offset=0;unresolved=0;covered=[];artifacts=[]
    for first,last,result in parts:
        covered.extend(range(first,last+1));text=result['text']
        def adjust(value):
            if isinstance(value,list):return [adjust(v) for v in value]
            if not isinstance(value,dict):return value
            out={k:adjust(v) for k,v in value.items()}
            for key in ('page','page_no'):
                if type(out.get(key))==int:out[key]+=first-1
            return out
        for item in result['diagnostics']:
            d=adjust(copy.deepcopy(item))
            if d.get('kind')=='parser_manifest':
                for artifact in d.get('artifact_bundle',[]):
                    entry=dict(artifact,name=f'pages-{first:04d}-{last:04d}/'+artifact['name']);artifacts.append(entry)
                continue
            if d.get('kind')=='formula':
                for key in ('start','end','normalized_text_start','normalized_text_end'):
                    if type(d.get(key))==int:d[key]+=offset
            d['original_pdf_sha256']=source_hash;d['page_batch']=[first,last]
            if d.get('status')=='unresolved':unresolved+=1
            diagnostics.append(d)
        texts.append(text);offset+=len(text)+2
    if covered!=list(range(1,max(covered,default=0)+1)):raise ValueError('Page batch coverage is incomplete or overlapping')
    text='\n\n'.join(texts)
    diagnostics.append(dict(kind='parser_manifest',status='extracted',source_sha256=source_hash,normalized_sha256=sha256_bytes(text.encode()),
        provenance=[dict(page=i) for i in covered],runtime=runtime,artifact_bundle=artifacts,
        mathematics=dict(unresolved_formula_count=unresolved),batch_pages=16,total_pages=len(covered)))
    return dict(text=text,diagnostics=diagnostics,normalization_complete=unresolved==0)

def normalize_book(source,models,output,runtime,*,recognize,progress=None):
    import pypdfium2 as pdfium
    from .worker import normalize_single_pdf
    from ._ported import _atomic_formula_artifact
    source=Path(source);source_hash=sha256_file(source)
    root=Path(output)/source_hash/runtime['profile_hash'];root.mkdir(parents=True,exist_ok=True)
    parts=[]
    with pdfium.PdfDocument(source) as pdf:
        count=len(pdf)
        for first in range(1,count+1,16):
            last=min(first+15,count);prefix=f'pages-{first:04d}-{last:04d}'
            checkpoint=root/(prefix+'.json');digest=root/(prefix+'.sha256')
            if checkpoint.exists() and digest.exists():
                if sha256_file(checkpoint)!=digest.read_text():raise ValueError('Book batch checkpoint integrity mismatch')
                batch=json.loads(checkpoint.read_bytes())
            else:
                path=root/(prefix+'.pdf')
                if not path.exists():
                    with pdfium.PdfDocument.new() as subset:
                        subset.import_pages(pdf,list(range(first-1,last)));subset.save(path)
                batch=normalize_single_pdf(path,models,root/'batches',runtime,recognize=recognize,progress=progress)
                _atomic_formula_artifact(checkpoint,canonical_json(batch));_atomic_formula_artifact(digest,sha256_file(checkpoint).encode())
            parts.append((first,last,batch))
            if progress:progress(dict(phase='book_batch',first_page=first,last_page=last,total_pages=count,cached=checkpoint.exists()))
    result=merge_batches(parts,source_hash,runtime)
    _atomic_formula_artifact(root/'result.json',canonical_json(result));_atomic_formula_artifact(root/'result.sha256',sha256_bytes(canonical_json(result)).encode())
    return result
