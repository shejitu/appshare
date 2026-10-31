#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把本地 appshare 目录的改动提交到 GitHub（一次 commit）。

为什么不用 git push：本机直连 github.com 常常握手失败（api.github.com 反而正常），
这里走 Git Data API（blob → tree → commit → 更新 ref），等效于一次普通提交。

用法：
    python tools/sync.py                 # 提交所有改动
    python tools/sync.py -m "改文案"      # 自定义提交信息
    python tools/sync.py --dry-run       # 只看会改什么
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

OWNER, REPO, BRANCH = "shejitu", "appshare", "main"
API = "https://api.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__", ".idea", ".vscode"}
SKIP_EXT = {".pyc", ".apk", ".zip"}
DEFAULT_TOKEN_FILE = r"E:/我原创的软件/github2monthpat.txt"


def token():
    t = os.environ.get("GH_TOKEN", "").strip()
    if not t and os.path.isfile(DEFAULT_TOKEN_FILE):
        with open(DEFAULT_TOKEN_FILE, encoding="utf-8-sig") as f:
            t = f.read().strip()
    if not t:
        raise SystemExit("找不到 token：设 GH_TOKEN，或放到 %s" % DEFAULT_TOKEN_FILE)
    return t


TOKEN = token()


def call(path, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "token " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "appshare-sync")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        print("HTTP %s %s %s\n%s" % (e.code, method, path,
                                     e.read().decode("utf-8", "replace")[:600]))
        sys.exit(1)


def local_files():
    out = {}
    for dirpath, dirnames, names in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for n in names:
            if os.path.splitext(n)[1].lower() in SKIP_EXT:
                continue
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, ROOT).replace("\\", "/")
            out[rel] = full
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--message", default="chore: 同步文档与工具")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ref = call("/repos/%s/%s/git/ref/heads/%s" % (OWNER, REPO, BRANCH))
    head = call("/repos/%s/%s/git/commits/%s" % (OWNER, REPO, ref["object"]["sha"]))
    base_tree = head["tree"]["sha"]
    remote = call("/repos/%s/%s/git/trees/%s?recursive=1" % (OWNER, REPO, base_tree))
    remote_blobs = {n["path"]: n["sha"] for n in remote.get("tree", []) if n["type"] == "blob"}

    files = local_files()
    tree_ops = []
    for rel in sorted(files):
        with open(files[rel], "rb") as fh:
            content = fh.read()
        local_sha = subprocess.run(["git", "hash-object", "--stdin"], input=content,
                                   stdout=subprocess.PIPE, check=False).stdout.decode().strip()
        if local_sha and remote_blobs.get(rel) == local_sha:
            continue
        print("  M %-34s %6d B" % (rel, len(content)))
        blob = call("/repos/%s/%s/git/blobs" % (OWNER, REPO), "POST",
                    {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
        tree_ops.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    for rel in sorted(set(remote_blobs) - set(files)):
        print("  D %s" % rel)
        tree_ops.append({"path": rel, "mode": "100644", "type": "blob", "sha": None})

    if not tree_ops:
        print("没有改动，无需提交。")
        return
    if args.dry_run:
        print("\n[dry-run] 共 %d 处改动，未提交" % len(tree_ops))
        return

    new_tree = call("/repos/%s/%s/git/trees" % (OWNER, REPO), "POST",
                    {"base_tree": base_tree, "tree": tree_ops})
    commit = call("/repos/%s/%s/git/commits" % (OWNER, REPO), "POST", {
        "message": args.message, "tree": new_tree["sha"], "parents": [ref["object"]["sha"]]})
    call("/repos/%s/%s/git/refs/heads/%s" % (OWNER, REPO, BRANCH), "PATCH",
         {"sha": commit["sha"], "force": False})
    print("\n已提交 %s 处改动 → %s" % (len(tree_ops), commit["sha"][:10]))


if __name__ == "__main__":
    main()
