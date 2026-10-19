"""Standalone OCR and exports of the exact normalization retained by ingestion."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


def configured_normalizer(config):
    from .orchestration.langflow.pdf_client import PdfNormalizerClient
    client=PdfNormalizerClient(config.pdf_url,config.pdf_token_file)
    pin=None
    def profile():
        nonlocal pin
        if pin is None:
            pin=client.profile()
            client.expected_profile=pin["profile_hash"]
        return pin
    def normalize(data):
        profile()
        result=client.normalize(data)
        return result["text"],result["diagnostics"]
    normalize.profile=profile
    return normalize


def destinations(output, *, force=False, source=None):
    output=Path(output).expanduser().absolute()
    sidecar=output.with_suffix(".ocr.json")
    if output==sidecar:
        raise ValueError("OCR document and evidence destinations must differ")
    for path in (output,sidecar):
        if path.is_symlink() or path.is_dir():
            raise ValueError("OCR output must be a regular file destination")
        if source and (path.resolve()==Path(source).resolve() or
                       (path.exists() and path.samefile(source))):
            raise ValueError("OCR output must not replace its input PDF")
        if path.exists() and not force:
            raise ValueError("OCR output exists; choose another path or explicitly enable overwrite")
    return output,sidecar


def write_exports(result,output,*,force=False,source=None):
    output,sidecar=destinations(output,force=force,source=source)
    if not isinstance(result.get("text"),str) or not isinstance(result.get("diagnostics"),list):
        raise ValueError("Invalid OCR normalization result")
    document=result["text"].encode("utf-8")
    evidence=json.dumps(result,ensure_ascii=False,indent=2).encode("utf-8")+b"\n"
    staged=[]
    try:
        for path,data in ((sidecar,evidence),(output,document)):
            path.parent.mkdir(parents=True,exist_ok=True)
            fd,temporary=tempfile.mkstemp(prefix=".nima-ocr-",dir=path.parent)
            with os.fdopen(fd,"wb") as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            staged.append((path,Path(temporary)))
        for path,temporary in staged:
            if force:
                if path.is_symlink():raise ValueError("Refusing symlink OCR output")
                os.replace(temporary,path)
            else:
                os.link(temporary,path)
                temporary.unlink()
    finally:
        for _,temporary in staged:temporary.unlink(missing_ok=True)
    unresolved=sum(d.get("status")=="unresolved" for d in result["diagnostics"])
    for d in result["diagnostics"]:
        unresolved=max(unresolved,d.get("mathematics",{}).get("unresolved_formula_count",0))
    return {"document":str(output),"evidence":str(sidecar),"normalization_complete":bool(result.get("normalization_complete",unresolved==0)),
            "unresolved_region_count":unresolved}


def ocr_command(args,config):
    path=Path(args.file).expanduser()
    if path.is_symlink() or not path.is_file() or path.stat().st_size>50_000_000:
        raise ValueError("OCR requires a regular PDF file of at most 50 MB")
    destinations(args.output,force=args.force,source=path)
    data=path.read_bytes()
    if not data.startswith(b"%PDF-"):raise ValueError("OCR input must be a PDF")
    from .orchestration.langflow.pdf_client import PdfNormalizerClient
    client=PdfNormalizerClient(config.pdf_url,config.pdf_token_file)
    manifest=client.profile();client.expected_profile=manifest["profile_hash"]
    print("OCR: rendering and recognizing the PDF through Langflow.",file=sys.stderr,flush=True)
    result=client.normalize(data)
    result["source_sha256"]=hashlib.sha256(data).hexdigest()
    result["recognition_profile"]=manifest
    saved=write_exports(result,args.output,force=args.force,source=path)
    return {"status":"complete" if saved["normalization_complete"] else "partial",**saved}


def export_ingested(args,config,result):
    directory=getattr(args,"ocr_output_dir",None)
    if not directory:return result
    from .installation import store_path
    from .corpus_snapshot import CorpusSnapshot
    from .corpus_registry import CorpusRegistry
    saved=[]; failures=[]
    with CorpusSnapshot(store_path(config),corpus_id=args.corpus,project_id=args.project) as store:
        registry=CorpusRegistry(store)
        for source in result.data.get("sources",[]):
            evidence_id=source.get("normalization_result_artifact_id")
            if not evidence_id:continue
            try:
                scope=dict(corpus_id=args.corpus,project_id=args.project,exact_scope=True)
                if not registry.resolve(evidence_id,**scope):
                    raise ValueError("OCR evidence is not published in the requested scope")
                evidence=json.loads(store.read_artifact(evidence_id))
                if hashlib.sha256(evidence["text"].encode()).hexdigest()!=source["normalized_artifact_id"]:
                    raise ValueError("Saved OCR evidence differs from ingested normalization")
                evidence=copy.deepcopy(evidence)
                for diagnostic in evidence["diagnostics"]:
                    if "artifact_ids" in diagnostic:
                        diagnostic["artifact_bundle"]=[]
                        for artifact in diagnostic["artifact_ids"]:
                            if not registry.resolve(artifact,**scope):raise ValueError("OCR crop is outside the requested scope")
                            data=store.read_artifact(artifact)
                            diagnostic["artifact_bundle"].append({"sha256":artifact,"data_base64":base64.b64encode(data).decode()})
                name=Path(source["name"]).stem+"-"+source["original_artifact_id"][:12]+".md"
                saved.append({"source_id":source["source_id"],**write_exports(evidence,Path(directory)/name,
                    force=getattr(args,"overwrite_ocr",False),source=args.file if Path(args.file).is_file() else None)})
            except (OSError,ValueError) as error:
                failures.append({"source_id":source["source_id"],"message":str(error)})
    return result.model_copy(update={"data":{**result.data,"ocr_exports":saved,"ocr_export_errors":failures}})
