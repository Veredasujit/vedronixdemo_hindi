import datetime
import io
import json
import os
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.transcriptions.language import Language
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from MetricsLogObserver import MetricsLogObserver
from UserBotLatencyLogObserver import UserBotLatencyLogObserver
from event_logger import configure_event_log_sink, log_call_event, set_call_context
from tool_calling import (
    build_create_data_tools_schema,
    register_appointment_functions,
    set_active_call_id,
)
from pipecat.services.deepgram.stt import LiveOptions, DeepgramSTTService

load_dotenv(override=True)
INITIAL_GREETING = (
    "नमस्ते जी, मैं रवि बोल रहा हूँ, कैपिटल हॉस्पिटल यमुनानगर से। "
    "मैं आपकी अपॉइंटमेंट बुकिंग में मदद करता हूँ।"
)

PROMPT_FILE = Path(__file__).with_name("prompt.txt")
RECORDINGS_DIR = Path(__file__).resolve().parent / "recording"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
METRICS_LOG_SINK_ID: Optional[int] = None
LATENCY_LOG_SINK_ID: Optional[int] = None


def extract_call_id(call_data: Dict[str, Any]) -> str:
    for key in ("call_id", "call_sid", "callId", "CallSid"):
        value = str(call_data.get(key, "")).strip()
        if value:
            return value
    return ""


def ensure_observer_log_sinks() -> None:
    global METRICS_LOG_SINK_ID, LATENCY_LOG_SINK_ID

    if METRICS_LOG_SINK_ID is None:
        METRICS_LOG_SINK_ID = logger.add(
            RECORDINGS_DIR / "metrics.log",
            rotation="10 MB",
            level="DEBUG",
            filter=lambda record: "📊" in record["message"],
        )

    if LATENCY_LOG_SINK_ID is None:
        LATENCY_LOG_SINK_ID = logger.add(
            RECORDINGS_DIR / "latency.log",
            rotation="10 MB",
            level="DEBUG",
            filter=lambda record: "LATENCY FROM USER STOPPED SPEAKING TO BOT STARTED SPEAKING" in record["message"],
        )
    configure_event_log_sink(RECORDINGS_DIR)


def load_system_prompt() -> str:
    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Prompt file is empty: {PROMPT_FILE}")
    return prompt


async def save_audio(audio: bytes, sample_rate: int, num_channels: int):
    if len(audio) > 0:
        filename = f"recording_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        output_path = RECORDINGS_DIR / filename
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wf:
                wf.setsampwidth(2)
                wf.setnchannels(num_channels)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            async with aiofiles.open(output_path, "wb") as file:
                await file.write(buffer.getvalue())
        logger.info(f"Merged audio saved to {output_path}")
        log_call_event(
            "audio_saved",
            path=str(output_path),
            sample_rate=sample_rate,
            num_channels=num_channels,
            bytes=len(audio),
        )
    else:
        logger.info("No audio data to save")


async def save_transcript(transcript_log: List[Dict[str, Any]]):
    filename = f"transcript_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path = RECORDINGS_DIR / filename
    async with aiofiles.open(output_path, "w", encoding="utf-8") as file:
        await file.write(json.dumps(transcript_log, ensure_ascii=False, indent=2, default=str))
    logger.info(f"Transcript saved to {output_path}")
    log_call_event("transcript_saved", path=str(output_path), entries=len(transcript_log))


