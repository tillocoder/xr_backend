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
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS rank_theme VARCHAR(24) NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE community_profiles
            ADD COLUMN IF NOT EXISTS rank_theme VARCHAR(24) NULL
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS ai_provider_configs (
                id SERIAL PRIMARY KEY,
                provider VARCHAR(24) NOT NULL DEFAULT 'gemini',
                usage_scope VARCHAR(24) NOT NULL DEFAULT 'default',
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
            ADD COLUMN IF NOT EXISTS usage_scope VARCHAR(24) NOT NULL DEFAULT 'default'
            """
        )
    )
    await connection.execute(
        text(
            """
            UPDATE ai_provider_configs
            SET usage_scope = 'default'
            WHERE COALESCE(BTRIM(usage_scope), '') = ''
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
            ALTER TABLE news_articles
            ADD COLUMN IF NOT EXISTS images_json JSONB NOT NULL DEFAULT '[]'::jsonb
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE news_articles
            ADD COLUMN IF NOT EXISTS content_blocks_json JSONB NOT NULL DEFAULT '[]'::jsonb
            """
        )
    )
    await connection.execute(
        text(
            """
            ALTER TABLE news_article_translations
            ADD COLUMN IF NOT EXISTS content_blocks_json JSONB NOT NULL DEFAULT '[]'::jsonb
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
            CREATE INDEX IF NOT EXISTS ix_news_articles_visible_at_id
            ON news_articles ((COALESCE(published_at, released_at, created_at)), id DESC)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_news_articles_liquidation_visible_at_id
            ON news_articles (is_liquidation, (COALESCE(published_at, released_at, created_at)), id DESC)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_news_articles_category_visible_at_id
            ON news_articles (category, (COALESCE(published_at, released_at, created_at)), id DESC)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_news_articles_source_visible_at_id
            ON news_articles (source, (COALESCE(published_at, released_at, created_at)), id DESC)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_news_articles_unnotified_released_at_id
            ON news_articles (released_at DESC, id DESC)
            WHERE notified_at IS NULL AND released_at IS NOT NULL
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
            CREATE INDEX IF NOT EXISTS ix_ai_provider_configs_provider_usage_scope_sort_order
            ON ai_provider_configs (provider, usage_scope, sort_order)
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
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_posts_author_created_at
            ON posts (author_id, created_at DESC)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_posts_symbol_created_at_id
            ON posts (symbol, created_at DESC, id DESC)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_posts_market_bias_created_at_id
            ON posts (market_bias, created_at DESC, id DESC)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_posts_created_at_id
            ON posts (created_at DESC, id DESC)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_posts_symbols_json_gin
            ON posts
            USING GIN (symbols_json)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_chats_last_message_id
            ON chats (last_message_id)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_chat_members_user_unread_count
            ON chat_members (user_id, unread_count)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_messages_reply_to_message_id
            ON messages (reply_to_message_id)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_messages_chat_created_at_id
            ON messages (chat_id, created_at DESC, id DESC)
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS market_coins (
                id VARCHAR(64) PRIMARY KEY,
                symbol VARCHAR(24) NOT NULL,
                name VARCHAR(120) NOT NULL,
                image_url VARCHAR(1024) NULL,
                market_cap_rank INTEGER NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS market_price_points (
                id BIGSERIAL PRIMARY KEY,
                coin_id VARCHAR(64) NOT NULL REFERENCES market_coins(id),
                price_usd DOUBLE PRECISION NOT NULL,
                change_24h DOUBLE PRECISION NOT NULL DEFAULT 0,
                quote_volume_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
                market_cap_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
                source VARCHAR(24) NOT NULL DEFAULT 'coingecko',
                captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS user_target_alerts (
                id VARCHAR(32) PRIMARY KEY,
                user_id VARCHAR(32) NOT NULL REFERENCES users(id),
                coin_id VARCHAR(64) NOT NULL REFERENCES market_coins(id),
                symbol VARCHAR(24) NOT NULL,
                target_price DOUBLE PRECISION NOT NULL,
                direction VARCHAR(12) NOT NULL DEFAULT 'above',
                is_active BOOLEAN NOT NULL DEFAULT true,
                last_triggered_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS market_alert_events (
                id VARCHAR(32) PRIMARY KEY,
                kind VARCHAR(32) NOT NULL,
                coin_id VARCHAR(64) NULL REFERENCES market_coins(id),
                dedupe_key VARCHAR(160) NOT NULL,
                title VARCHAR(180) NOT NULL,
                body VARCHAR(400) NOT NULL,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    await connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_market_coins_symbol ON market_coins (symbol)")
    )
    await connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_market_coins_rank ON market_coins (market_cap_rank)")
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_market_price_points_coin_captured_at ON market_price_points (coin_id, captured_at)"
        )
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_market_price_points_captured_at ON market_price_points (captured_at)"
        )
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_user_target_alerts_user_active ON user_target_alerts (user_id, is_active)"
        )
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_user_target_alerts_coin_active ON user_target_alerts (coin_id, is_active)"
        )
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_market_alert_events_kind_created ON market_alert_events (kind, created_at)"
        )
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_market_alert_events_dedupe ON market_alert_events (dedupe_key, created_at)"
        )
    )
