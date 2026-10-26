# 视频文案提取 Skill + MCP

从小红书、抖音、YouTube 的具体视频链接提取字幕，或将视频音频在本机转成逐字稿。包含 Skill 操作流程、跨平台 Python 程序、MCP 接口和有依据的校对工具。

**适配对象是能运行工具的 AI 客户端。** 将文件上传到只能聊天的网页或手机 App，并不会增加执行程序、浏览器或本地文件权限。模型品牌不决定兼容性：宿主必须支持本地 Skill/命令执行，或本地 stdio MCP。仅支持远程 HTTP MCP 的客户端也不能直接连接这一版。

这是 0.1.0 首版。按 Windows x64 与 macOS（Apple Silicon / Intel）设计，建议 Python 3.11、Windows 11 / macOS 14 或更新系统。已做与未做的验证见 [VALIDATION.md](VALIDATION.md)。Windows 真机验证完成前，不应宣传所有 Windows 环境都已通过实测。

## 能力与限制

| 输入 | 处理方式 |
|---|---|
| YouTube 具体视频链接 | 优先原语言字幕；无字幕时下载音频并本地转写 |
| 小红书视频笔记链接 | 下载器获取媒体；启用浏览器时额外读取页面原始字幕 |
| 抖音具体视频链接 | 下载器获取；受限时可启用独立浏览器读取指定作品 |
| 本地音视频 / SRT、VTT、JSON3 | 不需要平台账号；本地转写或解析字幕 |

平台改版、验证、地区限制、私密或删除视频可能阻断获取。第一版只接受允许平台及其 CDN 的 HTTPS 链接、单个文件媒体，不支持直播、整份播放列表或需要分片合并的媒体；单视频最多 60 分钟、媒体最多 512 MiB、字幕最多 4 MiB。超限会明确报错。

字幕和语音识别都可能错字或缺句；完成提取不代表准确率或完整性为 100%。保留原稿、时间戳与疑点，实质修改需原声或字幕证据。只提取用户有权访问和处理的内容。

## 首次安装

