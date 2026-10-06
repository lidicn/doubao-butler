#!/usr/bin/env python3
"""Add nowvoice to TTSManager"""

filepath = '/vol1/1000/docker/doubao-butler/butler/tts/manager.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 添加 import
old_import = '''from butler.tts.edge_tts import EdgeTTS
from butler.tts.kokoro import KokoroTTS'''

new_import = '''from butler.tts.edge_tts import EdgeTTS
from butler.tts.kokoro import KokoroTTS
from butler.tts.nowvoice_tts import NowVoiceTTS'''

content = content.replace(old_import, new_import)

# 2. 在 __init__ 里添加 nowvoice
old_init = '''        self.edge = EdgeTTS(settings)
        self.kokoro = KokoroTTS(settings)
        self.bark = bark'''

new_init = '''        self.edge = EdgeTTS(settings)
        self.kokoro = KokoroTTS(settings)
        self.nowvoice = NowVoiceTTS(settings)
        self.bark = bark'''

content = content.replace(old_init, new_init)

# 3. 在 synthesize 里添加 nowvoice 分支
old_synthesize = '''        # 主引擎（按角色指定 backend，避免 kokoro 音色名误打到 edge-tts 导致静默）
        try:
            if backend == "edge-tts":
                return await self.edge.synthesize(text, voice, speed)
            return await self.kokoro.synthesize(text, voice, speed)
        except Exception as e:
            logger.warning("TTS primary(%s) failed: %s", backend, e)'''

new_synthesize = '''        # 主引擎（按角色指定 backend，避免 kokoro 音色名误打到 edge-tts 导致静默）
        try:
            if backend == "edge-tts":
                return await self.edge.synthesize(text, voice, speed)
            elif backend == "nowvoice":
                return await self.nowvoice.synthesize(text, voice, speed)
            return await self.kokoro.synthesize(text, voice, speed)
        except Exception as e:
            logger.warning("TTS primary(%s) failed: %s", backend, e)'''

content = content.replace(old_synthesize, new_synthesize)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated!")
