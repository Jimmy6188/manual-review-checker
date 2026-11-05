#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py — 由一份 data.json 同时生成 HTML 报告与 Excel 清单。

用法:
    python build.py data.json                      # 输出到 data.json 所在目录
    python build.py data.json -o 输出目录 -n 前缀
    python build.py data.json --only html          # 只出 HTML
    python build.py data.json --only xlsx          # 只出 Excel

数据格式见 assets/report-data.example.json：
    {"meta": {...}, "items": [...], "top3": [...], "suspects": [...], "accept": [...]}

单一数据源：HTML 与 Excel 出自同一份 JSON，不会出现两处不一致。
"""
import argparse
import io
import json
import os
import re
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), 'assets', 'report-template.html')

SEV_ORDER = ['严重', '中等', '轻微', '待补']
SEV_FILL = {
    '严重': 'FDE8E6', '中等': 'FDF3E0', '轻微': 'EAF0F8', '待补': 'F1F2F4',
}
# 中文报告字体；跨平台（macOS/Linux）部署时改为目标系统可用的中文字体，如 'PingFang SC' / 'Noto Sans CJK SC'
FONT_NAME = '微软雅黑'
HEAD_FILL = PatternFill('solid', fgColor='2C3E50')
HEAD_FONT = Font(name=FONT_NAME, size=10, bold=True, color='FFFFFF')
BODY_FONT = Font(name=FONT_NAME, size=10)
THIN = Side(style='thin', color='D9DDE3')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

COLS = [
    ('序号', 6, 'id'),
    ('章节', 24, 'ch'),
    ('严重程度', 10, 'sev'),
    ('问题类别', 18, 'cat'),
    ('原文位置', 18, 'loc'),
    ('原文摘录', 42, 'quote'),
    ('问题说明', 52, 'issue'),
    ('修改建议', 42, 'fix'),
    ('复核证据', 20, 'evidence'),
]


def text_width(s):
    return sum(2 if ord(c) > 127 else 1 for c in str(s or ''))


def est_height(values, widths):
    lines = 1
    for v, w in zip(values, widths):
        n = text_width(v)
        if n:
            lines = max(lines, -(-n // max(w - 2, 4)))
    return min(max(lines * 15 + 4, 20), 200)


EVIDENCE_CATS = ('版式', '编号', '断句', '表格对齐', '图注', '目录', '格式')


def warn_evidence(items):
    """版式/结构类条目缺复核证据时只打印警告，不阻断出件。"""
    need = [it for it in items
            if any(k in str(it.get('cat', '')) for k in EVIDENCE_CATS)]
    missing = [it for it in need if not str(it.get('evidence', '')).strip()]
    if missing:
        print('警告：%d 条版式/结构类条目缺少复核证据（evidence），已照常出件，请在自评中标“存疑”。'
              % len(missing))
        for it in missing[:10]:
            print('  - %s  %s' % (it.get('loc', ''), it.get('cat', '')))
        if len(missing) > 10:
            print('  … 其余 %d 条见 data.json' % (len(missing) - 10))
    elif need:
        print('复核证据：%d 条版式/结构类条目均已填写。' % len(need))
    return len(missing)


# ---------------- Excel ----------------
def build_xlsx(data, path):
    wb = Workbook()
    ws = wb.active
    ws.title = '问题清单'

    meta = data.get('meta', {})
    items = data.get('items', [])

    # 标题行（合并）
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLS))
    t = ws.cell(row=1, column=1, value='%s　（%s，%s）' % (
        meta.get('title', '审校问题清单'),
        '终稿' if meta.get('stage') == 'final' else '中途版',
        meta.get('date', '')))
    t.font = Font(name=FONT_NAME, size=12, bold=True)
    t.alignment = Alignment(vertical='center')
    ws.row_dimensions[1].height = 26

    # 表头
    hr = 2
    for i, (name, width, _) in enumerate(COLS, start=1):
        c = ws.cell(row=hr, column=i, value=name)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[hr].height = 22

    widths = [w for _, w, _ in COLS]
    r = hr + 1
    for i, it in enumerate(items, start=1):
        row = [i, it.get('ch', ''), it.get('sev', ''), it.get('cat', ''),
               it.get('loc', ''), it.get('quote', ''), it.get('issue', ''),
               it.get('fix', ''), it.get('evidence', '')]
        if it.get('flag'):
            row[6] = '%s（%s）' % (row[6], it['flag'])
        for j, v in enumerate(row, start=1):
            c = ws.cell(row=r, column=j, value=v)
            c.font, c.border = BODY_FONT, BORDER
            c.alignment = Alignment(vertical='top', wrap_text=(j >= 6),
                                    horizontal='center' if j in (1, 3) else 'left')
        sev = it.get('sev', '')
        if sev in SEV_FILL:
            ws.cell(row=r, column=3).fill = PatternFill('solid', fgColor=SEV_FILL[sev])
        ws.row_dimensions[r].height = est_height(row, widths)
        r += 1

    ws.freeze_panes = 'A3'
    if r > hr + 1:
        ws.auto_filter.ref = 'A%d:%s%d' % (hr, get_column_letter(len(COLS)), r - 1)
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    # ---------------- 汇总 ----------------
    s = wb.create_sheet('汇总')
    s.column_dimensions['A'].width = 26
    s.column_dimensions['B'].width = 96

    def head(row, title):
        c = s.cell(row=row, column=1, value=title)
        c.font = Font(name=FONT_NAME, size=11, bold=True, color='FFFFFF')
        c.fill = HEAD_FILL
        c.alignment = Alignment(vertical='center')
        s.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        s.row_dimensions[row].height = 22
        return row + 1

    def line(row, a, b, bold=False):
        ca = s.cell(row=row, column=1, value=a)
        cb = s.cell(row=row, column=2, value=b)
        ca.font = Font(name=FONT_NAME, size=10, bold=bold)
        cb.font = Font(name=FONT_NAME, size=10, bold=bold)
        cb.alignment = Alignment(vertical='top', wrap_text=True)
        s.row_dimensions[row].height = est_height([a, b], [26, 96])
        return row + 1

    cnt = {k: 0 for k in SEV_ORDER}
    for it in items:
        if it.get('sev') in cnt:
            cnt[it['sev']] += 1

    row = 1
    row = head(row, '一、各级数量')
    for k in SEV_ORDER:
        row = line(row, k, cnt[k])
    row = line(row, '合计', len(items), bold=True)

    row += 1
    row = head(row, '二、问题类别分布')
    cat = {}
    for it in items:
        cat[it.get('cat', '未分类')] = cat.get(it.get('cat', '未分类'), 0) + 1
    for k, v in sorted(cat.items(), key=lambda x: -x[1]):
        row = line(row, k, '%d 条（%.1f%%）' % (v, v / max(len(items), 1) * 100))

    row += 1
    row = head(row, '三、最需优先处理的 3 个问题')
    for i, c in enumerate(data.get('top3', []), start=1):
        row = line(row, '%d. %s' % (i, c.get('t', '')), '%s　%s' % (c.get('d', ''), c.get('w', '')), bold=True)

    pending = [it for it in items if it.get('sev') == '待补']
    row += 1
    row = head(row, '四、待补资料清单（等客户提供后即可消除）')
    if pending:
        for i, it in enumerate(pending, start=1):
            row = line(row, 'B%d　%s' % (i, it.get('loc', '')), '%s｜%s' % (it.get('quote', ''), it.get('issue', '')))
    else:
        row = line(row, '无', '终稿或资料已齐。')

    row += 1
    row = head(row, '五、疑似问题清单（需人工复核）')
    sus = data.get('suspects', [])
    if sus:
        for i, x in enumerate(sus, start=1):
            row = line(row, 'S%d　%s' % (i, x.get('loc', '')), '%s｜%s' % (x.get('quote', ''), x.get('note', '')))
    else:
        row = line(row, '无', '')

    row += 1
    row = head(row, '六、验收标准自评')
    for a in data.get('accept', []):
        row = line(row, '%s　%s' % (a.get('no', ''), a.get('crit', '')), '%s　%s' % (a.get('res', ''), a.get('note', '')))

    try:
        wb.save(path)
    except PermissionError:
        raise SystemExit('写入失败：%s 正被占用（请先在 Excel 中关闭该文件）' % path)
    return path


# ---------------- HTML ----------------
def build_html(data, path):
    tpl = io.open(TEMPLATE, encoding='utf-8').read()
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    # 数据内联进 <script>，防三处破坏：</script> 提前闭合标签、U+2028/U+2029 破坏 JS 字符串字面量
    payload = (payload.replace('</', '<\\/')
                      .replace('\u2028', '\\u2028')
                      .replace('\u2029', '\\u2029'))
    new, n = re.subn(r'/\*BEGIN\*/.*?/\*END\*/', lambda m: '/*BEGIN*/' + payload + '/*END*/',
                     tpl, flags=re.S)
    if n != 1:
        raise SystemExit('模板数据块标记异常：找到 %d 处 /*BEGIN*/.../*END*/，应为 1 处' % n)
    io.open(path, 'w', encoding='utf-8').write(new)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('data')
    ap.add_argument('-o', '--outdir', default=None)
    ap.add_argument('-n', '--name', default=None)
    ap.add_argument('--only', choices=['html', 'xlsx', 'both'], default='both')
    args = ap.parse_args()

    with io.open(args.data, encoding='utf-8') as f:
        data = json.load(f)

    meta = data.get('meta', {})
    warn_evidence(data.get('items', []))
    if meta.get('stage') not in ('final', 'interim'):
        print('警告：meta.stage=%r 不合法（应为 final 或 interim），已按"中途版"处理。' % meta.get('stage'))
    outdir = args.outdir or os.path.dirname(os.path.abspath(args.data))
    os.makedirs(outdir, exist_ok=True)
    name = args.name or re.sub(r'\.(pdf|docx?|indd)$', '', os.path.basename(meta.get('file', '文档')), flags=re.I)

    if meta.get('stage') == 'final':
        pend = [i for i in data.get('items', []) if i.get('sev') == '待补']
        if pend:
            print('警告：stage=final 但存在 %d 条“待补”条目，终稿应按严重计。' % len(pend))

    made = []
    if args.only in ('html', 'both'):
        made.append(build_html(data, os.path.join(outdir, name + '_审校报告.html')))
    if args.only in ('xlsx', 'both'):
        made.append(build_xlsx(data, os.path.join(outdir, name + '_审校清单.xlsx')))

    print('已生成 %d 个文件：' % len(made))
    for p in made:
        print('  ' + os.path.abspath(p))


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
