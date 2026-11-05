"""A3 intake readiness and effective camera time; never auto-accepts A3/A4/A5."""
from datetime import datetime, timezone
import math


def merged_seconds(intervals):
    """Union of verified recording intervals, so overlapping slices count once."""
    intervals = sorted((float(a), float(b)) for a,b in intervals if b>a)
    merged=[]
    for start,end in intervals:
        if merged and start<=merged[-1][1]:
            merged[-1][1]=max(end,merged[-1][1])
        else:
            merged.append([start,end])
    return sum(b-a for a,b in merged), merged


def check_field_package(package, verified_media, known_public_hashes=()):
    errors, gaps, cameras = [], [], {}
    videos=package.get('videos',[])
    if package.get('template_only'):
        return {'status':'template_only','a3_completed':False,'errors':['填写模板不能作为实际接收清单'],
                'gaps':[],'cameras':{},'effective_camera_hours':0}
    if not videos:
        return {'status':'awaiting_field_data','a3_completed':False,'errors':[],
                'gaps':['现场视频未收到','业务区域未确认','现场复核人员未落实'],
                'cameras':{},'effective_camera_hours':0}
    seen_hashes=set()
    for video in videos:
        vid=video.get('video_id'); cam=video.get('camera_id')
        prefix=str(vid or 'missing_video_id')
        media=verified_media.get(vid)
        if not vid or not cam:
            errors.append(prefix+': video_id/camera_id required');continue
        if video.get('source_kind')!='field_recording':
            errors.append(prefix+': public/synthetic media cannot be field data');continue
        if not media or not media.get('verified'):
            errors.append(prefix+': actual media file not verified');continue
        actual_sha=media.get('sha256')
        if actual_sha in known_public_hashes:
            errors.append(prefix+': known public development video cannot be relabeled as field');continue
        if actual_sha != video.get('sha256'):
            errors.append(prefix+': SHA256 mismatch');continue
        if actual_sha in seen_hashes:
            gaps.append(prefix+': duplicate file; effective hours not counted twice');continue
        seen_hashes.add(actual_sha)
        try:
            start=datetime.fromisoformat(video['recording_start'].replace('Z','+00:00'))
            if start.tzinfo is None:raise ValueError('timezone required')
            duration=float(media['duration_seconds'])
            if not math.isfinite(duration) or duration<=0:raise ValueError('invalid duration')
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(prefix+': recording_start/duration invalid: '+str(exc));continue
        entry=cameras.setdefault(cam,{'intervals':[],'videos':[]})
        entry['intervals'].append((start.timestamp(),start.timestamp()+duration));entry['videos'].append(vid)
    def valid_polygon(points):
        try:
            return len(points)>=3 and all(len(p)==2 and all(math.isfinite(float(v)) and 0<=float(v)<=1 for v in p) for p in points) and abs(sum(points[i][0]*points[(i+1)%len(points)][1]-points[(i+1)%len(points)][0]*points[i][1] for i in range(len(points))))>1e-9
        except (TypeError,ValueError,IndexError):return False
    zones={z.get('camera_id'):z for z in package.get('zones',[]) if z.get('business_confirmed') is True and z.get('purpose') and z.get('version') and valid_polygon(z.get('polygon',[]))}
    reviewers=[r for r in package.get('reviewers',[]) if r.get('id') and r.get('available_for_review') is True]
    if len(cameras)>2:gaps.append('超过首轮 1—2 路范围，请单独安排观察批次')
    for cam,entry in cameras.items():
        seconds,merged=merged_seconds(entry.pop('intervals'))
        entry.update(effective_seconds=seconds,effective_hours=round(seconds/3600,6),
                     first_epoch=merged[0][0],last_epoch=merged[-1][1],
                     span_hours=round((merged[-1][1]-merged[0][0])/3600,6),
                     gap_seconds=round(merged[-1][1]-merged[0][0]-seconds,6),intervals=merged)
        if cam not in zones:gaps.append(cam+': 业务区域用途与边界未确认')
        if entry['span_hours']<24:gaps.append(cam+': 尚未形成 1—2 天观察窗口')
    if not reviewers:gaps.append('现场复核人员未落实')
    ready=bool(cameras) and not errors and bool(reviewers) and all(cam in zones for cam in cameras)
    return {'status':'ready_for_observation' if ready else 'intake_incomplete','a3_completed':False,
            'errors':errors,'gaps':gaps,'cameras':cameras,
            'effective_camera_hours':round(sum(x['effective_seconds'] for x in cameras.values())/3600,6),
            'note':'接收就绪与 A3 完成分开；完成观察还需真实误报/漏报复核证据。'}
