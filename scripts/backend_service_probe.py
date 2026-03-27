from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/w8AAgMBApN9lXcAAAAASUVORK5CYII="
)


@dataclass(slots=True)
class RequestResult:
    name: str
    method: str
    path: str
    status_code: int
    duration_ms: float
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class BurstResult:
    name: str
    requests: int
    success_count: int
    failure_count: int
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    statuses: dict[str, int] = field(default_factory=dict)


class ProbeFailure(RuntimeError):
    pass


class BackendServiceProbe:
    def __init__(
        self,
        *,
        api_base_url: str,
        admin_username: str | None,
        admin_password: str | None,
        summary_file: Path | None,
        timeout_seconds: float,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._origin = self._api_base_url.removesuffix("/api/v1")
        self._admin_username = (admin_username or "").strip() or None
        self._admin_password = (admin_password or "").strip() or None
        self._summary_file = summary_file
        self._timeout = httpx.Timeout(timeout_seconds)
        self._results: list[RequestResult] = []
        self._bursts: list[BurstResult] = []
        self._client: httpx.AsyncClient | None = None
        self._user_a: dict[str, Any] | None = None
        self._user_b: dict[str, Any] | None = None

    async def run(self) -> int:
        async with httpx.AsyncClient(
            base_url=self._api_base_url,
            timeout=self._timeout,
        ) as client:
            self._client = client
            await self._run_public_flow()
            await self._run_auth_flow()
            await self._run_me_flow()
            await self._run_signals_flow()
            await self._run_community_flow()
            await self._run_admin_flow()
            await self._run_bursts()

        self._print_summary()
        self._write_summary()
        failures = [result for result in self._results if not result.ok]
        burst_failures = [burst for burst in self._bursts if burst.failure_count > 0]
        return 1 if failures or burst_failures else 0

    async def _run_public_flow(self) -> None:
        await self._request("health", "GET", "/health")
        await self._request("news_feed_en", "GET", "/news/feed", params={"limit": 18, "lang": "en"})
        await self._request("news_list_en", "GET", "/news", params={"page": 1, "pageSize": 30, "lang": "en", "sort": "latest"})
        await self._request("home_overview_en", "GET", "/home/overview", params={"news_limit": 18, "lang": "en"})
        await self._request("learning_videos", "GET", "/learning/videos")

    async def _run_auth_flow(self) -> None:
        stamp = int(time.time())
        self._user_a = await self._authenticate_user(
            email=f"probe-a-{stamp}@xrhodl.local",
            display_name="Probe User A",
        )
        self._user_b = await self._authenticate_user(
            email=f"probe-b-{stamp}@xrhodl.local",
            display_name="Probe User B",
        )
        await self._request(
            "auth_session_a",
            "GET",
            "/auth/session",
            headers=self._auth_headers(self._user_a),
        )

    async def _run_me_flow(self) -> None:
        user = self._require_user(self._user_a)
        headers = self._auth_headers(user)
        await self._request("me_bootstrap", "GET", "/me/bootstrap", headers=headers)
        await self._request("me_daily_reward", "GET", "/me/daily-reward", headers=headers)
        offers = await self._request("me_membership_offers", "GET", "/me/membership/offers", headers=headers)
        offer_item = None
        if offers.ok:
            payload = json.loads(offers.detail) if offers.detail else {}
            offer_item = ((payload.get("proPlans") or []) + (payload.get("legendPlans") or []))[0]
        if offer_item:
            await self._request(
                "me_purchase_intent",
                "POST",
                "/me/membership/purchase-intent",
                headers=headers,
                json_body={"tier": offer_item["tier"], "planCode": offer_item["code"]},
            )
        await self._request(
            "me_update_settings",
            "PUT",
            "/me/settings",
            headers=headers,
            json_body={"locale": "uz", "theme": "light", "notifications": {"news": True}},
        )
        await self._request(
            "me_update_watchlist",
            "PUT",
            "/me/watchlist",
            headers=headers,
            json_body={"symbols": ["BTC", "ETH", "SOL"]},
        )
        await self._request(
            "me_notifications",
            "GET",
            "/me/notifications",
            headers=headers,
            params={"limit": 12, "unread_only": "true"},
        )
        await self._request(
            "me_push_register",
            "POST",
            "/me/push-token",
            headers=headers,
            expected_statuses={204},
            json_body={"token": f"probe-token-{int(time.time())}", "platform": "android"},
        )

    async def _run_signals_flow(self) -> None:
        user = self._require_user(self._user_a)
        headers = self._auth_headers(user)
        await self._request("signals_bootstrap", "GET", "/me/signals/bootstrap", headers=headers)
        await self._request("signals_targets_list", "GET", "/me/signals/targets", headers=headers)
        created = await self._request(
            "signals_target_create",
            "POST",
            "/me/signals/targets",
            headers=headers,
            json_body={"symbol": "BTC", "targetPrice": 50000},
        )
        target_id = None
        if created.ok and created.detail:
            payload = json.loads(created.detail)
            target_id = ((payload.get("item") or {}).get("id") or "").strip() or None
        await self._request(
            "signals_refresh",
            "POST",
            "/me/signals/refresh",
            headers=headers,
        )
        if target_id:
            await self._request(
                "signals_target_delete",
                "DELETE",
                f"/me/signals/targets/{target_id}",
                headers=headers,
                expected_statuses={204},
            )

    async def _run_community_flow(self) -> None:
        user_a = self._require_user(self._user_a)
        user_b = self._require_user(self._user_b)
        headers_a = self._auth_headers(user_a)
        headers_b = self._auth_headers(user_b)

        avatar = await self._request(
            "community_media_avatar",
            "POST",
            "/community/media/avatar",
            headers=headers_a,
            json_body={
                "fileName": "probe-avatar.png",
                "contentBase64": PNG_1X1_BASE64,
            },
        )
        avatar_url = None
        if avatar.ok and avatar.detail:
            avatar_url = (json.loads(avatar.detail).get("url") or "").strip() or None

        profile_a = {
            "displayName": "Probe User A",
            "username": f"probea{int(time.time())}",
            "avatarUrl": avatar_url,
            "biography": "backend service probe",
            "website": "https://xrhodl.local/probe",
            "socialAccounts": {"x": "probea"},
            "publicWatchlistSymbols": ["BTC", "ETH"],
            "blockedAccountIds": [],
        }
        profile_b = {
            "displayName": "Probe User B",
            "username": f"probeb{int(time.time())}",
            "avatarUrl": avatar_url,
            "biography": "backend service probe",
            "website": "https://xrhodl.local/probe",
            "socialAccounts": {"x": "probeb"},
            "publicWatchlistSymbols": ["SOL"],
            "blockedAccountIds": [],
        }
        await self._request("community_profile_sync_a", "POST", "/community/profiles/sync", headers=headers_a, json_body=profile_a)
        await self._request("community_profile_sync_b", "POST", "/community/profiles/sync", headers=headers_b, json_body=profile_b)
        await self._request("community_profile_a", "GET", f"/community/profiles/{user_a['userId']}", headers=headers_a)
        await self._request("community_profiles_recent", "GET", "/community/profiles/recent", params={"limit": 20})

        await self._request(
            "community_follow_b",
            "POST",
            f"/community/profiles/{user_b['userId']}/follow",
            headers=headers_a,
            expected_statuses={204},
        )
        await self._request(
            "community_follow_state",
            "GET",
            f"/community/profiles/{user_b['userId']}/follow-state",
            headers=headers_a,
        )
        await self._request(
            "community_followers_count_b",
            "GET",
            f"/community/profiles/{user_b['userId']}/followers/count",
        )

        post_image = await self._request(
            "community_media_post_image",
            "POST",
            "/community/media/post-image",
            headers=headers_a,
            json_body={
                "fileName": "probe-post.png",
                "contentBase64": PNG_1X1_BASE64,
            },
        )
        image_url = None
        if post_image.ok and post_image.detail:
            image_url = (json.loads(post_image.detail).get("url") or "").strip() or None

        post_created = await self._request(
            "community_post_create",
            "POST",
            "/community/posts",
            headers=headers_a,
            expected_statuses={201},
            json_body={
                "content": "Probe post for backend service validation.",
                "symbols": ["BTC", "ETH"],
                "marketBias": "bullish",
                "imageUrl": image_url,
            },
        )
        post_id = None
        if post_created.ok and post_created.detail:
            post_id = (json.loads(post_created.detail).get("id") or "").strip() or None

        await self._request("community_posts_list", "GET", "/community/posts", params={"limit": 15})
        await self._request("community_feed", "GET", "/community/feed", headers=headers_a, params={"limit": 20})

        if post_id:
            await self._request("community_post_detail", "GET", f"/community/posts/{post_id}")
            await self._request(
                "community_post_comment",
                "POST",
                f"/community/posts/{post_id}/comments",
                headers=headers_b,
                expected_statuses={201},
                json_body={"content": "Probe comment."},
            )
            await self._request(
                "community_post_reaction",
                "POST",
                f"/community/posts/{post_id}/reactions",
                headers=headers_b,
                json_body={"reactionKey": "rocket", "currentReaction": None},
            )
            await self._request(
                "community_post_reaction_get",
                "GET",
                f"/community/posts/{post_id}/reaction",
                headers=headers_b,
            )
            await self._request(
                "community_post_comments_get",
                "GET",
                f"/community/posts/{post_id}/comments",
                params={"limit": 20},
            )

        chat_created = await self._request(
            "community_chat_send",
            "POST",
            f"/community/chats/with/{user_b['userId']}/messages",
            headers=headers_a,
            expected_statuses={201},
            json_body={"body": "Probe direct message", "messageType": "text"},
        )
        await self._request("community_chats_list_a", "GET", "/community/chats", headers=headers_a, params={"limit": 20})
        await self._request(
            "community_chat_with_peer_a",
            "GET",
            f"/community/chats/with/{user_b['userId']}",
            headers=headers_a,
        )
        await self._request(
            "community_chat_messages_a",
            "GET",
            f"/community/chats/with/{user_b['userId']}/messages",
            headers=headers_a,
            params={"limit": 20},
        )
        await self._request(
            "community_chat_unread_b",
            "GET",
            "/community/chats/unread-count",
            headers=headers_b,
        )
        await self._request(
            "community_chat_read_b",
            "POST",
            f"/community/chats/with/{user_a['userId']}/read",
            headers=headers_b,
            expected_statuses={204},
        )

        if post_id:
            await self._request(
                "community_post_delete",
                "DELETE",
                f"/community/posts/{post_id}",
                headers=headers_a,
                expected_statuses={204},
            )
        await self._request(
            "community_unfollow_b",
            "DELETE",
            f"/community/profiles/{user_b['userId']}/follow",
            headers=headers_a,
            expected_statuses={204},
        )

    async def _run_admin_flow(self) -> None:
        if not self._admin_username or not self._admin_password:
            return
        auth = (self._admin_username, self._admin_password)
        await self._request("admin_stats", "GET", "/admin/stats", auth=auth)
        await self._request("admin_overview", "GET", "/admin/overview", auth=auth)
        await self._request("admin_users", "GET", "/admin/users", auth=auth, params={"limit": 20, "offset": 0})

    async def _run_bursts(self) -> None:
        user = self._require_user(self._user_a)
        auth_headers = self._auth_headers(user)

        await self._burst(
            "burst_news_feed_en",
            requests=20,
            concurrency=10,
            factory=lambda: self._raw_request("GET", "/news/feed", params={"limit": 18, "lang": "en"}),
        )
        await self._burst(
            "burst_news_list_en",
            requests=20,
            concurrency=10,
            factory=lambda: self._raw_request("GET", "/news", params={"page": 1, "pageSize": 30, "lang": "en", "sort": "latest"}),
        )
        await self._burst(
            "burst_home_overview_en",
            requests=20,
            concurrency=10,
            factory=lambda: self._raw_request("GET", "/home/overview", params={"news_limit": 18, "lang": "en"}),
        )
        await self._burst(
            "burst_me_bootstrap",
            requests=20,
            concurrency=10,
            factory=lambda: self._raw_request("GET", "/me/bootstrap", headers=auth_headers),
        )
        await self._burst(
            "burst_community_posts",
            requests=20,
            concurrency=10,
            factory=lambda: self._raw_request("GET", "/community/posts", params={"limit": 15}),
        )
        await self._burst(
            "burst_community_profiles_recent",
            requests=20,
            concurrency=10,
            factory=lambda: self._raw_request("GET", "/community/profiles/recent", params={"limit": 20}),
        )

    async def _authenticate_user(self, *, email: str, display_name: str) -> dict[str, Any]:
        response = await self._request(
            f"auth_google_{display_name.replace(' ', '_').lower()}",
            "POST",
            "/auth/google",
            json_body={
                "idToken": f"probe-token::{email}",
                "email": email,
                "displayName": display_name,
                "photoUrl": "https://example.com/avatar.png",
            },
        )
        if not response.ok or not response.detail:
            raise ProbeFailure(f"Authentication failed for {email}")
        payload = json.loads(response.detail)
        user = payload.get("user") or {}
        return {
            "accessToken": payload["accessToken"],
            "refreshToken": payload["refreshToken"],
            "userId": user["id"],
            "email": email,
        }

    def _auth_headers(self, user: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {user['accessToken']}"}

    def _require_user(self, user: dict[str, Any] | None) -> dict[str, Any]:
        if user is None:
            raise ProbeFailure("Probe user is not initialized.")
        return user

    async def _request(
        self,
        name: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        expected_statuses: set[int] | None = None,
    ) -> RequestResult:
        started_at = time.perf_counter()
        response: httpx.Response | None = None
        error_detail = ""
        try:
            response = await self._raw_request(
                method,
                path,
                headers=headers,
                params=params,
                json_body=json_body,
                auth=auth,
            )
            status_code = response.status_code
            ok = status_code in (expected_statuses or {200})
            if not ok:
                error_detail = _truncate(response.text)
            else:
                error_detail = response.text
        except Exception as exc:  # pragma: no cover - smoke harness
            status_code = 0
            ok = False
            error_detail = f"{type(exc).__name__}: {exc}"
        duration_ms = (time.perf_counter() - started_at) * 1000
        result = RequestResult(
            name=name,
            method=method.upper(),
            path=path,
            status_code=status_code,
            duration_ms=round(duration_ms, 2),
            ok=ok,
            detail=error_detail,
        )
        self._results.append(result)
        return result

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> httpx.Response:
        if self._client is None:
            raise ProbeFailure("HTTP client is not initialized.")
        return await self._client.request(
            method=method,
            url=path,
            headers=headers,
            params=params,
            json=json_body,
            auth=auth,
        )

    async def _burst(
        self,
        name: str,
        *,
        requests: int,
        concurrency: int,
        factory: Callable[[], Awaitable[httpx.Response]],
    ) -> None:
        semaphore = asyncio.Semaphore(max(1, concurrency))
        durations: list[float] = []
        statuses: dict[str, int] = {}
        failure_count = 0

        async def _run_one() -> None:
            nonlocal failure_count
            async with semaphore:
                started_at = time.perf_counter()
                try:
                    response = await factory()
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    durations.append(duration_ms)
                    key = str(response.status_code)
                    statuses[key] = statuses.get(key, 0) + 1
                    if response.status_code >= 400:
                        failure_count += 1
                except Exception:
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    durations.append(duration_ms)
                    statuses["error"] = statuses.get("error", 0) + 1
                    failure_count += 1

        await asyncio.gather(*(_run_one() for _ in range(requests)))
        durations_sorted = sorted(durations)
        self._bursts.append(
            BurstResult(
                name=name,
                requests=requests,
                success_count=requests - failure_count,
                failure_count=failure_count,
                p50_ms=_percentile(durations_sorted, 0.50),
                p95_ms=_percentile(durations_sorted, 0.95),
                max_ms=round(max(durations_sorted), 2) if durations_sorted else None,
                statuses=statuses,
            )
        )

    def _print_summary(self) -> None:
        print("\n=== Backend Service Probe ===")
        for result in self._results:
            marker = "OK" if result.ok else "FAIL"
            print(
                f"[{marker}] {result.name:<28} {result.status_code:<4} "
                f"{result.duration_ms:>8.2f} ms  {result.method} {result.path}"
            )
        print("\n=== Burst Summary ===")
        for burst in self._bursts:
            print(
                f"[BURST] {burst.name:<28} ok={burst.success_count}/{burst.requests} "
                f"fail={burst.failure_count} p50={burst.p50_ms} p95={burst.p95_ms} max={burst.max_ms}"
            )

    def _write_summary(self) -> None:
        if self._summary_file is None:
            return
        payload = {
            "apiBaseUrl": self._api_base_url,
            "results": [asdict(item) for item in self._results],
            "bursts": [asdict(item) for item in self._bursts],
        }
        self._summary_file.parent.mkdir(parents=True, exist_ok=True)
        self._summary_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nSummary written to {self._summary_file}")


def _percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * ratio))))
    return round(values[index], 2)


def _truncate(value: str, *, max_length: int = 800) -> str:
    normalized = (value or "").strip()
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3] + "..."


async def _main_async(args: argparse.Namespace) -> int:
    probe = BackendServiceProbe(
        api_base_url=args.api_base_url,
        admin_username=args.admin_username,
        admin_password=args.admin_password,
        summary_file=Path(args.summary_file) if args.summary_file else None,
        timeout_seconds=max(5.0, float(args.timeout_seconds)),
    )
    return await probe.run()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run staged smoke and microburst probes against the XR backend.")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--admin-username", default="")
    parser.add_argument("--admin-password", default="")
    parser.add_argument("--summary-file", default="")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
