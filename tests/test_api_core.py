"""
API 层核心端点测试
测试健康检查、登录状态等基础端点
"""
import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client():
    """创建测试客户端。"""
    from butler.app import create_app
    app = create_app()
    return TestClient(app)


def test_health_check(client):
    """测试健康检查端点。"""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data or "ok" in data


def test_login_status(client):
    """测试登录状态端点。"""
    response = client.get("/api/login/status")
    assert response.status_code == 200
    data = response.json()
    # 应该返回是否需要登录
    assert isinstance(data, dict)


def test_404_not_found(client):
    """测试 404 处理。"""
    response = client.get("/api/nonexistent_endpoint")
    assert response.status_code == 404


def test_static_files_served(client):
    """测试静态文件服务。"""
    response = client.get("/")
    # 应该返回 WebUI 页面，或者重定向到 /admin
    assert response.status_code in (200, 302, 307)
