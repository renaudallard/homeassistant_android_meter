# Privacy policy

Android Meter is a companion app for the Android Meter integration of
Home Assistant. It does not collect, store or share any personal data
with its developer or with third parties. It contains no analytics,
advertising or crash reporting code, and no third-party code apart from
the Kotlin standard library.

## Network

The app sends data to one place only: the Home Assistant webhook
address you set, by hand or from the pairing code Home Assistant shows.
It sends there:

- the pictures of the meter it takes, as JPEG, without location;
- the crop you draw on the phone, and the request to find the counter
  when you tap Find the counter.

Home Assistant answers with the interval between pictures, whether to
turn the torch on, whether to keep the camera open between pictures,
and the crop in force. The name of the Home
Assistant server is looked up through the phone's DNS resolver, as for
any app, so the network or the Private DNS provider you set sees it.
Nothing else is sent anywhere.

Reading the meter happens in your Home Assistant, not in the app nor in
any cloud service.

## Data on your phone

The webhook address, the camera chosen, the crop and whether the watch
is on are kept in the app's private storage. The webhook address holds
a secret, so this data is excluded from Android backups and
device-to-device transfers, and the main screen is kept out of
screenshots and Recents. The last picture is only held in memory, to
show it on screen.

## Permissions

- Camera: taking pictures of the meter
- Internet and local network: sending them to Home Assistant
- Foreground service and camera service: taking pictures with the
  screen off
- Notifications: showing the notification of the watch, and the one
  that offers to resume it
- Run at startup: offering to resume the watch after a restart or an
  update
- Ignore battery optimisations: keeping the watch running for days
  without Android stopping it
- Wake lock: finishing a picture and its upload while the screen is off
- Hide overlays: hiding the windows of other apps over the pairing
  dialog

## Removing your data

Uninstalling the app, or clearing its storage in Android's settings,
deletes everything it keeps. Pictures already sent are in your Home
Assistant, which the app has no control over.

## Children

Android Meter is not directed at children.

## Changes

Changes to this policy are published in this file, with their history in
the repository.

## Contact

Questions can be asked in the project's issues:
https://github.com/renaudallard/homeassistant_android_meter/issues
