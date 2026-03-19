from __future__ import annotations

from sqlalchemy import text


async def ensure_runtime_tables(connection) -> None:
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS diamonds_balance INTEGER NOT NULL DEFAULT 0
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS daily_reward_streak INTEGER NOT NULL DEFAULT 0
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS daily_reward_last_claimed_at TIMESTAMPTZ NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS reward_pro_expires_at TIMESTAMPTZ NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS settings_json JSONB NOT NULL DEFAULT '{}'::jsonb
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS linked_wallets_json JSONB NOT NULL DEFAULT '[]'::jsonb
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS ai_provider_configs (
                id SERIAL PRIMARY KEY,
                provider VARCHAR(24) NOT NULL DEFAULT 'gemini',
                label VARCHAR(64) NOT NULL DEFAULT '',
                api_key VARCHAR(512) NULL,
                model VARCHAR(64) NOT NULL DEFAULT 'gemini-3-flash-preview',
                sort_order INTEGER NOT NULL DEFAULT 1,
                enabled BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE ai_provider_configs
            ADD COLUMN IF NOT EXISTS label VARCHAR(64) NOT NULL DEFAULT ''
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE news_articles
            ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE news_articles
            ADD COLUMN IF NOT EXISTS source_guid VARCHAR(512) NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE news_articles
            ADD COLUMN IF NOT EXISTS category VARCHAR(32) NOT NULL DEFAULT 'altcoins'
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE news_articles
            ADD COLUMN IF NOT EXISTS view_count INTEGER NOT NULL DEFAULT 0
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_news_articles_released_at_notified_at
            ON news_articles (released_at, notified_at)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_news_articles_category_published_at
            ON news_articles (category, published_at)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_news_articles_view_count_published_at
            ON news_articles (view_count, published_at)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_news_articles_url
            ON news_articles (url)
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE ai_provider_configs
            ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 1
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id VARCHAR(32) PRIMARY KEY,
                user_id VARCHAR(32) NOT NULL REFERENCES users(id),
                access_token_hash VARCHAR(64) NOT NULL UNIQUE,
                refresh_token_hash VARCHAR(64) NOT NULL UNIQUE,
                access_expires_at TIMESTAMPTZ NOT NULL,
                refresh_expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE news_feed_state
            ADD COLUMN IF NOT EXISTS daily_fetch_count INTEGER NOT NULL DEFAULT 0
            """
        )
    )
    await connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions (user_id)")
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_auth_sessions_access_token_hash ON auth_sessions (access_token_hash)"
        )
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_auth_sessions_refresh_token_hash ON auth_sessions (refresh_token_hash)"
        )
    )
