from butler.config import Settings
s = Settings()
t = s.nowvoice_token
print(f"token: {repr(t[:30] if t else 'EMPTY')}")
