import unittest

from butler.core.dedup import bigrams, jaccard


class TestDedup(unittest.TestCase):
    def test_bigrams_basic(self):
        bg = bigrams("小凯回来啦")
        self.assertEqual(len(bg), 4)
        self.assertIn("小凯", bg)

    def test_jaccard_identical(self):
        a = bigrams("小凯回来啦")
        self.assertEqual(jaccard(a, a), 1.0)

    def test_jaccard_disjoint(self):
        a = bigrams("今天天气真好")
        b = bigrams("我要去睡觉了")
        self.assertEqual(jaccard(a, b), 0.0)

    def test_jaccard_partial(self):
        a = bigrams("小凯回来啦吃饭没")
        b = bigrams("小凯回来啦喝水没")
        sim = jaccard(a, b)
        self.assertTrue(0.0 < sim < 1.0)


if __name__ == "__main__":
    unittest.main()
