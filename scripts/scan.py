#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan.py — 技术文档审校的机器扫描（只提供线索，不替代人工判断）

用法:
    python scan.py <file.pdf> [-o review.json] [--terms terms.json]
    python scan.py <file_en.pdf> --lang en [-o review.json] [--dnt dnt.json]

默认查当前工作目录 terms.json；未提供则跳过术语与残留词两类扫描。
输出六类线索:
    1. 印刷页码偏移（PDF 页序 - 页脚数字）
    2. 目录条目与正文实际标题页的偏差
    3. 占位扫描（XX/XXX/占位）
    4. 空页与短页
    5. 术语变体分布（按 terms.json 分组）
    6. 疑似文档阶段（占位密度过高 -> 疑似中途版）

--lang en 追加四类（译文验收用，只提供线索，不作合规判定）:
    7. 残留汉字页（漏译信号）
    8. STE 子集分布（句长/段长/名词化/多词动词/未定义缩写）
    9. 数值+单位比对（--source 给中文定稿时逐项比对；不给则只列清单供人工并排核）
   10. 图注/表注行 + 不可译清单命中

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


TAG = '【翻译版】'


def p(msg):
    print(TAG + msg)

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
    """返回 (术语多写法, 跨品类残留词, 译法冲突 term_map 命中)。

    term_map 是 R4 机器拦截：用户在 R3 清单里择一后写进 terms.json，
    后续版本译文若又出现被禁写法，直接命中，不必再人工通读。
    """
    if not terms_path or not os.path.exists(terms_path):
        return [], [], []
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

    conflicts = []
    tm = cfg.get('term_map', {}) or {}
    for e in tm.get('entries', []) or []:
        src = e.get('src', '')
        boundary = e.get('boundary', True)
        hits = {}
        for w in e.get('forbid', []) or []:
            pg = [i + 1 - offset for i, t in enumerate(pages)
                  if _hit(t, w, boundary) and i + 1 - offset >= 1]
            if pg:
                hits[w] = pg
        if hits:
            conflicts.append({
                'src': src,
                'chosen': e.get('chosen', ''),
                'basis': e.get('basis', ''),
                'hits': hits,
                'total': sum(len(v) for v in hits.values()),
            })
    conflicts.sort(key=lambda x: -x['total'])
    return results, residue, conflicts


# 连续 3 个以上英文单词视作「未译段」——目标语为中文时的漏译信号
LATIN_RUN = re.compile(r'(?:[A-Za-z][A-Za-z\-\']*\s+){3,}[A-Za-z][A-Za-z\-\']*')


def scan_latin_leak(pages, offset, min_words=4, latin_ratio=0.8):
    """目标语为中文时，正文里成行的英文 = 漏译或未译。

    与 cjk_leak 对称：中文版里出现英文整行，比英文版里残留汉字更常见
    （模板段落、法规原文、软件菜单）。按行判定并要求该行 >=80% 是拉丁
    单词，避免把表格里的「Y Y Y Y」「PAGE UP」当成漏译。
    """
    by_page = {}
    for i, t in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        for line in t.split('\n'):
            line = line.strip()
            if len(line) < 15:
                continue
            words = line.split()
            if len(words) < min_words:
                continue
            latin = sum(1 for w in words
                        if re.match(r"^[A-Za-z][A-Za-z\-']{1,}$", w))
            if latin / len(words) >= latin_ratio:
                by_page.setdefault(printed, []).append(line[:80])
    return [{'page': p, 'count': len(v), 'samples': v[:3]}
            for p, v in sorted(by_page.items())]


# ---------------- 翻译版附加扫描（--lang en） ----------------

CJK = re.compile(r'[\u4e00-\u9fff]')
SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"“(\[])')
NOMINAL = re.compile(r'\b\w{4,}(?:tion|ment|ance|ence|sion)\s+of\b', re.I)
PHRASAL = [
    ('carry out', 'do / perform'), ('put out', 'extinguish'),
    ('give off', 'release'), ('take apart', 'disassemble'),
    ('put together', 'assemble'), ('find out', 'determine'),
    ('go up', 'increase'), ('go down', 'decrease'),
    ('look at', 'examine'), ('set up', 'set / adjust'),
]
# 边界用 (?![A-Za-z0-9]) 而非 \b：Python re 的 \w 含汉字，"10L的"里 L\b 不成立会漏抓
NUM_UNIT = re.compile(
    r'(?<![A-Za-z0-9.])(\d+(?:[.,]\d+)?)\s*'
    r'(km/h|km|mm|cm|kg|kPa|MPa|kW|kN|lbf·ft|N·m|N\.m|Nm|ml|Ah|psi|bar|rpm|mph|gal|lbs|lb|oz|ft|°C|°F|℃|℉|L|V|A|W|h|g|m)'
    r'(?![A-Za-z0-9])')
