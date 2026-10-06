import asyncio
from butler.config import get_settings
from butler.integrations.ha import HAClient

async def main():
    s = get_settings()
    ha = HAClient(s)
    await ha.call_service("media_player", "media_stop", {"entity_id": "media_player.xiaomi_lx06_a137_play_control"})
    print("stopped")

asyncio.run(main())
