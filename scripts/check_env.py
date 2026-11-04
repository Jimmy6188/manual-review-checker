#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_env.py — 审校技能环境自检。

用法:
    python scripts/check_env.py

检查 Python 版本与 pymupdf / openpyxl 是否可用，缺什么给出安装命令。
"""
import sys

MIN = (3, 9)
OK = '[OK]  '
BAD = '[缺]  '


def main():
    problems = []

    v = sys.version_info
    if v[:2] >= MIN:
        print('%s Python %d.%d.%d' % (OK, v[0], v[1], v[2]))
    else:
        print('%s Python %d.%d.%d（需 3.9+）' % (BAD, v[0], v[1], v[2]))
        problems.append('升级 Python 到 3.9 及以上')

    for mod, pkg in (('pymupdf', 'pymupdf'), ('openpyxl', 'openpyxl')):
        alt = 'fitz' if mod == 'pymupdf' else None
        try:
            m = __import__(mod)
        except ImportError:
            if alt:
                try:
                    m = __import__(alt)
                    ver = getattr(m, '__version__', '') or getattr(m, 'version', '')
                    print('%s %s（经 %s 兼容）%s' % (OK, mod, alt, ver))
                    continue
                except ImportError:
                    pass
            print('%s %s 未安装' % (BAD, mod))
            problems.append('pip install %s' % pkg)
            continue
        ver = getattr(m, '__version__', '') or getattr(m, 'version', '')
        print('%s %s %s' % (OK, mod, ver))

    print()
    if problems:
        print('需要处理：')
        for p in problems:
            print('  - ' + p)
        print('\n装包命令（国内网络慢可加 -i https://mirrors.aliyun.com/pypi/simple）：')
        print('  pip install pymupdf openpyxl')
        sys.exit(1)
    else:
        print('环境就绪，可以开始审校。')
        print('python 路径：%s' % sys.executable)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
