# Android Meter

Read a mechanical gas meter with an old Android phone stuck to it, and feed
the reading to Home Assistant's Energy dashboard.

The phone sits on the meter in a magnetic holder and takes a picture of the
counter every minute or so, torch on. It posts each picture to Home
Assistant, which reads the drums itself and keeps a gas sensor in m³. No
cloud, no OCR service, nothing pre-trained: the integration learns the
digits of your meter from a reading you type in once.

Two parts live in this repository:

- `custom_components/android_meter`, the Home Assistant integration
  (Home Assistant 2026.11.0 or later);
- `android`, the Android Meter app for the phone (Android 9 or later).

## How it works

1. The app takes a picture, posts the JPEG to the integration's webhook,
   and waits for the interval the answer gives it.
2. The integration turns the picture upright, crops the counter, cuts it
   into one window per drum, and turns each window into a small black and
   white mask of its digit.
3. A drum at rest is compared with the masks learnt for each digit. A drum
   caught between two digits, a blank one or one that looks like no known
   digit is left out.
4. The reading is not taken drum by drum but chosen among the values the
   meter can have reached since the last frame, at most the largest flow
   the meter is built for. Of the values that fit the frame equally well,
   the lowest is taken. A doubt therefore delays the reading, it never puts
   it ahead of the meter.
5. The same choice labels new masks. After the first reading is typed in,
   the digits the meter shows as it turns are learnt on their own.

A frame that shows a value the meter cannot have reached, for instance
lower than the reading, is refused and the status says so. That is how a
wrong reading shows up: type the right one in the options.

## Installation

### Integration

Search for **Android Meter** in Settings > Marketplace (or HACS) and
install it. If it is not listed, add this repository as a custom
integration repository first:
`https://github.com/renaudallard/homeassistant_android_meter`.

To install by hand, unpack `android_meter.zip` from the latest release into
`config/custom_components/android_meter/` and restart Home Assistant.

### App

Build it with `./gradlew assembleRelease` in `android/` (JDK 21, Android
SDK platform 37), or take the APK from the Android workflow's artifacts,
and install it on the phone.

## Setting up

1. **Mount the phone** on the meter with the back camera over the counter,
   close enough for the drums to fill a good part of the picture but no
   closer than the camera can focus. Keep it on its charger.
2. **Add the integration**: Settings > Devices & services > Add integration
   > Android Meter. Give the meter a name. The next screen shows the webhook
   address, for example `http://192.168.1.10:8123/api/webhook/...`.
3. **In the app**, enter that address, pick the camera (the list shows how
   close each one focuses) and press **Take one picture**. The picture is
   shown in the app and sent to Home Assistant.
4. **In Home Assistant**, open the **Frame** image of the new device: it
   shows the last picture. Open the integration's options (Configure) and
   set:
   - **Rotation**, so the digits stand upright;
   - **Crop left, top, width and height**, in pixels of the turned picture,
     from the top left corner of the first window to the bottom right
     corner of the last one, windows only;
   - **Gap between windows**, the width of the housing between two windows;
   - **Drums** and **Decimal drums** (for example 8 and 3 for 00123.456).

   Save, take another picture and check the Frame image: each window is
   outlined. Adjust until every outline sits on its window.
5. **Teach the reading**: in the options, type the reading the Frame image
   shows, for example `00123.456`, and save. The status turns to OK.
6. **Start** the app. It keeps going with the screen off.

Typing a reading again, a few hours later, teaches the digits the first
picture did not show and gets the reading going faster. The **Learnt
digits** sensor says how many of the ten are known; the options list which
ones are still missing.

## Options

| Option | Meaning |
|---|---|
| Interval | Seconds between two pictures (10 to 3600, 60 by default). |
| Torch | Light the meter for each picture. |
| Rotation | Clockwise turn that makes the digits upright. |
| Crop left, top, width, height | The windows, in pixels of the turned picture. |
| Gap between windows | Housing between two windows, in pixels. |
| Drums, Decimal drums | How many drums, and how many after the decimal point. |
| Largest flow | The most the meter passes: 6 m³/h for a G4, 10 for a G6. |
| Reading shown on the last frame | Teaches the reading; leave empty otherwise. |
| Forget the digits learnt | Start learning again, after a wrong reading was taught. |

Changing the rotation, crop, gap or number of drums forgets the digits
learnt, since they no longer match the windows; type the reading again.

## Entities

| Entity | Description |
|---|---|
| Reading | The meter in m³, device class gas, state class `total_increasing`. Use it in the Energy dashboard as gas consumption. |
| Status | How the last picture went (see below). |
| Learnt digits | How many of the ten digits are known. |
| Frame | The last picture, upright, with each window outlined: green when it was read, orange when it was not, `~` under a drum caught between two digits. |

| Status | Meaning |
|---|---|
| Waiting for the phone | No picture since Home Assistant started. |
| Phone offline | No picture for three intervals, and at least five minutes. |
| Unreadable picture | The phone sent something that is not a JPEG. |
| Crop not set | Set the crop in the options. |
| Crop outside the picture | The crop does not fit the picture. |
| Reading to teach | Type the reading in the options. |
| Digits not recognised | No drum at rest matched a known digit: light, focus, or the phone moved. |
| Implausible reading | The drums show a value the meter cannot have reached. Check the reading, and type the right one. |
| OK | The picture was read. |

## Good to know

- The reading lags while gas flows: the low drums are caught turning and
  are not read. It catches up when the meter stops. A meter that stopped
  mid-carry, with drums between two digits, keeps the reading slightly
  behind until gas flows again.
- A reading typed lower than the current one is a correction. Home
  Assistant counts the difference as negative consumption and logs once
  that the sensor is not strictly increasing.
- The webhook only accepts requests from the local network. Its address
  holds the webhook's secret: the app never sends it anywhere else, does
  not follow redirects, and keeps it out of Android backups.
- Download the integration's diagnostics (device page, three dots menu)
  when reporting a problem. They hold the options, the status, the reading
  and how many masks each digit has, never the webhook address or the
  pictures.
- The recognition was developed and tested on synthetic pictures of a drum
  counter, rolling drums, carries, uneven light and noise included. Real
  meters differ; reports with the Frame image are welcome.

## Development

```shell
python -m pip install -r requirements-dev.txt
ruff check . && ruff format --check .
mypy --strict custom_components/android_meter
pytest
```

The tests use `pytest-homeassistant`, the test package Home Assistant
publishes with each release, at the version of `homeassistant` pinned in
`requirements-dev.txt`. `tests/synth.py` draws the synthetic meter.

## License

BSD 2-Clause, see [LICENSE](LICENSE).
