from keynone.cleanup.formatting import finalize, join_units, normalize_lists


def test_join_units_spacing_and_lists() -> None:
    assert join_units(["Hello there.", "How are you?"]) == "Hello there. How are you?"
    assert join_units(["Agenda:\n1. Intro", "2. Budget", "Thanks."]) == (
        "Agenda:\n1. Intro\n2. Budget\nThanks."
    )
    assert join_units(["Hi team,", "\n\n", "Update below."]) == "Hi team,\n\nUpdate below."


def test_normalize_lists_renumbers_and_unifies_bullets() -> None:
    assert normalize_lists("1. a\n3. b\n7. c\ntext\n* x\n• y") == (
        "1. a\n2. b\n3. c\ntext\n- x\n- y"
    )


def test_finalize_chat_drops_single_final_period() -> None:
    kwargs = dict(style="chat", drop_final_period=True, trailing_space=False)
    assert finalize("Sounds good.", **kwargs) == "Sounds good"
    assert finalize("Sounds good. See you.", **kwargs) == "Sounds good. See you."
    assert finalize("Really?", **kwargs) == "Really?"


def test_finalize_trailing_space_and_cleanup() -> None:
    out = finalize("Line one.  \n\n\n\nLine two.", style="default", drop_final_period=False,
                   trailing_space=True)
    assert out == "Line one.\n\nLine two. "
    assert finalize("   ", style="default", drop_final_period=False, trailing_space=True) == ""
