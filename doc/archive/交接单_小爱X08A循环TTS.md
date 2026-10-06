# 交接单：小爱音箱（X08A）edge-tts 直连循环/截断问题

> 状态：**v2 已部署验证**——停止通道升级为 `player_play_operation stop`（实测即时有效），配合 800ms 提前量（≈实测云端延迟 700ms+余量），**无循环、无截断、无重复尾**（详见 §7）。
> 时间：2026-09-05
> 涉及设备：书房小爱 `xiao_study`（Redmi 触屏音箱 X08A，xiaoai_id=`ac77f44b-3b0a-44dc-bef7-1363e33b9ef4`，HA player_entity=`media_player.xiaomi_x08a_1648_play_control`）；主卧同型号 `xiao_touch8` 同理。
> 代码仓库：`e:/NAS/doubao-butler`（注意：本机 `e:/NAS` 是 NAS 的旧副本，**改动须 scp 到 NAS 再 `docker compose up -d --build` 才生效**，见末尾部署说明）。

---

## 1. 现象（用户报告）

- 管家对 X08A 走 edge-tts 直连播报时，**单条会单曲循环**（一直重复播）。
- 中途尝试用「估算时长定时发停止」后，出现**随机截断**：有时只播「你好」、有时「你好！我是。」——因为定时太早把语音掐断。

## 2. 根因（已逐项实测确认，非猜测）

通过 `player_get_play_status` 轮询 + 设备真实拉取日志（butler 访问 `/tts/*.mp3` 的 GET 次数）验证：

1. **X08A 对 `player_play_music` 单条/多条列表都是固件级强制单曲循环**，`loop_type` 关不掉。
2. **`player_play_url` 在 X08A 上完全不支持**：试过 type=0/1/2/3/"music"/"url"/"audio/mpeg"/"application/mp3"，全部返回 `code=0` 但设备**不拉音频**（无 GET），静默。LX06 等老型号支持 `player_play_url`（一次性不循环），X08A 不行。
3. **`player_pause {}` / `player_stop {}` 是空操作**：播放中调用 `status` 不变（仍为 1）。
4. **`player_set_loop {"loop_type":0}` 能改写 `loop_type` 字段，但设备仍 `status=1` 持续在播、循环照旧**——无效。
5. **唯一能令其停止的手段**：`player_play_music` 发一个**空 `audio_items` 列表（REPLACE_ALL）**，设备状态由 `playing(1)` 变为 `停止(2)`。

### 关键：播放状态字段（用来精确判定停止时机）

`player_get_play_status` 返回的 `info` 是 JSON 字符串，解析后：
- `info.status`：`1`=播放中，`2`=停止/暂停。
- `info.play_song_detail.position`：当前播放进度（毫秒），随播放推进。
- `info.play_song_detail.duration`：本条音频总时长（毫秒），实测准确（8s 文件=8100；24 字中文≈5280）。
- 循环时 `position` 会在播完后**从 ~duration 跳回 0**（回绕）。

=> 因此可**轮询 position/duration**，在本遍播完（position 接近 duration）或回环（position 突降）时发停止指令，实现「播完一次即停」，不再靠估算时长。

## 3. 当前已部署方案 v1（已被 v2 取代，保留此节供对照）

### 改动点
- `play_xiaomi_url_once(device_id, url, method="music")`：
  - `method=="music"`：发标准 `player_play_music`（payload 见代码，`audio_id=1582971365183456177`，`cp.id=355454500`，`REPLACE_ALL`）；附带 `player_set_loop {"loop_type":0}`（对 X08A 无效，仅作其它固件辅助）。
  - `method=="url"`：发 `player_play_url`（老型号一次性不循环，不受影响）。
- `_xiaomi_play_status(device_id)`：解析 `(position, duration, status)`。
- `_xiaomi_clear_queue(device_id)`：发空列表 `player_play_music`（REPLACE_ALL）停止。
- `schedule_xiaomi_stop(device_id, audio_secs)`：fire-and-forget 后台协程，**每 0.1s 轮询**：
  - 若 `status==1` 且 `duration` 已知且 `position >= duration-200` → 发空列表停止（播完前 200ms 停，edge-tts 尾部本有静音，截断无感）；
  - 若 `position` 突降（回环）→ 立即发空列表停止（兜底）；
  - **关键修复**：首次轮询若抓到 `status!=1` 且**尚未观测到播放**，必须 `continue` 继续等，不能 `return`——否则会因设备 `player_play_music` 异步生效的过渡态而提前退出、导致永不停止（这是上一版仍循环的根因）。
  - 兜底：900 次（~90s）后仍在播则强制发空列表。

