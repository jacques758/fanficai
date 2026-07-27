# fanficai

A command-line fanfiction co-writer. You define the story bible once; every
generation call re-injects it so a long fic stays consistent.

Python 3.10+, zero dependencies. Runs offline on a mock backend, or against
Anthropic/OpenAI when an API key is present.

```bash
python -m fanficai init --title "My Fic" --fandom "X" --rating T
python -m fanficai char "Rin" --role protagonist --traits "stubborn,precise"
python -m fanficai outline -n 6
python -m fanficai write -c 1 -w 900
python -m fanficai check -c 1        # free offline continuity lint
python -m fanficai export --out fic.md
```

Real generation:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # or OPENAI_API_KEY
```

Tests: `python -m unittest discover -s tests -v`

Full design notes, limitations and time estimates: **PROJECT_NOTES.txt**

## Commands

| Command | Purpose |
|---|---|
| `init` | create a project (`story.json`) |
| `char` | add or update a cast member |
| `outline` | generate a chapter outline |
| `write` | draft a chapter |
| `continue` | extend a chapter draft |
| `critique` | model-written craft feedback |
| `check` | offline POV/tense/continuity lint, no tokens |
| `show` | story bible + word-count dashboard |
| `read` | print a chapter |
| `export` | write the manuscript to Markdown |

## Guardrails

Explicit sexual content is refused, and content sexualising minors is hard
refused in `engine.guard()` â€” enforced in code, not just requested in the
prompt.

