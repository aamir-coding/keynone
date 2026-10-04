from keynone.cleanup.segmenter import SentenceSegmenter


def test_emits_sentence_once_next_one_starts() -> None:
    seg = SentenceSegmenter()
    assert seg.update("Hello there") == []
    assert seg.update("Hello there.") == []  # final only once the next sentence begins
    assert seg.update("Hello there. How") == ["Hello there."]
    assert seg.pending == "How"
    assert seg.update("Hello there. How are you? I'm") == ["How are you?"]


def test_flush_emits_unfinished_tail() -> None:
    seg = SentenceSegmenter()
    seg.update("We should meet on")
    assert seg.update("We should meet on Tuesday", flush=True) == ["We should meet on Tuesday"]
    assert seg.update("We should meet on Tuesday and then", flush=True) == ["and then"]
    assert seg.pending == ""


def test_abbreviations_do_not_split() -> None:
    seg = SentenceSegmenter()
    assert seg.update("Ask Dr. Smith at 4 p.m. tomorrow. Thanks") == [
        "Ask Dr. Smith at 4 p.m. tomorrow."
    ]


def test_revised_text_keeps_the_tail() -> None:
    seg = SentenceSegmenter()
    seg.update("One. Two")
    out = seg.update("One! Two. Three", flush=True)
    assert out and out[-1] == "Three"
