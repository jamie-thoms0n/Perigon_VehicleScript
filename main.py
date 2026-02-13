#!/usr/bin/env python3
"""
VIN → MOT (reg lookup) → VES (vehicle details)
Usage:
    python main.py --input VINs_2025.xlsx --output new_VINs_2025.xlsx
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
from typing import Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv

# Constants
MOT_BASE = "https://history.mot.api.gov.uk/v1/trade"
VES_URL = "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles"
DEFAULT_SLEEP = 0.2  # seconds between calls to avoid hammering the APIs


class ConfigError(Exception):
    """Raised when configuration or credentials are missing/invalid."""


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
    resp = requests.post(VES_URL, headers=headers, data=payload)
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
    resp = requests.get(url, headers=headers)
    if resp.status_code in (401, 403):
        raise ConfigError("MOT credentials rejected (401/403). Refresh MOT_ACCESS_CODE or MOT_API_KEY.")


def fetch_registration_from_mot(vin: str, mot_api_key: str, mot_access_code: str) -> Optional[str]:
    headers = {
        "Authorization": f"Bearer {mot_access_code}",
        "X-API-Key": mot_api_key,
        "Accept": "application/json",
    }
    url = f"{MOT_BASE}/vehicles/vin/{vin}"
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 404:
        return None  # VIN not found
    if resp.status_code in (401, 403):
        raise ConfigError("MOT credentials expired or invalid during run. Refresh and retry.")
    resp.raise_for_status()
    data = resp.json()
    # API can return list or object; handle both defensively
    if isinstance(data, list) and data:
        entry = data[0]
    elif isinstance(data, dict):
        entry = data
    else:
        return None
    return entry.get("registration")


def fetch_details_from_ves(registration: str, ves_api_key: str) -> Optional[dict]:
    headers = {"x-api-key": ves_api_key, "Content-Type": "application/json"}
    payload = json.dumps({"registrationNumber": registration})
    resp = requests.post(VES_URL, headers=headers, data=payload, timeout=30)
    if resp.status_code in (401, 403):
        raise ConfigError("VES_API_KEY invalid or expired during run. Refresh and retry.")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def valid_vin(vin: str) -> bool:
    if len(vin) != 17:
        return False
    if any(ch in vin for ch in ["I", "O", "Q"]):
        return False
    return vin.isalnum()


def process_vins(df: pd.DataFrame, mot_api_key: str, mot_access_code: str, ves_api_key: str, debug: bool = False) -> pd.DataFrame:
    output_rows = []
    total = len(df)
    for idx, row in df.iterrows():
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
        if debug:
            print(f"[{idx + 1}/{total}] VIN {vin}: querying MOT…")
        try:
            registration = fetch_registration_from_mot(vin, mot_api_key, mot_access_code)
            if debug:
                print(f"    MOT registration: {registration or 'not found'}")
            if not registration:
                result["error"] = "VIN not found in MOT"
                output_rows.append(result)
                time.sleep(DEFAULT_SLEEP)
                continue
            result["registration"] = registration
            vehicle = fetch_details_from_ves(registration, ves_api_key)
            if debug:
                print(f"    VES lookup: {'found' if vehicle else 'not found'}")
            if not vehicle:
                result["error"] = "Registration not found in VES"
            else:
                result["fuel_type"] = vehicle.get("fuelType", "")
                result["engine_size_cc"] = vehicle.get("engineCapacity", "")
                result["co2_emissions_gkm"] = vehicle.get("co2Emissions", "")
                result["success"] = True
        except ConfigError:
            raise
        except requests.exceptions.RequestException as e:
            result["error"] = f"Network/API error: {e}" if not result["error"] else result["error"]
        except (ValueError, KeyError, TypeError) as e:
            result["error"] = f"Parse error: {e}" if not result["error"] else result["error"]
        output_rows.append(result)
        time.sleep(DEFAULT_SLEEP)
    return pd.DataFrame(output_rows)


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch vehicle details from VIN list (xlsx).")
    parser.add_argument("--input", required=True, help="Path to input .xlsx file (must have column 'vin_number').")
    parser.add_argument("--output", required=True, help="Path for output .xlsx file.")
    parser.add_argument("--debug", action="store_true", help="Print progress for each VIN.")
    parser.add_argument("--mot-token-url", help="OAuth token endpoint to fetch MOT access code.")
    parser.add_argument("--mot-client-id", help="Client ID for MOT token fetch.")
    parser.add_argument("--mot-client-secret", help="Client secret for MOT token fetch.")
    parser.add_argument("--mot-scope", help="Scope for MOT token fetch.")
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
    token = resp.json().get("access_token")
    if not token:
        raise ConfigError("Token fetch succeeded but no access_token found in response.")
    return token


def main():
    args = parse_args()
    try:
        mot_api_key, mot_access_code, ves_api_key = load_config()
        # Optionally fetch MOT access token if not provided or overridden via CLI
        if args.mot_token_url and args.mot_client_id and args.mot_client_secret:
            mot_access_code = fetch_mot_access_code_from_oauth(
                token_url=args.mot_token_url,
                client_id=args.mot_client_id,
                client_secret=args.mot_client_secret,
                scope=args.mot_scope,
            )
        if not mot_access_code:
            raise ConfigError("MOT_ACCESS_CODE missing. Provide in .env or via OAuth flags.")
        check_mot_key(mot_api_key, mot_access_code)
        check_ves_key(ves_api_key)
    except ConfigError as e:
        sys.stderr.write(f"Config error: {e}\n")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Auth check failed due to network/API error: {e}\n")
        sys.exit(1)

    try:
        df = pd.read_excel(args.input)
    except Exception as e:
        sys.stderr.write(f"Failed to read input file: {e}\n")
        sys.exit(1)

    if "vin_number" not in df.columns:
        sys.stderr.write("Input file must contain a 'vin_number' column.\n")
        sys.exit(1)

    try:
        result_df = process_vins(df, mot_api_key, mot_access_code, ves_api_key, debug=args.debug)
    except ConfigError as e:
        sys.stderr.write(f"Run aborted: {e}\n")
        sys.exit(1)

    try:
        result_df.to_excel(args.output, index=False)
        print(f"Wrote output to {args.output}")
    except Exception as e:
        sys.stderr.write(f"Failed to write output file: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
