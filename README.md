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

### Step 1: Navigate to the Project Directory
Open your terminal and ensure you are inside the project folder:
```powershell
cd virtual-ai-interviewer
```

### Step 2: Create a Virtual Environment
Create a clean local Python virtual environment (`.venv`) inside the project folder:
```powershell
python -m venv .venv
```

### Step 3: Activate the Virtual Environment
Activate the environment to ensure Python uses the local virtual environment packages:

* **On Windows (PowerShell):**
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
* **On macOS/Linux:**
  ```bash
  source .venv/bin/activate
  ```

Once activated, your terminal prompt will be prefixed with `(livekit-agent-voice)`.

### Step 4: Install Dependencies
Use `uv` (or standard `pip`) to install the dependencies defined in `pyproject.toml`:

```powershell
uv pip install -r pyproject.toml
```
*(Alternatively, you can run `pip install .`)*

### Step 5: Download Model Files
Download local voice activity detection (VAD) models and other plugin assets:
```powershell
uv run -m livekit.agents download-files
```
*(If `uv` is not used, run `python -m livekit.agents download-files`)*

### Step 6: Place Files & Generate Questions
1. Place a candidate resume (`resume.pdf`, `resume.docx`, or `resume.txt`) and a job description (`jd.pdf`, `jd.docx`, or `jd.txt`) in the `virtual-ai-interviewer` directory.
2. Run the question generation script:
   ```powershell
   python generate_questions.py
   ```
   This will auto-detect your files and produce [questions.json](file:///d:/voice-pipeline/virtual-ai-interviewer/questions.json).

### Step 7: Start the Agent

#### Run Mode A: Interactive Console Mode (Local CLI Testing)
Test the agent using your terminal/keyboard/microphone without a web frontend:
```powershell
python agent.py console
```

#### Run Mode B: Agent Worker Mode (For Production/Web Frontend Integration)
Start the agent worker to listen for incoming LiveKit connection requests:
```powershell
python agent.py dev
```

---

## 📊 Viewing Results

* **Live Logs**:
  * Candidate transcriptions print in green: `[CANDIDATE] >>> ...`
  * Interviewer speech prints in blue: `[INTERVIEWER] <<< ...`
* **Scorecard**:
  * After the interview ends, review the complete transcript log (covering core questions and dynamic follow-ups) in [interview_responses.json](file:///d:/voice-pipeline/virtual-ai-interviewer/interview_responses.json).
