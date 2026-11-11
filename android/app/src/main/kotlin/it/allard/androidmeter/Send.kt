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
import java.text.DateFormat
import java.util.Date
import java.util.concurrent.CancellationException

/**
 * Takes a picture and posts it, on the handler's thread, then calls then
 * once with the seconds to wait before the next one, also when the shot
 * returned is cancelled. once only changes the wording, for a picture
 * taken by hand. Once live turns false, as the watch stops, nothing more
 * is said of the picture, which would cover what came since.
 */
fun shootAndSend(
    context: Context,
    handler: Handler,
    once: Boolean,
    live: () -> Boolean,
    then: (Int) -> Unit,
): Shot? {
    fun say(text: String) {
        if (live()) Status.say(text)
    }
    val settings = Settings(context)
    val camera = settings.camera ?: lenses(context).firstOrNull()?.id
    if (camera == null) {
        say(context.getString(R.string.no_camera))
        then(settings.interval)
        return null
    }
    say(context.getString(R.string.shooting))
    val shot = Shot(context, camera, settings.torch, handler) { result ->
        val now = DateFormat.getTimeInstance(DateFormat.SHORT).format(Date())
        val jpeg = result.getOrElse {
            say(
                if (it is CancellationException) context.getString(R.string.cancelled)
                else context.getString(R.string.camera_failed, it.message ?: it.javaClass.simpleName)
            )
            then(settings.interval)
            return@Shot
        }
        Status.picture(jpeg)
        val interval = try {
            val reply = upload(settings.url, jpeg)
            settings.interval = reply.interval
            settings.torch = reply.torch
            reply.crop?.let { settings.crop = it }
            say(
                if (once) context.getString(R.string.sent_once, now, reply.status)
                else context.getString(R.string.sent, now, reply.status, reply.interval)
            )
            reply.interval
        } catch (e: Exception) {
            val why = e.message ?: e.javaClass.simpleName
            say(
                if (once) context.getString(R.string.send_failed_once, now, why)
                else context.getString(R.string.send_failed, now, why, settings.interval)
            )
            settings.interval
        }
        then(interval)
    }
    shot.start()
    return shot
}
