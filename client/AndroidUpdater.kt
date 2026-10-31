/*
 * AndroidUpdater.kt —— appshare 客户端更新器（零第三方依赖）
 *
 * 用法：
 *   1. 把本文件拷进工程，改 BASE_URLS / 包名常量（可选）；
 *   2. AndroidManifest 里加权限与 FileProvider（见文件末尾注释）；
 *   3. res/xml/file_paths.xml 加一条 cache-path；
 *   4. 调用：
 *        AppUpdater.check(this, "scrollprompter") { info ->
 *            if (info == null) return@check          // 已是最新
 *            AppUpdater.download(this, info,
 *                onProgress = { done, total -> ... },
 *                onDone = { file -> AppUpdater.install(this, file) },
 *                onError = { e -> ... })
 *        }
 *
 * 全程用普通 Thread + 主线程回调，不依赖协程 / OkHttp，任何老工程都能编译。
 */

package com.shejitu.appshare

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** 服务端 version.json 的映射 */
data class UpdateInfo(
    val appId: String,
    val name: String,
    val versionName: String,
    val versionCode: Long,
    val minSdk: Int?,
    val mandatory: Boolean,
    val notes: String,
    val apkFile: String,
    val apkSize: Long,
    val sha256: String,
    val apkUrls: List<String>
)

object AppUpdater {

    /** 检查更新的地址模板，按顺序回退（%s = appId） */
    private val BASE_URLS = listOf(
        "https://raw.githubusercontent.com/shejitu/appshare/main/%s/version.json",
        "https://cdn.jsdelivr.net/gh/shejitu/appshare@main/%s/version.json",
        "https://ghfast.top/https://raw.githubusercontent.com/shejitu/appshare/main/%s/version.json"
    )

    private const val CONNECT_TIMEOUT = 12_000
    private const val READ_TIMEOUT = 20_000
    private val main = Handler(Looper.getMainLooper())

    /**
     * 检查更新。回调在主线程。
     * @param onResult 有新版本返回 UpdateInfo；已是最新返回 null；出错返回 error 非空。
     */
    fun check(
        context: Context,
        appId: String,
        currentVersionCode: Long = localVersionCode(context),
        onResult: (info: UpdateInfo?, error: Throwable?) -> Unit
    ) {
        Thread {
            var lastError: Throwable? = null
            for (tpl in BASE_URLS) {
                try {
                    val url = tpl.format(appId) + "?t=" + System.currentTimeMillis()
                    val body = httpGet(url, null)
                    val info = parse(body)
                    // 服务端标记了 minSdk 且本机不满足 → 不提示更新，避免装完崩
                    if (info.minSdk != null && Build.VERSION.SDK_INT < info.minSdk) {
                        post { onResult(null, null) }
                        return@Thread
                    }
                    post { onResult(if (info.versionCode > currentVersionCode) info else null, null) }
                    return@Thread
                } catch (e: Throwable) {
                    lastError = e
                }
            }
            post { onResult(null, lastError ?: IllegalStateException("所有更新地址均不可用")) }
        }.start()
    }

    /** 下载新版本。回调在主线程；进度为累计字节数。 */
    fun download(
        context: Context,
        info: UpdateInfo,
        onProgress: (done: Long, total: Long) -> Unit = { _, _ -> },
        onDone: (File) -> Unit,
        onError: (Throwable) -> Unit
    ) {
        Thread {
            var lastError: Throwable? = null
            for (url in info.apkUrls) {
                val tmp = File(dir(context), info.apkFile + ".part")
                try {
                    downloadTo(url, tmp, info.apkSize, onProgress)
                    val actual = sha256(tmp)
                    if (!actual.equals(info.sha256, ignoreCase = true)) {
                        tmp.delete()
                        lastError = IllegalStateException("sha256 校验失败，已丢弃（可能被篡改或下载不完整）")
                        continue
                    }
                    val target = File(dir(context), info.apkFile)
                    if (target.exists()) target.delete()
                    if (!tmp.renameTo(target)) { tmp.copyTo(target, true); tmp.delete() }
                    post { onDone(target) }
                    return@Thread
                } catch (e: Throwable) {
                    tmp.delete()
                    lastError = e
                }
            }
            val err = lastError ?: IllegalStateException("所有下载地址均失败")
            post { onError(err) }
        }.start()
    }

