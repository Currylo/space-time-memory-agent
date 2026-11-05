#!/usr/bin/env python3
"""Read-only probe of submitted field recordings and A3 intake gaps."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from st_memory.field_readiness import check_field_package


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'data/a3/intake.json')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/A3接收就绪检查.json')
    args=parser.parse_args();package=json.loads(args.input.read_text());verified={}
    if package.get('videos'):
        import cv2
        for video in package['videos']:
            path=Path(video.get('path',''));path=path if path.is_absolute() else ROOT/path
            if not path.is_file():continue
            h=hashlib.sha256()
            with path.open('rb') as handle:
                for b in iter(lambda:handle.read(1024*1024),b''):h.update(b)
            cap=cv2.VideoCapture(str(path));fps=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
            ok,_=cap.read();cap.release()
            verified[video.get('video_id')]={'verified':bool(ok and fps>0 and count>0),'sha256':h.hexdigest(),
                'duration_seconds':count/fps if fps>0 else 0}
    known={'e5d1099517a3d5fe8cded2035fce54ff641171b3be5b97cfcb774db90a64bc8e'}
    catalog=ROOT/'data/e01/videos.jsonl'
    if catalog.is_file():
        for line in catalog.read_text().splitlines():
            if line.strip():
                row=json.loads(line)
                for field in ('sha256','video_sha256'):
                    if row.get(field):known.add(row[field])
    result=check_field_package(package,verified,known)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
    return 1 if result['errors'] else 0


if __name__=='__main__':raise SystemExit(main())
