import asyncio
from butler.core.ha_client import HAClient

async def play():
    ha = HAClient()
    await ha.connect()
    states = await ha.get_states()
    for eid, st in states.items():
        if "lx06_a137" in eid and eid.startswith("media_player"):
            print("found:", eid, st.get("state"))
    await ha.close()

asyncio.run(play())
