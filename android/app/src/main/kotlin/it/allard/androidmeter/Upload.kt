/*
 * Copyright (c) 2026, Renaud Allard <renaud@allard.it>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the documentation
 *    and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

package it.allard.androidmeter

import org.json.JSONException
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL

const val MIN_INTERVAL = 10
const val MAX_INTERVAL = 3600

private const val MAX_REPLY = 64 * 1024

/** What Home Assistant answers a frame with. */
data class Reply(val interval: Int, val torch: Boolean, val status: String)

fun validUrl(url: String): Boolean =
    url.startsWith("http://") || url.startsWith("https://")

/**
 * Posts a JPEG to the webhook and reads the answer. Blocks, so it never
 * runs on the main thread. Redirects are not followed: the address holds
 * the webhook's secret and must not be handed to another host.
 */
fun upload(url: String, jpeg: ByteArray): Reply {
    val connection = URL(url).openConnection() as HttpURLConnection
    try {
        connection.requestMethod = "POST"
        connection.instanceFollowRedirects = false
        connection.connectTimeout = 15_000
        connection.readTimeout = 30_000
        connection.doOutput = true
        connection.setFixedLengthStreamingMode(jpeg.size)
        connection.setRequestProperty("Content-Type", "image/jpeg")
        connection.outputStream.use { it.write(jpeg) }
        val code = connection.responseCode
        if (code != HttpURLConnection.HTTP_OK) throw IOException("HTTP $code")
        val body = connection.inputStream.use { read(it) }
        return parse(body)
    } finally {
        connection.disconnect()
    }
}

private fun read(input: InputStream): String {
    val out = ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    while (true) {
        val n = input.read(buffer)
        if (n < 0) break
        if (out.size() + n > MAX_REPLY) throw IOException("answer too long")
        out.write(buffer, 0, n)
    }
    return out.toString(Charsets.UTF_8.name())
}

fun parse(body: String): Reply {
    try {
        val json = JSONObject(body)
        val interval = json.getInt("interval").coerceIn(MIN_INTERVAL, MAX_INTERVAL)
        val status = json.optString("status", "?").take(32)
        return Reply(interval, json.getBoolean("torch"), status)
    } catch (e: JSONException) {
        throw IOException("unexpected answer")
    }
}
