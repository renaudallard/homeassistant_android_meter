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

import android.content.Context
import android.os.Handler
import android.os.Looper

/** The settings the user types in, kept across restarts. */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var url: String
        get() = prefs.getString("url", "") ?: ""
        set(value) = prefs.edit().putString("url", value).apply()

    var camera: String?
        get() = prefs.getString("camera", null)
        set(value) = prefs.edit().putString("camera", value).apply()

    /* Whether the user started the watch and has not stopped it. */
    var wanted: Boolean
        get() = prefs.getBoolean("wanted", false)
        set(value) = prefs.edit().putBoolean("wanted", value).apply()

    /* What Home Assistant last asked for, used until it answers again. */
    var interval: Int
        get() = prefs.getInt("interval", 60)
        set(value) = prefs.edit().putInt("interval", value).apply()

    var torch: Boolean
        get() = prefs.getBoolean("torch", true)
        set(value) = prefs.edit().putBoolean("torch", value).apply()
}

/**
 * What the screen shows: whether the service runs, the last line of
 * news and the last picture. Written from any thread, shown on the main
 * one.
 */
object Status {
    @Volatile var running = false
        private set
    @Volatile var text: String? = null
        private set
    @Volatile var picture: ByteArray? = null
        private set

    private val main = Handler(Looper.getMainLooper())
    private var listener: (() -> Unit)? = null

    fun listen(update: (() -> Unit)?) {
        main.post { listener = update }
    }

    fun running(value: Boolean) {
        running = value
        changed()
    }

    fun say(value: String) {
        text = value
        changed()
    }

    fun picture(value: ByteArray) {
        picture = value
        changed()
    }

    private fun changed() {
        main.post { listener?.invoke() }
    }
}
