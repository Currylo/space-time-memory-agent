#!/usr/bin/env python3
"""Cross-profile and evidence rejection tests in temporary ledger copies only."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from st_memory.a2_adapter import Ledger, submit_candidate


def main():
    rd=ROOT/'reports/coverage-platform-20261105-r1'
    page=json.loads((ROOT/'demo/data/unified_platform_coverage.json').read_text())
    old=json.loads((ROOT/'demo/data/unified_platform.json').read_text())
    spec=importlib.util.spec_from_file_location('coverage_review_serve',ROOT/'demo/serve.py')
    serve=importlib.util.module_from_spec(spec);spec.loader.exec_module(serve)
    source=sqlite3.connect(f'file:{ROOT/"data/a2/adapter/ledger.sqlite"}?mode=ro',uri=True)
    before=source.execute('select count(*) from reviews').fetchone()[0]
    checks=[]
    for name in ['normal','tail_partial','clock_needs_review','wrong_source','wrong_hash','new_in_old_profile','old_in_new_profile']:
        with tempfile.TemporaryDirectory(prefix='coverage-review-') as temp:
            temp=Path(temp);target=sqlite3.connect(temp/'ledger.sqlite');source.backup(target)
            serve.REVIEW_LEDGER=temp/'ledger.sqlite';serve.REVIEW_LOG=temp/'reviews.jsonl'
            data=copy.deepcopy(page)
            if name=='tail_partial':data['candidates'][0]['clip']['coverage'].update(status='partial',missing_tail_seconds=.12)
            if name=='clock_needs_review':data['clock']['status']='needs_review'
            if name=='wrong_source':data['source']['sha256']='wrong-source'
            if name=='wrong_hash':data['candidates'][0]['clip']['sha256']='0'*64
            (temp/'coverage.json').write_text(json.dumps(data));(temp/'old.json').write_text(json.dumps(old))
            serve.COVERAGE_PLATFORM=temp/'coverage.json';serve.UNIFIED_PLATFORM=temp/'old.json'
            handler=object.__new__(serve.DemoHandler)
            handler.path='/api/review/submit?profile='+('baseline' if name=='new_in_old_profile' else 'coverage')
            item=(old if name=='old_in_new_profile' else page)['candidates'][0]
            body=json.dumps({'candidate_id':item['candidate_id'],'key':item['ledger_key'],
                'reviewer':'isolated_test_only','decision':'uncertain','note':'Isolated API handler check, not a real human review.'}).encode()
            handler.headers={'Content-Length':str(len(body))};handler.rfile=io.BytesIO(body)
            output={};handler._send_json=lambda data,status=200:output.update(status=int(status),data=data)
            handler._submit_review()
            after=target.execute('select count(*) from reviews').fetchone()[0];target.close()
            expected=200 if name=='normal' else 404 if name in ['new_in_old_profile','old_in_new_profile'] else 409
            assert output['status']==expected and after-before==(1 if name=='normal' else 0),(name,output)
            checks.append({'name':name,'status':output['status'],'test_review_delta':after-before,'errors':output['data'].get('errors',[])})
    assert source.execute('select count(*) from reviews').fetchone()[0]==before
    source.close()
    # Exercise duplicate handling with the actual persistent ledger. A client
    # that raises on every method proves this path cannot send another POST.
    class NoNetwork:
        def __getattr__(self,name):raise AssertionError('Unexpected platform request: '+name)
    ledger=Ledger(ROOT/'data/a2/adapter/ledger.sqlite')
    counts_before={t:ledger.connection.execute(f'select count(*) from {t}').fetchone()[0] for t in ['candidates','attempts','reviews']}
    duplicates=[]
    for item in page['candidates']:
        row=ledger.get(item['ledger_key']);candidate=json.loads(row['candidate_json'])
        result=submit_candidate(ledger,NoNetwork(),candidate,duration=3,pre_capture=12,wait_clip=False,wait_description=False)
        assert result['state']=='duplicate' and result['event_id']==item['event_id']
        duplicates.append(result)
    counts_after={t:ledger.connection.execute(f'select count(*) from {t}').fetchone()[0] for t in counts_before}
    ledger.close();assert counts_before==counts_after
    output={'isolated_handler_checks':checks,'production_review_count':before,'duplicates':duplicates,
            'before':counts_before,'after':counts_after,'new_platform_calls':0}
    (rd/'platform/review-and-idempotency.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'isolated_checks':len(checks),'duplicate_checks':len(duplicates),'new_platform_calls':0,'ledger_counts':counts_after}))


if __name__=='__main__':main()
