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
import struct
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
# 顺序是按 2026-10 实测结果排的（ghfast.top 当时不通，放最后兜底）。
MIRROR_PREFIXES = [
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
    "https://ghfast.top/",
]

APPS_START = "<!-- APPS:START -->"
APPS_END = "<!-- APPS:END -->"

# 本地仓库副本（tools/ 的上一层）。发布后顺手同步，避免本地与远端两套内容。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def local_sync(files):
    """把刚写到远端的文本同时落到本地副本。"""
    if not os.path.isdir(REPO_ROOT):
        return
    for rel, text in files.items():
        dst = os.path.join(REPO_ROOT, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    print("  - 本地副本已同步（%d 个文件）" % len(files))


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
        "channel": channel_of(parse_version(args.version)),
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


def build_legacy_json(doc):
    """旧版扁平格式（yuzhi-release / musi-release 时代的写法）。
    老客户端只改 URL、不改解析代码就能继续用。"""
    return {
        "version": doc["versionName"],
        "code": doc["versionCode"],
        "page": "https://github.com/%s/%s/releases/tag/%s-v%s" % (
            OWNER, REPO, doc["appId"], doc["versionName"]),
        "apk": doc["apk"]["url"],
        "size": doc["apk"]["size"],
        "notes": doc.get("notes", ""),
    }


def build_index_entry(app_id, doc):
    return {
        "appId": app_id,
        "channel": doc.get("channel", "stable"),
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
        return index, None
    put_file("apps.json", out, "index: %s v%s" % (app_id, entry["versionName"]), token, sha)
    print("  - 已更新 apps.json（共 %d 个 app）" % len(apps))
    return index, out


def update_readme(index, token, dry_run=False):
    """重写 README 里 APPS:START/END 之间的表格。

    以**本地** README 为基准（本地才是正在编辑的那份），远端只用来取 sha。
    否则本地刚写的段落会被远端的旧版本覆盖掉。
    """
    text, sha = get_file("README.md", token)
    local = os.path.join(REPO_ROOT, "README.md")
    if os.path.isfile(local):
        with open(local, encoding="utf-8") as fh:
            text = fh.read()
    if not text or APPS_START not in text or APPS_END not in text:
        print("  ! README.md 缺少 APPS 标记区，跳过表格更新")
        return None

    rows = ["| App | 标识 | 渠道 | 最新版本 | 大小 | 下载 |",
            "| --- | --- | --- | --- | --- | --- |"]
    for a in index["apps"]:
        size = a["size"] / 1048576.0
        ch_label = "内测" if a.get("channel") == "beta" else "对外"
        rows.append("| **%s** | `%s` | %s | v%s (%s) | %.2f MiB | [下载](%s) |" % (
            a["name"], a["appId"], ch_label, a["versionName"], a["versionCode"], size, a["apkUrl"]))
    if not index["apps"]:
        rows.append("| _暂无_ | | | | | |")

    block = "\n".join(rows)
    start = text.index(APPS_START) + len(APPS_START)
    end = text.index(APPS_END)
    new = text[:start] + "\n" + block + "\n" + text[end:]

    if dry_run:
        print("  [dry-run] README 表格 -> %d 行" % len(index["apps"]))
        return None
    put_file("README.md", new, "readme: 同步 app 列表", token, sha)
    print("  - 已刷新 README 表格")
    return new


# --------------------------------------------------------------------------
# 校验与命令
# --------------------------------------------------------------------------
def _axml_manifest(data):
    """解析 AndroidManifest.xml（二进制 XML），返回 manifest + uses-sdk 的属性字典。"""
    def string_pool(off):
        hsize = struct.unpack_from("<H", data, off + 2)[0]
        count, _, flags, str_start, _ = struct.unpack_from("<IIIII", data, off + 8)
        utf8 = bool(flags & (1 << 8))
        offsets = struct.unpack_from("<%dI" % count, data, off + hsize)
        base, out = off + str_start, []
        for o in offsets:
            p = base + o
            if utf8:
                p += 2 if data[p] & 0x80 else 1
                n = data[p]
                if n & 0x80:
                    n, p = ((n & 0x7F) << 8) | data[p + 1], p + 2
                else:
                    p += 1
                out.append(data[p:p + n].decode("utf-8", "replace"))
            else:
                n = struct.unpack_from("<H", data, p)[0]
                p += 2
                if n & 0x8000:
                    n, p = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0], p + 2
                out.append(data[p:p + n * 2].decode("utf-16-le", "replace"))
        return out

    strings, found, off = [], {}, 8
    while off < len(data) - 8:
        typ, _, size = struct.unpack_from("<HHI", data, off)
        if size == 0:
            break
        if typ == 0x0001:
            strings = string_pool(off)
        elif typ == 0x0102 and strings:      # START_ELEMENT
            name_i = struct.unpack_from("<I", data, off + 20)[0]
            attr_start, attr_size, attr_count = struct.unpack_from("<HHH", data, off + 24)
            if name_i < len(strings) and strings[name_i] in ("manifest", "uses-sdk"):
                p = off + 16 + attr_start
                for _ in range(attr_count):
                    _, a_name, a_raw, _, _, a_type, a_data = struct.unpack_from("<IIIHBBI", data, p)
                    nm = strings[a_name] if a_name < len(strings) else ""
                    if a_type == 0x03:                 # TYPE_STRING
                        found[nm] = strings[a_data]
                    elif a_type in (0x10, 0x11, 0x12):  # INT_DEC / HEX / BOOL
                        found[nm] = a_data
                    elif a_raw != 0xFFFFFFFF:
                        found[nm] = strings[a_raw]
                    p += attr_size
                if "package" in found:
                    break
        off += size
    return found


def read_apk_manifest(path):
    """返回 APK 里真实打包的 (packageName, versionName, versionCode, minSdk)。"""
    with zipfile.ZipFile(path) as z:
        attrs = _axml_manifest(z.read("AndroidManifest.xml"))
    return (attrs.get("package"), attrs.get("versionName"),
            attrs.get("versionCode"), attrs.get("minSdkVersion"))


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


# --------------------------------------------------------------------------
# 签名证书闸门
#   覆盖安装的前提是「证书完全一致」。发错包（比如本地用 debug keystore
#   随手编的包）时，apksigner/系统都会认为签名合法，只有老设备升级那一刻
#   才会报「签名不一致 / 版本校验未通过」，而且普通用户根本无从判断。
#   2026-11 的 1.10.07 就踩过：发布上去的是本地 debug 包，包内版本还是
#   1.10.06，老用户既收不到更新，手动装又装不上。
# --------------------------------------------------------------------------
KNOWN_CERTS = {
    # 慕思浏览器两条线必须同一把正式证书（MyTV）
    "musi": "073DD4416CFDE180DBC6D8661FF33E680E1559FC4291BA32F961D3791CF43909",
    "musi-beta": "073DD4416CFDE180DBC6D8661FF33E680E1559FC4291BA32F961D3791CF43909",
}


def apk_cert_sha256(path):
    """取 APK 里 v1 签名块的证书 SHA-256（大写无冒号）。取不到返回 None。

    APK 的 META-INF/*.RSA 是 PKCS#7 SignedData，用 openssl 解出第一张证书
    再做指纹 —— 不依赖 Java / Android SDK，CI 和本机都能跑。
    """
    import subprocess
    import tempfile

    with zipfile.ZipFile(path) as z:
        cand = [n for n in z.namelist()
                if n.startswith("META-INF/") and n.upper().endswith((".RSA", ".DSA", ".EC"))]
        if not cand:
            return None
        cand.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
        blob = z.read(cand[0])

    tmpdir = tempfile.mkdtemp(prefix="apkcert_")
    p7 = os.path.join(tmpdir, "sig.p7b")
    pem = os.path.join(tmpdir, "cert.pem")
    with open(p7, "wb") as fh:
        fh.write(blob)
    try:
        with open(pem, "wb") as out:
            subprocess.run(["openssl", "pkcs7", "-inform", "DER", "-in", p7,
                            "-print_certs", "-out", pem],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        r = subprocess.run(["openssl", "x509", "-in", pem, "-noout", "-fingerprint", "-sha256"],
                           check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    text = r.stdout.decode("utf-8", "replace")
    for line in text.splitlines():
        if "Fingerprint=" in line:
            fp = line.split("Fingerprint=", 1)[1].strip()
            fp = fp.replace(":", "").strip().upper()
            if len(fp) == 64:
                return fp
    return None


def check_certificate(path, expect):
    """证书与历史不一致时直接拒绝 —— 这不是可以 --force 放行的东西。"""
    got = apk_cert_sha256(path)
    if got is None:
        raise SystemExit(
            "无法读取 APK 签名证书（openssl 不可用或包未签名）：%s\n"
            "未签名的包发出去，客户一台都装不上。" % path)
    if got != expect.upper():
        raise SystemExit(
            "签名证书与历史不一致，已拒绝发布：\n"
            "  这个包: %s\n"
            "  应为  : %s\n"
            "证书一变，已装旧版的用户升级会报「签名不一致 / 安装失败」，\n"
            "必须用同一把正式 keystore 重新签名后再发（debug 包不行）。" % (got, expect))


# --------------------------------------------------------------------------
# 版本号规则
#   对外（stable）：1.0.0 起步；小改 +0.0.1；中等改 +0.1.0（patch 归零）；大改 +1.0.0（minor/patch 归零）
#   内测（beta）  ：A.0.0 起步，规则同上，A 只是渠道前缀，不参与大小比较
#   versionCode = major*10000 + minor*100 + patch，其中 A 记作 major=10
#     1.0.0 -> 10000   1.0.1 -> 10001   1.1.0 -> 10100
#     A.0.0 -> 100000  A.1.0 -> 100100  A.1.1 -> 100101
#   两条线互不干扰：内测包 code 恒大于对外包，所以「对外的装上去，再装内测」可以覆盖，
#   反过来「内测换对外」会被系统判为降级，必须先卸载。
# --------------------------------------------------------------------------
BETA_MAJOR = 10          # A.x.x 在 versionCode 里的数值
CHANNEL_BY_MAJOR = {1: "stable", BETA_MAJOR: "beta"}


def parse_version(text):
    """X.Y.Z（三段）或 X.Y（两段，老项目）都接受，统一成三段。

    首段可以是数字，也可以是字母 A —— A 就是内测线，内部按 major=10 参与换算。
    """
    parts = [p.strip() for p in str(text).strip().split(".")]
    if len(parts) not in (2, 3) or not parts[0]:
        raise SystemExit("版本号要写成 X.Y.Z 或 A.Y.Z，例如 1.0.0 / A.1.0（收到：%s）" % text)

    head = parts[0].upper()
    if head == "A":
        major = BETA_MAJOR
    elif head.isdigit():
        major = int(head)
        if major == BETA_MAJOR:
            raise SystemExit("首段 10 已被内测线 A 占用，内测请直接写 A.x.y（收到：%s）" % text)
    else:
        raise SystemExit("版本号首段只能是数字或字母 A，例如 1.2.1 / A.1.0（收到：%s）" % text)

    rest = parts[1:]
    if not all(p.isdigit() for p in rest):
        raise SystemExit("版本号 A 后面的段必须是数字，例如 A.1.0（收到：%s）" % text)

    nums = [int(p) for p in rest]
    while len(nums) < 2:
        nums.append(0)
    return (major, nums[0], nums[1])


def format_version(v):
    """把内部三元组还原成给人看的 versionName（A 线要还原成字母）。"""
    major, minor, patch = v
    if major == BETA_MAJOR:
        return "A.%d.%d" % (minor, patch)
    return "%d.%d.%d" % (major, minor, patch)


def channel_of(v):
    """1 开头 = 对外正式版，A 开头 = 自己内测用。"""
    return CHANNEL_BY_MAJOR.get(v[0], "other")


def version_code_for(v):
    return v[0] * 10000 + v[1] * 100 + v[2]


def bump_version(v, kind):
    major, minor, patch = v
    if kind == "small":
        patch += 1
        if patch > 99:            # 0.0.99 之后进位，避免和 0.1.0 撞 code
            patch, minor = 0, minor + 1
    elif kind == "medium":
        minor, patch = minor + 1, 0
    elif kind == "major":
        if major == BETA_MAJOR:
            raise SystemExit(
                "内测线（A.x）没有大版本 —— A 只是渠道前缀，不参与大改版递增。\n"
                "对外做大改版时，内测用 --version A.<对外 minor>.0 手工跟上即可（例如 A.2.0）。")
        major, minor, patch = major + 1, 0, 0
    else:
        raise SystemExit("--bump 只能是 small / medium / major")
    return (major, minor, patch)


def current_version(app_id, token):
    """优先取远端 <appId>/version.json，其次取本地副本。"""
    doc = None
    try:
        text, _ = get_file("%s/version.json" % app_id, token)
        if text:
            doc = json.loads(text)
    except SystemExit:
        doc = None
    if doc is None:
        local = os.path.join(REPO_ROOT, app_id, "version.json")
        if os.path.isfile(local):
            with open(local, encoding="utf-8") as fh:
                doc = json.load(fh)
    if not doc:
        return None
    return parse_version(doc["versionName"])


def current_code(app_id, token):
    """仓库里记录的真实 versionCode（老版本号不能用新公式反推，必须直接读）。"""
    try:
        text, _ = get_file("%s/version.json" % app_id, token)
        if text:
            return int(json.loads(text).get("versionCode") or 0)
    except (SystemExit, ValueError):
        pass
    return None


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
    p.add_argument("--version", help="versionName，X.Y.Z，如 1.0.0")
    p.add_argument("--bump", choices=["small", "medium", "major"],
                   help="按规则自动递增版本号（读现有 version.json）：small=+0.0.1 / medium=+0.1.0 / major=+1.0.0")
    p.add_argument("--version-code", help="一般不用填，由 versionName 自动换算")
    p.add_argument("--apk", help="本地 APK 路径")
    p.add_argument("--notes", default="", help="更新说明，\\n 换行")
    p.add_argument("--notes-file", help="从文件读取更新说明（优先于 --notes）")
    p.add_argument("--min-sdk", type=int, help="最低 Android API，如 24")
    p.add_argument("--package-name", help="应用包名，如 com.shejitu.scrollprompter")
    p.add_argument("--mandatory", action="store_true", help="标记为强制更新")
    p.add_argument("--mirror", action="append", help="额外的下载镜像前缀，可重复")
    p.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    p.add_argument("--dry-run", action="store_true", help="只打印，不写远端")
    p.add_argument("--force", action="store_true", help="APK 内版本与参数不一致时仍然发布（一般别用）")
    p.add_argument("--expect-cert",
                   help="期望的签名证书 SHA-256（大写，可带冒号）；默认按 appId 查 KNOWN_CERTS")
    p.add_argument("--allow-unshielded", action="store_true",
                   help="允许发布未加固的对外包（musi 默认要求有 BLA 清单）")
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

    # ---- 版本号：--bump 自动递增，或 --version 直接给，versionCode 一律自动换算 ----
    if args.bump:
        if not args.app_id:
            raise SystemExit("--bump 需要同时给 --app-id")
        cur = current_version(args.app_id, token)
        if cur is None:
            raise SystemExit("仓库里还没有 %s 的版本记录，首次请用 --version 1.0.0" % args.app_id)
        if channel_of(cur) == "other":
            raise SystemExit(
                "当前记录是遗留版本号 %s，已作废，不能在这上面递增。\n"
                "首次请显式指定：--version 1.0.0（对外）或 --version A.0.0（内测）。"
                % format_version(cur))
        new = bump_version(cur, args.bump)
        args.version = format_version(new)
        print("版本递增（%s）：%s → %s" % (args.bump, format_version(cur), args.version))
    if args.version and not args.version_code:
        if len(str(args.version).split(".")) == 3:
            args.version_code = str(version_code_for(parse_version(args.version)))
        else:
            raise SystemExit("--version %s 不是三段式，请显式给 --version-code" % args.version)

    if args.bump and not args.apk:
        print("下个版本：v%s (versionCode=%s)，未提供 --apk，仅预览。" % (args.version, args.version_code))
        return

    for field in ("app_id", "name", "version", "version_code", "apk"):
        if not getattr(args, field):
            raise SystemExit("缺少参数 --%s" % field.replace("_", "-"))
    # 渠道与 appId 必须一致：A 开头的内测版不许发到对外通道，反之亦然
    ch = channel_of(parse_version(args.version))
    if ch == "beta" and not str(args.app_id).endswith("-beta"):
        print("\n这是 A.x 内测版，但 appId 是 %s。" % args.app_id)
        print("内测请用对应通道：--app-id %s-beta" % args.app_id)
        raise SystemExit("已中止：内测版不能进对外通道，否则所有客户都会被提示升级。")
    if ch == "stable" and str(args.app_id).endswith("-beta"):
        raise SystemExit("已中止：%-beta 通道只能发 A.x 内测版（收到 %s）。" % (args.version))
    if ch == "other":
        print("  ! 版本号 %s 既不是 1.x（对外）也不是 A.x（内测），渠道记为 other" % args.version)
    if not str(args.app_id).replace("-", "").isalnum() or str(args.app_id).lower() != args.app_id:
        raise SystemExit("--app-id 只能用 a-z0-9-_，且必须小写")
    if args.notes_file:
        with open(args.notes_file, "r", encoding="utf-8") as fh:
            args.notes = fh.read().strip()
    args.notes = (args.notes or "").replace("\\n", "\n")

    sanity_check_apk(args.apk)
    pkg, apk_vname, apk_vcode, _ = read_apk_manifest(args.apk)

    # 签名闸门：证书必须与历史一致，否则老用户装不上（跳过 --force 也不行）
    expect_cert = args.expect_cert or KNOWN_CERTS.get(args.app_id)
    if expect_cert:
        check_certificate(args.apk, expect_cert.replace(":", ""))
        print("  证书  : 与历史一致（%s…）" % expect_cert.replace(":", "")[:16])
    else:
        print("  ! 没配置 %s 的期望证书，跳过签名校验" % args.app_id)

    # 对外版必须是加固过的包：没加固的包发出去等于把源码逻辑裸奔
    if args.app_id == "musi" and not args.allow_unshielded:
        with zipfile.ZipFile(args.apk) as z:
            shielded = "assets/enclave/bla-attestation-manifest.json" in z.namelist()
        if not shielded:
            raise SystemExit(
                "这个 APK 里没有 BLA 加固清单，是未加固的包。\n"
                "对外版必须是「编译 → BLA 加固 → 重签」的产物；\n"
                "确认要发裸包请加 --allow-unshielded（一般不该这么做）。")
        print("  加固  : BLA 清单存在")

    problems = []
    if str(apk_vname) != str(args.version):
        problems.append("versionName：包内是 %s，元数据要写 %s" % (apk_vname, args.version))
    if str(apk_vcode) != str(args.version_code):
        problems.append("versionCode：包内是 %s，元数据要写 %s" % (apk_vcode, args.version_code))
    if args.package_name and pkg and pkg != args.package_name:
        problems.append("包名：包内是 %s，参数写 %s" % (pkg, args.package_name))
    if problems:
        print("\nAPK 与要发布的版本对不上：")
        for x in problems:
            print("  - " + x)
        print("\n客户端就是靠这两个数判断更新，不一致会导致「永远提示更新」或「永远收不到更新」。")
        print("请改 build.gradle 里的 versionName / versionCode 重新打包（两者要和 --version 一致）。")
        if not args.force:
            raise SystemExit("已中止。确实要强推请加 --force。")
        print("  （已按 --force 继续）")
    if not args.package_name:
        args.package_name = pkg
        print("  包名  : %s（取自 APK）" % pkg)
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

    # 安卓不接受 versionCode 回退，提前提醒（用仓库里记录的真实 code，不要反推）
    try:
        prev_code = current_code(args.app_id, token)
    except SystemExit:
        prev_code = None
    if prev_code and int(args.version_code) <= prev_code:
        print("  ! 新 versionCode %s 不大于仓库记录的上一版 %s，安卓会拒绝覆盖安装"
              % (args.version_code, prev_code))

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
    version_text = json.dumps(version_doc, ensure_ascii=False, indent=2) + "\n"
    old_sha = get_sha_or_none(path, token)
    put_file(path, version_text,
             "release: %s v%s" % (args.app_id, args.version), token, old_sha)
    print("  - 已写入 %s" % path)

    legacy_path = "%s/legacy.json" % args.app_id
    legacy_text = json.dumps(build_legacy_json(version_doc), ensure_ascii=False, indent=2) + "\n"
    put_file(legacy_path, legacy_text,
             "release: %s v%s（旧格式兼容）" % (args.app_id, args.version), token,
             get_sha_or_none(legacy_path, token))
    print("  - 已写入 %s" % legacy_path)

    print("[4/4] 更新索引与 README")
    index, index_text = update_index(args.app_id, build_index_entry(args.app_id, version_doc), token)
    readme_text = update_readme(index, token)

    local = {path: version_text, legacy_path: legacy_text}
    if index_text:
        local["apps.json"] = index_text
    if readme_text:
        local["README.md"] = readme_text
    local_sync(local)

    print("\n完成。客户端检查更新地址：")
    print("  https://raw.githubusercontent.com/%s/%s/%s/%s/version.json" % (OWNER, REPO, BRANCH, args.app_id))
    print("  镜像: https://cdn.jsdelivr.net/gh/%s/%s@%s/%s/version.json" % (OWNER, REPO, BRANCH, args.app_id))
    print("  旧格式兼容: https://raw.githubusercontent.com/%s/%s/%s/%s/legacy.json" % (
        OWNER, REPO, BRANCH, args.app_id))
    print("  下载: %s" % download_url)


if __name__ == "__main__":
    main()
