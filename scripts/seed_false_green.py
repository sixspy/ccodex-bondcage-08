#!/usr/bin/env python3
"""本机文件库假绿事故造数脚本（库路径默认 data/app.db 不变）。

用法：
  python3 -I scripts/seed_false_green.py            # 造事故并打印现场
  BONDCAGE_DB=data/app-demo.db python3 -I scripts/seed_false_green.py
      # 基线 data/app.db 只读时，指到同目录另一文件库（路径仍在 data/ 下）

脚本目录与解释器隔离：以 -I 运行，backend 路径在脚本内显式注入。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# -I 隔离模式下 venv 的 site-packages 不在 sys.path，显式补入（若存在）
for sp in (ROOT / ".venv" / "lib").glob("python*/site-packages") if (ROOT / ".venv").exists() else []:
    sys.path.insert(0, str(sp))

# 基线库只读时的同目录回退文件库；可写时仍用 data/app.db，库路径口径不变
if not os.access(ROOT / "data" / "app.db", os.W_OK):
    os.environ.setdefault("BONDCAGE_DB", str(ROOT / "data" / "app-demo.db"))

from fastapi.testclient import TestClient  # noqa: E402

from accident_fixture import build_false_green_accident  # noqa: E402
from app import app  # noqa: E402
from db import DB_PATH, init_db  # noqa: E402


def main() -> int:
    init_db()
    print(f"[fixture] 库路径：{DB_PATH}")
    with TestClient(app) as client:
        scene = build_false_green_accident(client)
    print("[fixture] 假绿事故现场：")
    for k, v in scene.items():
        print(f"  - {k}: {v}")
    if scene["false_green"]:
        print("[fixture] 结论：假绿坐实——罚没在册、笼面抽空、无回滚口。")
        return 0
    print("[fixture] 结论：回滚口已存在，事故可救济，不再是假绿。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
