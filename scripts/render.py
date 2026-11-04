#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render.py — 把指定印刷页码渲染成 PNG，供目视复核。

用途：双栏排版的文本层会把左右栏串行，凡涉及版式、编号、断句、表格对齐的结论，
必须先看图再决定是否上报。

用法:
    python render.py <file.pdf> 23,39,72 -o pages
    python render.py <file.pdf> 23-26 --dpi 130
    python render.py <file.pdf> 23 --offset 12      # 显式指定页码偏移

页码一律按**印刷页码**输入；偏移默认自动推断，与 scan.py 一致。
"""
import argparse
import os
import sys

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import guess_offset, load_pages


def parse_pages(s):
    out = []
    for part in s.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('pages')
    ap.add_argument('-o', '--outdir', default='pages')
    ap.add_argument('--dpi', type=int, default=130)
    ap.add_argument('--offset', type=int, default=None)
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    if args.offset is not None:
        offset = args.offset
    else:
        offset, _ = guess_offset(load_pages(doc))
    os.makedirs(args.outdir, exist_ok=True)

    made = []
    for printed in parse_pages(args.pages):
        idx = printed + offset - 1
        if not (0 <= idx < doc.page_count):
            print('跳过 p%d（超出范围）' % printed)
            continue
        path = os.path.join(args.outdir, 'p%d.png' % printed)
        doc[idx].get_pixmap(dpi=args.dpi).save(path)
        made.append(path)

    print('偏移 %d，已渲染 %d 页：' % (offset, len(made)))
    for p in made:
        print('  ' + os.path.abspath(p))


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
