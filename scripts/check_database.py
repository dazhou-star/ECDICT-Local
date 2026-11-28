import os
import sqlite3
import sys

path = os.environ.get("ECDICT_DB", os.path.join("database", "dictionary.db"))
if not os.path.isfile(path):
    print("数据库文件不存在：", path)
    raise SystemExit(2)

try:
    connection = sqlite3.connect(path)
    row = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
        ("table", "words"),
    ).fetchone()
    if not row or row[0] == 0:
        print("数据库中不存在 words 表：", path)
        raise SystemExit(2)

    count = connection.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    connection.close()
except sqlite3.Error as error:
    print("数据库检查失败：", error)
    raise SystemExit(2)

print("现有词条数：", count)
if count <= 0:
    raise SystemExit(2)
