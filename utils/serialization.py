import json
from typing import Any

import toon_format


def format_tool_payload(data: Any) -> str:
    """Serialize a tool payload for the MCP text block.

    Returns TOON only when it is strictly shorter than the minified JSON
    of the same value, measured with ``len`` (code points, not tokens);
    otherwise returns that JSON. The JSON is compact and keeps non-ASCII
    characters as they are, as FastMCP's own text block does.

    TOON encodes the value parsed back from the JSON, so both encodings
    describe the same JSON data (tuples become lists, keys become
    strings). NaN and infinity have no JSON form and raise ``ValueError``.
    """
    minified = json.dumps(
        data, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    toon = toon_format.encode(json.loads(minified))
    return toon if len(toon) < len(minified) else minified
