#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""termdrift.py — 术语漂移检测（译文验收版专用，方向中立）

解决一类通读必然漏掉的问题：源词在译文里被两个及以上词族分裂。
这类缺陷的句子语法正确、结构完全对齐，逐句读发现不了，只有把
"同一个源词的全篇译法"聚合起来才看得见。

三层检出，对应 SKILL.md 硬规则 R1–R3：

    R1 标题层对齐（机器，零误报）
       源文与译文的标题按章节号 1:1 对齐，按源标题首词分组；
       同一源词对应 >=2 个不同译词 => 输出候选（只报候选，不判对错）。

    R2 章内近义词对（机器，候选级）
       按章统计目标语中的近义词对（drive/ride、check/inspect 等），
       两者同章各 >=3 次 => 输出候选。噪声较高，只作提示。

    R3 源文高频词清单（人工填表的脚手架）
       取源文出现 >= min_freq 的实词，逐个给出译文侧候选译法
       （按 log-odds 排序，附命中次数），输出可直接填写的 Markdown 表。
       填完全部行并择一后，把结果写进 terms.json 的 term_map，
       由 scan.py 在后续版本中机器拦截（R4）。

用法:
    python termdrift.py <source.pdf> <target.pdf> [-o 输出前缀]
    python termdrift.py 中文.pdf 英文.pdf --direction zh2en -o termdrift
    python termdrift.py 英文.pdf 中文.pdf --direction en2zh -o termdrift
    python termdrift.py a.pdf b.pdf --min-freq 15 --top 40

输出:
    <前缀>.json   机器可读结果（R1/R2/R3）
    <前缀>.md     人读的填表模板（R3）

