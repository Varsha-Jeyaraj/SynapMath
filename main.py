"""
Main entry point for the RAG MCQ Assessment System.

Ties together: ingest → generate paper → record answers → recommend topics.
"""

import json
import sys

import config


def cmd_ingest():
    """Ingest study material into the vector store."""
    from rag.ingest import ingest_all
    ingest_all()


def cmd_generate():
    """Generate an MCQ paper from the syllabus."""
    from rag.retriever import retrieve_for_syllabus
    from rag.generator import generate_paper

    with open(config.SYLLABUS_FILE, "r", encoding="utf-8") as f:
        syllabus = json.load(f)

    print("🔍 Retrieving relevant content...")
    content = retrieve_for_syllabus(syllabus)

    print("📝 Generating MCQ paper...")
    paper = generate_paper(syllabus, content)

    output_path = config.PROJECT_ROOT / "generated_paper.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(paper, f, indent=2, ensure_ascii=False)

    print(f"✅ Paper saved to {output_path} ({len(paper)} questions)")


def cmd_record(student_id: str):
    """Record student answers and track mistakes."""
    from guidance.tracker import MistakeTracker

    paper_path = config.PROJECT_ROOT / "generated_paper.json"
    if not paper_path.exists():
        print("❌ No generated paper found. Run 'generate' first.")
        return

    with open(paper_path, "r", encoding="utf-8") as f:
        paper = json.load(f)

    # TODO: Replace with actual answer collection (CLI prompts, web form, etc.)
    print(f"📋 Paper has {len(paper)} questions.")
    print("   (Answer recording is a placeholder — implement your UI here.)")

    # Placeholder: simulate all correct for demo
    answers = {i: q.get("answer", "A") for i, q in enumerate(paper)}

    tracker = MistakeTracker(student_id)
    result = tracker.record_attempt(paper, answers)
    print(f"✅ Recorded: {result['correct']}/{result['total_questions']} correct "
          f"({result['score_percent']}%)")


def cmd_recommend(student_id: str):
    """Show study guidance based on past mistakes."""
    from guidance.tracker import MistakeTracker
    from guidance.analyzer import WeaknessAnalyzer
    from guidance.recommender import TopicRecommender

    tracker = MistakeTracker(student_id)
    analyzer = WeaknessAnalyzer(tracker)
    recommender = TopicRecommender(analyzer)
    recommender.print_guidance()


def main():
    """CLI dispatcher."""
    usage = """
RAG MCQ Assessment System
========================
Usage:
    python main.py ingest                 — Ingest study material
    python main.py generate               — Generate an MCQ paper
    python main.py record <student_id>    — Record student answers
    python main.py recommend <student_id> — Show study guidance
    """

    if len(sys.argv) < 2:
        print(usage)
        return

    command = sys.argv[1].lower()

    if command == "ingest":
        cmd_ingest()
    elif command == "generate":
        cmd_generate()
    elif command == "record":
        student_id = sys.argv[2] if len(sys.argv) > 2 else "default_student"
        cmd_record(student_id)
    elif command == "recommend":
        student_id = sys.argv[2] if len(sys.argv) > 2 else "default_student"
        cmd_recommend(student_id)
    else:
        print(f"❌ Unknown command: {command}")
        print(usage)


if __name__ == "__main__":
    main()
