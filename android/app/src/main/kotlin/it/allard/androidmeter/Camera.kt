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
import android.graphics.ImageFormat
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraAccessException
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.ImageReader
import android.os.Handler
import android.util.Size
import android.view.Surface
import java.io.IOException
import java.util.concurrent.CancellationException
import java.util.concurrent.Executor

/* A JPEG at least this long on its long side, the smallest such. */
private const val WANTED_SIDE = 1600

/* Frames given to exposure and focus before a picture is taken anyway. */
private const val MIN_FRAMES = 10
private const val MAX_FRAMES = 60

private const val TIMEOUT_MS = 15_000L

/*
 * Camera numbers tried beyond Android's list. Some phones, Qualcomm based
 * ones such as Motorola's, leave their extra back cameras out of it, the
 * wide one that focuses closest among them, though any app can open them.
 */
private const val PROBED_IDS = 16

/** One camera as offered to the user. */
data class Lens(val id: String, val facing: Int?, val focusCm: Int?)

fun lenses(context: Context): List<Lens> {
    val manager = context.getSystemService(CameraManager::class.java)
    val listed = manager.cameraIdList.toList()
    val hidden = (0 until PROBED_IDS).map { it.toString() }.filter { it !in listed }
    return (listed + hidden).mapNotNull { id ->
        val chars = try {
            manager.getCameraCharacteristics(id)
        } catch (e: IllegalArgumentException) {
            return@mapNotNull null
        } catch (e: CameraAccessException) {
            return@mapNotNull null
        }
        if (id !in listed && !single(chars)) return@mapNotNull null
        val diopters = chars.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE)
        val focus = if (diopters == null || diopters <= 0f) null else Math.round(100f / diopters)
        Lens(id, chars.get(CameraCharacteristics.LENS_FACING), focus)
    }
}

/* An ordinary camera of its own, not several shown as one. */
private fun single(chars: CameraCharacteristics): Boolean {
    val caps = chars.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: return false
    return CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_BACKWARD_COMPATIBLE in caps &&
        CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_LOGICAL_MULTI_CAMERA !in caps
}

/**
 * Takes one picture: opens the camera, lights the torch if asked, lets
 * exposure and focus settle on a throwaway preview, takes a JPEG and
 * closes everything again. done runs once, on the handler's thread.
 */
