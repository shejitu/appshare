#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
appshare 发布工具
=================

把本地编译好的 APK 发布到公开仓库 https://github.com/shejitu/appshare，
并自动更新元数据（<appId>/version.json 与 apps.json），供客户端检查更新。

用法示例
--------
    # 首次发布会自动创建 app 目录与索引条目
    python tools/publish.py --app-id scrollprompter --name 滚动提示器 \
        --version 1.9 --version-code 19 --apk "E:/我原创的软件/提示器/滚动提示器_v1.9.apk" \
        --notes "修复长文本滚动卡顿\n新增字号调节"

    # 只看会做什么，不真正上传
    python tools/publish.py ... --dry-run

    # 查看当前仓库里有哪些 app
    python tools/publish.py --list

Token
-----
优先读环境变量 GH_TOKEN，其次读 --token-file（默认 E:/我原创的软件/github2monthpat.txt）。
需要经典 PAT 的 repo + workflow 权限。

设计要点
--------
* APK 二进制放 GitHub Releases（不打进 git 历史，仓库不会越滚越大）；
* 版本元数据是纯静态 JSON（走 raw / jsDelivr CDN），客户端检查更新不消耗 API 配额；
* version.json 自带 sha256，客户端下载后必须校验，防止中间人替换；
* 一个 app 一个 tag（<appId>-v<version>），历史版本全部保留在 Releases 里。
"""

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

OWNER = "shejitu"
REPO = "appshare"
BRANCH = "main"
API = "https://api.github.com"
DEFAULT_TOKEN_FILE = r"E:/我原创的软件/github2monthpat.txt"

# 国内可用性兜底：客户端按顺序尝试，任一个成功即停。
MIRROR_PREFIXES = [
    "https://ghfast.top/",
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
]

APPS_START = "<!-- APPS:START -->"
APPS_END = "<!-- APPS:END -->"


# --------------------------------------------------------------------------
# HTTP 基础
# --------------------------------------------------------------------------
def _request(url, method="GET", payload=None, token=None, raw=False, timeout=180):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "token %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "appshare-publish")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return (body if raw else json.loads(body.decode("utf-8") or "{}"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise SystemExit("HTTP %s %s %s\n%s" % (e.code, method, url, detail))


def get_file(path, token):
    """返回 (文本内容, sha)；不存在返回 (None, None)。"""
    url = "%s/repos/%s/%s/contents/%s?ref=%s" % (
        API, OWNER, REPO, urllib.parse.quote(path, safe="/"), BRANCH)
    try:
        d = _request(url, token=token)
    except SystemExit as e:
        if "HTTP 404" in str(e):
            return None, None
        raise
    return base64.b64decode(d["content"]).decode("utf-8"), d["sha"]


def put_file(path, text, message, token, sha=None):
    url = "%s/repos/%s/%s/contents/%s" % (API, OWNER, REPO, urllib.parse.quote(path, safe="/"))
    payload = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    return _request(url, method="PUT", payload=payload, token=token)


def put_binary(path, data, message, token, sha=None):
    url = "%s/repos/%s/%s/contents/%s" % (API, OWNER, REPO, urllib.parse.quote(path, safe="/"))
    payload = {
        "message": message,
        "content": base64.b64encode(data).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    return _request(url, method="PUT", payload=payload, token=token)


def get_sha_or_none(path, token):
    try:
        _, sha = get_file(path, token)
        return sha
    except SystemExit:
        return None


# --------------------------------------------------------------------------
# Releases
# --------------------------------------------------------------------------
def ensure_release(tag, title, body, token):
    url = "%s/repos/%s/%s/releases/tags/%s" % (API, OWNER, REPO, urllib.parse.quote(tag, safe=""))
    try:
        return _request(url, token=token)
    except SystemExit as e:
        if "HTTP 404" not in str(e):
            raise
    return _request("%s/repos/%s/%s/releases" % (API, OWNER, REPO), method="POST",
                    payload={"tag_name": tag, "name": title, "body": body,
                             "draft": False, "prerelease": False}, token=token)


def upload_asset(release, filename, filepath, token):
    """同名资产已存在则先删掉，实现「同版本重发覆盖」。"""
    for a in release.get("assets", []):
        if a["name"] == filename:
            _request("%s/repos/%s/%s/releases/assets/%s" % (API, OWNER, REPO, a["id"]),
                     method="DELETE", token=token)
            print("  - 已删除旧资产 %s" % filename)

    upload_url = release["upload_url"].split("{")[0]
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as fh:
        data = fh.read()

    req = urllib.request.Request(
        "%s?name=%s" % (upload_url, urllib.parse.quote(filename)),
        data=data, method="POST")
    req.add_header("Authorization", "token %s" % token)
    req.add_header("Content-Type", ctype)
    req.add_header("Content-Length", str(size))
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "appshare-publish")
    print("  - 上传 %s (%.2f MiB) ..." % (filename, size / 1048576.0))
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            asset = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit("上传失败 HTTP %s\n%s" % (e.code, e.read().decode("utf-8", "replace")[:600]))
    print("  - 完成 browser_download_url = %s" % asset["browser_download_url"])
    return asset


# --------------------------------------------------------------------------
# 元数据
# --------------------------------------------------------------------------
def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_version_json(args, apk_name, size, sha256, download_url, published_at):
    mirrors = [p + download_url for p in MIRROR_PREFIXES]
    if args.mirror:
        mirrors = list(args.mirror) + mirrors
    doc = {
        "schemaVersion": 1,
        "appId": args.app_id,
        "name": args.name,
        "versionName": args.version,
        "versionCode": int(args.version_code),
        "publishedAt": published_at,
        "mandatory": bool(args.mandatory),
        "minSdk": int(args.min_sdk) if args.min_sdk is not None else None,
        "packageName": args.package_name or None,
        "notes": args.notes or "",
        "apk": {
            "file": apk_name,
            "size": size,
            "sha256": sha256,
            "url": download_url,
            "mirrors": mirrors,
        },
    }
    return doc


def build_index_entry(app_id, doc):
    return {
        "appId": app_id,
        "name": doc["name"],
        "versionName": doc["versionName"],
        "versionCode": doc["versionCode"],
        "publishedAt": doc["publishedAt"],
        "mandatory": doc["mandatory"],
        "minSdk": doc["minSdk"],
        "packageName": doc["packageName"],
        "notes": doc["notes"],
        "size": doc["apk"]["size"],
        "sha256": doc["apk"]["sha256"],
        "checkUrl": "https://raw.githubusercontent.com/%s/%s/%s/%s/version.json" % (
            OWNER, REPO, BRANCH, app_id),
        "checkUrlMirror": "https://cdn.jsdelivr.net/gh/%s/%s@%s/%s/version.json" % (
            OWNER, REPO, BRANCH, app_id),
        "apkUrl": doc["apk"]["url"],
        "pageUrl": "https://github.com/%s/%s/releases/tag/%s-v%s" % (
            OWNER, REPO, app_id, doc["versionName"]),
    }


def update_index(app_id, entry, token, dry_run=False):
    text, sha = get_file("apps.json", token)
    if text:
        index = json.loads(text)
    else:
        index = {"schemaVersion": 1, "updatedAt": None, "apps": []}
    apps = [a for a in index.get("apps", []) if a.get("appId") != app_id]
    apps.append(entry)
    apps.sort(key=lambda a: a["appId"])
    index["apps"] = apps
    index["updatedAt"] = entry["publishedAt"]
    out = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
    if dry_run:
        print("  [dry-run] apps.json -> %d 个 app" % len(apps))
        return index
    put_file("apps.json", out, "index: %s v%s" % (app_id, entry["versionName"]), token, sha)
    print("  - 已更新 apps.json（共 %d 个 app）" % len(apps))
    return index


def update_readme(index, token, dry_run=False):
    """重写 README 里 APPS:START/END 之间的表格。"""
    text, sha = get_file("README.md", token)
    if not text or APPS_START not in text or APPS_END not in text:
        print("  ! README.md 缺少 APPS 标记区，跳过表格更新")
        return

    rows = ["| App | 标识 | 最新版本 | 大小 | 下载 |", "| --- | --- | --- | --- | --- |"]
    for a in index["apps"]:
        size = a["size"] / 1048576.0
        rows.append("| **%s** | `%s` | v%s (%s) | %.2f MiB | [下载](%s) |" % (
            a["name"], a["appId"], a["versionName"], a["versionCode"], size, a["apkUrl"]))
    if not index["apps"]:
        rows.append("| _暂无_ | | | | |")

    block = "\n".join(rows)
    start = text.index(APPS_START) + len(APPS_START)
    end = text.index(APPS_END)
    new = text[:start] + "\n" + block + "\n" + text[end:]

    if dry_run:
        print("  [dry-run] README 表格 -> %d 行" % len(index["apps"]))
        return
    put_file("README.md", new, "readme: 同步 app 列表", token, sha)
    print("  - 已刷新 README 表格")


# --------------------------------------------------------------------------
# 校验与命令
# --------------------------------------------------------------------------
def sanity_check_apk(path):
    if not os.path.isfile(path):
        raise SystemExit("APK 不存在：%s" % path)
    if os.path.getsize(path) > 2 * 1024 ** 3:
        raise SystemExit("超过 GitHub Release 单资产 2GB 限制")
    if not zipfile.is_zipfile(path):
        raise SystemExit("不是合法 APK（zip 结构校验失败）：%s" % path)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
    if "AndroidManifest.xml" not in names:
        raise SystemExit("缺 AndroidManifest.xml，不像 APK：%s" % path)


def cmd_list(token):
    text, _ = get_file("apps.json", token)
    index = json.loads(text) if text else {"apps": []}
    if not index["apps"]:
        print("（仓库里还没有任何 app）")
        return
    print("%-16s %-14s %-10s %-10s %s" % ("appId", "名称", "版本", "versionCode", "大小"))
    for a in index["apps"]:
        print("%-16s %-14s %-10s %-10s %.2f MiB" % (
            a["appId"], a["name"], "v" + a["versionName"], a["versionCode"],
            a["size"] / 1048576.0))


def main():
    p = argparse.ArgumentParser(description="发布 APK 到 appshare 公开仓库")
    p.add_argument("--app-id", help="英文小写标识，如 scrollprompter（目录名）")
    p.add_argument("--name", help="显示名称，如 滚动提示器")
    p.add_argument("--version", help="versionName，如 1.9")
    p.add_argument("--version-code", help="versionCode 整数，客户端靠它比较大小")
    p.add_argument("--apk", help="本地 APK 路径")
    p.add_argument("--notes", default="", help="更新说明，\\n 换行")
    p.add_argument("--notes-file", help="从文件读取更新说明（优先于 --notes）")
    p.add_argument("--min-sdk", type=int, help="最低 Android API，如 24")
    p.add_argument("--package-name", help="应用包名，如 com.shejitu.scrollprompter")
    p.add_argument("--mandatory", action="store_true", help="标记为强制更新")
    p.add_argument("--mirror", action="append", help="额外的下载镜像前缀，可重复")
    p.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    p.add_argument("--dry-run", action="store_true", help="只打印，不写远端")
    p.add_argument("--list", action="store_true", help="列出仓库里已有的 app")
    args = p.parse_args()

    token = os.environ.get("GH_TOKEN", "").strip()
    if not token and os.path.isfile(args.token_file):
        with open(args.token_file, "r", encoding="utf-8-sig") as fh:
            token = fh.read().strip()
    if not token:
        raise SystemExit("找不到 token：请设 GH_TOKEN，或放进 %s" % args.token_file)

    if args.list:
        cmd_list(token)
        return

    for field in ("app_id", "name", "version", "version_code", "apk"):
        if not getattr(args, field):
            raise SystemExit("缺少参数 --%s" % field.replace("_", "-"))
    if not str(args.app_id).replace("-", "").isalnum() or str(args.app_id).lower() != args.app_id:
        raise SystemExit("--app-id 只能用 a-z0-9-_，且必须小写")
    if args.notes_file:
        with open(args.notes_file, "r", encoding="utf-8") as fh:
            args.notes = fh.read().strip()
    args.notes = (args.notes or "").replace("\\n", "\n")

    sanity_check_apk(args.apk)
    size = os.path.getsize(args.apk)
    sha = file_sha256(args.apk)
    published_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tag = "%s-v%s" % (args.app_id, args.version)
    apk_name = "%s-v%s.apk" % (args.app_id, args.version)

    print("准备发布 %s v%s (versionCode=%s)" % (args.name, args.version, args.version_code))
    print("  APK   : %s" % args.apk)
    print("  大小  : %.2f MiB" % (size / 1048576.0))
    print("  sha256: %s" % sha)
    print("  tag   : %s" % tag)

    # 先算好下载地址（Releases 资产的 URL 规则是确定的）
    download_url = "https://github.com/%s/%s/releases/download/%s/%s" % (
        OWNER, REPO, urllib.parse.quote(tag), urllib.parse.quote(apk_name))
    version_doc = build_version_json(args, apk_name, size, sha, download_url, published_at)

    if args.dry_run:
        print("\n[dry-run] version.json 预览：")
        print(json.dumps(version_doc, ensure_ascii=False, indent=2))
        idx = {"schemaVersion": 1, "updatedAt": published_at,
               "apps": [build_index_entry(args.app_id, version_doc)]}
        update_index(args.app_id, build_index_entry(args.app_id, version_doc), token, dry_run=True)
        update_readme(idx, token, dry_run=True)
        return

    print("\n[1/4] 创建/复用 Release")
    body = "%s v%s\n\n%s" % (args.name, args.version, args.notes or "（无更新说明）")
    release = ensure_release(tag, "%s v%s" % (args.name, args.version), body, token)

    print("[2/4] 上传 APK")
    upload_asset(release, apk_name, args.apk, token)

    print("[3/4] 写 %s/version.json" % args.app_id)
    path = "%s/version.json" % args.app_id
    old_sha = get_sha_or_none(path, token)
    put_file(path, json.dumps(version_doc, ensure_ascii=False, indent=2) + "\n",
             "release: %s v%s" % (args.app_id, args.version), token, old_sha)
    print("  - 已写入 %s" % path)

    print("[4/4] 更新索引与 README")
    index = update_index(args.app_id, build_index_entry(args.app_id, version_doc), token)
    update_readme(index, token)

    print("\n完成。客户端检查更新地址：")
    print("  https://raw.githubusercontent.com/%s/%s/%s/%s/version.json" % (OWNER, REPO, BRANCH, args.app_id))
    print("  镜像: https://cdn.jsdelivr.net/gh/%s/%s@%s/%s/version.json" % (OWNER, REPO, BRANCH, args.app_id))
    print("  下载: %s" % download_url)


if __name__ == "__main__":
    main()
