"""Tool parameters as the model needs them, from the Pydantic contracts the handlers validate against.

`model_json_schema()` is written for validators: a `title` on every field, `anyOf [X, null]` for each optional value,
int32 bounds, string lengths and patterns. Every request carries the declared tools, so those bytes are paid on every
model call while telling the model nothing it acts on. The handlers still validate the full contract.
"""

from pydantic import BaseModel

_VALIDATION_ONLY = frozenset({"title", "maxLength", "minLength", "pattern", "additionalProperties"})
_INT32_MAX = 2147483647


def _compact(node):
    if isinstance(node, list):
        return [_compact(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {
        key: _compact(value)
        for key, value in node.items()
        if key not in _VALIDATION_ONLY and not (key == "default" and value is None) and not (key == "maximum" and value == _INT32_MAX)
    }
    options = out.get("anyOf")
    if isinstance(options, list) and len(options) == 2 and {"type": "null"} in options:
        (value,) = [option for option in options if option != {"type": "null"}]
        out.pop("anyOf")
        out = {**value, **out}
    return out


def parameters(contract: type[BaseModel]) -> dict:
    return _compact(contract.model_json_schema())
