# Purchase and goods transit plan

Purchase capture: buyer takes a photo or uploads a PDF, reviews extracted text and items, corrects quantities/units/amounts, selects Home or Restaurant and submits. Unknown OCR lines stay visible. The existing Android OCR bridge reads photos locally; web photos and image-only PDF pages use the configured server photo reader. PDF text extraction runs on the server. OCR is a draft, never a stock write. Location records where that purchase was stored; it is not yet a live location balance. Historical purchases remain location not recorded until corrected.

## Transit workflow to implement

1. Create a transfer from Home to Restaurant (or reverse), selecting purchase lots, items and quantities. Show available source quantities. A transfer has an immutable reference and source/destination; it does not create another purchase or another expense.
2. Dispatch: buyer records dispatched quantities, carrier/person, departure time, expected arrival and optional photo. Atomically reserve/debit source quantities and credit an In transit bucket. Record actor and timestamp on an append-only event timeline.
3. Receive: destination staff opens the transfer reference/QR, checks items and records actual received quantities, condition and optional photo. Credit destination only for received quantities. Keep the balance in transit for partial deliveries; flag shortages or damage for resolution with a reason.
4. Close: finish only when every dispatched quantity is received, returned or written off with an authorised reason. Returned quantities go back to the source. Never edit a completed event; post a correction event.

## Records and stock rules

Use stock_locations (Home, Restaurant, In transit), stock_lots linked to purchase lines, stock_transfers, stock_transfer_lines and immutable stock_movement_events. Each event stores actor, server timestamp, item, base-unit quantity, source, destination, transfer and an idempotency key. Lock source lots during dispatch to prevent two people moving the same stock. Validate finite positive quantities and compatible units on the server. Concurrent receipt submissions must not receive more than the outstanding quantity.

Opening location balances require physical counts; do not infer old stock locations from purchase history. Existing purchase stock triggers feed aggregate stock: extend these into location movements before using transfers operationally. Transfers preserve aggregate ownership, and must not invoke purchase stock increments. Kitchen availability should include Restaurant only; Home and In transit remain separately visible. Consumption debits Restaurant. Reconcile the sum of all location buckets to owned aggregate stock.

## Purchases display

Show cards for At home, In transit and At restaurant, with item/quantity balances and filters. Transfer rows show reference, route, carrier, ETA, last update, dispatched/received/outstanding quantities and an expandable timeline. Flag overdue arrivals and unresolved shortages. Start with explicit dispatch/receive updates; optional live GPS later needs a carrier app and location consent and should never replace receipt confirmation.

## Delivery order and acceptance

First migrate location balances and reconcile existing stock triggers, then add transfer dispatch/partial receipt/correction endpoints and UI, then overdue alerts. Verify: partial delivery, return, loss, duplicate taps, simultaneous dispatch, simultaneous receipt, unit conversion, deleted purchase references, and cross-location totals. Audit every movement; retain links to the original purchase. No transit endpoints or location balance changes are enabled by this capture change.

## Activation and checks

Apply Alembic revision 0055 before deploying the changed server. Rebuild/install the Admin Android app to enable its on-device OCR bridge. The Rush app bridge also accepts the purchase page. Browser photo scanning and scanned PDF image extraction require OCR_SPACE_API_KEY; buyers can also paste phone-recognised text. Original uploaded files are not retained by this change; submitted OCR text is retained with the confirmed purchase lines. Validate real supplier bill layouts on a phone before rollout; ambiguous column layouts intentionally require manual entry.
