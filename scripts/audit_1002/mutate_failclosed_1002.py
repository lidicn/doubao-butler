"""一次性变异探针（⛔ 进生产码、⛔ 留盘）：证明对齐后的那例真会咬。

第 1 步按现状跑，期望 PASS；第 2 步把 MemoryAgentClient.retrieve 换成 §三 之前的
「缺主体照样外发」姿势，同一例必须 AssertionError——否则那条 assert c.calls == []
是恒真断言。只在本进程内替换属性，盘上一个字节都不动。
"""
import sys
import asyncio

sys.path.insert(0, "/vol1/1000/docker/doubao-butler")

from butler.integrations.memory_agent import MemoryAgentClient  # noqa: E402
import tests.test_ma_client_args as t  # noqa: E402

t.test_retrieve_without_member_sends_nothing()
t.test_fail_closed_guard_makes_the_difference()
print("BASELINE|both PASS")


async def leaky(self, query, member="", limit=5):      # 拆门变异：复刻 §三 之前的姿势
    args = {"query": query, "top_k": max(1, int(limit))}
    if member:
        args["member_id"] = member
    await self.call_tool("retrieve_agent_memories", args)
    return []


MemoryAgentClient.retrieve = leaky
try:
    t.test_retrieve_without_member_sends_nothing()
except AssertionError as e:
    print("MUTANT|BIT %s" % str(e)[:80])
else:
    print("MUTANT|NOT-BIT 尺无效")
    sys.exit(1)
