import type { BackgroundAsset, Chapter, Compatibility, Reciter, RenderJob, VersePreview, VisualStyle } from "./types";

const configuredApiBase = import.meta.env.VITE_API_BASE_URL?.trim() ?? "";
const API_BASE = configuredApiBase.endsWith("/") ? configuredApiBase.slice(0, -1) : configuredApiBase;
const apiUrl = (path: string) => `${API_BASE}${path}`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), init);
  } catch {
    throw new Error(`Could not reach the API at ${apiUrl(path)}. Start the backend with make dev.`);
  }
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }
  return (await response.json()) as T;
}

async function responseErrorMessage(response: Response): Promise<string> {
  const body = await response.text();
  if (!body) {
    return response.statusText || `Request failed with HTTP ${response.status}`;
  }
  try {
    const parsed = JSON.parse(body) as unknown;
    if (parsed && typeof parsed === "object") {
      const detail = "detail" in parsed ? parsed.detail : null;
      if (typeof detail === "string") {
        return detail;
      }
      if (detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string") {
        return detail.message;
      }
      if ("message" in parsed && typeof parsed.message === "string") {
        return parsed.message;
      }
    }
  } catch {
    return body;
  }
  return body;
}

export const api = {
  chapters: () => request<Chapter[]>("/api/v1/chapters"),
  reciters: () => request<Reciter[]>("/api/v1/reciters"),
  backgrounds: () => request<BackgroundAsset[]>("/api/v1/backgrounds"),
  style: () => request<VisualStyle>("/api/v1/style"),
  saveStyle: (body: VisualStyle) =>
    request<VisualStyle>("/api/v1/style", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }),
  backgroundUrl: (backgroundId: string) =>
    apiUrl(`/api/v1/backgrounds/file/${backgroundId.split("/").map(encodeURIComponent).join("/")}`),
  compatibility: (reciterId: string, chapterId: number, moshafId?: string) => {
    const params = new URLSearchParams({
      reciter_id: reciterId,
      chapter_id: String(chapterId)
    });
    if (moshafId) {
      params.set("moshaf_id", moshafId);
    }
    return request<Compatibility>(`/api/v1/compatibility?${params.toString()}`);
  },
  verses: (chapterId: number) => request<VersePreview[]>(`/api/v1/chapters/${chapterId}/verses`),
  validateRender: (body: unknown) =>
    request<{ compatible: boolean; reason: string | null }>("/api/v1/render/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }),
  createRender: (body: unknown) =>
    request<RenderJob>("/api/v1/renders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }),
  cancelRender: (jobId: string) =>
    request<RenderJob>(`/api/v1/renders/${jobId}/cancel`, { method: "POST" }),
  uploadBackground: async (file: File) => {
    const form = new FormData();
    form.set("file", file);
    return request<BackgroundAsset>("/api/v1/backgrounds/upload", { method: "POST", body: form });
  },
  eventSource: (jobId: string) => new EventSource(apiUrl(`/api/v1/renders/${jobId}/events`)),
  videoUrl: (jobId: string) => apiUrl(`/api/v1/renders/${jobId}/video`),
  thumbnailUrl: (jobId: string) => apiUrl(`/api/v1/renders/${jobId}/thumbnail`),
  outputsUrl: (jobId: string) => apiUrl(`/api/v1/renders/${jobId}/outputs`)
};
