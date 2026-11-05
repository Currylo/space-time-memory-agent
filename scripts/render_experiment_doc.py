#!/usr/bin/env python3
"""Regenerate the experiment HTML from Markdown, preserving its existing CSS."""
from html import escape
from pathlib import Path
import re

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[2]
source = ROOT / "时空记忆智能体-分阶段实验文档.md"
destination = source.with_suffix(".html")
markdown = source.read_text(encoding="utf-8")
old_html = destination.read_text(encoding="utf-8")
style = re.search(r"<style>(.*?)</style>", old_html, flags=re.S)
if not style:
    raise ValueError("Existing experiment stylesheet is missing")
version = re.search(r"版本：([^（｜\s]+)", markdown).group(1)
updated = re.search(r"更新日期：(\d{4}-\d{2}-\d{2})", markdown).group(1)
renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
tokens = renderer.parse(markdown)
links = []
for index, token in enumerate(tokens):
    if token.type == "heading_open" and token.tag in ("h2", "h3"):
        anchor = f"section-{len(links) + 1}"
        token.attrSet("id", anchor)
        label = tokens[index + 1].content
        links.append(f'<a class="level-{token.tag[1]}" href="#{anchor}">{escape(label)}</a>')
body = renderer.renderer.render(tokens, renderer.options, {})
body = body.replace("<table>", '<div class="table-scroll"><table>').replace("</table>", "</table></div>")
html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>时空记忆智能体 · 分阶段实验文档 {escape(version)}</title><style>{style.group(1)}</style></head><body>
<!-- Generated from the experiment Markdown. The teacher's original full-plan HTML is not modified. -->
<aside><div class="label">实验执行与证据</div><div class="brand">时空记忆智能体</div><nav>{''.join(links)}</nav></aside>
<main><div class="topline">{escape(version)} · {updated}</div>{body}
<div class="footer">Markdown 为可编辑源文件，HTML 为同步阅读版。{escape(version)} · {updated} · 由 <code>{escape(source.name)}</code> 生成。</div></main></body></html>
'''
destination.write_text(html, encoding="utf-8")
print(f"Rendered {destination.name}: {version}, {len(links)} section anchors")
