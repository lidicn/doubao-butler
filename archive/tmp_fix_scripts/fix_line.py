#!/usr/bin/env python3
"""Fix line 2555 indentation"""

filepath = '/vol1/1000/docker/doubao2api/doubao2api/unified_server.py'

with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix line 2555 (index 2554)
lines[2554] = '    # ── POST /v1/audio/podcast ──\n'

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Fixed line 2555")
