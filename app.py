"""Flask web application for the RAG MCQ Assessment system."""

import json
import os
import re
import traceback
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

import config
from rag.image_gen import is_safe_quiz_image_url


app = Flask(__name__)

_is_production = os.getenv("RENDER", "") or os.getenv("FLASK_ENV") == "production"

_secret = os.getenv("FLASK_SECRET_KEY", "")
if _is_production and (not _secret or _secret == "dev-secret-change-me"):
    raise RuntimeError("FLASK_SECRET_KEY must be set to a strong random value in production")
app.secret_key = _secret or "dev-secret-change-me"

if _is_production:
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _normalized_database_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if raw.startswith("postgres://"):
        return "postgresql://" + raw[len("postgres://") :]
    if raw:
        return raw
    return f"sqlite:///{(config.PROJECT_ROOT / 'app.db').as_posix()}"


app.config["SQLALCHEMY_DATABASE_URI"] = _normalized_database_url()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    joined_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class UserSettings(db.Model):
    __tablename__ = "user_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    theme = db.Column(db.String(16), nullable=False, default="dark")
    difficulty = db.Column(db.String(16), nullable=False, default="medium")
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Attempt(db.Model):
    __tablename__ = "attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    total_questions = db.Column(db.Integer, nullable=False)
    correct = db.Column(db.Integer, nullable=False)
    incorrect = db.Column(db.Integer, nullable=False)
    score_percent = db.Column(db.Float, nullable=False)


class Mistake(db.Model):
    __tablename__ = "mistakes"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=False, index=True)
    question_index = db.Column(db.Integer, nullable=False)
    question = db.Column(db.Text, nullable=False, default="")
    topic_id = db.Column(db.String(255), nullable=False, default="unknown")
    difficulty_level = db.Column(db.String(64), nullable=False, default="unknown")
    correct_answer = db.Column(db.String(16), nullable=False, default="")
    student_answer = db.Column(db.String(16), nullable=True)


class AttemptQuestion(db.Model):
    __tablename__ = "attempt_questions"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=False, index=True)
    question_index = db.Column(db.Integer, nullable=False)
    question = db.Column(db.Text, nullable=False, default="")
    options_json = db.Column(db.Text, nullable=False, default="[]")
    correct_answer = db.Column(db.String(16), nullable=False, default="")
    student_answer = db.Column(db.String(16), nullable=True)
    correct_option_text = db.Column(db.Text, nullable=True)
    student_option_text = db.Column(db.Text, nullable=True)
    explanation = db.Column(db.Text, nullable=False, default="")
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    image_url = db.Column(db.String(512), nullable=True)


def _ensure_attempt_question_image_url_column() -> None:
    try:
        from sqlalchemy import inspect, text

        insp = inspect(db.engine)
        if "attempt_questions" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("attempt_questions")}
        if "image_url" in cols:
            return
        dialect = db.engine.dialect.name
        with db.engine.begin() as conn:
            if dialect == "postgresql":
                conn.execute(
                    text(
                        "ALTER TABLE attempt_questions ADD COLUMN IF NOT EXISTS image_url VARCHAR(512)"
                    )
                )
            else:
                conn.execute(text("ALTER TABLE attempt_questions ADD COLUMN image_url VARCHAR(512)"))
    except Exception as exc:
        print(f"Note: could not add attempt_questions.image_url: {exc}")


with app.app_context():
    db.create_all()
    _ensure_attempt_question_image_url_column()



PAPER_FILE = config.PROJECT_ROOT / "generated_paper.json"


def _load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _save_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def _option_text(options: list, answer_letter: str | None) -> str | None:
    if not isinstance(options, list):
        return None
    letter = str(answer_letter or "").strip().upper()[:1]
    index_map = {"A": 0, "B": 1, "C": 2, "D": 3}
    idx = index_map.get(letter)
    if idx is None or idx >= len(options):
        return None
    return str(options[idx])


def _has_llm_config() -> bool:
    provider = (getattr(config, "LLM_PROVIDER", "") or "").strip().lower()
    if provider == "huggingface":
        return bool(getattr(config, "HF_API_KEY", "").strip())
    if provider == "google":
        return bool(config.GOOGLE_API_KEY.strip())
    return bool(config.GOOGLE_API_KEY.strip() or getattr(config, "HF_API_KEY", "").strip())


def _infer_syllabus_from_reference_docs() -> dict:
    supported_ext = {".pdf", ".md", ".txt"}
    files = []
    for configured_dir in config.QUIZ_REFERENCE_DIRS:
        base_dir = config.resolve_reference_dir(configured_dir)
        if not base_dir.exists():
            continue
        for p in base_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in supported_ext:
                continue
            if not config.include_quiz_reference_file(p):
                continue
            files.append(p)
    files = sorted(files, key=lambda p: p.name.lower())

    mappings = []
    if files:
        for idx, path in enumerate(files[:8], start=1):
            label = path.stem.replace("_", " ").replace("-", " ").strip()
            topic_id = f"doc_{idx:03d}"
            mappings.append(
                {
                    "topic_id": topic_id,
                    "topic_name": label or topic_id,
                    "required_levels": [1, 2, 3],
                    "weightage_percent": 0,
                    "min_questions": 6,
                    "max_questions": 10,
                    "notes": f"Auto-inferred from {path.name}",
                }
            )
    else:
        mappings.append(
            {
                "topic_id": "doc_001",
                "topic_name": "Mathematics",
                "required_levels": [1, 2, 3],
                "weightage_percent": 100,
                "min_questions": 12,
                "max_questions": 20,
                "notes": "Auto-generated fallback when no reference files are found.",
            }
        )

    n = len(mappings)
    for i, m in enumerate(mappings):
        m["weightage_percent"] = 100 // n + (1 if i < (100 % n) else 0)

    total_questions = min(40, max(12, n * 6))
    return {
        "syllabus_name": "Auto-Inferred from reference docs (G10/G11)",
        "total_questions": total_questions,
        "time_limit_minutes": max(30, total_questions * 2),
        "topic_mappings": mappings,
    }


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return s or "topic"