async def run_bot(
    transport: BaseTransport,
    handle_sigint: bool,
    testing: bool,
    call_id: Optional[str] = None,
):
    ensure_observer_log_sinks()

    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
        enable_prompt_caching=True,)
    register_appointment_functions(
        llm,
        api_url=os.getenv("APPOINTMENT_API_URL"),
        api_key=os.getenv("APPOINTMENT_API_KEY"),
    )
    log_call_event("llm_ready", model="gpt-4o-mini")
    
    stt = DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        live_options=LiveOptions(
            model="nova-3",
            language="hi",
            smart_format=True,
            punctuate=True,
            interim_results=True,
        )
    )
    log_call_event("stt_ready", provider="deepgram", model="nova-3", language="hi")

    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID"),
            model=os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5"),
            language=Language.HI,
            stability=0.45,
            similarity_boost=0.80,
            style=0.25,
            use_speaker_boost=True,
            speed=0.95,
            apply_text_normalization="on",
        ),
        enable_ssml_parsing=True,
     )
    log_call_event(
        "tts_ready",
        provider="elevenlabs",
        model=os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5"),
    )


    tools = build_create_data_tools_schema()

    # ✅ Single consolidated system message — move ALL instructions into prompt.txt
    system_prompt = load_system_prompt()
    messages = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]
    if call_id:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Active call context: call_id is "
                    f"{call_id}. Use this value for hangup_call if needed."
                ),
            }
        )

    context = LLMContext(messages=messages, tools=tools)
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                stop=[TurnAnalyzerUserTurnStopStrategy(
                    turn_analyzer=LocalSmartTurnAnalyzerV3()
                )]
            ),
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )
    user_aggregator = context_aggregator.user()
    assistant_aggregator = context_aggregator.assistant()

    transcript_log: List[Dict[str, Any]] = []
    audiobuffer = AudioBufferProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            audiobuffer,
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[
            MetricsLogObserver(),
            UserBotLatencyLogObserver(),
        ],
    )

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        for function_call in function_calls:
            log_call_event(
                "function_call_requested",
                function_name=function_call.function_name,
                tool_call_id=function_call.tool_call_id,
                arguments=function_call.arguments,
            )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        ensure_observer_log_sinks()
        log_call_event("client_connected")
        await audiobuffer.start_recording()
        await task.queue_frames([TTSSpeakFrame(text=INITIAL_GREETING, append_to_context=True)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        log_call_event("client_disconnected")
        await save_transcript(transcript_log)
        await task.cancel()

    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        log_call_event("user_speaking_started", strategy=str(strategy))

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message: UserTurnStoppedMessage):
        log_call_event(
            "user_speaking_stopped",
            strategy=str(strategy),
            timestamp=str(message.timestamp),
        )
        log_call_event(
            "user_transcription_final",
            text=message.content,
            timestamp=str(message.timestamp),
        )
        transcript_log.append(
            {
                "role": "user",
                "content": message.content,
                "timestamp": message.timestamp,
            }
        )

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message: AssistantTurnStoppedMessage):
        log_call_event(
            "assistant_response_final",
            text=message.content,
            timestamp=str(message.timestamp),
        )
        transcript_log.append(
            {
                "role": "assistant",
                "content": message.content,
                "timestamp": message.timestamp,
            }
        )

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        await save_audio(audio, sample_rate, num_channels)

    runner = PipelineRunner(handle_sigint=handle_sigint, force_gc=True)
    await runner.run(task)


async def bot(runner_args: RunnerArguments, testing: Optional[bool] = False):
    """Main bot entry point compatible with Pipecat Cloud."""
    configure_event_log_sink(RECORDINGS_DIR)
    _, call_data = await parse_telephony_websocket(runner_args.websocket)
    logger.info(f"📋 Call data: {call_data}")
    call_id = extract_call_id(call_data)
    logger.info(f"📞 Active call_id selected for tools: {call_id or '[missing]'}")
    set_call_context(call_id)
    log_call_event("call_initialized", call_id=call_id, stream_id=call_data.get("stream_id"))
    set_active_call_id(call_id)

    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_id,
        account_sid=call_data.get("account_id", ""),
        params=TwilioFrameSerializer.InputParams(
            twilio_sample_rate=8000,
            auto_hang_up=False,
        ),
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
        ),
    )

    await run_bot(transport, runner_args.handle_sigint, testing, call_id=call_id)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
