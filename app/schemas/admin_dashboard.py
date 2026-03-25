from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class AdminTimePoint(BaseModel):
    date: str
    value: int


class AdminWeeklyEngagementItem(BaseModel):
    day: str
    posts: int = 0
    comments: int = 0
    messages: int = 0


class AdminSentimentItem(BaseModel):
    name: str
    value: int
    color: str


class AdminMessageTypeItem(BaseModel):
    type: str
    count: int
    color: str


class AdminTopSymbolItem(BaseModel):
    symbol: str
    posts: int
    views: int
    reactions: int
    color: str = "#3b82f6"


class AdminNamedValueItem(BaseModel):
    name: str
    value: int
    color: str = "#64748b"


class AdminKeyMetricItem(BaseModel):
    label: str
    value: str
    pct: int = 0


class AdminStatsResponse(BaseModel):
    totalUsers: int = 0
    freeUsers: int = 0
    proTierUsers: int = 0
    legendUsers: int = 0
    proUsers: int = 0
    totalPosts: int = 0
    totalComments: int = 0
    totalPostViews: int = 0
    totalMessages: int = 0
    voiceMessages: int = 0
    deletedMessages: int = 0
    totalChats: int = 0
    activeSessions: int = 0
    totalLessons: int = 0
    publishedLessons: int = 0
    totalNewsArticles: int = 0
    translatedArticles: int = 0
    totalNotifications: int = 0
    totalReactions: int = 0
    totalFollows: int = 0
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AdminOverviewResponse(BaseModel):
    stats: AdminStatsResponse
    userSeries: list[AdminTimePoint] = Field(default_factory=list)
    postSeries: list[AdminTimePoint] = Field(default_factory=list)
    messageSeries: list[AdminTimePoint] = Field(default_factory=list)
    weeklyEngagement: list[AdminWeeklyEngagementItem] = Field(default_factory=list)
    sentiment: list[AdminSentimentItem] = Field(default_factory=list)
    messageTypes: list[AdminMessageTypeItem] = Field(default_factory=list)
    topSymbols: list[AdminTopSymbolItem] = Field(default_factory=list)
    platformDistribution: list[AdminNamedValueItem] = Field(default_factory=list)
    keyMetrics: list[AdminKeyMetricItem] = Field(default_factory=list)
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