def _infer_syllabus_from_topics_file() -> dict | None:
    try:
        raw = _load_json(config.TOPICS_FILE)
    except Exception:
        return None

    flat_topics: list[dict] = []
    if isinstance(raw, list):
        # Legacy format: [{"id": "...", "name": "..."}]
        for i, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            topic_id = str(item.get("id", "")).strip() or f"topic_{i:03d}"
            flat_topics.append({"topic_id": topic_id, "topic_name": name, "periods": 4})
    elif isinstance(raw, dict):
        # Grade/term format.
        for grade in raw.get("grades", []):
            grade_no = grade.get("grade")
            for term in grade.get("terms", []):
                term_no = term.get("term")
                for idx, topic in enumerate(term.get("topics", []), start=1):
                    if not isinstance(topic, dict):
                        continue
                    name = str(topic.get("topic", "")).strip()
                    if not name:
                        continue
                    no = topic.get("no", idx)
                    topic_id = f"g{grade_no}_t{term_no}_{int(no):02d}_{_slug(name)}"
                    try:
                        periods = int(topic.get("periods", 4))
                    except Exception:
                        periods = 4
                    flat_topics.append(
                        {
                            "topic_id": topic_id,
                            "topic_name": name,
                            "periods": max(1, periods),
                        }
                    )

    if not flat_topics:
        return None

    # Keep quiz size practical; choose top weighted topics.
    flat_topics.sort(key=lambda t: t.get("periods", 1), reverse=True)
    chosen = flat_topics[:8]
    total_periods = max(1, sum(t.get("periods", 1) for t in chosen))

    mappings = []
    for t in chosen:
        periods = t.get("periods", 1)
        weight = max(1, round(periods * 100 / total_periods))
        min_q = 2 if periods <= 4 else 3
        mappings.append(
            {
                "topic_id": t["topic_id"],
                "topic_name": t["topic_name"],
                "required_levels": [1, 2, 3],
                "weightage_percent": weight,
                "min_questions": min_q,
                "max_questions": max(min_q + 1, min_q + 3),
                "notes": "Auto-inferred from topics.json",
            }
        )

    # Normalize weightage to exactly 100.
    diff = 100 - sum(m["weightage_percent"] for m in mappings)
    if mappings:
        mappings[0]["weightage_percent"] += diff

    total_questions = max(12, min(40, sum(m["min_questions"] for m in mappings)))
    return {
        "syllabus_name": "Auto-Inferred from topics.json",
        "total_questions": total_questions,
        "time_limit_minutes": max(30, total_questions * 2),
        "topic_mappings": mappings,
    }


def _load_or_infer_syllabus() -> dict:
    if config.SYLLABUS_FILE.exists():
        try:
            data = _load_json(config.SYLLABUS_FILE)
            if isinstance(data, dict) and data.get("topic_mappings"):
                return data
        except Exception:
            pass
    topics_based = _infer_syllabus_from_topics_file()
    if topics_based:
        return topics_based
    return _infer_syllabus_from_reference_docs()


def _topic_name_lookup_from_topics_file() -> dict[str, str]:
    """
    Build topic_id -> topic_name from data/topics.json using the same id format
    used by _infer_syllabus_from_topics_file.
    """
    lookup: dict[str, str] = {}
    try:
        raw = _load_json(config.TOPICS_FILE)
    except Exception:
        return lookup

    if not isinstance(raw, dict):
        return lookup

    for grade in raw.get("grades", []):
        grade_no = grade.get("grade")
        for term in grade.get("terms", []):
            term_no = term.get("term")
            for idx, topic in enumerate(term.get("topics", []), start=1):
                if not isinstance(topic, dict):
                    continue
                name = str(topic.get("topic", "")).strip()
                if not name:
                    continue
                no = topic.get("no", idx)
                topic_id = f"g{grade_no}_t{term_no}_{int(no):02d}_{_slug(name)}"
                lookup[topic_id] = name
    return lookup


def _textbook_index_by_grade() -> dict[int, list[str]]:
    """Index textbook filenames by detected grade number (6-11)."""
    books_dir = config.DATA_DIR / "TextBooks"
    if not books_dir.exists():
        return {}

    by_grade: dict[int, list[str]] = {}
    for p in books_dir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        name = p.name
        m = re.search(r"\bg[\s\-_]?(\d{1,2})\b", name, flags=re.IGNORECASE)
        if not m:
            continue
        try:
            grade = int(m.group(1))
        except Exception:
            continue
        if grade < 6 or grade > 11:
            continue
        by_grade.setdefault(grade, []).append(name)

    for grade in by_grade:
        by_grade[grade] = sorted(by_grade[grade], key=lambda x: x.lower())
    return by_grade


