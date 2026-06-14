# Database Schema - Mermaid ER Diagram

```
mermaid
erDiagram
    USERS ||--o| USER_SETTINGS : has
    USERS ||--o{ ATTEMPTS : makes
    ATTEMPTS ||--o{ MISTAKES : contains
    ATTEMPTS ||--o{ ATTEMPT_QUESTIONS : includes

    USERS {
        int id PK
        string email UK
        string name
        string password_hash
        datetime joined_at
    }

    USER_SETTINGS {
        int id PK
        int user_id FK
        string theme
        string difficulty
        datetime updated_at
    }

    ATTEMPTS {
        int id PK
        int user_id FK
        datetime created_at
        int total_questions
        int correct
        int incorrect
        float score_percent
    }

    MISTAKES {
        int id PK
        int attempt_id FK
        int question_index
        text question
        string topic_id
        string difficulty_level
        string correct_answer
        string student_answer
    }

    ATTEMPT_QUESTIONS {
        int id PK
        int attempt_id FK
        int question_index
        text question
        text options_json
        string correct_answer
        string student_answer
        text correct_option_text
        text student_option_text
        text explanation
        boolean is_correct
    }
```

## Relationships Explanation

- **USERS to USER_SETTINGS**: One-to-One (Each user has one settings record)
- **USERS to ATTEMPTS**: One-to-Many (A user can have multiple quiz attempts)
- **ATTEMPTS to MISTAKES**: One-to-Many (Each attempt can contain multiple mistakes)
- **ATTEMPTS to ATTEMPT_QUESTIONS**: One-to-Many (Each attempt includes multiple questions)

## Live Editor URL

To view/edit this diagram live, copy the code above and paste it into:
**https://mermaid.live/**
