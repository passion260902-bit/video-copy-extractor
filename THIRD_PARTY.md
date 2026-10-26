# 第三方组件与维护

本仓库包含自行编写的封装代码和说明，不复制或捆绑其他 Skill，不包含浏览器、模型权重或第三方可执行文件。依赖通过安装脚本从公开分发服务安装；各自许可仍适用，顶层 MIT 不替代依赖许可。

| 组件 | 用途 | 原始项目 |
|---|---|---|
| yt-dlp | 三平台公开链接的元数据与媒体格式解析 | https://github.com/yt-dlp/yt-dlp |
| faster-whisper / CTranslate2 | 本地 CPU 语音识别 | https://github.com/SYSTRAN/faster-whisper 、https://github.com/OpenNMT/CTranslate2 |
| PyAV | 本地媒体音频解码，发行包含 FFmpeg 库 | https://github.com/PyAV-Org/PyAV |
| Playwright | 用户明确启用时连接本机 Chrome / Edge 的独立 profile | https://github.com/microsoft/playwright-python |
| MCP Python SDK | 本地 stdio 工具接口 | https://github.com/modelcontextprotocol/python-sdk |
| Hugging Face Hub | 用户明确执行命令时下载模型 | https://github.com/huggingface/huggingface_hub |

具体直接与间接依赖版本及分发文件哈希见 `uv.lock`、`requirements-lock.txt`。安装脚本要求预编译 wheel 与哈希校验，下载地址是公开 PyPI。版本锁定用于可复现，不代表无漏洞；维护者仍需跟进上游安全更新并复测。

更新依赖时先修改 `pyproject.toml`，用 uv 重新锁定并导出，再运行 Windows/Mac CI 与人工平台 smoke test：

```sh
uv lock
uv export --locked --all-extras --no-emit-project --no-header --format requirements-txt --output-file requirements-lock.txt
```

模型不在发布包里，遵守对应模型卡许可；不要附带账号令牌、开发者缓存模型或用户媒体。若将来捆绑第三方二进制，需单独检查各组件和 FFmpeg 构建的再分发要求。
