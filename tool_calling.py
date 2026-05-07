import aiohttp
import time
import json
import hmac
import hashlib
import os
from typing import Optional

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams
from event_logger import log_call_event, set_call_context

# ── Config ─────────────────────────────────────────────────────────────────────
BASE_URL = "https://api.vedronix.com/api/v1"
SMARTFLO_HANGUP_URL = "https://api-smartflo.tatateleservices.com/v1/call/hangup"
ACTIVE_CALL_ID: Optional[str] = None

def _get_clinic_credentials(clinic_id: str) -> Optional[dict]:
    if clinic_id == "clinic_001":
        return {
            "apiKey": os.getenv("CLINIC_001_API_KEY") or os.getenv("APPOINTMENT_API_KEY"),
            "apiSecret": os.getenv("CLINIC_001_API_SECRET") or os.getenv("APPOINTMENT_API_SECRET"),
        }
    return None


def set_active_call_id(call_id: Optional[str]) -> None:
    global ACTIVE_CALL_ID
    ACTIVE_CALL_ID = str(call_id).strip() if call_id else None
    set_call_context(ACTIVE_CALL_ID)


def get_active_call_id() -> Optional[str]:
    return ACTIVE_CALL_ID


def _normalize_call_id(raw_value: object) -> str:
    value = str(raw_value or "").strip()
    if value.lower() in {"", "none", "null", "unknown", "active_call_id"}:
        return ""
    return value


# ── Function Handler ───────────────────────────────────────────────────────────
async def book_appointment(params: FunctionCallParams):
    args = params.arguments
    log_call_event(
        "function_call_started",
        function_name=params.function_name,
        tool_call_id=params.tool_call_id,
        arguments=args,
    )

    try:
        clinic_id = os.getenv("DEFAULT_CLINIC_ID", "clinic_001")
        clinic = _get_clinic_credentials(clinic_id)

        if not clinic:
            log_call_event(
                "function_call_failed",
                function_name=params.function_name,
                tool_call_id=params.tool_call_id,
                reason="invalid_clinic_id",
                clinic_id=clinic_id,
            )
            await params.result_callback({
                "success": False,
                "error": f"Invalid default clinic_id: {clinic_id}"
            })
            return

        # Extract fields
        name = args.get("name")
        symptom = args.get("symptom")
        days = args.get("days")
        preferred_time = args.get("preferred_time")

        if not all([name, symptom, days, preferred_time]):
            log_call_event(
                "function_call_failed",
                function_name=params.function_name,
                tool_call_id=params.tool_call_id,
                reason="missing_required_fields",
            )
            await params.result_callback({
                "success": False,
                "error": "Missing required fields: name, symptom, days, preferred_time.",
            })
            return

        # API credentials
        api_key = clinic["apiKey"]
        api_secret = clinic["apiSecret"]

        if not api_key or not api_secret:
            missing = []
            if not api_key:
                missing.extend(["CLINIC_001_API_KEY", "APPOINTMENT_API_KEY"])
            if not api_secret:
                missing.extend(["CLINIC_001_API_SECRET", "APPOINTMENT_API_SECRET"])
            log_call_event(
                "function_call_failed",
                function_name=params.function_name,
                tool_call_id=params.tool_call_id,
                reason="missing_api_credentials",
                missing_env=missing,
            )
            await params.result_callback({
                "success": False,
                "error": "API credentials missing in environment variables",
                "missing_env": missing,
            })
            return

        # MATCH YOUR NODE BACKEND FORMAT
        body = {
            "patient_name": name,
            "patient_phone": "+919999999999",  # replace with dynamic if available
            "symptoms": f"{symptom} for {days} days",
            "transcript": f"Patient {name} reports {symptom} for {days} days. Prefers {preferred_time}.",
            "language": "hi",
            "audio_file": "audio_file_location_url",
            "date": "2026-12-23",
            "time_slot": "12.30",
            "metadata": {
                "source": "ai_agent",
                "preferred_time": preferred_time
            }
        }

        # Step 1: Timestamp (ms)
        ts = str(int(time.time() * 1000))

        # Step 2: Compact JSON (IMPORTANT)
        raw_body = json.dumps(body, separators=(',', ':'))

        # Step 3: Payload
        payload = f"{ts}.{raw_body}"

        # Step 4: Signature
        signature = hmac.new(
            api_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "x-timestamp": ts,
            "x-signature": signature,
        }

        # API CALL
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BASE_URL}/aiAgent/appointment/create-smart",
                data=raw_body,  # MUST be raw_body (not json=body)
                headers=headers,
            ) as resp:

                text = await resp.text()

                try:
                    data = json.loads(text)
                except:
                    data = {"raw": text}

                if resp.status in (200, 201):
                    log_call_event(
                        "function_call_succeeded",
                        function_name=params.function_name,
                        tool_call_id=params.tool_call_id,
                        status_code=resp.status,
                    )
                    await params.result_callback({
                        "success": True,
                        "message": "Appointment created successfully",
                        "data": data,
                    })
                else:
                    log_call_event(
                        "function_call_failed",
                        function_name=params.function_name,
                        tool_call_id=params.tool_call_id,
                        status_code=resp.status,
                        response=data,
                    )
                    print(f"[create_data] API ERROR status={resp.status}")
                    print(f"[create_data] API ERROR body={data}")
                    await params.result_callback({
                        "success": False,
                        "error": data,
                        "status_code": resp.status
                    })

    except Exception as e:
        log_call_event(
            "function_call_failed",
            function_name=params.function_name,
            tool_call_id=params.tool_call_id,
            reason="exception",
            error=str(e),
        )
        print(f"[create_data] EXCEPTION error={e}")
        await params.result_callback({
            "success": False,
            "error": str(e),
        })


