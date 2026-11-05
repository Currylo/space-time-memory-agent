#!/usr/bin/env python3
"""Render the same-source platform execution report to a standalone HTML page."""
from __future__ import annotations

from html import escape
from pathlib import Path
import argparse
import re

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, default=ROOT / "reports" / "同源平台闭环执行报告.md")
args = parser.parse_args()
SOURCE = args.source
DESTINATION = SOURCE.with_suffix(".html")
MARKDOWN = SOURCE.read_text(encoding="utf-8")
TITLE = SOURCE.stem
renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
tokens = renderer.parse(MARKDOWN)
links = []
for index, token in enumerate(tokens):
    if token.type == "heading_open" and token.tag in ("h2", "h3"):
        anchor = f"section-{len(links) + 1}"
        token.attrSet("id", anchor)
        label = tokens[index + 1].content
        links.append((token.tag, anchor, label))
body = renderer.renderer.render(tokens, renderer.options, {})
body = body.replace("<table>", '<div class="table-scroll"><table>').replace("</table>", "</table></div>")
nav = "".join(
    f'<a class="level-{tag[1]}" href="#{anchor}">{escape(label)}</a>'
    for tag, anchor, label in links
)
html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(TITLE)} · 时空记忆智能体</title>
<style>
:root{{--ink:#17313f;--muted:#647b88;--line:#dce6ea;--paper:#f3f7f8;--teal:#188d86;--blue:#226885}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth;scroll-padding-top:20px}}
body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.75 "Noto Sans CJK SC","Microsoft YaHei",sans-serif}}
aside{{position:fixed;inset:0 auto 0 0;width:280px;background:#102c3b;color:#d9e9ee;padding:24px 18px;overflow:auto}}
aside .label{{font-size:10px;letter-spacing:2px;color:#83b8c4;text-transform:uppercase}}
aside .brand{{font-size:20px;font-weight:700;margin:6px 0 18px}}
aside a{{display:block;color:#c4dce3;text-decoration:none;font-size:12px;padding:4px 0;border-left:2px solid transparent;padding-left:8px}}
aside a:hover{{color:#fff;border-left-color:#58c6c0}}
aside a.level-3{{padding-left:20px;font-size:11px;color:#9fc2cc}}
main{{margin-left:280px;max-width:1100px;padding:34px 42px 70px}}
h1{{font-size:30px;line-height:1.3;margin:0 0 12px}}h2{{font-size:22px;margin:34px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}}h3{{font-size:17px;margin:24px 0 8px}}
p{{margin:8px 0}}a{{color:var(--blue)}}code{{background:#e8f0f2;padding:1px 4px;border-radius:3px;font-size:.92em}}
pre{{background:#0d2431;color:#dceef2;padding:14px 16px;border-radius:7px;overflow:auto;font-size:12px;line-height:1.6}}
blockquote{{margin:12px 0;padding:9px 14px;border-left:4px solid var(--teal);background:#e9f4f2;color:#35545c}}
.table-scroll{{overflow-x:auto;margin:12px 0}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:12px;min-width:620px}}
th,td{{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#e8f1f3;font-weight:650}}tr:nth-child(even) td{{background:#fbfdfe}}
.footer{{margin-top:40px;padding-top:12px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}}
@media(max-width:900px){{aside{{position:static;width:auto}}main{{margin:0;padding:20px 14px 50px}}}}
@media print{{aside{{display:none}}main{{margin:0;max-width:none}}body{{background:#fff}}}}
</style></head><body>
<aside><div class="label">执行与证据</div><div class="brand">同源平台闭环</div><nav>{nav}</nav></aside>
<main>{body}
<div class="footer">Markdown 为可编辑源文件，HTML 为同步阅读版。生成自 <code>{escape(SOURCE.name)}</code>。本报告先等待审核，不表示审核已通过。</div></main></body></html>
'''
DESTINATION.write_text(html, encoding="utf-8")
print(f"Rendered {DESTINATION.name}: {len(links)} section anchors")
