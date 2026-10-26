# 有依据的校对

每个任务保存 `raw.json`、`review-input.json`、`result.json` 和 `text/` 下的纯文本、时间戳稿及 Markdown 稿。模型原始结果和字幕原文不被校对覆盖。`raw.json` 的 `segments` 含 `id/start/end/text`。

只对有实际证据的词句制作校对记录：

```json
{
  "source": {"title": "视频标题", "url": "", "author": ""},
  "edits": [
    {"segment_id": 0, "before": "原始片段全文", "after": "校对后全文", "reason": "说明修改依据", "evidence": ["已核对的原声时间段或字幕片段编号"]}
  ],
  "uncertainties": [
    {"segment_id": 1, "note": "读音不清，暂保留原文，需复听"}
  ]
}
```

`before` 必须逐字匹配，包括首尾空格。未修改的段落不放入 edits。删除也需证据，不能批量去除数字、短句或重复句。证据字段不能虚构；只有原词形的大小写调整可以引用实际原始片段作为格式依据，不称作复听确认。

在安装后的环境中运行：

```text
python scripts/vce.py review --raw RAW_FILE --review REVIEW_FILE --output-dir NEW_DIRECTORY
```

输出目录必须不存在。错误输入不创建结果；源稿保留，输出记录原始 SHA-256。疑点单独列出，不混入纯正文。结果仍标记准确性未经认证。

分享记录前删去私有链接、文件路径、账号或其他个人信息。发布插件时不要打包任何真实校对产物。
