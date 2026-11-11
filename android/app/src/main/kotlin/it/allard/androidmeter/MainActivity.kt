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
import android.app.AlertDialog
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
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
import java.net.IDN
import java.util.Locale

private const val PERMISSIONS = 1
private const val ASKED = "asked"

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
    private lateinit var crop: Button
    private var shownPicture: ByteArray? = null
    private var lenses: List<Lens> = emptyList()
    private var thread: HandlerThread? = null
    /*
     * The last single picture, on its own thread, where it is cancelled
     * with the activity; one already done ignores that. Whether one is
     * under way is Status.shooting, which the watch waits on.
     */
    private var single: Shot? = null
    /*
     * What to do once the permissions asked for are granted. Kept with the
     * screen, which Android may make anew while it asks.
     */
    private var pending: Asked? = null
    /*
     * Opened from the Resume notification: start once in front. Any app may
     * open this screen so, which only ever resumes a watch the user left on.
     */
    private var resuming = false

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
        val pasteButton = Button(this).apply { text = getString(R.string.paste) }
        column.addView(pasteButton, MATCH_PARENT, WRAP_CONTENT)
        column.addView(TextView(this).apply { text = getString(R.string.camera) })
        column.addView(camera, MATCH_PARENT, WRAP_CONTENT)
        column.addView(toggle, MATCH_PARENT, WRAP_CONTENT)
        column.addView(shoot, MATCH_PARENT, WRAP_CONTENT)
        crop = Button(this).apply { text = getString(R.string.crop_button) }
        column.addView(crop, MATCH_PARENT, WRAP_CONTENT)
        column.addView(status, MATCH_PARENT, WRAP_CONTENT)
        column.addView(TextView(this).apply { text = getString(R.string.charger) })
        unlimited = Button(this).apply { text = getString(R.string.battery) }
        column.addView(unlimited, MATCH_PARENT, WRAP_CONTENT)
        column.addView(picture, MATCH_PARENT, WRAP_CONTENT)
        setContentView(ScrollView(this).apply { addView(column) })

        toggle.setOnClickListener { if (Status.running) stop() else withPermissions(Asked.START) }
        shoot.setOnClickListener { withPermissions(Asked.SHOOT) }
        unlimited.setOnClickListener { askUnlimited() }
        pasteButton.setOnClickListener { paste() }
        crop.setOnClickListener { cropIt() }
        loadLenses()
        if (savedInstanceState == null) {
            fromIntent(intent)
        } else {
            pending = savedInstanceState.getString(ASKED)?.let { Asked.valueOf(it) }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        pending?.let { outState.putString(ASKED, it.name) }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        fromIntent(intent)
    }

    /*
     * The pairing QR code Home Assistant shows holds
     * androidmeter://setup?url=<webhook address>. It reaches the app as a
     * link opened, as text shared from the camera, or pasted. Any page or
     * app can hand such text over, so the address is only taken once the
     * user agrees to the host it names; the rest of it is the webhook's
     * secret and stays off the screen. Opened again from Recents, the screen
     * is handed the intent it was first opened with: that is not offered
     * again.
     */
    private fun fromIntent(intent: Intent?) {
        if (intent == null || intent.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY != 0) return
        when (intent.action) {
            Intent.ACTION_VIEW -> intent.dataString?.let { offer(it) }
            Intent.ACTION_SEND -> sharedText(intent)?.let { offer(it) }
            ACTION_RESUME -> resuming = true
        }
    }

    /*
     * Before Android 13 reading one extra unparcels them all: an object of
     * a class unknown here, shared by another app, would kill the app and
     * the watch with it.
     */
    private fun sharedText(intent: Intent): String? = try {
        intent.getStringExtra(Intent.EXTRA_TEXT)
    } catch (e: RuntimeException) {
        null
    }

    private fun paste() {
        val clip = getSystemService(ClipboardManager::class.java).primaryClip
        val text = clip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)
        offer(text?.toString() ?: "")
    }

    private fun offer(text: String) {
        val address = pairingAddress(text)
        val uri = address?.let { Uri.parse(it) }
        /* As the connection names it: letters that only look like others show as what they are. */
        val host = try {
            uri?.host?.let { IDN.toASCII(it).lowercase(Locale.ROOT) }
        } catch (e: IllegalArgumentException) {
            null
        }
        if (address == null || uri == null || host.isNullOrEmpty()) {
            Status.say(getString(R.string.link_bad))
            return
        }
        val port = uri.port
        AlertDialog.Builder(this)
            .setTitle(R.string.link_title)
            .setMessage(getString(R.string.link_text, if (port > 0) "$host:$port" else host))
            .setPositiveButton(R.string.link_use) { _, _ ->
                settings.url = address
                url.setText(address)
                Status.say(getString(R.string.link_set))
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    override fun onResume() {
        super.onResume()
        Orientation.start(this)
        Status.listen(::refresh)
        refresh()
        if (resuming) {
            resuming = false
            if (!Status.running && settings.wanted) withPermissions(Asked.START)
        }
    }

    override fun onPause() {
        Orientation.stop()
        Status.listen(null)
        save()
        super.onPause()
    }

    override fun onDestroy() {
        thread?.let { t ->
            /* Quit from the thread itself, as the capture service does. */
            Handler(t.looper).post {
                single?.cancel()
                t.quitSafely()
            }
        }
        thread = null
        super.onDestroy()
    }

    private fun loadLenses() {
        lenses = lenses(this)
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
        /* The watch waits for a picture taken by hand, not the other way round. */
        shoot.isEnabled = !running && !Status.shooting
        url.isEnabled = !running
        camera.isEnabled = !running
        status.text = Status.text ?: getString(R.string.idle)
        val power = getSystemService(PowerManager::class.java)
        unlimited.visibility =
            if (power.isIgnoringBatteryOptimizations(packageName)) View.GONE else View.VISIBLE
        val jpeg = Status.picture
        if (jpeg != null && jpeg !== shownPicture) {
            shownPicture = jpeg
            decodeUpright(jpeg)?.let { picture.setImageBitmap(it.bitmap) }
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

    private fun withPermissions(action: Asked) {
        if (!save()) {
            Status.say(getString(R.string.bad_url))
            return
        }
        val asked = CaptureService.needed.toMutableList()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            asked += Manifest.permission.POST_NOTIFICATIONS
        }
        val wanted = asked.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (wanted.isEmpty()) {
            act(action)
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
        if (CaptureService.allowed(this)) {
            act(action)
        } else {
            /* The watch cannot run without them: no more notices to resume it. */
            settings.wanted = false
            Status.say(getString(R.string.denied))
        }
    }

    private fun act(action: Asked) {
        when (action) {
            Asked.START -> start()
            Asked.SHOOT -> shootOnce()
        }
    }

    private fun start() {
        startForegroundService(Intent(this, CaptureService::class.java).setAction(CaptureService.ACTION_START))
    }

    private fun stop() {
        startService(Intent(this, CaptureService::class.java).setAction(CaptureService.ACTION_STOP))
    }

    /* The crop is drawn on the last picture, sent to Home Assistant. */
    private fun cropIt() {
        if (!save()) {
            Status.say(getString(R.string.bad_url))
            return
        }
        if (Status.picture == null) {
            Status.say(getString(R.string.no_picture))
            return
        }
        startActivity(Intent(this, CropActivity::class.java))
    }

    private fun shootOnce() {
        /* The watch starts on this thread too, so the two cannot cross. */
        if (Status.running || Status.shooting) return
        Status.shooting(true)
        refresh()
        val t = thread ?: HandlerThread("single").also {
            it.start()
            thread = it
        }
        val handler = Handler(t.looper)
        handler.post {
            single = shootAndSend(this, handler, true, { true }) { Status.shooting(false) }
        }
    }

    private enum class Asked { START, SHOOT }

    companion object {
        const val ACTION_RESUME = "it.allard.androidmeter.RESUME"
    }
}
