import logging
import json
import os
import uuid
import warnings
from datetime import datetime

warnings.filterwarnings("ignore", category=DeprecationWarning)

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    RunContext,
    TurnHandlingOptions,
)
from livekit.plugins import noise_cancellation, silero, deepgram, openai
from livekit.agents import llm
from livekit.agents import AgentStateChangedEvent, MetricsCollectedEvent, metrics
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN
from livekit.agents.voice.agent_session import SessionConnectOptions


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
        self.questions = load_questions()
        formatted_questions = "\n".join(f"Question #{i+1}: {q.get('question', '')}" for i, q in enumerate(self.questions))
        full_instructions = f"""{SYSTEM_PROMPT}

PREPARED INTERVIEW QUESTIONS (ASK THESE EXACT 9 QUESTIONS IN ORDER):
{formatted_questions}

STEPS FOR THE INTERVIEW:
- Welcome message has been spoken.
- Candidate is currently answering the intro/welcome.
- After Candidate responds to intro, ask Question #1 from the list above.
- Proceed sequentially through Question #1 to Question #9.
- After Candidate responds to Question #9, thank the candidate and call `end_call`.
- DO NOT invent generic questions. You MUST ask the exact prepared questions above in order.
"""
        super().__init__(
            instructions=full_instructions,
        )
        self.room = room

        # Interview state
        self.current_question_index = 0
        self.responses_history = []
        self.last_asked_question = WELCOME_MESSAGE

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
    def __init__(self, providers: list[llm.LLM]) -> None:
        super().__init__()
        self._providers = providers

    @property
    def model(self) -> str:
        return self._providers[0].model if self._providers else ""

    @property
    def provider(self) -> str:
        return self._providers[0].provider if self._providers else ""

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
            providers=self._providers,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
            parallel_tool_calls=parallel_tool_calls,
            tool_choice=tool_choice,
            extra_kwargs=extra_kwargs,
        )

    async def aclose(self) -> None:
        for p in self._providers:
            await p.aclose()