class Shot(
    context: Context,
    private val cameraId: String,
    private val torch: Boolean,
    private val handler: Handler,
    private val done: (Result<ByteArray>) -> Unit,
) {
    private val manager = context.getSystemService(CameraManager::class.java)
    private val executor = Executor { handler.post(it) }
    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var reader: ImageReader? = null
    private var texture: SurfaceTexture? = null
    private var preview: Surface? = null
    private var orientation = 0
    private var frames = 0
    private var taken = false
    private var finished = false
    private val timeout = Runnable { finish(Result.failure(IOException("timed out"))) }

    @SuppressLint("MissingPermission")
    fun start() {
        handler.postDelayed(timeout, TIMEOUT_MS)
        try {
            val chars = manager.getCameraCharacteristics(cameraId)
            val size = jpegSize(chars)
            orientation = jpegOrientation(chars)
            val reader = ImageReader.newInstance(size.width, size.height, ImageFormat.JPEG, 1)
            this.reader = reader
            reader.setOnImageAvailableListener({ r ->
                val image = r.acquireLatestImage() ?: return@setOnImageAvailableListener
                val bytes = image.use {
                    val buffer = it.planes[0].buffer
                    ByteArray(buffer.remaining()).also { out -> buffer.get(out) }
                }
                finish(Result.success(bytes))
            }, handler)
            val texture = SurfaceTexture(false)
            texture.setDefaultBufferSize(640, 480)
            this.texture = texture
            preview = Surface(texture)
            manager.openCamera(cameraId, executor, opened)
        } catch (e: Exception) {
            finish(Result.failure(e))
        }
    }

    /*
     * The turn that makes the picture upright for the way the phone is held,
     * as the camera documentation computes it: the sensor is mounted at an
     * angle to the phone, and a front camera mirrors the turn.
     */
    private fun jpegOrientation(chars: CameraCharacteristics): Int {
        val sensor = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
        val held = Orientation.degrees
        val front = chars.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_FRONT
        return ((if (front) sensor - held else sensor + held) % 360 + 360) % 360
    }

    /*
     * The smallest JPEG long enough, of the sensor's own shape where there
     * is one: a wide size is a crop and loses the height the counter may
     * need.
     */
    private fun jpegSize(chars: CameraCharacteristics): Size {
        val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            ?: throw IOException("no stream configuration")
        val sizes = map.getOutputSizes(ImageFormat.JPEG)
        if (sizes.isNullOrEmpty()) throw IOException("no JPEG size")
        val large = sizes.filter { maxOf(it.width, it.height) >= WANTED_SIDE }
        val array = chars.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE)
        val full = if (array == null || array.height() == 0) emptyList() else {
            val shape = array.width().toFloat() / array.height()
            large.filter { Math.abs(it.width.toFloat() / it.height - shape) < 0.02f }
        }
        return (full.ifEmpty { large }).minByOrNull { it.width.toLong() * it.height }
            ?: sizes.maxBy { it.width.toLong() * it.height }
    }

    private val opened = object : CameraDevice.StateCallback() {
        override fun onOpened(camera: CameraDevice) {
            device = camera
            if (finished) {
                camera.close()
                return
            }
            try {
                val outputs = listOf(
                    OutputConfiguration(preview!!),
                    OutputConfiguration(reader!!.surface),
                )
                camera.createCaptureSession(
                    SessionConfiguration(SessionConfiguration.SESSION_REGULAR, outputs, executor, configured)
                )
            } catch (e: Exception) {
                finish(Result.failure(e))
            }
        }

        override fun onDisconnected(camera: CameraDevice) {
            finish(Result.failure(IOException("camera disconnected")))
        }

        override fun onError(camera: CameraDevice, error: Int) {
            finish(Result.failure(IOException("camera error $error")))
        }
    }

    private val configured = object : CameraCaptureSession.StateCallback() {
        override fun onConfigured(s: CameraCaptureSession) {
            session = s
            if (finished) return
            try {
                val request = request(CameraDevice.TEMPLATE_PREVIEW, preview!!)
                s.setRepeatingRequest(request.build(), settling, handler)
            } catch (e: Exception) {
                finish(Result.failure(e))
            }
        }

        override fun onConfigureFailed(s: CameraCaptureSession) {
            finish(Result.failure(IOException("capture session refused")))
        }
    }

    private fun request(template: Int, target: Surface): CaptureRequest.Builder {
        val builder = device!!.createCaptureRequest(template)
        builder.addTarget(target)
        builder.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
        builder.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
        builder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
        builder.set(
            CaptureRequest.FLASH_MODE,
            if (torch) CaptureRequest.FLASH_MODE_TORCH else CaptureRequest.FLASH_MODE_OFF,
        )
        return builder
    }

    /* Waits for exposure and focus, then asks for the picture once. */
    private val settling = object : CameraCaptureSession.CaptureCallback() {
        override fun onCaptureCompleted(
            s: CameraCaptureSession,
            request: CaptureRequest,
            result: TotalCaptureResult,
        ) {
            if (taken || finished) return
            frames++
            if (frames < MIN_FRAMES) return
            if (frames < MAX_FRAMES && !(exposed(result) && focused(result))) return
            taken = true
            try {
                val still = request(CameraDevice.TEMPLATE_STILL_CAPTURE, reader!!.surface)
                still.set(CaptureRequest.JPEG_QUALITY, 90.toByte())
                still.set(CaptureRequest.JPEG_ORIENTATION, orientation)
                s.capture(still.build(), null, handler)
            } catch (e: Exception) {
                finish(Result.failure(e))
            }
        }
    }

    private fun exposed(result: CaptureResult): Boolean =
        when (result.get(CaptureResult.CONTROL_AE_STATE)) {
            null,
            CaptureResult.CONTROL_AE_STATE_CONVERGED,
            CaptureResult.CONTROL_AE_STATE_FLASH_REQUIRED,
            CaptureResult.CONTROL_AE_STATE_LOCKED -> true
            else -> false
        }

    private fun focused(result: CaptureResult): Boolean =
        when (result.get(CaptureResult.CONTROL_AF_STATE)) {
            null,
            CaptureResult.CONTROL_AF_STATE_INACTIVE,
            CaptureResult.CONTROL_AF_STATE_PASSIVE_FOCUSED,
            CaptureResult.CONTROL_AF_STATE_FOCUSED_LOCKED,
            CaptureResult.CONTROL_AF_STATE_NOT_FOCUSED_LOCKED -> true
            else -> false
        }

    /** Gives up on the picture and closes the camera. Call on the handler's thread. */
    fun cancel() {
        finish(Result.failure(CancellationException("cancelled")))
    }

    private fun finish(result: Result<ByteArray>) {
        if (finished) return
        finished = true
        handler.removeCallbacks(timeout)
        try {
            session?.close()
        } catch (_: Exception) {
        }
        device?.close()
        reader?.close()
        preview?.release()
        texture?.release()
        session = null
        device = null
        reader = null
        preview = null
        texture = null
        done(result)
    }
}
