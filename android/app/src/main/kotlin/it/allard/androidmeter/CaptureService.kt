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

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.PowerManager

/**
 * Takes a picture at the interval Home Assistant asks for, until stopped.
 *
 * It holds a partial wake lock from one picture to the next: the handler
 * counts uptime, which stops while the processor sleeps. The lock times
 * out a little after the next picture is due, so a service that died
 * does not keep the phone awake. The phone is meant to stay on its
 * charger.
 *
 * Android lets a camera service start only from something the user did:
 * the Start button, or a tap on the notification Resume posts when the
 * service was stopped by a reboot, an update or the system itself.
 */
class CaptureService : Service() {
    private lateinit var thread: HandlerThread
    private lateinit var handler: Handler
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var running = false
    private var shot: Shot? = null

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            /* Held until the next picture, and a margin, should this one never end. */
            wakeLock?.acquire((Settings(this@CaptureService).interval + WAKE_MARGIN) * 1000L)
            shot = shootAndSend(this@CaptureService, handler, false) { seconds ->
                shot = null
                if (running) {
                    wakeLock?.acquire((seconds + WAKE_MARGIN) * 1000L)
                    handler.postDelayed(this, seconds * 1000L)
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        thread = HandlerThread("capture")
        thread.start()
        handler = Handler(thread.looper)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val settings = Settings(this)
        when (intent?.action) {
            ACTION_STOP -> {
                settings.wanted = false
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_START -> settings.wanted = true
            else -> {
                /*
                 * Restarted by the system after it killed the process. A
                 * service started from the background may not use the
                 * camera, so ask the user to tap instead.
                 */
                if (running) return START_STICKY
                if (settings.wanted) Resume.offer(this)
                stopSelf()
                return START_NOT_STICKY
            }
        }
        if (running) return START_STICKY
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                startForeground(NOTIFICATION, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA)
            } else {
                startForeground(NOTIFICATION, notification())
            }
        } catch (e: Exception) {
            /* Started from the background, where the camera may not be used. */
            Resume.offer(this)
            stopSelf()
            return START_NOT_STICKY
        }
        Resume.clear(this)
        val power = getSystemService(PowerManager::class.java)
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "AndroidMeter:capture").also {
            it.setReferenceCounted(false)
        }
        running = true
        Status.running(true)
        handler.post(tick)
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        handler.removeCallbacksAndMessages(null)
        handler.post { shot?.cancel() }
        thread.quitSafely()
        wakeLock?.release()
        wakeLock = null
        Status.running(false)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun notification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, getString(R.string.channel), NotificationManager.IMPORTANCE_LOW)
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.watching))
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    companion object {
        const val ACTION_START = "it.allard.androidmeter.START"
        const val ACTION_STOP = "it.allard.androidmeter.STOP"
        private const val CHANNEL = "capture"
        private const val NOTIFICATION = 1
        private const val WAKE_MARGIN = 120
    }
}
