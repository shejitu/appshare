# appshare · 公开 App 分发仓库

私人仓库里做出来的 App，需要对外分享时，把**编译好的安装包**发布到这里。
别人装上 App 后，App 自己来这儿查版本：有新版本就直接下载覆盖安装。

- 元数据是静态 JSON，走 CDN，**不消耗 GitHub API 配额**，也不怕限流；
- 安装包放 GitHub Releases，不进 git 历史，仓库不会越滚越大；
- 每个版本都带 `sha256`，客户端下载后校验，防止被中间人替换。

---

## App 列表

<!-- APPS:START -->
| App | 标识 | 渠道 | 最新版本 | 大小 | 下载 |
| --- | --- | --- | --- | --- | --- |
| **慕思浏览器** | `musi` | 对外 | v1.10.01 (11001) | 2.85 MiB | [下载](https://github.com/shejitu/appshare/releases/download/musi-v1.10.01/musi-v1.10.01.apk) |
| **慕思浏览器** | `musi-beta` | 内测 | vA.10.0 (101000) | 2.85 MiB | [下载](https://github.com/shejitu/appshare/releases/download/musi-beta-vA.10.0/musi-beta-vA.10.0.apk) |
| **滚动提示器** | `scrollprompter` | 对外 | v1.8 (9) | 5.80 MiB | [下载](https://github.com/shejitu/appshare/releases/download/scrollprompter-v1.8/scrollprompter-v1.8.apk) |
| **予知影视** | `yuzhi` | 对外 | v1.0.0 (10000) | 30.68 MiB | [下载](https://github.com/shejitu/appshare/releases/download/yuzhi-v1.0.0/yuzhi-v1.0.0.apk) |
<!-- APPS:END -->

> 表格由 `tools/publish.py` 自动维护，不用手改。

### 体验期说明

慕思浏览器**对外版**（`musi`）是限期体验版：**有效期至 2026-12-31（北京时间）**。
到期后不能点播，会提示「体验版本到期，需要新版本请联系微信：shejitu」，点确定后自动退出。

内测通道（`musi-beta`）供本人验收使用，**不受时间限制**；客户不需要、也不应该拿这条线的包。

> 判断是否到期不只看手机本地时间：App 会取一次网络时间做基准，并用开机计时推进，
> 把系统日期往前调绕不过去。到期判定按北京时间算，改时区同样无效。

### 本版内容范围（1.4.0 起，两条线不同）

**对外版（`musi`）**按授权要求收窄了两块内容，**不是故障**：

- **直播只提供央视频道**（CCTV / CGTN / 央视 / 中央电视）。频道列表里不会再出现卫视、
  地方台、港澳台与境外台；换源后若该源没有央视频道，会直接提示「该源没有央视频道」。
- **「其他仓库」已关闭**：首页顶栏不再有「仓库」按钮，第二行的「📦 其他仓库」点下去只提示
  「限制内容」。豆瓣榜单、连续剧场、搜索、分类这些发现与点播入口不受影响。

**内测通道（`musi-beta`）内容全量**：直播保留源里的所有频道（含卫视、港澳台、国际新闻），
顶栏「仓库」与「其他仓库」二级菜单照旧可用，**也**没有时间限制。
两条线用同一份源码，靠版本号首字符区分（`1.` 对外 / `A.` 内测），不是两套代码。

> 为什么分开：内测是用来验功能的，内容被砍掉就验不出「源坏了」还是「限制生效」；
> 对外是交付物，范围按授权收窄。

### 升级流程（1.4.0 起）

点「⬆ 版本 → 下载升级」后：

1. 若还没开「安装未知应用」权限，先弹「先允许安装应用」，点**去开启**跳到系统设置；
2. 允许本应用后按返回，**会自动接着下载**，不用再点一次；
3. 下载落在 `/sdcard/Android/data/com.mytv.lite/files/download/update/`（FileProvider 白名单内，
   装的时候读得到），下完直接拉起系统安装器。

对外版的更新弹窗**只有「下载升级」和「以后再说」两个按钮**，不再提供「浏览器打开」
这类链接下载入口 —— 免得客户在浏览器里下到来路不明的包。

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

### 旧格式兼容（从 yuzhi-release / musi-release 迁过来的）

那两个老仓库当年用的是扁平格式 `{version, code, page, apk, size, notes}`。
为了不让老客户端改代码，每个 App 额外提供一份同格式文件：

```
https://raw.githubusercontent.com/shejitu/appshare/main/<appId>/legacy.json
```

字段一模一样，**客户端只改 URL，解析代码一行都不用动**。（`publish.py` 每次发版会自动更新它。）

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

## 版本号规则：1.x 对外，A.x 内测

两个通道，**版本号第一位就是身份标记**，一眼能看出这条包是给客户的还是自己调的：

| 通道 | appId | 版本号 | 用途 |
| --- | --- | --- | --- |
| 对外 | `yuzhi` / `musi` | **1**.0.0 起 | 发给客户的正式版 |
| 内测 | `yuzhi-beta` / `musi-beta` | **A**.0.0 起 | 自己验功能用，客户不会收到提示 |

> 内测前缀早期用过数字 **9**，已经作废。数字容易和正式版看混，现在统一用字母 **A**。

递增规则（两条线各自独立计算）：

| 改动程度 | 命令 | 版本变化 |
| --- | --- | --- |
| 小改动 / 修 bug | `--bump small` | 1.0.0 → 1.0.1；A.1.0 → A.1.1 |
| 中等改动 / 加功能 | `--bump medium` | 1.0.3 → 1.1.0；A.1.3 → A.2.0 |
| 大改版 / 重构 | `--bump major` | 1.4.7 → 2.0.0（内测线没有大版本，见下） |

`versionCode` 自动换算，**不要手填**：

```
versionCode = major × 10000 + minor × 100 + patch    （A 记作 major = 10）
    1.0.0 → 10000     1.0.1 → 10001     1.1.0 → 10100     2.0.0 → 20000
    A.0.0 → 100000    A.0.1 → 100001    A.1.0 → 100100
```

内测线的 `A` 只是渠道标记，不参与大版本递增 —— `--bump major` 在 A 线上会直接报错。
对外做大改版时，内测用 `--version A.<对外 minor>.0` 手工跟上即可（例如对外 2.0.0 → 内测 A.2.0）。

脚本会拦两种事故：

- **A.x 内测包想发进对外通道** → 直接中止（否则所有客户都会被提示升到测试版）；
- **APK 里真实打包的 versionName/versionCode 与 `--version` 不符** → 直接中止（否则客户端会「永远提示更新」或「永远收不到更新」）。

```bash
# 对外首发
python tools/publish.py --app-id yuzhi --name 予知影视 --version 1.0.0 --apk "YuzhiTV-1.0.0.apk"
# 内测首发（客户端读的是另一条线，客户完全看不到）
python tools/publish.py --app-id yuzhi-beta --name "予知影视（内测）" --version A.0.0 --apk "YuzhiTV-A.0.0.apk"
# 之后只管改动大小，版本号交给脚本
python tools/publish.py --app-id yuzhi --bump small --apk "..."
```

> **内测机切回对外版要先卸载**：A.1.0 的 versionCode 是 100100，大于对外 1.2.1 的 10201，
> 安卓不允许降级覆盖。这是「内测线 code 恒大于对外线」的必然代价，测试机要装公用包时先卸载旧版。
> 反过来「对外装完再装内测」是可以直接覆盖的。

---

## 目录结构

```
appshare/
├─ apps.json                 # 全量索引（所有 App 的最新版本）
├─ <appId>/
│  ├─ version.json           # 该 App 的最新版本信息，客户端拉的就是它
│  └─ legacy.json            # 旧扁平格式兼容（老客户端只改 URL 就能用）
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