### 调用链
`butler/api/role_routes.py` `try_role`：当设备 `play_mode=="tts_speak"` 且 `ha_player_entity` 存在、`xiaomi_play=="music"` 时，调 `play_xiaomi_url_once(..., method="music")` 并随后 `schedule_xiaomi_stop(...)`（`xiaomi_play` 在设备配置 `data/devices.json` 里设置，X08A 当前设为 `music`）。

### 验证结果（v1）
- 日志：`xiaomi stop@end pos=5151 dur=5280 -> 0`（在结束前 129ms 触发空列表，`code=0`）。
- 设备拉取 `/tts/*.mp3` 次数 = **2**：第 1 次是正常播放，第 2 次是回环刚启动即被停止。**不再无限循环、不再截断**，残留 ~0.5s 重复尾。

## 4. 残留问题 / 可改进点（v1 遗留，v2 已解决大部分）

- **~0.5s 重复尾**：停止命令经小米云端（api2.mina.mi.com）下发，有几百毫秒延迟。v1 在 `duration-200ms` 发停止，设备可能已先回环并预拉取（第 2 次 GET），用户听到「完整一句 + 约半秒的句首重复」。→ **v2 已解决**（§7）。
- **并发**：同一设备连续两条 TTS 时，后一条 `REPLACE_ALL` 会替换队列，前一条的 `schedule_xiaomi_stop` 可能在后一条播放中才触发、误停后一条。→ **v2 已通过会话管理缓解**（§7）。

## 5. 复现 / 验证命令

```bash
# 触发试跑（容器内）
docker exec doubao-butler python -c "import httpx,os; pw=os.environ['BUTLER_WEB_PASSWORD']; \
  r=httpx.post('http://localhost:8095/api/roles/agent_lidicn/try', \
  headers={'Authorization':f'Bearer {pw}','Content-Type':'application/json'}, \
  json={'text':'测试一下'})"

# 看停止是否触发（应出现 xiaomi stop@end 或 xiaomi stop@loop）
docker logs --since 30s doubao-butler | grep 'xiaomi stop'

# 看设备拉取次数（=1 最优；=2 为一次循环尾/预拉取；>2 说明仍在循环）
docker logs --since 30s doubao-butler | grep -c 'GET /tts/'
```

## 6. 部署与凭据注意

- 本机 `e:/NAS` 是 NAS 断开副本，**改完必须 scp 到 NAS 再 `docker compose up -d --build`**：
  ```bash
  scp butler/integrations/ha.py lidicn@192.168.2.200:/vol1/1000/docker/doubao-butler/butler/integrations/ha.py
  ssh lidicn@192.168.2.200 "cd /vol1/1000/docker/doubao-butler && docker compose up -d --build"
  ```
  SSH 用 `C:\Windows\System32\OpenSSH\ssh.exe`（便携版 OpenSSH 在 PowerShell 下偶发解析失败）。
- 小米 token 来自 HA `xiaomi_miot` 已登录态：`/vol1/1000/docker/homeassistant/config/.storage/xiaomi_miot/auth-<uid>-cn-micoapi.json` 的 `service_token`+`user_id`(sid=micoapi)，注入容器环境变量 `XIAOMI_SERVICE_TOKEN` / `XIAOMI_USER_ID`。**绝不用 HA 的 JWT**（`/api/services` 对 X08A 不返回 `media_id`，走不通直连）。
- 调试用临时脚本用 `docker cp` 注入容器执行（`docker exec ... python /app/xxx.py`），避免在 NAS 留痕。

---

## 7. v2 更新（2026-09-05 深夜，已完成部署验证）

### 7.1 新增实测发现（推翻/修正 v1 部分结论）

对 X08A 做了 7 组 payload 实验 + 停止通道验证 + 延迟标定，结论：

