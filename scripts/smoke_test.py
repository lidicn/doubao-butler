#!/usr/bin/env python3
"""
豆包管家 端到端冒烟测试脚本
启动后验证核心功能是否正常
"""
import asyncio
import sys
import time
from typing import Tuple


def check_health(base_url: str) -> Tuple[bool, str]:
    """检查健康检查端点。"""
    import httpx
    try:
        r = httpx.get(f"{base_url}/api/health", timeout=5)
        if r.status_code == 200:
            return True, f"健康检查通过 ({r.status_code})"
        return False, f"健康检查失败 ({r.status_code})"
    except Exception as e:
        return False, f"健康检查异常: {e}"


def check_login_status(base_url: str) -> Tuple[bool, str]:
    """检查登录状态端点。"""
    import httpx
    try:
        r = httpx.get(f"{base_url}/api/login/status", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return True, f"登录状态正常 (需登录: {data.get('need_login', 'unknown')})"
        return False, f"登录状态检查失败 ({r.status_code})"
    except Exception as e:
        return False, f"登录状态检查异常: {e}"


def check_webui(base_url: str) -> Tuple[bool, str]:
    """检查 WebUI 是否可访问。"""
    import httpx
    try:
        r = httpx.get(f"{base_url}/", timeout=5, follow_redirects=True)
        if r.status_code == 200:
            return True, f"WebUI 可访问 ({len(r.text)} bytes)"
        return False, f"WebUI 不可访问 ({r.status_code})"
    except Exception as e:
        return False, f"WebUI 检查异常: {e}"


def check_api_docs(base_url: str) -> Tuple[bool, str]:
    """检查 API 文档是否可访问。"""
    import httpx
    try:
        r = httpx.get(f"{base_url}/docs", timeout=5, follow_redirects=True)
        if r.status_code == 200:
            return True, "API 文档可访问"
        return False, f"API 文档不可访问 ({r.status_code})"
    except Exception as e:
        return False, f"API 文档检查异常: {e}"


async def check_mqtt_connection() -> Tuple[bool, str]:
    """检查 MQTT 连接状态。"""
    try:
        from butler.runtime import get_runtime
        rt = get_runtime()
        if rt.mqtt and rt.mqtt.connected.is_set():
            return True, "MQTT 已连接"
        return False, "MQTT 未连接"
    except Exception as e:
        return False, f"MQTT 检查异常: {e}"


async def check_database() -> Tuple[bool, str]:
    """检查数据库连接。"""
    try:
        from butler.store.db import get_conn
        conn = get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        count = cursor.fetchone()[0]
        if count > 0:
            return True, f"数据库正常 ({count} 张表)"
        return False, "数据库无表"
    except Exception as e:
        return False, f"数据库检查异常: {e}"


async def run_smoke_tests(base_url: str = "http://192.168.2.200:8095") -> None:
    """运行所有冒烟测试。"""
    print("=" * 60)
    print("🚀 豆包管家 端到端冒烟测试")
    print("=" * 60)
    print(f"目标: {base_url}")
    print()

    tests = [
        ("健康检查", lambda: check_health(base_url)),
        ("登录状态", lambda: check_login_status(base_url)),
        ("WebUI 访问", lambda: check_webui(base_url)),
        ("API 文档", lambda: check_api_docs(base_url)),
        ("数据库连接", check_database),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            if asyncio.iscoroutinefunction(test_func):
                ok, msg = await test_func()
            else:
                ok, msg = test_func()
            status = "✅" if ok else "❌"
            print(f"{status} {name}: {msg}")
            if ok:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ {name}: 异常 - {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"📊 结果: {passed} 通过 / {failed} 失败 / {len(tests)} 总计")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
    else:
        print("🎉 所有冒烟测试通过！")
        sys.exit(0)


if __name__ == "__main__":
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.2.200:8095"
    asyncio.run(run_smoke_tests(base_url))
