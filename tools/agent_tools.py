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
    async def log_response_and_get_next_question(
        self,
        context: RunContext,
        question: str,
        answer: str,
        is_followup: bool,
        score: int,
        feedback: str,
    ) -> str:
        """Logs the candidate's response AND automatically returns the next base question in a single operation.
        Use this primary tool to save a round-trip when moving to the next interview question.

        Args:
            question: The exact question that was asked.
            answer: A brief 1-sentence summary of the candidate's response (under 15 words).
            is_followup: True if this was a dynamic follow-up question, False if it was a base question.
            score: An integer from 0 to 10 evaluating accuracy, depth, and clarity.
            feedback: A brief 1-sentence explanation of the score (under 15 words).
        """
        entry = {
            "question_number": len(self.responses_history) + 1,
            "question": question,
            "answer": answer,
            "is_followup": is_followup,
            "score": score,
            "feedback": feedback,
            "timestamp": datetime.now().isoformat(),
        }
        self.responses_history.append(entry)
        self._save_responses()

        logger.info(
            "Logged response #%d (Score: %d/10, followup=%s): %s",
            entry["question_number"],
            score,
            is_followup,
            question[:60],
        )

        idx = self.current_question_index
        if idx >= len(self.questions):
            return (
                f"Response #{entry['question_number']} logged with score {score}/10. "
                "ALL_QUESTIONS_COMPLETE: All base questions have been asked. Thank the candidate, summarize, and call end_call."
            )

        question_data = self.questions[idx]
        self.current_question_index = idx + 1
        question_text = question_data.get("question", "")
        category = question_data.get("category", "general")
        question_id = question_data.get("id", idx + 1)
        total = len(self.questions)

        logger.info("Serving base question %d/%d: %s", question_id, total, question_text[:80])

        return (
            f"Response #{entry['question_number']} logged with score {score}/10. "
            f"NEXT BASE_QUESTION ({question_id}/{total}) [{category}]: {question_text}"
        )

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
        score: int,
        feedback: str,
    ) -> str:
        """Logs a question-answer pair from the interview, evaluating the candidate's response.
        Call this exactly ONCE after the candidate has provided a complete answer to a question.

        Args:
            question: The exact question that was asked.
            answer: A summary of the candidate's response.
            is_followup: True if this was a dynamic follow-up question, False if it was a base question.
            score: An integer from 0 to 10 evaluating the accuracy, depth, and semantic understanding of the candidate's answer. Evaluate semantically, not just by exact keyword matching.
            feedback: Constructive feedback or a brief explanation of why the score was given.
        """
        entry = {
            "question_number": len(self.responses_history) + 1,
            "question": question,
            "answer": answer,
            "is_followup": is_followup,
            "score": score,
            "feedback": feedback,
            "timestamp": datetime.now().isoformat(),
        }
        self.responses_history.append(entry)
        self._save_responses()

        logger.info(
            "Logged response #%d (Score: %d/10, followup=%s): %s",
            entry["question_number"],
            score,
            is_followup,
            question[:60],
        )

        return f"Response #{entry['question_number']} logged successfully with score {score}/10."

    @function_tool
    async def end_call(
        self,
        context: RunContext,
        interview_summary: str,
        average_score: float,
        strengths: list[str],
        weaknesses: list[str],
    ) -> str:
        """Ends the interview call. Call this exactly when the interview is complete
        and you have thanked the candidate and given a brief closing summary.

        Args:
            interview_summary: A comprehensive text summary of the candidate's overall performance.
            average_score: The calculated average score across all questions.
            strengths: A list of the candidate's strongest areas demonstrated during the interview.
            weaknesses: A list of areas where the candidate needs improvement.
        """
        logger.info("Interview concluded. Disconnecting...")

        # Persist a machine-readable final report for downstream review.
        final_report = {
            "type": "FINAL_REPORT",
            "interview_summary": interview_summary,
            "average_score": average_score,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "timestamp": datetime.now().isoformat(),
        }
        self.final_report = final_report
        self.responses_history.append(final_report)

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
