# Copyright (c) 2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Constants for android_meter."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "android_meter"

CONF_WEBHOOK_ID: Final = "webhook_id"

CONF_INTERVAL: Final = "interval"
CONF_TORCH: Final = "torch"
CONF_ROTATION: Final = "rotation"
CONF_X: Final = "x"
CONF_Y: Final = "y"
CONF_WIDTH: Final = "width"
CONF_HEIGHT: Final = "height"
CONF_GAP: Final = "gap"
CONF_DIGITS: Final = "digits"
CONF_DECIMALS: Final = "decimals"
CONF_MAX_FLOW: Final = "max_flow"

# Fields of the options form that are acted on, never stored.
CONF_READING: Final = "reading"
CONF_FORGET: Final = "forget"

DEFAULT_NAME: Final = "Gas meter"

# A G4 meter, the usual one in a house, is built for 6 m³/h at most.
DEFAULTS: Final[dict[str, int | float | bool]] = {
    CONF_INTERVAL: 60,
    CONF_TORCH: True,
    CONF_ROTATION: 0,
    CONF_X: 0,
    CONF_Y: 0,
    CONF_WIDTH: 0,
    CONF_HEIGHT: 0,
    CONF_GAP: 0,
    CONF_DIGITS: 8,
    CONF_DECIMALS: 3,
    CONF_MAX_FLOW: 6.0,
}

# The options that decide what a cell is: changing one makes the digits
# learnt so far worthless.
GEOMETRY: Final = (CONF_ROTATION, CONF_X, CONF_Y, CONF_WIDTH, CONF_HEIGHT, CONF_GAP, CONF_DIGITS)

ROTATIONS: Final = (0, 90, 180, 270)
MIN_INTERVAL: Final = 10
MAX_INTERVAL: Final = 3600
MAX_DIGITS: Final = 12
MAX_DECIMALS: Final = 6

# The app sends a JPEG of a few hundred kilobytes; this is far above it.
MAX_FRAME_BYTES: Final = 8 * 1024 * 1024

# Without a frame for this many intervals, and at least SILENCE_FLOOR
# seconds, the phone is taken for offline.
SILENCE_INTERVALS: Final = 3
SILENCE_FLOOR: Final = 300

STORAGE_VERSION: Final = 1

STATUS_WAITING: Final = "waiting"
STATUS_OFFLINE: Final = "offline"
STATUS_BAD_FRAME: Final = "bad_frame"
STATUS_NO_CROP: Final = "no_crop"
STATUS_BAD_CROP: Final = "bad_crop"
STATUS_UNTAUGHT: Final = "untaught"
STATUS_UNREADABLE: Final = "unreadable"
STATUS_IMPLAUSIBLE: Final = "implausible"
STATUS_OK: Final = "ok"

STATUSES: Final = (
    STATUS_WAITING,
    STATUS_OFFLINE,
    STATUS_BAD_FRAME,
    STATUS_NO_CROP,
    STATUS_BAD_CROP,
    STATUS_UNTAUGHT,
    STATUS_UNREADABLE,
    STATUS_IMPLAUSIBLE,
    STATUS_OK,
)
