"""Visible deterministic lookup and rendering for the native Tool Guide."""
from lfx.io import Output
from lfx.schema import Message, DataFrame

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.tool_guide import ToolGuideRequest, tool_guide
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent
from nima_semantica.orchestration.langflow.values import _value


class ToolGuide(BaseComponent):
    name = "ToolGuide"
    display_name = "Tool Guide"
    description = "Inspect exact contracts and delivery status. No model, storage, tool dispatch, or automatic selection."
    nima_manifest = manifest_for_component("ToolGuide")
    outputs = [
        Output(name="result", display_name="Typed catalog result", method="result_data", group_outputs=True),
        Output(name="preview", display_name="Readable guide", method="preview_message", group_outputs=True),
        Output(name="table", display_name="Tool inventory", method="table_data", group_outputs=True),
    ]

    async def run(self):
        request = ToolGuideRequest.model_validate(_value(self.payload))
        return tool_guide(request).model_dump(mode="json")

    async def table_data(self) -> DataFrame:
        tools = (await self.result_data()).data["data"].get("tools", [])
        return DataFrame([{key: item[key] for key in ("tool_id", "name", "implementation", "visual_approval", "boundary")} for item in tools])

    async def preview_message(self) -> Message:
        import json
        result = (await self.result_data()).data
        if result["status"] == "failed" and result["data"].get("executed") is False:
            return Message(text=json.dumps(result, ensure_ascii=False))
        lines = ["# NIMA Tool Guide", result["note"], "", result["data"]["contract_note"]]
        for item in result["data"]["tools"]:
            lines.extend(["", "## " + item["name"],
                f"ID: {item['tool_id']} | Implementation: {item['implementation']} | Visual approval: {item['visual_approval']}",
                item["description"], "Boundary: " + item["boundary"]])
            lines.extend(["Usage and recovery:", "```json", json.dumps(item["usage"], indent=2), "```"])
            if item["input_schema"] is not None:
                lines.extend(["Input schema:", "```json", json.dumps(item["input_schema"], indent=2), "```",
                    "Examples:", "```json", json.dumps(item["examples"], indent=2), "```"])
        lines.extend(["", result["data"]["deployment_status"]])
        for diagnostic in result["diagnostics"]:
            lines.append(diagnostic["code"] + ": " + diagnostic["message"])
        return Message(text="\n".join(lines))
