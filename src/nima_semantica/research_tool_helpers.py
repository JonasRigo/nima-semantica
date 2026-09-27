"""Exact source anchors and strict structured execution output for research tools."""
import json
from .reasoning_state import Anchor

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result

def execution_artifact(stdout):
    try:
        value = json.loads(stdout, object_pairs_hook=strict_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except ValueError as exc:
        raise ValueError("Print exactly one JSON object; put diagnostics in fields or stderr. A new execution is needed.") from exc
    if not isinstance(value, dict):
        raise ValueError("Execution output must be a JSON object.")
    # JSON's exponent syntax can overflow to inf without invoking parse_constant.
    json.dumps(value, allow_nan=False)
    return value

def exact_anchors(sources):
    return tuple(Anchor(source_id=key, start=start, end=min(start+20000, len(text)), quotation=text[start:start+20000])
        for key, text in sources.items() for start in range(0, len(text), 20000) if text[start:start+20000])
