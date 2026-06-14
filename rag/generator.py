"""MCQ Generator module."""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from langchain_google_genai import ChatGoogleGenerativeAI

import config
from rag.hf_inference import hf_text_generation
from rag.prompts import get_mcq_prompt


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", (value or "").lower())).strip()


# Stems that depend on an external figure/image — drop; quiz is text-only.
_FIGURE_PHRASES = (
    "as shown in the figure",
    "as shown in figure",
    "as shown in the diagram",
    "as shown in diagram",
    "as shown in the graph",
    "as shown in the picture",
    "as shown in the image",
    "shown in the figure",
    "shown in the diagram",
    "shown in the graph",
    "see the figure",
    "see the diagram",
    "see the graph",
    "see the picture",
    "see the image",
    "see figure",
    "see diagram",
    "in the figure below",
    "in the diagram below",
    "in the graph below",
    "in the picture below",
    "according to the figure",
    "according to the diagram",
    "according to the graph",
    "refer to the figure",
    "refer to the diagram",
    "refer to the graph",
    "refer to the picture",
    "from the figure",
    "from the diagram",
    "the figure shows",
    "the diagram shows",
    "the graph shows",
    "the image shows",
    "the picture shows",
    "below shows a",
    "above shows a",
    "in the accompanying figure",
    "in the accompanying diagram",
    "use the figure",
    "use the diagram",
)


def _question_requires_external_visual(stem: str) -> bool:
    t = (stem or "").lower()
    return any(p in t for p in _FIGURE_PHRASES)


def _dedupe_questions_preserve_order(questions: List[Dict]) -> List[Dict]:
    seen: set[str] = set()
    out: List[Dict] = []
    for q in questions:
        stem = str(q.get("question", "")).strip()
        key = _normalize_text(stem)
        if key:
            if key in seen:
                continue
            seen.add(key)
        out.append(q)
    return out


def _is_probably_copied_question(question: str, context_chunks: List[str]) -> bool:
    """
    Heuristic guardrail:
    if a sufficiently long normalized question appears as a contiguous substring
    in any context chunk, treat it as copied and reject it.
    """
    norm_q = _normalize_text(question)
    if len(norm_q) < 40:
        return False
    for chunk in context_chunks or []:
        if norm_q in _normalize_text(chunk):
            return True
    return False


_google_llm = None
_google_llm_lock = threading.Lock()


def _invoke_google(prompt: str) -> str:
    global _google_llm
    if _google_llm is None:
        with _google_llm_lock:
            if _google_llm is None:
                _google_llm = ChatGoogleGenerativeAI(
                    model=config.GEMINI_MODEL,
                    google_api_key=config.GOOGLE_API_KEY,
                    temperature=0.4,
                )
    response = _google_llm.invoke(prompt)
    return str(response.content)


def _invoke_huggingface(prompt: str) -> str:
    candidates = [config.HF_LLM_MODEL, *config.HF_LLM_FALLBACKS]
    candidates = list(dict.fromkeys(m for m in candidates if m))
    errors: list[str] = []
    for model_id in candidates:
        try:
            print(f"LLM attempt: {model_id}")
            return hf_text_generation(
                model_id=model_id,
                prompt=prompt,
                max_new_tokens=1400,
                temperature=0.3,
            )
        except Exception as exc:
            print(f"LLM failed: {model_id} -> {exc}")
            errors.append(f"{model_id}: {exc}")
            continue
    raise RuntimeError("All HF LLM models failed. " + " | ".join(errors))


def _invoke_llm(prompt: str) -> str:
    provider = config.LLM_PROVIDER
    if provider == "huggingface":
        return _invoke_huggingface(prompt)
    if provider == "google":
        return _invoke_google(prompt)
    if config.HF_API_KEY:
        return _invoke_huggingface(prompt)
    return _invoke_google(prompt)


