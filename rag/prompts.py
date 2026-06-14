"""
Prompt templates for MCQ generation at various Bloom's Taxonomy levels.
"""


# ── Base system prompt ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert MCQ question paper setter. You create
high-quality multiple-choice questions based on provided study material.

Rules:
- Each question must have exactly 4 options (A, B, C, D).
- Exactly one option must be correct.
- Distractors should be plausible but clearly incorrect.
- Include a brief explanation for the correct answer.
- Match the difficulty to the Bloom's Taxonomy level specified.
- Prioritise the provided study material as the primary source.
- Use the study material only for concepts/facts, not for copying question text.
- Do not copy any question sentence verbatim from the context.
- Create new wording and, when possible, fresh values/scenarios.
- If context is sparse, use syllabus/topic intent to generate curriculum-aligned mathematics questions.
- Text-only MCQs: every question must be fully answerable from words and numbers in the stem
  and options. Do NOT refer to figures, diagrams, images, graphs "shown", "below", "above",
  or "as illustrated". Do NOT use phrases like "as shown in the figure", "see the diagram",
  "refer to the graph", "in the picture", or "according to the figure". If a task would
  need a drawing (geometry, plots, circuits), describe the setup in plain text instead
  (e.g. all side lengths, coordinates, labels, orientations) so no visual is required.
- Do not output image_prompt, image_url, or any image-related key. Omit them entirely.
- Within the same batch, each question stem must be distinct — do not repeat or trivially
  reword the same problem.
- Return only JSON (no markdown, no explanation outside JSON).
- Output valid JSON: a list of objects with keys:
  question, options (list), answer (letter), explanation only.
"""


# ── Level-specific instructions ──────────────────────────────────────────────
LEVEL_INSTRUCTIONS = {
    "Remember": (
        "Generate questions that test recall of facts, definitions, and basic "
        "concepts. Use stems like 'What is...', 'Which of the following...'"
    ),
    "Understand": (
        "Generate questions that test comprehension — explaining concepts, "
        "paraphrasing, or classifying. Use stems like 'Explain...', 'Which "
        "statement best describes...'"
    ),
    "Apply": (
        "Generate questions that require applying knowledge to new situations "
        "or solving problems. Use stems like 'Given that...', 'Calculate...', "
        "'How would you use...'"
    ),
    "Analyse": (
        "Generate questions that require breaking down information, comparing, "
        "or finding relationships. Use stems like 'Compare...', 'What is the "
        "relationship between...'"
    ),
    "Evaluate": (
        "Generate questions that require making judgements, critiquing, or "
        "justifying decisions. Use stems like 'Which approach is most "
        "appropriate...', 'Evaluate whether...'"
    ),
    "Create": (
        "Generate questions that test ability to synthesise or design — e.g. "
        "'Which design would best...', 'Propose a solution for...'"
    ),
}


def get_mcq_prompt(difficulty_level: str) -> str:
    """
    Build the full prompt for MCQ generation at a given difficulty level.

    Args:
        difficulty_level: One of the Bloom's level names.

    Returns:
        A formatted prompt string with placeholders: {topic}, {context},
        {num_questions}.
    """
    level_instruction = LEVEL_INSTRUCTIONS.get(
        difficulty_level,
        LEVEL_INSTRUCTIONS["Remember"],
    )

    return f"""{SYSTEM_PROMPT}

Difficulty Level: {difficulty_level}
{level_instruction}

Topic: {{topic}}
Number of questions: {{num_questions}}

Study Material (use this as context):
{{context}}

Generate the MCQ questions now as a JSON array.
"""
