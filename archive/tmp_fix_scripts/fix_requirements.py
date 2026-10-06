#!/usr/bin/env python3
"""Add requests to requirements.txt"""

filepath = '/vol1/1000/docker/doubao-butler/requirements.txt'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

if 'requests' not in content:
    content += '\nrequests==2.32.3\ncryptography==43.0.1\n'
    print("Added!")
else:
    print("Already exists!")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
