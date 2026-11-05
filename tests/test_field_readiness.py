import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from st_memory.field_readiness import check_field_package, merged_seconds


class FieldReadinessTests(unittest.TestCase):
    def package(self):
        return {'videos':[{'video_id':'fixture','camera_id':'test-camera','source_kind':'field_recording',
                 'sha256':'a'*64,'recording_start':'2026-11-05T00:00:00+08:00'}],
                'zones':[{'camera_id':'test-camera','business_confirmed':True,'purpose':'test fixture only',
                          'version':'fixture','polygon':[[0,0],[1,0],[1,1]]}],
                'reviewers':[{'id':'test-only','available_for_review':True}]}

    def media(self):return {'fixture':{'verified':True,'sha256':'a'*64,'duration_seconds':300}}

    def test_empty_does_not_report_a3_complete(self):
        x=check_field_package({},{});self.assertEqual(x['status'],'awaiting_field_data');self.assertFalse(x['a3_completed'])

    def test_overlapping_slices_are_not_double_counted(self):
        seconds,parts=merged_seconds([(0,120),(60,180),(200,240)])
        self.assertEqual(seconds,220);self.assertEqual(len(parts),2)

    def test_public_video_cannot_become_field_by_renaming(self):
        x=check_field_package(self.package(),self.media(),{'a'*64});self.assertTrue(x['errors'])

    def test_unprobed_media_is_not_ready(self):
        x=check_field_package(self.package(),{});self.assertEqual(x['status'],'intake_incomplete')

    def test_missing_timezone_is_rejected(self):
        p=self.package();p['videos'][0]['recording_start']='2026-11-05T00:00:00'
        self.assertTrue(check_field_package(p,self.media())['errors'])

    def test_ready_is_not_observation_completion(self):
        x=check_field_package(self.package(),self.media());self.assertEqual(x['status'],'ready_for_observation')
        self.assertFalse(x['a3_completed']);self.assertAlmostEqual(x['effective_camera_hours'],300/3600,places=6)

    def test_outside_frame_polygon_not_ready(self):
        p=self.package();p['zones'][0]['polygon']=[[0,0],[2,0],[1,1]]
        self.assertEqual(check_field_package(p,self.media())['status'],'intake_incomplete')

    def test_blank_template_never_ready(self):
        p=self.package();p['template_only']=True
        self.assertEqual(check_field_package(p,self.media())['status'],'template_only')
