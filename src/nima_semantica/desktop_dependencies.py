"""Use the upstream OKF Studio release, without maintaining another GUI."""
from pathlib import Path
import hashlib,os,platform,shutil,subprocess,sys

STUDIO_VERSION='0.10.0'
STUDIO_SHA256='37c7efe010911908ce5c8078dfc43d9710b7e74323137107fd1777df8870fd1c'

def provision_studio(config):
    candidate=config.studio_executable or os.environ.get('NIMA_OKF_STUDIO') or shutil.which('okf-viewer')
    if candidate:
        if not Path(candidate).is_file():raise ValueError('Configured Studio executable is unavailable')
        config.studio_executable=str(Path(candidate).resolve());return dict(executable=config.studio_executable,source='existing installation')
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):return dict(available=False,reason='Headless installation; complete OKF export remains available')
    if sys.platform!='linux' or platform.machine() not in ('x86_64','amd64'):
        return dict(available=False,reason='Install or build upstream OKF Studio for this platform and configure studio_executable')
    import httpx
    root=Path(config.data_root)/'installation/okf-studio';root.mkdir(parents=True,exist_ok=True)
    package=root/('okf-studio-'+STUDIO_VERSION+'.deb')
    url='https://github.com/saschb2b/okf-studio/releases/download/v'+STUDIO_VERSION+'/OKF.Studio_'+STUDIO_VERSION+'_amd64.deb'
    if not package.exists():
        with httpx.stream('GET',url,follow_redirects=True,timeout=120) as response:
            response.raise_for_status()
            with package.with_suffix('.part').open('wb') as out:
                for chunk in response.iter_bytes():out.write(chunk)
        package.with_suffix('.part').replace(package)
    if hashlib.sha256(package.read_bytes()).hexdigest()!=STUDIO_SHA256:raise ValueError('Official Studio package checksum differs')
    subprocess.run(['dpkg-deb','-x',str(package),str(root/'app')],check=True)
    config.studio_executable=str(root/'app/usr/bin/okf-viewer')
    from .installation import write_json
    result=dict(executable=config.studio_executable,version=STUDIO_VERSION,package_url=url,sha256=STUDIO_SHA256)
    write_json(root/'installation.json',result)
    return result