def _find_topic_id_for_grade_topic(grade: int, topic_name: str) -> str:
    """Resolve to existing topics.json id where possible, otherwise fallback."""
    target = re.sub(r"[^a-z0-9]+", " ", str(topic_name or "").lower()).strip()
    if not target:
        return f"practice_g{grade}_topic"

    try:
        raw = _load_json(config.TOPICS_FILE)
    except Exception:
        raw = {}

    if isinstance(raw, dict):
        for g in raw.get("grades", []):
            if int(g.get("grade", -1)) != int(grade):
                continue
            for term in g.get("terms", []):
                term_no = term.get("term")
                for idx, topic in enumerate(term.get("topics", []), start=1):
                    if not isinstance(topic, dict):
                        continue
                    name = str(topic.get("topic", "")).strip()
                    norm = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
                    if not norm:
                        continue
                    if target == norm or target in norm or norm in target:
                        no = topic.get("no", idx)
                        return f"g{grade}_t{term_no}_{int(no):02d}_{_slug(name)}"

    return f"practice_g{grade}_{_slug(topic_name)}"


def _build_practice_catalog() -> list[dict]:
    """
    Build practice tabs from concept_mapping.json:
    - one tab per main_topic
    - subtopics grouped by grade (11 -> 6)
    """
    try:
        concept_mapping = _load_json(config.DATA_DIR / "concept_mapping.json")
    except Exception:
        concept_mapping = []

    textbooks = _textbook_index_by_grade()
    tabs: list[dict] = []
    if not isinstance(concept_mapping, list):
        return tabs

    for main in concept_mapping:
        main_topic = str(main.get("main_topic", "")).strip()
        if not main_topic:
            continue

        grade_groups: dict[int, dict] = {}
        for strand in main.get("strands", []):
            strand_name = str(strand.get("strand_name", "")).strip()
            for step in strand.get("progression", []):
                grade = step.get("grade")
                topic = str(step.get("topic", "")).strip()
                if not isinstance(grade, int) or not topic:
                    continue
                if grade < 6 or grade > 11:
                    continue

                grp = grade_groups.setdefault(
                    grade,
                    {"grade": grade, "subtopics": []},
                )
                if any(s["topic"] == topic for s in grp["subtopics"]):
                    continue

                grp["subtopics"].append(
                    {
                        "topic": topic,
                        "strand_name": strand_name,
                        "textbooks": textbooks.get(grade, []),
                    }
                )

        grades_payload = [
            {
                "grade": grade,
                "subtopics": sorted(items["subtopics"], key=lambda s: s["topic"].lower()),
            }
            for grade, items in sorted(grade_groups.items(), key=lambda x: x[0], reverse=True)
        ]
        tabs.append(
            {
                "tab_id": _slug(main_topic),
                "label": main_topic,
                "grades": grades_payload,
            }
        )

    return tabs


def _generate_and_save_paper() -> dict:
    syllabus = _load_or_infer_syllabus()
    if not _has_llm_config():
        from rag.demo import generate_demo_paper

        paper = generate_demo_paper(syllabus)
        _save_json(PAPER_FILE, paper)
        return {
            "status": "ok",
            "paper": paper,
            "count": len(paper),
            "mode": "demo",
            "message": "Generated demo paper. Configure GOOGLE_API_KEY for full RAG generation.",
        }

    try:
        from rag.generator import generate_paper
        from rag.retriever import retrieve_for_syllabus

        content = retrieve_for_syllabus(syllabus)
        paper = generate_paper(syllabus, content)
        _save_json(PAPER_FILE, paper)
        return {"status": "ok", "paper": paper, "count": len(paper), "mode": "rag"}
    except Exception as exc:
        traceback.print_exc()
        from rag.demo import generate_demo_paper

        paper = generate_demo_paper(syllabus)
        _save_json(PAPER_FILE, paper)
        return {
            "status": "ok",
            "paper": paper,
            "count": len(paper),
            "mode": "demo",
            "message": f"RAG failed, using demo: {exc}",
        }


def _require_signed_in_email() -> str | None:
    email = session.get("email")
    return email if isinstance(email, str) and email.strip() else None


def _current_user() -> User | None:
    email = _require_signed_in_email()
    if not email:
        return None
    return User.query.filter_by(email=email).first()


class DBTrackerAdapter:
    """Adapter so existing guidance analyzer/recommender can use DB data."""

    def __init__(self, user: User, attempt_id: int | None = None):
        self.user = user
        self.attempt_id = attempt_id
        attempts = Attempt.query.filter_by(user_id=user.id).order_by(Attempt.created_at.asc()).all()
        self.data = {
            "attempts": [
                {
                    "score_percent": a.score_percent,
                    "mistakes": [],
                }
                for a in attempts
            ]
        }

    def get_all_mistakes(self) -> list[dict]:
        query = (
            Mistake.query.join(Attempt, Mistake.attempt_id == Attempt.id)
            .filter(Attempt.user_id == self.user.id)
        )
        if self.attempt_id is not None:
            query = query.filter(Mistake.attempt_id == self.attempt_id)
        rows = query.all()
        return [
            {
                "question_index": m.question_index,
                "question": m.question,
                "topic_id": m.topic_id,
                "difficulty_level": m.difficulty_level,
                "correct_answer": m.correct_answer,
                "student_answer": m.student_answer,
            }
            for m in rows
        ]


