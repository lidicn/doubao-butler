#!/usr/bin/env python3
"""迁移 _assign_task_to_tp/dp 从旧脚本到 DeskPilot PM 管道 API"""
import re

path = '/vol1/1000/docker/doubao-butler/butler/core/tools.py'
with open(path, 'r', encoding='utf-8') as f:
    code = f.read()

# === 1. 替换 _assign_task_to_tp 的消息发送部分 ===
# 旧代码：base64 编码 + dpk.system_run(python, [pm_send_to_tp.py, b64])
# 新代码：直接调用 _deskpilot_pm_send("TP", message)

old_tp_send = '''    b64 = base64.b64encode(message.encode("utf-8")).decode("ascii")

    dpk = getattr(agent, "deskpilot", None)
    if dpk is None:
        return f"DeskPilot 客户端未初始化，但任务 {task_id} 已创建。"

    try:
        r = await dpk.system_run(
            r"C:\\Users\\lidicn\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
            [r"C:\\tools\\pm_send_to_tp.py", b64],
            timeout=30.0,
        )
        ok = r.get("ok", False) if isinstance(r, dict) else False
        if ok:
            return (f"✅ TP 工单已创建并发送：{task_id}「{title}」（{priority}）。"
                    f"TP 接手/完成时会通过 API 上报状态，PM 可在看板实时跟踪。"
                    f"注意：需电脑未锁屏，否则消息发送失败。")
        err = r.get("message") or r.get("error") or str(r)
        return f"⚠️ 任务 {task_id} 已创建，但消息发送失败：{err}。TP 可在看板查看任务。"
    except Exception as e:
        logger.warning("assign_task_to_tp failed: %s", e)
        return f"⚠️ 任务 {task_id} 已创建，但消息发送异常：{e}"'''

new_tp_send = '''    # 通过 DeskPilot PM 管道发送到 TP 对话（target=TP，Ctrl+2）
    ok, err = await _deskpilot_pm_send("TP", message)
    if ok:
        return (f"✅ TP 工单已创建并发送：{task_id}「{title}」（{priority}）。"
                f"TP 接手/完成时会通过 API 上报状态，PM 可在看板实时跟踪。"
                f"注意：需电脑未锁屏，否则消息发送失败。")
    return f"⚠️ 任务 {task_id} 已创建，但消息发送失败：{err}。TP 可在看板查看任务。"'''

if old_tp_send in code:
    code = code.replace(old_tp_send, new_tp_send)
    print('_assign_task_to_tp: 消息发送已迁移到 DeskPilot API ✅')
else:
    print('_assign_task_to_tp: 未找到旧代码，可能已迁移')

# === 2. 替换 _assign_task_to_dp 的消息通知部分 ===
# 保留交接单文件生成（pm_send_to_dp.py），但消息通知改用 _deskpilot_pm_send

old_dp_msg = '''        # 3. 发消息到 DP 对话通知
        msg = (
            f"【PM 工单】{title}\\n"
            f"任务ID：{task_id} | 优先级：{priority} | 类型：{task_type}\\n"
            f"交接单已生成到 DeskPilot/docs/，请查看。\\n"
            f"【状态上报】接手：{report_cmd}\\n"
            f"完成：{complete_cmd}"
        )
        msg_b64 = base64.b64encode(msg.encode("utf-8")).decode("ascii")
        await dpk.system_run(
            r"C:\\Users\\lidicn\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
            [r"C:\\tools\\pm_send_to_dp_msg.py", msg_b64],
            timeout=30.0,
        )'''

new_dp_msg = '''        # 3. 通过 DeskPilot PM 管道发消息到 DP 对话通知（target=DP，Ctrl+3）
        msg = (
            f"【PM 工单】{title}\\n"
            f"任务ID：{task_id} | 优先级：{priority} | 类型：{task_type}\\n"
            f"交接单已生成到 DeskPilot/docs/，请查看。\\n"
            f"【状态上报】接手：{report_cmd}\\n"
            f"完成：{complete_cmd}"
        )
        await _deskpilot_pm_send("DP", msg)'''

if old_dp_msg in code:
    code = code.replace(old_dp_msg, new_dp_msg)
    print('_assign_task_to_dp: 消息通知已迁移到 DeskPilot API ✅')
else:
    print('_assign_task_to_dp: 未找到旧消息通知代码，可能已迁移')

# === 3. 清理不再需要的 base64 import（如果函数内不再使用）===
# _assign_task_to_tp 不再需要 base64，但 _assign_task_to_dp 还需要（交接单生成用）
# 所以保留 import base64

with open(path, 'w', encoding='utf-8') as f:
    f.write(code)

# 验证
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()
print()
print('=== 验证 ===')
print('旧脚本 pm_send_to_tp.py 引用:', '❌ 仍存在' if 'pm_send_to_tp.py' in c else '✅ 已清除')
print('旧脚本 pm_send_to_dp_msg.py 引用:', '❌ 仍存在' if 'pm_send_to_dp_msg.py' in c else '✅ 已清除')
print('pm_send_to_dp.py 引用（交接单生成，保留）:', '✅ 保留' if 'pm_send_to_dp.py' in c else '⚠️ 被误删')
print('_deskpilot_pm_send("TP" 调用:', '✅' if '_deskpilot_pm_send("TP"' in c else '❌')
print('_deskpilot_pm_send("DP" 调用:', '✅' if '_deskpilot_pm_send("DP"' in c else '❌')
print('文件大小:', len(c), 'bytes')
print('完成！')
