# -*- coding: utf-8 -*-
"""批221（DB 第五期审计 44 条确证缺陷的收口）：`butler.param` 共用入参尺子。

覆盖报告里三条跨轮反复兑现的形态：
  ① `LIMIT -1` = SQLite 无上限（DB95/98/110/119/122/125/128/130/136/140）
  ② 负数时间窗把 cutoff 推到未来（DB96/98/115/128/130）
  ③ `d.get(k, D)` 在显式 `null` 时默认值不生效（DB107/120/123/137）＋ `bool("false")` 读成 True（DB139）
判据来源：`doc/审计报告/DB_第五期_33轮审计汇总报告_20261015.md` §三 §四。
"""
import unittest

from butler.param import (
    ParamError,
    body_bool,
    body_num,
    query_num,
    to_bool,
    to_float,
    to_int,
)


class TestToInt(unittest.TestCase):
    def test_rejects_negative_limit_instead_of_full_table(self):
        """DB95/110/125/140：limit=-1 在 SQLite 里是「无上限」，必须判失败而不是放行。"""
        with self.assertRaises(ParamError):
            to_int("-1", "limit", default=50, minimum=0)

    def test_accepts_legal_value_and_string_form(self):
        self.assertEqual(to_int("20", "limit", default=50, minimum=0), 20)

    def test_non_numeric_string_raises_not_crashes(self):
        """DB126：`【温度：26.5】` 这类值过去裸 int(value) 抛 ValueError ⇒ 500。"""
        with self.assertRaises(ParamError):
            to_int("26.5", "temperature", default=26, minimum=16, maximum=30)

    def test_missing_or_null_falls_back_to_default(self):
        self.assertEqual(to_int(None, "limit", default=50, minimum=0), 50)
        self.assertEqual(to_int("", "limit", default=50, minimum=0), 50)

    def test_explicit_zero_is_not_swallowed_by_default(self):
        """DB124/DB-NEG-036：0 是合法值，`or default` 那种写法会把它吞成默认值。"""
        self.assertEqual(to_int(0, "days", default=7, minimum=0), 0)

    def test_upper_bound_untouched_when_not_given(self):
        """MA61 教训：上界刻意不动 ⇒ 不传 maximum 就不夹。"""
        self.assertEqual(to_int("100000", "limit", default=50, minimum=0), 100000)

    def test_bool_is_not_a_number_for_api_params(self):
        with self.assertRaises(ParamError):
            to_int(True, "limit", default=50, minimum=0)


class TestToFloat(unittest.TestCase):
    def test_non_numeric_raises_not_crashes(self):
        """DB101/102/109/141：MQTT 载荷与配置里的 confidence/ttl 非数字。"""
        with self.assertRaises(ParamError):
            to_float("abc", "confidence", default=0.0, minimum=0.0, maximum=1.0)

    def test_out_of_range_raises(self):
        """DB118：同文件 50 行外已有 [0,1] 正确夹逼，尺子必须自带同一判据。"""
        with self.assertRaises(ParamError):
            to_float("1.7", "min_conf", default=0.7, minimum=0.0, maximum=1.0)

    def test_null_falls_back_to_default(self):
        self.assertEqual(to_float(None, "confidence", default=0.5), 0.5)

    def test_number_passthrough(self):
        self.assertEqual(to_float(26.5, "temperature", default=26.0), 26.5)


class TestToBool(unittest.TestCase):
    def test_explicit_null_uses_default_even_when_default_is_true(self):
        """DB120/123/137：`bool(d.get("enabled", True))` 对显式 null 得到 False。"""
        self.assertTrue(to_bool(None, "enabled", default=True))
        self.assertFalse(to_bool(None, "tts", default=False))

    def test_false_string_is_false(self):
        """DB139：`bool("false") == True` ⇒「不要覆盖静默」被读成「要」。"""
        self.assertFalse(to_bool("false", "override_quiet", default=False))
        self.assertFalse(to_bool("0", "override_quiet", default=False))
        self.assertFalse(to_bool("off", "override_quiet", default=False))
        self.assertFalse(to_bool("", "override_quiet", default=False))

    def test_true_string_is_true(self):
        self.assertTrue(to_bool("true", "enabled", default=False))
        self.assertTrue(to_bool("YES", "enabled", default=False))
        self.assertTrue(to_bool("1", "enabled", default=False))

    def test_unknown_string_raises_instead_of_guessing(self):
        with self.assertRaises(ParamError):
            to_bool("maybe", "enabled", default=True)

    def test_real_booleans_passthrough(self):
        self.assertTrue(to_bool(True, "enabled", default=False))
        self.assertFalse(to_bool(False, "enabled", default=True))


