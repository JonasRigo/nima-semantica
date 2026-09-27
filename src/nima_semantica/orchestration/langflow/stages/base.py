"""Shared typed transport, receipt, and single-execution preview infrastructure."""

from __future__ import annotations

import asyncio
import json

from lfx.custom import Component
from lfx.io import HandleInput, Output
from lfx.schema import Data, DataFrame, Message

from nima_semantica.tool_contracts import ToolResult
from ..values import _value


def handle(name, label=None, required=True):
    return HandleInput(
        name=name,
        display_name=label or name.replace("_", " ").title(),
        input_types=["Data", "JSON"],
        required=required,
    )


def body(value):
    data = _value(value)
    return data["data"] if "receipt_ids" in data and "data" in data else data


def inherited(*values):
    return list(
        dict.fromkeys(
            receipt for value in values for receipt in _value(value).get("receipt_ids", [])
        )
    )


def result(operation, data, *inputs, status="complete", diagnostics=()):
    return ToolResult(
        operation=operation,
        status=status,
        data=data,
        receipt_ids=inherited(*inputs),
        diagnostics=diagnostics,
    ).model_dump(mode="json")


class InspectableStage(Component):
    """Run once per canvas execution and share the result across preview ports."""

    inputs = [handle("payload")]
    outputs = [
        Output(name="result", display_name="JSON", method="result_data", group_outputs=True),
        Output(
            name="preview",
            display_name="Readable preview",
            method="preview_message",
            group_outputs=True,
        ),
        Output(name="table", display_name="Table", method="table_data", group_outputs=True),
    ]

    async def result_data(self) -> Data:
        if not hasattr(self, "_stage_task"):
            # Shared task prevents concurrent preview branches from duplicating a write.
            self._stage_task = asyncio.create_task(self.run())
        value = await self._stage_task
        raw = getattr(self, "raw_response", None)
        properties = getattr(raw, "properties", None)
        usage = getattr(properties, "usage", None)
        if usage and "data" in value:
            value = {
                **value,
                "data": {
                    **value["data"],
                    "usage": (
                        usage.model_dump(mode="json")
                        if hasattr(usage, "model_dump")
                        else dict(usage)
                    ),
                },
            }
        return Data(data=value)

    async def preview_message(self) -> Message:
        return Message(
            text=json.dumps((await self.result_data()).data, ensure_ascii=False, indent=2)
        )

    async def table_data(self) -> DataFrame:
        value = body(await self.result_data())
        rows = next(
            (
                value[key]
                for key in (
                    "findings",
                    "obligations",
                    "regions",
                    "profiles",
                    "facts",
                    "diagnostics",
                )
                if isinstance(value.get(key), list)
            ),
            [],
        )
        return DataFrame(rows)
