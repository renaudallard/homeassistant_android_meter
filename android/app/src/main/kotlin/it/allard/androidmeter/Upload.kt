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

/** What Home Assistant answers a frame with; crop is the crop in force, as sent. */
data class Reply(val interval: Int, val torch: Boolean, val status: String, val crop: String?)

fun validUrl(url: String): Boolean =
    url.startsWith("http://") || url.startsWith("https://")

/**
 * The webhook address in what the pairing QR code gave: the
 * androidmeter://setup?url=... link it holds, or a plain webhook address,
 * with whatever text a camera or a clipboard put around it. Null when
 * there is none.
 */
fun pairingAddress(text: String): String? {
    for (word in text.split(Regex("\\s+"))) {
        val address = when {
            word.startsWith("androidmeter://") -> android.net.Uri.parse(word).getQueryParameter("url")
            word.startsWith("http://") || word.startsWith("https://") -> word
            else -> null
        }?.trim() ?: continue
        if (validUrl(address) && address.contains("/api/webhook/")) return address
    }
    return null
}

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

/** What Home Assistant answers a crop with: its error, or null, and the crop in force. */
data class CropReply(val error: String?, val crop: String?)

/** Sends a crop drawn on the phone. Blocks, like upload. */
fun sendCrop(url: String, crop: JSONObject): CropReply {
    val connection = URL(url).openConnection() as HttpURLConnection
    try {
        val body = JSONObject().put("crop", crop).toString().toByteArray(Charsets.UTF_8)
        connection.requestMethod = "POST"
        connection.instanceFollowRedirects = false
        connection.connectTimeout = 15_000
        connection.readTimeout = 30_000
        connection.doOutput = true
        connection.setFixedLengthStreamingMode(body.size)
        connection.setRequestProperty("Content-Type", "application/json")
        connection.outputStream.use { it.write(body) }
        val code = connection.responseCode
        if (code != HttpURLConnection.HTTP_OK) throw IOException("HTTP $code")
        val json = JSONObject(connection.inputStream.use { read(it) })
        val error = if (json.isNull("error")) null else json.optString("error").take(32)
        return CropReply(error, json.optJSONObject("crop")?.toString())
    } catch (e: JSONException) {
        throw IOException("unexpected answer")
    } finally {
        connection.disconnect()
    }
}

fun parse(body: String): Reply {
    try {
        val json = JSONObject(body)
        val interval = json.getInt("interval").coerceIn(MIN_INTERVAL, MAX_INTERVAL)
        val status = json.optString("status", "?").take(32)
        val crop = json.optJSONObject("crop")?.toString()
        return Reply(interval, json.getBoolean("torch"), status, crop)
    } catch (e: JSONException) {
        throw IOException("unexpected answer")
    }
}
