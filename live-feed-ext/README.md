# URS Live Stock Feed (Chrome helper)

Reads Petpooja's **Item Report: Day Wise** about every 10 minutes (10:30 AM to midnight,
the PC's local time) and sends today's rows to **admin.ursmajestic.com → Live stock**.
Read only: it reloads the report page and reads its table; it never clicks, submits or
changes anything in Petpooja.

## Install (once, on the restaurant PC)
1. Chrome → `chrome://extensions` → turn on **Developer mode** (top right).
2. **Load unpacked** → choose this `live-feed-ext` folder.
3. On admin.ursmajestic.com → **Live stock** → **Chrome helper** → **Create helper code**; copy it.
4. Click the add-on's icon (puzzle piece → URS Live Stock Feed), paste the code,
   press **Save & test**. You should see "Connected".
5. Be signed in to Petpooja in the same Chrome. The add-on opens (and keeps) a small pinned
   tab on the item report and refreshes it itself.

## Notes
- Chrome must be open and the PC awake during service.
- The badge on the icon shows ✓ (last send worked) or ! (it didn't; open the popup for why).
- Only walk-in / counter sales are in Petpooja's item report. Zomato and Swiggy join after their uploads.
- Sales after midnight are not read live (the report's date rolls over); the nightly upload covers them.
- To stop it: remove the add-on, or revoke the helper code.