def _extract_first_json_array(text: str) -> str | None:
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_mcq_response(raw_text: str) -> List[Dict]:
    """Parse the LLM output into structured MCQ dicts."""
    text = (raw_text or "").strip()
    if not text:
        return []

    # Remove markdown fences when present.
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()

    candidates: list[str] = [text]
    array_candidate = _extract_first_json_array(text)
    if array_candidate:
        candidates.append(array_candidate)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue

        if isinstance(parsed, dict) and isinstance(parsed.get("questions"), list):
            parsed = parsed["questions"]
        if not isinstance(parsed, list):
            continue

        out: list[Dict] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            options = item.get("options", [])
            if not isinstance(options, list):
                options = []
            options = [str(o).strip() for o in options][:4]
            raw_answer = str(item.get("answer", "")).strip()
            answer = raw_answer.upper()[:1]
            if answer not in {"A", "B", "C", "D"}:
                # Accept answer text and map it to option letter.
                mapped = ""
                for idx, opt in enumerate(options):
                    if raw_answer.strip().lower() == opt.strip().lower():
                        mapped = "ABCD"[idx]
                        break
                if not mapped:
                    m = raw_answer.lower()
                    if "option a" in m:
                        mapped = "A"
                    elif "option b" in m:
                        mapped = "B"
                    elif "option c" in m:
                        mapped = "C"
                    elif "option d" in m:
                        mapped = "D"
                answer = mapped
            if answer not in {"A", "B", "C", "D"}:
                continue
            question = str(item.get("question", "")).strip()
            if not question or len(options) != 4:
                continue
            row: Dict = {
                "question": question,
                "options": options,
                "answer": answer,
                "explanation": str(item.get("explanation", "")).strip(),
            }
            out.append(row)
        if out:
            return out

    print(f"Warning: Could not parse MCQ JSON. Raw preview: {text[:350]}")
    return []


_MCQ_CONTEXT_CHAR_CAP = 10_000


def generate_mcqs(
    topic_name: str,
    difficulty_level: str,
    context_chunks: List[str],
    num_questions: int = 5,
) -> List[Dict]:
    """Generate MCQ questions for a topic at a given difficulty level."""
    prompt = get_mcq_prompt(difficulty_level)
    context = "\n\n".join(context_chunks) if context_chunks else "No specific context provided."
    if len(context) > _MCQ_CONTEXT_CHAR_CAP:
        context = context[:_MCQ_CONTEXT_CHAR_CAP] + "\n\n[Context truncated for speed.]"

    formatted_prompt = prompt.format(
        topic=topic_name,
        context=context,
        num_questions=num_questions,
    )

    raw_text = _invoke_llm(formatted_prompt)
    questions = parse_mcq_response(raw_text)

    if not questions:
        # Retry with a strict repair prompt while preserving generated content.
        repair_prompt = (
            "Convert the following content into STRICT JSON only. "
            "Output exactly a JSON array of {n} items. "
            "Each item must contain keys: question, options, answer, explanation only. "
            "options must contain exactly 4 strings, and answer must be A/B/C/D. "
            "Text-only stems: remove any reference to figures, diagrams, or images; "
            "embed all given data in words and numbers. No image_prompt key.\n\n"
            "CONTENT:\n"
            "{content}"
        ).format(n=num_questions, content=raw_text[:8000])
        repaired = _invoke_llm(repair_prompt)
        questions = parse_mcq_response(repaired)
        for q in questions:
            q.pop("image_prompt", None)
            q.pop("image_url", None)

    filtered_questions: List[Dict] = []
    for q in questions:
        question_text = str(q.get("question", ""))
        if _question_requires_external_visual(question_text):
            continue
        if _is_probably_copied_question(question_text, context_chunks):
            continue
        q.pop("image_prompt", None)
        q.pop("image_url", None)
        filtered_questions.append(q)
    questions = _dedupe_questions_preserve_order(filtered_questions)

    if len(questions) < num_questions:
        shortfall = num_questions - len(questions)
        existing = "\n".join(
            f"- {str(q.get('question', ''))[:220]}" for q in questions[:12]
        )
        top_up_prompt = (
            "Generate exactly {n} NEW MCQs for topic '{topic}' at '{level}' level. "
            "Use the context only for concepts/facts. "
            "Do NOT copy or closely paraphrase any existing question from context. "
            "Text-only: no figures, diagrams, graphs, or images; never say 'as shown in the figure' "
            "or similar; put all data in the stem. Each stem must differ from these existing ones:\n"
            "{existing}\n\n"
            "Return STRICT JSON array only with keys: question, options, answer, explanation.\n\n"
            "CONTEXT:\n{context}"
        ).format(
            n=shortfall,
            topic=topic_name,
            level=difficulty_level,
            existing=existing or "(none yet)",
            context=context[:8000],
        )
        top_up_raw = _invoke_llm(top_up_prompt)
        top_up_questions = parse_mcq_response(top_up_raw)
        for q in top_up_questions:
            qt = str(q.get("question", ""))
            if _question_requires_external_visual(qt):
                continue
            if _is_probably_copied_question(qt, context_chunks):
                continue
            q.pop("image_prompt", None)
            q.pop("image_url", None)
            questions.append(q)
            if len(questions) >= num_questions:
                break
        questions = _dedupe_questions_preserve_order(questions)

    for q in questions:
        q["topic_id"] = topic_name
        q["difficulty_level"] = difficulty_level
        q.pop("image_prompt", None)
        q.pop("image_url", None)

    return questions[:num_questions]


