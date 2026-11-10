<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/android_meter/brand/dark_logo.png"/>
    <img src="custom_components/android_meter/brand/logo.png" alt="Android Meter" width="512"/>
  </picture>
</p>

<p align="center">
  <a href="https://github.com/renaudallard/homeassistant_android_meter/releases/latest">
    <img src="https://img.shields.io/github/v/release/renaudallard/homeassistant_android_meter?label=version&style=flat-square&sort=semver" alt="Latest release"/>
  </a>
  <a href="https://github.com/renaudallard/homeassistant_android_meter/releases">
    <img src="https://img.shields.io/github/downloads/renaudallard/homeassistant_android_meter/total?style=flat-square&label=downloads" alt="Downloads"/>
  </a>
  <a href="https://github.com/renaudallard/homeassistant_android_meter/actions/workflows/validate.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/renaudallard/homeassistant_android_meter/validate.yml?style=flat-square&label=hacs%20%2F%20hassfest" alt="Validate"/>
  </a>
  <a href="https://github.com/renaudallard/homeassistant_android_meter/actions/workflows/test.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/renaudallard/homeassistant_android_meter/test.yml?style=flat-square&label=tests" alt="Tests"/>
  </a>
  <a href="https://github.com/renaudallard/homeassistant_android_meter/actions/workflows/android.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/renaudallard/homeassistant_android_meter/android.yml?style=flat-square&label=android" alt="Android"/>
  </a>
  <a href="https://www.home-assistant.io/">
    <img src="https://img.shields.io/badge/Home%20Assistant-2026.11%2B-41BDF5?logo=home-assistant&logoColor=white&style=flat-square" alt="Home Assistant"/>
  </a>
  <a href="https://hacs.xyz">
    <img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square" alt="HACS"/>
  </a>
  <a href="./LICENSE">
    <img src="https://img.shields.io/github/license/renaudallard/homeassistant_android_meter?style=flat-square" alt="License"/>
  </a>
  <a href="https://www.paypal.me/RenaudAllard">
    <img src="https://img.shields.io/badge/PayPal-Donate-blue.svg?logo=paypal&style=flat-square" alt="PayPal"/>
  </a>
</p>

---

Read a **mechanical gas meter** with an old Android phone stuck to it, and
feed the reading to Home Assistant's **Energy dashboard**.

The phone sits on the meter in a magnetic holder and takes a picture of the
counter every minute or so, torch on. It posts each picture to Home
Assistant, which reads the drums itself and keeps a gas sensor in m³.

**Nothing pre-trained, no cloud.** The integration learns the digits of
your own meter from one reading you type in, and the pictures never leave
your network.

<p align="center">
  <img src="assets/frame.jpg" alt="The Frame image: each drum window outlined, the digit read under it" width="640"/>
</p>
<p align="center"><sub>
The Frame image, drawn by the integration on a synthetic counter. Green
windows were read; the last drum is turning, so it is left out and the
reading stays at 01284.390 until it stops.
</sub></p>

> Two parts live in this repository: the Home Assistant integration in
> `custom_components/android_meter`, for Home Assistant **2026.11 or newer**,
> and the Android Meter app in `android`, for **Android 9 or newer**.

## Highlights

- **Learnt from your meter.** Type the reading the first picture shows, and
  the digits it holds are learnt. The others are learnt on their own as the
  meter turns, and typing a reading again later speeds that up.
- **Never ahead of the meter.** A reading is chosen among the values the
  meter can have reached since the last picture, at most the largest flow it
  is built for. Of the values that fit equally well, the lowest is taken, so
  a doubt delays the reading but never pushes it past the real one.
- **Turning drums left out.** A drum caught between two digits, a blank one
  or one unlike any known digit plays no part in the reading.
- **Wrong readings show up.** A picture whose drums show a value the meter
  cannot have reached, lower than the reading for instance, is refused and
  the status says so.
- **The phone is only a camera.** The app takes the picture, posts it, and
  waits for the interval the answer gives it: every setting lives in Home
  Assistant.
- **Local only.** The webhook only takes requests from your network, and
  the app has no dependency beyond Android itself.

## How it works

1. The app takes a picture, posts the JPEG to the integration's webhook,
   and waits for the interval the answer gives it.
2. The integration turns the picture upright, crops the counter, cuts it
   into one window per drum, and turns each window into a small black and
   white mask of its digit.
