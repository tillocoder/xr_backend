from datetime import datetime

from pydantic import BaseModel, Field


class CommunityPublicHoldingSummaryResponse(BaseModel):
    amount: float
    avgBuyPrice: float
    entryDate: datetime


class CommunityPollResponse(BaseModel):
    options: list[str] = Field(default_factory=list)
    voteCounts: list[int] = Field(default_factory=list)
    totalVotes: int = 0
    durationDays: int = 0
    endsAt: datetime | None = None


class CommunityProfileResponse(BaseModel):
    uid: str
    displayName: str
    username: str
    avatarUrl: str | None = None
    coverImageUrl: str | None = None
    avatarUpdatedAt: datetime | None = None
    coverImageUpdatedAt: datetime | None = None
    biography: str = ""
    birthdayLabel: str = ""
    website: str = ""
    socialAccounts: dict[str, str] = Field(default_factory=dict)
    publicWatchlistSymbols: list[str] = Field(default_factory=list)
    blockedAccountIds: list[str] = Field(default_factory=list)
    usernameUpdatedAt: datetime | None = None
    displayNameWindowStartedAt: datetime | None = None
    displayNameChangeCount: int = 0
    membershipTier: str = "free"
    isPro: bool = False


class CommunityPostResponse(BaseModel):
    id: str
    authorUid: str
    authorName: str
    authorUsername: str
    authorAvatarUrl: str | None = None
    authorMembershipTier: str = "free"
    authorIsPro: bool = False
    content: str
    symbol: str | None = None
    symbols: list[str] = Field(default_factory=list)
    imageUrl: str | None = None
    marketBias: str | None = None
    poll: CommunityPollResponse | None = None
    commentCount: int = 0
    viewCount: int = 0
    reactionCounts: dict[int, int] = Field(default_factory=dict)
    createdAt: datetime | None = None


class CommunityCommentResponse(BaseModel):
    id: str
    postId: str
    authorUid: str
    authorName: str
    authorUsername: str
    authorAvatarUrl: str | None = None
    replyToCommentId: str | None = None
    replyToAuthorUsername: str | None = None
    content: str
    createdAt: datetime | None = None
    reactionCounts: dict[int, int] = Field(default_factory=dict)


class CommunityProfileUpsertRequest(BaseModel):
    displayName: str
    username: str
    avatarUrl: str | None = None
    coverImageUrl: str | None = None
    biography: str = ""
    birthdayLabel: str = ""
    website: str = ""
    socialAccounts: dict[str, str] = Field(default_factory=dict)
    publicWatchlistSymbols: list[str] = Field(default_factory=list)
    blockedAccountIds: list[str] = Field(default_factory=list)


class CommunityUpdatePostRequest(BaseModel):
    content: str


class CommunityUpdateUsernameRequest(BaseModel):
    username: str


class CommunityCreatePostRequest(BaseModel):
    content: str
    symbol: str | None = None
    symbols: list[str] = Field(default_factory=list)
    imageUrl: str | None = None
    pollOptions: list[str] = Field(default_factory=list)
    pollDurationDays: int | None = None
    marketBias: str | None = None


class CommunityVotePollRequest(BaseModel):
    optionIndex: int


class CommunityAddCommentRequest(BaseModel):
    content: str
    replyToCommentId: str | None = None
    replyToAuthorUsername: str | None = None


class CommunityReactCommentRequest(BaseModel):
    reactionCode: int


class CommunityImageUploadRequest(BaseModel):
    fileName: str
    contentBase64: str


class CommunityImageUploadResponse(BaseModel):
    url: str
    path: str


class PostReactionChangeIn(BaseModel):
    reactionIndex: int | None = None
    reactionKey: str | None = None
    currentReactionIndex: int | None = None
    currentReaction: str | None = None


class PostReactionStateOut(BaseModel):
    postId: str
    authorUid: str | None = None
    reaction: str | None = None
    reactionKey: str | None = None
    reactionIndex: int | None = None
    reactionCounts: dict[int, int]
    updatedAt: datetime
