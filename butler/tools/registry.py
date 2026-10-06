"""注册表 + 统一工具分发入口（从 butler/core/tools.py 拆分）。"""
from __future__ import annotations

import asyncio
import time

from butler.logging_setup import get_logger

logger = get_logger("butler.tools")

# ---- 工具 schema（OpenAI 格式，交给 new-api 做意图路由）----
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前日期和时间。用于回答「现在几点」「今天几号」「星期几」等。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取深圳宝安区实时天气和预报。用于回答「今天天气怎么样」「明天会下雨吗」「现在多少度」等天气相关问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["realtime", "daily", "hourly"],
                        "description": "realtime=实时天气，daily=未来3天预报，hourly=未来12小时逐小时降水。默认realtime。"
                    }
                },
                "required": []
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_presence",
            "description": "查询当前谁在家/在哪个房间。用于回答「凯文在家吗」「现在谁在客厅」「爱美丽在哪」等家庭成员位置问题。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_device",
            "description": "控制 Home Assistant 里的设备：开/关灯、空调、风扇、窗帘等。"
            "domain 例：light/switch/climate/fan/cover；service 例：turn_on/turn_off；"
            "entity_id 可以是 HA 实体ID，也可以是中文设备名（如「显示器挂灯」「书房射灯」「客厅空调」），系统会自动匹配真实设备。"
            "不确定 entity_id 时直接用中文设备名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "service": {"type": "string"},
                    "entity_id": {"type": "string"},
                    "data": {"type": "object"},
                },
                "required": ["domain", "service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_memory",
            "description": "查询全屋设备历史与家庭记忆，例如「昨天净水器出水量多少」"
            "「上周谁常开客厅灯」「厨房昨晚几点用的水」。直接传口语问句。",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "push_to_tv",
            "description": "把一条通知（标题+内容）推送到客厅电视展示。用于需要可视化呈现的结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["title", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_camera",
            "description": "分析某房间摄像头当前画面（依赖 memory-agent 视觉能力），返回画面描述。",
            "parameters": {
                "type": "object",
                "properties": {"room": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "根据文字描述生成一张图片，返回图片地址。",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_music",
            "description": "根据文字描述创作一段音乐，返回音频地址。",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "设定一个提醒，到点主动提醒家人。时间二选一：minutes=多少分钟后；"
            "at=绝对时间（格式 YYYY-MM-DD HH:MM 或 MM-DD HH:MM 或 HH:MM，HH:MM 表示今天该时刻，"
            "已过则明天）。member=要提醒/要找的人（如 lidicn/Kevin/Emily）：到点会全屋摄像头找人，"
            "在所在房间小爱播报；找不到则推送到手机 Bark。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "minutes": {"type": "integer", "description": "相对：N 分钟后触发"},
                    "at": {"type": "string", "description": "绝对时间：YYYY-MM-DD HH:MM / MM-DD HH:MM / HH:MM"},
                    "member": {"type": "string", "description": "目标成员名，如 lidicn/Kevin/Emily；到点全屋找人"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_tv_movie",
            "description": "在客厅电视的飞牛TV上按片名搜索并播放指定影片或节目。"
            "用户说「在电视上放/播XXX」「用飞牛TV看XXX」时调用，传中文片名。"
            "返回播放请求的提交结果（是否成功拉起飞牛TV搜索）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "movie_name": {
                        "type": "string",
                        "description": "要播放的影片/节目中文名，例如「看不见的客人」",
                    },
                },
                "required": ["movie_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_schedule",
            "description": "查询指定成员的日程安排。可查今天、未来几天或指定日期范围。"
            "用户说「我今天有什么安排」「明天的日程」「这周有什么事」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "member": {"type": "string", "description": "成员名，如 lidicn/Kevin/Emily，不填则查当前对话对象"},
                    "days": {"type": "integer", "description": "查未来几天，默认1（今天）"},
                    "date": {"type": "string", "description": "指定日期 YYYY-MM-DD，与 days 二选一"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_schedule",
            "description": "创建一条日程/提醒。用户说「帮我记一下」「明天下午3点开会」「9月12日早上7点提醒我去医院」时调用。"
            "时间格式用 YYYY-MM-DD HH:MM 或 MM-DD HH:MM 或 HH:MM（今天）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "member": {"type": "string", "description": "成员名，默认当前对话对象"},
                    "title": {"type": "string", "description": "日程标题，如「医院复诊」「部门会议」"},
                    "description": {"type": "string", "description": "详细说明，可选"},
                    "start_time": {"type": "string", "description": "开始时间 YYYY-MM-DD HH:MM"},
                    "end_time": {"type": "string", "description": "结束时间，可选"},
                    "location": {"type": "string", "description": "地点，可选"},
                    "recurrence": {"type": "string", "description": "重复：none/daily/weekly/monthly，默认 none"},
                },
                "required": ["title", "start_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_schedule",
            "description": "修改一条已有的日程。用户说「把明天的会议改到下午4点」「取消医院复诊」时调用。"
            "先 query_schedule 找到日程 id，再用此工具修改。",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {"type": "integer", "description": "要修改的日程ID"},
                    "title": {"type": "string", "description": "新标题，不填则不改"},
                    "start_time": {"type": "string", "description": "新开始时间，不填则不改"},
                    "end_time": {"type": "string", "description": "新结束时间，不填则不改"},
                    "done": {"type": "boolean", "description": "标记完成/未完成"},
                },
                "required": ["schedule_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": "在指定房间的小爱音箱播放音乐。用户说「放点音乐」「播放周杰伦的歌」「书房放首歌」时调用。"
            "通过小爱音箱内置音乐能力播放。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "歌曲名/歌手/关键词，如「周杰伦」「晴天」「轻音乐」"},
                    "room": {"type": "string", "description": "房间名，如「书房」「客厅」，默认当前房间"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_channel",
            "description": "切换客厅电视到指定频道（IPTV）。用户说「换到CCTV1」「中央一台」「湖南卫视」「换台到5台」时调用。支持频道名（CCTV1/湖南卫视/北京卫视等）和频道号（1/2/5）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "频道名或频道号，如「CCTV1」「中央一台」「湖南卫视」「5」"},
                },
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": "根据用户描述创建一个新技能（草稿状态，需用户确认后生效）。当用户说「帮我创建一个技能」「我想实现...」「以后当...时就...」「创建自动化」时调用。返回技能预览，用户确认后才会生效。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "用户对技能的自然语言描述，如「每天早上7点看到Kevin就说早上好」"},
                    "skill_json": {"type": "string", "description": "可选：直接提供技能JSON定义。不填则由系统根据description生成"},
                },
                "required": ["description"],
            },
        },
    },
    # ── TVPilot 细粒度工具（ReAct 电视手，HTTP :8090）──
    {
        "type": "function",
        "function": {
            "name": "tv_foreground",
            "description": "查询电视当前前台应用包名。用于操作后验证（如确认 mytv/飞牛TV 是否在前台）。结构化观察，优先于截图。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_keyevent",
            "description": "向电视发送按键。常用：KEYCODE_HOME（回桌面/逃生）、KEYCODE_ENTER（确认）、KEYCODE_BACK（返回）、KEYCODE_DPAD_UP/DOWN/LEFT/RIGHT（方向导航）、KEYCODE_DEL（删除）。",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string", "description": "按键名，如 KEYCODE_HOME"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_launch_app",
            "description": "启动电视上的 App 并等待进入前台（最多8秒）。package 是 Android 包名，如 com.tvcam.mytv（我的电视）、com.fongmi.android.tv（飞牛TV）。启动后建议用 tv_foreground 验证。",
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {"type": "string", "description": "Android 包名"},
                    "activity": {"type": "string", "description": "可选：Activity 名，不填则默认 .MainActivity"},
                },
                "required": ["package"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_input_text",
            "description": "在电视当前输入框输入文字。注意：中文输入支持取决于电视端输入法，ASCII 文本可靠。输入前确保输入框已聚焦（先 tv_tap 搜索框）。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要输入的文字"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_tap",
            "description": "点击电视屏幕指定坐标。坐标范围 0-4096（1080p 电视通常 x:0-1920, y:0-1080）。用于点击按钮/搜索框/列表项。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "横坐标"},
                    "y": {"type": "integer", "description": "纵坐标"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_swipe",
            "description": "在电视屏幕上滑动。用于滚动列表/页面。duration_ms 默认 300。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer"}, "y1": {"type": "integer"},
                    "x2": {"type": "integer"}, "y2": {"type": "integer"},
                    "duration_ms": {"type": "integer", "description": "滑动时长毫秒，默认300"},
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_screenshot",
            "description": "截取电视当前屏幕，返回 base64 图片（已压缩到 width=480 减 token）。仅在结构化观察（tv_foreground）无法判断时使用，或失败排查时必用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ── TVPilot 组合动作（v0.3 高置信 combo，带前台验证）──
    {
        "type": "function",
        "function": {
            "name": "tv_go_home",
            "description": "回桌面组合动作（推荐）：按 HOME 键并验证回到桌面。比单独按 KEYCODE_HOME 更可靠，带前台验证。用于操作异常时逃生/兜底。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_search_play",
            "description": "在飞牛TV上搜索并播放指定影片/节目（推荐，6步组合动作）。自动完成：启动飞牛TV → 点击搜索框 → 中文输入关键词 → 搜索 → 选择首条 → 播放。中文输入可靠（tvremoteime广播方案）。用户说「在电视上放/播XXX」「用飞牛TV看XXX」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "要搜索的影片/节目名，支持中文，如「流浪地球」「狂飙」"},
                },
                "required": ["keyword"],
            },
        },
    },
    # ── DeskPilot 工具（Windows 手，HTTP :8765）──
    {
        "type": "function",
        "function": {
            "name": "desk_system_status",
            "description": "查询 Windows 主机系统状态（CPU/内存/磁盘/平台/主机名）。用于观察验证或用户问电脑状态时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_system_notify",
            "description": "在 Windows 桌面弹出通知。用于向电脑前的用户传递信息（如任务完成、提醒）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "通知标题"},
                    "message": {"type": "string", "description": "通知内容"},
                },
                "required": ["title", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_system_run",
            "description": "在 Windows 主机上运行程序或打开文件。用户说「打开XXX」「启动XXX」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "可执行文件路径或文件路径，如「notepad.exe」「C:\\\\Users\\\\xxx\\\\Documents\\\\file.txt」"},
                    "args": {"type": "string", "description": "可选命令行参数"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_volume_get",
            "description": "查询 Windows 主机当前音量和静音状态。用于音量操作后的观察验证。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_volume_set",
            "description": "设置 Windows 主机音量（0-100）。用户说「把音量调到XX」「音量XX」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "音量值 0-100", "minimum": 0, "maximum": 100},
                },
                "required": ["level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_volume_mute",
            "description": "切换 Windows 主机静音（静音/取消静音）。用户说「静音」「取消静音」时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_windows_list",
            "description": "列出 Windows 所有可见窗口（标题/进程）。用于窗口操作后的观察验证，或查找目标窗口。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_windows_activate",
            "description": "按标题激活 Windows 窗口（切换到前台）。用户说「切换到XXX窗口」「打开XXX」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题关键词（模糊匹配），如「微信」「Chrome」"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_windows_maximize",
            "description": "按标题最大化 Windows 窗口。用户说「最大化XXX」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题关键词"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_windows_close",
            "description": "按标题关闭 Windows 窗口。用户说「关闭XXX」「关掉XXX窗口」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题关键词"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_music_play",
            "description": "在 Windows 主机的 LX Music（落雪音乐）上搜索并播放指定歌曲。用户说「放首XXX」「播放XXX歌曲」「听XXX」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "歌曲名/歌手名，如「周杰伦 晴天」「夜曲」"},
                },
                "required": ["keyword"],
            },
        },
    },
    # ── DeskPilot 原子桌面操控（v2.5，坐标基于截图分辨率）──
    {
        "type": "function",
        "function": {
            "name": "desk_desktop_screenshot",
            "description": "截取 Windows 桌面画面，返回 base64 JPEG（默认 640px 宽）。"
            "用于观察当前桌面状态、验证操作结果、定位点击坐标。"
            "坐标操作（click/swipe）的 x,y 基于此截图的分辨率。"
            "【策略】操作窗口内控件时优先用 desk_uia_snapshot/desk_uia_click（省 token 且精确），"
            "本工具用于画面观察与 UIA 不可用时的回退。",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "可选截图区域，格式 'x,y,w,h'，基于原始分辨率"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_desktop_click",
            "description": "在 Windows 桌面指定坐标点击。坐标基于截图分辨率（管家会自动换算到原始屏幕坐标）。"
            "命中危险区域（回收站/系统托盘/关闭按钮）时会返回 confirmation_required，需带 confirm=true 重试。"
            "点击后建议调用 desk_desktop_screenshot 验证画面变化。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "横坐标（基于截图分辨率）"},
                    "y": {"type": "integer", "description": "纵坐标（基于截图分辨率）"},
                    "button": {"type": "string", "description": "左键 left 或右键 right，默认 left", "enum": ["left", "right"]},
                    "double": {"type": "boolean", "description": "是否双击，默认 false"},
                    "confirm": {"type": "boolean", "description": "危险区域二次确认，命中危险区域时需设为 true 重试"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_desktop_type",
            "description": "在 Windows 桌面当前焦点输入文字（支持中文，通过剪贴板+Ctrl+V）。"
            "注意会覆盖系统剪贴板。输入前确保目标输入框已获得焦点（可先 click 输入框）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要输入的文字，支持中文"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_desktop_swipe",
            "description": "在 Windows 桌面滑动/拖拽（按住左键从起点拖到终点）。"
            "用于滚动页面、拖拽窗口、滑动滑块等。坐标基于截图分辨率（管家会自动换算到原始屏幕坐标）。"
            "【拖拽窗口技巧】①先调用 desk_windows_activate 激活目标窗口；"
            "②起点必须在窗口标题栏（窗口顶部约 30px 高的区域，y≈窗口top+15）；"
            "③拖到目标位置后释放。拖拽窗口内容区域只会选中文字，不会移动窗口。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "起点横坐标"},
                    "y1": {"type": "integer", "description": "起点纵坐标"},
                    "x2": {"type": "integer", "description": "终点横坐标"},
                    "y2": {"type": "integer", "description": "终点纵坐标"},
                    "duration_ms": {"type": "integer", "description": "拖拽持续时间毫秒，默认 300，范围 50-10000"},
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_desktop_key",
            "description": "发送按键到 Windows 桌面。单键如 'enter'/'f5'/'escape'/'tab'，"
            "组合键用加号连接如 'ctrl+c'/'alt+tab'/'win+d'/'ctrl+shift+t'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "按键名，单键或组合键（加号连接）"},
                },
                "required": ["key"],
            },
        },
    },
    # ── DeskPilot UIA 语义操控（v2.6，M6 — 优先于截图+坐标）──
    {
        "type": "function",
        "function": {
            "name": "desk_uia_snapshot",
            "description": "获取 Windows 前台（或指定窗口）的 UIA 控件树，返回结构化 JSON"
            "（控件名/type/path/automation_id，省 token 且精确）。"
            "【优先使用】要操作窗口内控件时先调用本工具拿到控件树与 path；"
            "能找到目标控件就用 desk_uia_click/desk_uia_type 精确操作；"
            "UIA 定位不到（游戏/自绘界面）才回退 desk_desktop_screenshot + desk_desktop_click。",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string", "description": "窗口标题子串；缺省取前台窗口"},
                    "depth": {"type": "integer", "description": "递归深度上限（1-16，默认 8）"},
                    "max_nodes": {"type": "integer", "description": "节点总数上限（10-2000，默认 200）"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_uia_click",
            "description": "UIA 语义点击：按 path/name/automation_id 定位控件并精确点击"
            "（DPI 无关、精确命中语义按钮）。配合 desk_uia_snapshot：先 snapshot 拿 path/name 再点击。"
            "优先于 desk_desktop_click（坐标点击）。定位失败返回 not_found 时可回退坐标点击。",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string", "description": "窗口标题子串"},
                    "path": {"type": "array", "items": {"type": "integer"},
                             "description": "控件路径（顶层 children 索引序列，如 [0,2,1]）"},
                    "name": {"type": "string", "description": "控件名称/文本（子串匹配）"},
                    "automation_id": {"type": "string", "description": "automation_id"},
                    "control_type": {"type": "string", "description": "控件类型（Button/Edit/Window…）"},
                    "class_name": {"type": "string", "description": "友好类名（Edit/Button…）"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_uia_type",
            "description": "UIA 语义输入：向定位到的输入控件写入文字（支持中文，剪贴板+Ctrl+V）。"
            "配合 desk_uia_snapshot 使用。可带 clear_existing=true 先清空再输入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string", "description": "窗口标题子串"},
                    "path": {"type": "array", "items": {"type": "integer"}, "description": "控件路径"},
                    "name": {"type": "string", "description": "控件名称/文本"},
                    "text": {"type": "string", "description": "要输入的文字，支持中文"},
                    "clear_existing": {"type": "boolean", "description": "输入前是否 Ctrl+A 全选清除", "default": False},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desk_uia_wait",
            "description": "等待 UIA 控件满足条件（exists/visible/enabled），轮询直到超时。"
            "用于等待对话框/新窗口/动态元素就绪后再操作（如点保存后等另存为对话框出现）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string", "description": "窗口标题子串"},
                    "path": {"type": "array", "items": {"type": "integer"}, "description": "控件路径"},
                    "name": {"type": "string", "description": "控件名称"},
                    "timeout": {"type": "number", "description": "超时秒数（0.5-60，默认 5）"},
                    "condition": {"type": "string", "enum": ["exists", "visible", "enabled"],
                                  "description": "等待条件", "default": "exists"},
                },
                "required": [],
            },
        },
    },
    # ── new-api 运维工具（SQLite 直连，三表同步）──
    {
        "type": "function",
        "function": {
            "name": "newapi_list_channels",
            "description": "列出 new-api 所有渠道（ID/名称/状态/权重/模型数量）。用于了解当前有哪些上游渠道。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "newapi_list_models",
            "description": "列出 new-api 所有虚拟模型（模型名/映射渠道数/总权重）。用于了解当前有哪些虚拟模型可用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "newapi_get_model_allocation",
            "description": "查询某个虚拟模型的渠道分配详情（哪些渠道/权重/优先级/启用状态）。用户问「XXX模型分配给哪些渠道」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "虚拟模型名，如「butler-chat」「dsh-power-chat」"},
                },
                "required": ["model"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "newapi_set_model_allocation",
            "description": "全量设置某个虚拟模型的渠道分配（先删后插，自动三表同步+备份）。用户说「把XXX模型分配给渠道1和3」「调整XXX模型的渠道」时调用。修改后需调用 docker_restart 重启 new-api 容器生效。危险操作，执行前应确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "虚拟模型名"},
                    "allocations": {
                        "type": "array",
                        "description": "渠道分配列表，每项含 channel_id/weight/priority",
                        "items": {
                            "type": "object",
                            "properties": {
                                "channel_id": {"type": "integer", "description": "渠道 ID（从 newapi_list_channels 获取）"},
                                "weight": {"type": "integer", "description": "权重（默认 100），越大分配越多"},
                                "priority": {"type": "integer", "description": "优先级（默认 0），越大越优先"},
                            },
                            "required": ["channel_id"],
                        },
                    },
                },
                "required": ["model", "allocations"],
            },
        },
    },
    # ── Docker 运维工具（docker.sock）──
    {
        "type": "function",
        "function": {
            "name": "docker_ps",
            "description": "列出 NAS 上所有运行中的容器（名称/状态/镜像/端口）。用于查看服务运行状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "description": "是否包含已停止的容器，默认 false"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_restart",
            "description": "重启指定容器。修改 new-api 配置后必须重启 new-api 容器生效。用户说「重启XXX容器」「重启new-api」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "容器名，如「new-api」「doubao-butler」"},
                },
                "required": ["container"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_logs",
            "description": "获取指定容器的最近日志（默认最后 50 行）。用于排查服务问题。用户说「看看XXX日志」「XXX报错了」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "容器名"},
                    "tail": {"type": "integer", "description": "返回最后 N 行，默认 50", "default": 50},
                },
                "required": ["container"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_compose_up",
            "description": "在指定目录执行 docker compose up -d --build 部署/更新服务。用户说「部署XXX」「更新XXX服务」「重新构建XXX」时调用。耗时可能较长（build 过程），超时 300s。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_dir": {"type": "string", "description": "docker-compose.yml 所在目录（NAS 宿主机路径），如「/vol1/1000/docker/doubao-butler」"},
                    "services": {"type": "array", "items": {"type": "string"}, "description": "指定服务名列表，空=全部服务"},
                    "build": {"type": "boolean", "description": "是否 --build 重新构建，默认 true"},
                },
                "required": ["project_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_task_to_tp",
            "description": "给 TVPilot（TP）开发者发工单。通过豆包软件 Ctrl+2 快捷键切换到 TP 对话，"
            "粘贴工单内容并发送。纯键盘操作，不点击、不改变窗口大小。"
            "用于 PM 向 TP 下达开发任务、问题反馈、联调指令等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "工单标题，简短明确"},
                    "desc": {"type": "string", "description": "工单详细描述，包括背景、要求、验收标准"},
                    "priority": {"type": "string", "description": "优先级：P0（紧急阻塞）、P1（高）、P2（中）、P3（低）", "enum": ["P0", "P1", "P2", "P3"]},
                },
                "required": ["title", "desc"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_task_to_dp",
            "description": "给 DeskPilot（DP）开发者发工单。生成规范的交接单 .md 文件，"
            "双副本同步至 DeskPilot/docs/ 和 board/handoffs/。DP 开发者在 CodeBuddy 中打开项目即可看到。"
            "用于 PM 向 DP 下达开发任务、问题反馈、联调指令等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "工单标题，简短明确"},
                    "desc": {"type": "string", "description": "工单详细描述，包括背景、要求、验收标准"},
                    "priority": {"type": "string", "description": "优先级：P0（紧急阻塞）、P1（高）、P2（中）、P3（低）", "enum": ["P0", "P1", "P2", "P3"]},
                    "task_type": {"type": "string", "description": "工单类型：需求、变更、问题、联调", "enum": ["需求", "变更", "问题", "联调"]},
                },
                "required": ["title", "desc"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_dp",
            "description": "给 DeskPilot（DP）开发者发即时消息。通过豆包软件 Ctrl+3 快捷键切换到 DP 对话，"
            "粘贴消息并发送，完成后 Ctrl+1 切回 PM。纯键盘操作，不点击、不改变窗口大小。"
            "用于 PM 向 DP 即时沟通、催办、简单指令。正式工单请用 assign_task_to_dp 生成交接单文档。"
            "注意：需要电脑未锁屏（锁屏时键盘操作被拦截）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "要发送的消息内容"},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_tp",
            "description": "给 TVPilot（TP）开发者发即时消息。通过豆包软件 Ctrl+2 快捷键切换到 TP 对话，"
            "粘贴消息并发送，完成后 Ctrl+1 切回 PM。纯键盘操作，不点击、不改变窗口大小。"
            "用于 PM 向 TP 即时沟通、催办、简单指令。正式工单请用 assign_task_to_tp 生成工单。"
            "注意：需要电脑未锁屏（锁屏时键盘操作被拦截）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "要发送的消息内容"},
                },
                "required": ["message"],
            },
        },
    },
]




