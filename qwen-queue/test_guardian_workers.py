"""Offline validation tests for guardian-managed worker admission."""

import os
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

from guardian_workers import _pi_command, is_worker_job, validate_worker_submission, worker_command


class GuardianWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_root = os.environ.get("LOCAL_WORKER_ROOT")
        os.environ["LOCAL_WORKER_ROOT"] = self.temp_dir.name

    def tearDown(self):
        if self.original_root is None:
            os.environ.pop("LOCAL_WORKER_ROOT", None)
        else:
            os.environ["LOCAL_WORKER_ROOT"] = self.original_root
        self.temp_dir.cleanup()

    def _payload(self):
        return {
            "source": "hermes",
            "idempotency_key": "worker-test",
            "work_class": "tool_execution",
            "workspace": self.temp_dir.name,
            "task": "Make one documented change.",
            "parent_run_id": "parent-123",
            "priority": 70,
            "timeout_seconds": 600,
        }

    def test_admits_work_class_and_builds_fixed_pi_command(self):
        submitted = validate_worker_submission(self._payload())
        spec = submitted["worker"]
        with patch("guardian_workers._pi_command", return_value=["pi"]):
            command, workspace = worker_command(spec)
        self.assertEqual(Path(self.temp_dir.name).resolve(), workspace)
        self.assertEqual("pi", command[0])
        self.assertIn("llamacpp/local-llm", command)
        self.assertIn("--no-extensions", command)
        self.assertIn("--approve", command)
        self.assertTrue(is_worker_job({"_guardian_worker": spec}))

    def test_windows_uses_hermes_managed_pi_runtime_without_path_lookup(self):
        hermes_home = Path(self.temp_dir.name) / "hermes" / "node"
        cli = hermes_home / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "cli.js"
        cli.parent.mkdir(parents=True)
        (hermes_home / "node.exe").touch()
        cli.touch()
        with patch.dict(os.environ, {"LOCALAPPDATA": self.temp_dir.name}, clear=False), patch("guardian_workers.sys.platform", "win32"), patch("guardian_workers.shutil.which") as which:
            self.assertEqual([str(hermes_home / "node.exe"), str(cli)], _pi_command())
        which.assert_not_called()

    def test_rejects_unknown_class_and_workspace_outside_root(self):
        unknown = self._payload()
        unknown["work_class"] = "anything-goes"
        with self.assertRaisesRegex(ValueError, "Unknown work_class"):
            validate_worker_submission(unknown)

        outside = self._payload()
        outside["workspace"] = str(Path(self.temp_dir.name).parent)
        with self.assertRaisesRegex(ValueError, "inside the configured worker root"):
            validate_worker_submission(outside)

    def test_rejects_direct_route_and_quality_escalates(self):
        direct = self._payload()
        direct["model"] = "raw-alias"
        with self.assertRaisesRegex(ValueError, "guardian-owned"):
            validate_worker_submission(direct)
        frontier = self._payload()
        frontier["quality_floor"] = "frontier"
        self.assertEqual("requires_cloud", validate_worker_submission(frontier)["admission"])

    def test_consult_requires_gate_and_never_admits_local_runner(self):
        planning = self._payload()
        planning["work_class"] = "planning"
        self.assertEqual("gate_required", validate_worker_submission(planning)["admission"])
        planning["gate"] = "operator"
        self.assertEqual("consult_unavailable", validate_worker_submission(planning)["admission"])


if __name__ == "__main__":
    unittest.main()
