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

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.MotionEvent
import android.view.View

/* The smallest side the box can be dragged to, in pixels of the picture. */
const val LEAST_SIDE = 8f

/**
 * The picture with the crop over it: a box moved by dragging its middle and
 * resized by dragging a corner, with each drum window drawn inside it. The
 * box is kept in pixels of the full picture.
 */
/* Built in code only, never from a layout file. */
@SuppressLint("ViewConstructor")
class CropView(context: Context, private val picture: Picture) : View(context) {
    val box = RectF()
    var digits = 8
    var gap = 0f

    private val density = resources.displayMetrics.density
    private val outline = Paint().apply {
        style = Paint.Style.STROKE
        color = Color.YELLOW
        strokeWidth = 2 * density
    }
    private val window = Paint(outline).apply { color = Color.GREEN }
    private val shade = Paint().apply { color = Color.argb(140, 0, 0, 0) }
    private val shown = RectF()
    private val onScreen = RectF()
    private val pane = RectF()
    private var scale = 1f
    private var grip = NONE
    private var lastX = 0f
    private var lastY = 0f

    override fun onDraw(canvas: Canvas) {
        scale = minOf(width.toFloat() / picture.width, height.toFloat() / picture.height)
        val left = (width - picture.width * scale) / 2
        val top = (height - picture.height * scale) / 2
        shown.set(left, top, left + picture.width * scale, top + picture.height * scale)
        canvas.drawBitmap(picture.bitmap, null, shown, null)
        val b = toView(box, onScreen)
        canvas.drawRect(shown.left, shown.top, shown.right, b.top, shade)
        canvas.drawRect(shown.left, b.bottom, shown.right, shown.bottom, shade)
        canvas.drawRect(shown.left, b.top, b.left, b.bottom, shade)
        canvas.drawRect(b.right, b.top, shown.right, b.bottom, shade)
        val across = (box.width() - (digits - 1) * gap) / digits
        if (across > 0) {
            for (i in 0 until digits) {
                val start = box.left + i * (across + gap)
                pane.set(start, box.top, start + across, box.bottom)
                canvas.drawRect(toView(pane, pane), window)
            }
        }
        canvas.drawRect(toView(box, onScreen), outline)
    }

    /* r, in pixels of the picture, as drawn on the screen; written to out. */
    private fun toView(r: RectF, out: RectF): RectF {
        out.set(
            shown.left + r.left * scale,
            shown.top + r.top * scale,
            shown.left + r.right * scale,
            shown.top + r.bottom * scale,
        )
        return out
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        val x = (event.x - shown.left) / scale
        val y = (event.y - shown.top) / scale
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                grip = gripAt(x, y)
                lastX = x
                lastY = y
                return grip != NONE
            }
            MotionEvent.ACTION_MOVE -> {
                if (grip == NONE) return false
                drag(x - lastX, y - lastY)
                lastX = x
                lastY = y
                invalidate()
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> grip = NONE
        }
        return true
    }

    private fun gripAt(x: Float, y: Float): Int {
        val reach = 32 * density / scale
        fun near(px: Float, py: Float) = Math.abs(x - px) < reach && Math.abs(y - py) < reach
        return when {
            near(box.left, box.top) -> TOP_LEFT
            near(box.right, box.top) -> TOP_RIGHT
            near(box.left, box.bottom) -> BOTTOM_LEFT
            near(box.right, box.bottom) -> BOTTOM_RIGHT
            box.contains(x, y) -> MOVE
            else -> NONE
        }
    }

    private fun drag(dx: Float, dy: Float) {
        val least = LEAST_SIDE
        val w = picture.width.toFloat()
        val h = picture.height.toFloat()
        when (grip) {
            MOVE -> {
                val mx = dx.coerceIn(-box.left, w - box.right)
                val my = dy.coerceIn(-box.top, h - box.bottom)
                box.offset(mx, my)
            }
            TOP_LEFT -> {
                box.left = (box.left + dx).coerceIn(0f, box.right - least)
                box.top = (box.top + dy).coerceIn(0f, box.bottom - least)
            }
            TOP_RIGHT -> {
                box.right = (box.right + dx).coerceIn(box.left + least, w)
                box.top = (box.top + dy).coerceIn(0f, box.bottom - least)
            }
            BOTTOM_LEFT -> {
                box.left = (box.left + dx).coerceIn(0f, box.right - least)
                box.bottom = (box.bottom + dy).coerceIn(box.top + least, h)
            }
            BOTTOM_RIGHT -> {
                box.right = (box.right + dx).coerceIn(box.left + least, w)
                box.bottom = (box.bottom + dy).coerceIn(box.top + least, h)
            }
        }
    }

    private companion object {
        const val NONE = 0
        const val MOVE = 1
        const val TOP_LEFT = 2
        const val TOP_RIGHT = 3
        const val BOTTOM_LEFT = 4
        const val BOTTOM_RIGHT = 5
    }
}
