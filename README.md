# 🎙️ Virtual AI Interviewer Agent — Phase 1 Pipeline

An AI-powered conversational interview agent built using **LiveKit**, **Deepgram**, and **Mistral / Groq / Gemini**. It auto-detects and parses candidate resumes and job descriptions (supporting PDF, DOCX, and TXT), generates targeted interview questions, and conducts the interview session with support for dynamic on-the-spot follow-up questions.

---

## 📂 Project Structure & Files

Here is a breakdown of the core files in the `virtual-ai-interviewer` directory:

* **[agent.py](file:///d:/voice-pipeline/virtual-ai-interviewer/agent.py)**: The main entrypoint for the LiveKit voice agent. Initializes session state and orchestrates STT, LLM reasoning, and TTS.
* **[generate_questions.py](file:///d:/voice-pipeline/virtual-ai-interviewer/generate_questions.py)**: Parses `resume` and `jd` files from the project root and calls the LLM to write structured base questions.
* **[system_prompt.py](file:///d:/voice-pipeline/virtual-ai-interviewer/system_prompt.py)**: Contains the system directives defining the interviewer's professional, neutral persona (no formatting, no list output, strictly plain text).
* **[tools/agent_tools.py](file:///d:/voice-pipeline/virtual-ai-interviewer/tools/agent_tools.py)**: Contains state-transition tools:
  * `get_next_base_question`: Serves the next core question.
  * `log_question_response`: Logs answers dynamically to `interview_responses.json`.
  * `end_call`: Gracefully disconnects the session.
* **[pyproject.toml](file:///d:/voice-pipeline/virtual-ai-interviewer/pyproject.toml)**: Defines Python packages (`livekit-agents`, `livekit-plugins-openai`, `pypdf`, `python-docx`, `google-genai`).
* **[.env.local](file:///d:/voice-pipeline/virtual-ai-interviewer/.env.local)**: Configuration parameters for LLMs, VAD settings, and LiveKit connection URLs.

---

## 🛠️ API Services & Models Priority

### 1. Pre-Interview Question Generation (`generate_questions.py`):
```
🥇 Mistral (Primary)     → model: mistral-small-latest
🥈 Groq (Secondary)      → model: llama-3.1-8b-instant
🥉 Gemini (Last Resort)  → model: gemini-2.0-flash
```

### 2. Live Conversation Chat (`agent.py`):
```
🥇 Groq (Primary, supporting native tool use)  → model: llama-3.1-8b-instant
🥈 Gemini (Secondary Fallback)                 → model: gemini-2.0-flash
```

---

## ⚙️ How to Set Up & Run

### Step 1: Set Up Local LiveKit Server (Recommended)
Running LiveKit locally reduces latency and developer API costs.

* **Via Docker**:
  ```powershell
  docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp livekit/livekit-server --dev
  ```
* **Configure keys in [.env.local](file:///d:/voice-pipeline/virtual-ai-interviewer/.env.local)**:
  ```env
  LIVEKIT_URL=ws://localhost:7880
  LIVEKIT_API_KEY=devkey
  LIVEKIT_API_SECRET=secret
  ```

### Step 2: Install Dependencies
Initialize virtual environment and sync dependencies:
```powershell
uv sync
```

### Step 3: Download Model Files
Download local voice activity detection (VAD) files:
```powershell
uv run python agent.py download-files
```

### Step 4: Place Files & Generate Questions
1. Place a candidate resume (`resume.pdf`, `resume.docx`, or `resume.txt`) and a job description (`jd.pdf`, `jd.docx`, or `jd.txt`) in the root directory.
2. Run the generation script:
   ```powershell
   uv run python generate_questions.py
   ```
   This will auto-detect your files and produce [questions.json](file:///d:/voice-pipeline/virtual-ai-interviewer/questions.json).

### Step 5: Start the Agent
Run the agent in interactive console mode to test via your CLI:
```powershell
uv run python agent.py console
```

---

## 📊 Viewing Results

* **Live Logs**:
  * Candidate transcriptions print in green: `[CANDIDATE] >>> ...`
  * Interviewer speech prints in blue: `[INTERVIEWER] <<< ...`
* **Scorecard**:
  * After the interview ends, review the complete transcript log (covering core questions and dynamic follow-ups) in `interview_responses.json`.
