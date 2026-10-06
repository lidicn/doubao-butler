#!/usr/bin/env python3
"""Fix audio_url check to only accept full https URLs"""

filepath = '/vol1/1000/docker/doubao2api/doubao2api/client.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the check condition
old_code = '''                    audio_url = playback.get("audio_link", "")
                    if audio_url:
                        result["audio_url"] = audio_url
                        result["title"] = meta.get("title", "")
                        result["duration"] = playback.get("duration", 0)
                        result["status"] = "completed"
                        log.info("Podcast ready: %s", result["audio_url"])
                        return result'''

new_code = '''                    audio_url = playback.get("audio_link", "")
                    # Only accept full https URLs (relative paths mean podcast is still generating)
                    if audio_url and audio_url.startswith("https://"):
                        result["audio_url"] = audio_url
                        result["title"] = meta.get("title", "") or meta.get("podcast_title", "")
                        result["duration"] = playback.get("duration", 0)
                        result["status"] = "completed"
                        log.info("Podcast ready: %s", result["audio_url"])
                        return result'''

if old_code in content:
    content = content.replace(old_code, new_code)
    print("Replaced!")
else:
    print("Old code not found!")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
