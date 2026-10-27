# URS Live Stock Feed (Chrome helper)

Reads Petpooja's **Item Report: Day Wise With Time** about every 10 minutes, **only during the
restaurant's hours**, and sends today's rows to **admin.ursmajestic.com → Live stock**.
Read only: it reloads the report page, sets the report's own date range to today's business day
(1 AM to 1 AM) and presses the report's Search button. It never submits or changes anything in Petpooja.

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
- Only walk-in / counter sales are in Petpooja's item report. Zomato and Swiggy join after their uploads.
- The server rejects a report that isn't for today's business day, so a stale or rolled-over report can't overwrite the numbers.
- To stop it: remove the add-on, or revoke the helper code.