class ConceptMapper:
    """Maps a grade-11 weak topic to lower-grade chapters using concept_mapping.json."""

    def __init__(self):
        self.concept_mapping = self._safe_load(config.DATA_DIR / "concept_mapping.json", default=[])
        self.topics_data = self._safe_load(config.TOPICS_FILE, default={})
        self.grade11_competencies = self._build_grade11_competency_index()

    @staticmethod
    def _safe_load(path: Path, default: Any) -> Any:
        try:
            return _load_json(path)
        except Exception:
            return default

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()

    @staticmethod
    def _normalize_comp(comp: str) -> str:
        # "6.4 (a part)" -> "6.4"
        m = re.search(r"\d+\.\d+", str(comp or ""))
        return m.group(0) if m else str(comp or "").strip()

    def _build_grade11_competency_index(self) -> dict[str, set[str]]:
        index: dict[str, set[str]] = {}
        grades = self.topics_data.get("grades", []) if isinstance(self.topics_data, dict) else []
        for grade in grades:
            if grade.get("grade") != 11:
                continue
            for term in grade.get("terms", []):
                for topic in term.get("topics", []):
                    name = self._norm(topic.get("topic", ""))
                    if not name:
                        continue
                    levels = {
                        self._normalize_comp(level)
                        for level in topic.get("competency_levels", [])
                        if str(level or "").strip()
                    }
                    if levels:
                        index.setdefault(name, set()).update(levels)
        return index

    def map_topic(self, topic_name: str) -> list[dict]:
        topic_norm = self._norm(topic_name)
        if not topic_norm or not isinstance(self.concept_mapping, list):
            return []

        source_comp = self.grade11_competencies.get(topic_norm, set())
        candidates = []

        for main in self.concept_mapping:
            for strand in main.get("strands", []):
                progression = strand.get("progression", [])
                grade11_rows = [row for row in progression if row.get("grade") == 11]
                if not grade11_rows:
                    continue

                g11_topics = {self._norm(row.get("topic", "")) for row in grade11_rows}
                g11_comp = {
                    self._normalize_comp(level)
                    for row in grade11_rows
                    for level in row.get("competency_levels", [])
                    if str(level or "").strip()
                }
                matches_topic = topic_norm in g11_topics
                overlap = source_comp.intersection(g11_comp) if (source_comp and g11_comp) else set()
                overlap_count = len(overlap)
                matches_comp = overlap_count > 0
                if not (matches_topic or matches_comp):
                    continue

                focus_chapters = sorted(
                    [
                        {
                            "grade": int(row.get("grade", 0)),
                            "topic": row.get("topic", ""),
                            "competency_levels": row.get("competency_levels", []),
                        }
                        for row in progression
                        if isinstance(row.get("grade"), int) and row.get("grade") <= 11
                    ],
                    key=lambda row: row["grade"],
                    reverse=True,
                )

                candidates.append(
                    {
                        "main_topic": main.get("main_topic", ""),
                        "strand_name": strand.get("strand_name", ""),
                        "match_type": "topic" if matches_topic else "competency",
                        "score": 100 + overlap_count if matches_topic else overlap_count,
                        "focus_chapters": focus_chapters,
                    }
                )

        if not candidates:
            return []

        # If we have exact topic matches, suppress noisy competency-only fallbacks.
        has_topic_match = any(c["match_type"] == "topic" for c in candidates)
        if has_topic_match:
            candidates = [c for c in candidates if c["match_type"] == "topic"]

        # Rank and dedupe (same strand/path can appear as repeated guidance noise).
        candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
        deduped = []
        seen = set()
        for c in candidates:
            chapter_path = tuple(
                (row.get("grade"), row.get("topic", ""))
                for row in c.get("focus_chapters", [])
            )
            key = (c.get("main_topic", ""), c.get("strand_name", ""), chapter_path)
            if key in seen:
                continue
            seen.add(key)
            c.pop("score", None)
            deduped.append(c)
            if len(deduped) >= 3:
                break

        return deduped


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/signup", methods=["POST"])
def api_signup():
    body = request.get_json(force=True)
    name = str(body.get("name", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    if not name:
        return jsonify({"status": "error", "message": "Name is required."}), 400
    if not email or "@" not in email:
        return jsonify({"status": "error", "message": "A valid email is required."}), 400
    if len(password) < 6:
        return jsonify({"status": "error", "message": "Password must be at least 6 characters."}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"status": "error", "message": "Email already registered. Please sign in."}), 409

    user = User(email=email, name=name, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.flush()
    db.session.add(UserSettings(user_id=user.id, theme="dark", difficulty="medium"))
    db.session.commit()

    session["email"] = email
    return jsonify({"status": "ok", "email": email, "name": name})


@app.route("/api/signin", methods=["POST"])
def api_signin():
    body = request.get_json(force=True)
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    if not email:
        return jsonify({"status": "error", "message": "Email is required."}), 400
    if not password:
        return jsonify({"status": "error", "message": "Password is required."}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"status": "error", "message": "No account found with that email. Please sign up."}), 404

    if not check_password_hash(user.password_hash, password):
        return jsonify({"status": "error", "message": "Incorrect password."}), 401

    session["email"] = email
    return jsonify({"status": "ok", "email": email, "name": user.name})


@app.route("/api/signout", methods=["POST"])
def api_signout():
    session.pop("email", None)
    return jsonify({"status": "ok"})


@app.route("/api/me")
def api_me():
    user = _current_user()
    if not user:
        return jsonify({"status": "ok", "email": None, "name": None})
    return jsonify(
        {
            "status": "ok",
            "email": user.email,
            "name": user.name,
            "joined": user.joined_at.isoformat() if user.joined_at else None,
        }
    )


@app.route("/api/profile")
def api_profile_get():
    user = _current_user()
    if not user:
        return jsonify({"status": "error", "message": "Not signed in."}), 401
    return jsonify(
        {
            "status": "ok",
            "email": user.email,
            "name": user.name,
            "joined": user.joined_at.isoformat() if user.joined_at else None,
        }
    )


@app.route("/api/profile", methods=["POST"])
def api_profile_update():
    user = _current_user()
    if not user:
        return jsonify({"status": "error", "message": "Not signed in."}), 401

    body = request.get_json(force=True)
    name = str(body.get("name", "")).strip()
    if not name:
        return jsonify({"status": "error", "message": "Name is required."}), 400

    user.name = name
    db.session.commit()
    return jsonify({"status": "ok", "name": name})


@app.route("/api/settings")
def api_settings_get():
    user = _current_user()
    if not user:
        return jsonify({"status": "error", "message": "Not signed in."}), 401

    settings = UserSettings.query.filter_by(user_id=user.id).first()
    if not settings:
        settings = UserSettings(user_id=user.id, theme="dark", difficulty="medium")
        db.session.add(settings)
        db.session.commit()

    return jsonify({"status": "ok", "theme": settings.theme, "difficulty": settings.difficulty})


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    user = _current_user()
    if not user:
        return jsonify({"status": "error", "message": "Not signed in."}), 401

    body = request.get_json(force=True)
    theme = body.get("theme", "dark")
    difficulty = body.get("difficulty", "medium")
    if theme not in {"light", "dark"}:
        theme = "dark"
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "medium"

    settings = UserSettings.query.filter_by(user_id=user.id).first()
    if not settings:
        settings = UserSettings(user_id=user.id)
        db.session.add(settings)

    settings.theme = theme
    settings.difficulty = difficulty
    settings.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"status": "ok", "theme": settings.theme, "difficulty": settings.difficulty})


