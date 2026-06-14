"""Demo paper generation for offline/no-key mode."""

from typing import Dict, List


def _build_question(topic_id: str, topic_name: str, index: int) -> Dict:
    question_number = index + 1
    return {
        "question": f"[{topic_name}] Practice question {question_number}: Which option best reflects the key idea?",
        "options": [
            "A concise definition of the topic",
            "An unrelated historical fact",
            "A random implementation detail",
            "A statement that contradicts the topic",
        ],
        "answer": "A",
        "explanation": "The best answer is the concise definition because it directly expresses the core concept.",
        "topic_id": topic_id,
        "difficulty_level": "Remember",
    }


def generate_demo_paper(syllabus: Dict) -> List[Dict]:
    paper: List[Dict] = []
    for mapping in syllabus.get("topic_mappings", []):
        topic_id = mapping.get("topic_id", "unknown_topic")
        min_questions = max(1, int(mapping.get("min_questions", 3)))
        for idx in range(min_questions):
            paper.append(_build_question(topic_id, topic_id, idx))
    return paper
