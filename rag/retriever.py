"""Retriever module."""

import json
from typing import List

from langchain_community.vectorstores import Chroma

import config
from rag.embeddings import build_embeddings


def get_vector_store():
    """Load the persisted ChromaDB vector store."""
    embeddings = build_embeddings()
    return Chroma(
        persist_directory=config.CHROMA_PERSIST_DIR,
        collection_name=config.CHROMA_COLLECTION_NAME,
        embedding_function=embeddings,
    )


def retrieve_for_topic(
    topic_name: str,
    difficulty_level: str,
    grade: int | None = None,
    k: int = 5,
    vectorstore=None,
) -> List[str]:
    """Retrieve top-k relevant chunks for one topic and difficulty."""
    query = f"{topic_name} - {difficulty_level} level content"
    if grade is not None:
        query = f"Grade {grade} {query}"
    vs = vectorstore if vectorstore is not None else get_vector_store()
    try:
        if grade is not None:
            results = vs.similarity_search(query, k=k, filter={"grade": grade})
        else:
            results = vs.similarity_search(query, k=k)
    except Exception:
        results = vs.similarity_search(query, k=k)
    return [doc.page_content for doc in results]


def retrieve_for_syllabus(syllabus: dict, k_per_topic: int = 3) -> dict:
    """Retrieve relevant chunks for every topic in the syllabus."""
    topics_lookup = {}
    try:
        with open(config.TOPICS_FILE, "r", encoding="utf-8") as f:
            topics = json.load(f)
            topics_lookup = {t["id"]: t["name"] for t in topics}
    except Exception:
        pass

    levels_lookup = {}
    try:
        with open(config.DIFFICULTY_LEVELS_FILE, "r", encoding="utf-8") as f:
            levels = json.load(f)
            levels_lookup = {l["level_id"]: l["name"] for l in levels}
    except Exception:
        pass

    results = {}
    vectorstore = get_vector_store()
    for mapping in syllabus.get("topic_mappings", []):
        topic_id = mapping["topic_id"]
        topic_name = mapping.get("topic_name") or topics_lookup.get(topic_id, topic_id)
        grade = mapping.get("grade")
        if not isinstance(grade, int):
            grade = None

        required_levels = mapping.get("required_levels", [1])
        highest_level = max(required_levels)
        level_name = levels_lookup.get(highest_level, "Remember")

        print(f"Retrieving for: {topic_name} @ {level_name} level (grade={grade})")
        results[topic_id] = retrieve_for_topic(
            topic_name=topic_name,
            difficulty_level=level_name,
            grade=grade,
            k=k_per_topic,
            vectorstore=vectorstore,
        )
    return results
