export type AppSession = {
  id: string;
  displayName: string;
  email: string | null;
  discordUsername: string | null;
  avatarUrl: string | null;
  isAdmin: boolean;
  authEnabled: boolean;
};

export type ClipSummary = {
  id: string;
  name: string;
  durationSeconds: number | null;
  status: string;
  storagePath: string;
  team: string | null;
  player: string | null;
  tier: string | null;
  extension: string | null;
};

export type MovieSlot = {
  id: string;
  sortIndex: number;
  overlayText: string;
  clipId: string | null;
};

export type VideoSummary = {
  id: string;
  movieId: string;
  storagePath: string;
  status: string;
  createdAt: string;
  durationSeconds: number | null;
  downloadUrl: string | null;
};

export type MovieSummary = {
  id: string;
  title: string;
  status: string;
  updatedAt: string;
  slotCount: number;
  latestVideo: VideoSummary | null;
  transitionStyle: string;
  transitionHoldSeconds: number;
};

export type MovieDetail = MovieSummary & {
  clips: ClipSummary[];
  slots: MovieSlot[];
  transitionStyles: string[];
  latestJob: RenderJob | null;
};

export type Logo = {
  id: string;
  team: string;
  originalName: string;
  storagePath: string;
  imageUrl: string;
  createdAt: string;
};

export type ClientConfig = {
  filenamePattern: string;
  supportedExtensions: string[];
  minClipCount: number;
  maxClipCount: number;
  maxClipSizeBytes: number;
  maxUploadBytes: number;
  maxClipDurationSeconds: number;
};

export type RenderJobClipResult = {
  id: string;
  clipId: string | null;
  clipName: string;
  clipOrderIndex: number;
  status: string;
  reasonCode: string | null;
  reasonSummary: string | null;
  technicalDetail: string | null;
  providerMessage: string | null;
};

export type RenderAttempt = {
  id: string;
  attemptNumber: number;
  status: string;
  failureStage: string | null;
  errorCategory: string | null;
  errorCode: string | null;
  providerErrorCode: string | null;
  errorSummary: string | null;
  errorDetail: string | null;
  createdAt: string;
  completedAt: string | null;
  clipResults: RenderJobClipResult[];
};

export type RenderJob = {
  id: string;
  movieId: string;
  status: string;
  finalStatusReason: string | null;
  acceptedClipCount: number;
  failedClipCount: number;
  hasClipLevelFailures: boolean;
  attempts: RenderAttempt[];
};

export type AdminUser = {
  id: string;
  displayName: string;
  email: string | null;
  discordUsername: string | null;
  isAdmin: boolean;
  movieCount: number;
  videoCount: number;
  latestSignInAt: string | null;
};

export type AdminVideo = {
  id: string;
  movieTitle: string;
  ownerName: string;
  status: string;
  storagePath: string;
  latestAttemptStatus: string | null;
  createdAt: string;
};

export type AdminError = {
  jobId: string;
  movieTitle: string;
  ownerName: string;
  attemptNumber: number;
  status: string;
  failureStage: string | null;
  errorCategory: string | null;
  errorSummary: string | null;
  failedClipCount: number;
  createdAt: string;
  clipResults: RenderJobClipResult[];
};

export type AdminDashboard = {
  totalUsers: number;
  totalMovies: number;
  totalVideos: number;
  failedAttempts: number;
};
