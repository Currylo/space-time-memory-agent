#!/usr/bin/env python3
"""Offline clock holdout, clip/ledger identity checks, and scoped metadata update."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from st_memory.unified_platform import ClockSample, clock_segment_anchors, fit_clock, evidence_coverage
from unified_replay_round import LiveAligner,three_point_check


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def write(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temporary.replace(path)


def main():
    rd=ROOT/'reports/coverage-platform-20261105-r1'
    alignment=json.loads((rd/'alignment/clock_alignment.json').read_text())
    all_segments=alignment['live_alignment']['segments']
    segments=[s for s in all_segments if s['loop_index']==0]
    assert len(segments)==14
    # Fit only the middle 11 segments; the first/middle/final complete segment
    # are held out. First-segment 1 s timestamp offset stays visible.
    held={0,len(segments)//2,len(segments)-1}
    points=[]
    for i,segment in enumerate(segments):
        if i in held:continue
        anchors=clock_segment_anchors(segment,fps=25.)
        for key in ['start_anchor','coverage_end_anchor']:
            points.append(ClockSample(**anchors[key]))
    model=fit_clock(points)
    checks=[]
    for i in sorted(held):
        anchors=clock_segment_anchors(segments[i],fps=25.)
        for key in ['start_anchor','coverage_end_anchor']:
            x=anchors[key];checks.append({'segment_id':segments[i]['segment_id'],'anchor':key,
                'source_time':x['source_time'],'actual_epoch':x['platform_epoch'],
                'residual_seconds':x['platform_epoch']-model.platform_epoch(x['source_time'])})
    maximum=max(abs(x['residual_seconds']) for x in checks)
    holdout={'fit_segments':len(segments)-len(held),'held_out_segments':len(held),'points':checks,
             'numeric_max_abs_residual_seconds':maximum,'max_allowed_residual_seconds':1.0,
             'status':'verified' if maximum<=1+1e-6 else 'needs_review',
             'note':'First segment has about 1 s timestamp offset. No microsecond physical-clock or alarm-latency claim.'}
    aligner=object.__new__(LiveAligner);aligner.index=SimpleNamespace(fps=25.);aligner.segment_samples=segments
    updated=three_point_check(aligner,{'video_duration':132.6})
    alignment['three_point_check']=updated;alignment['holdout_check']=holdout
    write(rd/'alignment/clock_alignment.json',alignment)
    payload=json.loads((ROOT/'demo/data/unified_platform_coverage.json').read_text())
    manifest=json.loads((rd/'replay_manifest.json').read_text())
    payload['clock'].update(holdout_check=holdout,frame_period_seconds=.04,
        latency_seconds_used_in_injection=manifest['calibration_latency_seconds'],
        three_point_check=updated,physical_clock_accuracy_measured=False)
    if holdout['status']!='verified' or updated['status']!='verified':payload['clock']['status']='needs_review'
    connection=sqlite3.connect(f'file:{ROOT/"data/a2/adapter/ledger.sqlite"}?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    checks=[]
    for item in payload['candidates']:
        row=dict(connection.execute('select * from candidates where key=?',(item['ledger_key'],)).fetchone())
        clip=item['clip']
        assert row['event_id']==item['event_id'] and row['review_status']=='pending_review'
        assert sha(ROOT/clip['path'])==clip['sha256']
        assert item['loop_index']==0 and item['run_id']=='unified-mot20-05-coverage1280-rulesv3-20261105-003'
        assert all(x.get('methods_agree') is not False for x in clip['samples'])
        coverage=evidence_coverage(evidence_start=item['required_coverage_start'],evidence_end=item['required_coverage_end'],
            clip_source_start=clip['clip_source_start'],clip_source_end=clip['clip_source_end'],clip_duration=clip['duration'],
            clip_fps=clip['fps'],comparison_window_seconds=item['comparison_window_seconds'])
        assert coverage['status']=='verified' and coverage==clip['coverage']
        review_count=connection.execute('select count(*) from reviews where key=?',(item['ledger_key'],)).fetchone()[0]
        assert review_count==0
        checks.append({'candidate_id':item['candidate_id'],'event_id':item['event_id'],'hash_matches':True,
                       'coverage':coverage['status'],'review_count':review_count,
                       'intra_clip_residual':clip['max_abs_intra_residual']})
    prior=json.loads((ROOT/'reports/同源平台闭环终审-20261105/independent-check.json').read_text())['identities']
    for x in prior:
        row=connection.execute('select event_id from candidates where key=?',(x['ledger_key'],)).fetchone()
        assert row[0]==x['event_id']
        assert connection.execute('select count(*) from reviews where key=?',(x['ledger_key'],)).fetchone()[0]==0
    connection.close()
    write(ROOT/'demo/data/unified_platform_coverage.json',payload)
    write(rd/'platform/page_dataset_snapshot.json',payload)
    result={'new_candidates':len(checks),'old_18_preserved':True,'holdout':holdout,'three_point_check':updated,'checks':checks,
            'max_intra_clip_residual_seconds':max(x['intra_clip_residual'] for x in checks),
            'zero_real_reviews':True,'no_new_events_created_by_this_verifier':True}
    write(rd/'platform/offline-verification.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['checks','holdout','three_point_check']},ensure_ascii=False))


if __name__=='__main__':main()
