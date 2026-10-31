# 予知影视 · 更新分发仓

这个仓库**只做一件事**：给已安装的「予知影视」提供版本检测与安装包下载。

源码主仓 `shejitu/yuzhi-tv` 是**私有**的。

## 为什么单独建这个仓

GitHub 私有仓库的 `releases/latest` 对**匿名请求**一律返回 `404`。
App 端不可能内置 PAT —— 那等于把令牌打进安装包，任何人反编译都能取走。

GitHub 也没有"单文件公开"的能力，所以只能把**版本文件 + 安装包**镜像到这个公开仓。

## 文件说明

| 文件 | 作用 |
|---|---|
| `version.json` | 版本检测的唯一数据源，App 每次检测先读它 |
| Release 里的 `.apk` | 对应版本的安装包，供应用内直接下载安装 |

## version.json 格式

```json
{
  "version": "3.5.2",
  "code": 108,
  "page": "https://github.com/shejitu/yuzhi-release/releases/tag/v3.5.2",
  "apk": "https://github.com/shejitu/yuzhi-release/releases/download/v3.5.2/YuzhiTV-v3.5.2.apk",
  "size": 32226062,
  "notes": "更新说明"
}
```

App 端逻辑：先请求 `raw.githubusercontent.com/.../version.json`（不消耗 API 限额），
失败再退回 `api.github.com/repos/.../releases/latest`。
