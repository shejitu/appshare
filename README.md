# appshare · 公开 App 分发仓库

私人仓库里做出来的 App，需要对外分享时，把**编译好的安装包**发布到这里。
别人装上 App 后，App 自己来这儿查版本：有新版本就直接下载覆盖安装。

- 元数据是静态 JSON，走 CDN，**不消耗 GitHub API 配额**，也不怕限流；
- 安装包放 GitHub Releases，不进 git 历史，仓库不会越滚越大；
- 每个版本都带 `sha256`，客户端下载后校验，防止被中间人替换。

---

## App 列表

<!-- APPS:START -->
| App | 标识 | 最新版本 | 大小 | 下载 |
| --- | --- | --- | --- | --- |
| **滚动提示器** | `scrollprompter` | v1.8 (9) | 5.80 MiB | [下载](https://github.com/shejitu/appshare/releases/download/scrollprompter-v1.8/scrollprompter-v1.8.apk) |
| **予知影视** | `yuzhi` | v3.5.3 (109) | 30.68 MiB | [下载](https://github.com/shejitu/appshare/releases/download/yuzhi-v3.5.3/yuzhi-v3.5.3.apk) |
<!-- APPS:END -->

> 表格由 `tools/publish.py` 自动维护，不用手改。

---

## 客户端怎么查更新

每个 App 只需知道自己的 `appId`，然后拉这个地址（约 1KB）：

```
https://raw.githubusercontent.com/shejitu/appshare/main/<appId>/version.json
```

国内直连 raw 不稳时的等价镜像：

```
https://ghproxy.net/https://raw.githubusercontent.com/shejitu/appshare/main/<appId>/version.json
https://cdn.jsdelivr.net/gh/shejitu/appshare@main/<appId>/version.json
```

> jsDelivr 对 `@main` 有缓存，刚发完版可能还是旧内容。客户端要**并发请求三条、取 versionCode 最大**那条，
> 不能直接采信最先返回的（`client/AndroidUpdater.kt` 已经是这个逻辑）。

拿到的 JSON：

```json
{
  "schemaVersion": 1,
  "appId": "scrollprompter",
  "name": "滚动提示器",
  "versionName": "1.9",
  "versionCode": 19,
  "publishedAt": "2026-10-31T02:10:00Z",
  "mandatory": false,
  "minSdk": 24,
  "packageName": "com.shejitu.scrollprompter",
  "notes": "修复长文本滚动卡顿",
  "apk": {
    "file": "scrollprompter-v1.9.apk",
    "size": 6100000,
    "sha256": "……",
    "url": "https://github.com/shejitu/appshare/releases/download/scrollprompter-v1.9/scrollprompter-v1.9.apk",
    "mirrors": [
      "https://ghfast.top/https://github.com/shejitu/appshare/releases/download/scrollprompter-v1.9/scrollprompter-v1.9.apk"
    ]
  }
}
```

判断逻辑很简单：**`versionCode` 大于本机 versionCode 就是有更新**。
不要比 `versionName` 字符串（"1.10" < "1.9" 这种坑）。

- 字段说明见 [docs/version.schema.json](docs/version.schema.json)
- 安卓接入（可直接抄的 Kotlin 代码）见 [docs/接入指南.md](docs/接入指南.md)，成品代码在 [client/AndroidUpdater.kt](client/AndroidUpdater.kt)

---

## 全量索引

`apps.json` 列出所有 App 的最新版本，做应用市场式列表页时用得上：

```
https://cdn.jsdelivr.net/gh/shejitu/appshare@main/apps.json
```

---

## 发一个新版本

```bash
python tools/publish.py \
  --app-id scrollprompter --name 滚动提示器 \
  --version 1.9 --version-code 19 \
  --apk "E:/我原创的软件/提示器/滚动提示器_v1.9.apk" \
  --notes "修复长文本滚动卡顿"
```

一条命令会做完：建 Release → 传 APK → 写 `<appId>/version.json` → 更新 `apps.json` → 刷新上面的表格。
细节和参数见 [docs/发布流程.md](docs/发布流程.md)。

---

## 目录结构

```
appshare/
├─ apps.json                 # 全量索引（所有 App 的最新版本）
├─ <appId>/
│  └─ version.json           # 该 App 的最新版本信息，客户端拉的就是它
├─ docs/
│  ├─ 接入指南.md            # 安卓端检查更新 + 下载 + 安装
│  ├─ 发布流程.md            # 怎么发版、怎么强制更新、怎么回滚
│  └─ version.schema.json    # version.json 的字段定义
├─ client/
│  └─ AndroidUpdater.kt      # 无第三方依赖的现成 Updater
└─ tools/
   ├─ publish.py             # 发布安装包（一条命令）
   └─ sync.py                # 提交文档/工具改动（走 GitHub API，绕开 git push 的网络问题）
```

安装包本体不在仓库里，在 [Releases](https://github.com/shejitu/appshare/releases)。

---

## 注意

- 这里的东西**所有人可见**，只放确定要公开的成品；源码仍留在各自的私人仓库。
- 包名不变才能覆盖安装升级，改包名 = 用户会装出两个 App。
- 升级时 `versionCode` 必须递增，否则安卓会拒绝安装（`INSTALL_FAILED_VERSION_DOWNGRADE`）。
- 第三方镜像（ghfast / ghproxy / gh-proxy）是公益服务，随时可能失效，客户端务必做**顺序回退**。
