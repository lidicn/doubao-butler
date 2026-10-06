"""Docker 运维封装：通过 docker.sock 调用 docker CLI（容器内需安装 docker CLI 并挂载 /var/run/docker.sock）。

与 new-api 封装配合：修改 new-api 数据库后调用 restart("new-api") 使配置生效。
"""
from __future__ import annotations

import asyncio
import subprocess

from butler.logging_setup import get_logger

logger = get_logger("butler.integrations.docker_tools")

# P0-7: 白名单 — 防止 LLM 给任意路径/容器名
_ALLOWED_PROJECT_PREFIXES = ("/vol1/1000/docker/")
_ALLOWED_CONTAINERS = {
    "doubao-butler", "new-api", "tvpilot", "memory-agent",
    "autoforge", "node-red", "homeassistant", "mqtt",
    "mariadb", "redis", "watchtower",
}


def _validate_project_dir(project_dir: str) -> str | None:
    """校验 project_dir 在白名单前缀下，返回规范化路径或 None。"""
    from pathlib import Path
    try:
        resolved = str(Path(project_dir).resolve())
    except Exception:
        return None
    if not resolved.startswith(_ALLOWED_PROJECT_PREFIXES):
        return None
    return resolved


def _validate_container(name: str) -> bool:
    """校验容器名在白名单内。"""
    return name in _ALLOWED_CONTAINERS


class DockerError(Exception):
    pass