@app.route("/api/topics")
def api_topics():
    try:
        return jsonify(_load_json(config.TOPICS_FILE))
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/syllabus")
def api_syllabus():
    try:
        return jsonify(_load_or_infer_syllabus())
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    if not _has_llm_config():
        return jsonify(
            {
                "status": "ok",
                "message": "Demo mode. Configure LLM_PROVIDER + corresponding API key in .env for real ingestion.",
            }
        )
    try:
        from rag.ingest import ingest_all

        result = ingest_all()
        return jsonify({"status": "ok", "message": result.get("message", "Ingestion complete.")})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"status": "ok", "message": f"Falling back to demo mode: {exc}"})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    try:
        return jsonify(_generate_and_save_paper())
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/quiz/load", methods=["POST"])
def api_quiz_load():
    """
    Ingest (no-op when sources unchanged) + generate paper in one request.
    Faster UX than chaining /api/ingest and /api/generate from the browser.
    """
    ingest_meta: dict
    if _has_llm_config():
        try:
            from rag.ingest import ingest_all

            ingest_meta = ingest_all()
        except Exception as exc:
            traceback.print_exc()
            ingest_meta = {
                "status": "error",
                "message": f"Ingest issue (generation may still run): {exc}",
            }
    else:
        ingest_meta = {
            "status": "ok",
            "message": "Demo mode. Configure LLM_PROVIDER + API key for real ingestion.",
        }
    try:
        payload = _generate_and_save_paper()
        if isinstance(payload, dict):
            payload["ingest"] = ingest_meta
        return jsonify(payload)
    except Exception as exc:
        traceback.print_exc()
        return (
            jsonify(
                {
                    "status": "error",
                    "message": str(exc),
                    "ingest": ingest_meta,
                }
            ),
            500,
        )


@app.route("/api/practice/quiz", methods=["POST"])
def api_practice_quiz():
    try:
        user = _current_user()
        if not user:
            return jsonify({"status": "error", "message": "Please sign in first."}), 401

        body = request.get_json(force=True) or {}
        try:
            grade = int(body.get("grade"))
        except Exception:
            return jsonify({"status": "error", "message": "Valid grade is required."}), 400
        topic_name = str(body.get("topic", "")).strip()
        if not topic_name:
            return jsonify({"status": "error", "message": "Topic is required."}), 400

        topic_id = _find_topic_id_for_grade_topic(grade, topic_name)
        syllabus = {
            "syllabus_name": f"Practice: Grade {grade} - {topic_name}",
            "total_questions": 10,
            "time_limit_minutes": 20,
            "topic_mappings": [
                {
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "grade": grade,
                    "required_levels": [1, 2, 3],
                    "weightage_percent": 100,
                    "min_questions": 10,
                    "max_questions": 10,
                    "notes": f"Topic practice for Grade {grade}",
                }
            ],
        }

        if not _has_llm_config():
            from rag.demo import generate_demo_paper

            paper = generate_demo_paper(syllabus)
            _save_json(PAPER_FILE, paper)
            return jsonify(
                {
                    "status": "ok",
                    "mode": "demo",
                    "paper": paper,
                    "count": len(paper),
                    "message": "Generated practice quiz in demo mode.",
                }
            )

        try:
            from rag.generator import generate_paper
            from rag.retriever import retrieve_for_syllabus

            content = retrieve_for_syllabus(syllabus)
            paper = generate_paper(syllabus, content)
            _save_json(PAPER_FILE, paper)
            return jsonify({"status": "ok", "mode": "rag", "paper": paper, "count": len(paper)})
        except Exception as exc:
            traceback.print_exc()
            from rag.demo import generate_demo_paper

            paper = generate_demo_paper(syllabus)
            _save_json(PAPER_FILE, paper)
            return jsonify(
                {
                    "status": "ok",
                    "mode": "demo",
                    "paper": paper,
                    "count": len(paper),
                    "message": f"Practice RAG generation failed, using demo: {exc}",
                }
            )
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/practice/catalog")
def api_practice_catalog():
    try:
        user = _current_user()
        if not user:
            return jsonify({"status": "error", "message": "Please sign in first."}), 401
        return jsonify({"status": "ok", "tabs": _build_practice_catalog()})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/paper")