ABBR = re.compile(r'\b[A-Z][A-Z0-9]{1,5}\b')
FIG_LINE = re.compile(r'^\s*(?:(?:Fig(?:ure)?\.?|Table|Tab\.)\s*\d+|[图表]\s*\d+)', re.I)


def scan_cjk_leak(pages, offset):
    """英文版残留汉字：漏译的最硬信号。"""
    out = {}
    for i, t in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        hits = [ln.strip()[:90] for ln in t.split('\n') if CJK.search(ln)]
        if hits:
            out[printed] = hits[:6]
    return out


def _sentences(text):
    return [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]


def _words(s):
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-./·]*", s))


def scan_ste(pages, offset):
    """STE 子集：S1 句长 / S2 段长 / S4 名词化 / S5 多词动词 / S6 缩略语首现。

    只统计分布供人工复核，不作合规判定（词典校验需官方 Issue 8）。
    """
    long25, long20, para, nominal, phrasal = [], [], [], [], []
    first_seen, abbr_all = {}, {}

    for i, t in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1 or not t.strip():
            continue

        for s in _sentences(t):
            n = _words(s)
            if n > 25:
                long25.append({'page': printed, 'words': n, 'text': s[:110]})
            elif n > 20:
                long20.append({'page': printed, 'words': n, 'text': s[:110]})

        for p in re.split(r'\n\s*\n', t):
            p = p.strip()
            if len(p) > 60 and len(_sentences(p)) > 6:
                para.append({'page': printed, 'sentences': len(_sentences(p)),
                             'text': p[:90]})

        for m in NOMINAL.finditer(t):
            nominal.append({'page': printed, 'text': m.group(0)[:60]})

        low = t.lower()
        for phrase, better in PHRASAL:
            if phrase in low:
                phrasal.append({'page': printed, 'text': phrase, 'better': better})

        for m in ABBR.finditer(t):
            a = m.group(0)
            if a in ('I', 'A'):
                continue
            abbr_all[a] = abbr_all.get(a, 0) + 1
            if a not in first_seen:
                first_seen[a] = (printed, t[max(0, m.start() - 130):m.end() + 10])

    undefined = []
    for a, (pg, ctx) in first_seen.items():
        if abbr_all.get(a, 0) < 2:
            continue
        if ('(%s)' % a) in ctx or ('(%s,' % a) in ctx or ('%s (' % a) in ctx:
            continue
        undefined.append({'abbr': a, 'page': pg, 'context': ctx.replace('\n', ' ')[-100:]})

    return {
        'sent_over_25': long25[:40],
        'sent_21_25': long20[:40],
        'para_over_6': para[:20],
        'nominalization': nominal[:30],
        'phrasal_verb': phrasal[:30],
        'abbr_undefined': undefined[:20],
        'count': {
            'sent_over_25': len(long25), 'sent_21_25': len(long20),
            'para_over_6': len(para), 'nominalization': len(nominal),
            'phrasal_verb': len(phrasal), 'abbr_undefined': len(undefined),
        },
    }


def scan_numbers(pages, offset):
    """抽取「数值+单位」清单，供人工与源文并排核对。不做自动 diff（本地化后值本应改变）。"""
    out = {}
    for i, t in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        vals = []
        for m in NUM_UNIT.finditer(t):
            v = '%s %s' % (m.group(1), m.group(2))
            if v not in vals:
                vals.append(v)
        if vals:
            out[printed] = vals[:40]
    return out


def scan_figures(pages, offset):
    """图注/表注行抽取，供核对是否漏译、编号是否与正文引用一致。"""
    out = {}
    for i, t in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        hits = [ln.strip()[:100] for ln in t.split('\n') if FIG_LINE.match(ln)]
        if hits:
            out[printed] = hits[:20]
    return out


