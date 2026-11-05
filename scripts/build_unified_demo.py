#!/usr/bin/env python3
"""Run all three rules on ONE source, ONE shared target set and ONE track clock.

No platform configuration, ledger, legacy runs or GT files are modified.
The new output is an offline same-source engineering run, not benchmark scoring.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".cache/ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache/matplotlib"))
sys.path.insert(0, str(ROOT / "src"))
from st_memory.unified_rules import UnifiedRules


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'data/a2/unified/MOT20-05-raw.mp4')
    parser.add_argument('--run-id',default='unified-mot20-05-detect-pose-v2-001')
    parser.add_argument('--device',default='0')
    parser.add_argument('--ffmpeg',default=shutil.which('ffmpeg') or 'ffmpeg')
    args=parser.parse_args()
    import cv2
    import numpy as np
    import torch
    import supervision as sv
    from ultralytics import YOLO
    if args.device!='cpu' and not torch.cuda.is_available():
        raise SystemExit('CUDA unavailable in this execution context; run on the GPU-enabled host.')
    run=ROOT/'runs'/args.run_id
    if run.exists():raise SystemExit(f'Refusing to overwrite {run}')
    run.mkdir(parents=True)
    source=args.source.resolve(strict=True)
    weights=ROOT/'models/yolov8n-pose.pt'
    detector_weights=ROOT/'models/yolov8n.pt'
    cap=cv2.VideoCapture(str(source))
    fps=float(cap.get(cv2.CAP_PROP_FPS));total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH));height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps<=0 or total<=0:raise RuntimeError('Cannot read video metadata')
    stride=max(1,round(fps/5));sample_fps=fps/stride;period=stride/fps;duration=total/fps
    cfg={
        'version':'unified-causal-demo-v2','source_sha256':sha(source),'pose_weights_sha256':sha(weights),
        'detector_weights_sha256':sha(detector_weights),
        'roi':[.05,.05,.95,.95],'zone_id':'unified-public-observation-v1',
        'business_zone':False,'max_gap_seconds':.4,'stay_seconds':10.,
        'crowd':{'window_seconds':60.,'from_below':3,'to_at_least':6,'sustain_seconds':2.},
        'fall':{'conf_th':.5,'angle_th':35.,'aspect_th':1.1,'wide_aspect_th':1.4,
                'drop_th':.07,'min_duration':.5,'min_consecutive':3},
        'detector':'YOLOv8n','pose_model':'YOLOv8n-pose','pose_match_iou':.5,
        'tracker':'Supervision ByteTrack','imgsz':640,'confidence':.25,
        'analysis_fps':sample_fps,'video_fps':fps,'width':width,'height':height,
        'pose_adaptation':'per-track causal first-second baseline; trailing observations only; no centered median',
        'scope':'same-source multi-rule engineering demonstration; not field calibrated; no GT supplied',
    }
    dump(run/'config.json',cfg)
    print(json.dumps({'stage':'start','video_seconds':duration,'fps':fps,'sample_fps':sample_fps,
                      'source_sha256':cfg['source_sha256'],'GPU':torch.cuda.get_device_name(0) if args.device!='cpu' else 'cpu'}),flush=True)
    model=YOLO(str(weights));detector=YOLO(str(detector_weights))
    tracker=sv.ByteTrack(frame_rate=round(sample_fps),minimum_consecutive_frames=2)
    engine=UnifiedRules(cfg,args.run_id)
    out_width=960;out_height=int(height*out_width/width/2)*2
    encoder=subprocess.Popen([args.ffmpeg,'-hide_banner','-loglevel','error','-f','rawvideo','-pix_fmt','bgr24',
        '-s',f'{out_width}x{out_height}','-r',str(fps),'-i','pipe:0','-an','-c:v','libx264',
        '-preset','veryfast','-crf','25','-threads','2','-pix_fmt','yuv420p','-movflags','+faststart',
        '-y',str(run/'analysis.mp4')],stdin=subprocess.PIPE,stderr=(run/'encoder.log').open('w'))
    started=time.perf_counter();frames=[];last_people=[];all_ids=set();index=0
    predictions=(run/'observations.jsonl').open('w',encoding='utf-8')
    try:
        while True:
            ok,frame=cap.read()
            if not ok:break
            t=index/fps
            if index==0:cv2.imwrite(str(run/'source-preview.jpg'),frame)
            if index%stride==0:
                result=detector.predict(frame,imgsz=640,conf=.25,classes=[0],device=args.device,verbose=False)[0]
                pose_result=model.predict(frame,imgsz=640,conf=.25,classes=[0],device=args.device,verbose=False)[0]
                det=sv.Detections.from_ultralytics(result)
                if len(det):
                    det.data['pose']=np.zeros((len(det),17,3),dtype=np.float32)
                    pose_boxes=pose_result.boxes.xyxy.detach().cpu().numpy()
                    if len(pose_boxes):
                        xy=pose_result.keypoints.xy.detach().cpu().numpy();conf=pose_result.keypoints.conf.detach().cpu().numpy()
                        kp=np.concatenate((xy,conf[:,:,None]),axis=2)
                        ious=sv.box_iou_batch(det.xyxy,pose_boxes)
                        pairs=sorted([(float(ious[a,b]),a,b) for a in range(len(det)) for b in range(len(pose_boxes)) if ious[a,b]>=.5],reverse=True)
                        used_det=set();used_pose=set()
                        for _,a,b in pairs:
                            if a not in used_det and b not in used_pose:
                                det.data['pose'][a]=kp[b];used_det.add(a);used_pose.add(b)
                tracked=tracker.update_with_detections(det)
                people=[]
                for i,box in enumerate(tracked.xyxy):
                    identifier=int(tracked.tracker_id[i]);all_ids.add(identifier)
                    person={'id':identifier,'box':[round(float(v),6) for v in (box/np.array([width,height,width,height]))],
                            'confidence':round(float(tracked.confidence[i]),4),'pose':None}
                    if 'pose' in tracked.data:
                        points=tracked.data['pose'][i];shoulder=(points[5,:2]+points[6,:2])/2;hip=(points[11,:2]+points[12,:2])/2
                        vector=shoulder-hip
                        person['pose']={'angle':float(np.degrees(np.arctan2(abs(vector[1]),abs(vector[0])+1e-6))),
                                        'confidence':float(np.mean(points[[5,6,11,12],2]))}
                    people.append(person)
                row=engine.update(t,people,min(period,duration-t))
                predictions.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
                # UI receives the exact sampled observations, not new predictions.
                frames.append(row);last_people=people
                if len(frames)%50==0:print(json.dumps({'stage':'inference','video_time':round(t,1),'samples':len(frames),'candidates':len(engine.events)}),flush=True)
            display=cv2.resize(frame,(out_width,out_height))
            roi=cfg['roi'];cv2.rectangle(display,(int(roi[0]*out_width),int(roi[1]*out_height)),(int(roi[2]*out_width),int(roi[3]*out_height)),(190,210,40),2)
            for person in last_people:
                box=person['box'];a=(int(box[0]*out_width),int(box[1]*out_height));b=(int(box[2]*out_width),int(box[3]*out_height))
                color=(80,220,220) if person['dwell']>=cfg['stay_seconds'] else (110,210,70)
                cv2.rectangle(display,a,b,color,1)
                cv2.putText(display,f"{person['id']} {person['dwell']:.1f}s",(a[0],max(12,a[1]-3)),cv2.FONT_HERSHEY_SIMPLEX,.35,color,1,cv2.LINE_AA)
            if index==0:cv2.imwrite(str(run/'analysis-preview.jpg'),display)
            encoder.stdin.write(display.tobytes());index+=1
    finally:
        predictions.close();cap.release();encoder.stdin.close();code=encoder.wait(timeout=60)
    if code:raise RuntimeError(f'Video encoder failed: {code}')
    if index!=total:raise RuntimeError(f'Incomplete video decode: {index}/{total}')
    dump(run/'candidates.json',engine.events)
    counts=Counter(e['kind'] for e in engine.events)
    metadata={'schema':'same-source-three-rules-v1','run_id':args.run_id,'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'source':{'name':'MOT20-05 官方原序列视频','url':'https://motchallenge.net/data/MOT20/',
                  'video_url':'https://motchallenge.net/sequenceVideos/MOT20-05-raw.mp4','path':str(source.relative_to(ROOT)),
                  'sha256':cfg['source_sha256'],'duration':duration,'fps':fps,'frame_count':total,'width':width,'height':height,
                  'license':'CC BY-NC-SA 3.0; third-party source terms may also apply','repeated':False,'split':'development'},
        'config':cfg,'config_sha256':sha(run/'config.json'),'sample_count':len(frames),'track_count':len(all_ids),
        'wall_seconds':time.perf_counter()-started,'candidate_counts':{kind:counts.get(kind,0) for kind in ('stay','fall','crowd')},
        'frames':frames,'candidates':engine.events,'files':{},
        'platform_status':'new_same_source_run_not_connected_to_frigate',
        'limits':['Three rules share the same decoded frames and local tracker IDs; outputs are model predictions, not GT.',
                  'Fall rule is a multi-person causal engineering adaptation; original GMD benchmark metrics do not transfer.',
                  'No fall candidate is not proof that no fall occurred. Low-confidence poses are marked unavailable.',
                  'Crowd change uses a complete 60 s window; it is a count-based prompt, not confirmed spatial gathering.',
                  'Replay is offline. Video frames between inference samples display the most recent sampled boxes.']}
    media=ROOT/'demo/media/unified';media.mkdir(parents=True,exist_ok=True)
    for name,path in [('analysis',run/'analysis.mp4'),('original',source),('preview',run/'source-preview.jpg')]:
        filename={'analysis':'analysis.mp4','original':'original.mp4','preview':'preview.jpg'}[name]
        target=media/filename;shutil.copyfile(path,target)
        metadata['files'][name]={'url':'/media/unified/'+filename,'sha256':sha(target)}
    dump(run/'result.json',metadata);dump(ROOT/'demo/data/unified.json',metadata)
    print(json.dumps({'stage':'complete','samples':len(frames),'duration':duration,'candidates':metadata['candidate_counts'],
                      'wall_seconds':metadata['wall_seconds'],'replay_sha256':metadata['files']['analysis']['sha256']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
