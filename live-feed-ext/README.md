# URS Live Stock Feed (Chrome helper)

About every 10 minutes, **only during the restaurant's hours**, it:
1. opens Petpooja's **home page** and presses its **Refresh** button (the circular arrow beside the date),
   which pulls the latest orders in so the reports aren't behind;
2. opens **Item Report: Day Wise With Time**, sets its date range to today's business day (1 AM to 1 AM)
   and presses the report's Search button;
3. reads the rows and sends them to **admin.ursmajestic.com → Live stock**;
4. opens **Zomato's partner "Order history"** (zomato.com/partners), scrolls the list, and sends today's
   orders (rejected / cancelled ones are left out by the server). Untick **Include Zomato** in the add-on to skip this;
5. opens Swiggy's partner portal **Past Orders** (partner.swiggy.com/food/pastorders/<restaurant id>) and sends today's
   orders the same way. Untick **Include Swiggy** to skip this.

## Nightly upload of the finished day
After closing it uploads the day's **Petpooja item report** by itself, so the manual *Item Report Day Wise* upload
is no longer needed. The PC and Chrome must be on:
- about **1:15 AM**: presses Petpooja's Refresh, reads the finished day's item report and sends it to
  admin.ursmajestic.com (the same import as the manual upload: sales, costs, stock use);
- about **6 AM**: sends it again once Petpooja has synced its late sales; the second send replaces the first;
- if the PC was off, the next run also catches up the three days before.
Untick **Upload each finished day automatically** in the add-on to turn it off, or press **Upload yesterday now**.
It also uploads **Swiggy's** finished day from the Past Orders list (replacing the Swiggy .csv upload): net sales, discounts,
items and stock use, the same as the CSV. It also uploads Petpooja's **Order Listing** for the finished day from the *All Orders* page (replacing the .xlsx upload:
order counts, average bill and order times). Likewise **Zomato's** finished day (replacing the Zomato .csv): the add-on reads each order's details from Zomato's order history
(subtotal, the restaurant's discounts, items). Nothing is uploaded by hand any more.

Read only: the only buttons it presses are Petpooja's Refresh and Search; on Zomato and Swiggy it only fetches the order list.
It never submits or changes anything on any of the sites.

You must be signed in to **Petpooja, Zomato and Swiggy's partner portal** in this Chrome.

## Restaurant hours
Default **12:00 PM to 1:00 AM** (PC's local time), set in the add-on's window under **Opens / Closes**.
Outside those hours it does nothing: no reloads, no reads, no tab opened. After midnight it still reads
the *previous* business day, which is what Petpooja does until 1 AM.

## Install (once, on the restaurant PC)
1. Chrome → `chrome://extensions` → turn on **Developer mode** (top right).
2. **Load unpacked** → choose this `live-feed-ext` folder. Pin it from the puzzle-piece menu.
3. On admin.ursmajestic.com → **Live stock** → **Chrome helper** → **Create helper code**; copy it.
4. Click the add-on's icon, paste the code, check **Opens / Closes**, press **Save & test**.
5. Be signed in to Petpooja in the same Chrome. The add-on opens (and keeps) a small pinned tab on the
   item report during opening hours and refreshes it itself.

## Notes
- Chrome must be open and the PC awake during service.
- The badge on the icon shows ✓ (last send worked) or ! (it didn't; open the popup for why).
- Only walk-in / counter sales are in Petpooja's item report. Zomato and Swiggy orders are read from their own portals.
- The server rejects a report that isn't for today's business day, so a stale or rolled-over report can't overwrite the numbers.
- To stop it: remove the add-on, or revoke the helper code.