def scan_dnt(pages, offset, dnt_path):
    """不可译清单命中：原文出现的位置。译文侧若被译走，需人工对照确认。"""
    if not dnt_path or not os.path.exists(dnt_path):
        return None
    with open(dnt_path, encoding='utf-8') as f:
        cfg = json.load(f)
    out = []
    for item in cfg.get('items', []):
        w = str(item.get('text', '')).strip()
        if not w:
            continue
        cs = item.get('caseSensitive', True)
        pat = re.compile(re.escape(w), 0 if cs else re.I)
        pg = [i + 1 - offset for i, t in enumerate(pages)
              if pg_ok(i, offset) and pat.search(t)]
        if pg:
            out.append({'text': w, 'kind': item.get('kind', ''), 'pages': pg,
                        'note': item.get('note', '')})
    return out


def pg_ok(i, offset):
    return i + 1 - offset >= 1


# ---------------- 数值与单位：源-靶逐项比对 ----------------
# 默认口径 verbatim：译制版的数值与单位须与中文源文一致，不得自行换算。
# 归一化只消除「字符编码与数字排版」差异；单位写法（含大小写、中点、缩写变体）不归一化——
# N·m 与 Nm、kW 与 KW、r/min 与 rpm 是不同写法，必须一致，不一致即缺陷。

UNIT_CHAR_EQ = {
    '\u2103': '\u00b0C',   # ℃ → °C
    '\u2109': '\u00b0F',   # ℉ → °F
    '\u00a0': ' ',         # 不换行空格
}


def _norm_unit(u):
    """仅做字符级等价替换与全角转半角。大小写、中点、缩写一律保留原样。"""
    s = u.strip()
    for k, v in UNIT_CHAR_EQ.items():
        s = s.replace(k, v)
    out = []
    for c in s:
        o = ord(c)
        out.append(chr(o - 0xFEE0) if 0xFF01 <= o <= 0xFF5E else c)
    return ''.join(out).strip()


def _norm_num(v):
    """去千分位符与空格：5,000 / 5 000 / 5'000 → 5000。不改变数值本身。"""
    return v.replace(',', '').replace(' ', '').replace("'", '').replace('\u00a0', '')


def extract_units(pages, offset):
    """{归一化值|归一化单位: [印刷页码...]}"""
    out = {}
    for i, t in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        for m in NUM_UNIT.finditer(t):
            key = '%s|%s' % (_norm_num(m.group(1)), _norm_unit(m.group(2)))
            lst = out.setdefault(key, [])
            if printed not in lst:
                lst.append(printed)
    return out


def _split_key(key):
    v, _, u = key.partition('|')
    return {'value': v, 'unit': u}


def compare_units(src_units, tgt_units, policy='verbatim'):
    """源-靶数值比对。

    先按「数值相同、单位不同」配对——这是单位写法错误或擅自换算，单独成一类；
    剩余单边项再按 verbatim/localize 口径处理。

    policy=verbatim（默认）：两侧集合应一致，任何单边出现都进候选。
    policy=localize：允许换算，单边出现只作提示（需人工确认换算是否正确）。
    """
    only_src_keys = [k for k in src_units if k not in tgt_units]
    only_tgt_keys = [k for k in tgt_units if k not in src_units]

    def by_value(keys, units):
        d = {}
        for k in keys:
            v, _, u = k.partition('|')
            d.setdefault(v, []).append((u, units[k]))
        return d

    sv, tv = by_value(only_src_keys, src_units), by_value(only_tgt_keys, tgt_units)
    pairs, used = [], set()
    for v, lst_t in tv.items():
        if v not in sv:
            continue
        u_s, pg_s = sv[v][0]
        for u_t, pg_t in lst_t:
            pairs.append({'value': v, 'src_unit': u_s, 'tgt_unit': u_t,
                          'src_pages': pg_s, 'tgt_pages': pg_t})
            used.add('%s|%s' % (v, u_s))
            used.add('%s|%s' % (v, u_t))
    pairs.sort(key=lambda x: x['tgt_pages'][0])

    only_src = [dict(_split_key(k), pages=src_units[k])
                for k in only_src_keys if k not in used]
    only_tgt = [dict(_split_key(k), pages=tgt_units[k])
                for k in only_tgt_keys if k not in used]
    only_src.sort(key=lambda x: x['pages'][0])
    only_tgt.sort(key=lambda x: x['pages'][0])

    return {
        'policy': policy,
        'source_terms': len(src_units),
        'target_terms': len(tgt_units),
        'matched': len(set(src_units) & set(tgt_units)),
        'unit_mismatch': pairs[:40],
        'unit_mismatch_count': len(pairs),
        'only_in_source': only_src[:60],
        'only_in_target': only_tgt[:60],
        'only_in_source_count': len(only_src),
        'only_in_target_count': len(only_tgt),
    }


