-- Assistant: chat history (conversations and their messages) and per-user fact memory. Every row belongs to a user.
CREATE TABLE conversations (
    conversation_id text PRIMARY KEY,
    user_id         text   NOT NULL,
    title           text   NOT NULL,
    summary         text   NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_user_idx ON conversations (user_id, updated_at DESC);

CREATE TABLE messages (
    message_id      bigserial PRIMARY KEY,
    conversation_id text   NOT NULL REFERENCES conversations (conversation_id) ON DELETE CASCADE,
    role            text   NOT NULL CHECK (role IN ('user', 'assistant')),
    text            text   NOT NULL,
    cards           jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation_idx ON messages (conversation_id, message_id);

CREATE TABLE facts (
    fact_id    text PRIMARY KEY,
    user_id    text   NOT NULL,
    kind       text   NOT NULL CHECK (kind IN ('permanent', 'temporal')),
    key        text   NOT NULL,
    value      text   NOT NULL,
    category   text   NOT NULL DEFAULT '',
    embedding  real[],
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz                      -- set for temporal facts only
);
CREATE INDEX facts_user_idx ON facts (user_id);
