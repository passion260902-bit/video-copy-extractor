"""Opt-in, local browser fallback. No system browser profile or cookie export."""
from __future__ import annotations

from pathlib import Path
import sys
import time
import urllib.parse

from .acquire import MAX_CAPTION, MAX_DURATION, MAX_MEDIA, SafeError, download_asset, name_media, validate_url


def _profile(path: Path) -> Path:
    path = path.resolve()
    marker = path / ".video-copy-extractor-profile"
    if path.exists() and any(path.iterdir()) and not marker.is_file():
        raise SafeError("unsafe_profile", "拒绝使用已有浏览器资料；请选择应用独立的空 profile 目录。")
    path.mkdir(parents=True, exist_ok=True)
    marker.touch(exist_ok=True)
    return path


_DOUYIN = r"""(id) => {
  const box = document.querySelector('[data-e2e-vid="'+id+'"]') ||
              document.querySelector('[data-e2e="player-container"].video_'+id);
  if (!box) return null;
  const video = box.querySelector('video');
  if (!video) return null;
  return {id, title:document.title, author:'', duration:video.duration,
          media:video.currentSrc || video.src, caption:null};
}"""

_XHS = r"""(id) => {
  const note = window.__INITIAL_STATE__?.note?.noteDetailMap?.[id]?.note;
  if (!note || note.noteId !== id) return null;
  let media = note.video?.mediaV2;
  try { if (typeof media === 'string') media = JSON.parse(media); } catch { return null; }
  const tracks = media?.video?.subtitles?.source || [];
  const caption = tracks.find(x => typeof x.url === 'string' && x.url.startsWith('https://'));
  const streams = Object.values(media?.stream || {}).flat().filter(
     x => x.audio_channels > 0 && typeof x.master_url === 'string');
  streams.sort((a,b) => (a.size || Infinity) - (b.size || Infinity));
  return {id:note.noteId, title:note.title || '', author:note.user?.nickname || '',
          duration:(media?.video?.duration || streams[0]?.duration || 0)/1000,
          caption:caption?.url || null, media:streams[0]?.master_url || null};
}"""

_YOUTUBE = r"""(id) => {
  const player = document.querySelector('#movie_player');
  const response = player?.getPlayerResponse?.() || window.ytInitialPlayerResponse;
  if (response?.videoDetails?.videoId !== id) return null;
  const renderer = response.captions?.playerCaptionsTracklistRenderer;
  const tracks = renderer?.captionTracks || [];
  const index = renderer?.audioTracks?.[renderer.defaultAudioTrackIndex || 0]?.defaultCaptionTrackIndex;
  let caption = Number.isInteger(index) ? tracks[index] : null;
  if (!caption && tracks.length === 1) caption = tracks[0];
  return {id, title:response.videoDetails.title || '', author:response.videoDetails.author || '',
          duration:Number(response.videoDetails.lengthSeconds),
          caption:caption?.baseUrl || null, automatic:caption?.kind === 'asr', media:null};
}"""


