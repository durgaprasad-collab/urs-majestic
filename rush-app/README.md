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
The supplied 9 October invoice was also read through the configured OCR service:
Bill 7, Gobi Fried Rice (Regular) x 1, Dine-in. No kitchen order was created.