def api_paper():
    try:
        if not PAPER_FILE.exists():
            _generate_and_save_paper()
        paper = _load_json(PAPER_FILE)
        return jsonify({"status": "ok", "paper": paper, "count": len(paper)})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/submit", methods=["POST"])
def api_submit():
    try:
        user = _current_user()
        if not user:
            return jsonify({"status": "error", "message": "Please sign in first."}), 401

        body = request.get_json(force=True)
        raw_answers = body.get("answers", {})
        answers = {int(k): v for k, v in raw_answers.items()}

        if not PAPER_FILE.exists():
            _generate_and_save_paper()

        paper = _load_json(PAPER_FILE)

        mistakes_payload = []
        correct = 0

        review = []
        for idx, question in enumerate(paper):
            student_answer = raw_answers.get(str(idx), "")
            correct_answer = question.get("answer", "")
            options = question.get("options", [])
            is_correct = student_answer == correct_answer
            if is_correct:
                correct += 1
            else:
                mistakes_payload.append(
                    {
                        "question_index": idx,
                        "question": question.get("question", ""),
                        "topic_id": question.get("topic_id", "unknown"),
                        "difficulty_level": question.get("difficulty_level", "unknown"),
                        "correct_answer": correct_answer,
                        "student_answer": student_answer,
                    }
                )

            img = question.get("image_url") or ""
            rev = {
                "index": idx,
                "question": question.get("question", ""),
                "options": options,
                "correct_answer": correct_answer,
                "explanation": question.get("explanation", ""),
                "student_answer": student_answer,
                "correct_option_text": _option_text(options, correct_answer),
                "student_option_text": _option_text(options, student_answer),
                "is_correct": is_correct,
            }
            if img and is_safe_quiz_image_url(img):
                rev["image_url"] = img
            review.append(rev)

        total = len(paper)
        incorrect = len(mistakes_payload)
        score_percent = round(correct / max(total, 1) * 100, 1)

        attempt = Attempt(
            user_id=user.id,
            total_questions=total,
            correct=correct,
            incorrect=incorrect,
            score_percent=score_percent,
        )
        db.session.add(attempt)
        db.session.flush()

        for mistake in mistakes_payload:
            db.session.add(
                Mistake(
                    attempt_id=attempt.id,
                    question_index=mistake["question_index"],
                    question=mistake["question"],
                    topic_id=mistake["topic_id"],
                    difficulty_level=mistake["difficulty_level"],
                    correct_answer=mistake["correct_answer"],
                    student_answer=mistake["student_answer"],
                )
            )

        for item in review:
            iu = item.get("image_url") or ""
            db.session.add(
                AttemptQuestion(
                    attempt_id=attempt.id,
                    question_index=item["index"],
                    question=item.get("question", ""),
                    options_json=json.dumps(item.get("options", []), ensure_ascii=False),
                    correct_answer=item.get("correct_answer", ""),
                    student_answer=item.get("student_answer", ""),
                    correct_option_text=item.get("correct_option_text"),
                    student_option_text=item.get("student_option_text"),
                    explanation=item.get("explanation", ""),
                    is_correct=bool(item.get("is_correct", False)),
                    image_url=(iu if iu and is_safe_quiz_image_url(iu) else None),
                )
            )

        db.session.commit()

        return jsonify(
            {
                "status": "ok",
                "email": user.email,
                "correct": correct,
                "incorrect": incorrect,
                "total": total,
                "score_percent": score_percent,
                "review": review,
            }
        )
    except Exception as exc:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/recommend")
