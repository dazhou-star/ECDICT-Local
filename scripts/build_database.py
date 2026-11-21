#!/usr/bin/env python3
"""Import ECDICT CSV into an indexed SQLite database.

Place this project in E:\\ECDICT, beside ecdict.csv, then run:
    python scripts\\build_database.py
Optional:
    python scripts\\build_database.py --csv path\\to\\ecdict.csv --db database\\dictionary.db
"""
import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT

def quote_ident(name):
    return '"' + name.replace('"', '""') + '"'

def normalize_name(name):
    name = (name or "").strip()
    return name if name else "unnamed"

def import_csv(csv_path: Path, db_path: Path):
    if not csv_path.exists():
        raise FileNotFoundError(f"找不到 CSV：{csv_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        backup = db_path.with_suffix(db_path.suffix + ".bak")
        print(f"现有数据库将备份为：{backup}")
        if backup.exists():
            backup.unlink()
        db_path.replace(backup)

    # Read only header first; preserve every source column as TEXT.
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            raise ValueError("CSV 文件为空")
    headers = [normalize_name(h) for h in headers]
    if "word" not in headers:
        raise ValueError(f"CSV 缺少 word 字段，实际字段：{headers}")

    # Resolve duplicate column names safely without discarding values.
    seen = {}
    safe_headers = []
    for h in headers:
        n = seen.get(h, 0)
        seen[h] = n + 1
        safe_headers.append(h if n == 0 else f"{h}_{n+1}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-64000")
        columns_sql = ", ".join(
            f"{quote_ident(h)} TEXT" + (" PRIMARY KEY COLLATE NOCASE" if h == "word" else "")
            for h in safe_headers
        )
        conn.execute(f"CREATE TABLE words ({columns_sql})")
        placeholders = ",".join("?" for _ in safe_headers)
        insert_sql = f"INSERT OR REPLACE INTO words VALUES ({placeholders})"
        batch = []
        count = 0
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            original_headers = reader.fieldnames or []
            for row in reader:
                values = []
                for original, safe in zip(original_headers, safe_headers):
                    value = row.get(original)
                    if value is not None:
                        value = value.strip()
                    values.append(value)
                word_index = safe_headers.index("word")
                if not values[word_index]:
                    continue
                batch.append(values)
                if len(batch) >= 5000:
                    conn.executemany(insert_sql, batch)
                    conn.commit()
                    count += len(batch)
                    batch.clear()
                    print(f"\r已导入 {count:,} 条", end="", flush=True)
            if batch:
                conn.executemany(insert_sql, batch)
                count += len(batch)
        conn.commit()

        # Fast exact lookup and prefix autocomplete.
        conn.execute('CREATE INDEX IF NOT EXISTS idx_words_word_nocase ON words(word COLLATE NOCASE)')
        for field in ("frq", "bnc", "collins", "oxford"):
            if field in safe_headers:
                conn.execute(f"CREATE INDEX IF NOT EXISTS {quote_ident('idx_words_' + field)} ON words({quote_ident(field)})")
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        metadata = {
            "source_csv": str(csv_path),
            "imported_entries": str(conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]),
            "source_fields": json.dumps(safe_headers, ensure_ascii=False),
            "encoding": "UTF-8",
        }
        conn.executemany("INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)", metadata.items())

        # Optional auxiliary files are preserved in raw text tables. Their format differs
        # across ECDICT revisions, so no guessed parsing or destructive normalization.
        for filename, table in [
            ("lemma.en.txt", "lemma_raw"),
            ("wordroot.txt", "wordroot_raw"),
            ("resemble.txt", "resemble_raw"),
        ]:
            source = csv_path.parent / filename
            if source.exists():
                conn.execute(f"DROP TABLE IF EXISTS {table}")
                conn.execute(f"CREATE TABLE {table} (line_no INTEGER PRIMARY KEY, content TEXT)")
                with source.open("r", encoding="utf-8-sig", errors="replace") as f:
                    conn.executemany(
                        f"INSERT INTO {table}(content) VALUES (?)",
                        ((line.rstrip("\r\n"),) for line in f)
                    )
                print(f"已保留辅助文件：{filename}")

        conn.commit()
        print(f"\n导入完成：{conn.execute('SELECT COUNT(*) FROM words').fetchone()[0]:,} 条")
        print(f"字段数：{len(safe_headers)}")
        print(f"数据库：{db_path}")
        print("说明：所有 CSV 字段均以 TEXT 原样保留；排序/数值显示由 API 或前端解释。")
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(REPO_ROOT / "ecdict.csv"))
    parser.add_argument("--db", default=str(PROJECT_ROOT / "database" / "dictionary.db"))
    args = parser.parse_args()
    import_csv(Path(args.csv).resolve(), Path(args.db).resolve())

if __name__ == "__main__":
    main()
