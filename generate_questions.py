"""
Pre-Interview Question Generator
---------------------------------
Reads a candidate resume and a job description (supports PDF, DOCX, TXT),
generates structured interview questions using LLMs.

Priority: Mistral (primary) → Groq (secondary) → Gemini (fallback)

Usage:
    uv run python generate_questions.py
    uv run python generate_questions.py --resume path/to/resume.pdf --jd path/to/jd.txt
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env.local")


# ---------------------------------------------------------------------------
# File Parsing Utilities
# ---------------------------------------------------------------------------

def extract_text_from_pdf(filepath: str) -> str:
    """Extract text from a PDF file using pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(filepath)
    pages_text = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)
    return "\n".join(pages_text)


def extract_text_from_docx(filepath: str) -> str:
    """Extract text from a DOCX file using python-docx."""
    from docx import Document

    doc = Document(filepath)
    paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
    return "\n".join(paragraphs)


def extract_text_from_txt(filepath: str) -> str:
    """Read plain text from a TXT file. Tries multiple encodings."""
    for encoding in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
        try:
            with open(filepath, "r", encoding=encoding) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Last resort: read as bytes and decode loosely
    with open(filepath, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def extract_text(filepath: str) -> str:
    """Auto-detect file format and extract text content."""
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(filepath)
    elif ext in (".docx", ".doc"):
        return extract_text_from_docx(filepath)
    elif ext == ".txt":
        return extract_text_from_txt(filepath)
    else:
        raise ValueError(f"Unsupported file format: {ext}. Supported: .pdf, .docx, .txt")


# ---------------------------------------------------------------------------
# Auto-Discovery
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".txt"]


def discover_file(base_name: str, search_dir: str = ".") -> str | None:
    """Search for a file matching `base_name.*` with a supported extension."""
    for ext in SUPPORTED_EXTENSIONS:
        candidate = Path(search_dir) / f"{base_name}{ext}"
        if candidate.exists():
            return str(candidate)
    return None


# ---------------------------------------------------------------------------
# Question Generation Prompt
# ---------------------------------------------------------------------------

GENERATION_PROMPT = """\
You are an expert technical interviewer. You have been given a candidate's resume and a job description (JD).

**Your Task**: Generate exactly 7 structured interview questions that:
1. Test the candidate's skills against the JD requirements.
2. Include a mix of technical, behavioral, and situational questions.
3. Probe gaps where the resume does not cover JD requirements.

**For each question, produce a JSON object with these fields:**
- "id": integer (1-7)
- "category": one of "Technical", "Behavioral", "Situational", "Gap Analysis"
- "question": the full question text
- "expected_key_points": a list of 2-4 key points you expect in a strong answer
- "difficulty": one of "Easy", "Medium", "Hard"


**Output Format**: Return ONLY a JSON array of 7 question objects. No markdown, no explanation. 4 Technical Questions ,  1 Technical Gap Analysis , 1 Behavioral and 1 Situational Question

---

## CANDIDATE RESUME:
{resume_text}

---

## JOB DESCRIPTION:
{jd_text}
"""


def parse_json_response(raw_text: str) -> list[dict]:
    """Parse a JSON array from an LLM response, stripping markdown fences if present."""
    text = raw_text.strip()
    # Strip markdown code fences if the model wraps the output
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    return json.loads(text)


# ---------------------------------------------------------------------------
# 1. Mistral Generator (Primary)
# ---------------------------------------------------------------------------

