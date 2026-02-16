# Vehicle VIN to Registration & Specs Tool

This project reads a list of VINs from a spreadsheet, looks up the UK registration via the MOT History API, then fetches vehicle details (fuel type, engine size, CO₂) via the DVLA Vehicle Enquiry Service (VES). Results are written to a new Excel file in your **Downloads** folder.

> ⚠️ IMPORTANT: Your spreadsheet must have a column named exactly `vin_number` (all lowercase, with underscore). The script will not run without it.

The guide below is written for newcomers—no command‑line experience assumed.

---

## What You Need

1) **A computer** running macOS or Windows.  
2) **Reliable internet** (the tool calls online MOT and VES APIs).  
3) **Python 3.9+** installed (we’ll show how).
4) **API credentials**  
   - MOT History API: `MOT_API_KEY`, plus OAuth details (`MOT_CLIENT_ID`, `MOT_CLIENT_SECRET`, `MOT_SCOPE`, `MOT_TOKEN_URL`).  
   - VES API: `VES_API_KEY`.  
5) **Your VIN spreadsheet** with the column `vin_number`. Formats supported:  
   - Excel `.xlsx` (first sheet is read)  
   - CSV exported from Excel or Google Sheets  

---

## Step 1: Get Python

### macOS
- Open **Spotlight** (⌘ + Space), type `Terminal`, press Enter.
- Check Python: type `python3 --version` and press Enter.  
  - If you see a version (e.g., `Python 3.12.2`), you’re good.  
  - If “command not found”, install Homebrew (https://brew.sh), then run:  
    - `brew install python`

### Windows
- Press **Start**, type `cmd`, press Enter to open Command Prompt.
- Check Python: type `py --version` and press Enter.  
  - If you see a version, you’re good.  
  - If not, install Python from https://www.python.org/downloads/ (check “Add Python to PATH” during install). Then reopen Command Prompt and try `py --version` again.

---

## Step 2: Download / Open the Project Folder

Put the project folder (contains `main.py`, `requirements.txt`, `.env`) somewhere easy, e.g., Desktop:
- macOS example path: `/Users/yourname/Desktop/Perigon_VehicleScript`
- Windows example path: `C:\Users\yourname\Desktop\Perigon_VehicleScript`

### How to find the path of your folder
- macOS Finder: open Terminal, type `cd ` (with a trailing space), then drag the folder from Finder into the Terminal window and press Enter. This pastes the full path for you.  
- Windows File Explorer: click the address bar while inside the folder, copy the full path.

---

## Step 3: Open a Terminal/Command Prompt in the Project Folder

### macOS
In Terminal, type `cd `, then paste the folder path, then press Enter. Example:
```
cd /Users/yourname/Desktop/Perigon_VehicleScript
```

### Windows
In Command Prompt, type `cd `, then paste the folder path, then press Enter. Example:
```
cd C:\Users\yourname\Desktop\Perigon_VehicleScript
```

Tip: If you type `ls` (macOS) or `dir` (Windows), you should see `main.py`, `.env`, `requirements.txt`, etc.

---

## Step 4: Install the Required Python Packages
In the command line terminal paste:

### macOS
```
python3 -m pip install -r requirements.txt
```
If you see a “permission denied” message, try:
```
python3 -m pip install --user -r requirements.txt
```

### Windows
```
py -m pip install -r requirements.txt
```
If you see a permissions message, right-click Command Prompt and “Run as administrator”, then rerun the command.

---

## Step 5: Add Your API Keys to `.env`

Open the `.env` file in a text editor (TextEdit on macOS, Notepad on Windows). It may already contain keys if you received them; otherwise fill in your own:
```
MOT_API_KEY=...
MOT_ACCESS_CODE=          # leave blank; the script auto-fetches a fresh token
VES_API_KEY=...
MOT_CLIENT_ID=...
MOT_CLIENT_SECRET=...
MOT_SCOPE=https://tapi.dvsa.gov.uk/.default
MOT_TOKEN_URL=...
```
Save the file. It is ignored by git; if you want your boss to reuse your keys, leave them in the `.env` you send. If not, blank them before sharing.

---

## Step 6: Prepare Your VIN File

- Ensure the first sheet (for `.xlsx`) or the CSV has a column named exactly `vin_number`.  
- If you’re using Google Sheets, export as CSV (File → Download → Comma-separated values).
- Put the file somewhere easy, e.g., Desktop. Note its full path:
  - macOS example: `/Users/yourname/Desktop/VINs_2025.xlsx`
  - Windows example: `C:\Users\yourname\Desktop\VINs_2025.xlsx`

---

## Step 7: Run the Script

### macOS
```
python3 main.py --input /Users/yourname/Desktop/exampleFileName.csv --debug --resume
```

### Windows
```
py main.py --input C:\Users\yourname\Desktop\exampleFileName.csv --debug --resume
```

What happens:
- The script auto-fetches a MOT access token and caches it; refreshes when it’s close to expiry.
- Processes VINs, respecting MOT/VES rate limits.
- Saves checkpoints every 100 rows so you can resume if interrupted (`--resume` flag).
- Writes the final Excel to your **Downloads** folder by default (e.g., `C:\Users\yourname\Downloads\new_VINs.xlsx` or `/Users/yourname/Downloads/new_VINs.xlsx`). You can override with `--output <path>`.
- `--debug` shows a progress bar and only prints failures.

Example with a custom output path (macOS):
```
python3 main.py --input /Users/yourname/Desktop/VINs_2025.xlsx --output /Users/yourname/Downloads/my_results.xlsx --debug --resume
```

---

## Understanding the Output

Columns:
- `vin_number` — the VIN you provided.
- `registration` — from MOT; still shown even if VES fails.
- `fuel_type`, `engine_size_cc` — from MOT; overridden by VES if available.
- `co2_emissions_gkm` — from VES only (will be blank if VES not found).
- `success` — True if VES succeeded; False otherwise.
- `error` — reason for any failure (e.g., “VIN not found in MOT”, “Registration not found in VES”, “Registration format not accepted by VES (likely non-GB/trade plate)”).

## How to Know It’s Working
- You’ll see a progress bar counting VINs.
- At the end: “Finished processing. Success: X, Errors: Y” and “Wrote output to …”.
- A new Excel file appears in your Downloads folder (or your chosen `--output` path).

---

## Common Issues & Fixes

- **“python: command not found” (macOS)**  
  Install Python via Homebrew (`brew install python`), then rerun using `python3`.

- **“py is not recognized” (Windows)**  
  Re‑run the Python installer and tick “Add Python to PATH”, then use `py`.
  
- **pip vs pip3 vs python vs python3**  
  - On macOS, use `python3` to run and `python3 -m pip` to install packages (older `python`/`pip` may point to Python 2).  
  - On Windows, use `py` to run and `py -m pip` to install packages.

- **401/403 errors**  
  Usually expired MOT token or bad keys. Ensure `.env` values are correct; rerun (the script will fetch a fresh token).

- **Lots of “Invalid VIN format”**  
  Check your VIN column; each VIN must be 17 characters, no I/O/Q.

- **400 from VES**  
  Likely a non‑GB/trade plate; the script now labels this explicitly.

- **Resuming after interruption**  
  Add `--resume` when rerunning. The script uses the existing output file and checkpoint to skip already processed rows.
- **Runtime expectations**  
  Processing is intentionally throttled by MOT/VES rate limits (not by the script). Rough guide: ~1,000 VINs ≈ 20–25 minutes; thousands of VINs can take over an hour. Longer runs are normal because of API rate limits.

---

## Optional: Faster Runs / Custom Settings

- `--checkpoint-every 200` to checkpoint less often (fewer disk writes).  
- `--output <path>` to change where the result is saved.  
- `--debug` can be removed for quieter output (you’ll still get a completion message).

---

## Quick Reference (Cheat Sheet)

1) Open terminal in project folder (`cd ...`).  
2) Install deps: `python3 -m pip install -r requirements.txt` (mac) or `py -m pip install -r requirements.txt` (win).  
3) Fill `.env` with keys.  
4) Run:
   - macOS: `python3 main.py --input /path/to/VINs.xlsx --debug --resume`
   - Windows: `py main.py --input C:\path\to\VINs.xlsx --debug --resume`
5) Find results in your Downloads folder.

---

## Where Files Go

- **Input**: anywhere you like; you provide the path.  
- **Output**: defaults to your Downloads folder; override with `--output`.  
- **Token cache**: `.mot_token_cache.json` (ignored by git).  
- **Checkpoints**: `<output>.checkpoint.json` (also ignored by git).

---

## Credits & Copyright
Developed by Jamie Thomson (Jamie.wlt@outlook.com). Please reach out with any problems.
Copyright © Perigon Partners. All rights reserved.