def scan_num_style(pages, offset, min_len=4):
    """同一数值的多种写法（5,000 / 5 000 / 5000）。不计为错误，只提示通篇风格是否统一。"""
    groups = {}
    for i, t in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        for m in NUM_UNIT.finditer(t):
            raw = m.group(1)
            key = _norm_num(raw)
            if len(key) < min_len:
                continue
            groups.setdefault(key, {}).setdefault(raw, []).append(printed)
    out = []
    for k, forms in groups.items():
        if len(forms) > 1:
            out.append({'value': k, 'forms': {f: pg[:5] for f, pg in forms.items()}})
    return out[:30]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('-o', '--out', default='review.json')
    ap.add_argument('--terms', default=os.path.join(os.getcwd(), 'terms.json'),
                    help='项目词表路径（默认当前工作目录 terms.json）')
    ap.add_argument('--lang', choices=['zh', 'en'], default='zh',
                    help='目标语语种：zh=中文（默认），en=英文。单语版按实际语种填，不限于中文，'
                         'en=英文（启用译文版附加扫描）')
    ap.add_argument('--direction', choices=['zh2en', 'en2zh', 'mono'], default='mono',
                    help='源→靶方向：zh2en / en2zh / mono（单语版，语种由 --lang 指定）。'
                         'en2zh 时 --source 指英文定稿，目标为中文')
    ap.add_argument('--dnt', default=os.path.join(os.getcwd(), 'do-not-translate.json'),
                    help='不可译清单路径（默认当前工作目录 do-not-translate.json）')
    ap.add_argument('--source', default=None,
                    help='源语定稿 PDF 路径：zh2en 给中文定稿，en2zh 给英文定稿。'
                         '给了即启用译文版附加扫描与数值单位逐项比对')
    ap.add_argument('--policy', choices=['verbatim', 'localize'], default='verbatim',
                    help='单位口径：verbatim=数值与单位须与源文一致（默认）；localize=按目标市场换算')
    args = ap.parse_args()

    global TAG
    TAG = '【单语版】' if getattr(args, 'direction', '') == 'mono' else '【翻译版】'

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
    terms, residue, term_conflicts = scan_terms(pages, offset, args.terms)

    en_extra = {}
    # 译文附加扫描的触发条件：目标语为英文，或给了源文（任一方向）。
    # zh→en 用 --lang en --source 中文.pdf；en→zh 用 --lang zh --source 英文.pdf。
    if args.lang == 'en' or args.source:
        if not os.path.exists(args.dnt):
            print('未提供不可译清单 %s，跳过该项扫描（可用 --dnt 指定）。' % args.dnt)
        en_extra = {
            'lang': args.lang,
            'direction': args.direction,
            'cjk_leak': scan_cjk_leak(pages, offset) if args.lang == 'en' else None,
            'latin_leak': scan_latin_leak(pages, offset) if args.lang == 'zh' else None,
            'ste': scan_ste(pages, offset) if args.lang == 'en' else None,
            'numbers': scan_numbers(pages, offset),
            'figures': scan_figures(pages, offset),
            'dnt_hits': scan_dnt(pages, offset, args.dnt),
            'num_style': scan_num_style(pages, offset),
            'unit_check': None,
        }
        if args.lang != 'en':
            print('目标语非英文：STE 子集与残留汉字检查不适用，已跳过；'
                  '改用「残留英文段」检查。')
        if args.source:
            if not os.path.exists(args.source):
                print('警告：源文 %s 不存在，跳过数值与单位比对。' % args.source)
            else:
                sdoc = pymupdf.open(args.source)
                try:
                    spages = load_pages(sdoc)
                    soff, _ = guess_offset(spages)
                    uc = compare_units(extract_units(spages, soff),
                                       extract_units(pages, offset), args.policy)
                    uc['source_file'] = os.path.abspath(args.source)
                    uc['source_offset'] = soff
                    en_extra['unit_check'] = uc
                finally:
                    sdoc.close()

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
        'term_conflicts': term_conflicts,
        'stage_hint': stage_hint,
    }
    data.update(en_extra)
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
    if term_conflicts:
        print('译法冲突（term_map 拦截）: %d 组' % len(term_conflicts))
        for c in term_conflicts[:10]:
            print('   %s 已定为「%s」，仍出现 %s' % (
                c['src'], c['chosen'],
                '、'.join('%s（%d 页）' % (w, len(p)) for w, p in c['hits'].items())))
        print('   以上为 R3 清单择一后写入 terms.json 的机器拦截结果，属确定性缺陷，可直接进主清单。')

    if en_extra:
        if en_extra.get('cjk_leak') is not None:
            leak = en_extra['cjk_leak']
            if len(leak) > body_pages * 0.5:
                print('警告：%d/%d 页含汉字，占比超过一半——该文件疑似中文原文而非英文译文。'
                      % (len(leak), body_pages))
                print('   请确认是否误用了 --lang en。此情形下残留汉字清单不可当作漏译上报。')
            p('残留汉字页: %d 页%s' % (
                len(leak), ('（' + '、'.join('p%s' % k for k in list(leak)[:15]) + '）') if leak else '（未检出）'))
            if leak:
                for k in list(leak)[:3]:
                    print('   p%s: %s' % (k, leak[k][0][:60]))
        else:
            ll = en_extra.get('latin_leak') or []
            p('残留英文段: %d 页%s' % (
                len(ll), ('（' + '、'.join('p%s' % k['page'] for k in ll[:15]) + '）') if ll else '（未检出）'))
            for k in ll[:3]:
                print('   p%s: %s' % (k['page'], k['samples'][0][:70]))
            print('   目标语为中文：成句英文属漏译信号，但软件菜单项、认证编号、'
                  '法规原文可能属不可译项，需人工确认。')
        if en_extra.get('ste') is not None:
            st = en_extra['ste']['count']
            p('STE 子集命中: 超 25 词句 %d / 21-25 词句 %d / 超 6 句段 %d / 名词化 %d / 多词动词 %d / 疑似未定义缩写 %d'
                  % (st['sent_over_25'], st['sent_21_25'], st['para_over_6'],
                     st['nominalization'], st['phrasal_verb'], st['abbr_undefined']))
            print('   以上为分布统计，非合规判定；S3（一句一指令）需人工核。')
        else:
            p('STE 子集: 目标语非英文，不适用（未统计，不得在报告中声称 STE 合规）。')
        uc = en_extra.get('unit_check')
        if uc is None:
            p('数值+单位: 未提供 --source 对照版本，仅输出清单（%d 页）供人工并排核对'
                  % len(en_extra['numbers']))
        else:
            p('数值+单位比对（口径 %s）：源文 %d 项 / 译文 %d 项 / 一致 %d 项'
                  % ('逐字一致' if uc['policy'] == 'verbatim' else '按目标市场换算',
                     uc['source_terms'], uc['target_terms'], uc['matched']))
            if uc['unit_mismatch_count']:
                print('   数值相同、单位不同 %d 处（单位写法错误或擅自换算，属缺陷，不可按排版差异放过）：'
                      % uc['unit_mismatch_count'])
                for d in uc['unit_mismatch'][:8]:
                    print('      %s：源文 %s (p%s) → 译文 %s (p%s)'
                          % (d['value'], d['src_unit'], d['src_pages'][0],
                             d['tgt_unit'], d['tgt_pages'][0]))
            if uc['only_in_source_count']:
                print('   源文有、译文无 %d 项：%s' % (
                    uc['only_in_source_count'],
                    '、'.join('%s %s(p%s)' % (d['value'], d['unit'], d['pages'][0])
                              for d in uc['only_in_source'][:8])))
            if uc['only_in_target_count']:
                print('   译文有、源文无 %d 项：%s' % (
                    uc['only_in_target_count'],
                    '、'.join('%s %s(p%s)' % (d['value'], d['unit'], d['pages'][0])
                              for d in uc['only_in_target'][:8])))
            if uc['policy'] == 'verbatim' and (uc['only_in_source_count'] or uc['only_in_target_count']):
                print('   口径为逐字一致：以上单边项均属候选问题，需人工确认是换算、漏译还是增译。')
        st = en_extra.get('num_style') or []
        if st:
            p('数字写法不统一: %d 组（不计错误，仅提示通篇风格）' % len(st))
            for g in st[:5]:
                print('   %s → %s' % (g['value'], ' / '.join(g['forms'])))
        p('图注/表注行: %d 页' % len(en_extra['figures']))
        dh = en_extra['dnt_hits']
        if dh is None:
            p('不可译清单: 未提供，已跳过')
        elif dh:
            p('不可译项命中: ' + '、'.join(
                '%s(p%s)' % (d['text'], ','.join(map(str, d['pages'][:3]))) for d in dh[:10]))

    print('阶段提示: %s（占位密度 %.1f%%，空页 %d）' % (stage_hint, density * 100, len(blank)))
    print('→ %s' % os.path.abspath(args.out))


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
