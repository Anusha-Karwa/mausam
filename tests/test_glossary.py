"""Plain-language glossary (app/services/glossary.py): the source for tooltips, Learn page, chat and tips."""
from app.services import glossary


def test_every_entry_is_complete_and_short():
    assert len(glossary.GLOSSARY) >= 10
    for key, entry in glossary.GLOSSARY.items():
        assert key == key.lower()
        assert entry["term"] and entry["short"] and entry["why"]
        assert len(entry["short"].split()) <= 40, f"{key}: tooltip too long"


def test_core_terms_exist():
    for key in ("nav", "sip", "benchmark"):
        assert key in glossary.GLOSSARY


def test_define_is_case_insensitive():
    assert glossary.define("NAV") == glossary.GLOSSARY["nav"]["short"]
    assert glossary.define("no-such-term") == ""


def test_as_list_sorted_by_term():
    terms = [e["term"].lower() for e in glossary.as_list()]
    assert terms == sorted(terms)
    assert all("key" in e for e in glossary.as_list())


def test_compact_maps_term_to_definition():
    c = glossary.compact()
    assert c["NAV"] == glossary.GLOSSARY["nav"]["short"]
    assert len(c) == len(glossary.GLOSSARY)


def test_explainers_have_three_paragraphs():
    for ex in glossary.EXPLAINERS:
        assert ex["slug"] and ex["title"]
        assert len(ex["body"]) == 3
