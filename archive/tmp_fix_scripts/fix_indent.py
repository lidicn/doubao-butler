#!/usr/bin/env python3
"""Fix podcast endpoint indentation"""

import sys

filepath = '/vol1/1000/docker/doubao2api/doubao2api/unified_server.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# The podcast code is at top level, needs to be indented to 4 spaces
# Find the podcast block
start_marker = '# ── POST /v1/audio/podcast ──'
end_marker = '    # ── POST /v1/audio/speech ──'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print(f"Markers not found: start={start_idx}, end={end_idx}")
    sys.exit(1)

# Extract the podcast block
podcast_block = content[start_idx:end_idx]

# Add 4 spaces to each line
indented_lines = []
for line in podcast_block.split('\n'):
    if line.strip():
        indented_lines.append('    ' + line)
    else:
        indented_lines.append(line)
indented_block = '\n'.join(indented_lines)

# Replace
new_content = content[:start_idx] + indented_block + content[end_idx:]

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Fixed indentation!")
