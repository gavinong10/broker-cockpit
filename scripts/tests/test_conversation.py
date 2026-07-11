"""Tests for scripts/lib/conversation.py — stdlib unittest only.

No live `claude` or ssh calls: subprocess.run is monkeypatched.
Run: python3 -m unittest discover scripts/tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make scripts/lib importable regardless of cwd
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from lib import conversation  # noqa: E402


def _user_line(text):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def _user_blocks_line(blocks):
    return json.dumps({"type": "user", "message": {"role": "user", "content": blocks}})


def _assistant_line(blocks):
    return json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": blocks}}
    )


def _write_jsonl(lines):
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    tmp.write("\n".join(lines) + "\n")
    tmp.close()
    return Path(tmp.name)


class TestExtractText(unittest.TestCase):
    def test_basic_extraction(self):
        path = _write_jsonl(
            [
                json.dumps({"type": "ai-title", "aiTitle": "noise"}),
                _user_line("Let us design a covered call strategy"),
                _assistant_line(
                    [
                        {"type": "text", "text": "Sure, here is the plan."},
                        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                    ]
                ),
                # tool_result user message (list content) must be skipped
                _user_blocks_line(
                    [{"type": "tool_result", "tool_use_id": "t1", "content": "output"}]
                ),
                # "<"-prefixed user string skipped (system-reminder etc.)
                _user_line("<system-reminder>ignore me</system-reminder>"),
                # too-short user string skipped (len <= 10)
                _user_line("ok"),
                _user_line("Second real user message about strikes"),
            ]
        )
        out = conversation.extract_text(path)
        self.assertIn("USER: Let us design a covered call strategy", out)
        self.assertIn("USER: Second real user message about strikes", out)
        self.assertIn("ASSISTANT: Sure, here is the plan.", out)
        self.assertNotIn("tool_result", out)
        self.assertNotIn("Bash", out)
        self.assertNotIn("system-reminder", out)
        self.assertNotIn("USER: ok", out)
        # chronological: first user before assistant before second user
        self.assertLess(
            out.index("covered call"), out.index("ASSISTANT: Sure")
        )
        self.assertLess(out.index("ASSISTANT: Sure"), out.index("Second real"))

    def test_assistant_block_truncated_to_1500(self):
        long_text = "A" * 5000
        path = _write_jsonl(
            [
                _user_line("please write something long"),
                _assistant_line([{"type": "text", "text": long_text}]),
            ]
        )
        out = conversation.extract_text(path)
        # the assistant payload appears, truncated to exactly 1500 chars of A
        self.assertIn("A" * 1500, out)
        self.assertNotIn("A" * 1501, out)

    def test_over_budget_drops_assistant_oldest_first_never_user(self):
        users = [f"user message number {i} with plenty of chars" for i in range(5)]
        lines = []
        for i, u in enumerate(users):
            lines.append(_user_line(u))
            lines.append(
                _assistant_line([{"type": "text", "text": f"reply-{i} " + "x" * 1400}])
            )
        path = _write_jsonl(lines)
        # budget fits all users + roughly the last 2 assistant blocks only
        out = conversation.extract_text(path, max_chars=3500)
        self.assertLessEqual(len(out), 3500)
        for u in users:
            self.assertIn("USER: " + u, out)  # ALL user messages kept
        # oldest assistant blocks dropped first
        self.assertNotIn("reply-0", out)
        self.assertNotIn("reply-1", out)
        self.assertIn("reply-4", out)  # newest assistant block survives

    def test_multiple_text_blocks_in_one_assistant_message(self):
        path = _write_jsonl(
            [
                _user_line("a real question about markets"),
                _assistant_line(
                    [
                        {"type": "text", "text": "first block"},
                        {"type": "thinking", "thinking": "hidden"},
                        {"type": "text", "text": "second block"},
                    ]
                ),
            ]
        )
        out = conversation.extract_text(path)
        self.assertIn("ASSISTANT: first block", out)
        self.assertIn("ASSISTANT: second block", out)
        self.assertNotIn("hidden", out)


class TestFindTranscript(unittest.TestCase):
    def test_found(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj = root / "-some-project"
            proj.mkdir()
            sid = "abc-123"
            target = proj / f"{sid}.jsonl"
            target.write_text("{}\n")
            with mock.patch.object(conversation, "PROJECTS_ROOT", root):
                self.assertEqual(conversation.find_transcript(sid), target)

    def test_missing_raises_with_root_listed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(conversation, "PROJECTS_ROOT", root):
                with self.assertRaises(FileNotFoundError) as cm:
                    conversation.find_transcript("nope")
                self.assertIn(str(root), str(cm.exception))


class TestRunClaudeJson(unittest.TestCase):
    def _completed(self, stdout="", returncode=0, stderr=""):
        return mock.Mock(stdout=stdout, returncode=returncode, stderr=stderr)

    def test_parses_envelope_with_json_embedded_in_prose(self):
        manifest = {"slug": "test-basket", "legs": [{"symbol_or_underlying": "SPY"}]}
        envelope = json.dumps(
            {
                "type": "result",
                "result": "Here is the manifest:\n```json\n"
                + json.dumps(manifest)
                + "\n```\nHope that helps!",
            }
        )
        with mock.patch.object(
            conversation.subprocess,
            "run",
            return_value=self._completed(stdout=envelope),
        ) as run:
            out = conversation.run_claude_json("make a manifest")
        self.assertEqual(out, manifest)
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "claude")
        self.assertIn("--output-format", argv)

    def test_bare_json_result(self):
        manifest = {"slug": "s", "name": "n"}
        envelope = json.dumps({"result": json.dumps(manifest)})
        with mock.patch.object(
            conversation.subprocess,
            "run",
            return_value=self._completed(stdout=envelope),
        ):
            self.assertEqual(conversation.run_claude_json("p"), manifest)

    def test_nonzero_exit_raises_with_stderr_excerpt(self):
        with mock.patch.object(
            conversation.subprocess,
            "run",
            return_value=self._completed(returncode=1, stderr="boom " * 200),
        ):
            with self.assertRaises(RuntimeError) as cm:
                conversation.run_claude_json("p")
        self.assertIn("boom", str(cm.exception))
        self.assertLessEqual(len(str(cm.exception)), 700)  # excerpt truncated

    def test_no_json_object_in_result_raises(self):
        envelope = json.dumps({"result": "I could not produce a manifest, sorry."})
        with mock.patch.object(
            conversation.subprocess,
            "run",
            return_value=self._completed(stdout=envelope),
        ):
            with self.assertRaises(RuntimeError):
                conversation.run_claude_json("p")

    def test_unparseable_envelope_raises(self):
        with mock.patch.object(
            conversation.subprocess,
            "run",
            return_value=self._completed(stdout="not json at all"),
        ):
            with self.assertRaises(RuntimeError):
                conversation.run_claude_json("p")

    def test_long_prompt_goes_via_stdin(self):
        manifest = {"slug": "s"}
        envelope = json.dumps({"result": json.dumps(manifest)})
        big_prompt = "y" * 150_000
        with mock.patch.object(
            conversation.subprocess,
            "run",
            return_value=self._completed(stdout=envelope),
        ) as run:
            out = conversation.run_claude_json(big_prompt)
        self.assertEqual(out, manifest)
        argv = run.call_args[0][0]
        self.assertNotIn(big_prompt, argv)  # not passed via argv
        self.assertEqual(run.call_args[1].get("input"), big_prompt)


if __name__ == "__main__":
    unittest.main()
