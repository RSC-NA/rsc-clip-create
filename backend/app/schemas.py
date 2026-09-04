from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionResponse(BaseModel):
    id: str
    displayName: str
    email: str | None
    discordUsername: str | None
    avatarUrl: str | None
    isAdmin: bool
    authEnabled: bool


class ClientConfigResponse(BaseModel):
    filenamePattern: str
    supportedExtensions: list[str]
    minClipCount: int
    maxClipCount: int
    maxClipSizeBytes: int
    maxUploadBytes: int
    maxClipDurationSeconds: float


class ClipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    durationSeconds: float | None
    status: str
    storagePath: str
    team: str | None
    player: str | None
    tier: str | None
    extension: str | None


class LogoResponse(BaseModel):
    id: str
    originalName: str
    storagePath: str
    imageUrl: str
    createdAt: datetime


class MovieSlotPayload(BaseModel):
    id: str | None = Field(default=None, max_length=36)
    # Bounded so an out-of-range value is a 422 rather than a MySQL INTEGER
    # overflow surfacing as a 500.
    sort_index: int = Field(ge=0, le=10_000)
    clip_id: str | None = Field(default=None, max_length=36)
    overlay_text: str = Field(default="", max_length=2000)


class MovieCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class MovieTimelineUpdateRequest(BaseModel):
    slots: list[MovieSlotPayload] = Field(default_factory=list, max_length=500)
    transition_style: str | None = None
    transition_hold_seconds: float | None = Field(default=None, ge=0, le=3)


class MovieSlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sortIndex: int
    overlayText: str
    clipId: str | None


class VideoSummaryResponse(BaseModel):
    id: str
    movieId: str
    storagePath: str
    status: str
    createdAt: datetime
    durationSeconds: float | None = None
    downloadUrl: str | None = None


class MovieSummaryResponse(BaseModel):
    id: str
    title: str
    status: str
    updatedAt: datetime
    slotCount: int
    latestVideo: VideoSummaryResponse | None
    transitionStyle: str
    transitionHoldSeconds: float


class MovieDetailResponse(MovieSummaryResponse):
    clips: list[ClipResponse]
    slots: list[MovieSlotResponse]
    transitionStyles: list[str]
    latestJob: "RenderJobResponse | None" = None


class RenderClipResultResponse(BaseModel):
    id: str
    clipId: str | None
    clipName: str
    clipOrderIndex: int
    status: str
    reasonCode: str | None
    reasonSummary: str | None
    technicalDetail: str | None
    providerMessage: str | None


class RenderAttemptResponse(BaseModel):
    id: str
    attemptNumber: int
    status: str
    failureStage: str | None
    errorCategory: str | None
    errorCode: str | None
    providerErrorCode: str | None
    errorSummary: str | None
    errorDetail: str | None
    createdAt: datetime
    completedAt: datetime | None
    clipResults: list[RenderClipResultResponse]


class RenderJobResponse(BaseModel):
    id: str
    movieId: str
    status: str
    finalStatusReason: str | None
    acceptedClipCount: int
    failedClipCount: int
    hasClipLevelFailures: bool
    attempts: list[RenderAttemptResponse]


class VideoLocationResponse(BaseModel):
    videoUuid: str
    storagePath: str
    downloadUrl: str


class AdminDashboardResponse(BaseModel):
    totalUsers: int
    totalMovies: int
    totalVideos: int
    failedAttempts: int


class AdminUserResponse(BaseModel):
    id: str
    displayName: str
    email: str | None
    discordUsername: str | None
    isAdmin: bool
    movieCount: int
    videoCount: int
    latestSignInAt: datetime | None


class AdminVideoResponse(BaseModel):
    id: str
    movieTitle: str
    ownerName: str
    status: str
    storagePath: str
    latestAttemptStatus: str | None
    createdAt: datetime


class AdminErrorResponse(BaseModel):
    jobId: str
    movieTitle: str
    ownerName: str
    attemptNumber: int
    status: str
    failureStage: str | None
    errorCategory: str | None
    errorSummary: str | None
    failedClipCount: int
    createdAt: datetime
    clipResults: list[RenderClipResultResponse]


MovieDetailResponse.model_rebuild()