def api_recommend():
    try:
        from guidance.analyzer import WeaknessAnalyzer

        user = _current_user()
        if not user:
            return jsonify({"status": "error", "message": "Please sign in first."}), 401

        attempt_id = request.args.get("attempt_id", type=int)
        attempts_desc = (
            Attempt.query.filter_by(user_id=user.id).order_by(Attempt.created_at.desc(), Attempt.id.desc()).all()
        )
        selected_attempt = None
        if attempts_desc:
            if attempt_id is None:
                selected_attempt = attempts_desc[0]
            else:
                selected_attempt = next((a for a in attempts_desc if a.id == attempt_id), None)
                if selected_attempt is None:
                    return jsonify({"status": "error", "message": "Selected attempt not found."}), 404

        tracker = DBTrackerAdapter(
            user,
            attempt_id=selected_attempt.id if selected_attempt else None,
        )
        analyzer = WeaknessAnalyzer(tracker)
        concept_mapper = ConceptMapper()

        def _level_counts_for_topic(by_tl: dict[str, dict[str, int]], tid: str) -> dict[str, int]:
            if tid in by_tl:
                return by_tl[tid]
            for k, v in by_tl.items():
                if str(k) == tid:
                    return v
            return {}

        weak_topics = analyzer.weakest_topics(top_n=5)
        topic_lookup = _topic_name_lookup_from_topics_file()
        by_topic_level = analyzer.mistakes_by_topic_and_level()

        weak_topics_payload = []
        chapter_guidance = []
        guidance_notes = []
        study_recommendations: list[dict[str, Any]] = []
        textbook_by_grade = _textbook_index_by_grade()
        practice_topics = []
        for topic_id, mistakes in weak_topics:
            topic_id = str(topic_id)
            topic_name = topic_lookup.get(topic_id) or topic_id
            mapped = concept_mapper.map_topic(topic_name)
            level_counts = _level_counts_for_topic(by_topic_level, topic_id)
            top_levels = sorted(level_counts.items(), key=lambda x: (-x[1], x[0]))[:3]
            difficulty_hint = (
                ", ".join(f"{lv}: {c}" for lv, c in top_levels) if top_levels else None
            )
            weak_topics_payload.append(
                {
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "mistakes": int(mistakes),
                    "difficulty_breakdown": difficulty_hint,
                }
            )
            chapter_guidance.append(
                {
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "mistakes": int(mistakes),
                    "mapped_strands": mapped,
                }
            )

            # Build practice plan using mapped chapters and available textbooks.
            chapters_seen = set()
            practice_plan = []
            for strand in mapped:
                for chapter in strand.get("focus_chapters", []):
                    grade = chapter.get("grade")
                    chapter_name = str(chapter.get("topic", "")).strip()
                    if not isinstance(grade, int) or not chapter_name:
                        continue
                    key = (grade, chapter_name)
                    if key in chapters_seen:
                        continue
                    chapters_seen.add(key)
                    practice_plan.append(
                        {
                            "grade": grade,
                            "chapter": chapter_name,
                            "textbooks": textbook_by_grade.get(grade, []),
                        }
                    )
                    if len(practice_plan) >= 6:
                        break
                if len(practice_plan) >= 6:
                    break

            practice_topics.append(
                {
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "mistakes": int(mistakes),
                    "practice_plan": practice_plan,
                }
            )

            actions = [
                f"Grade {p['grade']}: {p['chapter']}"
                for p in practice_plan[:6]
                if p.get("grade") is not None and p.get("chapter")
            ]
            study_recommendations.append(
                {
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "mistakes": int(mistakes),
                    "practice_actions": actions,
                    "tip": (
                        f"Most missed items in this attempt were tagged {top_levels[0][0]} level."
                        if top_levels
                        else None
                    ),
                }
            )

        if not weak_topics_payload:
            guidance_notes.append("No mistakes in the selected attempt. Keep practicing to maintain performance.")
        else:
            overall_levels = analyzer.mistakes_by_level()
            if overall_levels:
                hardest = next(iter(overall_levels.items()))
                guidance_notes.append(
                    f"Across this attempt, the hardest cognitive level for you was “{hardest[0]}” "
                    f"({hardest[1]} mistake(s)). Mix quick recall with a few harder application items."
                )
            guidance_notes.append(
                "Use the Practice tab: open the grades and chapters listed under Recommendations "
                "before re-attempting a full quiz on the same topics."
            )

        attempts_payload = []
        total_attempts = len(attempts_desc)
        for i, attempt in enumerate(attempts_desc):
            attempts_payload.append(
                {
                    "attempt_id": attempt.id,
                    "label": f"Attempt {total_attempts - i} ({attempt.score_percent:.1f}%)",
                    "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
                    "score_percent": attempt.score_percent,
                    "incorrect": attempt.incorrect,
                }
            )

        return jsonify(
            {
                "status": "ok",
                "email": user.email,
                "selected_attempt_id": selected_attempt.id if selected_attempt else None,
                "attempts": attempts_payload,
                "weak_topics": weak_topics_payload,
                "guidance_notes": guidance_notes,
                "study_recommendations": study_recommendations,
                "chapter_guidance": chapter_guidance,
                "practice_topics": practice_topics,
                "score_trend": analyzer.score_trend(),
                "mistakes_by_topic": analyzer.mistakes_by_topic(),
                "mistakes_by_level": analyzer.mistakes_by_level(),
            }
        )
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


def _gamification_badge_defs() -> list[dict[str, str]]:
    return [
        {
            "id": "first_spark",
            "title": "First Spark",
            "description": "Complete your first quiz attempt.",
            "icon": "bolt",
        },
        {
            "id": "steady_pulse",
            "title": "Steady Pulse",
            "description": "Complete 5 quiz attempts.",
            "icon": "monitor_heart",
        },
        {
            "id": "deep_network",
            "title": "Deep Network",
            "description": "Complete 15 quiz attempts.",
            "icon": "hub",
        },
        {
            "id": "synaptic_storm",
            "title": "Synaptic Storm",
            "description": "Complete 40 quiz attempts.",
            "icon": "storm",
        },
        {
            "id": "perfect_loop",
            "title": "Perfect Loop",
            "description": "Score 100% on any attempt.",
            "icon": "all_inclusive",
        },
        {
            "id": "sharp_mind",
            "title": "Sharp Mind",
            "description": "Finish at least 5 attempts with 90% or higher.",
            "icon": "psychology",
        },
        {
            "id": "streak_3",
            "title": "Three-Day Chain",
            "description": "Practice on 3 consecutive calendar days.",
            "icon": "link",
        },
        {
            "id": "streak_7",
            "title": "Weekly Rhythm",
            "description": "Practice on 7 consecutive calendar days.",
            "icon": "calendar_month",
        },
        {
            "id": "wide_reach",
            "title": "Wide Reach",
            "description": "Practice on 10 different calendar days.",
            "icon": "scatter_plot",
        },
        {
            "id": "century_mind",
            "title": "Century Mind",
            "description": "Answer 100 questions correctly across all attempts.",
            "icon": "blur_on",
        },
        {
            "id": "double_century",
            "title": "Double Century",
            "description": "Answer 300 questions correctly across all attempts.",
            "icon": "numbers",
        },
        {
            "id": "comeback",
            "title": "Comeback Trail",
            "description": "Improve your score by 20 points or more versus your previous attempt.",
            "icon": "trending_up",
        },
        {
            "id": "marathon_focus",
            "title": "Marathon Focus",
            "description": "Earn a perfect score on a paper with at least 20 questions.",
            "icon": "emoji_events",
        },
    ]