class DockerClient:
    """Docker CLI 封装。所有方法返回 dict，失败时 ok=False + error。"""

    def __init__(self, socket_path: str | None = None, timeout: int = 60):
        import os
        # P0-10: 优先用环境变量 DOCKER_HOST（支持 tcp:// 代理），默认回退 unix socket
        self.docker_host = socket_path or os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
        self.timeout = timeout

    async def _run(self, args: list[str], timeout: int | None = None) -> tuple[int, str, str]:
        """执行 docker 命令（async，通过 to_thread 避免阻塞事件循环）。"""
        return await asyncio.to_thread(self._run_sync, args, timeout)

    def _run_sync(self, args: list[str], timeout: int | None = None) -> tuple[int, str, str]:
        """同步执行 docker 命令，返回 (returncode, stdout, stderr)。"""
        cmd = ["docker"] + args
        try:
            p = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout or self.timeout,
                env={"DOCKER_HOST": self.docker_host,
                     "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
            )
            return p.returncode, p.stdout.strip(), p.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "command timeout"
        except FileNotFoundError:
            return -1, "", "docker CLI not found (需在 Dockerfile 中安装 docker CLI)"
        except Exception as e:
            return -1, "", str(e)

    # ── 容器管理 ────────────────────────────────────────────

    async def ps(self, all_containers: bool = False) -> dict:
        """列出容器。返回结构化列表（名称/状态/镜像/端口）。"""
        args = ["ps", "--format", "{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}|{{.Ports}}"]
        if all_containers:
            args.insert(1, "-a")
        rc, out, err = await self._run(args)
        if rc != 0:
            return {"ok": False, "tool": "docker_ps", "error": "internal_error", "message": err or out}
        containers = []
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) >= 4:
                containers.append({
                    "id": parts[0][:12],
                    "name": parts[1],
                    "status": parts[2],
                    "image": parts[3],
                    "ports": parts[4] if len(parts) > 4 else "",
                })
        return {"ok": True, "tool": "docker_ps", "result": {"count": len(containers), "containers": containers}}

    async def restart(self, container_name: str) -> dict:
        """重启指定容器（白名单内）。"""
        if not container_name:
            return {"ok": False, "tool": "docker_restart", "error": "invalid_parameter", "message": "container_name 不能为空"}
        if not _validate_container(container_name):
            return {"ok": False, "tool": "docker_restart", "error": "permission_denied", "message": f"container {container_name} not in whitelist"}
        rc, out, err = await self._run(["restart", container_name], timeout=30)
        if rc != 0:
            return {"ok": False, "tool": "docker_restart", "error": "internal_error", "message": err or out}
        # 验证容器状态
        time.sleep(2) if False else None  # 不 sleep，让调用方观察
        rc2, out2, _ = await self._run(["inspect", "-f", "{{.State.Status}}", container_name], timeout=10)
        status = out2 if rc2 == 0 else "unknown"
        return {"ok": True, "tool": "docker_restart", "result": {"container": container_name, "status": status}}

    async def logs(self, container_name: str, tail: int = 50) -> dict:
        """获取容器日志（白名单内，最后 N 行）。"""
        if not container_name:
            return {"ok": False, "tool": "docker_logs", "error": "invalid_parameter", "message": "container_name 不能为空"}
        if not _validate_container(container_name):
            return {"ok": False, "tool": "docker_logs", "error": "permission_denied", "message": f"container {container_name} not in whitelist"}
        rc, out, err = await self._run(["logs", "--tail", str(tail), container_name], timeout=15)
        if rc != 0:
            return {"ok": False, "tool": "docker_logs", "error": "internal_error", "message": err or out}
        # 限制返回长度，避免 token 爆炸
        lines = out.splitlines()
        if len(lines) > tail:
            lines = lines[-tail:]
        return {"ok": True, "tool": "docker_logs",
                "result": {"container": container_name, "line_count": len(lines), "logs": "\n".join(lines)}}

    # ── docker compose ──────────────────────────────────────

    async def compose_up(self, project_dir: str, services: list[str] | None = None,
                   build: bool = True, timeout: int = 300) -> dict:
        """在指定目录执行 docker compose up -d [--build] [services...]。

        project_dir 是 NAS 宿主机上的路径（如 /vol1/1000/docker/doubao-butler）。
        注意：容器内需要能访问这个路径（需要挂载），或者通过 docker 上下文。
        当前实现：假设 project_dir 已挂载到容器内相同路径。
        """
        if not project_dir:
            return {"ok": False, "tool": "docker_compose_up", "error": "invalid_parameter", "message": "project_dir 不能为空"}
        safe_dir = _validate_project_dir(project_dir)
        if not safe_dir:
            return {"ok": False, "tool": "docker_compose_up", "error": "permission_denied", "message": "project_dir not in whitelist prefix"}
        args = ["compose", "-f", f"{safe_dir}/docker-compose.yml", "up", "-d"]
        if build:
            args.append("--build")
        if services:
            args.extend(services)
        rc, out, err = await self._run(args, timeout=timeout)
        if rc != 0:
            return {"ok": False, "tool": "docker_compose_up", "error": "operation_timeout" if "timeout" in err else "internal_error",
                    "message": (err or out)[-500:]}
        return {"ok": True, "tool": "docker_compose_up",
                "result": {"project_dir": project_dir, "services": services or ["all"],
                           "output": out[-500:]}}

    async def compose_down(self, project_dir: str, services: list[str] | None = None,
                     timeout: int = 120) -> dict:
        """停止指定服务（docker compose down）。危险操作，需确认。"""
        if not project_dir:
            return {"ok": False, "tool": "docker_compose_down", "error": "invalid_parameter", "message": "project_dir 不能为空"}
        safe_dir = _validate_project_dir(project_dir)
        if not safe_dir:
            return {"ok": False, "tool": "docker_compose_down", "error": "permission_denied", "message": "project_dir not in whitelist prefix"}
        args = ["compose", "-f", f"{safe_dir}/docker-compose.yml", "down"]
        if services:
            args.extend(services)
        rc, out, err = await self._run(args, timeout=timeout)
        if rc != 0:
            return {"ok": False, "tool": "docker_compose_down", "error": "internal_error", "message": (err or out)[-500:]}
        return {"ok": True, "tool": "docker_compose_down",
                "result": {"project_dir": project_dir, "services": services or ["all"]}}
