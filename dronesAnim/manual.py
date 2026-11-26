"""Open the bundled offline manual in the user's browser."""

from pathlib import Path

import bpy


def open_manual(operator, page):
    path = Path(__file__).resolve().parent / "help" / "site" / page
    if not path.is_file():
        operator.report({"ERROR"}, "帮助手册文件缺失，请重新安装完整插件包")
        return {"CANCELLED"}
    try:
        result = bpy.ops.wm.url_open(url=path.as_uri())
    except RuntimeError as exc:
        operator.report({"ERROR"}, f"无法打开帮助手册：{exc}")
        return {"CANCELLED"}
    return result
