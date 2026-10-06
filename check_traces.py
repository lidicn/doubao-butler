import sqlite3
conn = sqlite3.connect('/app/data/butler.db')
c = conn.cursor()

# 看表结构
c.execute("PRAGMA table_info(agent_traces)")
print('=== agent_traces 表结构 ===')
for row in c.fetchall():
    print(row)

# 统计成功/失败
c.execute("SELECT status, COUNT(*) FROM agent_traces GROUP BY status")
print('\n=== 状态统计 ===')
for row in c.fetchall():
    print(row)

# 看几条失败记录
c.execute("SELECT id, user_text, tool_name, tool_args, result, status, error FROM agent_traces WHERE status != 'ok' ORDER BY id DESC LIMIT 10")
print('\n=== 最近失败记录 ===')
for row in c.fetchall():
    print(row)

conn.close()
