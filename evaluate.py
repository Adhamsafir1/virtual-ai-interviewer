import os
import sys
import json
import asyncio
from openai import AsyncOpenAI

async def evaluate_interview(input_file: str, output_file: str):
    print(f"\n[EVALUATION] Starting post-interview evaluation using {input_file}...\n")
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    responses = data.get("responses", [])
    if not responses:
        print("No responses to evaluate.")
        return

    # Format transcript for LLM
    transcript_text = ""
    for r in responses:
        speaker = r.get("speaker", "Unknown").upper()
        text = r.get("text", "")
        transcript_text += f"{speaker}: {text}\n"

    prompt = f"""
You are an expert technical interviewer evaluating a candidate's performance based on the following interview transcript.
For each question asked by the INTERVIEWER, provide an evaluation of the CANDIDATE's answer.

Return ONLY a JSON object with the following structure:
{{
  "interview_summary": "Overall summary of performance",
  "average_score": 8.5,
  "strengths": ["List of strengths"],
  "weaknesses": ["List of weaknesses"],
  "evaluated_questions": [
    {{
      "question": "The question asked",
      "answer_summary": "A 1-sentence summary of the candidate's answer",
      "score": 8,
      "feedback": "1-sentence feedback on the answer"
    }}
  ]
}}

Transcript:
{transcript_text}
"""
    client = AsyncOpenAI()
    
    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result_json = completion.choices[0].message.content
        result_data = json.loads(result_json)

        # Merge with original metadata
        data["final_report"] = result_data
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        print(f"\n[EVALUATION] Evaluation complete! Saved to {output_file}\n")

    except Exception as e:
        print(f"Evaluation failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python evaluate.py <input.json> <output.json>")
        sys.exit(1)
    
    asyncio.run(evaluate_interview(sys.argv[1], sys.argv[2]))
