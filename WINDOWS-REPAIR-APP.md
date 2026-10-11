# Using Brax Device Toolkit on Windows

The app provides a guided repair workflow for supported BraX3 phones and a
separate page for preparing OS downloads. Repair writes currently support
**BaseOS build 58 / P18**. Installing or formatting an OS is disabled in this
release, even when SP Flash Tool is installed.

## 1. Connect your phone

1. Start the phone normally in Android.
2. Open **Settings → About phone** and tap **Build number** seven times. Enter
   your screen-lock code if asked.
3. Open **Settings → System → Developer options** and enable **USB debugging**.
4. Connect a USB data cable, unlock the phone and accept **Allow USB debugging**
   for this computer.
5. In **Serial & IMEI**, refresh the device list, select your phone and click
   **Check connection**. Read the checked phone and OS information before continuing.

If ADB is missing, extract Android platform-tools and select its `adb.exe` in the
app. If Windows cannot recognize the phone, check the appropriate USB driver.
An authorization prompt must be accepted on the phone; a driver download cannot
approve the computer for you.

## 2. Prepare for a repair

Keep the cable connected and use **Enter META mode** in the guided workflow.
META is the phone's service mode, used to access supported factory records.
The app checks the service connection and supported firmware before continuing.
A phone entering service mode may disappear from the normal Android device list.

If the app was restarted while the phone was in service mode, use
**Advanced tools → Detect current mode**. The adjacent **Go to META mode** and
**Go to normal mode** controls provide supported transitions after detection.
Returning to normal mode restarts Android; it does not undo a previous write.

## 3. Enter and review the identifiers

Enter the assigned serial number and both IMEIs. Use the optional Bluetooth and
Wi-Fi fields only when those factory addresses need to be repaired. Validate the
inputs and review the values before writing.

An empty record means that the phone's stored field is blank or uninitialized.
Initializing it stores the new value you reviewed; it does not erase an existing
value first or reset the phone. The app detects empty records during inspection
and explains them in the write review.

Use **Write & verify identifiers** after checking the review. The guided operation
backs up the records it changes and checks the written values. Then complete
**Restart & verify** to check that the changes survive a restart. A successful
write alone is not the final verification step.

## Back up or restore records

In **Advanced tools → Connection & inspection**:

- **Read current serial & IMEIs** reads the stored values without changing them.
- **Back up current serial & IMEIs** saves a local snapshot and displays its location.
- **Choose backup & review** lets you select a backup, choose the records to restore
  and compare saved values with the phone's current values before confirmation.
- **Restart & verify restored records** checks the result after returning to Android.

Restore a backup belonging to the same phone. If that backup contains empty
records, restoring them returns those records to empty. These backups contain
factory records, not your photos, applications or a complete Android installation.

Use **Open records** to locate saved files. **Save activity log** exports session
messages; it does not export device backups. **Begin a fresh session after recovery**
clears the app's current selection and progress, retaining saved records. It does
not repair or reset the phone.

## Prepare an OS download

The **Install OS** page uses six steps: check Android, select and download an OS,
review the operation, shut down and reconnect, install, and verify startup.
**Only the first three steps are available in this release.** The later steps
remain locked until exact phone-to-firmware compatibility can be verified.
A matching chip or download checksum alone does not establish compatibility.

Start with Android running, check the phone and choose an OS package from the
catalog. The app verifies the download checksum and lets you review the package.
Changing the package requires a new review.

The SP Flash Tool prerequisite section can download an available Windows package
and check its checksum. Extract the complete archive, select `SPFlashToolV6.exe`
and let the app check its companion files. Selecting the executable does not
start it or enable installation.

### What do the installation modes mean?

All these operations are currently disabled. Their consequences differ:

| Mode | What it means |
| --- | --- |
| Download Only | Writes the selected firmware images. It can overwrite personal data if the selected images include the data partition; it does not guarantee data preservation. |
| Firmware Upgrade | Lets SP Flash Tool handle an OS update and partition-layout changes. Preservation of personal data and factory records has not been verified for BraX3. |
| Format Only | Erases storage without installing an OS. Depending on the erase range, it can remove factory records and leave the phone unable to boot. |
| Format + Download | Erases storage, then writes firmware. Erased factory records are not necessarily restored by an OS package. |

OS firmware is not a substitute for a backup of factory identifiers or calibration
records. The exact erase ranges depend on the vendor tool and package.

## Progress and troubleshooting

Read the result beside the action button. Use **View session log** for details
and **Save activity log** when sharing a problem report. Check a log for personal
device identifiers before sharing it publicly.

Where transfer progress is shown, the percentage applies to the named image or
transfer, not necessarily the whole installation. A completed transfer does not
prove that Android can boot. After a supported write or restore, finish the
separate restart verification before considering the operation complete.

If an operation fails, inspect the result and saved records before retrying or
starting a fresh session. A phone absent from ADB may be in service mode; use
**Detect current mode** rather than assuming it has disconnected.