class _FallbackLLMStream(llm.LLMStream):
    def __init__(
        self,
        llm_parent: FallbackLLM,
        *,
        providers: list[llm.LLM],
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
        parallel_tool_calls=NOT_GIVEN,
        tool_choice=NOT_GIVEN,
        extra_kwargs=NOT_GIVEN,
    ) -> None:
        super().__init__(llm_parent, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._providers = providers
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
        for i, provider in enumerate(self._providers):
            try:
                await self._forward(provider)
                return
            except Exception as provider_error:
                logger.warning("LLM provider #%d (%s) failed: %s. Trying fallback...", i + 1, provider, provider_error)
        logger.error("All configured LLM fallback providers failed.")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def entrypoint(ctx: JobContext):
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    openai_llm_model = os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini")
    mistral_model = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
    mistral_api_key = os.getenv("MISTRAL_API_KEY", "")
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

    providers: list[llm.LLM] = []

    # 1. Primary: Groq (Fastest)
    if groq_api_key:
        logger.info("Configured Primary LLM: Groq (%s)", groq_model)
        providers.append(
            openai.LLM(
                api_key=groq_api_key,
                base_url="https://api.groq.com/openai/v1",
                model=groq_model,
                temperature=0.2,
            )
        )

    # 2. Fallback 1: OpenAI
    if openai_api_key:
        logger.info("Configured Fallback #1 LLM: OpenAI (%s)", openai_llm_model)
        providers.append(openai.LLM(model=openai_llm_model, api_key=openai_api_key, temperature=0.2))

    # 3. Fallback 2: Mistral
    if mistral_api_key:
        logger.info("Configured Fallback #2 LLM: Mistral (%s)", mistral_model)
        providers.append(
            openai.LLM(
                api_key=mistral_api_key,
                base_url="https://api.mistral.ai/v1",
                model=mistral_model,
                temperature=0.2,
            )
        )

    if not providers:
        raise RuntimeError("No valid LLM providers configured.")

    primary_llm = providers[0] if len(providers) == 1 else FallbackLLM(providers=providers)

    deepgram_stt_model = os.getenv("DEEPGRAM_STT_MODEL", "flux-general-en").strip()
    if deepgram_stt_model != "flux-general-en":
        raise RuntimeError(
            "DEEPGRAM_STT_MODEL must be 'flux-general-en' when using Deepgram STTv2. "
            f"Received: {deepgram_stt_model!r}."
        )

    # Flux provides conversational English transcription. We use Silero for fast VAD.
    stt_plugin = deepgram.STTv2(
        model=deepgram_stt_model,
        eot_timeout_ms=800,  # Tighten End-of-Turn detection timeout
    )
    # Deepgram TTS streams audio as the response is generated.
    tts_plugin = deepgram.TTS(
        model=os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-hermes-en").strip(),
        sample_rate=16000,   # Use smaller chunks for faster TTFB streaming
    )

    vad = silero.VAD.load(
        min_silence_duration=0.3,
        activation_threshold=0.45,
        prefix_padding_duration=0.3,
    )

    session = AgentSession(
        stt=stt_plugin,
        vad=vad,
        llm=primary_llm,
        tts=tts_plugin,
        turn_handling=TurnHandlingOptions(
            turn_detection="vad",
            endpointing={
                "min_delay": 0.08,
                "max_delay": 0.2,
            },
            preemptive_generation={
                "enabled": True,
                "preemptive_tts": True,  # Preemptively start TTS as well
            },
            interruption={
                "enabled": True,
                "min_duration": 0.3,
                "min_words": 0,
                "false_interruption_timeout": None,
            },
        ),
        aec_warmup_duration=0.0,
        conn_options=SessionConnectOptions(
            llm_conn_options=APIConnectOptions(max_retry=1, timeout=3.0),
        ),
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
        try:
            await background_audio.aclose()
        except Exception:
            pass

    ctx.add_shutdown_callback(log_usage)

    import asyncio
    silence_task = None
    is_listening = False

    def _reset_silence_timer():
        nonlocal silence_task
        if silence_task:
            silence_task.cancel()
        
        async def _silence_prompt():
            try:
                await asyncio.sleep(12)
                if is_listening:
                    session.say("I'm still here. Take your time, or let me know if you'd like me to repeat the question.", allow_interruptions=True)
            except asyncio.CancelledError:
                pass

        silence_task = asyncio.create_task(_silence_prompt())

    def _cancel_silence_timer():
        nonlocal silence_task
        if silence_task:
            silence_task.cancel()
            silence_task = None

    @session.on("agent_state_changed")
    def _on_agent_state_changed(ev: AgentStateChangedEvent):
        nonlocal is_listening
        is_listening = (ev.new_state == "listening")
        
        # Handle silence timeout when state changes
        if is_listening:
            _reset_silence_timer()
        else:
            _cancel_silence_timer()

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
        _cancel_silence_timer()
        if ev.is_final and ev.transcript.strip():
            transcript = ev.transcript.strip()
            print(f"\n\033[92m[CANDIDATE] >>> {transcript}\033[0m\n", flush=True)
            agent.responses_history.append({"speaker": "candidate", "text": transcript})

    @session.on("conversation_item_added")
    def _on_conversation_item_added(ev):
        msg = ev.item
        if hasattr(msg, "role") and msg.role == "assistant":
            if hasattr(msg, "text_content"):
                text = msg.text_content
                if text and text.strip():
                    clean_text = text.strip()
                    agent.last_asked_question = clean_text
                    print(f"\n\033[94m[INTERVIEWER] <<< {clean_text}\033[0m\n", flush=True)
                    # Deduplicate in case LiveKit fires it multiple times or we already logged it
                    if not agent.responses_history or agent.responses_history[-1].get("text") != clean_text:
                        agent.responses_history.append({"speaker": "interviewer", "text": clean_text})

    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.8),
        thinking_sound=[
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.8),
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.7),
        ],
    )

    agent = Assistant(room=ctx.room)

    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()
    await background_audio.start(room=ctx.room, agent_session=session)

    # Speak the welcome/first message immediately after connection
    session.say(WELCOME_MESSAGE, allow_interruptions=True)
    print(f"\n\033[94m[INTERVIEWER] <<< {WELCOME_MESSAGE}\033[0m\n", flush=True)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