def _compute_gamification(user: User) -> dict[str, Any]:
    attempts = (
        Attempt.query.filter_by(user_id=user.id).order_by(Attempt.created_at.asc(), Attempt.id.asc()).all()
    )
    by_day: dict[str, int] = defaultdict(int)
    scores: list[float] = []
    total_correct = 0
    perfect_count = 0
    high_90_count = 0
    marathon_perfect = False

    for a in attempts:
        if a.created_at:
            day = a.created_at.date().isoformat()
            by_day[day] += 1
        sp = float(a.score_percent or 0)
        scores.append(sp)
        total_correct += int(a.correct or 0)
        if sp >= 99.5:
            perfect_count += 1
            if int(a.total_questions or 0) >= 20:
                marathon_perfect = True
        if sp >= 90:
            high_90_count += 1

    n = len(attempts)
    distinct_days = len(by_day)

    sorted_days = sorted(by_day.keys())
    best_streak = 0
    cur_streak = 0
    prev_d: datetime | None = None
    for ds in sorted_days:
        d = datetime.fromisoformat(ds).date()
        if prev_d is None:
            cur_streak = 1
        else:
            delta = (d - prev_d).days
            if delta == 1:
                cur_streak += 1
            elif delta == 0:
                pass
            else:
                cur_streak = 1
        best_streak = max(best_streak, cur_streak)
        prev_d = d

    current_streak = 0
    if sorted_days:
        today = datetime.utcnow().date()
        walk = today
        day_set = set(sorted_days)
        while walk.isoformat() in day_set:
            current_streak += 1
            walk = walk - timedelta(days=1)

    comeback = False
    for i in range(1, len(scores)):
        if scores[i] - scores[i - 1] >= 20:
            comeback = True
            break

    stats = {
        "total_attempts": n,
        "total_correct_answers": total_correct,
        "distinct_practice_days": distinct_days,
        "best_day_streak": best_streak,
        "current_day_streak": current_streak,
        "perfect_attempts": perfect_count,
        "attempts_90_plus": high_90_count,
        "has_comeback": comeback,
        "marathon_perfect": marathon_perfect,
    }

    earned_ids: set[str] = set()
    if n >= 1:
        earned_ids.add("first_spark")
    if n >= 5:
        earned_ids.add("steady_pulse")
    if n >= 15:
        earned_ids.add("deep_network")
    if n >= 40:
        earned_ids.add("synaptic_storm")
    if perfect_count >= 1:
        earned_ids.add("perfect_loop")
    if high_90_count >= 5:
        earned_ids.add("sharp_mind")
    if best_streak >= 3:
        earned_ids.add("streak_3")
    if best_streak >= 7:
        earned_ids.add("streak_7")
    if distinct_days >= 10:
        earned_ids.add("wide_reach")
    if total_correct >= 100:
        earned_ids.add("century_mind")
    if total_correct >= 300:
        earned_ids.add("double_century")
    if comeback:
        earned_ids.add("comeback")
    if marathon_perfect:
        earned_ids.add("marathon_focus")

    badges_out = []
    for b in _gamification_badge_defs():
        badges_out.append({**b, "earned": b["id"] in earned_ids})

    return {
        "attempts_by_day": dict(sorted(by_day.items())),
        "stats": stats,
        "badges": badges_out,
        "earned_count": len(earned_ids),
        "total_badges": len(badges_out),
    }


@app.route("/api/gamification")
def api_gamification():
    try:
        user = _current_user()
        if not user:
            return jsonify({"status": "error", "message": "Not signed in."}), 401
        payload = _compute_gamification(user)
        return jsonify({"status": "ok", **payload})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/attempts")
def api_attempts():
    try:
        user = _current_user()
        if not user:
            return jsonify({"status": "error", "message": "Please sign in first."}), 401

        attempts = (
            Attempt.query.filter_by(user_id=user.id).order_by(Attempt.created_at.desc(), Attempt.id.desc()).all()
        )

        payload = []
        for attempt in attempts:
            rows = (
                AttemptQuestion.query.filter_by(attempt_id=attempt.id)
                .order_by(AttemptQuestion.question_index.asc())
                .all()
            )
            questions = []
            for row in rows:
                try:
                    options = json.loads(row.options_json or "[]")
                    if not isinstance(options, list):
                        options = []
                except Exception:
                    options = []
                iu = getattr(row, "image_url", None) or ""
                qrow = {
                    "index": row.question_index,
                    "question": row.question,
                    "options": options,
                    "student_answer": row.student_answer or "",
                    "correct_answer": row.correct_answer or "",
                    "student_option_text": row.student_option_text or "",
                    "correct_option_text": row.correct_option_text or "",
                    "explanation": row.explanation or "",
                    "is_correct": bool(row.is_correct),
                }
                if iu and is_safe_quiz_image_url(iu):
                    qrow["image_url"] = iu
                questions.append(qrow)

            payload.append(
                {
                    "attempt_id": attempt.id,
                    "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
                    "score_percent": attempt.score_percent,
                    "total_questions": attempt.total_questions,
                    "correct": attempt.correct,
                    "incorrect": attempt.incorrect,
                    "questions": questions,
                }
            )

        return jsonify({"status": "ok", "attempts": payload})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=not _is_production, port=int(os.getenv("PORT", 5000)))