def _generate_mcqs_block(args: tuple) -> List[Dict]:
    topic_id, topic_name, level_name, chunks, questions_per_level = args
    try:
        questions = generate_mcqs(
            topic_name=topic_name,
            difficulty_level=level_name,
            context_chunks=chunks,
            num_questions=questions_per_level,
        )
        for q in questions:
            q["topic_id"] = topic_id
        return questions
    except Exception as exc:
        print(f"Error generating for {topic_name}/{level_name}: {exc}")
        return []


def generate_paper(syllabus: dict, retrieved_content: dict) -> List[Dict]:
    """Generate a full MCQ paper based on the syllabus configuration."""
    topics_lookup = {}
    levels_lookup = {}
    try:
        with open(config.TOPICS_FILE, "r", encoding="utf-8") as f:
            topics = json.load(f)
            topics_lookup = {t["id"]: t["name"] for t in topics}
    except Exception:
        pass
    try:
        with open(config.DIFFICULTY_LEVELS_FILE, "r", encoding="utf-8") as f:
            levels = json.load(f)
            levels_lookup = {l["level_id"]: l["name"] for l in levels}
    except Exception:
        pass

    tasks: list[tuple] = []
    for mapping in syllabus.get("topic_mappings", []):
        topic_id = mapping.get("topic_id", "")
        topic_name = (
            mapping.get("topic_name")
            or topics_lookup.get(topic_id, topic_id)
            or "General Mathematics"
        )
        chunks = retrieved_content.get(topic_id, []) or []
        num_q = int(mapping.get("min_questions", 5))

        required_levels = mapping.get("required_levels", [1])
        questions_per_level = max(1, num_q // max(1, len(required_levels)))

        for level_id in required_levels:
            level_name = levels_lookup.get(level_id, "Remember")
            print(
                f"Queued {questions_per_level} '{level_name}' questions for '{topic_name}'..."
            )
            tasks.append(
                (topic_id, topic_name, level_name, list(chunks), questions_per_level)
            )

    paper: List[Dict] = []
    if tasks:
        max_workers = min(len(tasks), max(1, config.GENERATION_MAX_WORKERS))
        print(f"MCQ generation: {len(tasks)} LLM task(s), max_workers={max_workers}")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for block in executor.map(_generate_mcqs_block, tasks):
                paper.extend(block)

    if not paper:
        # Final fallback: generate directly from combined retrieved context.
        combined_context: list[str] = []
        for chunks in retrieved_content.values():
            if isinstance(chunks, list):
                combined_context.extend(chunks)
        if combined_context:
            print("No mapped questions generated; using combined-context fallback generation.")
            fallback_questions = generate_mcqs(
                topic_name="Mathematics",
                difficulty_level="Apply",
                context_chunks=combined_context[:12],
                num_questions=max(8, int(syllabus.get("total_questions", 10) // 4)),
            )
            for q in fallback_questions:
                q["topic_id"] = q.get("topic_id") or "math_fallback"
            paper.extend(fallback_questions)

    raw_count = len(paper)
    cleaned: List[Dict] = []
    for q in paper:
        q.pop("image_prompt", None)
        q.pop("image_url", None)
        stem = str(q.get("question", ""))
        if _question_requires_external_visual(stem):
            continue
        cleaned.append(q)
    paper = _dedupe_questions_preserve_order(cleaned)
    dropped = raw_count - len(paper)
    if dropped:
        print(f"Paper cleanup: removed {dropped} duplicate or figure-dependent question(s).")
    return paper