async def dispatch_tool(name: str, args: dict, agent) -> str:
    """工具分发。agent 需提供 ha/memory/tv/doubao/scheduler 等客户端。"""
    # 懒加载域模块，避免循环 import
    from butler.tools.devices import _resolve_entity
    from butler.tools.schedule import (
        _create_schedule,
        _query_schedule,
        _set_reminder,
        _update_schedule,
    )
    from butler.tools.media import _play_music, _switch_channel
    from butler.tools.skills import _create_skill
    from butler.tools.tv import _dispatch_tvpilot
    from butler.tools.desk_pilot import (
        _assign_task_to_dp,
        _assign_task_to_tp,
        _dispatch_deskpilot,
        _dispatch_docker,
        _dispatch_newapi,
        _send_to_dp,
        _send_to_tp,
    )
    try:
        if name == "get_current_time":
            return time.strftime("现在时间是 %Y年%m月%d日 %H:%M:%S")

        if name == "show_capabilities":
            return (
                "我现在能做这些事：\n"
                "🏠 控制设备：开灯关灯、空调、风扇、窗帘等（直接说「打开书房灯」）\n"
                "🌤️ 查天气：实时天气、未来三天预报、会不会下雨\n"
                "⏰ 查时间：现在几点、今天几号、星期几\n"
                "👨‍👩‍👧 查家人：谁在家、在哪个房间\n"
                "📺 控制电视：打开APP、换台、搜片、投屏\n"
                "🖥️ 控制电脑：音量、打开窗口、截图、点按操作\n"
                "📷 看摄像头：查客厅/起居室/书房/厨房画面\n"
                "📋 查日程：今天有什么安排、创建提醒\n"
                "🧠 查记忆：问家里过去发生的事、设备历史\n"
                "🎵 放音乐：客厅或电视上播歌\n"
                "🔧 系统运维：查容器状态、看日志、重启服务\n"
                "\n复杂问题我会调用大模型来回答，随便聊也行。"
            )

        if name == "get_weather":
            import httpx
            wtype = args.get("type", "realtime")
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get("http://192.168.2.200:3000/api/weather")
                    data = r.json()
                    result = data.get("result", {})
                    rt = result.get("realtime", {})
                    sky_map = {"CLEAR_DAY":"晴","CLEAR_NIGHT":"晴","CLOUDY":"阴","OVERCAST":"阴","PARTLY_CLOUDY_DAY":"多云","PARTLY_CLOUDY_NIGHT":"多云","LIGHT_HAZE":"轻度雾霾","MODERATE_HAZE":"中度雾霾","HEAVY_HAZE":"重度雾霾","LIGHT_RAIN":"小雨","MODERATE_RAIN":"中雨","HEAVY_RAIN":"大雨","STORM_RAIN":"暴雨","FOG":"雾","LIGHT_SNOW":"小雪","MODERATE_SNOW":"中雪","HEAVY_SNOW":"大雪","THUNDER_SHOWER":"雷阵雨"}
                    if wtype == "realtime":
                        sky = sky_map.get(rt.get("skycon",""), rt.get("skycon",""))
                        aq_obj = rt.get("air_quality", {})
                        aq = aq_obj.get("aqi", {}).get("chn", "?")
                        aq_desc = aq_obj.get("description", {}).get("chn", "")
                        wind_dir = rt.get("wind", {}).get("direction", 0)
                        wind_spd = rt.get("wind", {}).get("speed", 0)
                        wind_arrow = {0:"北风",45:"东北风",90:"东风",135:"东南风",180:"南风",225:"西南风",270:"西风",315:"西北风"}.get(int(wind_dir/45)*45, f"{wind_dir}°")
                        minutely_desc = result.get("minutely", {}).get("description", "")
                        keypoint = result.get("forecast_keypoint", "")
                        lines = []
                        if keypoint:
                            lines.append(keypoint)
                        lines.append(f"{sky} {round(rt.get('temperature',0),1)}°C，体感{round(rt.get('apparent_temperature',0),1)}°C")
                        lines.append(f"湿度{int(rt.get('humidity',0)*100)}%，{wind_arrow}{round(wind_spd*3.6,1)}km/h")
                        if aq_desc:
                            lines.append(f"空气质量{aq_desc}（AQI {aq}）")
                        if minutely_desc and "无" not in minutely_desc:
                            lines.append(minutely_desc)
                        return "，".join(lines)
                    elif wtype == "daily":
                        daily = result.get("daily", {})
                        temps = daily.get("temperature", [])
                        skycons = daily.get("skycon", [])
                        precips = daily.get("precipitation", [])
                        keypoint = result.get("forecast_keypoint", "")
                        lines = []
                        if keypoint:
                            lines.append(keypoint)
                        for i in range(min(3, len(temps))):
                            t = temps[i]
                            s = sky_map.get(skycons[i].get("value","") if i < len(skycons) else "", "")
                            p = precips[i] if i < len(precips) else {}
                            prob = p.get("probability", 0)
                            line = f"第{i+1}天：{s} {round(t.get('min',0),1)}~{round(t.get('max',0),1)}°C"
                            if prob and prob > 30:
                                line += f"，降雨概率{prob}%"
                            lines.append(line)
                        return "\n".join(lines)
                    elif wtype == "hourly":
                        hourly = result.get("hourly", {})
                        precip = hourly.get("precipitation", [])
                        temps = hourly.get("temperature", [])
                        desc = hourly.get("description", "")
                        lines = [desc] if desc else []
                        for i in range(min(12, len(precip))):
                            p = precip[i]
                            t = temps[i] if i < len(temps) else {}
                            hour = p.get("datetime", "")[11:16]
                            prob = p.get("probability", 0)
                            val = p.get("value", 0)
                            if prob > 30 or val > 0.1:
                                lines.append(f"{hour} 降雨概率{prob}%（{round(val,1)}mm）{round(t.get('value',0),1)}°C")
                        if len(lines) == 1 and desc:
                            lines.append("未来12小时无明显降雨")
                        return "\n".join(lines)
            except Exception as e:
                return f"天气查询失败：{e}"

        if name == "get_presence":
            try:
                pres = await agent.memory.presence_status(room=None, minutes=30, timeout=10.0)
            except Exception as e:
                return f"在场查询失败：{e}"
            if not pres.get("ok"):
                return f"在场查询失败：{pres.get('error') or 'MA 未返回 ok'}"
            items = pres.get("items") or []
            if items:
                lines = []
                for item in items:
                    lines.append(f"{item.get('name','?')} 在{item.get('room','?')}（{item.get('last_seen','')[-8:-3]}）")
                return "当前在家：" + "，".join(lines)
            return "最近30分钟没有识别到家庭成员"

        if name == "control_device":
            domain = args.get("domain")
            service = args.get("service")
            data = dict(args.get("data") or {})
            eid = args.get("entity_id") or data.get("entity_id")
            # 模糊匹配：LLM 给的 entity_id 可能不存在，按关键词从 HA 找正确的
            if eid:
                resolved = await _resolve_entity(agent, eid, domain or "")
                if resolved and resolved != eid:
                    logger.info("control_device entity resolved: %s -> %s", eid, resolved)
                    eid = resolved
            # 如果 domain 为空，从 entity_id 前缀推断
            if not domain and eid and "." in eid:
                domain = eid.split(".")[0]

            # 修复：domain 为空说明设备没找到，直接报错，不要尝试执行
            if not domain:
                logger.warning("control_device failed: cannot find device for entity_id=%s", eid)
                return f"找不到设备「{eid}」，请检查设备名称是否正确"

            if eid:
                data["entity_id"] = eid
            # 白名单检查（P1-12）
            try:
                from butler.ha_tools.whitelist import HAWhitelist
                wl = HAWhitelist()
                allowed, reason = wl.is_allowed(eid)
                if not allowed:
                    return f"拒绝控制：{reason}"
            except Exception as wl_e:
                logger.warning("whitelist check failed: %s", wl_e)
            try:
                await agent.ha.call_service_strict(domain, service, data)
                res = f"已执行 {domain}.{service}（{eid}）：ok"
            except Exception as e:
                res = f"执行失败 {domain}.{service}（{eid}）：{e}"

            # 学习别名：记录用户原话 → entity_id
            try:
                from butler.core.aliases import get_alias_store
                store = get_alias_store()
                # P1-1：从 contextvar 取当前请求用户原话（避免并发串话）
                try:
                    from butler.core.agent import _current_user_text
                    user_text = _current_user_text.get()
                except Exception:
                    user_text = getattr(agent, "_last_user_text", "")
                if user_text and eid:
                    store.learn(user_text, eid, domain, service)
            except Exception as e:
                logger.warning("alias learn failed: %s", e)

            return f"已执行 {domain}.{service}（{eid or '无entity_id'}）：{res}"

        if name == "query_memory":
            try:
                return await asyncio.wait_for(
                    agent.memory.ask_memory(args.get("question", "")), timeout=5.0
                )
            except asyncio.TimeoutError:
                return "记忆查询超时，暂时无法回忆相关内容。"
            except Exception as e:
                return f"记忆查询失败：{e}"

        if name == "push_to_tv":
            agent.tv.notify(
                {"title": args.get("title", ""), "message": args.get("message", "")}
            )
            return "已推送到电视"

        if name == "analyze_camera":
            try:
                res = await asyncio.wait_for(
                    agent.memory.analyze_camera(args.get("room", "客厅")), timeout=8.0
                )
            except asyncio.TimeoutError:
                return "摄像头分析超时，暂时无法查看画面。"
            except Exception as e:
                return f"摄像头分析失败：{e}"
            if isinstance(res, dict):
                return res.get("description") or res.get("scene") \
                    or str(res.get("error") or res)
            return str(res)

        if name == "generate_image":
            return await agent.doubao.generate_image(args.get("prompt", ""))

        if name == "generate_music":
            return await agent.doubao.generate_music(args.get("prompt", ""))

        if name == "set_reminder":
            return await _set_reminder(
                agent, args.get("text", ""),
                int(args.get("minutes") or 5),
                at=str(args.get("at") or ""),
                member=str(args.get("member") or ""),
            )

        if name == "play_tv_movie":
            res = await agent.tv.play_fongmi(args.get("movie_name", ""))
            if isinstance(res, dict) and res.get("ok"):
                initials = res.get("initials") or ""
                msg = res.get("message") or ""
                tail = f"（首字母 {initials}）" if initials else ""
                return f"已让飞牛TV搜索并播放《{args.get('movie_name', '')}》{tail}。{msg}".strip()
            err = (res.get("error") if isinstance(res, dict) else None) or "调用失败"
            return f"在飞牛TV上播放《{args.get('movie_name', '')}》失败：{err}"

        if name == "query_schedule":
            return _query_schedule(agent, args)

        if name == "create_schedule":
            return _create_schedule(agent, args)

        if name == "update_schedule":
            return _update_schedule(agent, args)

        if name == "play_music":
            return await _play_music(agent, args)

        if name == "switch_channel":
            return await _switch_channel(agent, args)

        if name == "create_skill":
            return await _create_skill(agent, args)

        if name == "speak_to_speaker":
            return await agent.ha.notify_message(args.get("message", ""), agent.s.xiaomi_notify_entity)

        # ── TVPilot 细粒度工具（HTTP :8090）──
        if name.startswith("tv_"):
            return await _dispatch_tvpilot(agent, name, args)

        # ── DeskPilot 工具（Windows 手，HTTP :8765）──
        if name.startswith("desk_"):
            return await _dispatch_deskpilot(agent, name, args)

        # ── new-api 运维工具（SQLite 直连）──
        if name.startswith("newapi_"):
            return _dispatch_newapi(agent, name, args)

        # ── Docker 运维工具（docker.sock）──
        if name.startswith("docker_"):
            return await _dispatch_docker(agent, name, args)

        # ── PM 工单分发工具（通过 DeskPilot 在 Windows 本地执行脚本）──
        if name == "assign_task_to_tp":
            return await _assign_task_to_tp(agent, args)
        if name == "assign_task_to_dp":
            return await _assign_task_to_dp(agent, args)
        if name == "send_to_tp":
            return await _send_to_tp(agent, args)
        if name == "send_to_dp":
            return await _send_to_dp(agent, args)

        return f"未知工具: {name}"
    except Exception as e:  # 工具失败不应中断对话
        logger.warning("tool %s failed: %s", name, e)
        return f"工具执行失败：{e}"

