p = r"E:\NAS\doubao-butler\butler\self_evolution.py"
s = open(p, encoding="utf-8").read()

# 修复 skill_store 引用
s = s.replace(
    "store = getattr(self.rt, \"skill_store\", None)",
    "store = getattr(getattr(self.rt, \"runner\", None), \"store\", None)"
)

# 修复 all_skills 引用（在 analyze 方法中）
s = s.replace(
    "all_skills = store.list()",
    "all_skills = store.list() if store else []"
)

open(p, "w", encoding="utf-8").write(s)
print("fixed:")
print("  - runner.store:", "runner" in s and "store" in s)
