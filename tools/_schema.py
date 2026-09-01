# SPDX-License-Identifier: CC0-1.0

"""Auto-derive an OpenAI-shape tool schema from a Python function.

Used by `tools/__init__.py` so each tool's schema lives implicit in its
type hints and docstring — no JSON blobs to maintain in parallel.

Conventions a tool function must follow:
  - One top-level function, named the same as its file.
  - All parameters carry type annotations (str / int / float / bool /
    list / dict). Unannotated parameters fall back to "string" and
    will probably misbehave; annotate them.
  - First paragraph of the docstring becomes the model-facing
    description. Be concrete about what the tool does and when to
    use it — that text is what the model reads when deciding to call.
  - Required vs optional follows the signature: a parameter with a
    default is optional; one without is required.
"""

import inspect
import typing


_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json_type(py_type):
    """Map a Python annotation to a JSON-schema type string. Optional[T]
    and T | None are unwrapped to T. Anything we don't recognize falls
    back to "string", which is the safest default — the model can still
    pass arbitrary text and the tool function can validate further."""
    origin = typing.get_origin(py_type)
    if origin is typing.Union:
        args = typing.get_args(py_type)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_json_type(non_none[0])
    return _TYPE_MAP.get(py_type, "string")


def build_schema(fn, hide_params=None):
    """Build the OpenAI-shape tool schema dict for `fn`.

    `hide_params` is the set of parameter names to omit from the schema.
    Use it for framework-injected context (e.g. `agent_name`) that the
    model neither sees nor controls — those params still live in the
    function signature but the executor fills them in.

    Returned shape (passed straight to llm_backend.chat(tools=...)):

        {"type": "function",
         "function": {
             "name": <fn.__name__>,
             "description": <first paragraph of docstring>,
             "parameters": {
                 "type": "object",
                 "properties": {<param>: {"type": <json_type>}, ...},
                 "required": [<param names without defaults>],
             }}}
    """
    hide_params = set(hide_params or ())
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)

    properties = {}
    required = []
    for name, param in sig.parameters.items():
        if name == "self" or name in hide_params:
            continue
        py_type = hints.get(name, str)
        properties[name] = {"type": _python_type_to_json_type(py_type)}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    doc = inspect.getdoc(fn) or ""
    # First paragraph (split on blank line) as the model-facing description.
    description = doc.split("\n\n", 1)[0]
    description = " ".join(description.split())  # normalize newlines + whitespace

    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }
