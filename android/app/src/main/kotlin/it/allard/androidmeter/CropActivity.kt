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

import android.app.Activity
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONException
import org.json.JSONObject
import kotlin.math.roundToInt

/**
 * Draw the crop on the last picture: the box over the drum windows, the
 * number of drums, decimal drums and the gap between windows. Save sends it
 * to Home Assistant, which applies it unless the crop is locked there.
 */
class CropActivity : Activity() {
    private lateinit var settings: Settings
    private lateinit var view: CropView
    private lateinit var status: TextView
    private lateinit var save: Button
    private var decimals = 3
    private var step = 1f

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)
        val picture = Status.picture?.let { decodeUpright(it) }
        if (picture == null) {
            Status.say(getString(R.string.no_picture))
            finish()
            return
        }
        view = CropView(this, picture)
        step = maxOf(1f, picture.width / 400f)
        start(picture)

        val pad = (12 * resources.displayMetrics.density).toInt()
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        status = TextView(this).apply { text = getString(R.string.crop_hint) }
        save = Button(this).apply { text = getString(R.string.crop_save) }
        column.addView(status, MATCH_PARENT, WRAP_CONTENT)
        column.addView(view, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        column.addView(stepper(R.string.drums, { view.digits.toString() }) { d ->
            view.digits = (view.digits + d).coerceIn(1, 12)
            decimals = decimals.coerceAtMost(view.digits)
        })
        column.addView(stepper(R.string.decimals, { decimals.toString() }) { d ->
            decimals = (decimals + d).coerceIn(0, minOf(6, view.digits))
        })
        column.addView(stepper(R.string.gap, { view.gap.roundToInt().toString() }) { d ->
            view.gap = (view.gap + d * step).coerceIn(0f, view.box.width() / view.digits)
        })
        column.addView(save, MATCH_PARENT, WRAP_CONTENT)
        setContentView(column)
        title = getString(R.string.crop_title)
        save.setOnClickListener { send() }
    }

    /* The crop Home Assistant reported, or a box across the middle. */
    private fun start(picture: Picture) {
        val known = try {
            settings.crop?.let { JSONObject(it) }
        } catch (e: JSONException) {
            null
        }
        val w = picture.width.toFloat()
        val h = picture.height.toFloat()
        val width = known?.optInt("width") ?: 0
        val height = known?.optInt("height") ?: 0
        if (known != null && width > 0 && height > 0 && known.optInt("rotation") == 0) {
            val x = known.optInt("x").toFloat()
            val y = known.optInt("y").toFloat()
            view.box.set(x, y, minOf(w, x + width), minOf(h, y + height))
            view.digits = known.optInt("digits", 8).coerceIn(1, 12)
            decimals = known.optInt("decimals", 3).coerceIn(0, view.digits)
            view.gap = known.optInt("gap").toFloat()
        } else {
            view.box.set(w * 0.15f, h * 0.4f, w * 0.85f, h * 0.55f)
        }
    }

    private fun stepper(label: Int, value: () -> String, change: (Int) -> Unit): LinearLayout {
        val row = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        val shown = TextView(this).apply {
            text = value()
            gravity = Gravity.CENTER
            minEms = 3
        }
        fun button(text: String, d: Int) = Button(this).apply {
            this.text = text
            setOnClickListener {
                change(d)
                shown.text = value()
                view.invalidate()
            }
        }
        row.addView(TextView(this).apply { setText(label) }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        row.addView(button("−", -1))
        row.addView(shown)
        row.addView(button("+", 1))
        return row
    }

    private fun send() {
        val box = view.box
        val crop = JSONObject()
            .put("rotation", 0)
            .put("x", box.left.roundToInt())
            .put("y", box.top.roundToInt())
            .put("width", box.width().roundToInt())
            .put("height", box.height().roundToInt())
            .put("gap", view.gap.roundToInt())
            .put("digits", view.digits)
            .put("decimals", decimals)
        val url = settings.url
        save.isEnabled = false
        Thread {
            val text = try {
                val reply = sendCrop(url, crop)
                reply.crop?.let { settings.crop = it }
                when (reply.error) {
                    null -> getString(R.string.crop_saved)
                    "crop_locked" -> getString(R.string.crop_locked)
                    else -> getString(R.string.crop_refused, reply.error)
                }
            } catch (e: Exception) {
                getString(R.string.crop_failed, e.message ?: e.javaClass.simpleName)
            }
            runOnUiThread {
                status.text = text
                save.isEnabled = true
            }
        }.start()
    }
}
