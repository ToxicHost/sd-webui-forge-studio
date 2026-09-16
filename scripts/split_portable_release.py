"""Split a large portable ZIP into hash-checked GitHub Release assets.

The original ZIP is preserved. Each part stays below GitHub's 2 GiB limit.
The generated Windows batch file joins the exact parts and verifies the result;
it does not extract or execute the application or alter execution policy.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def split(archive: Path, out: Path, *, part_bytes: int = 1_900_000_000) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.zip", archive.name):
        raise ValueError("Use a simple ZIP filename containing letters, digits, dots, dashes or underscores")
    if not 0 < part_bytes < 2**31:
        raise ValueError("Each part must be smaller than 2 GiB")
    out.mkdir(parents=True, exist_ok=True)
    count = (archive.stat().st_size + part_bytes - 1) // part_bytes
    names = [f"{archive.name}.{index:03d}" for index in range(1, count+1)]
    if any((out/name).exists() for name in [*names,"Combine-Portable.bat","release-downloads.json"]):
        raise ValueError("Release output already exists; choose a fresh output directory")
    parts = []
    full_hash = hashlib.sha256()
    with archive.open("rb") as source:
        for name in names:
            remaining=part_bytes; size=0; hashed=hashlib.sha256()
            with (out/name).open("xb") as target:
                while remaining:
                    chunk=source.read(min(1024*1024,remaining))
                    if not chunk: break
                    target.write(chunk); hashed.update(chunk); full_hash.update(chunk)
                    size+=len(chunk); remaining-=len(chunk)
            parts.append({"filename":name,"bytes":size,"sha256":hashed.hexdigest()})
    value={"archive":archive.name,"bytes":archive.stat().st_size,"sha256":full_hash.hexdigest(),"parts":parts}
    commands=["@echo off","setlocal",'cd /d "%~dp0"',f'set "STUDIO_ARCHIVE=%~dp0{archive.name}"',
              'if exist "%STUDIO_ARCHIVE%" (', '  echo The assembled ZIP already exists. Move it before combining again.', '  goto failed', ')']
    for part in parts:
        commands.extend([f'if not exist "{part["filename"]}" (',f'  echo Missing download: {part["filename"]}', '  goto failed', ')'])
    joined='+'.join('"'+part['filename']+'"' for part in parts)
    commands.extend([f'copy /b {joined} "%STUDIO_ARCHIVE%" >nul','if errorlevel 1 goto failed',
                     'powershell -NoProfile -NonInteractive -Command "'+
                     "$ErrorActionPreference='Stop'; $s=[IO.File]::OpenRead($env:STUDIO_ARCHIVE); "+
                     "$a=[Security.Cryptography.SHA256]::Create(); try { "+
                     "$h=[BitConverter]::ToString($a.ComputeHash($s)).Replace('-',''); if ($h -ne '"+
                     value['sha256']+"') { exit 1 } } finally { $s.Dispose(); $a.Dispose() }"+'"',
                     'if errorlevel 1 (','  echo Checksum failed. Do not use the assembled ZIP. Download the parts again.', '  goto failed', ')',
                     'echo Checksum passed. Extract the ZIP to a short folder, then run Start-Studio.bat.',
                     'pause','exit /b 0',':failed','pause','exit /b 1'])
    (out/'Combine-Portable.bat').write_bytes(('\r\n'.join(commands)+'\r\n').encode('ascii'))
    (out/'release-downloads.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    return value


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(split(args.archive,args.out),indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
