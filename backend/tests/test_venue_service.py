from app.services.venue_service import _expand_day


def test_expand_day_maps_abbreviations_to_full_names():
    assert _expand_day("fri") == "Friday"
    assert _expand_day("Fri") == "Friday"
    assert _expand_day("Friday") == "Friday"
    assert _expand_day("mon") == "Monday"


def test_expand_day_passes_through_unrecognized_values():
    assert _expand_day("TBD") == "TBD"
