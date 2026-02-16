#!/usr/bin/env python3
"""
VIN → MOT (reg lookup) → VES (vehicle details)
Usage:
    python main.py --input VINs_2025.xlsx --output new_VINs_2025.xlsx
    python main.py --input VINs_2025.csv  --output new_VINs_2025.xlsx
Env:
    MOT_API_KEY
    MOT_ACCESS_CODE  # time-limited bearer token; refresh before each run
    VES_API_KEY
"""
import argparse
import json
import os
import sys
import time
import random
from collections import deque
from typing import Optional, Tuple, List, Dict

import pandas as pd
import requests
from dotenv import load_dotenv
from tqdm import tqdm

# Constants
MOT_BASE = "https://history.mot.api.gov.uk/v1/trade"
VES_URL = "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles"
DEFAULT_SLEEP = 0.2  # base spacing
MAX_RETRIES = 3
BACKOFF_BASE = 0.5
MAX_RPS = 12  # stay under 15 rps; also respects burst via window
BURST_WINDOW = 1.0
BURST_LIMIT = 10  # per spec
TOKEN_CACHE_PATH = ".mot_token_cache.json"
CHECKPOINT_DEFAULT = 100  # rows per checkpoint
CHECKPOINT_FILE = ".checkpoint.json"


class ConfigError(Exception):
    """Raised when configuration or credentials are missing/invalid."""


class RateLimiter:
    """Simple sliding-window limiter to honor burst and RPS caps."""

    def __init__(self, max_rps: int, burst_limit: int, window: float):
        self.max_rps = max_rps
        self.burst_limit = burst_limit
        self.window = window
        self.calls = deque()

    def wait(self):
        now = time.time()
        # purge old entries
        while self.calls and now - self.calls[0] > self.window:
            self.calls.popleft()
        # enforce burst
        if len(self.calls) >= self.burst_limit:
            sleep_time = self.window - (now - self.calls[0])
            time.sleep(max(sleep_time, 0))
        # enforce RPS (max_rps -> min_interval)
        min_interval = 1.0 / self.max_rps
        if self.calls:
            delta = now - self.calls[-1]
            if delta < min_interval:
                time.sleep(min_interval - delta)
        self.calls.append(time.time())


rate_limiter = RateLimiter(max_rps=MAX_RPS, burst_limit=BURST_LIMIT, window=BURST_WINDOW)


def load_config() -> Tuple[str, Optional[str], str]:
    load_dotenv()
    mot_api_key = os.getenv("MOT_API_KEY")
    mot_access_code = os.getenv("MOT_ACCESS_CODE")  # may be None if using token fetch
    ves_api_key = os.getenv("VES_API_KEY")
    missing = [name for name, val in {
        "MOT_API_KEY": mot_api_key,
        "VES_API_KEY": ves_api_key,
    }.items() if not val]
    if missing:
        raise ConfigError(f"Missing env vars: {', '.join(missing)}. Add them to .env and retry.")
    return mot_api_key, mot_access_code, ves_api_key


def check_ves_key(ves_api_key: str) -> None:
    """Quick auth check against VES. Uses an invalid reg to avoid real data usage.
    Treat 401/403 as bad key; 400/404/200 etc. mean the key was accepted.
    """
    headers = {"x-api-key": ves_api_key, "Content-Type": "application/json"}
    payload = json.dumps({"registrationNumber": "INVALID"})
    rate_limiter.wait()
    resp = requests.post(VES_URL, headers=headers, data=payload, timeout=15)
    if resp.status_code in (401, 403):
        raise ConfigError("VES_API_KEY rejected (401/403). Refresh the key and update .env.")


def check_mot_key(mot_api_key: str, mot_access_code: str) -> None:
    """Quick auth check against MOT using a bogus VIN (17 chars)."""
    headers = {
        "Authorization": f"Bearer {mot_access_code}",
        "X-API-Key": mot_api_key,
        "Accept": "application/json",
    }
    dummy_vin = "INVALIDVIN1234567"
    url = f"{MOT_BASE}/vehicles/vin/{dummy_vin}"
    rate_limiter.wait()
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code in (401, 403):
        raise ConfigError("MOT credentials rejected (401/403). Refresh MOT_ACCESS_CODE or MOT_API_KEY.")


