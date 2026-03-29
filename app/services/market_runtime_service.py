from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import Select, delete, desc, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models.entities import MarketAlertEvent, MarketCoin, MarketPricePoint, User, UserTargetAlert
from app.services.cache import RedisCache
from app.services.db_runtime_lock_service import (
    release_session_advisory_lock,
    try_acquire_session_advisory_lock,
)
from app.services.daily_reward_service import DailyRewardService
from app.services.notification_service import NotificationService
from app.services.periodic_runtime_service import PeriodicRuntimeService
from app.services.runtime_lease_service import RuntimeLeaseService


logger = logging.getLogger(__name__)

_SNAPSHOT_CACHE_KEY = "market:signals:snapshot:v1"
_SNAPSHOT_CACHE_GRACE_SECONDS = 240
_PRO_SIGNAL_DEDUPE_HOURS = 8
_MARKET_ALERT_DEDUPE_HOURS = 6
_TARGET_TRIGGER_COOLDOWN_HOURS = 24
_MARKET_RUNTIME_DB_LOCK_KEY = 60241612


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True, frozen=True)
class SmartSignalPreferences:
    enabled: bool = True
    price_target_enabled: bool = True
    percent_change_enabled: bool = True
    volume_spike_enabled: bool = True
    fear_greed_extreme_enabled: bool = True
    whale_activity_enabled: bool = True
    percent_threshold: float = 5.0
    volume_multiplier: float = 1.8

    @classmethod
    def from_user_settings(cls, settings_json: object) -> "SmartSignalPreferences":
        root = settings_json if isinstance(settings_json, dict) else {}
        raw = root.get("smartSignals")
        data = raw if isinstance(raw, dict) else {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            price_target_enabled=bool(data.get("priceTargetEnabled", True)),
            percent_change_enabled=bool(data.get("percentChangeEnabled", True)),
            volume_spike_enabled=bool(data.get("volumeSpikeEnabled", True)),
            fear_greed_extreme_enabled=bool(data.get("fearGreedExtremeEnabled", True)),
            whale_activity_enabled=bool(data.get("whaleActivityEnabled", True)),
            percent_threshold=_bounded_float(data.get("percentThreshold"), fallback=5.0, minimum=1.0, maximum=25.0),
            volume_multiplier=_bounded_float(
                data.get("volumeMultiplier"),
                fallback=1.8,
                minimum=1.2,
                maximum=6.0,
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "priceTargetEnabled": self.price_target_enabled,
            "percentChangeEnabled": self.percent_change_enabled,
            "volumeSpikeEnabled": self.volume_spike_enabled,
            "fearGreedExtremeEnabled": self.fear_greed_extreme_enabled,
            "whaleActivityEnabled": self.whale_activity_enabled,
            "percentThreshold": round(self.percent_threshold, 2),
            "volumeMultiplier": round(self.volume_multiplier, 2),
        }


def _bounded_float(
    value: object,
    *,
    fallback: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return fallback
    if normalized < minimum:
        return minimum
    if normalized > maximum:
        return maximum
    return normalized


class MarketRuntimeService(PeriodicRuntimeService):
    def __init__(
        self,
        *,
        settings: Settings,
        cache: RedisCache,
        notification_service: NotificationService,
        lease_service: RuntimeLeaseService,
        poll_interval_seconds: int | None = None,
        tracked_limit: int | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._cache = cache
        self._notification_service = notification_service
        self._lease_service = lease_service
        self._daily_rewards = DailyRewardService()
        self._poll_interval_seconds = max(
            30, int(poll_interval_seconds or settings.market_poll_interval_seconds)
        )
        self._tracked_limit = max(5, int(tracked_limit or settings.market_tracked_limit))
        self._manual_refresh_task: asyncio.Task | None = None
        self._manual_refresh_lock = asyncio.Lock()

    async def stop(self) -> None:
        await super().stop()
        manual_refresh_task = self._manual_refresh_task
        self._manual_refresh_task = None
        if manual_refresh_task is not None:
            manual_refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await manual_refresh_task

    @property
    def poll_interval_seconds(self) -> int:
        return self._poll_interval_seconds

    async def run_cycle(self) -> None:
        lease = await self._lease_service.acquire(
            "runtime:market:cycle",
            ttl_seconds=max(self._poll_interval_seconds * 3, 120),
        )
        if lease is None:
            return
        async with SessionLocal() as db:
            lock_acquired = await try_acquire_session_advisory_lock(
                db,
                _MARKET_RUNTIME_DB_LOCK_KEY,
                lock_name="runtime:market:cycle",
            )
            if not lock_acquired:
                await lease.release()
                return
            try:
                await self.refresh_market_snapshot(db)
            except Exception:
                await db.rollback()
                logger.exception("market_runtime_cycle_failed")
            finally:
                await release_session_advisory_lock(db, _MARKET_RUNTIME_DB_LOCK_KEY)
                await lease.release()

    async def refresh_market_snapshot(
        self,
        db: AsyncSession,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        cached = None if force_refresh else await self._cache.get_json(_SNAPSHOT_CACHE_KEY)
        if isinstance(cached, dict):
            updated_at = _parse_datetime(cached.get("updatedAt"))
            if updated_at is not None:
                age_seconds = (_utc_now() - updated_at).total_seconds()
                if age_seconds <= max(10, self._settings.market_cache_ttl_seconds):
                    return cached

        tracked_ids = await self._load_active_target_coin_ids(db)
        top_rows = await self._fetch_market_rows(limit=self._tracked_limit)
        top_by_id = {
            str(item.get("id") or "").strip(): item
            for item in top_rows
            if str(item.get("id") or "").strip()
        }
        extra_ids = [coin_id for coin_id in tracked_ids if coin_id not in top_by_id]
        extra_rows = await self._fetch_market_rows(ids=extra_ids) if extra_ids else []
        all_rows = [*top_rows, *extra_rows]
        if not all_rows:
            db_snapshot = await self._build_snapshot_from_db(db)
            if db_snapshot is not None:
                return db_snapshot
            return self._empty_snapshot()

        fear_greed = await self._fetch_fear_greed_index()
        snapshot = await self._persist_snapshot(
            db,
            market_rows=all_rows,
            top_market_ids=list(top_by_id.keys()),
            fear_greed=fear_greed,
        )
        await self._emit_market_alerts(db, snapshot)
        await self._emit_target_alerts(db, snapshot)
        await self._emit_pro_signals(db, snapshot)
        await self._prune_old_price_points(db)
        await db.commit()

        ttl_seconds = max(self._poll_interval_seconds + 15, self._settings.market_cache_ttl_seconds)
        await self._cache.set_json(_SNAPSHOT_CACHE_KEY, snapshot, ttl_seconds=ttl_seconds)
        return snapshot

    async def get_signal_bootstrap(self, db: AsyncSession, *, user_id: str) -> dict[str, Any]:
        snapshot = await self.get_snapshot(db)
        user = await db.get(User, user_id)
        is_pro = self.user_has_pro_access(user)
        prefs = SmartSignalPreferences.from_user_settings(user.settings_json if user else {})
        targets = await self.list_target_alerts(db, user_id=user_id, snapshot=snapshot)
        recent_alerts = await self.list_recent_alerts(db, limit=6)
        membership_tier = (
            self._daily_rewards.effective_membership_tier_user(user)
            if user is not None
            else "free"
        )
        return {
            "isPro": is_pro,
            "membershipTier": membership_tier,
            "preferences": prefs.to_json(),
            "targets": targets,
            "snapshot": snapshot,
            "recentAlerts": recent_alerts,
        }

    async def get_snapshot(self, db: AsyncSession) -> dict[str, Any]:
        cached = await self._cache.get_json(_SNAPSHOT_CACHE_KEY)
        if isinstance(cached, dict):
            updated_at = _parse_datetime(cached.get("updatedAt"))
            if updated_at is not None:
                age_seconds = (_utc_now() - updated_at).total_seconds()
                if age_seconds <= _SNAPSHOT_CACHE_GRACE_SECONDS:
                    return cached
        snapshot = await self._build_snapshot_from_db(db)
        if snapshot is not None:
            await self._cache.set_json(
                _SNAPSHOT_CACHE_KEY,
                snapshot,
                ttl_seconds=max(self._poll_interval_seconds + 15, self._settings.market_cache_ttl_seconds),
            )
            return snapshot
        return await self.refresh_market_snapshot(db, force_refresh=True)

    async def request_market_snapshot_refresh(self, db: AsyncSession) -> dict[str, Any]:
        cached = await self._cache.get_json(_SNAPSHOT_CACHE_KEY)
        if isinstance(cached, dict):
            updated_at = _parse_datetime(cached.get("updatedAt"))
            if updated_at is not None:
                age_seconds = max(0.0, (_utc_now() - updated_at).total_seconds())
                if age_seconds <= max(10, self._poll_interval_seconds):
                    await self._ensure_manual_refresh_task()
                    return cached
        snapshot = await self._build_snapshot_from_db(db)
        if snapshot is not None:
            await self._cache.set_json(
                _SNAPSHOT_CACHE_KEY,
                snapshot,
                ttl_seconds=max(self._poll_interval_seconds + 15, self._settings.market_cache_ttl_seconds),
            )
            await self._ensure_manual_refresh_task()
            return snapshot
        return await self.refresh_market_snapshot(db, force_refresh=True)

    async def _ensure_manual_refresh_task(self) -> None:
        async with self._manual_refresh_lock:
            task = self._manual_refresh_task
            if task is not None and not task.done():
                return
            self._manual_refresh_task = asyncio.create_task(self._refresh_market_snapshot_in_background())

    async def _refresh_market_snapshot_in_background(self) -> None:
        try:
            async with SessionLocal() as db:
                await self.refresh_market_snapshot(db, force_refresh=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("market_manual_refresh_failed")
        finally:
            current_task = asyncio.current_task()
            if self._manual_refresh_task is current_task:
                self._manual_refresh_task = None

    async def list_recent_alerts(self, db: AsyncSession, *, limit: int = 6) -> list[dict[str, Any]]:
        rows = (
            await db.scalars(
                select(MarketAlertEvent)
                .order_by(MarketAlertEvent.created_at.desc())
                .limit(max(1, min(limit, 12)))
            )
        ).all()
        return [
            {
                "id": row.id,
                "kind": row.kind,
                "coinId": row.coin_id,
                "title": row.title,
                "body": row.body,
                "payload": dict(row.payload_json if isinstance(row.payload_json, dict) else {}),
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]

    async def update_preferences(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        user = await db.get(User, user_id)
        if user is None:
            raise ValueError("User not found.")
        if not self.user_has_pro_access(user):
            raise PermissionError("Smart signals are available on XR Pro.")
        current = dict(user.settings_json if isinstance(user.settings_json, dict) else {})
        current["smartSignals"] = SmartSignalPreferences.from_user_settings(
            {"smartSignals": payload}
        ).to_json()
        user.settings_json = current
        await db.commit()
        return current["smartSignals"]

    async def list_target_alerts(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        snapshot: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(UserTargetAlert, MarketCoin)
                .join(MarketCoin, MarketCoin.id == UserTargetAlert.coin_id)
                .where(UserTargetAlert.user_id == user_id)
                .order_by(UserTargetAlert.created_at.desc())
            )
        ).all()
        snapshot = snapshot if isinstance(snapshot, dict) else await self.get_snapshot(db)
        tracked_items = [
            *snapshot.get("trackedCoins", []),
            *snapshot.get("coins", []),
        ]
        price_map = {
            str(item.get("id") or "").strip(): item
            for item in tracked_items
            if isinstance(item, dict)
        }
        items: list[dict[str, Any]] = []
        for alert, coin in rows:
            price_item = price_map.get(coin.id, {})
            items.append(
                {
                    "id": alert.id,
                    "coinId": coin.id,
                    "symbol": coin.symbol.upper(),
                    "name": coin.name,
                    "image": coin.image_url or "",
                    "targetPrice": float(alert.target_price),
                    "direction": alert.direction,
                    "isActive": bool(alert.is_active),
                    "lastTriggeredAt": alert.last_triggered_at.isoformat()
                    if alert.last_triggered_at is not None
                    else None,
                    "currentPrice": _to_float(price_item.get("price"), 0),
                    "change24h": _to_float(price_item.get("change24h"), 0),
                    "createdAt": alert.created_at.isoformat(),
                }
            )
        return items

    async def create_target_alert(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        symbol: str,
        target_price: float,
    ) -> dict[str, Any]:
        user = await db.get(User, user_id)
        if user is None:
            raise ValueError("User not found.")
        if not self.user_has_pro_access(user):
            raise PermissionError("Target alerts are available on XR Pro.")

        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("Coin symbol is required.")
        if target_price <= 0:
            raise ValueError("Target price must be greater than zero.")

        coin_payload = await self.ensure_coin_for_symbol(db, normalized_symbol)
        if coin_payload is None:
            raise ValueError(f"{normalized_symbol} was not found on market feeds.")

        current_price = _to_float(coin_payload.get("price"), 0)
        direction = "above" if target_price >= current_price else "below"
        alert = UserTargetAlert(
            user_id=user_id,
            coin_id=coin_payload["id"],
            symbol=normalized_symbol,
            target_price=float(target_price),
            direction=direction,
            is_active=True,
        )
        db.add(alert)
        await db.commit()
        await db.refresh(alert)
        return {
            "id": alert.id,
            "coinId": coin_payload["id"],
            "symbol": normalized_symbol,
            "name": coin_payload.get("name", normalized_symbol),
            "image": coin_payload.get("image", ""),
            "targetPrice": float(target_price),
            "direction": direction,
            "isActive": True,
            "currentPrice": current_price,
            "change24h": _to_float(coin_payload.get("change24h"), 0),
            "createdAt": alert.created_at.isoformat(),
        }

    async def delete_target_alert(self, db: AsyncSession, *, user_id: str, target_id: str) -> bool:
        deleted_id = await db.scalar(
            delete(UserTargetAlert)
            .where(
                UserTargetAlert.id == target_id,
                UserTargetAlert.user_id == user_id,
            )
            .returning(UserTargetAlert.id)
        )
        if deleted_id is None:
            return False
        await db.commit()
        return True

    async def ensure_coin_for_symbol(self, db: AsyncSession, symbol: str) -> dict[str, Any] | None:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            return None

        existing = await db.scalar(
            select(MarketCoin).where(func.upper(MarketCoin.symbol) == normalized_symbol).limit(1)
        )
        if existing is not None:
            latest = await db.scalar(
                select(MarketPricePoint)
                .where(MarketPricePoint.coin_id == existing.id)
                .order_by(MarketPricePoint.captured_at.desc())
                .limit(1)
            )
            return {
                "id": existing.id,
                "symbol": existing.symbol.upper(),
                "name": existing.name,
                "image": existing.image_url or "",
                "price": float(latest.price_usd) if latest is not None else 0,
                "change24h": float(latest.change_24h) if latest is not None else 0,
                "marketCap": float(latest.market_cap_usd) if latest is not None else 0,
                "totalVolume": float(latest.quote_volume_usd) if latest is not None else 0,
                "rank": int(existing.market_cap_rank or 0),
            }

        candidates = await self._search_market_symbol(normalized_symbol)
        if not candidates:
            return None
        coin_id = candidates[0]["id"]
        rows = await self._fetch_market_rows(ids=[coin_id])
        if not rows:
            return None
        snapshot = await self._persist_snapshot(
            db,
            market_rows=rows,
            top_market_ids=[],
            fear_greed=None,
        )
        coin_row = next(
            (
                item
                for item in [*snapshot.get("trackedCoins", []), *snapshot.get("coins", [])]
                if item.get("id") == coin_id
            ),
            None,
        )
        await db.commit()
        if coin_row is None:
            return None
        return coin_row

    def user_has_pro_access(self, user: User | None) -> bool:
        if user is None:
            return False
        paid_tier = self._daily_rewards.paid_membership_tier_user(user)
        if paid_tier != "free":
            return True
        expires_at = user.reward_pro_expires_at
        if expires_at is None:
            return bool(user.is_pro and paid_tier != "free")
        return expires_at >= _utc_now()

    async def _load_active_target_coin_ids(self, db: AsyncSession) -> list[str]:
        rows = (
            await db.scalars(
                select(UserTargetAlert.coin_id)
                .where(UserTargetAlert.is_active.is_(True))
                .distinct()
            )
        ).all()
        return [str(item).strip() for item in rows if str(item).strip()]

    async def _fetch_market_rows(
        self,
        *,
        limit: int | None = None,
        ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "vs_currency": "usd",
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
        if ids:
            params["ids"] = ",".join(sorted({item.strip() for item in ids if item.strip()}))
            if not params["ids"]:
                return []
        else:
            params.update(
                {
                    "order": "market_cap_desc",
                    "per_page": max(1, min(limit or self._tracked_limit, 50)),
                    "page": 1,
                }
            )

        data = await self._fetch_json(
            "https://api.coingecko.com/api/v3/coins/markets",
            params=params,
        )
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    async def _search_market_symbol(self, symbol: str) -> list[dict[str, Any]]:
        data = await self._fetch_json(
            "https://api.coingecko.com/api/v3/search",
            params={"query": symbol},
        )
        if not isinstance(data, dict):
            return []
        coins = data.get("coins")
        if not isinstance(coins, list):
            return []
        normalized = symbol.upper()
        exact = [
            item
            for item in coins
            if isinstance(item, dict)
            and str(item.get("symbol") or "").strip().upper() == normalized
        ]
        if exact:
            return exact
        return [item for item in coins if isinstance(item, dict)]

    async def _fetch_fear_greed_index(self) -> dict[str, Any] | None:
        data = await self._fetch_json("https://api.alternative.me/fng/", params={"limit": 1})
        if not isinstance(data, dict):
            return None
        rows = data.get("data")
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0] if isinstance(rows[0], dict) else None
        if row is None:
            return None
        return {
            "value": int(_to_float(row.get("value"), 0)),
            "classification": str(row.get("value_classification") or "").strip(),
            "timestamp": str(row.get("timestamp") or "").strip(),
        }

    async def _fetch_json(self, url: str, *, params: dict[str, Any]) -> Any:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                headers={"accept": "application/json"},
            ) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except Exception:
            logger.debug("market_http_fetch_failed", exc_info=True, extra={"url": url})
            return None

    async def _persist_snapshot(
        self,
        db: AsyncSession,
        *,
        market_rows: list[dict[str, Any]],
        top_market_ids: list[str],
        fear_greed: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = _utc_now()
        coin_rows = []
        price_rows = []
        snapshot_items = []
        for row in market_rows:
            coin_id = str(row.get("id") or "").strip()
            if not coin_id:
                continue
            price = _to_float(row.get("current_price"), 0)
            change_24h = _to_float(row.get("price_change_percentage_24h"), 0)
            market_cap = _to_float(row.get("market_cap"), 0)
            volume = _to_float(row.get("total_volume"), 0)
            coin_rows.append(
                {
                    "id": coin_id,
                    "symbol": str(row.get("symbol") or "").strip().upper(),
                    "name": str(row.get("name") or "").strip() or coin_id,
                    "image_url": str(row.get("image") or "").strip() or None,
                    "market_cap_rank": int(_to_float(row.get("market_cap_rank"), 0)) or None,
                    "is_active": True,
                    "updated_at": now,
                }
            )
            price_rows.append(
                {
                    "coin_id": coin_id,
                    "price_usd": price,
                    "change_24h": change_24h,
                    "quote_volume_usd": volume,
                    "market_cap_usd": market_cap,
                    "source": "coingecko",
                    "captured_at": now,
                }
            )
            snapshot_items.append(
                {
                    "id": coin_id,
                    "symbol": str(row.get("symbol") or "").strip().upper(),
                    "name": str(row.get("name") or "").strip() or coin_id,
                    "image": str(row.get("image") or "").strip(),
                    "price": price,
                    "change24h": change_24h,
                    "marketCap": market_cap,
                    "totalVolume": volume,
                    "rank": int(_to_float(row.get("market_cap_rank"), 0)),
                    "isTopTracked": coin_id in top_market_ids,
                }
            )

        if coin_rows:
            statement = insert(MarketCoin).values(coin_rows)
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=[MarketCoin.id],
                    set_={
                        "symbol": statement.excluded.symbol,
                        "name": statement.excluded.name,
                        "image_url": statement.excluded.image_url,
                        "market_cap_rank": statement.excluded.market_cap_rank,
                        "is_active": statement.excluded.is_active,
                        "updated_at": now,
                    },
                )
            )
        if price_rows:
            await db.execute(insert(MarketPricePoint).values(price_rows))

        top_snapshot = [item for item in snapshot_items if item["isTopTracked"]]
        top_snapshot.sort(key=lambda item: (item["rank"] <= 0, item["rank"], item["name"]))
        return {
            "updatedAt": now.isoformat(),
            "trackedLimit": self._tracked_limit,
            "coins": top_snapshot,
            "trackedCoins": snapshot_items,
            "fearGreed": fear_greed,
            "stats": {
                "stories": len(top_snapshot),
                "sources": len({item["symbol"] for item in top_snapshot}),
                "latestAgeSeconds": 0 if top_snapshot else None,
            },
        }

    async def _build_snapshot_from_db(self, db: AsyncSession) -> dict[str, Any] | None:
        subquery: Select[Any] = (
            select(
                MarketPricePoint.coin_id,
                func.max(MarketPricePoint.captured_at).label("max_captured_at"),
            )
            .group_by(MarketPricePoint.coin_id)
            .subquery()
        )
        rows = (
            await db.execute(
                select(MarketCoin, MarketPricePoint)
                .join(subquery, subquery.c.coin_id == MarketCoin.id)
                .join(
                    MarketPricePoint,
                    (MarketPricePoint.coin_id == subquery.c.coin_id)
                    & (MarketPricePoint.captured_at == subquery.c.max_captured_at),
                )
                .order_by(MarketCoin.market_cap_rank.asc().nullslast(), MarketCoin.name.asc())
                .limit(self._tracked_limit)
            )
        ).all()
        if not rows:
            return None

        last_updated_at = max(price.captured_at for _, price in rows)
        tracked_items = [
            {
                "id": coin.id,
                "symbol": coin.symbol.upper(),
                "name": coin.name,
                "image": coin.image_url or "",
                "price": float(price.price_usd),
                "change24h": float(price.change_24h),
                "marketCap": float(price.market_cap_usd),
                "totalVolume": float(price.quote_volume_usd),
                "rank": int(coin.market_cap_rank or 0),
                "isTopTracked": True,
            }
            for coin, price in rows
        ]
        return {
            "updatedAt": last_updated_at.isoformat(),
            "trackedLimit": self._tracked_limit,
            "coins": tracked_items,
            "trackedCoins": tracked_items,
            "fearGreed": None,
            "stats": {
                "stories": len(rows),
                "sources": len({coin.symbol.upper() for coin, _ in rows}),
                "latestAgeSeconds": max(0, int((_utc_now() - last_updated_at).total_seconds())),
            },
        }

    def _empty_snapshot(self) -> dict[str, Any]:
        return {
            "updatedAt": _utc_now().isoformat(),
            "trackedLimit": self._tracked_limit,
            "coins": [],
            "trackedCoins": [],
            "fearGreed": None,
            "stats": {"stories": 0, "sources": 0, "latestAgeSeconds": None},
        }

    async def _emit_market_alerts(self, db: AsyncSession, snapshot: dict[str, Any]) -> None:
        coins = [item for item in snapshot.get("coins", []) if isinstance(item, dict)]
        positive = sorted(
            [item for item in coins if _to_float(item.get("change24h"), 0) >= 5.0],
            key=lambda item: _to_float(item.get("change24h"), 0),
            reverse=True,
        )
        negative = sorted(
            [item for item in coins if _to_float(item.get("change24h"), 0) <= -5.0],
            key=lambda item: _to_float(item.get("change24h"), 0),
        )
        if positive:
            await self._emit_global_event(
                db,
                kind="pump_alert",
                coin=positive[0],
                title=f"{positive[0]['symbol']} pump alert",
                body=f"{positive[0]['symbol']} gained {positive[0]['change24h']:.2f}% in 24h.",
                dedupe_key=f"pump:{positive[0]['id']}",
                cool_down=timedelta(hours=_MARKET_ALERT_DEDUPE_HOURS),
            )
        if negative:
            await self._emit_global_event(
                db,
                kind="panic_alert",
                coin=negative[0],
                title=f"{negative[0]['symbol']} panic alert",
                body=f"{negative[0]['symbol']} dropped {negative[0]['change24h']:.2f}% in 24h.",
                dedupe_key=f"panic:{negative[0]['id']}",
                cool_down=timedelta(hours=_MARKET_ALERT_DEDUPE_HOURS),
            )
        volatility = await self._find_sharp_move_coin(db, coins)
        if volatility is not None:
            move = _to_float(volatility["movePercent"], 0)
            direction = "up" if move >= 0 else "down"
            await self._emit_global_event(
                db,
                kind="sharp_move_alert",
                coin=volatility,
                title=f"{volatility['symbol']} moved fast",
                body=f"{volatility['symbol']} moved {move:+.2f}% in a short window.",
                dedupe_key=f"sharp:{volatility['id']}:{direction}",
                cool_down=timedelta(hours=3),
            )

    async def _emit_target_alerts(self, db: AsyncSession, snapshot: dict[str, Any]) -> None:
        tracked_items = [
            *snapshot.get("trackedCoins", []),
            *snapshot.get("coins", []),
        ]
        coin_map = {
            str(item.get("id") or "").strip(): item
            for item in tracked_items
            if isinstance(item, dict)
        }
        rows = (
            await db.execute(
                select(UserTargetAlert, User)
                .join(User, User.id == UserTargetAlert.user_id)
                .where(UserTargetAlert.is_active.is_(True))
            )
        ).all()
        now = _utc_now()
        for alert, user in rows:
            coin = coin_map.get(alert.coin_id)
            if coin is None:
                continue
            if not self.user_has_pro_access(user):
                continue
            prefs = SmartSignalPreferences.from_user_settings(user.settings_json)
            if not (prefs.enabled and prefs.price_target_enabled):
                continue
            current_price = _to_float(coin.get("price"), 0)
            if current_price <= 0:
                continue
            reached = (
                current_price >= float(alert.target_price)
                if alert.direction == "above"
                else current_price <= float(alert.target_price)
            )
            if not reached:
                continue
            if alert.last_triggered_at is not None and (
                now - alert.last_triggered_at
            ) < timedelta(hours=_TARGET_TRIGGER_COOLDOWN_HOURS):
                continue

            payload = self._coin_payload(
                coin,
                kind="target_alert",
                extra={
                    "targetPrice": float(alert.target_price),
                    "direction": alert.direction,
                },
            )
            await self._notification_service.create_notification(
                db,
                user_id=user.id,
                kind="target_alert",
                title=f"{coin['symbol']} reached ${float(alert.target_price):,.4f}",
                body=f"{coin['symbol']} is now trading at ${current_price:,.4f}.",
                extra_payload=payload,
            )
            alert.is_active = False
            alert.last_triggered_at = now

    async def _emit_pro_signals(self, db: AsyncSession, snapshot: dict[str, Any]) -> None:
        users = (
            await db.scalars(
                select(User).where(
                    (User.is_pro.is_(True))
                    | (User.membership_tier != "free")
                    | (User.reward_pro_expires_at.is_not(None))
                )
            )
        ).all()
        pro_users = [user for user in users if self.user_has_pro_access(user)]
        if not pro_users:
            return

        coins = [item for item in snapshot.get("coins", []) if isinstance(item, dict)]
        if not coins:
            return

        volume_signal = await self._find_volume_spike_coin(db, coins)
        whale_signal = await self._find_whale_coin(db, coins)
        fear_greed = snapshot.get("fearGreed")
        fear_value = _to_float((fear_greed or {}).get("value"), -1)
        fear_extreme = fear_value >= 80 or (fear_value >= 0 and fear_value <= 20)

        if volume_signal is not None:
            ratio = _to_float(volume_signal.get("volumeRatio"), 0)
            emitted = await self._record_event(
                db,
                kind="smart_volume_signal",
                coin_id=volume_signal["id"],
                title=f"{volume_signal['symbol']} volume spike",
                body=f"Volume expanded {ratio:.2f}x versus recent flow.",
                payload=self._coin_payload(
                    volume_signal,
                    kind="smart_signal",
                    extra={"signalType": "volume_spike", "volumeRatio": ratio},
                ),
                dedupe_key=f"smart:volume:{volume_signal['id']}",
                cool_down=timedelta(hours=_PRO_SIGNAL_DEDUPE_HOURS),
            )
            if emitted:
                await self._notify_pro_users(
                    db,
                    users=pro_users,
                    title=f"{volume_signal['symbol']} volume spike",
                    body=f"Volume expanded {ratio:.2f}x versus recent flow.",
                    signal_payload=self._coin_payload(
                        volume_signal,
                        kind="smart_signal",
                        extra={
                            "signalType": "volume_spike",
                            "volumeRatio": ratio,
                            "confidence": self._signal_confidence(
                                change_24h=_to_float(volume_signal.get("change24h"), 0),
                                volume_ratio=ratio,
                            ),
                        },
                    ),
                    predicate=lambda prefs: prefs.enabled and prefs.volume_spike_enabled and ratio >= prefs.volume_multiplier,
                )

        if whale_signal is not None:
            ratio = _to_float(whale_signal.get("volumeRatio"), 0)
            emitted = await self._record_event(
                db,
                kind="whale_activity_alert",
                coin_id=whale_signal["id"],
                title=f"{whale_signal['symbol']} whale activity",
                body=f"Abnormal high-volume flow detected around {whale_signal['symbol']}.",
                payload=self._coin_payload(
                    whale_signal,
                    kind="smart_signal",
                    extra={"signalType": "whale_activity", "volumeRatio": ratio},
                ),
                dedupe_key=f"smart:whale:{whale_signal['id']}",
                cool_down=timedelta(hours=_PRO_SIGNAL_DEDUPE_HOURS),
            )
            if emitted:
                await self._notify_pro_users(
                    db,
                    users=pro_users,
                    title=f"{whale_signal['symbol']} whale activity",
                    body=f"Abnormal high-volume flow detected around {whale_signal['symbol']}.",
                    signal_payload=self._coin_payload(
                        whale_signal,
                        kind="smart_signal",
                        extra={
                            "signalType": "whale_activity",
                            "volumeRatio": ratio,
                            "confidence": self._signal_confidence(
                                change_24h=_to_float(whale_signal.get("change24h"), 0),
                                volume_ratio=ratio,
                            ),
                        },
                    ),
                    predicate=lambda prefs: prefs.enabled and prefs.whale_activity_enabled,
                )

        if fear_extreme:
            classification = str((fear_greed or {}).get("classification") or "").strip() or "Extreme"
            emitted = await self._record_event(
                db,
                kind="fear_greed_extreme",
                coin_id=None,
                title="Fear & Greed extreme",
                body=f"Market sentiment is at {int(fear_value)} ({classification}).",
                payload={
                    "kind": "smart_signal",
                    "signalType": "fear_greed_extreme",
                    "fearGreedValue": int(fear_value),
                    "fearGreedClassification": classification,
                    "targetRoute": "/market/fear-greed",
                },
                dedupe_key=f"smart:fng:{int(fear_value >= 80)}",
                cool_down=timedelta(hours=12),
            )
            if emitted:
                await self._notify_pro_users(
                    db,
                    users=pro_users,
                    title="Fear & Greed extreme",
                    body=f"Market sentiment is at {int(fear_value)} ({classification}).",
                    signal_payload={
                        "kind": "smart_signal",
                        "signalType": "fear_greed_extreme",
                        "fearGreedValue": int(fear_value),
                        "fearGreedClassification": classification,
                        "targetRoute": "/market/fear-greed",
                        "confidence": min(99, max(60, int(abs(fear_value - 50) * 1.6))),
                    },
                    predicate=lambda prefs: prefs.enabled and prefs.fear_greed_extreme_enabled,
                )

    async def _notify_pro_users(
        self,
        db: AsyncSession,
        *,
        users: list[User],
        title: str,
        body: str,
        signal_payload: dict[str, Any],
        predicate,
    ) -> None:
        for user in users:
            prefs = SmartSignalPreferences.from_user_settings(user.settings_json)
            if not predicate(prefs):
                continue
            await self._notification_service.create_notification(
                db,
                user_id=user.id,
                kind="smart_signal",
                title=title,
                body=body,
                extra_payload=signal_payload,
            )

    async def _emit_global_event(
        self,
        db: AsyncSession,
        *,
        kind: str,
        coin: dict[str, Any],
        title: str,
        body: str,
        dedupe_key: str,
        cool_down: timedelta,
    ) -> None:
        payload = self._coin_payload(coin, kind="market_alert")
        emitted = await self._record_event(
            db,
            kind=kind,
            coin_id=str(coin.get("id") or "").strip() or None,
            title=title,
            body=body,
            payload=payload,
            dedupe_key=dedupe_key,
            cool_down=cool_down,
        )
        if not emitted:
            return
        await self._notification_service.create_broadcast_notification(
            db,
            kind="market_alert",
            title=title,
            body=body,
            extra_payload=payload,
        )

    async def _record_event(
        self,
        db: AsyncSession,
        *,
        kind: str,
        coin_id: str | None,
        title: str,
        body: str,
        payload: dict[str, Any],
        dedupe_key: str,
        cool_down: timedelta,
    ) -> bool:
        threshold = _utc_now() - cool_down
        recent = await db.scalar(
            select(MarketAlertEvent.id)
            .where(
                MarketAlertEvent.dedupe_key == dedupe_key,
                MarketAlertEvent.created_at >= threshold,
            )
            .limit(1)
        )
        if recent is not None:
            return False
        db.add(
            MarketAlertEvent(
                kind=kind,
                coin_id=coin_id,
                dedupe_key=dedupe_key,
                title=title,
                body=body,
                payload_json=payload,
            )
        )
        await db.flush()
        return True

    async def _find_sharp_move_coin(
        self,
        db: AsyncSession,
        coins: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_abs_move = 0.0
        for coin in coins[: min(10, len(coins))]:
            points = (
                await db.scalars(
                    select(MarketPricePoint)
                    .where(MarketPricePoint.coin_id == coin["id"])
                    .order_by(desc(MarketPricePoint.captured_at))
                    .limit(4)
                )
            ).all()
            if len(points) < 2:
                continue
            latest = points[0]
            reference = points[-1]
            if reference.price_usd <= 0:
                continue
            move = ((latest.price_usd - reference.price_usd) / reference.price_usd) * 100
            if abs(move) < 3.0:
                continue
            if abs(move) > best_abs_move:
                best_abs_move = abs(move)
                best = {**coin, "movePercent": move}
        return best

    async def _find_volume_spike_coin(
        self,
        db: AsyncSession,
        coins: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_ratio = 0.0
        for coin in coins[: min(12, len(coins))]:
            points = (
                await db.scalars(
                    select(MarketPricePoint)
                    .where(MarketPricePoint.coin_id == coin["id"])
                    .order_by(desc(MarketPricePoint.captured_at))
                    .limit(8)
                )
            ).all()
            if len(points) < 4:
                continue
            current = float(points[0].quote_volume_usd or 0)
            baseline = [float(point.quote_volume_usd or 0) for point in points[1:] if point.quote_volume_usd]
            if current <= 0 or not baseline:
                continue
            average = sum(baseline) / len(baseline)
            if average <= 0:
                continue
            ratio = current / average
            if ratio >= 1.8 and ratio > best_ratio:
                best_ratio = ratio
                best = {**coin, "volumeRatio": ratio}
        return best

    async def _find_whale_coin(
        self,
        db: AsyncSession,
        coins: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_ratio = 0.0
        for coin in coins[: min(12, len(coins))]:
            market_cap = _to_float(coin.get("marketCap"), 0)
            if market_cap < 1_000_000_000:
                continue
            volume_candidate = await self._find_volume_spike_coin(db, [coin])
            if volume_candidate is None:
                continue
            ratio = _to_float(volume_candidate.get("volumeRatio"), 0)
            if ratio >= 2.5 and ratio > best_ratio:
                best_ratio = ratio
                best = volume_candidate
        return best

    def _coin_payload(
        self,
        coin: dict[str, Any],
        *,
        kind: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "kind": kind,
            "coinId": str(coin.get("id") or "").strip(),
            "coinSymbol": str(coin.get("symbol") or "").strip().upper(),
            "coinName": str(coin.get("name") or "").strip(),
            "imageUrl": str(coin.get("image") or "").strip(),
            "price": _to_float(coin.get("price"), 0),
            "change24h": _to_float(coin.get("change24h"), 0),
            "targetRoute": "/market",
        }
        if extra:
            payload.update({key: value for key, value in extra.items() if value is not None})
        return payload

    def _signal_confidence(self, *, change_24h: float, volume_ratio: float) -> int:
        score = 55 + min(abs(change_24h) * 4.0, 20) + min(max(volume_ratio - 1.0, 0) * 12.0, 24)
        return max(55, min(98, int(round(score))))

    async def _prune_old_price_points(self, db: AsyncSession) -> None:
        await db.execute(
            delete(MarketPricePoint).where(
                MarketPricePoint.captured_at < (_utc_now() - timedelta(days=14))
            )
        )


def _to_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
