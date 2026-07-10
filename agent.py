import logging
import json
import os
import uuid
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import aiohttp
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    RunContext,
    TurnHandlingOptions,
)
from livekit.agents.llm import function_tool
from livekit.plugins import noise_cancellation, silero, deepgram, google, openai
from livekit.agents import llm, tts, stt
from livekit.agents import AgentStateChangedEvent, MetricsCollectedEvent, metrics
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN


logger = logging.getLogger("agent")

load_dotenv(".env.local")


from system_prompt import SYSTEM_PROMPT, WELCOME_MESSAGE
from tools.agent_tools import AgentToolsMixin

# ---------------------------------------------------------------------------
# Questions Loader
# ---------------------------------------------------------------------------

QUESTIONS_FILE = "questions.json"
DEFAULT_QUESTIONS = [
    {
        "id": 1,
        "category": "technical",
        "question": "Can you walk me through a recent project you worked on and the technical decisions you made?",
        "expected_key_points": ["architecture choices", "tradeoffs", "outcome"],
        "difficulty": "medium",
    },
    {
        "id": 2,
        "category": "behavioral",
        "question": "Tell me about a time you faced a challenging deadline. How did you manage it?",
        "expected_key_points": ["prioritization", "communication", "result"],
        "difficulty": "medium",
    },
    {
        "id": 3,
        "category": "technical",
        "question": "How do you approach debugging a complex issue in production?",
        "expected_key_points": ["logging", "reproduction", "root cause analysis"],
        "difficulty": "medium",
    },
]


def load_questions() -> list[dict]:
    """Load questions from questions.json, falling back to defaults if missing."""
    if os.path.exists(QUESTIONS_FILE):
        try:
            with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
                questions = json.load(f)
            logger.info("Loaded %d questions from %s", len(questions), QUESTIONS_FILE)
            return questions
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to load %s: %s. Using default questions.", QUESTIONS_FILE, e)
    else:
        logger.warning("%s not found. Using default questions. Run generate_questions.py first.", QUESTIONS_FILE)
    return DEFAULT_QUESTIONS


# ---------------------------------------------------------------------------
# Interview Agent
# ---------------------------------------------------------------------------

class Assistant(Agent, AgentToolsMixin):
    def __init__(self, room) -> None:
        super().__init__(
            instructions=SYSTEM_PROMPT,
        )
        self.room = room

        # Interview state
        self.questions = load_questions()
        self.current_question_index = 0
        self.responses_history = []

        logger.info(
            "Interview Agent initialized with %d base questions.",
            len(self.questions),
        )

    def tts_node(self, text, model_settings):
        """Sanitize text to strip formatting characters before they are spoken."""
        async def clean_text_stream(text_stream):
            async for chunk in text_stream:
                # Strip double asterisks, single asterisks, hashtags, and formatting underscores
                chunk = chunk.replace("*", "").replace("_", "").replace("#", "")
                yield chunk
        
        cleaned = clean_text_stream(text)
        return super().tts_node(cleaned, model_settings)


# ---------------------------------------------------------------------------
# Fallback LLM Wrapper
# ---------------------------------------------------------------------------

class FallbackLLM(llm.LLM):
    def __init__(self, *, primary: llm.LLM, fallback: llm.LLM) -> None:
        super().__init__()
        self._primary = primary
        self._fallback = fallback

    @property
    def model(self) -> str:
        return self._primary.model

    @property
    def provider(self) -> str:
        return self._primary.provider

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls=NOT_GIVEN,
        tool_choice=NOT_GIVEN,
        extra_kwargs=NOT_GIVEN,
    ) -> llm.LLMStream:
        return _FallbackLLMStream(
            self,
            primary=self._primary,
            fallback=self._fallback,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
            parallel_tool_calls=parallel_tool_calls,
            tool_choice=tool_choice,
            extra_kwargs=extra_kwargs,
        )

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()