def generate_questions_mistral(resume_text: str, jd_text: str) -> list[dict]:
    """Call Mistral API to generate interview questions."""
    import httpx

    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY not set.")

    prompt = GENERATION_PROMPT.format(
        resume_text=resume_text[:8000],
        jd_text=jd_text[:4000],
    )

    payload = {
        "model": os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = httpx.post(
        "https://api.mistral.ai/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    questions = parse_json_response(content)
    if not isinstance(questions, list):
        raise ValueError("Expected a JSON array from Mistral.")
    return questions


# ---------------------------------------------------------------------------
# 2. Groq Generator (Secondary)
# ---------------------------------------------------------------------------

def generate_questions_groq(resume_text: str, jd_text: str) -> list[dict]:
    """Call Groq (Llama) as a secondary fallback."""
    import httpx

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set.")

    prompt = GENERATION_PROMPT.format(
        resume_text=resume_text[:6000],
        jd_text=jd_text[:3000],
    )

    payload = {
        "model": os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    questions = parse_json_response(content)
    if not isinstance(questions, list):
        raise ValueError("Expected a JSON array from Groq.")
    return questions


# ---------------------------------------------------------------------------
# 3. Gemini Generator (Last Resort)
# ---------------------------------------------------------------------------

def generate_questions_gemini(resume_text: str, jd_text: str, max_retries: int = 2) -> list[dict]:
    """Call Gemini 2.0 Flash as last resort. Retries on rate limit."""
    from google import genai

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set.")

    client = genai.Client(api_key=api_key)
    prompt = GENERATION_PROMPT.format(
        resume_text=resume_text[:8000],
        jd_text=jd_text[:4000],
    )

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
                contents=prompt,
            )
            questions = parse_json_response(response.text)
            if isinstance(questions, list):
                return questions
            raise ValueError("Expected a JSON array.")
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                wait = 30 * attempt
                print(f"  ⚠️  Gemini rate limited (attempt {attempt}/{max_retries}). Waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Gemini rate limit exceeded after all retries.")


# ---------------------------------------------------------------------------
# Unified Generator: Mistral → Groq → Gemini
# ---------------------------------------------------------------------------

def generate_questions(resume_text: str, jd_text: str) -> list[dict]:
    """Try Mistral first, then Groq, then Gemini as last resort."""

    # 1. Primary: Mistral
    if os.getenv("MISTRAL_API_KEY"):
        try:
            print("  → Trying Mistral (primary)...")
            return generate_questions_mistral(resume_text, jd_text)
        except Exception as e:
            print(f"  ⚠️  Mistral failed: {e}")

    # 2. Secondary: Groq
    if os.getenv("GROQ_API_KEY"):
        try:
            print("  → Trying Groq (secondary)...")
            return generate_questions_groq(resume_text, jd_text)
        except Exception as e:
            print(f"  ⚠️  Groq failed: {e}")

    # 3. Last resort: Gemini
    if os.getenv("GOOGLE_API_KEY"):
        try:
            print("  → Trying Gemini (last resort)...")
            return generate_questions_gemini(resume_text, jd_text)
        except Exception as e:
            print(f"  ❌ Gemini also failed: {e}")

    print("ERROR: All LLM providers failed. Cannot generate questions.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate interview questions from Resume + JD")
    parser.add_argument("--resume", type=str, default=None, help="Path to resume file (PDF/DOCX/TXT)")
    parser.add_argument("--jd", type=str, default=None, help="Path to job description file (PDF/DOCX/TXT)")
    parser.add_argument("--output", type=str, default="questions.json", help="Output file path")
    args = parser.parse_args()

    # Resolve resume path
    resume_path = args.resume
    if not resume_path:
        resume_path = discover_file("resume")
        if not resume_path:
            print("ERROR: No resume file found. Place a resume.pdf, resume.docx, or resume.txt in the project root,")
            print("       or pass --resume path/to/file.")
            sys.exit(1)

    # Resolve JD path
    jd_path = args.jd
    if not jd_path:
        jd_path = discover_file("jd")
        if not jd_path:
            print("ERROR: No JD file found. Place a jd.pdf, jd.docx, or jd.txt in the project root,")
            print("       or pass --jd path/to/file.")
            sys.exit(1)

    print(f"📄 Resume: {resume_path}")
    print(f"📋 JD:     {jd_path}")

    # Extract text
    print("Extracting text from resume...")
    resume_text = extract_text(resume_path)
    if not resume_text.strip():
        print("⚠️  WARNING: Resume file is empty or could not be parsed!")
    print(f"  → Extracted {len(resume_text)} characters from resume.")

    print("Extracting text from JD...")
    jd_text = extract_text(jd_path)
    if not jd_text.strip():
        print("⚠️  WARNING: JD file is empty or could not be parsed!")
        print("   Please add content to your JD file and try again.")
        sys.exit(1)
    print(f"  → Extracted {len(jd_text)} characters from JD.")

    # Generate questions
    print("🤖 Generating interview questions...")
    questions = generate_questions(resume_text, jd_text)

    # Save
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(questions, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Generated {len(questions)} questions → {args.output}")
    for q in questions:
        print(f"   [{q.get('category', '?')}] Q{q.get('id', '?')}: {q.get('question', '')[:80]}...")


if __name__ == "__main__":
    main()
