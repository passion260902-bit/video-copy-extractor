"""Command-line entry point. No installer or network action runs at import time."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys

from . import core


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="本地视频文案提取；详情见 README.md")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="离线环境检查")
    commands.add_parser("mcp", help="启动本地 stdio MCP 服务")
    model = commands.add_parser("download-model", help="显式从 Hugging Face 下载模型，不上传音视频")
    model.add_argument("--name", choices=("base", "small", "large-v3-turbo"), default="large-v3-turbo")
    model.add_argument("--output", type=Path)
    browser = commands.add_parser("login", help="打开独立浏览器，由用户自行登录")
    browser.add_argument("--channel", choices=("chrome", "msedge"), default="chrome")
    browser.add_argument("--platform", choices=("douyin", "xiaohongshu", "youtube"), required=True)
    for command in ("extract", "local"):
        sub = commands.add_parser(command, help="提取链接" if command == "extract" else "处理本地媒体或字幕")
        if command == "extract":
            group = sub.add_mutually_exclusive_group(required=True)
            group.add_argument("--url")
            group.add_argument("--url-stdin", action="store_true", help="从标准输入读 URL，避免链接写入 shell 历史")
            sub.add_argument("--browser", action="store_true", help="允许使用本插件的独立浏览器登录态")
            sub.add_argument("--channel", choices=("chrome", "msedge"), default="chrome")
        else:
            sub.add_argument("path", type=Path)
        sub.add_argument("--model", type=Path)
        sub.add_argument("--language", help="如 zh/en；省略自动识别，只影响 ASR")
        sub.add_argument("--output-dir", type=Path, help="任务目录的父目录；默认保存在用户应用数据目录")
    review = commands.add_parser("review", help="按证据应用逐段校对，不覆盖原稿")
    review.add_argument("--raw", type=Path, required=True)
    review.add_argument("--review", type=Path, required=True)
    review.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "mcp":
            from .mcp_server import main as serve
            return serve()
        if args.command == "doctor":
            result = core.doctor()
        elif args.command == "download-model":
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
            from huggingface_hub import snapshot_download
            repositories = {"base": "Systran/faster-whisper-base", "small": "Systran/faster-whisper-small",
                            "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}
            target = args.output or core.data_dir() / "models" / args.name
            with contextlib.redirect_stdout(sys.stderr):
                snapshot_download(repositories[args.name], local_dir=str(target), token=False,
                                  allow_patterns=["config.json", "preprocessor_config.json", "model.bin",
                                                  "tokenizer.json", "vocabulary.*"])
            result = {"status": "model_downloaded", "name": args.name, "directory": str(target)}
        elif args.command == "login":
            from .browser import open_browser
            open_browser(core.data_dir() / "browser-profile", args.channel, args.platform)
            result = {"status": "browser_closed", "login_verified": False}
        elif args.command == "review":
            from .review import apply_review
            apply_review(args.raw, args.review, args.output_dir)
            result = {"status": "review_applied", "accuracy_verified": False, "output_dir": str(args.output_dir)}
        else:
            options = {"model": args.model, "language": args.language, "output_root": args.output_dir}
            with contextlib.redirect_stdout(sys.stderr):
                if args.command == "extract":
                    url = sys.stdin.readline(16385).strip() if args.url_stdin else args.url
                    result = core.extract_video(url, browser=args.browser, channel=args.channel, **options)
                else:
                    result = core.extract_local(args.path, **options)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        print('{"status":"cancelled"}', file=sys.stderr)
        return 130
    except Exception as error:
        # Third-party exceptions often include signed URLs, cookies or local paths.
        from .acquire import SafeError
        safe = isinstance(error, (core.ExtractionError, SafeError))
        result = {"status": "failed", "code": getattr(error, "code", "processing_failed") if safe else "processing_failed",
                  "message": str(error) if safe else "处理失败；请运行 doctor 并检查依赖、模型、输入文件及平台访问状态。原始异常未输出，以免泄露路径或签名链接。"}
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