def fetch_registration_from_mot(vin: str, mot_api_key: str, mot_access_code: str, debug: bool = False) -> Optional[str]:
    headers = {
        "Authorization": f"Bearer {mot_access_code}",
        "X-API-Key": mot_api_key,
        "Accept": "application/json",
    }
    url = f"{MOT_BASE}/vehicles/vin/{vin}"
    resp = request_with_retry("GET", url, headers=headers, debug=debug)
    if resp is None:
        return None
    data = resp.json()
    # API can return list or object; handle both defensively
    if isinstance(data, list) and data:
        entry = data[0]
    elif isinstance(data, dict):
        entry = data
    else:
        return None
    return entry.get("registration")


def fetch_details_from_ves(registration: str, ves_api_key: str, debug: bool = False) -> Optional[dict]:
    headers = {"x-api-key": ves_api_key, "Content-Type": "application/json"}
    payload = json.dumps({"registrationNumber": registration})
    resp = request_with_retry("POST", VES_URL, headers=headers, data=payload, debug=debug, allow_400=True)
    if resp is None:
        return None
    if resp.status_code == 400:
        # invalid reg format / likely non-GB or trade
        raise ValueError("Registration format not accepted by VES (likely non-GB/trade plate)")
    return resp.json()


def request_with_retry(method: str, url: str, headers=None, data=None, timeout: int = 30, debug: bool = False, allow_400: bool = False):
    """HTTP with rate limit and exponential backoff. Returns response, None for 404, or 400 if allow_400."""
    for attempt in range(1, MAX_RETRIES + 1):
        rate_limiter.wait()
        try:
            resp = requests.request(method, url, headers=headers, data=data, timeout=timeout)
        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            delay = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            time.sleep(delay)
            continue

        if resp.status_code == 404:
            return None
        if resp.status_code == 400 and allow_400:
            return resp
        if resp.status_code in (401, 403):
            raise ConfigError("Credentials rejected (401/403).")
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_RETRIES:
                resp.raise_for_status()
            delay = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
            log_debug(debug, f"{method} {url} -> {resp.status_code}, retrying in {delay:.2f}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(delay)
            continue
        resp.raise_for_status()
        return resp
    return None


def log_debug(debug: bool, message: str):
    if debug:
        print(message)


def valid_vin(vin: str) -> bool:
    if len(vin) != 17:
        return False
    if any(ch in vin for ch in ["I", "O", "Q"]):
        return False
    return vin.isalnum()


def process_vins(df: pd.DataFrame,
                mot_token_manager: 'MotTokenManager',
                mot_api_key: str,
                ves_api_key: str,
                debug: bool = False,
                start_index: int = 0,
                existing_rows: Optional[List[Dict]] = None,
                checkpoint_every: int = CHECKPOINT_DEFAULT,
                output_path: Optional[str] = None,
                checkpoint_path: Optional[str] = None) -> pd.DataFrame:
    output_rows = existing_rows or []
    total = len(df)
    iterator = df.iterrows()
    if debug:
        iterator = tqdm(iterator, total=total, initial=start_index, desc="Processing VINs", unit="vin")
    for idx, row in iterator:
        if idx < start_index:
            continue
        vin = str(row.get("vin_number", "")).strip().upper()
        result = {
            "vin_number": vin,
            "registration": "",
            "fuel_type": "",
            "engine_size_cc": "",
            "co2_emissions_gkm": "",
            "success": False,
            "error": "",
        }
        if not vin:
            result["error"] = "Missing VIN"
            output_rows.append(result)
            continue
        if not valid_vin(vin):
            result["error"] = "Invalid VIN format"
            output_rows.append(result)
            continue
        try:
            token = mot_token_manager.get()
            registration = fetch_registration_from_mot(vin, mot_api_key, token, debug=debug)
            if not registration:
                result["error"] = "VIN not found in MOT"
                if debug:
                    tqdm.write(f"[MOT FAIL] {vin} -> no registration found")
                output_rows.append(result)
                time.sleep(DEFAULT_SLEEP)
                continue
            result["registration"] = registration
            try:
                vehicle = fetch_details_from_ves(registration, ves_api_key, debug=debug)
                if not vehicle:
                    # Preserve MOT-derived data; only CO2 unavailable
                    result["error"] = "Registration not found in VES"
                    if debug:
                        tqdm.write(f"[VES FAIL] {vin} ({registration}) -> not found")
                else:
                    result["fuel_type"] = vehicle.get("fuelType", "") or result["fuel_type"]
                    result["engine_size_cc"] = vehicle.get("engineCapacity", "") or result["engine_size_cc"]
                    result["co2_emissions_gkm"] = vehicle.get("co2Emissions", "")
                    result["success"] = True
            except ValueError as e:
                result["error"] = str(e)
                if debug:
                    tqdm.write(f"[VES FAIL] {vin} ({registration}) -> {e}")
        except ConfigError:
            raise
        except requests.exceptions.RequestException as e:
            result["error"] = f"Network/API error: {e}" if not result["error"] else result["error"]
        except (ValueError, KeyError, TypeError) as e:
            result["error"] = f"Parse error: {e}" if not result["error"] else result["error"]
        output_rows.append(result)
        time.sleep(DEFAULT_SLEEP)

        # Checkpointing
        if output_path and checkpoint_path and (len(output_rows) % checkpoint_every == 0):
            partial_df = pd.DataFrame(output_rows)
            try:
                partial_df.to_excel(output_path, index=False)
                with open(checkpoint_path, "w", encoding="utf-8") as f:
                    json.dump({"processed": len(output_rows)}, f)
                log_debug(debug, f"Checkpoint: wrote {len(output_rows)} rows to {output_path}")
            except Exception as e:
                log_debug(debug, f"Checkpoint write failed: {e}")

    return pd.DataFrame(output_rows)


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch vehicle details from VIN list (xlsx).")
    parser.add_argument("--input", required=True, help="Path to input .xlsx or .csv file (must have column 'vin_number').")
    default_output = os.path.join(os.path.expanduser("~"), "Downloads", "new_VINs.xlsx")
    parser.add_argument("--output", default=default_output, help="Path for output .xlsx file (default: ~/Downloads/new_VINs.xlsx).")
    parser.add_argument("--debug", action="store_true", help="Print progress for each VIN.")
    parser.add_argument("--mot-token-url", help="OAuth token endpoint to fetch MOT access code.")
    parser.add_argument("--mot-client-id", help="Client ID for MOT token fetch.")
    parser.add_argument("--mot-client-secret", help="Client secret for MOT token fetch.")
    parser.add_argument("--mot-scope", help="Scope for MOT token fetch.")
    parser.add_argument("--checkpoint-every", type=int, default=CHECKPOINT_DEFAULT, help="Rows between progress checkpoints.")
    parser.add_argument("--resume", action="store_true", help="Resume from existing checkpoint/output if present.")
    return parser.parse_args()


def fetch_mot_access_code_from_oauth(token_url: str, client_id: str, client_secret: str, scope: Optional[str]) -> str:
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope:
        data["scope"] = scope
    resp = requests.post(token_url, data=data, timeout=30)
    if resp.status_code in (401, 403):
        raise ConfigError("MOT OAuth credentials rejected (401/403).")
    resp.raise_for_status()
    body = resp.json()
    token = body.get("access_token")
    expires_in = body.get("expires_in")  # seconds
    if not token:
        raise ConfigError("Token fetch succeeded but no access_token found in response.")
    return token, expires_in


def load_cached_token() -> Optional[Tuple[str, Optional[float]]]:
    if not os.path.exists(TOKEN_CACHE_PATH):
        return None
    try:
        with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("token"), data.get("expires_at")
    except Exception:
        return None


def save_cached_token(token: str, expires_in: Optional[int]) -> None:
    expires_at = None
    if expires_in:
        expires_at = time.time() + expires_in - 30  # 30s safety margin
    payload = {"token": token, "expires_at": expires_at}
    with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def get_mot_access_code(args, mot_api_key: str, mot_access_code_env: Optional[str], debug: bool) -> Tuple[str, Optional[float]]:
    # 1) Env token wins if provided (user might have just pasted a fresh one)
    if mot_access_code_env:
        log_debug(debug, "Using MOT access code from MOT_ACCESS_CODE env var.")
        return mot_access_code_env, None

    # 2) Try cached token
    cached = load_cached_token()
    if cached:
        token, expires_at = cached
        if token and (not expires_at or time.time() < expires_at):
            log_debug(debug, "Using cached MOT access code.")
            return token, expires_at
        log_debug(debug, "Cached MOT access code expired; fetching a new one.")

    # 3) Fetch a new token via OAuth
    token_url = args.mot_token_url or os.getenv("MOT_TOKEN_URL")
    client_id = args.mot_client_id or os.getenv("MOT_CLIENT_ID")
    client_secret = args.mot_client_secret or os.getenv("MOT_CLIENT_SECRET")
    scope = args.mot_scope or os.getenv("MOT_SCOPE")
    if not all([token_url, client_id, client_secret]):
        raise ConfigError("MOT access token missing and OAuth details not provided (token_url, client_id, client_secret).")
    token, expires_in = fetch_mot_access_code_from_oauth(token_url, client_id, client_secret, scope)
    save_cached_token(token, expires_in)
    log_debug(debug, "Fetched new MOT access code via OAuth.")
    expires_at = time.time() + expires_in - 30 if expires_in else None
    return token, expires_at


class MotTokenManager:
    def __init__(self, initial_token: str, expires_at: Optional[float], refresh_fn, debug: bool):
        self.token = initial_token
        self.expires_at = expires_at
        self.refresh_fn = refresh_fn
        self.debug = debug

    def get(self) -> str:
        if self.expires_at and time.time() > self.expires_at - 600:  # refresh when <10 min left
            log_debug(self.debug, "MOT token nearing expiry; refreshing.")
            self.refresh()
        return self.token

    def refresh(self):
        token, expires_at = self.refresh_fn()
        self.token = token
        self.expires_at = expires_at
        log_debug(self.debug, "MOT token refreshed.")


def main():
    start_time = time.time()
    args = parse_args()
    try:
        mot_api_key, mot_access_code_env, ves_api_key = load_config()
        mot_access_code, expires_at = get_mot_access_code(args, mot_api_key, mot_access_code_env, debug=args.debug)
        def refresh_fn():
            token, exp = get_mot_access_code(args, mot_api_key, None, debug=args.debug)
            return token, exp
        mot_token_manager = MotTokenManager(mot_access_code, expires_at, refresh_fn, debug=args.debug)
        try:
            check_mot_key(mot_api_key, mot_token_manager.get())
        except ConfigError:
            # If token came from cache, try refreshing once
            if args.debug:
                print("MOT key check failed; attempting to fetch a new access code and retry.")
            mot_token_manager.refresh()
            check_mot_key(mot_api_key, mot_token_manager.get())
        check_ves_key(ves_api_key)
    except ConfigError as e:
        sys.stderr.write(f"Config error: {e}\n")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Auth check failed due to network/API error: {e}\n")
        sys.exit(1)

    try:
        if args.input.lower().endswith(".csv"):
            log_debug(args.debug, f"Reading CSV input: {args.input}")
            df = pd.read_csv(args.input)
        else:
            log_debug(args.debug, f"Reading Excel input: {args.input}")
            df = pd.read_excel(args.input)
    except Exception as e:
        sys.stderr.write(f"Failed to read input file: {e}\n")
        sys.exit(1)

    if "vin_number" not in df.columns:
        sys.stderr.write("Input file must contain a 'vin_number' column.\n")
        sys.exit(1)

    # Resume support
    existing_rows = None
    start_index = 0
    checkpoint_path = f"{args.output}.checkpoint.json"
    if args.resume and os.path.exists(args.output) and os.path.exists(checkpoint_path):
        try:
            partial_df = pd.read_excel(args.output)
            existing_rows = partial_df.to_dict(orient="records")
            start_index = len(existing_rows)
            log_debug(args.debug, f"Resuming from checkpoint at row {start_index}.")
        except Exception as e:
            log_debug(args.debug, f"Failed to load checkpoint, starting from scratch: {e}")

    log_debug(args.debug, f"Loaded {len(df)} VIN rows. Starting processing…")
    try:
        result_df = process_vins(
            df=df,
            mot_token_manager=mot_token_manager,
            mot_api_key=mot_api_key,
            ves_api_key=ves_api_key,
            debug=args.debug,
            start_index=start_index,
            existing_rows=existing_rows,
            checkpoint_every=args.checkpoint_every,
            output_path=args.output,
            checkpoint_path=checkpoint_path,
        )
    except ConfigError as e:
        sys.stderr.write(f"Run aborted: {e}\n")
        sys.exit(1)

    success_count = result_df[result_df["success"]].shape[0]
    error_count = len(result_df) - success_count
    if args.debug:
        print(f"Finished processing. Success: {success_count}, Errors: {error_count}")

    try:
        result_df.to_excel(args.output, index=False)
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
        elapsed = time.time() - start_time
        mins, secs = divmod(int(elapsed), 60)
        print(f"Wrote output to {args.output}")
        print(f"Total run time: {mins}m {secs}s")
    except Exception as e:
        sys.stderr.write(f"Failed to write output file: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