    /** 拉起系统安装器。Android 8+ 需先允许「安装未知应用」。 */
    fun install(context: Context, apk: File) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !context.packageManager.canRequestPackageInstalls()
        ) {
            // 引导用户去授权，授权后重新调用 install()
            val intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES)
                .setData(Uri.parse("package:" + context.packageName))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            return
        }
        val uri: Uri = FileProvider.getUriForFile(
            context, context.packageName + ".fileprovider", apk)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }

    // ---------------------------------------------------------------- 内部

    private fun post(block: () -> Unit) = main.post(block)

    private fun dir(context: Context): File =
        File(context.cacheDir, "updates").apply { mkdirs() }

    private fun localVersionCode(context: Context): Long {
        val pi = context.packageManager.getPackageInfo(context.packageName, 0)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) pi.longVersionCode
        else @Suppress("DEPRECATION") pi.versionCode.toLong()
    }

    private fun parse(body: String): UpdateInfo {
        val o = JSONObject(body)
        val apk = o.getJSONObject("apk")
        val urls = mutableListOf<String>()
        apk.optString("url").takeIf { it.isNotEmpty() }?.let { urls += it }
        apk.optJSONArray("mirrors")?.let { arr ->
            for (i in 0 until arr.length()) arr.optString(i).takeIf { it.isNotEmpty() }?.let { urls += it }
        }
        return UpdateInfo(
            appId = o.getString("appId"),
            name = o.optString("name"),
            versionName = o.optString("versionName"),
            versionCode = o.optLong("versionCode"),
            minSdk = if (o.isNull("minSdk")) null else o.optInt("minSdk"),
            mandatory = o.optBoolean("mandatory", false),
            notes = o.optString("notes"),
            apkFile = apk.getString("file"),
            apkSize = apk.optLong("size"),
            sha256 = apk.optString("sha256"),
            apkUrls = urls
        )
    }

    private fun httpGet(url: String, sink: File?): String {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = CONNECT_TIMEOUT
            readTimeout = READ_TIMEOUT
            instanceFollowRedirects = true
            setRequestProperty("User-Agent", "appshare-updater")
            setRequestProperty("Cache-Control", "no-cache")
        }
        try {
            val code = conn.responseCode
            if (code !in 200..299) throw IllegalStateException("HTTP $code @ $url")
            return if (sink == null) {
                conn.inputStream.bufferedReader().use { it.readText() }
            } else {
                conn.inputStream.use { it.copyTo(FileOutputStream(sink)) }
                ""
            }
        } finally {
            conn.disconnect()
        }
    }

    private fun downloadTo(url: String, dst: File, expectSize: Long, onProgress: (Long, Long) -> Unit) {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = CONNECT_TIMEOUT
            readTimeout = 30_000
            instanceFollowRedirects = true
            setRequestProperty("User-Agent", "appshare-updater")
        }
        try {
            val code = conn.responseCode
            if (code !in 200..299) throw IllegalStateException("HTTP $code @ $url")
            val total = if (conn.contentLength > 0) conn.contentLength.toLong() else expectSize
            var done = 0L
            conn.inputStream.use { input ->
                FileOutputStream(dst).use { out ->
                    val buf = ByteArray(64 * 1024)
                    var lastTick = 0L
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        val now = System.currentTimeMillis()
                        if (now - lastTick > 120) {          // 限流回调，避免刷爆 UI
                            lastTick = now
                            post { onProgress(done, total) }
                        }
                    }
                }
            }
            post { onProgress(done, total) }
            if (expectSize > 0 && dst.length() != expectSize) {
                throw IllegalStateException("大小不符：期望 $expectSize，实际 ${dst.length()}")
            }
        } finally {
            conn.disconnect()
        }
    }

    private fun sha256(file: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(1 shl 20)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}

/*
 * AndroidManifest.xml 需要：
 *
 *   <uses-permission android:name="android.permission.INTERNET" />
 *   <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />
 *
 *   <application ...>
 *       <provider
 *           android:name="androidx.core.content.FileProvider"
 *           android:authorities="${applicationId}.fileprovider"
 *           android:exported="false"
 *           android:grantUriPermissions="true">
 *           <meta-data
 *               android:name="android.support.FILE_PROVIDER_PATHS"
 *               android:resource="@xml/file_paths" />
 *       </provider>
 *   </application>
 *
 * res/xml/file_paths.xml：
 *
 *   <?xml version="1.0" encoding="utf-8"?>
 *   <paths>
 *       <cache-path name="updates" path="updates/" />
 *   </paths>
 *
 * 另外需要 androidx.core:core-ktx（FileProvider 来源）：
 *   implementation("androidx.core:core-ktx:1.12.0")
 * 不想引 androidx 的话，把 FileProvider 换成自定义 ContentProvider 也行。
 */
