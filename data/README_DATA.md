# Data Files — Schema Reference

This directory contains the data files that drive MCQ generation. Fill them in
with your own syllabus information.

---

## `topics.json`

An array of topic objects:

| Field            | Type          | Description                                 |
|------------------|---------------|---------------------------------------------|
| `id`             | `string`      | Unique identifier, e.g. `"topic_001"`       |
| `name`           | `string`      | Human-readable topic name                   |
| `description`    | `string`      | What this topic covers                      |
| `parent_topic_id`| `string|null` | ID of parent topic (for hierarchy), or null |
| `tags`           | `string[]`    | Free-form tags for filtering                |
| `subtopics`      | `object[]`    | Nested subtopics (same shape minus subtopics)|

---

## `difficulty_levels.json`

An array of difficulty level objects (Bloom's Taxonomy):

| Field            | Type       | Description                              |
|------------------|------------|------------------------------------------|
| `level_id`       | `integer`  | 1–6, matching Bloom's hierarchy          |
| `name`           | `string`   | e.g. "Remember", "Understand", "Apply"   |
| `bloom_category` | `string`   | Formal Bloom's category name             |
| `description`    | `string`   | What this level tests                    |
| `question_stems` | `string[]` | Prompt starters for this level           |
| `example`        | `string`   | Example question at this level           |

---

## `syllabus.json`

Top-level object with:

| Field              | Type       | Description                          |
|--------------------|------------|--------------------------------------|
| `syllabus_name`    | `string`   | Name of the syllabus/exam            |
| `total_questions`  | `integer`  | Number of MCQs on the paper          |
| `time_limit_minutes`| `integer` | Exam duration                        |
| `topic_mappings`   | `object[]` | Per-topic configuration (see below)  |

Each topic mapping:

| Field              | Type       | Description                            |
|--------------------|------------|----------------------------------------|
| `topic_id`         | `string`   | References an `id` from `topics.json`  |
| `required_levels`  | `int[]`    | Which Bloom's levels to test           |
| `weightage_percent`| `number`   | % of total paper this topic covers     |
| `min_questions`    | `integer`  | Minimum questions from this topic      |
| `max_questions`    | `integer`  | Maximum questions from this topic      |
| `notes`            | `string`   | Free-text notes for paper setters      |
