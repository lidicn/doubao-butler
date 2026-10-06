#!/usr/bin/env python3
"""
Xiaomi MiJia service_token 刷新工具（已验证可用版）

从 pass_token 换取新的 service_token + ssecurity。
核心逻辑与 songloft-plugin-miot 的 exchangeServiceToken 一致。

用法:
    python xiaomi_token_refresh.py --accounts-file /path/to/accounts
    python xiaomi_token_refresh.py --pass-token "V1:..." --user-id "23802665"

输出: stdout 纯 JSON，失败时退出码 1
"""
import argparse
import base64
import hashlib
import json
import sys
import time
import uuid

import requests


def read_accounts(path: str) -> dict:
    """从 Songloft accounts 文件读取第一个账号"""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    accounts = json.loads(json.loads(raw))
    account = accounts[0]
    return {
        "user_id": str(account.get("user_id", "")),
        "pass_token": account.get("pass_token", ""),
        "ssecurity": account.get("services", {}).get("micoapi", {}).get("ssecurity", ""),
    }


def refresh(pass_token: str, user_id: str) -> dict:
    """用 pass_token 刷新 service_token"""
    device_id = uuid.uuid4().hex[:12]
    session = requests.Session()
    session.headers["User-Agent"] = (
        f"Android-7.1.1-1.0.0-ONEPLUS A3010-136-{device_id} "
        f"APP/xiaomi.smarthome APPV/62830"
    )

    # Step 1: serviceLogin
    resp = session.get(
        "https://account.xiaomi.com/pass/serviceLogin",
        params={"sid": "micoapi", "_json": "true"},
        cookies={
            "passToken": pass_token,
            "userId": user_id,
            "deviceId": device_id,
            "sdkVersion": "3.8.6",
        },
        allow_redirects=False,
        timeout=15,
    )

    raw = resp.text
    if raw.startswith("&&&START&&&"):
        raw = raw.replace("&&&START&&&", "", 1)
    data = json.loads(raw)

    if data.get("code") != 0:
        raise RuntimeError(f"serviceLogin failed: code={data.get('code')} desc={data.get('desc')}")

    location = data["location"]
    ssecurity = data["ssecurity"]
    nonce = str(data["nonce"])

    # Step 2: clientSign
    client_sign = base64.b64encode(
        hashlib.sha1(f"nonce={nonce}&{ssecurity}".encode()).digest()
    ).decode()

    # Step 3: 跟随 location
    sep = "&" if "?" in location else "?"
    sts_url = f"{location}{sep}_userIdNeedEncrypt=true&clientSign={client_sign}"

    resp2 = session.get(sts_url, allow_redirects=True, timeout=15)

    # 从 cookies 提取 serviceToken
    service_token = None
    for cookie in session.cookies:
        if cookie.name == "serviceToken" and cookie.value:
            service_token = cookie.value
            break

    if not service_token:
        raise RuntimeError(
            f"Failed to get serviceToken: status={resp2.status_code} "
            f"body={resp2.text[:200]!r}"
        )

    return {
        "service_token": service_token,
        "ssecurity": ssecurity,
        "user_id": user_id,
        "expires_at": int(time.time() * 1000) + 12 * 3600 * 1000,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts-file", help="Songloft accounts 文件路径")
    parser.add_argument("--pass-token", help="小米 pass_token")
    parser.add_argument("--user-id", help="小米 user_id")
    args = parser.parse_args()

    if args.accounts_file:
        creds = read_accounts(args.accounts_file)
        pass_token = creds["pass_token"]
        user_id = creds["user_id"]
    elif args.pass_token and args.user_id:
        pass_token = args.pass_token
        user_id = args.user_id
    else:
        print("Error: 需要 --accounts-file 或 --pass-token + --user-id", file=sys.stderr)
        sys.exit(1)

    try:
        result = refresh(pass_token, user_id)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