class _FallbackLLMStream(llm.LLMStream):
    def __init__(
        self,
        llm_parent: FallbackLLM,
        *,
        primary: llm.LLM,
        fallback: llm.LLM,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
        parallel_tool_calls=NOT_GIVEN,
        tool_choice=NOT_GIVEN,
        extra_kwargs=NOT_GIVEN,
    ) -> None:
        super().__init__(llm_parent, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._primary = primary
        self._fallback = fallback
        self._parallel_tool_calls = parallel_tool_calls
        self._tool_choice = tool_choice
        self._extra_kwargs = extra_kwargs

    async def _forward(self, provider: llm.LLM) -> None:
        stream = provider.chat(
            chat_ctx=self._chat_ctx,
            tools=self._tools,
            conn_options=self._conn_options,
            parallel_tool_calls=self._parallel_tool_calls,
            tool_choice=self._tool_choice,
            extra_kwargs=self._extra_kwargs,
        )
        async with stream:
            async for chunk in stream:
                self._event_ch.send_nowait(chunk)

    async def _run(self) -> None:
        try:
            await self._forward(self._primary)
        except Exception as primary_error:
            logger.warning("Primary LLM failed; falling back to next provider: %s", primary_error)
            await self._forward(self._fallback)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def entrypoint(ctx: JobContext):
    llm_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    groq_model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    groq_api_key = os.getenv("GROQ_API_KEY", "")
    mistral_model = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
    mistral_api_key = os.getenv("MISTRAL_API_KEY", "")
    stt_model = os.getenv("DEEPGRAM_STT_MODEL", "nova-2")
    tts_model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")
    stt_base_url = os.getenv("DEEPGRAM_STT_BASE_URL", "https://api.deepgram.com/v1/listen")
    
    gemini_llm = google.LLM(model=llm_model)
    primary_llm = gemini_llm
    
    if groq_api_key:
        primary_llm = FallbackLLM(
            primary=openai.LLM(
                api_key=groq_api_key,
                base_url="https://api.groq.com/openai/v1",
                model=groq_model,
            ),
            fallback=gemini_llm,
        )
        
    if mistral_api_key:
        primary_llm = FallbackLLM(
            primary=openai.LLM(
                api_key=mistral_api_key,
                base_url="https://api.mistral.ai/v1",
                model=mistral_model,
            ),
            fallback=primary_llm,
        )

    session = AgentSession(
        # Current livekit deepgram STT plugin uses v1 listen params; nova-3 is compatible.
        stt=deepgram.STT(model=stt_model, language="en", base_url=stt_base_url),
        llm=primary_llm,
        # English voice output via Deepgram Aura.
        tts=deepgram.TTS(model=tts_model),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(
            turn_detection="vad",
            interruption={
                "mode": "vad",
            },
            endpointing={
                "min_delay": float(os.getenv("EOU_MIN_DELAY_SEC", "0.15")),
                "max_delay": float(os.getenv("EOU_MAX_DELAY_SEC", "0.6")),
            },
        ),
        # Favor latency over token efficiency for a more conversational feel.
        preemptive_generation=True,
    )
    usage_collector = metrics.UsageCollector()
    last_eou_metrics: metrics.EOUMetrics | None = None

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        nonlocal last_eou_metrics
        if ev.metrics.type == "eou_metrics":
            last_eou_metrics = ev.metrics

        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)


    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info("Usage summary: %s", summary)


    ctx.add_shutdown_callback(log_usage)

    @session.on("agent_state_changed")
    def _on_agent_state_changed(ev: AgentStateChangedEvent):
        if (
            ev.new_state == "speaking"
            and last_eou_metrics
            and session.current_speech
            and getattr(last_eou_metrics, "speech_id", None) == session.current_speech.id
        ):
            # Try to grab the time it finished speaking
            speaking_time = getattr(last_eou_metrics, "timestamp", getattr(last_eou_metrics, "end_of_utterance_time", ev.created_at))
            delta = ev.created_at - speaking_time
            delta_ms = delta * 1000 if isinstance(delta, (int, float)) else delta.total_seconds() * 1000
            logger.info("Time to first audio frame: %sms", delta_ms)

    

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(ev):
        if ev.is_final and ev.transcript.strip():
            print(f"\n\033[92m[CANDIDATE] >>> {ev.transcript.strip()}\033[0m\n", flush=True)

    @session.on("conversation_item_added")
    def _on_conversation_item_added(ev):
        msg = ev.item
        if hasattr(msg, "role") and msg.role == "assistant":
            if hasattr(msg, "text_content"):
                text = msg.text_content
                if text and text.strip():
                    print(f"\n\033[94m[INTERVIEWER] <<< {text.strip()}\033[0m\n", flush=True)

    agent = Assistant(room=ctx.room)

    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()

    # Speak the welcome/first message immediately after connection
    session.say(WELCOME_MESSAGE, allow_interruptions=True)
    print(f"\n\033[94m[INTERVIEWER] <<< {WELCOME_MESSAGE}\033[0m\n", flush=True)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