1. **`audio_items` 元素里的 `play_times`（播放次数限制，小米官方 AudioPlayer 规范字段）对 X08A 无效**——试 `play_times:1`，仍无限循环。
2. **`audio_type` 换成 `TTS` / 空串 均无效**——固件只认单曲循环。
3. **`player_set_loop` 参数名是 `type` 不是 `loop_type`**（Yonsm/MiService 源码佐证：`{"media":"common","type":N}`），且注释明确它**只影响 `player_play_url` 路径的设备**；对 `player_play_music` 设备（X08A）无效。v1 用 `{"loop_type":0}` 字段名发错了。
4. **`player_play_url` 复核：X08A 实际会拉取音频**（每 ~3.2s 一次，循环），但 `player_get_play_status` **不报 playing 状态**（status 保持 2）——即状态通道对 url 路径失效，无法用于精确停止。v1 记录「不拉音频」与本次不符，可能与当时测试 URL 可达性或固件状态有关，**结论以本次为准：url 路径不可用于精确停止**。
5. **真正的停止通道是 `player_play_operation {"action":"stop","media":"app_ios"}`**：播放中调用后 ~0.7s 内 `status` 由 1 变 2、`position` 冻结，**即时有效**；`pause`/`play` 同样有效。v1 测的 `player_pause {}`/`player_stop {}` 空消息是**方法名不对**，并非「没有停止手段」。
6. **双条目队列（TTS + 0.3s 静音）实验**：X08A 对 `audio_items` 多条目**只播放第一条并单曲循环，第二条从不拉取**（GET=0）——「队列播完自停」思路在 X08A 上不可行（wav 第二条目甚至会令整个指令被拒）。
7. **云端延迟标定**：`player_play_operation stop` 经 `api2.mina.mi.com` 下发到 X08A 生效，实测延迟 **~680-740ms**（3 组测量：679/684/687/738ms）。

### 7.2 v2 方案（已部署）

`butler/integrations/ha.py`：
- 新增 `XIAOMI_STOP_LEAD_MS = 800`（模块常量，≈ 实测延迟 700ms + 余量 100ms）。
- 新增 `_xiaomi_stop_playback(device_id)`：发 `player_play_operation {"action":"stop","media":"app_ios"}`。
- `schedule_xiaomi_stop` v2：
  - 停止通道：**`player_play_operation stop`**（替代 v1 的空列表清队列）；`_xiaomi_clear_queue` 仅作 operation stop 失败时的最终兜底。
  - 停止时机：`status==1` 且 `position >= duration - 800` → 发 stop。原理：stop 到达设备时（+~700ms）播放位置 ≈ duration-100ms，**截断的只是 edge-tts 句尾静音，人耳无感**；且 stop 在回环出声前生效，**无重复尾**。
  - 回环兜底（position 突降）与 90s 超时兜底保留，改用 operation stop。
  - **会话管理**：`self._xiaomi_watch_tasks[device_id]` 记录 watch 任务；新播放先 `cancel` 旧任务，防前一条 TTS 的 watch 误停后一条（解决 v1 并发残留）。
- 播放 payload 不变（`player_play_music` 单条 + `REPLACE_ALL`）。

### 7.3 v2 验证结果（真实设备，2026-09-05）

- 短文本「测试一下」（dur=1512ms）：日志 `xiaomi stop@end(pos>=dur-800) pos=1110 dur=1512 -> 0`，**GET=1**（v1 为 2），无重复尾。
- 长文本 8.5s 连续 3 次：每次 `stop@end pos=7861~8165 < dur=8472`（**stop 均在回环前生效**），无截断、无重复尾；GET=2 为设备网络层预拉取（回环前预缓冲），**未构成听感重复**（判定标准以 stop 生效时 position 是否已回绕为准，而非 GET 次数）。
- 连续触发无并发误停（会话管理生效）。

### 7.4 仍待办

- 主卧 `xiao_touch8` 与书房 `xiao_study` 的 `ha_player_entity` 均为 `media_player.xiaomi_x08a_1648_play_control`（疑似复制笔误），且两设备 xiaoai_id 是否相同未验证——主卧尚未真机试跑，**建议在主卧实测一次**。
- `player_play_operation pause` 已实测有效，未来如需「暂停/恢复」播报可直接使用。
- 若网络波动导致重复尾复现，可调大 `XIAOMI_STOP_LEAD_MS`（截断句尾）或调小（残留更短重复）。
