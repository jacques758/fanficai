"""Offline tests for the fanficai prototype. Run: python -m unittest -v"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from fanficai import engine, store
from fanficai.cli import main
from fanficai.models import Character, Project
from fanficai.providers import MockProvider, OllamaProvider, OpenAIProvider, get_provider


def sample_project() -> Project:
    p = Project(
        title="Long Way Round", fandom="Test Fandom", rating="T",
        premise="Two rivals share one apartment and no patience.",
        tags=["slow burn", "hurt/comfort"], avoid=["character death"],
    )
    p.characters = [
        Character(name="Rin", role="protagonist", traits=["stubborn"], wants="to be believed"),
        Character(name="Cass", role="antagonist", traits=["glib"], wants="to be left alone"),
    ]
    return p


class TestModels(unittest.TestCase):
    def test_roundtrip(self):
        p = sample_project()
        p.chapters = engine.make_outline(p, MockProvider(), 2)
        again = Project.from_dict(p.to_dict())
        self.assertEqual(again.title, p.title)
        self.assertEqual(len(again.characters), 2)
        self.assertEqual(len(again.chapters), 2)
        self.assertEqual(again.chapters[0].beats, p.chapters[0].beats)

    def test_bible_contains_constraints(self):
        b = sample_project().bible()
        self.assertIn("RATING: T", b)
        self.assertIn("Rin", b)
        self.assertIn("character death", b)

    def test_default_pov_is_protagonist(self):
        self.assertEqual(sample_project().default_pov_character(), "Rin")


class TestOutline(unittest.TestCase):
    def test_parse_outline(self):
        raw = (
            "## 1. Small Hours\nPOV: Rin\nSUMMARY: It begins.\n- beat one\n- beat two\n\n"
            "## 2. The Favour\nPOV: Cass\nSUMMARY: It worsens.\n- beat three\n"
        )
        chapters = engine.parse_outline(raw)
        self.assertEqual([c.number for c in chapters], [1, 2])
        self.assertEqual(chapters[0].title, "Small Hours")
        self.assertEqual(chapters[0].pov, "Rin")
        self.assertEqual(chapters[1].beats, ["beat three"])

    def test_outline_falls_back_on_garbage(self):
        class Junk(MockProvider):
            def generate(self, system, prompt, max_tokens=2000):
                return "sorry, no."
        chapters = engine.make_outline(sample_project(), Junk(), 4)
        self.assertEqual(len(chapters), 4)


class TestDrafting(unittest.TestCase):
    def test_write_and_continue(self):
        p = sample_project()
        prov = MockProvider()
        p.chapters = engine.make_outline(p, prov, 2)

        ch = engine.write_chapter(p, prov, 1, words=120)
        self.assertGreater(ch.word_count, 50)
        self.assertEqual(ch.word_count, len(ch.text.split()))

        before = ch.word_count
        ch = engine.continue_chapter(p, prov, 1, words=80)
        self.assertGreater(ch.word_count, before)

    def test_continue_requires_draft(self):
        p = sample_project()
        p.chapters = engine.make_outline(p, MockProvider(), 1)
        with self.assertRaises(ValueError):
            engine.continue_chapter(p, MockProvider(), 1)

    def test_write_creates_missing_chapter(self):
        p = sample_project()
        engine.write_chapter(p, MockProvider(), 5, words=60)
        self.assertIsNotNone(p.chapter(5))


class TestSafety(unittest.TestCase):
    def test_explicit_is_refused(self):
        p = sample_project()
        p.tags.append("explicit smut")
        with self.assertRaises(engine.SafetyRefusal):
            engine.guard(p)

    def test_minor_plus_explicit_is_refused(self):
        p = sample_project()
        p.premise = "explicit scenes with a 14-year-old"
        with self.assertRaises(engine.SafetyRefusal):
            engine.guard(p)

    def test_clean_project_passes(self):
        engine.guard(sample_project())  # must not raise


class TestLint(unittest.TestCase):
    def test_detects_avoid_list_violation(self):
        p = sample_project()
        engine.write_chapter(p, MockProvider(), 1, words=60)
        p.chapter(1).text += "\n\nAnd then came the character death."
        p.chapter(1).touch()
        notes = engine.lint_chapter(p, 1)
        self.assertTrue(any("avoid list" in n for n in notes))

    def test_detects_pov_drift(self):
        p = sample_project()
        engine.write_chapter(p, MockProvider(), 1, words=60)
        p.chapter(1).text += "\n\nI walked out and my hands shook."
        p.chapter(1).touch()
        self.assertTrue(any("POV" in n for n in engine.lint_chapter(p, 1)))

    def test_no_draft(self):
        self.assertIn("no draft", engine.lint_chapter(sample_project(), 9)[0])

    def test_dialogue_with_curly_apostrophes_is_not_pov_drift(self):
        """Regression: treating U+2019 as a closing quote truncated the dialogue
        strip, leaking 'I' from speech into the narration check."""
        p = sample_project()
        engine.write_chapter(p, MockProvider(), 1, words=60)
        ch = p.chapter(1)
        ch.text = (
            "Rin set the box down and waited.\n\n"
            "\u201cCome on, it\u2019s just rain. I don\u2019t mind it,\u201d Cass said.\n\n"
            "\u201cI have a deadline,\u201d she answered, and meant it."
        )
        ch.touch()
        notes = engine.lint_chapter(p, 1)
        self.assertFalse([n for n in notes if n.startswith("POV")], notes)

    def test_sentence_initial_capitals_are_not_names(self):
        """Regression: 'Come', 'Great', 'Attention' were reported as cast."""
        p = sample_project()
        engine.write_chapter(p, MockProvider(), 1, words=60)
        ch = p.chapter(1)
        ch.text = (
            "Great. Attention turned to the door. Nothing moved.\n\n"
            "Rin waited. Then the lights went out."
        )
        ch.touch()
        notes = engine.lint_chapter(p, 1)
        self.assertFalse([n for n in notes if "off-bible" in n], notes)

    def test_genuinely_invented_name_is_caught(self):
        p = sample_project()
        engine.write_chapter(p, MockProvider(), 1, words=60)
        ch = p.chapter(1)
        ch.text = "Rin turned as Tess came through the door without knocking."
        ch.touch()
        notes = engine.lint_chapter(p, 1)
        self.assertTrue(any("Tess" in n for n in notes), notes)


class TestUsageAccounting(unittest.TestCase):
    def test_usage_is_folded_into_project_total(self):
        import os

        p = sample_project()
        prov = MockProvider()
        prov.last_usage = {"in": 500, "out": 1000}
        prov.model = "gpt-4o-mini"
        with mock.patch.dict(
            os.environ,
            {
                "FANFICAI_INPUT_USD_PER_MTOK": "0.15",
                "FANFICAI_OUTPUT_USD_PER_MTOK": "0.60",
            },
        ):
            line = engine.record_usage(p, prov)
        self.assertEqual(p.tokens_in, 500)
        self.assertEqual(p.tokens_out, 1000)
        self.assertEqual(p.calls, 1)
        # 500 * 0.15/1M + 1000 * 0.60/1M = 0.000675
        self.assertAlmostEqual(p.cost_usd, 0.000675, places=6)
        self.assertIn("500 in / 1000 out", line)

    def test_zero_usage_reports_nothing(self):
        p = sample_project()
        self.assertEqual(engine.record_usage(p, MockProvider()), "")
        self.assertEqual(p.calls, 0)

    def test_unknown_model_reports_tokens_not_dollars(self):
        p = sample_project()
        prov = MockProvider()
        prov.last_usage = {"in": 10, "out": 10}
        prov.model = "some-new-model"
        line = engine.record_usage(p, prov)
        self.assertIn("cost unknown", line)
        self.assertEqual(p.cost_usd, 0.0)


class TestProviders(unittest.TestCase):
    def test_auto_without_keys_gives_mock(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in
                 ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FANFICAI_PROVIDER")}
        try:
            self.assertEqual(get_provider("auto").name, "mock")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_mock_is_deterministic(self):
        p = sample_project()
        a = engine.write_chapter(p, MockProvider(seed=1), 1, words=100).text
        b = engine.write_chapter(sample_project(), MockProvider(seed=1), 1, words=100).text
        self.assertEqual(a, b)

    def test_openai_uses_responses_payload(self):
        response = {
            "output": [{"content": [{"type": "output_text", "text": "Draft text"}]}],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
        with mock.patch("fanficai.providers._post", return_value=response) as post:
            provider = OpenAIProvider(api_key="test-key")
            self.assertEqual(provider.generate("rules", "prompt"), "Draft text")
            request = post.call_args.args[0]
            body = __import__("json").loads(request.data)
            self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
            self.assertEqual(body["instructions"], "rules")
            self.assertEqual(provider.last_usage, {"in": 10, "out": 20})

    def test_ollama_configuration(self):
        provider = OllamaProvider(model="llama-test", base_url="http://localhost:11434/")
        self.assertEqual(provider.model, "llama-test")
        self.assertEqual(provider.url, "http://localhost:11434/api/generate")


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.proj = str(Path(self.dir.name) / "story.json")
        self.out = ""

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *args) -> int:
        """Run a command with its output captured, so the suite stays quiet and
        assertions can inspect what the user would have seen."""
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = main(["--project", self.proj, "--provider", "mock", *args])
        self.out = buf.getvalue()
        return code

    def test_full_happy_path(self):
        self.assertEqual(self.run_cli("init", "--title", "Test Fic", "--fandom", "X"), 0)
        self.assertEqual(self.run_cli("char", "Rin", "--role", "protagonist"), 0)
        self.assertEqual(self.run_cli("outline", "-n", "2"), 0)
        self.assertEqual(self.run_cli("write", "-c", "1", "-w", "120"), 0)
        self.assertEqual(self.run_cli("continue", "-c", "1", "-w", "60"), 0)
        self.assertEqual(self.run_cli("check", "-c", "1"), 0)
        self.assertEqual(self.run_cli("critique", "-c", "1"), 0)
        self.assertEqual(self.run_cli("show"), 0)
        self.assertIn("TOTAL", self.out)

        out = Path(self.dir.name) / "out.md"
        self.assertEqual(self.run_cli("export", "--out", str(out)), 0)
        self.assertIn("# Test Fic", out.read_text(encoding="utf-8"))

        saved = store.load(self.proj)
        self.assertEqual(len(saved.chapters), 2)
        self.assertGreater(saved.chapter(1).word_count, 100)

    def test_init_refuses_overwrite(self):
        self.assertEqual(self.run_cli("init", "--title", "A"), 0)
        self.assertEqual(self.run_cli("init", "--title", "B"), 1)
        self.assertIn("already exists", self.out)
        self.assertEqual(self.run_cli("init", "--title", "B", "--force"), 0)

    def test_missing_project_errors_cleanly(self):
        self.assertEqual(self.run_cli("show"), 1)
        self.assertIn("No project at", self.out)


if __name__ == "__main__":
    unittest.main()
