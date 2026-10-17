import unittest

import cloud_gate as cg
import model_registry as mr

SPEC = mr.load_registry().get("deepseek-flash")


class CloudGateTests(unittest.TestCase):
    def code(self, authorized=True, clearance="internet"):
        r = cg.refusal(SPEC, authorized, clearance)
        return r[0] if r else None

    def test_allows_authorized_internet_clearance(self) -> None:
        self.assertIsNone(self.code())
        self.assertIsNone(self.code(clearance=" Internet "))

    def test_refuses_unauthorized_before_anything_else(self) -> None:
        self.assertEqual(self.code(authorized=False), "cloud_forbidden")

    def test_missing_or_blank_clearance(self) -> None:
        self.assertEqual(self.code(clearance=None), "clearance_required")
        self.assertEqual(self.code(clearance="  "), "clearance_required")

    def test_narrow_or_unknown_clearance_fails_closed(self) -> None:
        for c in ("none", "pc", "lan", "wide-open", "internet2"):
            self.assertEqual(self.code(clearance=c), "clearance_too_narrow", c)
