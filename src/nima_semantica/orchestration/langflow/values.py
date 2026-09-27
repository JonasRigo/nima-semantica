"""Small Langflow value conversions without graph, storage, or model execution."""

from lfx.schema import Data, Message
from pydantic import ValidationError

from nima_semantica.providers import strict_json_object


def _value(value):
    if isinstance(value, Message):
        return strict_json_object(value.text)
    if isinstance(value, Data):
        return dict(value.data)
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "data") and isinstance(value.data, dict):
        return dict(value.data)
    if value in (None, ""):
        return {}
    return strict_json_object(str(value))


def _diagnostics(error):
    if isinstance(error, ValidationError):
        return [
            {"code": item["type"], "field": ".".join(map(str, item["loc"])), "message": item["msg"]}
            for item in error.errors(include_input=False, include_context=False)[:32]
        ]
    return [{"code": type(error).__name__, "message": str(error)[:2000]}]


def _text(value):
    return value.text if isinstance(value, Message) else str(value or "")
