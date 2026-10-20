import json

import pytest
import toon_format

from utils.serialization import format_tool_payload


def test_uniform_rows_are_encoded_as_toon():
    data = {
        "queue": [
            {"title": "Frieren", "mal_id": 52991, "unwatched_count": 2},
            {"title": "Dandadan", "mal_id": 57334, "unwatched_count": 0},
        ]
    }

    text = format_tool_payload(data)

    assert text == (
        "queue[2]{title,mal_id,unwatched_count}:\n"
        "  Frieren,52991,2\n"
        "  Dandadan,57334,0"
    )
    assert toon_format.decode(text) == data


def test_rows_with_nested_arrays_fall_back_to_minified_json():
    # An array inside the rows rules out a TOON table, as the synonyms
    # in MAL's alternative_titles do.
    data = {
        "data": [
            {"node": {"id": 1, "synonyms": ["A", "B"]}},
            {"node": {"id": 2, "synonyms": ["C"]}},
        ]
    }

    text = format_tool_payload(data)

    assert text == (
        '{"data":[{"node":{"id":1,"synonyms":["A","B"]}},'
        '{"node":{"id":2,"synonyms":["C"]}}]}'
    )
    assert len(toon_format.encode(data)) > len(text)


def test_equal_length_keeps_json():
    # TOON must be strictly shorter; both encode 1 as "1".
    assert format_tool_payload(1) == "1"


def test_non_ascii_is_kept_as_is_in_json():
    data = [{"a": "葬送"}, {"b": None}]

    assert format_tool_payload(data) == '[{"a":"葬送"},{"b":null}]'


def test_toon_encodes_the_json_data_model():
    # int keys and tuples have no TOON form; JSON turns them into
    # strings and lists first.
    text = format_tool_payload({1: ("x", "y")})

    assert text == '"1"[2]: x,y'
    assert toon_format.decode(text) == json.loads(json.dumps({1: ["x", "y"]}))


def test_nan_has_no_json_form():
    with pytest.raises(ValueError):
        format_tool_payload({"mean": float("nan")})