def _save_snapshot(data: dict, target: dict, output_dir: Path) -> dict:
    if str(data.get("id")) != target["id"]:
        raise SafeError("video_mismatch", "浏览器页面与指定视频 ID 不一致，已停止提取。")
    duration = data.get("duration")
    if isinstance(duration, (int, float)) and duration > MAX_DURATION:
        raise SafeError("duration_limit", "第一版仅处理不超过 60 分钟的视频。")
    result = {key: target[key] for key in ("platform", "id", "source_url")}
    result.update(title=data.get("title") or "", author=data.get("author") or "",
                  duration=duration if isinstance(duration, (int, float)) and duration > 0 else None,
                  media_path=None, caption_path=None, caption_format=None, caption_kind=None,
                  selection_note=target.get("selection_note"))
    headers = {"User-Agent": "Mozilla/5.0", "Referer": target["source_url"]}
    caption = data.get("caption")
    if caption:
        extension = "srt"
        if target["platform"] == "youtube":
            extension = "json3"
            parsed = urllib.parse.urlsplit(caption)
            query = urllib.parse.parse_qsl(parsed.query)
            query = [(key, value) for key, value in query if key not in ("fmt", "tlang")]
            caption = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query + [("fmt", "json3")])))
        else:
            suffix = Path(urllib.parse.urlsplit(caption).path).suffix.lower().lstrip(".")
            if suffix in ("srt", "vtt", "json3"):
                extension = suffix
        output = output_dir / ("captions." + extension)
        download_asset(caption, output, target["platform"], MAX_CAPTION, headers)
        result.update(caption_path=output, caption_format=extension,
                      caption_kind="automatic_original" if data.get("automatic") else "provided_original")
    elif data.get("media"):
        output = output_dir / "media.download"
        download_asset(data["media"], output, target["platform"], MAX_MEDIA, headers)
        result["media_path"] = name_media(output)
    else:
        raise SafeError("no_browser_media", "当前页面没有可安全获取的原始字幕或带音轨媒体；请使用本地视频文件。")
    return result


def acquire_browser(target: dict, output_dir: Path, profile_dir: Path, channel: str = "chrome") -> dict:
    if channel not in ("chrome", "msedge"):
        raise SafeError("unsupported_browser", "浏览器模式仅支持本机 Chrome 或 Edge。")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SafeError("missing_browser_dependency", "缺少 Playwright；请安装项目 browser 可选依赖。") from None
    profile = _profile(profile_dir)
    script = {"douyin": _DOUYIN, "xiaohongshu": _XHS, "youtube": _YOUTUBE}[target["platform"]]
    print("已请求打开应用独立浏览器。若页面要求登录或验证，请本人在浏览器内完成；等待最多 90 秒。", file=sys.stderr)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(profile), channel=channel, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                def guard(route):
                    if route.request.is_navigation_request() and route.request.frame == page.main_frame:
                        try:
                            validate_url(route.request.url, target["platform"])
                        except SafeError:
                            route.abort()
                            return
                    route.continue_()
                page.route("**/*", guard)
                page.goto(target["fetch_url"], wait_until="domcontentloaded", timeout=30000)
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    validate_url(page.url, target["platform"])
                    data = page.evaluate(script, target["id"])
                    if data and (data.get("caption") or data.get("media")):
                        return _save_snapshot(data, target, output_dir)
                    page.wait_for_timeout(1000)
                raise SafeError("needs_user_login", "未获得目标视频。请完成独立浏览器中的登录/验证，并确认作品可播放后重试。")
            finally:
                context.close()
    except SafeError:
        raise
    except Exception:
        raise SafeError("browser_acquisition_failed", "浏览器提取未完成。请检查 Chrome/Edge 安装、独立 profile 是否被占用及作品访问权限。") from None


def open_browser(profile_dir: Path, channel: str = "chrome", platform: str = "douyin") -> dict:
    """Let the user log in locally; closing this browser window completes setup."""
    sites = {"douyin": "https://www.douyin.com/", "xiaohongshu": "https://www.xiaohongshu.com/",
             "youtube": "https://www.youtube.com/"}
    if platform not in sites or channel not in ("chrome", "msedge"):
        raise SafeError("unsupported_browser", "请选择受支持的平台，以及本机 Chrome 或 Edge。")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SafeError("missing_browser_dependency", "缺少 Playwright；请安装项目 browser 可选依赖。") from None
    profile = _profile(Path(profile_dir))
    print("请在应用独立浏览器中自行登录，完成后关闭该浏览器窗口。登录状态仅保存在本机独立 profile；不导出 Cookie。", file=sys.stderr)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(str(profile), channel=channel, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(sites[platform], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_event("close", timeout=600000)
            finally:
                context.close()
    except Exception:
        raise SafeError("login_browser_failed", "登录浏览器未正常完成。请检查浏览器安装或 profile 占用；登录是否成功需要后续提取确认。") from None
    return {"status": "browser_closed", "platform": platform, "login_verified": False}
