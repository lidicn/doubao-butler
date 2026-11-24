"""`wake_ding.mp3` 生成参数 durable 判据（附带件②）。

隐患：`startup_assets.FFMPEG_ARGV` 曾为 `volume=0.4 / duration=0.2`，实测
`ffmpeg -af volumedetect` → mean −30.4 dB / max −26.5 dB（几乎听不见）。现网文件是**手工重生成**
的 0.6s / volume=6.0（mean −6.5 / max −2.9），但文件在挂载卷持久、被删重建就回到旧参数。
本档钉两件事：

1. 参数逐枚相等（14 项）且就是验证过的那组 → 重建也响；
2. 用 ffmpeg 真重建一份，读 `mean_volume`/`max_volume`，断言落在可听区间（两侧读数都给）。

跑法（本机无 ffmpeg 会 SkipTest；容器内有 7.1.5）：
    python -B -m unittest tests.test_wake_ding_asset
容器内真跑（⛔ 不动部署树，只落 /tmp）：
    docker cp tests/test_wake_ding_asset.py <c>:/tmp/tests/ && \
    docker exec <c> sh -c 'cd /app && PYTHONPATH=/tmp:/app python3 -m unittest tests.test_wake_ding_asset -v'
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

from butler.startup_assets import FFMPEG_ARGV, WAKE_DING_NAME

EXPECTED_ARGV = (
    "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.6",
    "-af", "afade=t=in:st=0:d=0.01,afade=t=out:st=0.55:d=0.05,volume=6.0",
    "-ar", "24000", "-ac", "1", "-b:a", "24k",
)

#: 可听区间（dB）。旧参数实测 mean −30.4 / max −26.5（两侧都不达标）；手工件 −6.5 / −2.9。
MEAN_DB_RANGE = (-20.0, -3.0)
MAX_DB_RANGE = (-12.0, -1.0)
#: 叮时长区间（秒）：`sine=duration=0.6`，容器实测文件时长 0.65s（含 mp3 帧填充）。
DURATION_RANGE = (0.55, 0.85)


def ffmpeg_bin() -> str | None:
    return os.environ.get("DB_FFMPEG_BIN") or shutil.which("ffmpeg")


class ParamsPinnedTest(unittest.TestCase):
    def test_argv_is_the_validated_audible_set(self):
        """逐枚相等（⛔ 子串/正则放宽）：谁把 volume/duration 改回去，这里当场红。"""
        self.assertEqual(tuple(FFMPEG_ARGV), EXPECTED_ARGV)

    def test_fade_out_floats_with_duration(self):
        """fade-out 起点（0.55s）必须贴住新的 0.6s 尾巴，否则尾音被硬切。"""
        af = FFMPEG_ARGV[FFMPEG_ARGV.index("-af") + 1]
        self.assertIn("duration=0.6", FFMPEG_ARGV[FFMPEG_ARGV.index("-i") + 1])
        self.assertIn("afade=t=out:st=0.55:d=0.05", af)
        self.assertIn("volume=6.0", af)


class RebuildLoudnessTest(unittest.TestCase):
    def test_rebuilt_ding_is_audible(self):
        ff = ffmpeg_bin()
        if not ff:
            raise unittest.SkipTest(
                "本机没有 ffmpeg（容器内有 7.1.5，用本档 docstring 里的 docker exec 跑法取读数）")
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / WAKE_DING_NAME
            gen = subprocess.run([*FFMPEG_ARGV, str(out)], capture_output=True, text=True)
            self.assertEqual(gen.returncode, 0, gen.stderr[-800:])
            self.assertTrue(out.exists() and out.stat().st_size > 0, "ffmpeg 退出码 0 但文件没落盘")
            vol = subprocess.run([ff, "-hide_banner", "-i", str(out), "-af", "volumedetect",
                                  "-f", "null", "-"], capture_output=True, text=True)
            text = vol.stderr
            mean = self._db(text, "mean_volume")
            peak = self._db(text, "max_volume")
            dur = self._duration(text)
        self.assertGreaterEqual(mean, MEAN_DB_RANGE[0], f"mean {mean} dB 偏小（会听不见）：{text[-400:]}")
        self.assertLessEqual(mean, MEAN_DB_RANGE[1], f"mean {mean} dB 偏大（爆音）：{text[-400:]}")
        self.assertGreaterEqual(peak, MAX_DB_RANGE[0], f"max {peak} dB 偏小（会听不见）")
        self.assertLessEqual(peak, MAX_DB_RANGE[1], f"max {peak} dB 偏大（削波）")
        self.assertGreaterEqual(dur, DURATION_RANGE[0], f"时长 {dur}s 偏短，叮可能被掐")
        self.assertLessEqual(dur, DURATION_RANGE[1], f"时长 {dur}s 偏长，会拖慢开窗")

    @staticmethod
    def _db(text: str, key: str) -> float:
        m = re.search(key + r":\s*(-?\d+(?:\.\d+)?) dB", text)
        if not m:
            raise AssertionError(f"volumedetect 没读出 {key}：{text[-600:]}")
        return float(m.group(1))

    @staticmethod
    def _duration(text: str) -> float:
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
        if not m:
            raise AssertionError(f"没读出时长：{text[-600:]}")
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


if __name__ == "__main__":
    unittest.main()
