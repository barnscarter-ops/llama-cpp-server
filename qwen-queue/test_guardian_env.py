import tempfile
import unittest
from pathlib import Path

import guardian_env as ge


class GuardianEnvTests(unittest.TestCase):
    def test_parse_skips_comments_and_strips_quotes(self) -> None:
        self.assertEqual(ge.parse('# c\nA=1\nB = "two"\n\nbad\nC=\n'), {"A": "1", "B": "two", "C": ""})

    def test_real_env_wins_and_only_names_are_returned(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "g.env"
            f.write_text("X=file\nY=file\n")
            env = {"X": "real"}
            self.assertEqual(ge.load(env, f), ["Y"])
            self.assertEqual(env, {"X": "real", "Y": "file"})

    def test_missing_file_is_fine(self) -> None:
        self.assertEqual(ge.load({}, "/nonexistent/none.env"), [])

    def test_gaps_name_missing_tokens_only_for_enabled_features(self) -> None:
        self.assertEqual(ge.turn_on_gaps({}), [])
        gaps = ge.turn_on_gaps({"GUARDIAN_QWEN_APPROVAL": "true", "GUARDIAN_CLOUD": "true"})
        self.assertEqual(len(gaps), 2)
        self.assertEqual(ge.turn_on_gaps({"GUARDIAN_QWEN_APPROVAL": "true", "GUARDIAN_QWEN_APPROVAL_TOKEN": "t"}), [])
