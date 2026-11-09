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

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.hardware.camera2.CameraCharacteristics
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.PowerManager
import android.text.InputType
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView

private const val PERMISSIONS = 1

/**
 * The address to post to, the camera, Start and Stop, and a button for a
 * single picture, sent too, to aim the phone and set the crop in Home
 * Assistant. The last picture is shown below.
 */
class MainActivity : Activity() {
    private lateinit var settings: Settings
    private lateinit var url: EditText
    private lateinit var camera: Spinner
    private lateinit var toggle: Button
    private lateinit var shoot: Button
    private lateinit var status: TextView
    private lateinit var picture: ImageView
    private lateinit var unlimited: Button
    private var lenses: List<Lens> = emptyList()
    private var thread: HandlerThread? = null
    @Volatile private var single: Shot? = null
    private var pending: (() -> Unit)? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        url = EditText(this).apply {
            hint = getString(R.string.url_hint)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
            setText(settings.url)
        }
        camera = Spinner(this)
        toggle = Button(this)
        shoot = Button(this).apply { text = getString(R.string.shoot) }
        status = TextView(this).apply { setPadding(0, pad, 0, pad) }
        picture = ImageView(this).apply { adjustViewBounds = true }
        column.addView(url, MATCH_PARENT, WRAP_CONTENT)
        column.addView(TextView(this).apply { text = getString(R.string.camera) })
        column.addView(camera, MATCH_PARENT, WRAP_CONTENT)
        column.addView(toggle, MATCH_PARENT, WRAP_CONTENT)
        column.addView(shoot, MATCH_PARENT, WRAP_CONTENT)
        column.addView(status, MATCH_PARENT, WRAP_CONTENT)
        column.addView(TextView(this).apply { text = getString(R.string.charger) })
        unlimited = Button(this).apply { text = getString(R.string.battery) }
        column.addView(unlimited, MATCH_PARENT, WRAP_CONTENT)
        column.addView(picture, MATCH_PARENT, WRAP_CONTENT)
        setContentView(ScrollView(this).apply { addView(column) })

        toggle.setOnClickListener { if (Status.running) stop() else withPermissions(::start) }
        shoot.setOnClickListener { withPermissions(::shootOnce) }
        unlimited.setOnClickListener { askUnlimited() }
        loadLenses()
    }

    override fun onResume() {
        super.onResume()
        Status.listen(::refresh)
        refresh()
    }

    override fun onPause() {
        Status.listen(null)
        save()
        super.onPause()
    }

    override fun onDestroy() {
        thread?.let { t ->
            Handler(t.looper).post { single?.cancel() }
            t.quitSafely()
        }
        thread = null
        super.onDestroy()
    }

    private fun loadLenses() {
        lenses = try {
            lenses(this)
        } catch (e: Exception) {
            emptyList()
        }
        val labels = lenses.map { lens ->
            val facing = when (lens.facing) {
                CameraCharacteristics.LENS_FACING_BACK -> getString(R.string.facing_back)
                CameraCharacteristics.LENS_FACING_FRONT -> getString(R.string.facing_front)
                else -> getString(R.string.facing_other)
            }
            val focus = lens.focusCm?.let { getString(R.string.focus_cm, it) }
                ?: getString(R.string.focus_fixed)
            getString(R.string.camera_entry, lens.id, facing, focus)
        }
        camera.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
        val chosen = lenses.indexOfFirst { it.id == settings.camera }
        val first = if (chosen >= 0) chosen else lenses.indexOfFirst {
            it.facing == CameraCharacteristics.LENS_FACING_BACK
        }.coerceAtLeast(0)
        if (lenses.isNotEmpty()) camera.setSelection(first)
        camera.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                settings.camera = lenses.getOrNull(position)?.id
            }

            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
    }

    private fun save(): Boolean {
        val text = url.text.toString().trim()
        settings.url = text
        return validUrl(text)
    }

    private fun refresh() {
        val running = Status.running
        toggle.text = getString(if (running) R.string.stop else R.string.start)
        shoot.isEnabled = !running && single == null
        url.isEnabled = !running
        camera.isEnabled = !running
        status.text = Status.text ?: getString(R.string.idle)
        val power = getSystemService(PowerManager::class.java)
        unlimited.visibility =
            if (power.isIgnoringBatteryOptimizations(packageName)) View.GONE else View.VISIBLE
        Status.picture?.let { jpeg ->
            val options = BitmapFactory.Options().apply { inSampleSize = 4 }
            picture.setImageBitmap(BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, options))
        }
    }

    /* Asks Android to leave the app out of battery optimisation, so it is not stopped. */
    @SuppressLint("BatteryLife")
    private fun askUnlimited() {
        startActivity(
            Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                .setData(Uri.parse("package:$packageName"))
        )
    }

    private fun withPermissions(action: () -> Unit) {
        if (!save()) {
            status.text = getString(R.string.bad_url)
            return
        }
        val asked = mutableListOf(Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            asked += Manifest.permission.POST_NOTIFICATIONS
        }
        val wanted = asked.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (wanted.isEmpty()) {
            action()
            return
        }
        pending = action
        requestPermissions(wanted.toTypedArray(), PERMISSIONS)
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<String>, results: IntArray) {
        super.onRequestPermissionsResult(code, permissions, results)
        val action = pending
        pending = null
        if (code != PERMISSIONS || action == null) return
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            action()
        } else {
            status.text = getString(R.string.denied)
        }
    }

    private fun start() {
        startForegroundService(Intent(this, CaptureService::class.java).setAction(CaptureService.ACTION_START))
    }

    private fun stop() {
        startService(Intent(this, CaptureService::class.java).setAction(CaptureService.ACTION_STOP))
    }

    private fun shootOnce() {
        val t = thread ?: HandlerThread("single").also {
            it.start()
            thread = it
        }
        val handler = Handler(t.looper)
        handler.post {
            single = shootAndSend(this, handler, true) {
                runOnUiThread {
                    single = null
                    refresh()
                }
            }
            runOnUiThread(::refresh)
        }
    }
}
