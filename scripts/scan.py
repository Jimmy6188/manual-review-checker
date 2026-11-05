#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan.py — 技术文档审校的机器扫描（只提供线索，不替代人工判断）

用法:
    python scan.py <file.pdf> [-o review.json] [--terms terms.json]

默认查当前工作目录 terms.json；未提供则跳过术语与残留词两类扫描。
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


TOC_WINDOW = 10  # 以书签目标页为中心的搜索窗口（PDF 页序）


def find_toc_pages(normed, toc, first_pdf_page, min_hits=3):
    """把「第一个书签目标页之前、且含 >= min_hits 个书签标题」的页视为目录页。"""
    keys = [norm(t) for _, t, _ in toc if len(norm(t)) >= 3]
    if not keys:
        return set()
    limit = min(max(first_pdf_page - 1, 0), len(normed))
    out = set()
    for i in range(limit):
        if sum(1 for k in keys if k in normed[i]) >= min_hits:
            out.add(i)
    return out


def check_toc(doc, pages, offset, window=TOC_WINDOW):
    """目录条目 vs 正文标题页。

    搜索窗口以 PDF 书签目标页为中心，并显式排除目录页自身；
    正文增删页导致的 1-2 页偏移仍能检出。
    返回 (真偏差列表, 疑似匹配失败列表)，页码均为印刷页码。
    """
    toc = doc.get_toc()
    normed = [norm(t) for t in pages]
    start = max(offset, 0)
    first_pdf_page = min((p for _, _, p in toc), default=1)
    toc_pages = find_toc_pages(normed, toc, first_pdf_page)
    out, suspect = [], []
    for level, title, pdf_page in toc:
        key = norm(title)
        if len(key) < 3:
            continue
        lo = max(start, pdf_page - 1 - window)
        hi = min(len(normed), pdf_page + window)
        actual = None
        for i in range(lo, hi):
            if i in toc_pages:
                continue
            if key in normed[i]:
                actual = i + 1
                break
        rec = {
            'title': title.replace('\t', ' ').strip(),
            'level': level,
            'toc_page': pdf_page - offset,
            'actual_page': (actual - offset) if actual else None,
        }
        if actual is None:
            suspect.append(dict(rec, reason='标题未在书签页附近找到'))
        elif actual != pdf_page:
            out.append(rec)
    # 兜底：命中集中在 1-2 页、而目录页码跨度很大 -> 标题匹配失败，不是真偏差
    if out:
        actuals = {b['actual_page'] for b in out}
        span = max(b['toc_page'] for b in out) - min(b['toc_page'] for b in out)
        if len(actuals) <= 2 and span > 10:
            suspect.extend(dict(b, reason='命中集中于少数页，疑似标题匹配失败') for b in out)
            out = []
    return out, suspect


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


def _hit(text, word, boundary=False):
    """默认按子串匹配，避免「制动系统」「刹车助力」等合成词漏检。

    词表某组/某词设 "boundary": true 时启用严格边界：
    纯汉字词须前后都非汉字（避免「制动」命中「驻车制动」）；
    含字母数字的缩写始终按子串匹配。
    """
    if not boundary or not all(_is_cjk(c) for c in word):
        return word in text
    for m in re.finditer(re.escape(word), text):
        s, e = m.start(), m.end()
        if (s == 0 or not _is_cjk(text[s - 1])) and (e == len(text) or not _is_cjk(text[e])):
            return True
    return False


def scan_terms(pages, offset, terms_path):
    if not terms_path or not os.path.exists(terms_path):
        return [], []
    with open(terms_path, encoding='utf-8') as f:
        cfg = json.load(f)
    results = []
    for grp in cfg.get('synonym_groups', []):
        hits = {}
        boundary = grp.get('boundary', False)
        for v in grp['variants']:
            pg = [i + 1 - offset for i, t in enumerate(pages)
                  if _hit(t, v, boundary) and i + 1 - offset >= 1]
            if pg:
                hits[v] = pg
        if len(hits) >= 2:
            results.append({'label': grp.get('label', grp['id']), 'hits': hits})
    residue = []
    for item in cfg.get('residue_words', []):
        w = item['word']
        boundary = item.get('boundary', False)
        pg = [i + 1 - offset for i, t in enumerate(pages)
              if _hit(t, w, boundary) and i + 1 - offset >= 1]
        if pg:
            residue.append({'word': w, 'hint': item.get('hint', ''), 'pages': pg})
    return results, residue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('-o', '--out', default='review.json')
    ap.add_argument('--terms', default=os.path.join(os.getcwd(), 'terms.json'),
                    help='项目词表路径（默认当前工作目录 terms.json）')
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

    if not os.path.exists(args.terms):
        print('未提供词表 %s，跳过术语变体与跨品类残留扫描（可用 --terms 指定项目词表）。'
              % args.terms)
    toc_bad, toc_suspect = check_toc(doc, pages, offset)
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
        'toc_mismatch_suspect': toc_suspect,
        'toc_suspect_count': len(toc_suspect),
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
    if toc_suspect:
        print('目录疑似匹配失败: %d 条（已单列，不计入偏差）' % len(toc_suspect))
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
