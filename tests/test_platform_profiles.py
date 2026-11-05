import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from unified_replay_round import CameraController


class PlatformProfileTests(unittest.TestCase):
    def test_payload_profile_selects_only_fixed_files(self):
        spec=importlib.util.spec_from_file_location('profile_test_serve',ROOT/'demo/serve.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'base.json';b=Path(d)/'coverage.json'
            a.write_text(json.dumps({'run':'old'}));b.write_text(json.dumps({'run':'new'}))
            module.UNIFIED_PLATFORM=a;module.COVERAGE_PLATFORM=b
            handler=object.__new__(module.DemoHandler)
            handler.path='/api/unified/platform';self.assertEqual(handler._unified_payload()['run'],'old')
            handler.path='/api/unified/platform?profile=coverage';self.assertEqual(handler._unified_payload()['run'],'new')
            handler.path='/api/unified/platform?profile=../../wrong';self.assertIsNone(handler._unified_payload())
