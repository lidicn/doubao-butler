"""
pytest 全局配置文件
统一测试框架到 pytest
"""
import asyncio
import os
import sys

import pytest

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="session")
def event_loop():
    """创建一个 session 级别的事件循环，供所有异步测试使用。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_settings():
    """测试用配置。"""
    from butler.config import Settings
    return Settings(
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_user="test",
        mqtt_password="test",
        new_api_url="http://localhost:3001/v1",
        new_api_key="test-key",
        doubao_api_url="http://localhost:9090/v1/chat/completions",
        doubao_api_key="test-key",
    )


@pytest.fixture
def sample_event():
    """测试用事件。"""
    from butler.bus.topics import ButlerEvent
    return ButlerEvent(
        kind="face",
        room="客厅",
        member="lidicn",
        confidence=0.95,
        payload={"name": "lidicn", "confidence": 0.95},
    )
