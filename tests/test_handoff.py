"""Checks for untrusted archive paths and isolated public-demo ledger import."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from st_memory.a2_adapter import Ledger, candidate_key  # noqa: E402


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


restore = module("restore_handoff")
init = module("init_handoff_ledger")


class HandoffTests(unittest.TestCase):
    def test_archive_escape_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "root"
            root.mkdir()
            for name in ("../outside", "/absolute", "..\\outside"):
                with self.assertRaises(ValueError):
                    restore.destination(root, name)
            (root / "link").symlink_to(Path(d), target_is_directory=True)
            with self.assertRaises(ValueError):
                restore.destination(root, "link/outside")

    def test_verified_archive_restores_without_overwriting_changed_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "root"
            root.mkdir()
            archive = Path(d) / "asset.tar.gz"
            body = b"public asset"
            entry = {"path": "demo/data/sample.json", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo(entry["path"])
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
            restore.restore_archive(archive, root, [entry])
            restore.restore_archive(archive, root, [entry])
            target = root / entry["path"]
            target.write_bytes(b"local changes")
            with self.assertRaises(ValueError):
                restore.restore_archive(archive, root, [entry])
            self.assertEqual(target.read_bytes(), b"local changes")

    def test_archive_rejects_unlisted_members(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "asset.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("unexpected")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                restore.restore_archive(archive, Path(d), [])

    def test_seed_import_has_no_reviews_and_refuses_existing_database(self):
        candidate = {
            "candidate_id": "unit-candidate", "source_video_sha256": "a" * 64,
            "camera": "unit-camera", "label": "person", "rule_id": "stay",
            "rule_version": "v1", "zone_id": "unit-zone", "zone_version": "v1",
            "run_id": "unit-run", "loop_index": 0, "track_id": 1,
            "source_video_time": 1.0, "condition_met_video_time": 2.0,
            "evidence_start": 0.0, "evidence_end": 2.0, "score": None,
            "replay_id": "unit-replay",
        }
        key = candidate_key(candidate)
        with tempfile.TemporaryDirectory() as d:
            seed = Path(d) / "seed.json"
            output = Path(d) / "ledger.sqlite"
            seed.write_text(json.dumps({"schema": "st-memory-handoff-ledger-seed-v1", "source_commit": "unit", "candidates": [{"key": key, "candidate": candidate, "event": {"id": "existing-unit-event", "has_clip": True}, "source": "unit-snapshot"}]}))
            self.assertEqual(init.initialize(seed, output), 1)
            ledger = Ledger(output)
            try:
                self.assertEqual(ledger.get(key)["review_status"], "pending_review")
                self.assertEqual(ledger.reviews(key), [])
                self.assertEqual(ledger.attempts(key), [])
                self.assertEqual(ledger.connection.execute("SELECT count(*) FROM imports").fetchone()[0], 1)
            finally:
                ledger.close()
            old = output.read_bytes()
            with self.assertRaises(FileExistsError):
                init.initialize(seed, output)
            self.assertEqual(output.read_bytes(), old)


if __name__ == "__main__":
    unittest.main()
