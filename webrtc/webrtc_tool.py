from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tool_calling import book_appointment


def build_webrtc_tools_schema() -> ToolsSchema:
	"""Tool schema for WebRTC bot appointment booking."""
	book_appointment_function = FunctionSchema(
		name="create_data",
		description=(
			"Create appointment data after collecting patient name, age, symptom, duration, "
			"preferred slot (morning/evening), and exact appointment time within that slot."
		),
		properties={
			"name": {
				"type": "string",
				"description": "Patient's full name",
			},
			"age": {
				"type": "string",
				"description": "Patient age in years, for example 32.",
			},
			"symptom": {
				"type": "string",
				"description": "Main symptom or health issue.",
			},
			"days": {
				"type": "string",
				"description": "How many days patient has had this symptom.",
			},
			"preferred_time": {
				"type": "string",
				"enum": ["morning", "evening"],
				"description": "Preferred appointment time slot.",
			},
			"appointment_time": {
				"type": "string",
				"description": "Required exact requested time such as 10:30, 3pm, 15:00, or 04:30 PM.",
			},
		},
		required=["name", "age", "symptom", "days", "preferred_time", "appointment_time"],
	)

	return ToolsSchema(standard_tools=[book_appointment_function])


def register_webrtc_functions(llm) -> None:
	"""Register WebRTC-compatible tool handlers with the LLM service."""
	llm.register_function(
		"create_data",
		book_appointment,
		cancel_on_interruption=False,
	)