async def hangup_call(params: FunctionCallParams):
    args = params.arguments
    log_call_event(
        "function_call_started",
        function_name=params.function_name,
        tool_call_id=params.tool_call_id,
        arguments=args,
    )

    try:
        call_id = _normalize_call_id(args.get("call_id", ""))
        if not call_id:
            call_id = _normalize_call_id(get_active_call_id())
        if not call_id:
            log_call_event(
                "function_call_failed",
                function_name=params.function_name,
                tool_call_id=params.tool_call_id,
                reason="missing_call_id",
            )
            await params.result_callback(
                {
                    "success": False,
                    "error": "Missing call_id. Provide call_id or set active call context first.",
                }
            )
            return

        # Accept either a full Authorization header value or a raw token.
        auth_value = (
            os.getenv("SMARTFLO_AUTHORIZATION")
            or os.getenv("SMARTFLO_API_TOKEN")
            or os.getenv("SMARTFLO_AUTH_TOKEN")
        )
        if not auth_value:
            log_call_event(
                "function_call_failed",
                function_name=params.function_name,
                tool_call_id=params.tool_call_id,
                reason="missing_auth",
            )
            await params.result_callback(
                {
                    "success": False,
                    "error": "Missing Smartflo auth. Set SMARTFLO_AUTHORIZATION, SMARTFLO_API_TOKEN, or SMARTFLO_AUTH_TOKEN.",
                }
            )
            return

        body = {"call_id": call_id}
        headers_raw = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": auth_value,
        }
        headers_bearer = {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": auth_value if auth_value.lower().startswith("bearer ") else f"Bearer {auth_value}",
        }

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            async with session.post(SMARTFLO_HANGUP_URL, json=body, headers=headers_raw) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = {"raw": text}

                if resp.status == 200:
                    log_call_event(
                        "function_call_succeeded",
                        function_name=params.function_name,
                        tool_call_id=params.tool_call_id,
                        status_code=resp.status,
                        auth_mode="raw",
                    )
                    await params.result_callback(
                        {
                            "success": True,
                            "message": "Hangup request sent successfully.",
                            "data": data,
                            "used_call_id": call_id,
                            "auth_mode": "raw",
                        }
                    )
                    return

                if resp.status not in (401, 403):
                    log_call_event(
                        "function_call_failed",
                        function_name=params.function_name,
                        tool_call_id=params.tool_call_id,
                        status_code=resp.status,
                        auth_mode="raw",
                        response=data,
                    )
                    await params.result_callback(
                        {
                            "success": False,
                            "error": data,
                            "status_code": resp.status,
                            "used_call_id": call_id,
                            "auth_mode": "raw",
                        }
                    )
                    return

            # Retry with Bearer format if raw auth failed as unauthorized.
            async with session.post(SMARTFLO_HANGUP_URL, json=body, headers=headers_bearer) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = {"raw": text}

                if resp.status == 200:
                    log_call_event(
                        "function_call_succeeded",
                        function_name=params.function_name,
                        tool_call_id=params.tool_call_id,
                        status_code=resp.status,
                        auth_mode="bearer",
                    )
                    await params.result_callback(
                        {
                            "success": True,
                            "message": "Hangup request sent successfully.",
                            "data": data,
                            "used_call_id": call_id,
                            "auth_mode": "bearer",
                        }
                    )
                else:
                    log_call_event(
                        "function_call_failed",
                        function_name=params.function_name,
                        tool_call_id=params.tool_call_id,
                        status_code=resp.status,
                        auth_mode="bearer",
                        response=data,
                    )
                    await params.result_callback(
                        {
                            "success": False,
                            "error": data,
                            "status_code": resp.status,
                            "used_call_id": call_id,
                            "auth_mode": "bearer",
                        }
                    )

    except Exception as e:
        log_call_event(
            "function_call_failed",
            function_name=params.function_name,
            tool_call_id=params.tool_call_id,
            reason="exception",
            error=str(e),
        )
        await params.result_callback(
            {
                "success": False,
                "error": str(e),
            }
        )


# ── Tool Schema ────────────────────────────────────────────────────────────────
def build_create_data_tools_schema() -> ToolsSchema:
    book_appointment_function = FunctionSchema(
        name="create_data",
        description=(
            "Create appointment data after collecting patient name, symptom, duration, "
            "and preferred time (morning/evening)."
        ),
        properties={
            "name": {
                "type": "string",
                "description": "Patient's full name",
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
        },
        required=["name", "symptom", "days", "preferred_time"],
    )

    hangup_call_function = FunctionSchema(
        name="hangup_call",
        description="Hang up an active call using the Smartflo call_id.",
        properties={
            "call_id": {
                "type": "string",
                "description": "Unique ID of the call to hang up (for example: 1627373566.350603).",
            },
        },
        required=["call_id"],
    )
    return ToolsSchema(standard_tools=[book_appointment_function, hangup_call_function])


# ── Register Function ──────────────────────────────────────────────────────────
def register_appointment_functions(
    llm,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> None:
    llm.register_function(
        "create_data",
        book_appointment,
        cancel_on_interruption=False,
    )
    llm.register_function(
        "hangup_call",
        hangup_call,
        cancel_on_interruption=False,
    )
