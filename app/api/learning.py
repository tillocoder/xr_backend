from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.presentation.api.json_cache import JsonRouteCache
from app.presentation.api.request_state import get_notification_service
from app.schemas.learning import (
    AdminLearningVideoLessonResponse,
    LearningVideoLessonResponse,
    LearningVideoUploadResponse,
    LearningVideoLessonUpsertRequest,
)
from app.services.learning_service import LearningService

router = APIRouter(prefix="/learning", tags=["learning"])
admin_router = APIRouter(prefix="/admin/learning", tags=["admin-learning"])

_security = HTTPBasic()
_learning_service = LearningService()
_PUBLIC_LEARNING_VIDEO_CACHE = JsonRouteCache(
    namespace="learning:videos:v1",
    ttl_setting_name="learning_public_cache_ttl_seconds",
    default_ttl_seconds=75,
    min_ttl_seconds=20,
    max_ttl_seconds=180,
)


def _require_admin(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    settings = get_settings()
    if not settings.admin_features_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    is_valid_username = secrets.compare_digest(
        credentials.username,
        settings.admin_panel_username,
    )
    is_valid_password = secrets.compare_digest(
        credentials.password,
        settings.admin_panel_password,
    )
    if is_valid_username and is_valid_password:
        return credentials.username
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin credentials.",
        headers={"WWW-Authenticate": "Basic"},
    )


