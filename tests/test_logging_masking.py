"""WO-DB-102 脱敏自测：三条都必须整行存在 + 秘密被遮。"""
import io
import logging
import unittest

from butler.logging_setup import MaskingFilter, MaskingFormatter


def _make_logger():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(MaskingFormatter(fmt="%(levelname)s [%(name)s] %(message)s"))
    handler.addFilter(MaskingFilter())
    lg = logging.getLogger("test_masking_" + str(id(buf)))
    lg.handlers = [handler]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    return lg, buf


class TestLoggingMasking(unittest.TestCase):
    def test_token_masking_line_intact(self):
        lg, buf = _make_logger()
        lg.info("a token=%s", "SECRET123")
        out = buf.getvalue()
        self.assertNotIn("SECRET123", out, f"secret leaked: {out}")
        self.assertIn("***", out, f"mask missing: {out}")
        self.assertIn("a token=", out, f"line dropped: {out}")

    def test_key_and_user_both_placeholders(self):
        lg, buf = _make_logger()
        lg.info("key=%s user=%s", "MYKEY", "alice")
        out = buf.getvalue()
        self.assertNotIn("MYKEY", out, f"key leaked: {out}")
        self.assertIn("alice", out, f"user value missing: {out}")
        self.assertIn("user=alice", out, f"second placeholder gone: {out}")
        self.assertIn("key=***", out, f"key not masked: {out}")

    def test_normal_chinese_not_masked(self):
        lg, buf = _make_logger()
        lg.info("普通中文日志 %s", "ok")
        out = buf.getvalue()
        self.assertIn("普通中文日志", out, f"line dropped: {out}")
        self.assertIn("ok", out, f"value missing: {out}")
        self.assertNotIn("***", out, f"false positive masking: {out}")


if __name__ == "__main__":
    unittest.main()