3. A drum at rest is compared with the masks learnt for each digit.
4. The reading is chosen among the values the meter can have reached since
   the last picture, and the lowest of the most likely ones is kept.
5. The same choice labels new masks: once the meter can only show one digit
   on a drum, or only one value fits, what the drum shows is learnt as that
   digit.

## Installing

### The integration

Search for **Android Meter** in Settings > Marketplace (or HACS) and
install it. If it is not listed, add this repository as a custom
integration repository first:
`https://github.com/renaudallard/homeassistant_android_meter`.

To install by hand, unpack `android_meter.zip` from the latest release into
`config/custom_components/android_meter/` and restart Home Assistant.

### The app

Download `android-meter-<version>.apk` from the
[latest release](https://github.com/renaudallard/homeassistant_android_meter/releases/latest)
on the phone and open it; Android asks to allow installing from the browser
or file manager the first time. Every release carries the app and the
integration under the same version, and an update installs over the
previous one.

To build it yourself, run `./gradlew assembleRelease` in `android/` (JDK 21,
Android SDK platform 37).

## Setting up

1. **Mount the phone** on the meter with the back camera over the counter,
   close enough for the drums to fill a good part of the picture but no
   closer than the camera can focus. Keep it on its charger.
2. **Add the integration**: Settings > Devices & services > Add integration
   > Android Meter. Give the meter a name. The next screen shows a pairing
   QR code and the webhook address, for example
   `http://192.168.1.10:8123/api/webhook/...`.
3. **Pair the app**: scan the QR code with the phone's camera and share the
   text it shows to Android Meter, or copy it and press **Paste the pairing
   link** in the app. The app asks whether to send the pictures to that Home
   Assistant; it names only the host, as the rest of the address is the
   webhook's secret. The code is also in Configure > Phone and pairing. Typing
   the address in the app works too.
4. **In the app**, pick the camera (the list shows how close each one
   focuses) and press **Take one picture**. A phone close to the meter
   wants the camera that focuses closest, often the wide one, which also
   sees the most. Some phones, Motorola's among them, leave their extra
   back cameras out of Android's list; the app offers them all the same. The picture is shown in the app
   and sent to Home Assistant, upright however the phone is held.
5. **Set the crop on the phone**: press **Set the crop** in the app, then
   **Find the counter**: Home Assistant looks for the row of drums on the
   last picture and sets the box, the drums, the decimal drums (the red
   ones) and the gap. Check that each green frame sits on one window. If
   not, or if no counter was found, drag the yellow box over the drum
   windows yourself, moving it from its middle and sizing it from its
   corners, set the drums, decimal drums and gap with the − and + buttons,
   and press **Save the crop**.

   A drum hidden by a glare between two others is counted all the same,
   but one hidden at either end, behind a seal wire or out of the picture,
   is left out of the crop. Drums left out on the right make the reading
   coarser. Drums left out on the left cost nothing: they only turn when
   the drum to their right goes from 9 to 0, so the reading carries them
   on its own. A phone that cannot be set far enough back to see the whole
   counter is best aimed at the last drums, red ones included.

   The meter's device page has the same settings as controls, next to the
   **Frame** image, for fine tuning: **Rotation**, **Crop left, top,
   width and height**, **Gap between windows**, **Drums** and **Decimal
   drums**. Each change redraws the Frame image with every window
   outlined. Turning the picture starts the crop anew.
6. **Teach the reading**, see below. Settings also shows a Repairs notice,
   **Android Meter needs a reading**, until it is done.
7. **Start** the app. It keeps taking pictures with the screen off, as a
   foreground service with its notification showing. Press **Let it run
   without battery limits** once, so Android does not stop it to save
   battery.

## Teaching the digits

The integration learns the digits of your meter from the readings you type
in, then goes on learning on its own as the meter turns.

1. Open Configure > **Teach the reading**, or press **Fix** on the Repairs
   notice. The screen shows the last picture with each drum outlined. The
   **Teach the reading** box on the device page does the same.
2. Type the meter's reading, every drum from left to right, for example
   `01234.567`, drums left of the crop included. A drum caught between two
   digits counts as the lower one.
3. Save. Every drum of the crop showing a whole digit is learnt, the
   status turns to OK, and **Lock the crop** turns on.

The screen lists the digits learnt and the ones still to learn, and the
**Learnt digits** sensor counts them. Teaching again later, when the meter
shows other digits, gets all ten sooner. **Forget the digits learnt**
starts over, for instance after a wrong reading was typed.

A change of crop forgets the digits learnt, since they no longer match the
windows. **Lock the crop** keeps a slip from doing that: while it is on, the
crop and counter controls, **Find the counter** and the phone's crop editor
are refused. Turn it off to move the crop, then teach the reading again.

## Settings

On the meter's device page:

| Control | Meaning |
|---|---|
| Rotation | Clockwise turn that makes the digits upright. |
| Crop left, top, width, height | The windows, in pixels of the turned picture. |
| Gap between windows | Housing between two windows, in pixels. |
| Drums, Decimal drums | How many drums the crop holds, and how many of them are after the decimal point. |
| Lock the crop | On, the controls above and Find the counter refuse changes. Turns on when a reading is taught. |
| Teach the reading | Type the meter's reading, as the Frame image shows it, to teach it. |
| Forget the digits learnt | Start learning again. |
| Find the counter | Find the row of drums on the last picture and set the crop around it. |

In Configure, a menu of two screens:

| Screen | Options |
|---|---|
| Teach the reading | The meter's reading, with the last picture; Forget the digits learnt. |
| Phone and pairing | The pairing QR code; interval (10 to 3600 s, 60 by default); torch; largest flow (6 m³/h for a G4, 10 for a G6). |

## What you get

| Entity | Description |
|---|---|
| Reading | The meter in m³, device class gas, state class `total_increasing`. Use it in the Energy dashboard as gas consumption. |
| Status | How the last picture went (see below). |
| Learnt digits | How many of the ten digits are known. |
| Frame | The last picture, upright, with each window outlined: green when it was read, orange when it was not, `~` under a drum caught between two digits. Redrawn on every change of crop. |

| Status | Meaning |
|---|---|
| Waiting for the phone | No picture since Home Assistant started. |
| Phone offline | No picture for three intervals, and at least five minutes. |
| Unreadable picture | The phone sent something that is not a JPEG. |
| Crop not set | Set the crop with the controls on the device page. |
| Crop outside the picture | The crop does not fit the picture. |
| Reading to teach | Teach the reading (Configure > Teach the reading, or the Repairs notice). |
| Digits not recognised | No drum at rest matched a known digit: light, focus, or the phone moved. |
| Implausible reading | The drums show a value the meter cannot have reached. Check the reading, and type the right one. |
| OK | The picture was read. |

## Good to know

- The reading lags while gas flows: the low drums are caught turning and
  are not read. It catches up when the meter stops. A meter that stopped
  mid-carry, with drums between two digits, keeps the reading slightly
  behind until gas flows again.
- Android only lets an app start the camera from something the user did.
  After a reboot, an update of the app, or Android stopping it, the app
  therefore cannot start again on its own: it posts **The meter watch
  stopped**, and a tap on that notification starts it again.
- A reading typed lower than the current one is a correction. Home
  Assistant counts the difference as negative consumption and logs once
  that the sensor is not strictly increasing.
- The webhook address holds the webhook's secret: the app never sends it
  anywhere else, does not follow redirects, and keeps it out of Android
  backups.
- The recognition was developed on synthetic pictures of a drum counter,
  rolling drums, carries, uneven light and noise included, and checked on a
  real Actaris G4. Marks printed under the digits and a glow across a
  window are handled; a glare streak across a digit, or a seal wire in
  front of it, is not, so place the phone and its torch to keep the
  windows clear. Reports with the Frame image are welcome.

## Reporting a problem

Download the integration's diagnostics (device page, three dots menu) and
attach them with a Frame image. They hold the options, the status, the
reading and how many masks each digit has, never the webhook address or the
pictures.

## Development

```shell
python -m pip install -r requirements-dev.txt
ruff check . && ruff format --check .
mypy --strict custom_components/android_meter
pytest
```

The tests use `pytest-homeassistant`, the test package Home Assistant
publishes with each release, at the version of `homeassistant` pinned in
`requirements-dev.txt`. `tests/synth.py` draws the synthetic meter, and
`tests/test_simulation.py` runs hours of a meter in use through the reader.

## License

BSD 2-Clause, see [LICENSE](LICENSE).
