#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py — 校验 data.json 中原文摘录是否能在源 PDF 文本层找到。

用法:
    python validate.py data.json <file.pdf>

三态结果：精确命中 / 归一化后命中 / 未命中。
只打印警告，不写文件、不改动报告、不阻断出件。
归一化处理全半角（NFKC）、全部空白与常见中英标点差异。
双栏串行、图内文字等文本层缺陷会造成“未命中”，需人工看图判断，
不能直接判定为编造。

依赖: pymupdf
"""
import argparse
import io
import json
import os
import sys
import unicodedata

try:
    import pymupdf
except ImportError:  # 兼容旧版
    import fitz as pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import load_pages

PUNCT = str.maketrans({
    '，': ',', '。': '.', '：': ':', '；': ';', '！': '!', '？': '?',
    '（': '(', '）': ')', '【': '[', '】': ']', '“': '"', '”': '"',
    '‘': "'", '’': "'", '、': ',', '－': '-', '—': '-', '～': '~',
})


def norm(s):
    """全半角统一 + 标点对齐 + 去掉全部空白。"""
    s = unicodedata.normalize('NFKC', str(s or '')).translate(PUNCT)
    return ''.join(s.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('data')
    ap.add_argument('pdf')
    args = ap.parse_args()

    with io.open(args.data, encoding='utf-8') as f:
        data = json.load(f)
    doc = pymupdf.open(args.pdf)
    raw = '\n'.join(load_pages(doc))
    normalized = norm(raw)

    quotes = []
    for i, it in enumerate(data.get('items', []), start=1):
        q = it.get('quote', '')
        if q:
            quotes.append(('I%d' % i, it.get('loc', ''), q))
    for i, s in enumerate(data.get('suspects', []), start=1):
        q = s.get('quote', '')
        if q:
            quotes.append(('S%d' % i, s.get('loc', ''), q))

    exact, loose, miss = [], [], []
    for no, loc, q in quotes:
        if q.strip() in raw:
            exact.append(no)
        elif norm(q) and norm(q) in normalized:
            loose.append(no)
        else:
            miss.append((no, loc, q))

    print('摘录校验：共 %d 条 | 精确命中 %d | 归一化后命中 %d | 未命中 %d'
          % (len(quotes), len(exact), len(loose), len(miss)))
    if loose:
        print('  归一化后命中（空白／全半角／标点差异）：%s' % '、'.join(loose[:20]))
    if miss:
        print('  警告：以下摘录未在文本层找到（可能是双栏串行、图内文字或摘录不实，需人工核对）：')
        for no, loc, q in miss:
            print('    %s  %s  %s' % (no, loc, q[:60].replace('\n', ' ')))
    else:
        print('  无未命中条目。')
    print('  结果仅为警告，未写入报告，未改动任何文件。')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
