#!/usr/bin/env python3
"""Local browser checks for coverage view and accepted baseline (no review POST)."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connected',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/person-coverage-20261105/browser')
    args=parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    ledger = sqlite3.connect(f"file:{ROOT/'data/a2/adapter/ledger.sqlite'}?mode=ro", uri=True)
    before = {t:ledger.execute(f"select count(*) from {t}").fetchone()[0] for t in ("candidates","attempts","reviews")}
    checks, errors, posts = [], [], []
    data=json.loads((ROOT/'demo/data/unified_coverage1280.json').read_text())
    def check(name, passed, detail=None):
        checks.append({"name":name,"passed":bool(passed),"detail":detail})
        print(('PASS ' if passed else 'FAIL ')+name,flush=True)
    def screenshot(page, name):
        page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(200)
        page.screenshot(path=str(output/name),full_page=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
        context = browser.new_context(viewport={"width":1440,"height":1000},record_video_dir=str(output),record_video_size={"width":1440,"height":1000})
        page = context.new_page()
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
        page.on('request',lambda r:posts.append(r.url) if r.method=='POST' else None)
        page.goto('http://127.0.0.1:8765/showcase.html',wait_until='networkidle')
        page.wait_for_function("!document.getElementById('play').disabled")
        check('enhanced_profile_is_default',page.locator('#profileSelect').input_value()=='coverage')
        check('no_initial_error',page.locator('#error').is_hidden())
        check('raw_detected_even_before_track_activation',page.locator('#detected').inner_text()=='52' and page.locator('#tracked').inner_text()=='0')
        check('h264_main_media', 'original_h264.mp4' in page.locator('#sceneVideo').get_attribute('src'))
        banner=page.locator('#platformSummary').inner_text()
        check('new_run_not_using_old_platform_events',('88 条平台候选事件' in banner if args.connected else '增强分析' in banner) and '18 条平台候选事件' not in banner)
        page.locator('#seek').evaluate("el=>{el.value=40;el.dispatchEvent(new Event('input'));}")
        page.wait_for_timeout(400)
        check('separate_detection_tracking_and_roi_counts', [page.locator('#'+k).inner_text() for k in ('detected','tracked','zoneCount')]==['93','47','43'])
        check('overlay_has_actual_boxes',page.locator('#overlay').evaluate("c=>{const a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;return a.some((v,i)=>i%4===3&&v>0);}"))
        page.locator('.person-inspector summary').click()
        check('every_current_track_listed',page.locator('#personList button').count()==47)
        page.locator('#personList button').first.click()
        check('individual_dwell_visible','区域内已观测' in page.locator('#personSummary').inner_text())
        partial=next(r for r in data['frames'] if 0<r['pose_valid']<r['tracked'])
        page.locator('#seek').evaluate("(el,t)=>{el.value=t;el.dispatchEvent(new Event('input'));}",partial['time'])
        page.wait_for_timeout(200)
        check('partial_pose_does_not_claim_all_people_observable','部分目标姿态不足' in page.locator('#fallStatus').inner_text())
        page.locator('#seek').evaluate("el=>{el.value=40;el.dispatchEvent(new Event('input'));}")
        page.wait_for_timeout(200)
        screenshot(page,'01-enhanced-40s.png')
        page.locator('#play').click();page.wait_for_timeout(800)
        t1=page.locator('#sceneVideo').evaluate('v=>v.currentTime')
        check('play_advances',t1>40.3,t1)
        page.locator('#play').click();page.wait_for_timeout(300)
        t2=page.locator('#sceneVideo').evaluate('v=>v.currentTime');page.wait_for_timeout(400)
        check('pause_holds',abs(page.locator('#sceneVideo').evaluate('v=>v.currentTime')-t2)<.05)
        page.locator('#originalMode').click();page.wait_for_timeout(300)
        check('original_switch_preserves_time',abs(page.locator('#sceneVideo').evaluate('v=>v.currentTime')-t2)<.15)
        check('original_mode_clears_overlay',page.locator('#overlay').evaluate("c=>!c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)"))
        page.locator('#analysisMode').click();page.wait_for_timeout(300)
        check('enhanced_candidates_match_run',page.locator('#candidateList button').count()==len(data['candidates']))
        page.locator('#candidateList button').first.click();page.wait_for_timeout(350)
        if args.connected:
            page.wait_for_function("document.getElementById('evidenceVideo').readyState>=1")
            check('new_run_has_own_platform_clip',page.locator('#evidenceVideo').is_visible() and 'profile=coverage' in page.locator('#evidenceVideo').get_attribute('src'))
            check('new_review_form_empty_and_available',not page.locator('#submitReview').is_disabled() and page.locator('#reviewer').input_value()=='' and page.locator('#decision').input_value()=='')
            ev=page.locator('#evidenceVideo');ev.evaluate('v=>v.play()');page.wait_for_timeout(700)
            check('new_platform_clip_plays',ev.evaluate('v=>v.currentTime')>.5)
            ev.evaluate('v=>{v.pause();v.currentTime=8;}');page.wait_for_timeout(400)
            check('new_platform_clip_seek',abs(ev.evaluate('v=>v.currentTime')-8)<.2)
        else:
            check('local_evidence_not_fabricated_platform_clip',page.locator('#evidenceVideo').is_hidden() and '尚未写入平台' in page.locator('#evidenceMessage').inner_text())
            check('new_candidate_review_disabled',page.locator('#submitReview').is_disabled())
        expected=data['candidates'][0]['rule_window_start']
        page.locator('#playWindow').click();page.wait_for_timeout(400)
        t=page.locator('#sceneVideo').evaluate('v=>v.currentTime')
        check('local_evidence_play_starts_at_causal_window',expected<=t<expected+1.2,{'start':expected,'actual':t})
        page.locator('#play').click()
        screenshot(page,'02-local-evidence.png')
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250)
        check('mobile_no_horizontal_overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        screenshot(page,'03-mobile.png')
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('#profileSelect').select_option('baseline')
        page.wait_for_load_state('networkidle');page.wait_for_function("!document.getElementById('play').disabled")
        check('baseline_switch_preserves_video_time',abs(page.locator('#sceneVideo').evaluate('v=>v.currentTime')-t)<1)
        check('baseline_has_original_18_platform_cases',page.locator('#candidateList button').count()==18 and '18 条平台候选事件' in page.locator('#platformSummary').inner_text())
        page.locator('#candidateList button').first.click();page.wait_for_timeout(500)
        check('baseline_platform_clip_still_loads',page.locator('#evidenceVideo').is_visible() and page.locator('#evidenceVideo').evaluate('v=>v.readyState>=1'))
        check('baseline_review_not_prefilled',page.locator('#reviewer').input_value()=='' and page.locator('#decision').input_value()=='')
        screenshot(page,'04-baseline-platform.png')
        page.goto('http://127.0.0.1:8765/field.html',wait_until='networkidle')
        page.wait_for_function("document.getElementById('status').textContent!=='读取中…'")
        check('a3_waiting_field_materials','等待现场材料' in page.locator('#status').inner_text())
        check('a3_effective_hours_zero','0.00' in page.locator('#summary').inner_text())
        screenshot(page,'05-field-readiness.png')
        check('no_post_requests',not posts,posts)
        check('no_browser_errors',not errors,errors)
        video_record=page.video
        context.close()
        if video_record:video_record.save_as(str(output/'browser-session.webm'))
        browser.close()
    after={t:ledger.execute(f'select count(*) from {t}').fetchone()[0] for t in before}
    ledger.close();check('production_ledger_unchanged',before==after,{'before':before,'after':after})
    result={'checks':checks,'passed':sum(x['passed'] for x in checks),'failed':sum(not x['passed'] for x in checks),'errors':errors,'posts':posts}
    (output/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return 1 if result['failed'] else 0


if __name__=='__main__':
    raise SystemExit(main())