@router.get("/videos", response_model=list[LearningVideoLessonResponse])
async def get_learning_videos(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[LearningVideoLessonResponse]:
    cache_key = _PUBLIC_LEARNING_VIDEO_CACHE.build_key("published")
    cached = await _PUBLIC_LEARNING_VIDEO_CACHE.get(request, cache_key)
    if cached is not None:
        return cached
    payload = await _learning_service.list_published_video_lessons(db)
    await _PUBLIC_LEARNING_VIDEO_CACHE.set(request, cache_key, payload)
    return payload


@admin_router.get("", response_class=HTMLResponse)
async def get_learning_admin_panel(
    request: Request,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    lessons = await _learning_service.list_all_video_lessons(db)
    return HTMLResponse(_render_admin_page(request, lessons))


@admin_router.get("/videos", response_model=list[AdminLearningVideoLessonResponse])
async def get_learning_admin_videos(
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminLearningVideoLessonResponse]:
    return await _learning_service.list_all_video_lessons(db)


@admin_router.post(
    "/videos",
    response_model=AdminLearningVideoLessonResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_learning_video(
    request: Request,
    payload: LearningVideoLessonUpsertRequest,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminLearningVideoLessonResponse:
    lesson = await _learning_service.create_video_lesson(db, payload)
    _queue_learning_video_notification(request, lesson)
    return lesson


@admin_router.put("/videos/{lesson_id}", response_model=AdminLearningVideoLessonResponse)
async def update_learning_video(
    lesson_id: str,
    payload: LearningVideoLessonUpsertRequest,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminLearningVideoLessonResponse:
    return await _learning_service.update_video_lesson(db, lesson_id, payload)


@admin_router.post("/upload-video", response_model=LearningVideoUploadResponse)
async def upload_learning_video_file(
    file: UploadFile = File(...),
    _: str = Depends(_require_admin),
) -> LearningVideoUploadResponse:
    return await _learning_service.upload_video_file(file)


@admin_router.post("/videos/{lesson_id}/publish", response_model=AdminLearningVideoLessonResponse)
async def publish_learning_video(
    request: Request,
    lesson_id: str,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminLearningVideoLessonResponse:
    lesson = await _learning_service.set_published(db, lesson_id, value=True)
    _queue_learning_video_notification(request, lesson)
    return lesson


@admin_router.post("/videos/{lesson_id}/unpublish", response_model=AdminLearningVideoLessonResponse)
async def unpublish_learning_video(
    lesson_id: str,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminLearningVideoLessonResponse:
    return await _learning_service.set_published(db, lesson_id, value=False)


@admin_router.post("/videos/{lesson_id}/feature", response_model=AdminLearningVideoLessonResponse)
async def feature_learning_video(
    lesson_id: str,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminLearningVideoLessonResponse:
    return await _learning_service.set_featured(db, lesson_id, value=True)


@admin_router.post("/videos/{lesson_id}/unfeature", response_model=AdminLearningVideoLessonResponse)
async def unfeature_learning_video(
    lesson_id: str,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminLearningVideoLessonResponse:
    return await _learning_service.set_featured(db, lesson_id, value=False)


@admin_router.delete("/videos/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_learning_video(
    lesson_id: str,
    _: str = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _learning_service.delete_video_lesson(db, lesson_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _queue_learning_video_notification(
    request: Request,
    lesson: AdminLearningVideoLessonResponse,
) -> None:
    if not lesson.isPublished:
        return

    summary = lesson.summary.strip()
    body = summary if summary else "A new learning video is now available in Learn."
    if len(body) > 160:
        body = f"{body[:160].rstrip()}..."

    get_notification_service(request).queue_broadcast_notification(
        kind="learning_video",
        title="New learning video",
        body=f"{lesson.title}: {body}",
        extra_payload={
            "targetRoute": "/insights",
            "learningVideoId": lesson.id,
            "videoUrl": lesson.videoUrl,
            "thumbnailUrl": lesson.thumbnailUrl,
            "imageUrl": lesson.thumbnailUrl,
            "linkUrl": lesson.linkUrl,
        },
    )


def _render_admin_page(
    request: Request,
    lessons: list[AdminLearningVideoLessonResponse],
) -> str:
    payload = json.dumps(
        [item.model_dump(mode="json") for item in lessons],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    base_url = str(request.base_url).rstrip("/")
    api_base = f"{base_url}/admin/learning"
    return _ADMIN_PAGE_TEMPLATE.replace("__LESSONS_JSON__", payload).replace(
        "__API_BASE__",
        api_base,
    )


_ADMIN_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>XR HODL Learning Admin</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Segoe UI, sans-serif;
      background: #07111f;
      color: #e8eef9;
    }
    .wrap {
      width: min(1100px, calc(100% - 32px));
      margin: 24px auto 48px;
    }
    h1 { margin: 0 0 8px; font-size: 32px; }
    p { color: #9eb1ca; }
    .grid {
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }
    .panel {
      background: linear-gradient(180deg, rgba(18,33,56,.95), rgba(10,18,31,.98));
      border: 1px solid rgba(107,135,172,.24);
      border-radius: 20px;
      padding: 18px;
      box-shadow: 0 18px 50px rgba(0,0,0,.25);
    }
    label {
      display: block;
      margin-bottom: 14px;
      font-size: 13px;
      font-weight: 700;
      color: #c7d5ea;
    }
    input, textarea, select {
      width: 100%;
      margin-top: 6px;
      border: 1px solid rgba(107,135,172,.24);
      border-radius: 12px;
      background: #091423;
      color: #f4f8ff;
      padding: 12px 13px;
      font: inherit;
    }
    textarea { min-height: 110px; resize: vertical; }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .checks {
      display: flex;
      gap: 14px;
      align-items: center;
      margin: 4px 0 16px;
      color: #c7d5ea;
      font-size: 13px;
    }
    .checks label {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin: 0;
      font-weight: 600;
    }
    .checks input { width: auto; margin: 0; }
    button {
      border: 0;
      border-radius: 12px;
      padding: 12px 16px;
      cursor: pointer;
      font: inherit;
      font-weight: 700;
    }
    .primary { background: #36f7a1; color: #07111f; width: 100%; }
    .toolbar { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
    .hint { font-size: 12px; color: #8ea1bc; }
    .hint.block { display: block; margin-top: 8px; line-height: 1.45; }
    .hint.warn-block {
      color: #ffd5da;
      background: rgba(71,33,40,.42);
      border: 1px solid rgba(255,133,148,.22);
      border-radius: 12px;
      padding: 10px 12px;
    }
    .list { display: grid; gap: 14px; margin-top: 16px; }
    .card {
      background: rgba(7, 17, 31, .8);
      border: 1px solid rgba(107,135,172,.24);
      border-radius: 16px;
      overflow: hidden;
    }
    .thumb {
      height: 180px;
      background: linear-gradient(135deg, #102746, #0a1220);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #6fdcff;
      font-size: 42px;
      font-weight: 800;
    }
    .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .body { padding: 14px; }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 10px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(54,247,161,.12);
      color: #78ffc0;
      font-size: 12px;
      font-weight: 700;
    }
    .pill.dim {
      background: rgba(120,142,172,.14);
      color: #cad6e7;
    }
    .title { font-size: 18px; font-weight: 800; margin: 0 0 8px; }
    .summary { margin: 0; line-height: 1.5; }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }
    .ghost { background: #162338; color: #dce6f4; }
    .warn { background: #472128; color: #ffd5da; }
    .empty {
      margin-top: 16px;
      padding: 18px;
      border-radius: 16px;
      border: 1px dashed rgba(107,135,172,.28);
      color: #9eb1ca;
      text-align: center;
    }
    .status {
      min-height: 20px;
      margin-top: 12px;
      color: #8df0b7;
      font-size: 13px;
      font-weight: 600;
    }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="toolbar">
      <div>
        <h1>Learning Video Admin</h1>
        <p>Only lessons added here are shown in the app learning page.</p>
      </div>
      <div class="hint">Protected by HTTP Basic Auth</div>
    </div>

    <div class="grid">
      <section class="panel">
        <form id="lesson-form">
          <label>Title
            <input name="title" maxlength="140" required placeholder="Video lesson title">
          </label>
          <label>Description
            <textarea name="summary" maxlength="500" placeholder="Short lesson description"></textarea>
          </label>
          <label>Video URL
            <input name="videoUrl" maxlength="1024" placeholder="https://youtube.com/watch?v=...">
            <span class="hint block">Paste a YouTube link or direct video URL, or leave this empty and upload a video file below.</span>
            <span class="hint block warn-block" id="youtube-warning" hidden>YouTube links may show the YouTube player chrome inside the app. If you want a clean in-app video player with only the lesson itself, upload an MP4/WebM file instead.</span>
          </label>
          <label>Upload video file
            <input name="videoFile" type="file" accept="video/*">
          </label>
          <label>Extra link
            <input name="linkUrl" maxlength="1024" placeholder="https://your-extra-resource.com">
          </label>
          <label>Thumbnail URL
            <input name="thumbnailUrl" maxlength="1024" placeholder="https://.../thumbnail.jpg">
          </label>
          <div class="row">
            <label>Category
              <select name="tagKey">
                <option value="education">Education</option>
                <option value="strategy">Strategy</option>
                <option value="analysis">Analysis</option>
              </select>
            </label>
            <label>Duration (minutes)
              <input name="durationMinutes" type="number" min="0" max="600" value="6">
            </label>
          </div>
          <div class="row">
            <label>Sort order
              <input name="sortOrder" type="number" min="0" max="9999" value="0">
            </label>
            <div></div>
          </div>
          <div class="checks">
            <label><input name="isFeatured" type="checkbox"> Featured</label>
            <label><input name="isPublished" type="checkbox" checked> Published</label>
          </div>
          <button class="primary" type="submit">Add video lesson</button>
          <div class="status" id="status"></div>
        </form>
      </section>

      <section class="panel">
        <div class="toolbar">
          <strong>Current video lessons</strong>
          <span class="hint" id="lesson-count"></span>
        </div>
        <div class="list" id="lesson-list"></div>
        <div class="empty" id="empty-state" hidden>No video lessons created yet.</div>
      </section>
    </div>
  </div>

  <script>
    const API_BASE = "__API_BASE__";
    const lessons = __LESSONS_JSON__;
    const listEl = document.getElementById("lesson-list");
    const emptyEl = document.getElementById("empty-state");
    const countEl = document.getElementById("lesson-count");
    const statusEl = document.getElementById("status");
    const formEl = document.getElementById("lesson-form");
    const youtubeWarningEl = document.getElementById("youtube-warning");
    const videoUrlInput = formEl.elements.namedItem("videoUrl");

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function cardHtml(item) {
      const lessonId = JSON.stringify(item.id || "");
      const videoUrl = JSON.stringify(item.videoUrl || "");
      const isYoutube = isYouTubeUrl(item.videoUrl || "");
      const thumb = item.thumbnailUrl
        ? `<img src="${escapeHtml(item.thumbnailUrl)}" alt="">`
        : `<span>XR</span>`;
      const pills = [
        `<span class="pill">${escapeHtml(item.tagKey)}</span>`,
        `<span class="pill dim">${item.durationMinutes || 0} min</span>`,
        `<span class="pill dim">${item.isPublished ? "Published" : "Draft"}</span>`,
      ];
      pills.unshift(`<span class="pill dim">${isYoutube ? "YouTube link" : "Uploaded / direct"}</span>`);
      if (item.isFeatured) {
        pills.unshift(`<span class="pill">Featured</span>`);
      }
      const extraLink = item.linkUrl
        ? `<button class="ghost" onclick='window.open(${JSON.stringify(item.linkUrl)}, "_blank", "noopener")'>Open link</button>`
        : "";
      return `
        <article class="card">
          <div class="thumb">${thumb}</div>
          <div class="body">
            <div class="meta">${pills.join("")}</div>
            <h3 class="title">${escapeHtml(item.title)}</h3>
            <p class="summary">${escapeHtml(item.summary)}</p>
            <div class="actions">
              <button class="ghost" onclick='window.open(${videoUrl}, "_blank", "noopener")'>Open video</button>
              ${extraLink}
              <button class="ghost" onclick='togglePublish(${lessonId}, ${item.isPublished ? "false" : "true"})'>
                ${item.isPublished ? "Unpublish" : "Publish"}
              </button>
              <button class="warn" onclick='deleteLesson(${lessonId})'>Delete</button>
            </div>
          </div>
        </article>
      `;
    }

    function render() {
      countEl.textContent = `${lessons.length} total`;
      if (!lessons.length) {
        listEl.innerHTML = "";
        emptyEl.hidden = false;
        return;
      }
      emptyEl.hidden = true;
      listEl.innerHTML = lessons.map(cardHtml).join("");
    }

    function isYouTubeUrl(value) {
      return /(?:youtube[.]com|youtu[.]be)/i.test(String(value || ""));
    }

    function updateVideoSourceHint() {
      if (!(videoUrlInput instanceof HTMLInputElement) || !youtubeWarningEl) {
        return;
      }
      youtubeWarningEl.hidden = !isYouTubeUrl(videoUrlInput.value);
    }

    async function createLesson(payload) {
      const response = await fetch(`${API_BASE}/videos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
    }

    async function uploadVideoFile(file) {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch(`${API_BASE}/upload-video`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      return await response.json();
    }

    async function togglePublish(id, nextValue) {
      const path = nextValue ? "publish" : "unpublish";
      const response = await fetch(`${API_BASE}/videos/${id}/${path}`, {
        method: "POST",
      });
      if (!response.ok) {
        alert(await response.text());
        return;
      }
      window.location.reload();
    }

    async function deleteLesson(id) {
      if (!window.confirm("Delete this lesson?")) return;
      const response = await fetch(`${API_BASE}/videos/${id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        alert(await response.text());
        return;
      }
      window.location.reload();
    }

    formEl.addEventListener("submit", async (event) => {
      event.preventDefault();
      statusEl.textContent = "Saving...";
      const formData = new FormData(formEl);
      const externalVideoUrl = (formData.get("videoUrl") || "").toString().trim();
      const videoFile = formData.get("videoFile");
      const payload = {
        title: (formData.get("title") || "").toString().trim(),
        summary: (formData.get("summary") || "").toString().trim(),
        videoUrl: externalVideoUrl,
        linkUrl: (formData.get("linkUrl") || "").toString().trim() || null,
        thumbnailUrl: (formData.get("thumbnailUrl") || "").toString().trim() || null,
        tagKey: (formData.get("tagKey") || "education").toString(),
        durationMinutes: Number(formData.get("durationMinutes") || 0),
        sortOrder: Number(formData.get("sortOrder") || 0),
        isFeatured: formData.get("isFeatured") === "on",
        isPublished: formData.get("isPublished") === "on",
      };
      try {
        const hasVideoFile = videoFile instanceof File && videoFile.size > 0;
        if (!payload.videoUrl && !hasVideoFile) {
          throw new Error("Add a video URL or upload a video file.");
        }
        if (hasVideoFile) {
          statusEl.textContent = "Uploading video...";
          const upload = await uploadVideoFile(videoFile);
          payload.videoUrl = (upload.path || upload.url || "").toString().trim();
        }
        await createLesson(payload);
        formEl.reset();
        statusEl.textContent = "Saved. Reloading...";
        window.location.reload();
      } catch (error) {
        statusEl.textContent = error instanceof Error ? error.message : "Save failed.";
      }
    });

    if (videoUrlInput instanceof HTMLInputElement) {
      videoUrlInput.addEventListener("input", updateVideoSourceHint);
      videoUrlInput.addEventListener("change", updateVideoSourceHint);
    }

    updateVideoSourceHint();
    render();
  </script>
</body>
</html>
"""
