#!/usr/bin/env python3
"""Fix P1: Reuse httpx.AsyncClient in MemoryAgent"""

filepath = '/vol1/1000/docker/doubao-butler/butler/integrations/memory_agent.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 在 __init__ 里添加 _client 属性
old_init = '''    def __init__(self, settings: Settings):
        self.s = settings
        self.url = settings.memory_agent_mcp_url
        self.token = settings.memory_agent_token
        self._session_id: Optional[str] = None
        self._initialized = False'''

new_init = '''    def __init__(self, settings: Settings):
        self.s = settings
        self.url = settings.memory_agent_mcp_url
        self.token = settings.memory_agent_token
        self._session_id: Optional[str] = None
        self._initialized = False
        # P1 修复：复用 httpx.AsyncClient 单例
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self, timeout: float = 8) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=timeout)
        return self._client'''

content = content.replace(old_init, new_init)

# 2. 修改 _post 方法，使用单例
old_post = '''        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(self.url, headers=self._headers(), json=body)'''

new_post = '''        c = self._get_client(timeout=timeout)
        r = await c.post(self.url, headers=self._headers(), json=body)'''

content = content.replace(old_post, new_post)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("MemoryAgent httpx.AsyncClient reuse fixed!")