class TestBodyHelpers(unittest.TestCase):
    def test_explicit_null_number_takes_default_not_none(self):
        """DB107 一族的口径：显式 null 与缺键同判 ⇒ 一律回落默认值，⛔ 把 None 传给下游算式。"""
        body = {"confidence": None, "volume": None}
        self.assertEqual(body_num(body, "confidence", default=0.5), 0.5)
        self.assertEqual(body_bool(body, "enabled", default=True), True)

    def test_absent_key_returns_default(self):
        self.assertEqual(body_num({}, "volume", default=80, minimum=0, maximum=100), 80)

    def test_required_number_when_no_default_given(self):
        with self.assertRaises(ParamError):
            body_num({}, "cmd_id", required=True)

    def test_range_violation_raises(self):
        """DB122/129：volume/duration 越界值过去裸 int() 照传。"""
        with self.assertRaises(ParamError):
            body_num({"volume": "120"}, "volume", default=80, minimum=0, maximum=100)


class TestQueryHelper(unittest.TestCase):
    class _Req:
        def __init__(self, params):
            self.query_params = params

    def test_missing_param_uses_default(self):
        self.assertEqual(query_num(self._Req({}), "limit", default=50, minimum=0), 50)

    def test_negative_window_raises(self):
        """DB96/128/130：days/window/seconds 负数 ⇒ cutoff 落到未来 ⇒ 空窗与正常空窗同形。"""
        for key in ("days", "window", "seconds"):
            with self.subTest(key=key):
                with self.assertRaises(ParamError):
                    query_num(self._Req({key: "-1"}), key, default=7, minimum=1)

    def test_zero_kept_when_allowed(self):
        self.assertEqual(query_num(self._Req({"limit": "0"}), "limit", default=50, minimum=0), 0)


class TestPathHelper(unittest.TestCase):
    """`int(request.path_params.get("id", "0"))` 一族（DB131/133/138 等 13 处站点）。

    本地导入：`path_num` 未落之前只让本类红，⛔ 把整档 25 例拖成收集错误。
    """

    class _Req:
        def __init__(self, params):
            self.path_params = params

    def test_illegal_id_raises_instead_of_becoming_zero(self):
        from butler.param import path_num

        with self.assertRaises(ParamError):
            path_num(self._Req({"id": "abc"}), "id", default=0, minimum=1)

    def test_zero_id_rejected_when_minimum_is_one(self):
        """DB138：默认 "0" 落到 `WHERE id = 0` ⇒ 匹配不到却回 200，与「记录不存在」同形。"""
        from butler.param import path_num

        with self.assertRaises(ParamError):
            path_num(self._Req({"id": "0"}), "id", default=0, minimum=1)

    def test_negative_id_rejected(self):
        from butler.param import path_num

        with self.assertRaises(ParamError):
            path_num(self._Req({"rule_id": "-3"}), "rule_id", minimum=0)

    def test_legal_id_passthrough(self):
        from butler.param import path_num

        self.assertEqual(path_num(self._Req({"id": "42"}), "id", default=0, minimum=1), 42)

    def test_absent_id_falls_back_to_default(self):
        from butler.param import path_num

        self.assertEqual(path_num(self._Req({}), "id", default=0, minimum=1), 0)


class TestParamErrorShape(unittest.TestCase):
    def test_message_names_the_parameter_and_never_the_raw_secret_value(self):
        exc = None
        try:
            to_int("26.5", "temperature", default=26)
        except ParamError as e:
            exc = e
        self.assertIn("temperature", str(exc))

    def test_is_a_valueerror_so_existing_except_valueerror_sites_still_catch_it(self):
        self.assertTrue(issubclass(ParamError, ValueError))


class TestSoftHelpers(unittest.TestCase):
    """载荷／配置侧的宽形制：不可解 ⇒ 记一行日志并按默认值走，⛔ 打断消费链与调度器注册。

    与 HTTP 侧的分工：HTTP 请求参数抛 `ParamError`→400（调用方能改），
    MQTT 载荷与磁盘配置抛出去只会让管家整条链崩，所以这两枚只能软着陆＋留痕。
    """

    def test_bad_value_lands_on_default_and_leaves_a_log_line(self):
        from butler.param import soft_num
        with self.assertLogs("butler.param", level="WARNING") as cap:
            got = soft_num("abc", "priority", default=3)
        self.assertEqual(got, 3)
        self.assertIn("priority", cap.output[0])
        self.assertNotIn("abc", cap.output[0], "日志回显原值⇒把外部内容抄进日志")

    def test_absent_value_lands_on_default_without_logging(self):
        from butler.param import soft_num
        got = soft_num(None, "confidence", default=0.0, integer=False)
        self.assertEqual(got, 0.0)

    def test_out_of_range_value_lands_on_default(self):
        from butler.param import soft_num
        got = soft_num(-1, "limit", default=50, minimum=1)
        self.assertEqual(got, 50)

    def test_soft_flag_reads_false_words_as_false(self):
        from butler.param import soft_flag
        self.assertIs(soft_flag("false", "override_quiet", default=True), False)
        self.assertIs(soft_flag(None, "override_quiet", default=True), True)

    def test_soft_flag_garbage_lands_on_default(self):
        from butler.param import soft_flag
        with self.assertLogs("butler.param", level="WARNING"):
            got = soft_flag("maybe", "override_quiet", default=False)
        self.assertIs(got, False)


if __name__ == "__main__":
    unittest.main()
