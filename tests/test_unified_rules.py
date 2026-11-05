import copy
import unittest
from st_memory.unified_rules import UnifiedRules


CONFIG = {"source_sha256":"test-only", "zone_id":"test-zone", "version":"test-v1",
          "roi":[0,0,1,1], "max_gap_seconds":.4, "stay_seconds":10.,
          "crowd":{"window_seconds":60., "from_below":3, "to_at_least":6,"sustain_seconds":2.},
          "fall":{"conf_th":.5,"angle_th":35.,"aspect_th":1.1,"wide_aspect_th":1.4,
                  "drop_th":.07,"min_duration":.5,"min_consecutive":3}}


def people(n):
    return [{"id":i,"box":[.1,.1,.2,.5],"pose":None} for i in range(n)]


class UnifiedRulesTests(unittest.TestCase):
    def test_stay_only_after_frame_coverage_threshold(self):
        e=UnifiedRules(copy.deepcopy(CONFIG),'test')
        for k in range(49):e.update(k*.2,people(1),.2)
        self.assertEqual(e.events,[])
        e.update(9.8,people(1),.2)
        self.assertEqual(e.events[0]['condition_met_video_time'],10.)
        self.assertEqual(e.events[0]['evidence_frame_video_time'],9.8)

    def test_gap_breaks_dwell(self):
        e=UnifiedRules(copy.deepcopy(CONFIG),'test')
        for k in range(45):e.update(k*.2,people(1),.2)
        e.update(10.,people(1),.2)
        self.assertEqual(e.states[0]['dwell'],.2)
        self.assertEqual(e.events,[])

    def test_empty_frames_preserve_zero_and_full_window(self):
        e=UnifiedRules(copy.deepcopy(CONFIG),'test')
        for k in range(310):e.update(k*.2,people(0 if k<300 else 6),.2)
        crowd=[r for r in e.events if r['kind']=='crowd']
        self.assertEqual(len(crowd),1)
        self.assertEqual(crowd[0]['features']['from_count'],0)
        self.assertGreaterEqual(crowd[0]['condition_met_video_time'],62.)

    def test_future_frames_do_not_change_past_candidates(self):
        a=UnifiedRules(copy.deepcopy(CONFIG),'same');b=UnifiedRules(copy.deepcopy(CONFIG),'same')
        for k in range(70):a.update(k*.2,people(1),.2)
        past=copy.deepcopy(a.events)
        for k in range(100):b.update(k*.2,people(1),.2)
        self.assertEqual(past,[e for e in b.events if e['condition_met_video_time']<=14.])

    def test_missing_pose_is_not_fall(self):
        e=UnifiedRules(copy.deepcopy(CONFIG),'test')
        for k in range(25):
            p=people(1);p[0]['box']=[.1,.6,.9,.7] if k>5 else [.1,.1,.2,.4]
            row=e.update(k*.2,p,.2)
            self.assertEqual(row['pose_valid'],0)
        self.assertFalse(any(v['kind']=='fall' for v in e.events))

    def test_short_video_does_not_shorten_count_window(self):
        e=UnifiedRules(copy.deepcopy(CONFIG),'test')
        for k in range(100):row=e.update(k*.2,people(0 if k<50 else 6),.2)
        self.assertFalse(row['crowd_ready'])
        self.assertFalse(any(v['kind']=='crowd' for v in e.events))

    def test_tracker_warmup_is_not_an_empty_region_for_count_change(self):
        e=UnifiedRules(copy.deepcopy(CONFIG),'test')
        e.update(0.,[],.2,count_observable=False)
        for k in range(1,302):row=e.update(k*.2,people(6),.2)
        self.assertFalse(any(v['kind']=='crowd' for v in e.events))
        self.assertTrue(row['count_window_observable'])

    def test_unknown_count_inside_window_blocks_false_growth(self):
        e=UnifiedRules(copy.deepcopy(CONFIG),'test')
        for k in range(311):
            row=e.update(k*.2,people(0 if k<300 else 6),.2,count_observable=(k!=200))
        self.assertFalse(row['count_window_observable'])
        self.assertFalse(any(v['kind']=='crowd' for v in e.events))

    def test_valid_pose_and_observed_posture_create_candidate(self):
        e=UnifiedRules(copy.deepcopy(CONFIG),'test')
        for k in range(11):
            p=people(1)
            p[0]['pose']={'confidence':.9,'angle':80. if k<6 else 10.}
            if k>=6:p[0]['box']=[.1,.6,.9,.8]
            e.update(k*.2,p,.2)
        falls=[v for v in e.events if v['kind']=='fall']
        self.assertEqual(len(falls),1)
        self.assertEqual(falls[0]['review_status'],'pending_review')
        self.assertGreaterEqual(falls[0]['features']['observed_posture_seconds'],.5)


if __name__=='__main__':unittest.main()
