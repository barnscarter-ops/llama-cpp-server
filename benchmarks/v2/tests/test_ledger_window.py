import json
import tempfile
import unittest
from pathlib import Path

import lane_fixture  # noqa: F401
from bench_v2.errors import WindowError
from bench_v2.ledger import Ledger, LedgerError
from bench_v2.window import LaneMutex, RunMarkers, WINDOW_TEMPLATE, load_window


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "l.jsonl"
        self.led = Ledger(self.path, clock=lambda: 1.0)
        for i in range(4):
            self.led.event("note", n=i)

    def test_chain_verifies(self):
        self.assertEqual(self.led.verify(), 4)

    def test_edit_is_detected(self):
        lines = self.path.read_text().splitlines()
        rec = json.loads(lines[1])
        rec["n"] = 99
        lines[1] = json.dumps(rec, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(LedgerError):
            self.led.verify()

    def test_deletion_is_detected(self):
        lines = self.path.read_text().splitlines()
        del lines[1]
        self.path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(LedgerError):
            self.led.verify()

    def test_append_continues_chain_after_reopen(self):
        Ledger(self.path, clock=lambda: 2.0).event("note", n=5)
        self.assertEqual(Ledger(self.path).verify(), 5)


class WindowTests(unittest.TestCase):
    def write(self, **over):
        doc = dict(WINDOW_TEMPLATE, start="2000-01-01T00:00:00Z", end="2099-01-01T00:00:00Z")
        doc.update(over)
        p = Path(tempfile.mkdtemp()) / "w.json"
        p.write_text(json.dumps(doc))
        return str(p)

    def test_missing_file_refused(self):
        with self.assertRaises(WindowError):
            load_window(None, "workbench")
        with self.assertRaises(WindowError):
            load_window("/nonexistent/window.json", "workbench")

    def test_valid_window_loads(self):
        w = load_window(self.write(), "workbench", now=1.8e9)
        self.assertTrue(w.allows("stop_services"))
        self.assertFalse(w.allows("qwen_load"))

    def test_wrong_lane_refused(self):
        with self.assertRaises(WindowError):
            load_window(self.write(), "r9700", now=1.8e9)

    def test_expired_and_future_refused(self):
        with self.assertRaises(WindowError):
            load_window(self.write(end="2001-01-01T00:00:00Z"), "workbench", now=1.8e9)
        with self.assertRaises(WindowError):
            load_window(self.write(start="2090-01-01T00:00:00Z"), "workbench", now=1.8e9)

    def test_missing_stop_approval_refused(self):
        w = self.write(approvals={"stop_services": False})
        with self.assertRaises(WindowError):
            load_window(w, "workbench", now=1.8e9)

    def test_r9700_needs_guardian_quiet(self):
        w = self.write(lane="r9700", approvals={"stop_services": True, "guardian_quiet": False})
        with self.assertRaises(WindowError):
            load_window(w, "r9700", now=1.8e9)

    def test_no_approver_refused(self):
        with self.assertRaises(WindowError):
            load_window(self.write(approved_by=""), "workbench", now=1.8e9)


class MarkerTests(unittest.TestCase):
    def test_stop_files(self):
        d = Path(tempfile.mkdtemp())
        m = RunMarkers(d, "workbench")
        self.assertFalse(m.stop_requested())
        (d / "workbench.STOP").write_text("")
        self.assertTrue(m.stop_requested())
        (d / "workbench.STOP").unlink()
        (d / "STOP").write_text("")
        self.assertTrue(m.stop_requested())

    def test_restore_marker_roundtrip(self):
        m = RunMarkers(tempfile.mkdtemp(), "r9700")
        m.mark_restore_required({"x": 1})
        self.assertTrue(m.restore_pending())
        m.clear_restore_required()
        self.assertFalse(m.restore_pending())


class MutexTests(unittest.TestCase):
    def test_second_holder_refused(self):
        p = Path(tempfile.mkdtemp()) / "swap.lock"
        a, b = LaneMutex(p), LaneMutex(p)
        a.acquire()
        try:
            with self.assertRaises(WindowError):
                b.acquire()
        finally:
            a.release()
        b.acquire()
        b.release()


if __name__ == "__main__":
    unittest.main()
