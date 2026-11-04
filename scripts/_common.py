#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_common.py — scan.py 与 render.py 共享的页码偏移推断。

统一用 sort=True 提取文本并推断偏移，保证两个脚本对同一 PDF 算出相同偏移，
避免 render 按印刷页码渲染时错页。
"""
import re
from collections import Counter

NUM_LINE = re.compile(r'^\s*(\d{1,4})\s*$')


def load_pages(doc):
    """每页按坐标排序的文本（sort=True，接近视觉阅读顺序）。"""
    return [p.get_text('text', sort=True) for p in doc]


def guess_offset(pages):
    """用页脚数字反推印刷页码偏移，取众数。返回 (offset, votes)。"""
    diffs = Counter()
    for i, t in enumerate(pages):
        lines = [l.strip() for l in t.strip().split('\n') if l.strip()]
        if not lines:
            continue
        for cand in reversed(lines[-3:]):
            m = NUM_LINE.match(cand)
            if m:
                footer = int(m.group(1))
                if 1 <= footer <= len(pages):
                    diffs[(i + 1) - footer] += 1
                break
    if not diffs:
        return 0, 0
    return diffs.most_common(1)[0]
