"""Acquire one public video without persisting credentials or extractor metadata."""
from __future__ import annotations

import math
import os
from pathlib import Path
import re
import shutil
import time
import urllib.parse
import urllib.request

MAX_MEDIA = 512 * 1024 * 1024
MAX_CAPTION = 4 * 1024 * 1024
MAX_DURATION = 3600
PAGE_HOSTS = {
    "douyin": {"douyin.com", "www.douyin.com", "v.douyin.com", "www.iesdouyin.com"},
    "xiaohongshu": {"xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com"},
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
}
ASSET_DOMAINS = {
    "douyin": ("douyinvod.com", "douyincdn.com", "douyin.com", "bytecdn.cn", "ibytedtos.com", "amemv.com"),
    "xiaohongshu": ("xhscdn.com", "xhscdn.net", "xiaohongshu.com"),
    "youtube": ("youtube.com", "googlevideo.com", "ytimg.com"),
}


class SafeError(RuntimeError):
    """A public error whose message never includes remote URLs or credentials."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_url(url: str, platform: str | None = None, *, asset: bool = False):
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        invalid = (parsed.scheme != "https" or parsed.username is not None
                   or parsed.password is not None or parsed.port not in (None, 443)
                   or any(ord(char) < 33 for char in url) or "\\" in url)
    except (ValueError, TypeError):
        invalid, host = True, ""
    if invalid:
        raise SafeError("invalid_url", "需要不含账户信息的 HTTPS 视频链接。")
    if asset:
        allowed = platform in ASSET_DOMAINS and any(
            host == domain or host.endswith("." + domain) for domain in ASSET_DOMAINS[platform])
    else:
        allowed = host in PAGE_HOSTS.get(platform, set()) if platform else any(
            host in hosts for hosts in PAGE_HOSTS.values())
    if not allowed:
        raise SafeError("unsupported_host", "链接或重定向域名不在允许的网站范围。")
    return parsed


def normalize_asset(url: str, platform: str) -> str:
    """Upgrade known legacy Xiaohongshu CDN links; never send an HTTP request."""
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "http":
            host = (parsed.hostname or "").lower()
            if (platform != "xiaohongshu" or not (host == "xhscdn.com" or host.endswith(".xhscdn.com"))
                    or parsed.username is not None or parsed.password is not None
                    or parsed.port not in (None, 80)
                    or any(ord(char) < 33 for char in url) or "\\" in url):
                raise SafeError("unsupported_host", "拒绝升级非允许 CDN 或带账户信息/异常端口的媒体地址。")
            # Drop the explicit HTTP default port; retain the resource path and query byte-for-byte.
            url = urllib.parse.urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))
    except (ValueError, TypeError):
        raise SafeError("invalid_url", "媒体地址格式无效。") from None
    validate_url(url, platform, asset=True)
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, platform: str, *, asset: bool = False):
        self.platform, self.asset = platform, asset

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl, self.platform, asset=self.asset)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def normalize_url(url: str) -> dict:
    """Keep access tokens in memory only; output a stable identity URL."""
    parsed = validate_url(url)
    platform = next(key for key, hosts in PAGE_HOSTS.items() if parsed.hostname.lower() in hosts)
    if parsed.hostname.lower() in {"v.douyin.com", "xhslink.com", "www.xhslink.com"}:
        try:
            opener = urllib.request.build_opener(_Redirect(platform))
            with opener.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as response:
                url = response.geturl()
            parsed = validate_url(url, platform)
        except SafeError:
            raise
        except Exception:
            raise SafeError("resolve_failed", "短链接无法解析；请复制浏览器中具体视频的完整链接。") from None
    query = urllib.parse.parse_qs(parsed.query)
    notice = None
    if platform == "douyin":
        match = re.fullmatch(r"/(?:share/)?video/(\d+)/?", parsed.path)
        candidates = [query.get("modal_id", [None])[0], match.group(1) if match else None,
                      query.get("vid", [None])[0]]
        ident = next((item for item in candidates if item), "")
        if not re.fullmatch(r"\d{5,30}", ident):
            raise SafeError("missing_video_id", "抖音链接需要具体视频 ID，不能只提供作者主页。")
        if len(set(item for item in candidates if item)) > 1:
            notice = "链接含多个作品 ID；已按 modal_id 指定的当前作品选择，并校验实际返回 ID。"
        canonical = "https://www.douyin.com/video/" + ident
        fetch_url = canonical
    elif platform == "xiaohongshu":
        match = re.fullmatch(r"/(?:explore|discovery/item)/([a-fA-F0-9]{24})/?", parsed.path)
        if not match:
            raise SafeError("missing_video_id", "小红书链接需要具体笔记 ID。")
        ident = match.group(1).lower()
        canonical = "https://www.xiaohongshu.com/explore/" + ident
        # These fields can be needed for authorized public-page access; never serialize them.
        access = {key: query[key][0] for key in ("xsec_token", "xsec_source") if key in query}
        fetch_url = canonical + ("?" + urllib.parse.urlencode(access) if access else "")
    else:
        if parsed.hostname == "youtu.be":
            ident = parsed.path.strip("/")
        elif parsed.path == "/watch":
            ident = query.get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|embed)/([A-Za-z0-9_-]{11})/?", parsed.path)
            ident = match.group(1) if match else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident):
            raise SafeError("missing_video_id", "YouTube 链接需要具体视频 ID，不能只提供播放列表。")
        canonical = "https://www.youtube.com/watch?v=" + ident
        fetch_url = canonical
    return {"platform": platform, "id": ident, "source_url": canonical,
            "fetch_url": fetch_url, "selection_note": notice}


class _QuietLogger:
    def debug(self, message):
        pass

    warning = error = info = debug


def _bounded_progress(status):
    if max(status.get("downloaded_bytes") or 0, status.get("total_bytes") or 0) > MAX_MEDIA:
        raise SafeError("media_too_large", "媒体超过 512 MiB 上限。")


def _options(platform: str, output_dir: Path) -> dict:
    return {
        "quiet": True, "no_warnings": True, "logger": _QuietLogger(),
        "noprogress": True, "ignoreconfig": True, "noplaylist": True,
        "allowed_extractors": [{"youtube": "youtube$", "douyin": "douyin$", "xiaohongshu": "xiaohongshu$"}[platform]],
        "cachedir": False, "cookiefile": None, "cookiesfrombrowser": None,
        "usenetrc": False, "netrc_location": None, "writeinfojson": False,
        "writethumbnail": False, "writesubtitles": False, "writeautomaticsub": False,
        "postprocessors": [], "post_hooks": [], "exec_cmd": {},
        "external_downloader": None, "remote_components": [],
        "js_runtimes": {"node": {}} if shutil.which("node") else {},
        "socket_timeout": 20, "retries": 1, "fragment_retries": 1,
        "extractor_retries": 1, "max_filesize": MAX_MEDIA,
        "progress_hooks": [_bounded_progress], "overwrites": False,
        "outtmpl": str(output_dir / "media.bin"),
        "format": "bestaudio[protocol=https]/best[protocol=https]", "fixup": "never",
    }


def _duration(info: dict) -> float | None:
    values = [info.get("duration")] + [entry.get("duration") for entry in info.get("formats", [])]
    numbers = [float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(value) and value > 0]
    value = max(numbers) if numbers else None
    if value and value > MAX_DURATION:
        raise SafeError("duration_limit", "第一版仅处理不超过 60 分钟的视频。")
    return value


def _caption(info: dict):
    """Select an original-language track, never a guessed automatic translation."""
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    originals = [(key[:-5], formats) for key, formats in automatic.items() if key.endswith("-orig")]
    language = info.get("language") or (originals[0][0] if len(originals) == 1 else None)
    choices = []
    if language:
        choices.extend((manual.get(key), "provided") for key in (language, language.split("-")[0]))
    if language:
        choices.append((automatic.get(language + "-orig"), "automatic_original"))
    if len(originals) == 1:
        choices.append((originals[0][1], "automatic_original"))
    # A sole track without contrary language evidence is usable with an explicit caveat.
    if not language and len(manual) == 1:
        choices.append((next(iter(manual.values())), "provided_language_unverified"))
    for formats, kind in choices:
        for extension in ("json3", "srt", "vtt"):
            for entry in formats or []:
                if entry.get("ext") == extension and (entry.get("url") or entry.get("data")):
                    return entry, extension, kind
    return None


def download_asset(url: str, output: Path, platform: str, limit: int, headers: dict | None = None):
    """HTTPS + CDN allowlist on every redirect; cap actual bytes, not only headers."""
    url = normalize_asset(url, platform)
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    created = False
    deadline = time.monotonic() + (120 if limit <= MAX_CAPTION else 600)
    try:
        with urllib.request.build_opener(_Redirect(platform, asset=True)).open(request, timeout=20) as response:
            validate_url(response.geturl(), platform, asset=True)
            with output.open("xb") as handle:
                created = True
                size = 0
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if time.monotonic() > deadline:
                        raise SafeError("download_timeout", "下载超过时间上限，请检查网络后重试。")
                    if size > limit:
                        raise SafeError("download_limit", "下载超过大小上限。")
                    handle.write(chunk)
        if not size:
            raise SafeError("empty_download", "下载结果为空，不能当作提取成功。")
    except Exception:
        if created:
            output.unlink(missing_ok=True)
        raise


def name_media(download: Path) -> Path:
    """Identify a supported binary container, never a manifest disguised as media."""
    with download.open("rb") as handle:
        prefix = handle.read(32)
    extension = None
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        extension = "mp4"
    elif prefix.startswith(b"\x1a\x45\xdf\xa3"):
        extension = "webm"
    elif prefix.startswith(b"OggS"):
        extension = "ogg"
    elif prefix.startswith(b"fLaC"):
        extension = "flac"
    elif prefix.startswith(b"RIFF") and prefix[8:12] == b"WAVE":
        extension = "wav"
    elif prefix.startswith(b"ID3"):
        extension = "mp3"
    elif len(prefix) >= 2 and prefix[0] == 255 and prefix[1] & 0xF6 == 0xF0:
        extension = "aac"
    elif len(prefix) >= 2 and prefix[0] == 255 and prefix[1] & 0xE0 == 0xE0:
        extension = "mp3"
    if extension is None:
        download.unlink(missing_ok=True)
        raise SafeError("unsupported_container", "下载结果不是支持的媒体容器，不能当作音频转写。")
    destination = download.with_name("media." + extension)
    if destination.exists():
        raise SafeError("output_exists", "媒体输出已存在，已停止覆盖。")
    download.rename(destination)
    return destination


def _yt_acquire(target: dict, output_dir: Path) -> dict:
    # Python API does not load yt-dlp CLI configuration. Disable ambient third-party plugins too.
    os.environ["YTDLP_NO_PLUGINS"] = "1"
    try:
        import yt_dlp
    except ImportError:
        raise SafeError("missing_dependency", "缺少 yt-dlp；请先安装项目依赖。") from None
    try:
        with yt_dlp.YoutubeDL(_options(target["platform"], output_dir)) as downloader:
            info = downloader.extract_info(target["fetch_url"], download=False, process=False)
            if not isinstance(info, dict) or info.get("_type", "video") != "video":
                raise SafeError("not_single_video", "返回的不是单个视频。")
            if str(info.get("id")) != target["id"]:
                raise SafeError("video_mismatch", "返回作品 ID 与指定视频不一致，已停止提取。")
            duration = _duration(info)
            result = {key: target[key] for key in ("platform", "id", "source_url")}
            result.update(title=info.get("title") or "", author=info.get("uploader") or info.get("channel") or "",
                          duration=duration, media_path=None, caption_path=None,
                          caption_format=None, caption_kind=None, selection_note=target["selection_note"])
            selected = _caption(info)
            if selected:
                entry, extension, kind = selected
                output = output_dir / ("captions." + extension)
                if entry.get("data"):
                    payload = entry["data"].encode("utf-8")
                    if len(payload) > MAX_CAPTION:
                        raise SafeError("caption_limit", "字幕超过 4 MiB 上限。")
                    with output.open("xb") as handle:
                        handle.write(payload)
                else:
                    download_asset(entry["url"], output, target["platform"], MAX_CAPTION,
                                   {"User-Agent": "Mozilla/5.0", "Referer": target["source_url"]})
                result.update(caption_path=output, caption_format=extension, caption_kind=kind)
                return result
            # Ask the extractor to select one direct file, then download through our
            # bounded HTTPS reader. No ffmpeg, manifest, merger or downloader plugin runs.
            direct = []
            for item in info.get("formats", []):
                try:
                    normalized = normalize_asset(item.get("url"), target["platform"])
                except SafeError:
                    continue
                if item.get("protocol") in (None, "http", "https") and not item.get("fragments"):
                    direct.append({**item, "url": normalized, "protocol": "https"})
            if info.get("formats"):
                if not direct:
                    raise SafeError("no_direct_media", "未找到允许 CDN 上的单文件媒体；请使用浏览器模式或本地视频。")
                info["formats"] = direct
            selected = downloader.process_ie_result(info, download=False)
            media_url = selected.get("url") if isinstance(selected, dict) else None
            if not media_url or selected.get("requested_formats") or selected.get("fragments"):
                raise SafeError("no_direct_media", "该媒体需要分片或合并，当前安全获取模式不支持；请使用本地视频。")
            media = output_dir / "media.download"
            download_asset(media_url, media, target["platform"], MAX_MEDIA,
                           {"User-Agent": "Mozilla/5.0", "Referer": target["source_url"]})
            if not media.is_file() or not 0 < media.stat().st_size <= MAX_MEDIA:
                raise SafeError("missing_media", "未取得有效大小的媒体文件。")
            result["media_path"] = name_media(media)
            return result
    except SafeError:
        raise
    except Exception:
        raise SafeError("acquisition_failed", "未能读取该视频。可能需要登录、平台验证，或当前下载器不支持；可显式使用浏览器模式重试。") from None


def acquire(url: str, output_dir: Path, browser: bool = False,
            profile_dir: Path | None = None, channel: str = "chrome") -> dict:
    target = normalize_url(url)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise SafeError("output_not_empty", "获取目录必须为空，避免覆盖或混入旧视频。")
    # This extractor currently omits Xiaohongshu's mediaV2 captions. An explicit
    # browser request checks those original captions before falling back to ASR.
    if browser and target["platform"] == "xiaohongshu":
        if profile_dir is None:
            raise SafeError("missing_profile", "浏览器模式需要应用专属 profile 目录。")
        from .browser import acquire_browser
        return acquire_browser(target, output_dir, Path(profile_dir), channel)
    try:
        return _yt_acquire(target, output_dir)
    except SafeError as error:
        # Identity and limit failures must never be hidden by a fallback.
        if not browser or error.code not in {"acquisition_failed", "missing_dependency", "no_direct_media"}:
            raise
        for name in ("media.download", "captions.json3", "captions.srt", "captions.vtt"):
            (output_dir / name).unlink(missing_ok=True)
        if profile_dir is None:
            raise SafeError("missing_profile", "浏览器模式需要应用专属 profile 目录。") from None
        from .browser import acquire_browser
        return acquire_browser(target, output_dir, Path(profile_dir), channel)
