from livekit.agents.llm import function_tool
from livekit.agents import RunContext
import json
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger("agent_tools")

RESPONSES_FILE = "interview_responses.json"


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
                "responses": self.responses_history,
            }
            with open(RESPONSES_FILE, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            logger.info("Saved %d responses to %s", len(self.responses_history), RESPONSES_FILE)
        except Exception as e:
            logger.error("Failed to save responses: %s", e)

    @function_tool
    async def get_next_base_question(self, context: RunContext) -> str:
        """Retrieves the next base interview question from the pre-generated list.
        Call this to move to the next core interview topic.
        Returns the question text, or a completion message if all questions are done."""
        idx = self.current_question_index

        if idx >= len(self.questions):
            return (
                "ALL_QUESTIONS_COMPLETE: All base interview questions have been asked. "
                "Please thank the candidate, summarize the interview, and call end_call to finish."
            )

        question_data = self.questions[idx]
        self.current_question_index = idx + 1

        question_text = question_data.get("question", "")
        category = question_data.get("category", "general")
        question_id = question_data.get("id", idx + 1)
        total = len(self.questions)

        logger.info("Serving base question %d/%d: %s", question_id, total, question_text[:80])

        return (
            f"BASE_QUESTION ({question_id}/{total}) [{category}]: {question_text}"
        )

    @function_tool
    async def log_question_response(
        self,
        context: RunContext,
        question: str,
        answer: str,
        is_followup: bool,
    ) -> str:
        """Logs a question-answer pair from the interview.
        Call this EVERY TIME after the candidate finishes answering a question,
        whether it is a base question or an on-the-spot follow-up question.

        Args:
            question: The exact question that was asked.
            answer: A summary of the candidate's response.
            is_followup: True if this was a dynamic follow-up question, False if it was a base question.
        """
        entry = {
            "question_number": len(self.responses_history) + 1,
            "question": question,
            "answer": answer,
            "is_followup": is_followup,
            "timestamp": datetime.now().isoformat(),
        }
        self.responses_history.append(entry)
        self._save_responses()

        logger.info(
            "Logged response #%d (followup=%s): %s",
            entry["question_number"],
            is_followup,
            question[:60],
        )

        return f"Response #{entry['question_number']} logged successfully."

    @function_tool
    async def end_call(self, context: RunContext) -> str:
        """Ends the interview call. Call this exactly when the interview is complete
        and you have thanked the candidate and given a brief closing summary."""
        logger.info("Interview concluded. Disconnecting...")

        # Save final responses
        self._save_responses()

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