1. 从项目发布包解压整个 `video-copy-extractor` 文件夹，放在自己的普通本地目录。不要只取 `SKILL.md`。
2. 从 [Python 官方网站](https://www.python.org/downloads/) 安装 64 位 Python 3.11（也支持 3.12 / 3.13）。
3. 在 Windows 运行 `setup-windows.cmd`；在 Mac 的终端进入解压目录，运行 `bash setup-macos.command`。

安装脚本仅建立项目内的 `.venv`，从公开 PyPI 安装 `requirements-lock.txt` 中锁定并校验哈希的依赖，不改系统 Python，不下载语音模型或浏览器。首次需联网及足够磁盘空间。完整依赖包含本地 ASR、浏览器适配和 MCP。若某系统无对应预编译包，安装会停止，不会偷偷编译或修改系统依赖。

下文在项目目录执行。Windows 的运行命令为：

```powershell
.\.venv\Scripts\python.exe scripts\vce.py doctor
```

Mac 对应为：

```sh
.venv/bin/python scripts/vce.py doctor
```

为简洁，后续示例写作 `python scripts/vce.py ...`；请将 `python` 替换成上面对应的 `.venv` 解释器。

## 连接 AI 客户端

**支持本地 Skill 和命令执行的客户端：** 按该客户端自己的 Skill 安装方式选择完整文件夹，保留目录结构；让它读取 `SKILL.md` 并使用本项目 `.venv`。不同客户端的 Skill 安装目录可能不同，不要复制作者的电脑路径。

**支持本地 stdio MCP 的客户端：** 在该客户端的 MCP 配置中添加下面配置，并将两处路径换成自己的实际绝对路径。Windows 路径在 JSON 中使用正斜杠也可避免反斜杠转义。

```json
{
  "mcpServers": {
    "video-copy-extractor": {
      "command": "ABSOLUTE_PATH_TO_VENV_PYTHON",
      "args": ["ABSOLUTE_PATH_TO_PROJECT/scripts/vce.py", "mcp"]
    }
  }
}
```

客户端应能列出 `doctor`、`extract_video`、`read_transcript` 三个工具。先调用 `doctor`，再发送视频链接。CPU 转写可能需数分钟或更久，需在客户端设置足够的工具超时；进度消息只报告实际耗时。长稿按片段分页，必须读取到 `has_more=false` 才取得全部返回文本。

MCP 仅使用标准输入输出，不开放网络监听端口。它不提供通用命令执行或任意本地文件读取工具。模型路径、输出位置和浏览器权限由部署者配置，模型不能通过工具参数更改。

## 提取与模型准备

```sh
python scripts/vce.py extract --url "VIDEO_URL"
python scripts/vce.py local "LOCAL_SUBTITLE.srt"
```

有可用字幕时不需要语音模型。没有字幕时，首次由用户明确运行：

```sh
python scripts/vce.py download-model --name large-v3-turbo
```

此命令从 Hugging Face 下载 CTranslate2 语音模型，只下载，不上传音视频；文件较大，耗时依网络而定，CPU 运行速度与内存用量依机器而定。默认准备 `large-v3-turbo` 以偏向质量。`base` / `small` 可作资源较少时的选择，中文及术语准确性可能较低；不要把小模型测试结果等同于大模型质量。

默认模型在用户应用数据目录内，后续自动识别；自备模型必须包含 `config.json`、`model.bin`、`tokenizer.json`，支持的格式是 faster-whisper/CTranslate2，不能直接使用 MLX、GGUF 或普通 PyTorch 模型。

```sh
python scripts/vce.py local "LOCAL_VIDEO.mp4" --model "LOCAL_MODEL_DIRECTORY" --language zh
```

也可由部署者在 MCP 的 `env` 中配置 `VCE_MODEL_PATH`。提取阶段不会自动下载模型，也不需要大模型 API 密钥。语音识别在本机 CPU 上执行，不依赖 Apple 专用框架、CUDA 或系统安装的 FFmpeg；PyAV 自带解码库。[实现依据](https://github.com/SYSTRAN/faster-whisper#requirements)

## 需要登录时

先安装本机 Chrome 或 Edge。浏览器适配使用 Playwright，但不会自动下载或覆盖浏览器。登录在本插件单独的浏览器资料目录中完成，不读取日常浏览器的 Cookies，也不导出 Cookie 文件。

```sh
python scripts/vce.py login --platform douyin --channel chrome
python scripts/vce.py extract --url "VIDEO_URL" --browser --channel chrome
```

平台参数还可选 `xiaohongshu` / `youtube`；Edge 的 channel 是 `msedge`。也可直接用 `--browser` 开始提取，在弹出的窗口自行完成登录或验证。不得绕过验证码或权限限制。

MCP 默认禁用浏览器；部署者允许时在 MCP 配置的 `env` 加入 `"VCE_ENABLE_BROWSER":"1"`，可用 `"VCE_BROWSER_CHANNEL":"msedge"` 切换 Edge。只有 `--browser` 或此部署设置会允许提取流程使用登录态。

## 输出与隐私

每次任务生成独立编号目录。包含 `raw.json`、`result.json`、`review-input.json`，以及 `text/` 下的纯正文、时间戳稿、Markdown 稿和校对记录。已取得的媒体/字幕也保存在该任务内，供本地复核；不会覆盖旧结果。失败的临时任务会清理，不留下看似成功的目录。

默认存储位置按系统计算：Windows 的本地应用数据目录，Mac 的用户 `Library/Application Support`，其下均为 `video-copy-extractor`。这些路径在每位使用者的电脑上生成，**不在仓库内**。任务记录不保存分享 token、媒体签名 URL 或 Cookie；浏览器自身仍可能保存访问历史和登录凭据，应视作私密数据。

插件没有自建遥测或音视频上传服务。平台访问会联系原平台及其 CDN，安装和模型下载会联系相应分发服务。使用 MCP 时，返回的文案会进入所连接的大模型客户端；聊天软件可能保存对话与用户提交的原始链接，是否接受由使用者按客户端隐私设置决定。不能将“本地转写”理解为“任何数据都不会离开电脑”。

不要提交 `.venv`、模型、任务结果、浏览器资料、用户配置、真实链接或逐字稿。 `.gitignore` 是辅助措施；正式发布使用下面的文件白名单打包。分享问题反馈时只给脱敏错误码、版本和复现步骤。

## 维护与公开发布

代码采用 [MIT](LICENSE) 许可；依赖、浏览器和模型分别遵循其自身许可，参见 [THIRD_PARTY.md](THIRD_PARTY.md)。发布包不包含第三方二进制或模型权重。依赖更新需要重新锁定、运行测试并复查平台实际行为，不能以一次成功承诺长期兼容。

```sh
python -m unittest discover -s tests -v
python scripts/pack_release.py --output ../video-copy-extractor-public.zip
```

打包仅收录 `release-files.txt` 中明确列出的源码和说明，拒绝符号链接及敏感文件，并对内容做隐私模式扫描；附带逐文件 SHA-256 清单。扫描通过不等于形式化安全证明，发布前仍应查看压缩包目录。应新建干净仓库，仅上传这一份公开包的内容，不上传上层工作目录或旧 Git 历史。

GitHub Actions 工作流会在公开仓库启用后运行 Windows、Mac 的安装与离线测试。它不携带任何开发者账号、不自动登录平台，也不使用真实视频作为公开测试样本。
