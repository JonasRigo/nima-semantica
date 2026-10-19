"""A crop enters as JSON text; only its image reaches the selected vision model."""
import json
from lfx.custom import Component
from lfx.io import HandleInput, MessageTextInput, MultilineInput, Output
from lfx.schema.message import Message
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.pdf.recognition import TRANSCRIPTION_PROMPT, transcribe


class FormulaTranscription(Component):
    name = "FormulaTranscription"
    display_name = "PDF Formula Recognition"
    description = "Faithful PNG-to-LaTeX transcription with the connected vision model. No corpus access."
    nima_manifest = manifest_for_component("FormulaTranscription")
    inputs = [MessageTextInput(name="request_json", display_name="Crop request", required=True),
              HandleInput(name="model", display_name="Vision model", input_types=["LanguageModel"], required=True),
              MultilineInput(name="prompt", display_name="Transcription instructions", value=TRANSCRIPTION_PROMPT)]
    outputs = [Output(name="result", display_name="Transcription JSON", method="run_request")]

    async def run_request(self) -> Message:
        return Message(text=json.dumps(await transcribe(json.loads(self.request_json), self.model, self.prompt)))
