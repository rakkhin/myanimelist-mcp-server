import json

import toon_format

from utils.serialization import format_tool_payload


def tool_payload(result) -> dict:
    """Decode a tool result's text block, its only content.

    The text must be TOON or minified JSON, exactly as format_tool_payload
    writes the decoded data. Takes a ToolResult or a client CallToolResult.
    """
    assert result.structured_content is None
    [block] = result.content
    try:
        data = json.loads(block.text)
    except json.JSONDecodeError:
        data = toon_format.decode(block.text)
    assert format_tool_payload(data) == block.text
    return data
