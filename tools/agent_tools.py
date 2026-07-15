from livekit.agents.llm import function_tool
from livekit.agents import RunContext
import json
import logging
import asyncio
import subprocess
import sys
from datetime import datetime

logger = logging.getLogger("agent_tools")

RESPONSES_FILE = "raw_transcript.json"


class AgentToolsMixin:
    """
    Tools available to the AI Interviewer Agent.
    Requires `self.room`, `self.questions`, `self.current_question_index`,
    and `self.responses_history` to be set on the agent instance.
    """

    def _save_responses(self):
        """Persist the current responses history to a JSON file."""
        try:
            output = {
                "interview_date": datetime.now().isoformat(),
                "total_base_questions": len(self.questions),
                "status": "completed" if getattr(self, "final_report", None) else "in_progress",
                "responses": self.responses_history,
            }
            if getattr(self, "final_report", None):
                output["final_report"] = self.final_report
            with open(RESPONSES_FILE, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            logger.info("Saved %d responses to %s", len(self.responses_history), RESPONSES_FILE)
        except Exception as e:
            logger.error("Failed to save responses: %s", e)

    @function_tool
    async def get_next_question(self, context: RunContext) -> str:
        """Retrieves the next base interview question from the pre-generated list.
        Call this to move to the next core interview topic when the candidate finishes their answer.
        Returns the question text, or a completion message if all questions are done.
        """
        idx = self.current_question_index

        if idx >= len(self.questions):
            return (
                "ALL_QUESTIONS_COMPLETE: All base interview questions have been asked. "
                "Please thank the candidate, state that the interview is complete, and call the end_call tool to finish."
            )

        question_data = self.questions[idx]
        self.current_question_index = idx + 1

        question_text = question_data.get("question", "")
        category = question_data.get("category", "general")
        question_id = question_data.get("id", idx + 1)
        total = len(self.questions)

        logger.info("Serving base question %d/%d: %s", question_id, total, question_text[:80])

        return (
            f"NEXT BASE_QUESTION ({question_id}/{total}) [{category}]: {question_text}"
        )

    @function_tool
    async def end_call(self, context: RunContext) -> str:
        """Ends the interview call. Call this exactly when the interview is complete
        and you have thanked the candidate.
        """
        logger.info("Interview concluded. Disconnecting and processing final report...")

        # Save final responses
        self._save_responses()

        # Launch background evaluation
        try:
            logger.info("Triggering post-interview evaluation...")
            subprocess.Popen(
                ["uv", "run", "python", "evaluate.py", "raw_transcript.json", "interview_responses.json"],
                stdout=sys.stdout,
                stderr=sys.stderr,
                start_new_session=True
            )
        except Exception as e:
            logger.error(f"Failed to start evaluation: {e}")

        if hasattr(self, "room") and self.room:
            async def delayed_disconnect():
                await asyncio.sleep(3.0)
                await self.room.disconnect()
                import os
                import signal
                os.kill(os.getpid(), signal.SIGINT)

            asyncio.create_task(delayed_disconnect())
            return "Ending the interview now. Say your goodbye!"

        return "Failed to end call: No room context."
