# -*- coding: utf-8 -*-
"""ASCII 入口：避免 .bat / 命令行在非 UTF-8 代码页下解析中文文件名失败。

等价于直接运行「智能桌宠.py」，两者都可用。
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "智能桌宠.py")

if not os.path.isfile(ENTRY):
    sys.stderr.write("[ERROR] entry not found: %s\n" % ENTRY)
    sys.exit(1)

os.chdir(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

runpy.run_path(ENTRY, run_name="__main__")
