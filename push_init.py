#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地 appshare 目录初始内容一次性推到 GitHub（单 commit，走 Git Data API）。"""
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

OWNER, REPO, BRANCH = "shejitu", "appshare", "main"
API = "https://api.github.com"
ROOT = r"E:/我的workbuddy/WorkSpace/appshare"

TOKEN = os.environ.get("GH_TOKEN", "").strip()
if not TOKEN:
    with open(r"E:/我原创的软件/github2monthpat.txt", encoding="utf-8-sig") as f:
        TOKEN = f.read().strip()


def call(path, method="GET", payload=None, raw=False):
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "token " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "appshare-push")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            b = r.read()
            return b if raw else json.loads(b or b"{}")
    except urllib.error.HTTPError as e:
        print("HTTP %s %s %s" % (e.code, method, path))
        print(e.read().decode("utf-8", "replace")[:800])
        sys.exit(1)


FILES = []
for dirpath, _, names in os.walk(ROOT):
    if ".git" in dirpath:
        continue
    for n in names:
        full = os.path.join(dirpath, n)
        rel = os.path.relpath(full, ROOT).replace("\\", "/")
        FILES.append(rel)
FILES.sort()

ref = call("/repos/%s/%s/git/ref/heads/%s" % (OWNER, REPO, BRANCH))
head = call("/repos/%s/%s/git/commits/%s" % (OWNER, REPO, ref["object"]["sha"]))
base_tree = head["tree"]["sha"]
print("base commit %s / tree %s" % (ref["object"]["sha"][:8], base_tree[:8]))

tree = []
for rel in FILES:
    with open(os.path.join(ROOT, rel), "rb") as fh:
        content = fh.read()
    blob = call("/repos/%s/%s/git/blobs" % (OWNER, REPO), "POST",
                {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
    tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    print("  blob %-28s %6d B  %s" % (rel, len(content), blob["sha"][:8]))

new_tree = call("/repos/%s/%s/git/trees" % (OWNER, REPO), "POST",
                {"base_tree": base_tree, "tree": tree})
commit = call("/repos/%s/%s/git/commits" % (OWNER, REPO), "POST", {
    "message": "init: appshare 分发仓库（版本元数据 + 发布工具 + 安卓接入代码）",
    "tree": new_tree["sha"],
    "parents": [ref["object"]["sha"]],
})
call("/repos/%s/%s/git/refs/heads/%s" % (OWNER, REPO, BRANCH), "PATCH",
     {"sha": commit["sha"], "force": False})
print("\nOK commit %s" % commit["sha"])
print("https://github.com/%s/%s/commit/%s" % (OWNER, REPO, commit["sha"]))
