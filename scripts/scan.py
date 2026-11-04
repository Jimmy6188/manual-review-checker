#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan.py — 技术文档审校的机器扫描（只提供线索，不替代人工判断）

用法:
    python scan.py <file.pdf> [-o review.json] [--terms assets/terms.json]

输出六类线索:
    1. 印刷页码偏移（PDF 页序 - 页脚数字）
    2. 目录条目与正文实际标题页的偏差
    3. 占位扫描（XX/XXX/占位）
    4. 空页与短页
    5. 术语变体分布（按 terms.json 分组）
    6. 疑似文档阶段（占位密度过高 -> 疑似中途版）

依赖: pymupdf
"""
import argparse
import json
import os
import re
import sys

try:
    import pymupdf
except ImportError:  # 兼容旧版
    import fitz as pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import guess_offset, load_pages

PLACEHOLDER = re.compile(r'(占位|X{2,})')
FAULT_CODE = re.compile(r'P[Xx]{3,}')


def norm(s):
    return re.sub(r'[\s\u3000\ufffc\u00a0]+', '', s)


def check_toc(doc, pages, offset):
    """目录条目 vs 正文首次出现页。返回偏差列表（页码均为印刷页码）。"""
    toc = doc.get_toc()
    normed = [norm(t) for t in pages]
    start = max(offset, 0)
    out = []
    for level, title, pdf_page in toc:
        key = norm(title)
        if len(key) < 3:
            continue
        actual = None
        for i in range(start, len(normed)):
            if key in normed[i]:
                actual = i + 1
                break
        if actual and actual != pdf_page:
            out.append({
                'title': title.replace('\t', ' ').strip(),
                'level': level,
                'toc_page': pdf_page - offset,
                'actual_page': actual - offset,
            })
    return out


def scan_placeholders(pages, offset):
    by_page = {}
    for i, t in enumerate(pages):
        hits = []
        for line in t.split('\n'):
            s = line.strip()
            if not s:
                continue
            if FAULT_CODE.search(s):
                continue
            if PLACEHOLDER.search(s):
                hits.append(s[:80])
        if hits:
            by_page[i + 1 - offset] = hits
    return by_page


def scan_blank(pages, offset, limit=None, has_image=None):
    blank, short = [], []
    has_image = has_image if has_image is not None else [False] * len(pages)
    for i, t in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        if limit and printed > limit:
            continue
        n = len(t.strip())
        if n < 30:
            if has_image[i]:
                short.append(printed)  # 纯图页：有图不算空页，但字数极少仍提示
            else:
                blank.append(printed)
        elif n < 120:
            short.append(printed)
    return blank, short


def _is_cjk(c):
    return '\u4e00' <= c <= '\u9fff'


def _hit(text, word):
    """word 命中判定：纯汉字词须前后非汉字（避免"制动"命中"驻车制动"）；含字母数字的缩写保持子串匹配。"""
    if not all(_is_cjk(c) for c in word):
        return word in text
    for m in re.finditer(re.escape(word), text):
        s, e = m.start(), m.end()
        if (s == 0 or not _is_cjk(text[s - 1])) and (e == len(text) or not _is_cjk(text[e])):
            return True
    return False


def scan_terms(pages, offset, terms_path):
    if not terms_path or not os.path.exists(terms_path):
        return [], 'terms.json 未找到，跳过术语扫描'
    with open(terms_path, encoding='utf-8') as f:
        cfg = json.load(f)
    results = []
    for grp in cfg.get('synonym_groups', []):
        hits = {}
        for v in grp['variants']:
            pg = [i + 1 - offset for i, t in enumerate(pages) if _hit(t, v) and i + 1 - offset >= 1]
            if pg:
                hits[v] = pg
        if len(hits) >= 2:
            results.append({'label': grp.get('label', grp['id']), 'hits': hits})
    residue = []
    for item in cfg.get('residue_words', []):
        w = item['word']
        pg = [i + 1 - offset for i, t in enumerate(pages) if _hit(t, w) and i + 1 - offset >= 1]
        if pg:
            residue.append({'word': w, 'hint': item.get('hint', ''), 'pages': pg})
    return results, residue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('-o', '--out', default='review.json')
    ap.add_argument('--terms', default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'terms.json'))
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    pages = load_pages(doc)
    total = doc.page_count
    offset, votes = guess_offset(pages)

    # 正文页数：扣掉前置页与尾部完全空白的页（末页常无页码）
    tail_blank = 0
    for t in reversed(pages):
        if t.strip():
            break
        tail_blank += 1
    body_pages = max(total - offset - tail_blank, 1)

    toc_bad = check_toc(doc, pages, offset)
    ph = scan_placeholders(pages, offset)
    has_image = [len(p.get_images()) > 0 for p in doc]
    blank, short = scan_blank(pages, offset, body_pages, has_image)
    terms, residue = scan_terms(pages, offset, args.terms)

    density = len(ph) / body_pages
    stage_hint = 'interim?' if (density > 0.08 or len(blank) >= 2) else 'final?'

    data = {
        'file': os.path.abspath(args.pdf),
        'pages_pdf': total,
        'offset': offset,
        'offset_votes': votes,
        'printed_range': '1-%d' % body_pages,
        'toc_mismatch': toc_bad,
        'toc_mismatch_count': len(toc_bad),
        'placeholders': {'page_count': len(ph), 'density': round(density, 3), 'by_page': ph},
        'blank_pages': blank,
        'short_pages': short,
        'terms': terms,
        'residue': residue,
        'stage_hint': stage_hint,
    }
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    # ---- 摘要 ----
    print('文件: %s' % os.path.basename(args.pdf))
    print('PDF 页数: %d | 印刷页码偏移: %d（%d 页投票）| 正文页码: 1-%d'
          % (total, offset, votes, body_pages))
    print('目录偏差: %d 条' % len(toc_bad))
    for b in toc_bad[:8]:
        print('   %s  目录 %s → 实际 %s' % (b['title'][:28], b['toc_page'], b['actual_page']))
    if len(toc_bad) > 8:
        print('   … 其余 %d 条见 JSON' % (len(toc_bad) - 8))
    print('占位页: %d 页（密度 %.1f%%）' % (len(ph), density * 100))
    if ph:
        print('   ' + '、'.join('p%s' % k for k in list(ph)[:20]))
    if blank:
        print('空页: ' + '、'.join('p%s' % k for k in blank[:20]))
    if short:
        print('短页（<120 字）: ' + '、'.join('p%s' % k for k in short[:20]))
    if terms:
        print('术语多写法: %d 组' % len(terms))
        for t in terms[:10]:
            print('   %s：%s' % (t['label'], ' / '.join(
                '%s×%d' % (k, len(v)) for k, v in t['hits'].items())))
    if residue:
        print('跨品类残留词: ' + '、'.join(r['word'] for r in residue[:12]))
    print('阶段提示: %s（占位密度 %.1f%%，空页 %d）' % (stage_hint, density * 100, len(blank)))
    print('→ %s' % os.path.abspath(args.out))


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