依赖: pymupdf
"""
import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

try:
    import pymupdf
except ImportError:  # 兼容旧版
    import fitz as pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import guess_offset, load_pages

CJK = re.compile(r'[\u4e00-\u9fff]')
CJK_RUN = re.compile(r'[\u4e00-\u9fff]{2,}')
LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z\-']*")
HEAD_NUM = re.compile(r'^\s*(\d{1,2}(?:\.\d{1,2}){1,2})\s*\t?\s*(.*)$')
NEXT_HEAD = re.compile(r'\s+\d{1,2}(?:\.\d{1,2}){1,2}\s')
TOC_TAIL = re.compile(r'[·.…]{3,}\s*\d+\s*$')

# ---------- R2 近义词对（技能预置，纯通用词，不含任何品牌/车型数据） ----------
# 技术手册里同一概念应二选一；两者并存 => 至少有一处与源文不符。
PAIRS_EN = [
    ('drive', 'ride'), ('check', 'inspect'), ('replace', 'change'),
    ('install', 'mount'), ('remove', 'detach'), ('remove', 'uninstall'),
    ('start', 'crank'), ('stop', 'halt'), ('ensure', 'make sure'),
    ('allow', 'permit'), ('use', 'utilize'), ('before', 'prior to'),
    ('after', 'subsequent to'), ('turn', 'rotate'), ('pull', 'draw'),
    ('push', 'press'), ('fix', 'repair'), ('adjust', 'set'),
    ('connect', 'attach'), ('disconnect', 'detach'), ('store', 'keep'),
    ('release', 'loosen'), ('tighten', 'torque'), ('select', 'choose'),
    ('show', 'display'), ('begin', 'start'), ('finish', 'complete'),
    ('operate', 'run'), ('carry', 'transport'), ('lift', 'raise'),
]
PAIRS_ZH = [
    ('驾驶', '骑行'), ('检查', '查看'), ('更换', '替换'),
    ('安装', '装配'), ('拆卸', '拆除'), ('起动', '启动'),
    ('停止', '停车'), ('确保', '保证'), ('允许', '许可'),
    ('使用', '采用'), ('调整', '调节'), ('连接', '接合'),
    ('拧紧', '紧固'), ('选择', '选取'), ('显示', '展示'),
]

# 中文虚字：出现在词首或词尾即判定该 n-gram 不是术语
ZH_EDGE_STOP = set('的了在是和与或请可应不要为以及该其上下中内外时后前将把被对从到向'
                   '则即而但却都很更最太还又也并如若因所以此那些个之於于一能会示位')

EN_STOP = set("""a an the and or but if then than that this these those is are was were be been
being do does did not no nor of to in on at by for from with without into onto over under
above below up down out off again further once here there when where why how all any both
each few more most other some such only own same so too very can will just should now
it its it's they them their we our you your he she his her as has have had may might must
shall would could about after before during while until because through between""".split())

# 中文高频但非术语的 n-gram（按品类无关的通用词收，宁少勿多）
ZH_STOP_GRAM = set("""本车 车辆 可以 进行 使用 如果 需要 应当 必须 不得 请勿 严禁 以及 或者
一个 以上 以下 进行 用于 位于 应当 可能 造成 导致 出现 发生 包括 具有 相应 相关
所有 任何 其他 其中 同时 此外 注意 警告 说明 注释 参见 参阅 如下 以下 上述""".split())


def stem(w):
    """粗词干：去 -ing/-ed/-es/-s，再统一去掉词尾 e。
    让 replace / replacing 归为 replac，change / changing 归为 chang。"""
    w = w.lower()
    for suf in ('ing', 'ed', 'es', 's'):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[:-len(suf)]
            break
    return w.rstrip('e')


def first_token(title, lang):
    """标题首词：中文取前 2 字，英文取首词词干。用于 R1 分组。"""
    t = title.strip()
    if lang == 'zh':
        m = CJK_RUN.search(t)
        return m.group(0)[:2] if m else ''
    m = LATIN_WORD.search(t)
    return stem(m.group(0)) if m else ''


def load_raw(doc):
    """按 PDF 原生顺序取文本（不排序）。标题抽取用这个：排序会把
    「5.2.12」与紧随的标题行拆开，反而破坏标题结构。"""
    return [p.get_text('text') for p in doc]


def extract_headings(pages, offset):
    """抽取 {章节号: (印刷页, 标题)}。

    标题可能是「5.2 标题」同排、也可能是编号独占一行、标题在下一行。
    标题串到下一个编号时截断（排版把相邻标题并到一行的情况）。
    """
    out = {}
    for i, text in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        lines = text.split('\n')
        for j, line in enumerate(lines):
            m = HEAD_NUM.match(line)
            if not m:
                continue
            title = m.group(2).strip()
            k = j + 1
            while not title and k < len(lines):
                title = lines[k].strip()
                k += 1
            if not title or TOC_TAIL.search(title):
                continue
            cut = NEXT_HEAD.search(' ' + title)  # 串入下一个标题则截断
            if cut:
                title = title[:cut.start()].strip()
            if not title or len(title) > 60 or title[-1] in '。.；;，,':
                continue
            out.setdefault(m.group(1), (printed, title))
    return out


def r1_title_drift(src_heads, tgt_heads, src_lang, tgt_lang):
    """同一源标题首词对应 >=2 个译标题首词 => 候选。"""
    groups = defaultdict(list)
    for num, (pg, title) in src_heads.items():
        if num not in tgt_heads:
            continue
        key = first_token(title, src_lang)
        if not key:
            continue
        groups[key].append((num, pg, title, tgt_heads[num][1]))
    out = []
    for key, items in groups.items():
        forms = defaultdict(list)
        for num, pg, s, t in items:
            ft = first_token(t, tgt_lang)
            if ft:
                forms[ft].append({'num': num, 'page': pg, 'src': s, 'tgt': t})
        if len(forms) >= 2:
            out.append({
                'src_key': key,
                'count': len(items),
                'forms': {k: v for k, v in sorted(forms.items(),
                                                  key=lambda x: -len(x[1]))},
            })
    out.sort(key=lambda x: (-x['count'], x['src_key']))
    return out


def chapter_ranges(doc, raw_pages, offset, total_pages):
    """章节页码范围。优先用 PDF 书签的一级条目（最可靠）；无书签时按
    「N. 标题」样式推断，并限制标题长度以免把编号步骤误判成章标题。"""
    out = {}
    try:
        toc = doc.get_toc(simple=True) or []
    except Exception:
        toc = []
    tops = [(pg - offset, t) for lv, t, pg in toc if lv == 1 and pg - offset >= 1]
    if len(tops) >= 3:
        tops.sort()
        for idx, (pg, _t) in enumerate(tops):
            end = tops[idx + 1][0] - 1 if idx + 1 < len(tops) else total_pages
            out[idx + 1] = (pg, max(end, pg))
        return out

    pat = re.compile(r'^\s*(\d{1,2})\.\s*\t?\s*(\S.*)$')
    seen, uniq = set(), []
    for i, text in enumerate(raw_pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        for line in text.split('\n'):
            m = pat.match(line)
            if not m:
                continue
            num, title = int(m.group(1)), m.group(2).strip()
            if not (1 <= num <= 40) or num in seen:
                continue
            if len(title) > 20 or title[-1] in '。.；;，,':  # 编号步骤，不是章标题
                continue
            seen.add(num)
            uniq.append((printed, num))
    if len(uniq) < 3:
        return {1: (1, total_pages)} if total_pages >= 1 else {}
    for idx, (pg, num) in enumerate(uniq):
        end = uniq[idx + 1][0] - 1 if idx + 1 < len(uniq) else total_pages
        out[num] = (pg, max(end, pg))
    return out


def r2_synonym_pairs(pages, offset, chapters, tgt_lang):
    pairs = PAIRS_EN if tgt_lang == 'en' else PAIRS_ZH
    out = []
    for ch, (a, b) in sorted(chapters.items()):
        text = ' '.join(pages[p + offset - 1] for p in range(a, b + 1)
                        if 0 <= p + offset - 1 < len(pages))
        hits = []
        for x, y in pairs:
            if tgt_lang == 'en':
                px = r'\b%s(?:s|es|ed|ing)?\b' % re.escape(x)
                py = r'\b%s(?:s|es|ed|ing)?\b' % re.escape(y)
            else:
                px = re.escape(x)
                py = re.escape(y)
            cx = len(re.findall(px, text, re.I))
            cy = len(re.findall(py, text, re.I))
            if cx >= 3 and cy >= 3:
                hits.append({'a': x, 'a_count': cx, 'b': y, 'b_count': cy})
        if hits:
            out.append({'chapter': ch, 'pages': [a, b], 'pairs': hits})
    return out


def zh_ngrams(pages, offset, min_freq, min_pages=2):
    """中文源文的高频术语候选（无分词器时的近似取词）。

    先取 2–4 字 n-gram，再合并碎片：若某个 n-gram 是另一个更高频
    n-gram 的子串，则丢弃（「动机」「发动」并入「发动机」）。
    """
    cnt = Counter()
    pgset = defaultdict(set)
    for i, text in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        for run in CJK_RUN.findall(text):
            for n in (2, 3, 4):
                for k in range(len(run) - n + 1):
                    g = run[k:k + n]
                    if g[0] in ZH_EDGE_STOP or g[-1] in ZH_EDGE_STOP:
                        continue
                    if g in ZH_STOP_GRAM:
                        continue
                    cnt[g] += 1
                    pgset[g].add(printed)
    cands = [(g, c) for g, c in cnt.items()
             if c >= min_freq and len(pgset[g]) >= min_pages]
    cands.sort(key=lambda x: (-len(x[0]), -x[1]))
    kept = []
    for g, c in cands:
        if any(g in k and kc >= 0.6 * c for k, kc in kept):
            continue  # 是更长术语的子串，并入长词
        kept.append((g, c))
    kept.sort(key=lambda x: -x[1])
    return dict(kept)


def en_terms(pages, offset, min_freq, min_pages=2):
    cnt = Counter()
    pgset = defaultdict(set)
    for i, text in enumerate(pages):
        printed = i + 1 - offset
        if printed < 1:
            continue
        for w in LATIN_WORD.findall(text.lower()):
            if w in EN_STOP or len(w) < 3:
                continue
            cnt[w] += 1
            pgset[w].add(printed)
    return {w: c for w, c in cnt.items()
            if c >= min_freq and len(pgset[w]) >= min_pages}


def _pair_index(pairs):
    idx = defaultdict(set)
    for a, b in pairs:
        idx[a].add(b)
        idx[b].add(a)
    return idx


PAIR_IDX_EN = _pair_index(PAIRS_EN)
PAIR_IDX_ZH = _pair_index(PAIRS_ZH)


def _extend_with_pairmates(scored, topn, pair_idx):
    """候选里若出现近义对的一方，把另一方也拉进来（可能排在 topn 之外）。

    这是抓漂移的关键：driving 进了候选，riding 哪怕只排第 12 位也必须露出，
    否则填表的人根本想不到要查它。
    """
    chosen = list(scored[:topn])
    names = {w for _, _, w in chosen}
    flagged = set()
    for c, l, w in scored[topn:]:
        mates = pair_idx.get(w, set()) & names
        if mates:
            chosen.append((c, l, w))
            flagged.add(w)
            names.add(w)
    return chosen, flagged


def split_sents(text, lang):
    if lang == 'zh':
        parts = re.split(r'(?<=[。！？])', text)
    else:
        parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def align_sentences(src_pages, tgt_pages, src_lang, tgt_lang):
    """句级序位对齐：同页内源句与译句按出现顺序配对。

    译文与源文同版式、同分页时，同页句子数通常一致；差得太多（表格页、
    图注页）就整页跳过，避免错配污染候选。
    """
    pairs = []
    for i in range(min(len(src_pages), len(tgt_pages))):
        ss = split_sents(src_pages[i], src_lang)
        ts = split_sents(tgt_pages[i], tgt_lang)
        if not ss or not ts:
            continue
        if abs(len(ss) - len(ts)) > max(1, round(0.25 * len(ss))):
            continue
        for a, b in zip(ss, ts):
            pairs.append((a, b))
    return pairs


def _tokens(text, lang):
    if lang == 'en':
        return [w.lower() for w in LATIN_WORD.findall(text)
                if w.lower() not in EN_STOP and len(w) >= 3]
    toks = []
    for run in CJK_RUN.findall(text):
        for k in range(len(run) - 1):
            toks.append(run[k:k + 2])
    return toks


def r3_candidates(src_pages, src_offset, tgt_pages, tgt_offset,
                  terms, src_lang, tgt_lang, topn=6):
    """对每个源词，给出译文侧候选译法。

    优先用句级对齐（precision 高，候选就是该词的译法）；句对太少时
    退化为页级 log-odds（召回高，但会混入主题词，需人工筛）。
    """
    pairs = align_sentences(src_pages, tgt_pages, src_lang, tgt_lang)
    mode = 'sentence'
    if len(pairs) < 200:
        mode = 'page'
    pair_idx = PAIR_IDX_EN if tgt_lang == 'en' else PAIR_IDX_ZH

    out = []
    if mode == 'sentence':
        tgt_of = []  # [(源句, 译句 tokens)]
        for a, b in pairs:
            tgt_of.append((a, _tokens(b, tgt_lang)))
        for term, freq in terms.items():
            hit, other = Counter(), Counter()
            npages = set()
            for idx, (a, toks) in enumerate(tgt_of):
                found = (term in a) if src_lang == 'zh' else \
                    re.search(r'\b%s\b' % re.escape(term), a, re.I)
                if found:
                    hit.update(toks)
                    npages.add(idx)
                else:
                    other.update(toks)
            if len(npages) < 3:
                continue
            tot_h = sum(hit.values()) or 1
            tot_o = sum(other.values()) or 1
            scored = []
            for w, c in hit.items():
                if c < 3:
                    continue
                lift = (c / tot_h) / ((other[w] + 0.5) / tot_o)
                if lift < 1.3:      # 与背景分布无差别，不可能是该词的译法
                    continue
                scored.append((c, lift, w))
            scored.sort(key=lambda x: (-x[0], -x[1]))
            chosen, flagged = _extend_with_pairmates(scored, topn, pair_idx)
            out.append({
                'src_term': term, 'src_freq': freq, 'src_sents': len(npages),
                'candidates': [{'tgt': w, 'in': c, 'out': other[w],
                                'lift': round(l, 2), 'pair': w in flagged}
                               for c, l, w in chosen],
            })
    else:
        n = min(len(src_pages), len(tgt_pages))
        toks_by_page = {i: _tokens(tgt_pages[i], tgt_lang) for i in range(n)}
        for term, freq in terms.items():
            in_pages = {i for i in range(n)
                        if (term in src_pages[i] if src_lang == 'zh'
                            else re.search(r'\b%s\b' % re.escape(term),
                                           src_pages[i], re.I))}
            if len(in_pages) < 2:
                continue
            cp, co = Counter(), Counter()
            for i in range(n):
                (cp if i in in_pages else co).update(toks_by_page[i])
            np_, no = sum(cp.values()), sum(co.values())
            scored = []
            for w, c in cp.items():
                if c < 3:
                    continue
                a = (c + 0.5) / (np_ + 1)
                b = (co[w] + 0.5) / (no + 1)
                lodds = math.log(a / b)
                if lodds < 0.3:
                    continue
                scored.append((c, lodds, w))
            scored.sort(key=lambda x: (-x[0], -x[1]))
            chosen, flagged = _extend_with_pairmates(scored, topn, pair_idx)
            out.append({
                'src_term': term, 'src_freq': freq, 'src_sents': len(in_pages),
                'candidates': [{'tgt': w, 'in': c, 'out': co[w],
                                'lift': round(l, 2), 'pair': w in flagged}
                               for c, l, w in chosen],
            })
    out.sort(key=lambda x: -x['src_freq'])
    return out, mode


def write_md(path, meta, r1, r2, r3):
    L = []
    L.append('# 术语漂移检测报告（R1–R3）\n')
    L.append('| 项 | 值 |')
    L.append('|---|---|')
    for k, v in meta.items():
        L.append('| %s | %s |' % (k, v))
    L.append('')

    L.append('## R1 标题层对齐（机器，零误报）\n')
    L.append('同一源标题首词对应多个译词，逐个确认后择一。\n')
    if not r1:
        L.append('未发现多译。\n')
    else:
        L.append('| 源首词 | 条数 | 译法分布 |')
        L.append('|---|---|---|')
        for g in r1:
            dist = '；'.join('%s(%d)' % (k, len(v)) for k, v in g['forms'].items())
            L.append('| %s | %d | %s |' % (g['src_key'], g['count'], dist))
        L.append('')
        L.append('<details><summary>逐条明细</summary>\n')
        for g in r1:
            L.append('### %s（%d 条）\n' % (g['src_key'], g['count']))
            L.append('| 章节 | 页 | 源标题 | 译标题 |')
            L.append('|---|---|---|---|')
            for k, items in g['forms'].items():
                for it in items:
                    L.append('| %s | %s | %s | %s |'
                             % (it['num'], it['page'], it['src'], it['tgt']))
            L.append('')
        L.append('</details>\n')

    L.append('## R2 章内近义词对（候选级，噪声较高）\n')
    L.append('同章内两个近义词各出现 >=3 次。至少有一处与源文不符，需逐处核对。\n')
    if not r2:
        L.append('未命中。\n')
    else:
        L.append('| 章 | 页 | 近义对 |')
        L.append('|---|---|---|')
        for c in r2:
            ps = '；'.join('%s=%d / %s=%d' % (p['a'], p['a_count'], p['b'], p['b_count'])
                           for p in c['pairs'])
            L.append('| %s | %s–%s | %s |'
                     % (c['chapter'], c['pages'][0], c['pages'][1], ps))
        L.append('')

    L.append('## R3 源文高频词全覆盖清单（必须填完才能出件）\n')
    L.append('> 填法：把「译文写法（全部）」一栏列全 —— 这一步本身就会暴露漂移。')
    L.append('> 「是否唯一」填 ✓ 或 ✗；✗ 的必须给出「择一结果」与「依据」。')
    L.append('> 填完把结果转成 `terms.json` 的 `term_map.entries`，后续由 scan.py 机器拦截。\n')
    L.append('| # | 源词 | 次数 | 命中句 | 译文候选（机器，按相关度） | 译文写法（全部） | 是否唯一 | 择一结果 | 依据 |')
    L.append('|---|---|---|---|---|---|---|---|---|')
    for idx, it in enumerate(r3, 1):
        cand = '、'.join(('%s(%d)%s' % (c['tgt'], c['in'], '⚠' if c.get('pair') else ''))
                         for c in it['candidates'])
        L.append('| %d | %s | %d | %d | %s |  |  |  |  |'
                 % (idx, it['src_term'], it['src_freq'], it['src_sents'], cand))
    L.append('')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source', help='源语定稿 PDF（master）')
    ap.add_argument('target', help='目标语译本 PDF')
    ap.add_argument('-o', '--out', default='termdrift', help='输出前缀（默认 termdrift）')
    ap.add_argument('--direction', choices=['zh2en', 'en2zh', 'auto'], default='auto',
                    help='源→靶方向；auto 时按 source 的字符构成猜（默认 auto）')
    ap.add_argument('--min-freq', type=int, default=20,
                    help='R3 取词的源文最低出现次数（默认 20）')
    ap.add_argument('--top', type=int, default=40,
                    help='R3 最多列多少个源词（默认 40）')
    ap.add_argument('--topn', type=int, default=6,
                    help='R3 每个源词给几个译文候选（默认 6）')
    args = ap.parse_args()

    for p in (args.source, args.target):
        if not os.path.exists(p):
            sys.exit('文件不存在: %s' % p)

    sdoc, tdoc = pymupdf.open(args.source), pymupdf.open(args.target)
    spages, tpages = load_pages(sdoc), load_pages(tdoc)
    sraw, traw = load_raw(sdoc), load_raw(tdoc)
    soff, _ = guess_offset(sraw)
    toff, _ = guess_offset(traw)

    direction = args.direction
    if direction == 'auto':
        cjk = sum(len(CJK.findall(t)) for t in spages)
        direction = 'zh2en' if cjk > 200 else 'en2zh'
        print('方向未指定，按源文字符构成推断为 %s（源文汉字 %d 个）。'
              '建议显式用 --direction 指定。' % (direction, cjk))
    src_lang, tgt_lang = ('zh', 'en') if direction == 'zh2en' else ('en', 'zh')

    shead = extract_headings(sraw, soff)
    thead = extract_headings(traw, toff)
    common = [k for k in shead if k in thead]

    r1 = r1_title_drift(shead, thead, src_lang, tgt_lang)
    chapters = chapter_ranges(tdoc, traw, toff, max(len(tpages) - toff, 1))
    r2 = r2_synonym_pairs(tpages, toff, chapters, tgt_lang)

    terms = (zh_ngrams(spages, soff, args.min_freq) if src_lang == 'zh'
             else en_terms(spages, soff, args.min_freq))
    terms = dict(sorted(terms.items(), key=lambda x: -x[1])[:args.top])
    r3, r3_mode = r3_candidates(spages, soff, tpages, toff, terms,
                                src_lang, tgt_lang, args.topn)

    meta = {
        '方向': direction,
        '源文': os.path.basename(args.source),
        '译文': os.path.basename(args.target),
        '源文页码偏移': soff,
        '译文页码偏移': toff,
        '标题可对齐': '%d / %d' % (len(common), len(shead)),
        'R1 多译组': len(r1),
        'R2 命中章': len(r2),
        'R3 源词条数': len(r3),
        'R3 候选来源': '句级对齐' if r3_mode == 'sentence' else '页级共现（句对不足，候选含主题词，需筛）',
    }

    payload = {'meta': meta, 'direction': direction,
               'r1_title_drift': r1, 'r2_synonym_pairs': r2,
               'r3_source_terms': r3}
    with open(args.out + '.json', 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    write_md(args.out + '.md', meta, r1, r2, r3)

    print('方向: %s | 标题可对齐 %d/%d' % (direction, len(common), len(shead)))
    print('R1 多译组: %d' % len(r1))
    for g in r1[:8]:
        print('  %s ×%d → %s' % (g['src_key'], g['count'],
                                 '、'.join(g['forms'].keys())))
    print('R2 命中章: %d' % len(r2))
    for c in r2[:6]:
        print('  ch%s p%s–%s: %s' % (c['chapter'], c['pages'][0], c['pages'][1],
                                     '；'.join('%s=%d/%s=%d' % (p['a'], p['a_count'],
                                                               p['b'], p['b_count'])
                                              for p in c['pairs'])))
    print('R3 源词 %d 条（填表模板见 %s.md）' % (len(r3), args.out))
    print('→ %s.json / %s.md' % (args.out, args.out))


if __name__ == '__main__':
    main()
