import httpx
try:
    r = httpx.get("https://speech.platform.bing.com", timeout=10)
    print(f"EdgeTTS reachable: {r.status_code}")
except Exception as e:
    print(f"EdgeTTS unreachable: {e}")

try:
    r = httpx.get("https://api.nowvoice.ai", timeout=10)
    print(f"NowVoice reachable: {r.status_code}")
except Exception as e:
    print(f"NowVoice unreachable: {e}")
