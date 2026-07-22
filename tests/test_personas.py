from personas import DEFAULT_PERSONA_ID, PERSONAS, get_persona, get_persona_choices


def test_get_persona_returns_correct_persona():
    p = get_persona("traffic")
    assert p.id == "traffic"
    assert p.display_name == "交通警察"


def test_get_persona_fallback_to_default():
    p = get_persona("nonexistent")
    assert p.id == DEFAULT_PERSONA_ID


def test_get_persona_choices_length():
    choices = get_persona_choices()
    assert len(choices) == len(PERSONAS)


def test_get_persona_choices_format():
    choices = get_persona_choices()
    for pid, label in choices:
        assert pid in PERSONAS
        assert PERSONAS[pid].icon in label
        assert PERSONAS[pid].display_name in label


def test_default_persona_has_traffic_accident_extra_dimensions():
    p = get_persona(DEFAULT_PERSONA_ID)
    assert "A1/A2/A3" in p.rewrite_extra_dimensions
    assert "A3移車" in p.rewrite_extra_dimensions


def test_default_persona_has_focus_instructions():
    p = get_persona(DEFAULT_PERSONA_ID)
    assert "交通執法導向" in p.answer_focus_instructions
    assert "A1/A2/A3" in p.answer_focus_instructions


def test_unknown_persona_fallbacks_to_traffic():
    p = get_persona("duty_officer")
    assert p.id == "traffic"


def test_all_personas_have_required_fields():
    for pid, p in PERSONAS.items():
        assert p.id == pid
        assert p.display_name
        assert p.icon
        assert p.answer_role_description
