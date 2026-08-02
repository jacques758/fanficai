import unittest

from fanficai.demo import create_demo_story


class DemoTests(unittest.TestCase):
    def test_creates_outline_draft_checks_and_export(self) -> None:
        result = create_demo_story(
            title="Run Verification Story",
            protagonist="Rin",
            traits=["stubborn", "precise"],
            premise="A courier opens the wrong message.",
            tone="quiet mystery",
            chapters=4,
            words=300,
        )
        self.assertEqual(len(result.project.chapters), 4)
        self.assertGreaterEqual(result.project.chapters[0].word_count, 250)
        self.assertTrue(result.checks)
        self.assertIn("# Run Verification Story", result.markdown)
        self.assertIn("## 1. Small Hours", result.markdown)

    def test_rejects_missing_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "required"):
            create_demo_story(
                title="",
                protagonist="Rin",
                traits=[],
                premise="",
                tone="warm",
                chapters=3,
                words=300,
            )


if __name__ == "__main__":
    unittest.main()
