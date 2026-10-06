#!/usr/bin/env python3
"""Fix perception table schema"""

import sqlite3
from pathlib import Path

DB_PATH = Path("/app/data/butler.db")

conn = sqlite3.connect(str(DB_PATH))
c = conn.cursor()

# 检查并添加 llm_analysis_json 列
try:
    c.execute("ALTER TABLE perception_reports ADD COLUMN llm_analysis_json TEXT")
    print("Added llm_analysis_json column")
except sqlite3.OperationalError as e:
    if "duplicate column" in str(e):
        print("Column already exists")
    else:
        raise

# 检查并创建 candidate_rules 表
c.execute("""
    CREATE TABLE IF NOT EXISTS candidate_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL,
        name TEXT,
        description TEXT,
        conditions_json TEXT,
        infer TEXT,
        status TEXT DEFAULT 'pending',
        report_id INTEGER
    )
""")
print("candidate_rules table ready")

conn.commit()
conn.close()
print("Done!")
