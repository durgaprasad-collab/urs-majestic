# Rush photo entry

The Rush web page (`/rush`) now accepts a camera photo or an existing JPG/PNG
of a Petpooja invoice or KOT. Review and correct the extracted rows, tick the
review box, and choose **Use reviewed order**. This replaces the local draft
only; **Send to kitchen** still creates the order and starts the 12-minute clock.

- Ignore the printed Pick Up header. An item named Parcel means takeaway;
  otherwise classify the scan as Dine-in. Parcel is not sent as a cooking item.
- A bill number remains labelled `Bill 7`, for example; it is not a KOT number.
- Only exact, unambiguous menu matches are selected. Unknown dishes and missing
  quantities need correction. Unsupported layouts require manual entry.
- Check the printed date and all rows against the photo. This does not import
  historical sales or change the order's start time to the printed invoice time.

## Server dependency

App 1.3 bundles Google's Latin-script ML Kit model (Android 6+). The existing
camera/file picker supplies the photo to the local native bridge; the photo is
not uploaded by this path. Only recognised text is posted to `/rush/scan-text`
for menu matching. The image is decoded off the UI thread, sampled to a bounded
size and EXIF-oriented before recognition. The review, reference, combo and
pass rules use the same server parser. Native OCR errors do not silently upload
the image to another service. Network retries reuse the extracted text.

Install `URS-Rush-1.3.apk` over the existing app. Test a few restaurant bills:
speed and accuracy have not been measured on the actual restaurant phone.
The page says **Fast phone reader** when the local bridge is active. The app
still needs internet to load Rush, match dishes and save kitchen orders.

Older app versions and ordinary browsers retain the hosted path below.

Uses the existing `OCR_SPACE_API_KEY` setting and Pillow, with no new Python
dependencies or database migration. The default shared demo key may be rate
limited; configure an outlet key for regular use. Photos are sent to OCR.space
over HTTPS for recognition and are not saved by this feature. The full photo
can contain customer details, so crop to the required slip area when possible.
The page displays the external photo-reading service before capture.

Deploy the backend/template changes to update the page. The camera input must
use `accept="image/*"` with `capture="environment"`: Capacitor only launches
image capture for this MIME wildcard. **Choose photo** stays a separate picker.
Android app 1.2 also declares camera-intent visibility so Android 11+ can find
the camera during Capacitor's `resolveActivity` check. Install this update over
1.1; no direct CAMERA permission is needed to delegate to the system camera.
Test capture on the restaurant phone; automated checks cannot verify its camera.

## Checks

From `backend/`: `python -m unittest discover -s tests -p test_rush_scan.py -v`.
All Rush checks: `python -m unittest discover -s tests -p 'test_rush*.py'`.
UI flows: `node tests/test_rush_ui.cjs` and `node tests/test_rush_ui.cjs --native`.
The supplied 9 October invoice was also read through the configured OCR service:
Bill 7, Gobi Fried Rice (Regular) x 1, Dine-in. No kitchen order was created.
