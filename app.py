from __future__ import annotations

import streamlit as st

from fanficai.demo import DemoResult, create_demo_story
from fanficai.engine import SafetyRefusal


st.set_page_config(page_title="FanficAI Story Studio", page_icon="✍️", layout="wide")

st.title("FanficAI Story Studio")
st.caption(
    "Create an original-fiction outline and chapter draft with the deterministic offline provider. "
    "No sign-in, API key, or server-side project storage is required."
)

with st.sidebar:
    st.header("Story bible")
    title = st.text_input("Title", "The Long Way Round")
    protagonist = st.text_input("Protagonist", "Rin")
    trait_text = st.text_input("Traits", "stubborn, precise")
    premise = st.text_area(
        "Premise",
        "A careful courier opens a message meant for someone else and must decide who to trust.",
    )
    tone = st.selectbox(
        "Tone",
        ["warm, character-driven", "tense and atmospheric", "hopeful adventure", "quiet mystery"],
    )
    chapter_count = st.slider("Outline chapters", 2, 6, 4)
    target_words = st.slider("Chapter-one target words", 200, 900, 450, 50)
    generate = st.button("Create story demo", type="primary", width="stretch")

st.info(
    "This public demo intentionally uses FanficAI's offline mock provider. It exercises the same "
    "story-bible, outline, drafting, continuity-check, and Markdown-export pipeline without cost."
)

if generate:
    try:
        st.session_state["demo_result"] = create_demo_story(
            title=title,
            protagonist=protagonist,
            traits=[part.strip() for part in trait_text.split(",")],
            premise=premise,
            tone=tone,
            chapters=chapter_count,
            words=target_words,
        )
    except (SafetyRefusal, ValueError) as error:
        st.error(str(error))

result: DemoResult | None = st.session_state.get("demo_result")
if result is None:
    st.subheader("Ready when you are")
    st.write("Adjust the story bible, then select **Create story demo** to generate the result.")
else:
    project = result.project
    st.success(
        f"Created a {len(project.chapters)}-chapter outline and drafted "
        f"{project.chapters[0].word_count} words for chapter one."
    )
    outline_tab, chapter_tab, bible_tab, checks_tab = st.tabs(
        ["Outline", "Chapter one", "Story bible", "Continuity checks"]
    )
    with outline_tab:
        for chapter in project.chapters:
            st.markdown(f"### {chapter.number}. {chapter.title}")
            st.caption(f"POV: {chapter.pov}")
            st.write(chapter.summary)
            for beat in chapter.beats:
                st.markdown(f"- {beat}")
    with chapter_tab:
        first = project.chapters[0]
        st.markdown(f"## {first.number}. {first.title}")
        st.write(first.text)
    with bible_tab:
        st.code(project.bible(), language="text")
    with checks_tab:
        for check in result.checks:
            st.success(check)

    filename = "-".join(project.title.lower().split()) or "story"
    st.download_button(
        "Download manuscript as Markdown",
        data=result.markdown,
        file_name=f"{filename}.md",
        mime="text/markdown",
        width="stretch",
    )

st.caption(
    "Safety: explicit sexual content and any sexualization of minors are refused. "
    "Generated prose is a deterministic demonstration and should be edited before publication."
)
