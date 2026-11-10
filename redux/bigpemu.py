"""Pinned, unmodified official BigPEmu setup and portable launch defaults."""
from pathlib import Path, PurePosixPath, PureWindowsPath
import hashlib
import io
import json
import os
import tempfile
import zipfile
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .library import ImportCancelled

VERSION='1.221'
URL='https://www.richwhitehouse.com/jaguar/builds/BigPEmu_v1221.zip'
SIZE=10805979
SHA256='90b49dff119c66f8e8bd6eb45c1c5f24959dde165778e3d0e7fb5f7c46ee8c3d'
FNV64='332F838AB15C13FF'
SYSTEMS=('jaguar','jaguar_cd')
EXTENSIONS=frozenset({'.j64','.cof','.rom','.jag','.abs','.cue','.cdi','.bigpimg'})


def directory(root):return Path(root)/'tools/emulators/BigPEmu'
def executable(root):return directory(root)/'BigPEmu.exe'


def _check(cancelled):
    if cancelled is not None and cancelled.is_set():raise ImportCancelled('BigPEmu setup cancelled')


class _Redirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parsed=urlsplit(newurl)
        if parsed.scheme!='https' or parsed.hostname!='www.richwhitehouse.com' or parsed.username or parsed.password:
            raise ValueError('BigPEmu redirected outside its official HTTPS host')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def download(cancelled=None):
    request=Request(URL,headers={'User-Agent':'ReduxMediaLibrary (+https://github.com/baysidejr/redux)',
                    'Accept':'application/zip, application/octet-stream, */*'})
    data=bytearray()
    with build_opener(_Redirect()).open(request,timeout=20) as response:
        while True:
            _check(cancelled)
            block=response.read(min(65536,SIZE+1-len(data)))
            if not block:break
            data.extend(block)
            if len(data)>SIZE:raise ValueError('BigPEmu archive exceeds its published size')
    return bytes(data)


def extract_archive(data,folder):
    """Extract only a verified, bounded archive to a new staging directory."""
    if len(data)!=SIZE or hashlib.sha256(data).hexdigest()!=SHA256:
        raise ValueError('BigPEmu archive does not match the verified official release')
    folder=Path(folder)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries=archive.infolist()
        if len(entries)>1000 or sum(i.file_size for i in entries)>128*1024*1024:
            raise ValueError('BigPEmu archive exceeds extraction limits')
        seen=set()
        for entry in entries:
            name=entry.filename.replace('\\','/')
            parts=PurePosixPath(name).parts
            if not parts or name.startswith('/') or PureWindowsPath(name).drive or any(p in {'.','..'} or ':' in p or p.rstrip(' .')!=p for p in parts):
                raise ValueError('Unsafe BigPEmu archive path')
            key=name.rstrip('/').casefold()
            if key in seen or (entry.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Duplicate or linked BigPEmu archive entry')
            seen.add(key)
            target=folder.joinpath(*parts)
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with target.open('xb') as output:output.write(archive.read(entry))
    for name in ('BigPEmu.exe','ReadMe.txt','Plugins/BigPEmu_Input_DirectInput.dll'):
        if not (folder/name).is_file():raise ValueError('BigPEmu distribution is incomplete')


def install(root,*,cancelled=None,downloader=download):
    target=directory(root).absolute()
    for path in (*reversed(target.parents),target):
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
            raise ValueError('BigPEmu storage cannot use links or junctions')
    if (target/'BigPEmu.exe').is_file():
        if not (target/'ReadMe.txt').is_file() or not (target/'Plugins/BigPEmu_Input_DirectInput.dll').is_file():
            raise ValueError('BigPEmu installation is incomplete; keep the full official ZIP structure')
        return {'already_installed':True}
    if target.exists():raise ValueError('BigPEmu folder is incomplete; move it aside before installing again')
    data=downloader(cancelled)
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bigpemu-',dir=target.parent) as temporary:
        stage=Path(temporary)/'BigPEmu';stage.mkdir()
        extract_archive(data,stage)
        _check(cancelled)
        (stage/'redux-provenance.json').write_text(json.dumps({'version':VERSION,'url':URL,'sha256':SHA256,
            'published_fnv1a64':FNV64,'archive_bytes':SIZE,'unmodified_official_distribution':True},indent=2))
        stage.rename(target)
    return {'version':VERSION,'sha256':SHA256}
