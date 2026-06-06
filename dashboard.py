#!/usr/bin/env python3
"""
Operation Phoenix - Peptide Protocol Dashboard
Ipamorelin + Tesamorelin Protocol Tracker
Deployed on Streamlit Community Cloud
"""

import streamlit as st
import json
import os
import urllib.parse
import hashlib
import requests
import pandas as pd
from datetime import datetime, timedelta, date

# ============================================================
# AUTHENTICATION - Password gate
# ============================================================
def check_password():
    """Returns True if the user entered the correct password."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.set_page_config(page_title="Operation Phoenix", page_icon="\U0001f525", layout="centered")

    st.markdown("""
    <style>
        .login-container { max-width: 400px; margin: 100px auto; text-align: center; }
        .stTextInput > div > div > input { text-align: center; }
    </style>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("## \U0001f525 Operation Phoenix")
        st.markdown("*Peptide Protocol Tracker*")
        st.divider()

        password = st.text_input("Enter password:", type="password", placeholder="Password")

        if st.button("Access Dashboard", type="primary", use_container_width=True):
            stored_hash = st.secrets.get("APP_PASSWORD_HASH", "")
            input_hash = hashlib.sha256(password.encode()).hexdigest()

            if input_hash == stored_hash:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")

        st.caption("This is a private health tracker.")

    return False


if not check_password():
    st.stop()

# ============================================================
# PAGE CONFIG (after auth)
# ============================================================
# Only set page config if not already set by login screen
try:
    st.set_page_config(page_title="Operation Phoenix", page_icon="\U0001f525", layout="wide", initial_sidebar_state="expanded")
except:
    pass

# ============================================================
# CONFIG & SECRETS
# ============================================================
CLIENT_ID = st.secrets["WHOOP_CLIENT_ID"]
CLIENT_SECRET = st.secrets["WHOOP_CLIENT_SECRET"]
REDIRECT_URI = "https://whoop.com/callback"
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE = "https://api.prod.whoop.com/developer"
SCOPES = "read:recovery read:cycles read:sleep read:workout read:profile read:body_measurement"

PROTOCOL_START = date(2026, 5, 11)
PROTOCOL_END = date(2026, 7, 10)
BASELINE_START = date(2026, 5, 4)

WEEKS = [
    ("Baseline", date(2026, 5, 4), date(2026, 5, 10)),
    ("Week 1", date(2026, 5, 11), date(2026, 5, 17)),
    ("Week 2", date(2026, 5, 18), date(2026, 5, 24)),
    ("Week 3", date(2026, 5, 25), date(2026, 5, 31)),
    ("Week 4", date(2026, 6, 1), date(2026, 6, 7)),
    ("Week 5", date(2026, 6, 8), date(2026, 6, 14)),
    ("Week 6", date(2026, 6, 15), date(2026, 6, 21)),
    ("Week 7", date(2026, 6, 22), date(2026, 6, 28)),
    ("Week 8", date(2026, 6, 29), date(2026, 7, 5)),
    ("Week 9", date(2026, 7, 6), date(2026, 7, 12)),
]

INJECTION_DAYS = []
current = PROTOCOL_START
while current <= PROTOCOL_END:
    if current.weekday() < 5:
        INJECTION_DAYS.append(current)
    current += timedelta(days=1)

MEASUREMENT_SITES = [
    "Waist (at navel)", "Waist (narrowest)", "Hips (widest)",
    "Left Thigh", "Right Thigh", "Chest",
    "Left Arm (flexed)", "Right Arm (flexed)",
]

# ============================================================
# DATA PERSISTENCE - Google Sheets + Google Drive Backend
# ============================================================
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
import base64

SHEET_NAME = st.secrets.get("GOOGLE_SHEET_NAME", "Operation Phoenix Data")

@st.cache_resource(ttl=300)
def get_google_creds():
    """Create Google credentials (shared by Sheets and Drive)."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )

@st.cache_resource(ttl=300)
def get_gsheet_client():
    """Create authenticated Google Sheets client."""
    return gspread.authorize(get_google_creds())

@st.cache_resource(ttl=300)
def get_drive_service():
    """Create authenticated Google Drive service."""
    return build("drive", "v3", credentials=get_google_creds())


def get_or_create_drive_folder(folder_name, parent_id=None):
    """Get or create a Google Drive folder, return its ID."""
    drive = get_drive_service()
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = drive.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    # Create folder
    metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = drive.files().create(body=metadata, fields="id").execute()
    # Share with user's Google account so they can see it too
    try:
        drive.permissions().create(
            fileId=folder["id"],
            body={"type": "user", "role": "writer", "emailAddress": "guillermoreynag@gmail.com"},
            sendNotificationEmail=False,
        ).execute()
    except:
        pass
    return folder["id"]


def upload_photo_to_drive(file_data, filename, week_name):
    """Upload a photo to Google Drive in the week's folder. Returns file ID and web link."""
    drive = get_drive_service()
    root_id = get_or_create_drive_folder("Operation Phoenix Photos")
    week_id = get_or_create_drive_folder(week_name, parent_id=root_id)

    # Check if file already exists (same name in same folder) and delete it
    query = f"name='{filename}' and '{week_id}' in parents and trashed=false"
    existing = drive.files().list(q=query, spaces="drive", fields="files(id)").execute().get("files", [])
    for f in existing:
        drive.files().delete(fileId=f["id"]).execute()

    # Upload
    media = MediaIoBaseUpload(io.BytesIO(file_data), mimetype="image/jpeg", resumable=True)
    metadata = {"name": filename, "parents": [week_id]}
    uploaded = drive.files().create(body=metadata, media_body=media, fields="id, webViewLink, webContentLink").execute()
    # Make viewable by anyone with link
    try:
        drive.permissions().create(
            fileId=uploaded["id"],
            body={"type": "anyone", "role": "reader"},
        ).execute()
    except:
        pass
    return uploaded["id"], uploaded.get("webViewLink", "")


def get_photo_url(file_id):
    """Get a thumbnail URL for a Drive file."""
    if not file_id:
        return None
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w400"


def get_week_photos(week_name):
    """Get all photos for a given week from Google Sheets metadata."""
    meta = get_all_meta()
    photos = {}
    for view in ["Front", "Side", "Back"]:
        key = f"photo_{week_name}_{view}"
        file_id = meta.get(key, "")
        if file_id:
            photos[view] = file_id
    return photos


def save_photo_meta(week_name, view, file_id):
    """Save photo file ID to metadata."""
    try:
        ss = get_spreadsheet()
        ws_meta = get_or_create_worksheet(ss, "Meta", META_HEADERS)
        existing_meta = {}
        for row in ws_meta.get_all_records():
            if row.get("key"):
                existing_meta[row["key"]] = row.get("value", "")
        existing_meta[f"photo_{week_name}_{view}"] = file_id
        new_rows = [META_HEADERS]
        for k, v in sorted(existing_meta.items()):
            new_rows.append([k, str(v)])
        ws_meta.clear()
        ws_meta.update("A1", new_rows)
    except Exception as e:
        st.warning(f"Could not save photo metadata: {e}")


def get_or_create_worksheet(spreadsheet, title, headers):
    """Get a worksheet by title, or create it with headers if it doesn't exist."""
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=200, cols=len(headers))
        ws.update("A1", [headers])
        ws.format("A1:{}1".format(chr(64 + len(headers))), {"textFormat": {"bold": True}})
    return ws


def get_spreadsheet():
    """Get the Operation Phoenix spreadsheet (cached in session)."""
    if "gsheet" not in st.session_state:
        gc = get_gsheet_client()
        try:
            st.session_state.gsheet = gc.open(SHEET_NAME)
        except gspread.SpreadsheetNotFound:
            st.error(f"Google Sheet '{SHEET_NAME}' not found. Make sure it's shared with the service account email.")
            st.stop()
    return st.session_state.gsheet


# --- Checkin Data ---
CHECKIN_HEADERS = ["week", "date", "weight", "body_fat", "measurements_json", "subjective_json", "photos_json", "notes", "saved_at"]
INJECTION_HEADERS = ["date", "done"]
WHOOP_HEADERS = ["date", "recovery", "hrv", "rhr", "spo2", "sleep_perf", "sleep_hrs", "resp_rate", "strain", "calories_kj", "workouts_json"]
META_HEADERS = ["key", "value"]


def load_checkin_data():
    """Load all check-in and injection data from Google Sheets."""
    if "checkin_data" in st.session_state:
        return st.session_state.checkin_data

    data = {"checkins": {}, "injections": {}}
    try:
        ss = get_spreadsheet()

        # Load check-ins
        ws = get_or_create_worksheet(ss, "Checkins", CHECKIN_HEADERS)
        rows = ws.get_all_records()
        for row in rows:
            week = row.get("week", "")
            if not week:
                continue
            entry = {
                "date": row.get("date", ""),
                "weight": float(row["weight"]) if row.get("weight") else None,
                "body_fat": float(row["body_fat"]) if row.get("body_fat") else None,
                "notes": row.get("notes", ""),
                "saved_at": row.get("saved_at", ""),
            }
            try:
                entry["measurements"] = json.loads(row.get("measurements_json", "{}"))
            except:
                entry["measurements"] = {}
            try:
                entry["subjective"] = json.loads(row.get("subjective_json", "{}"))
            except:
                entry["subjective"] = {}
            try:
                entry["photos"] = json.loads(row.get("photos_json", "{}"))
            except:
                entry["photos"] = {}
            data["checkins"][week] = entry

        # Load injections
        ws_inj = get_or_create_worksheet(ss, "Injections", INJECTION_HEADERS)
        inj_rows = ws_inj.get_all_records()
        for row in inj_rows:
            if row.get("date"):
                data["injections"][row["date"]] = str(row.get("done", "")).upper() == "TRUE"

    except Exception as e:
        st.warning(f"Could not load data from Google Sheets: {e}")

    st.session_state.checkin_data = data
    return data


def save_checkin_data(data):
    """Save check-in and injection data to Google Sheets."""
    st.session_state.checkin_data = data
    try:
        ss = get_spreadsheet()

        # Save check-ins
        ws = get_or_create_worksheet(ss, "Checkins", CHECKIN_HEADERS)
        rows = [CHECKIN_HEADERS]
        for week_name in ["Baseline"] + [f"Week {i}" for i in range(1, 10)]:
            entry = data.get("checkins", {}).get(week_name)
            if entry:
                rows.append([
                    week_name,
                    entry.get("date", ""),
                    entry.get("weight", ""),
                    entry.get("body_fat", "") if entry.get("body_fat") else "",
                    json.dumps(entry.get("measurements", {})),
                    json.dumps(entry.get("subjective", {})),
                    json.dumps(entry.get("photos", {})),
                    entry.get("notes", ""),
                    entry.get("saved_at", ""),
                ])
        ws.clear()
        ws.update("A1", rows)
        if len(rows) > 0:
            ws.format("A1:{}1".format(chr(64 + len(CHECKIN_HEADERS))), {"textFormat": {"bold": True}})

        # Save injections
        ws_inj = get_or_create_worksheet(ss, "Injections", INJECTION_HEADERS)
        inj_rows = [INJECTION_HEADERS]
        for d_str, done in sorted(data.get("injections", {}).items()):
            inj_rows.append([d_str, str(done).upper()])
        ws_inj.clear()
        ws_inj.update("A1", inj_rows)
        if len(inj_rows) > 0:
            ws_inj.format("A1:B1", {"textFormat": {"bold": True}})

    except Exception as e:
        st.warning(f"Could not save to Google Sheets: {e}")


def load_whoop_data():
    """Load Whoop data from Google Sheets."""
    if "whoop_data" in st.session_state:
        return st.session_state.whoop_data

    data = {}
    try:
        ss = get_spreadsheet()

        # Load daily data
        ws = get_or_create_worksheet(ss, "Whoop Daily", WHOOP_HEADERS)
        rows = ws.get_all_records()
        daily = {}
        for row in rows:
            ds = row.get("date", "")
            if not ds:
                continue
            entry = {}
            for key in ["recovery", "hrv", "rhr", "spo2", "sleep_perf", "sleep_hrs", "resp_rate", "strain", "calories_kj"]:
                v = row.get(key)
                if v != "" and v is not None:
                    try:
                        entry[key] = float(v)
                    except:
                        pass
            try:
                wk = json.loads(row.get("workouts_json", "[]"))
                if wk:
                    entry["workouts"] = wk
            except:
                pass
            daily[ds] = entry

        if daily:
            data["daily"] = daily

        # Load metadata (last_pull, etc.)
        ws_meta = get_or_create_worksheet(ss, "Meta", META_HEADERS)
        meta_rows = ws_meta.get_all_records()
        for row in meta_rows:
            if row.get("key") == "whoop_last_pull":
                data["last_pull"] = row.get("value", "")

    except Exception as e:
        st.warning(f"Could not load Whoop data: {e}")

    st.session_state.whoop_data = data
    return data


def save_whoop_data(data):
    """Save Whoop data to Google Sheets."""
    st.session_state.whoop_data = data
    try:
        ss = get_spreadsheet()

        # Save daily data
        ws = get_or_create_worksheet(ss, "Whoop Daily", WHOOP_HEADERS)
        rows = [WHOOP_HEADERS]
        for ds in sorted(data.get("daily", {}).keys()):
            d = data["daily"][ds]
            rows.append([
                ds,
                d.get("recovery", ""),
                round(d["hrv"], 1) if d.get("hrv") is not None else "",
                d.get("rhr", ""),
                d.get("spo2", ""),
                d.get("sleep_perf", ""),
                d.get("sleep_hrs", ""),
                d.get("resp_rate", ""),
                round(d["strain"], 1) if d.get("strain") is not None else "",
                d.get("calories_kj", ""),
                json.dumps(d.get("workouts", [])) if d.get("workouts") else "",
            ])
        ws.clear()
        ws.update("A1", rows)
        if len(rows) > 0:
            ws.format("A1:{}1".format(chr(64 + len(WHOOP_HEADERS))), {"textFormat": {"bold": True}})

        # Save metadata (merge, don't overwrite)
        ws_meta = get_or_create_worksheet(ss, "Meta", META_HEADERS)
        existing_meta = {}
        for row in ws_meta.get_all_records():
            if row.get("key"):
                existing_meta[row["key"]] = row.get("value", "")
        if data.get("last_pull"):
            existing_meta["whoop_last_pull"] = data["last_pull"]
        meta_rows = [META_HEADERS]
        for k, v in sorted(existing_meta.items()):
            meta_rows.append([k, str(v)])
        ws_meta.clear()
        ws_meta.update("A1", meta_rows)

    except Exception as e:
        st.warning(f"Could not save Whoop data: {e}")


# ============================================================
# WHOOP API
# ============================================================
def get_all_meta():
    """Get all metadata from Google Sheets as a dict."""
    meta = {}
    try:
        ss = get_spreadsheet()
        ws_meta = get_or_create_worksheet(ss, "Meta", META_HEADERS)
        for row in ws_meta.get_all_records():
            if row.get("key"):
                meta[row["key"]] = row.get("value", "")
    except:
        pass
    return meta


def get_saved_token():
    """Get saved Whoop access token, auto-refreshing if expired."""
    meta = get_all_meta()
    access_token = meta.get("whoop_access_token")
    refresh_token = meta.get("whoop_refresh_token")
    saved_at = meta.get("whoop_token_saved_at", "")

    if not access_token:
        return None

    # Check if token is older than 50 minutes (expires at 60)
    token_expired = False
    if saved_at:
        try:
            saved_time = datetime.fromisoformat(saved_at)
            if (datetime.now() - saved_time).total_seconds() > 3000:
                token_expired = True
        except:
            token_expired = True

    # Try to refresh if expired and we have a refresh token
    if token_expired and refresh_token:
        new_token = refresh_access_token(refresh_token)
        if new_token:
            return new_token

    return access_token


def refresh_access_token(refresh_token):
    """Use refresh token to get a new access token."""
    try:
        resp = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })
        if resp.status_code == 200:
            token_data = resp.json()
            save_token(token_data)
            return token_data.get("access_token")
    except:
        pass
    return None


def save_token(token_data):
    """Save Whoop token to Google Sheets."""
    try:
        ss = get_spreadsheet()
        ws_meta = get_or_create_worksheet(ss, "Meta", META_HEADERS)
        rows = ws_meta.get_all_records()

        # Build updated meta rows
        meta = {}
        for row in rows:
            if row.get("key"):
                meta[row["key"]] = row.get("value", "")

        meta["whoop_access_token"] = token_data.get("access_token", "")
        meta["whoop_token_saved_at"] = datetime.now().isoformat()
        if token_data.get("refresh_token"):
            meta["whoop_refresh_token"] = token_data["refresh_token"]

        new_rows = [META_HEADERS]
        for k, v in sorted(meta.items()):
            new_rows.append([k, str(v)])
        ws_meta.clear()
        ws_meta.update("A1", new_rows)
    except Exception as e:
        st.warning(f"Could not save token: {e}")


def exchange_code_for_token(code):
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
    })
    if resp.status_code == 200:
        token_data = resp.json()
        save_token(token_data)
        return token_data.get("access_token")
    return None


def build_auth_url():
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": "dashboard",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def fetch_all_pages(endpoint, token, start_date=None, end_date=None):
    headers = {"Authorization": f"Bearer {token}"}
    all_records = []
    params = {"limit": 25}
    if start_date:
        params["start"] = f"{start_date}T00:00:00.000Z"
    if end_date:
        params["end"] = f"{end_date}T23:59:59.999Z"
    url = f"{API_BASE}{endpoint}"
    while True:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            records = data.get("records", [])
            all_records.extend(records)
            next_token = data.get("next_token")
            if not next_token:
                break
            params["nextToken"] = next_token
        except:
            break
    return all_records


def millis_to_hours(ms):
    return round(ms / 3600000, 1) if ms else None


def pull_whoop_data(token, start_str, end_str):
    daily = {}

    # Recovery (v2)
    for r in fetch_all_pages("/v2/recovery", token, start_str, end_str):
        if r.get("score_state") != "SCORED" or not r.get("score"):
            continue
        ds = r.get("created_at", "")[:10]
        if not ds:
            continue
        daily.setdefault(ds, {})
        s = r["score"]
        daily[ds]["recovery"] = s.get("recovery_score")
        hrv = s.get("hrv_rmssd_milli")
        daily[ds]["hrv"] = round(hrv, 1) if hrv else None
        daily[ds]["rhr"] = s.get("resting_heart_rate")
        daily[ds]["spo2"] = s.get("spo2_percentage")

    # Sleep (v2)
    for sl in fetch_all_pages("/v2/activity/sleep", token, start_str, end_str):
        if sl.get("nap") or sl.get("score_state") != "SCORED" or not sl.get("score"):
            continue
        ds = sl.get("end", sl.get("created_at", ""))[:10]
        if not ds:
            continue
        daily.setdefault(ds, {})
        sc = sl["score"]
        daily[ds]["sleep_perf"] = sc.get("sleep_performance_percentage")
        stage = sc.get("stage_summary", {})
        total_ms = sum(stage.get(k, 0) or 0 for k in [
            "total_light_sleep_time_milli", "total_slow_wave_sleep_time_milli", "total_rem_sleep_time_milli"
        ])
        daily[ds]["sleep_hrs"] = millis_to_hours(total_ms)
        daily[ds]["resp_rate"] = sc.get("respiratory_rate")

    # Cycles (v2)
    for c in fetch_all_pages("/v2/cycle", token, start_str, end_str):
        if c.get("score_state") != "SCORED" or not c.get("score"):
            continue
        ds = c.get("start", c.get("created_at", ""))[:10]
        if not ds:
            continue
        daily.setdefault(ds, {})
        daily[ds]["strain"] = c["score"].get("strain")
        daily[ds]["calories_kj"] = c["score"].get("kilojoule")

    # Workouts (v2)
    n_workouts = 0
    for w in fetch_all_pages("/v2/activity/workout", token, start_str, end_str):
        n_workouts += 1
        if w.get("score_state") != "SCORED" or not w.get("score"):
            continue
        ds = w.get("start", w.get("created_at", ""))[:10]
        if not ds:
            continue
        daily.setdefault(ds, {})
        daily[ds].setdefault("workouts", []).append({
            "strain": w["score"].get("strain"),
            "calories_kj": w["score"].get("kilojoule"),
        })

    return daily


def compute_weekly_averages(daily_data):
    results = []
    for name, w_start, w_end in WEEKS:
        vals = {"recovery": [], "hrv": [], "rhr": [], "sleep": [], "strain": []}
        current = w_start
        while current <= w_end:
            ds = current.isoformat()
            if ds in daily_data:
                d = daily_data[ds]
                for k in vals:
                    v = d.get(k if k != "sleep" else "sleep_hrs")
                    if v is not None:
                        vals[k].append(v)
            current += timedelta(days=1)
        avg = lambda lst: round(sum(lst) / len(lst), 1) if lst else None
        results.append({
            "Week": name,
            "Dates": f"{w_start.strftime('%b %d')} - {w_end.strftime('%b %d')}",
            "Recovery %": avg(vals["recovery"]),
            "HRV (ms)": avg(vals["hrv"]),
            "RHR (bpm)": avg(vals["rhr"]),
            "Sleep (hrs)": avg(vals["sleep"]),
            "Strain": avg(vals["strain"]),
        })
    return results


# ============================================================
# CUSTOM CSS
# ============================================================
st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: 700; color: #2C3E50; margin-bottom: 0; }
    .sub-header { font-size: 1rem; color: #7f8c8d; margin-top: -10px; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { padding: 10px 20px; }
    section[data-testid="stSidebar"] { background-color: #1a1a2e; }
    section[data-testid="stSidebar"] .stMarkdown { color: #e0e0e0; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown("### \U0001f525 Operation Phoenix")
    st.markdown("**Ipamorelin + Tesamorelin**")
    st.markdown("Dr. Peter Martinez-Noda DO")
    st.divider()

    today = date.today()
    if today < PROTOCOL_START:
        days_until = (PROTOCOL_START - today).days
        st.info(f"Protocol starts in **{days_until} days**")
    elif today <= PROTOCOL_END:
        days_in = (today - PROTOCOL_START).days + 1
        total_days = (PROTOCOL_END - PROTOCOL_START).days + 1
        pct = int(days_in / total_days * 100)
        st.progress(pct / 100, text=f"Day {days_in} of {total_days}")
        for name, ws, we in WEEKS:
            if ws <= today <= we:
                st.success(f"Currently: **{name}**")
                break
    else:
        st.success("Protocol Complete!")

    st.divider()
    st.markdown("**Quick Stats**")
    st.markdown("Age: 32 | Height: 5'7\"")
    st.markdown("Start weight: 82 kg")
    st.markdown("Injections: Mon-Fri @ 9PM")
    st.markdown("Total injections: 45")

    st.divider()
    if st.button("Logout", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()

# ============================================================
# MAIN CONTENT
# ============================================================
st.markdown('<p class="main-header">Operation Phoenix</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Peptide Protocol Tracker | May 11 - July 10, 2026</p>', unsafe_allow_html=True)

# ============================================================
# AUTO-PULL WHOOP DATA (runs once per session, if last pull > 12 hours)
# ============================================================
if "auto_pull_done" not in st.session_state:
    st.session_state.auto_pull_done = False

if not st.session_state.auto_pull_done:
    st.session_state.auto_pull_done = True
    try:
        token = get_saved_token()
        if token:
            wd = load_whoop_data()
            last_pull = wd.get("last_pull", "")
            should_pull = True
            if last_pull:
                try:
                    last_time = datetime.fromisoformat(last_pull)
                    hours_ago = (datetime.now() - last_time).total_seconds() / 3600
                    if hours_ago < 12:
                        should_pull = False
                except:
                    pass
            if should_pull:
                start_str = BASELINE_START.isoformat()
                end_str = (PROTOCOL_END + timedelta(days=3)).isoformat()
                daily = pull_whoop_data(token, start_str, end_str)
                if daily:
                    save_whoop_data({"daily": daily, "last_pull": datetime.now().isoformat()})
                    # Clear cached whoop data so it reloads
                    if "whoop_data" in st.session_state:
                        del st.session_state["whoop_data"]
    except:
        pass

tab_overview, tab_whoop, tab_checkin, tab_injections, tab_supplements, tab_training, tab_nutrition, tab_dexa, tab_measurements = st.tabs([
    "\U0001f4ca Overview", "\U0001f4f1 Whoop Data", "\U0001f4cb Weekly Check-in", "\U0001f489 Injection Log", "\U0001f48a Supplement Stack", "\U0001f3cb️ Training", "\U0001f372 Nutrition", "\U0001f9b4 DEXA Baseline", "\U0001f4cf Measurements"
])

# ============================================================
# TAB: OVERVIEW
# ============================================================
with tab_overview:
    st.subheader("Protocol Overview")

    checkin_data = load_checkin_data()
    whoop_data = load_whoop_data()

    completed_injections = sum(1 for d in INJECTION_DAYS if checkin_data.get("injections", {}).get(d.isoformat()))

    latest_weight = 82.0
    for wk in reversed(["Baseline"] + [f"Week {i}" for i in range(1, 10)]):
        w = checkin_data.get("checkins", {}).get(wk, {}).get("weight")
        if w and w > 0:
            latest_weight = w
            break

    weeks_done = sum(1 for _, _, we in WEEKS if today > we)
    checkins_done = len(checkin_data.get("checkins", {}))

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Injections Done", f"{completed_injections}/45")
    col2.metric("Latest Weight", f"{latest_weight} kg")
    col3.metric("Weeks Complete", f"{weeks_done}/9")
    col4.metric("Check-ins Done", f"{checkins_done}/10")

    st.divider()

    if whoop_data.get("daily"):
        st.subheader("Whoop Weekly Averages")
        weekly_avg = compute_weekly_averages(whoop_data["daily"])
        st.dataframe(pd.DataFrame(weekly_avg), use_container_width=True, hide_index=True)

        st.subheader("Trends")
        chart_data = pd.DataFrame(weekly_avg).set_index("Week")
        col_a, col_b = st.columns(2)
        with col_a:
            for col_name in ["Recovery %", "HRV (ms)"]:
                if chart_data[col_name].notna().any():
                    st.line_chart(chart_data[[col_name]])
        with col_b:
            for col_name in ["Sleep (hrs)", "Strain"]:
                if chart_data[col_name].notna().any():
                    st.line_chart(chart_data[[col_name]])

    if checkin_data.get("checkins"):
        st.subheader("Body Measurement Trends")
        rows = []
        for wk in ["Baseline"] + [f"Week {i}" for i in range(1, 10)]:
            cd = checkin_data.get("checkins", {}).get(wk, {})
            if cd:
                row = {"Week": wk}
                if cd.get("weight"):
                    row["Weight (kg)"] = cd["weight"]
                for site in MEASUREMENT_SITES:
                    v = cd.get("measurements", {}).get(site)
                    if v and v > 0:
                        row[site] = v
                rows.append(row)
        if rows:
            mdf = pd.DataFrame(rows).set_index("Week")
            if "Weight (kg)" in mdf.columns and mdf["Weight (kg)"].notna().any():
                st.line_chart(mdf[["Weight (kg)"]])
            waist_cols = [c for c in mdf.columns if "Waist" in c and mdf[c].notna().any()]
            if waist_cols:
                st.line_chart(mdf[waist_cols])

# ============================================================
# TAB: WHOOP DATA
# ============================================================
with tab_whoop:
    st.subheader("Whoop Data")
    token = get_saved_token()

    if not token:
        st.warning("Connect your Whoop account to pull data.")
        auth_url = build_auth_url()
        st.markdown(f"**Step 1:** [Click here to authorize with Whoop]({auth_url})")
        st.markdown("**Step 2:** After authorizing, copy the **entire URL** from your browser and paste below:")
        callback_url = st.text_input("Paste the redirect URL:", placeholder="https://whoop.com/callback?code=...")
        if callback_url:
            parsed = urllib.parse.urlparse(callback_url)
            qp = urllib.parse.parse_qs(parsed.query)
            if "code" in qp:
                with st.spinner("Connecting..."):
                    new_token = exchange_code_for_token(qp["code"][0])
                if new_token:
                    st.success("Connected!")
                    st.rerun()
                else:
                    st.error("Connection failed. Try again.")
    else:
        st.success("Whoop connected")
        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button("Pull Latest Data", type="primary", use_container_width=True):
                with st.spinner("Pulling data from Whoop..."):
                    start_str = BASELINE_START.isoformat()
                    end_str = (PROTOCOL_END + timedelta(days=3)).isoformat()
                    daily = pull_whoop_data(token, start_str, end_str)
                    save_whoop_data({"daily": daily, "last_pull": datetime.now().isoformat()})
                st.success(f"Pulled data for {len(daily)} days!")
                st.rerun()
        with col2:
            wd = load_whoop_data()
            if wd.get("last_pull"):
                st.caption(f"Last pull: {wd['last_pull'][:19].replace('T', ' ')}")

        wd = load_whoop_data()
        if wd.get("daily"):
            st.subheader("Weekly Averages")
            st.dataframe(pd.DataFrame(compute_weekly_averages(wd["daily"])), use_container_width=True, hide_index=True)
            st.subheader("Daily Data")
            daily_rows = []
            for ds in sorted(wd["daily"].keys(), reverse=True):
                d = wd["daily"][ds]
                daily_rows.append({
                    "Date": ds,
                    "Recovery %": d.get("recovery"),
                    "HRV (ms)": d.get("hrv"),
                    "RHR (bpm)": d.get("rhr"),
                    "Sleep (hrs)": d.get("sleep_hrs"),
                    "Strain": round(d["strain"], 1) if d.get("strain") else None,
                })
            st.dataframe(pd.DataFrame(daily_rows), use_container_width=True, hide_index=True)
        else:
            st.info("Click 'Pull Latest Data' to fetch your Whoop data.")

        with st.expander("Re-authorize Whoop (if token expired)"):
            auth_url = build_auth_url()
            st.markdown(f"[Click here to re-authorize]({auth_url})")
            new_url = st.text_input("Paste new redirect URL:", key="reauth")
            if new_url:
                parsed = urllib.parse.urlparse(new_url)
                qp = urllib.parse.parse_qs(parsed.query)
                if "code" in qp:
                    if exchange_code_for_token(qp["code"][0]):
                        st.success("Re-authorized!")
                        st.rerun()

# ============================================================
# TAB: WEEKLY CHECK-IN
# ============================================================
with tab_checkin:
    st.subheader("Weekly Check-in")
    st.caption("Complete every Monday morning before eating")

    checkin_data = load_checkin_data()
    checkin_data.setdefault("checkins", {})

    week_options = [f"{name} ({ws.strftime('%b %d')})" for name, ws, we in WEEKS]
    default_idx = 0
    for i, (name, ws, we) in enumerate(WEEKS):
        if ws <= today <= we + timedelta(days=1):
            default_idx = i
            break

    selected_week = st.selectbox("Select week:", week_options, index=default_idx)
    week_idx = week_options.index(selected_week)
    week_name, week_start, week_end = WEEKS[week_idx]
    existing = checkin_data["checkins"].get(week_name, {})

    with st.form(f"checkin_{week_name}"):
        st.markdown(f"### {week_name} ({week_start.strftime('%B %d, %Y')})")

        st.markdown("**Body Composition**")
        c1, c2 = st.columns(2)
        with c1:
            _w = existing.get("weight")
            weight = st.number_input("Weight (kg)", 50.0, 150.0, float(_w) if _w not in (None, "", 0) else 82.0, 0.1)
        with c2:
            _bf = existing.get("body_fat")
            body_fat = st.number_input("Body Fat % (DEXA)", 0.0, 50.0, float(_bf) if _bf not in (None, "", 0) else 0.0, 0.1)

        st.markdown("**Measurements (cm)** - [See Measurement Guide tab for instructions]")
        measurements = {}
        cols = st.columns(4)
        for i, site in enumerate(MEASUREMENT_SITES):
            with cols[i % 4]:
                v = existing.get("measurements", {}).get(site)
                measurements[site] = st.number_input(site, 0.0, 200.0, float(v) if v not in (None, "", 0) else 0.0, 0.1, key=f"m_{site}")

        st.markdown("**How are you feeling? (1-10)**")
        subjective = {}
        sub_items = ["Energy", "Sleep Quality", "Mood", "Libido", "Appetite", "Injection Comfort"]
        cols = st.columns(3)
        for i, item in enumerate(sub_items):
            with cols[i % 3]:
                v = existing.get("subjective", {}).get(item)
                subjective[item] = st.slider(item, 1, 10, int(v) if v not in (None, "", 0) else 5, key=f"s_{item}")

        notes = st.text_area("Notes", existing.get("notes", ""), key="notes")

        if st.form_submit_button("Save Check-in", type="primary", use_container_width=True):
            checkin_data["checkins"][week_name] = {
                "date": week_start.isoformat(),
                "weight": weight,
                "body_fat": body_fat if body_fat > 0 else None,
                "measurements": measurements,
                "subjective": subjective,
                "photos": existing.get("photos", {}),
                "notes": notes,
                "saved_at": datetime.now().isoformat(),
            }
            save_checkin_data(checkin_data)
            st.success(f"{week_name} check-in saved!")

    # --- Progress Photos (outside form for file upload support) ---
    st.divider()
    st.subheader(f"Progress Photos — {week_name}")
    st.caption("Upload front, side, and back photos. They're stored securely in Google Drive.")

    # Show existing photos
    week_photos = get_week_photos(week_name)
    if week_photos:
        photo_cols = st.columns(len(week_photos))
        for i, (view, file_id) in enumerate(week_photos.items()):
            with photo_cols[i]:
                st.markdown(f"**{view}**")
                st.image(get_photo_url(file_id), use_container_width=True)

    # Upload new photos
    upload_cols = st.columns(3)
    uploaded_files = {}
    for i, view in enumerate(["Front", "Side", "Back"]):
        with upload_cols[i]:
            has_photo = view in week_photos
            label = f"{'Replace' if has_photo else 'Upload'} {view} photo"
            uploaded = st.file_uploader(label, type=["jpg", "jpeg", "png"], key=f"photo_{week_name}_{view}")
            if uploaded:
                uploaded_files[view] = uploaded

    if uploaded_files:
        if st.button("📸 Save Photos", type="primary", use_container_width=True):
            for view, uploaded in uploaded_files.items():
                try:
                    with st.spinner(f"Uploading {view}..."):
                        filename = f"{week_name}_{view}.jpg"
                        file_id, link = upload_photo_to_drive(uploaded.getvalue(), filename, week_name)
                        save_photo_meta(week_name, view, file_id)
                        checkin_data.setdefault("checkins", {}).setdefault(week_name, {}).setdefault("photos", {})[view] = True
                    st.success(f"{view} photo uploaded!")
                except Exception as e:
                    st.error(f"Error uploading {view}: {e}")
            save_checkin_data(checkin_data)
            st.rerun()

    if checkin_data.get("checkins"):
        st.divider()
        st.subheader("Check-in History")
        rows = []
        for wk in ["Baseline"] + [f"Week {i}" for i in range(1, 10)]:
            cd = checkin_data["checkins"].get(wk, {})
            if cd:
                row = {"Week": wk, "Weight": cd.get("weight")}
                for site in MEASUREMENT_SITES:
                    v = cd.get("measurements", {}).get(site)
                    if v and v > 0:
                        row[site] = v
                for item in sub_items:
                    v = cd.get("subjective", {}).get(item)
                    if v:
                        row[item] = v
                rows.append(row)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ============================================================
# TAB: INJECTION LOG
# ============================================================
with tab_injections:
    st.subheader("Injection Schedule")
    st.caption("Mon-Fri | ~1 hour after dinner, empty stomach | 9:00 PM")

    checkin_data = load_checkin_data()
    checkin_data.setdefault("injections", {})
    changed = False

    for week_num in range(1, 10):
        week_days = INJECTION_DAYS[(week_num - 1) * 5: week_num * 5]
        if not week_days:
            continue
        is_current = any(ws <= today <= we for name, ws, we in WEEKS if name == f"Week {week_num}")
        with st.expander(f"Week {week_num}: {week_days[0].strftime('%b %d')} - {week_days[-1].strftime('%b %d')}", expanded=is_current):
            cols = st.columns(5)
            for i, day in enumerate(week_days):
                key = day.isoformat()
                is_done = checkin_data["injections"].get(key, False)
                with cols[i]:
                    new_val = st.checkbox(day.strftime("%a %b %d"), is_done, key=f"inj_{key}")
                    if new_val != is_done:
                        checkin_data["injections"][key] = new_val
                        changed = True

    if changed:
        save_checkin_data(checkin_data)

    total_done = sum(1 for d in INJECTION_DAYS if checkin_data["injections"].get(d.isoformat()))
    total_missed = sum(1 for d in INJECTION_DAYS if d < today and not checkin_data["injections"].get(d.isoformat()))
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("Completed", f"{total_done}/45")
    c2.metric("Missed", total_missed)
    c3.metric("Remaining", 45 - total_done - total_missed)

# ============================================================
# TAB: SUPPLEMENT STACK
# ============================================================
with tab_supplements:
    st.subheader("Daily Supplement Protocol")
    st.caption("Evidence-based timing optimized for peptide protocol, fat loss, muscle growth & fertility")

    # --- Supplement Schedule Data ---
    SUPPLEMENT_SCHEDULE = {
        "breakfast": {
            "label": "Breakfast (6-8 AM)",
            "icon": "☕",
            "color": "#f59e0b",
            "note": "Take with food containing dietary fat for fat-soluble absorption",
            "supplements": [
                {"name": "Collagen Peptides", "dose": "15g powder", "brand": "Momentous / Vital Proteins", "why": "Connective tissue repair (knee, spine, shoulder). Take WITH Vitamin C for collagen synthesis. Best 30-60 min before training on workout days."},
                {"name": "NMN (Wonderfeel Youngr)", "dose": "2 capsules (900mg NMN)", "brand": "Wonderfeel", "why": "NAD+ production peaks with circadian rhythm. Contains resveratrol + D3. Supports mitochondrial energy and exercise performance."},
                {"name": "Ubiquinol (CoQ10)", "dose": "1 softgel (100mg)", "brand": "Qunol", "why": "Fat-soluble. Works synergistically with NMN on electron transport chain. Both target mitochondrial energy from different angles."},
                {"name": "Urolithin A", "dose": "2 capsules (500mg)", "brand": "More", "why": "Activates mitophagy (clears damaged mitochondria). Paired with NMN = clear the old, fuel the new. Reduces exercise-induced muscle damage."},
                {"name": "Elite Omega 3", "dose": "2 softgels (800mg EPA / 600mg DHA)", "brand": "Carlson", "why": "Fat-soluble. Anti-inflammatory for training recovery. Supports sperm membrane fluidity for fertility."},
                {"name": "Vitamin C", "dose": "1 capsule (500mg)", "brand": "Thorne", "why": "Antioxidant. Required for collagen synthesis. Separated from zinc (at lunch) to give each its own absorption window."},
                {"name": "Nattokinase", "dose": "1 capsule (2000 FUs)", "brand": "Doctor's Best", "why": "Fibrinolytic enzyme. Supports cardiovascular health. Targets your elevated LDL (129 mg/dL)."},
                {"name": "Citrus Bergamot", "dose": "2 capsules (1000mg)", "brand": "Double Wood", "why": "Inhibits HMG-CoA reductase (natural statin pathway). Paired with nattokinase for dual-mechanism LDL management."},
                {"name": "L-Tyrosine", "dose": "1 capsule (500mg)", "brand": "Pure Encapsulations", "why": "Dopamine/norepinephrine precursor. Supports focus, motivation, and thyroid function (TSH 1.03). Morning for cognitive benefit."},
                {"name": "Creatine (Liposomal)", "dose": "Per label", "brand": "RHO Nutrition", "why": "Supports lean mass gain (+1.14 kg) and fat loss (-0.88% BF) during recomposition. Consistency > timing."},
                {"name": "Vitamin E", "dose": "1 capsule (200-400 IU)", "brand": "Mixed Tocopherols (TBD)", "why": "Completes antioxidant defense for sperm membrane protection. Synergistic with Selenium + NAC (fertility trifecta)."},
            ]
        },
        "lunch": {
            "label": "Lunch (12-1 PM)",
            "icon": "\U0001f96a",
            "color": "#60a5fa",
            "note": "Take with your largest carb-containing meal. Fertility stack concentrated here.",
            "supplements": [
                {"name": "Zinc Picolinate", "dose": "1 capsule (30mg)", "brand": "Pure Encapsulations", "why": "Critical for testosterone production and sperm quality. Picolinate form for superior absorption. Separated from calcium by hours."},
                {"name": "Selenium", "dose": "1 capsule (200mcg)", "brand": "Thorne", "why": "Pairs with zinc for selenoprotein production in sperm. Protects sperm DNA integrity. Take with food."},
                {"name": "NAC", "dose": "1 capsule (600mg)", "brand": "Pure Encapsulations", "why": "Glutathione precursor. Zinc + Selenium + NAC = evidence-based fertility trifecta. Protects sperm from oxidative damage."},
                {"name": "Tongkat Ali", "dose": "2 capsules (400mg)", "brand": "Momentous", "why": "Improves semen volume, concentration, morphology, motility. Supports free testosterone. Midday to avoid sleep interference."},
                {"name": "Bio Boron", "dose": "1 capsule (10mg)", "brand": "Pure Therapro Rx", "why": "Reduces SHBG (yours is 53, flagged high) to increase free testosterone. Synergistic with Tongkat Ali."},
                {"name": "5-MTHF", "dose": "1 capsule (1mg)", "brand": "Thorne", "why": "Active folate for sperm DNA synthesis and methylation. Paired with B12 (split across meals for sustained methylation support)."},
                {"name": "Berberine", "dose": "1 capsule (200mg)", "brand": "Thorne", "why": "AMPK activator. Improves insulin sensitivity. At LUNCH not dinner -- avoids blood glucose drop that would interfere with peptide GH response."},
                {"name": "D3 & K2", "dose": "1 capsule", "brand": "Dr. Berg", "why": "Fat-soluble. Vitamin D was 24 (low) now 42. K2 directs calcium to bones (BMD 33rd percentile). Contains P5P, Mg citrate, Zn sulfate."},
            ]
        },
        "dinner": {
            "label": "Dinner (6-7 PM) -- LIGHT MEAL",
            "icon": "\U0001f957",
            "color": "#22c55e",
            "note": "Keep dinner light (moderate protein, low carb, low fat). Peptide injection 1 hour after.",
            "supplements": [
                {"name": "Methylcobalamin (B12)", "dose": "1 capsule (1000mcg)", "brand": "Pure Encapsulations", "why": "Essential for sperm DNA methylation and fertility. Separated from morning B-vitamins for sustained methylation support."},
                {"name": "Electrolytes (LMNT)", "dose": "Per label", "brand": "LMNT", "why": "Evening replenishment after training. Sodium/potassium/magnesium balance supports sleep quality. On rest days, take whenever."},
            ]
        },
        "bedtime": {
            "label": "Bedtime (9-10 PM) -- AFTER Peptide Injection",
            "icon": "\U0001f319",
            "color": "#a78bfa",
            "note": "Sleep stack: enhances slow-wave deep sleep = maximizes GH pulse from peptide injection",
            "supplements": [
                {"name": "Magnesium Glycinate", "dose": "1 capsule (120mg)", "brand": "Pure Encapsulations", "why": "Promotes muscle relaxation + enhances slow-wave sleep. Glycinate form provides both Mg and glycine. Amplifies peptide GH response."},
                {"name": "Glycine", "dose": "2 capsules (1g)", "brand": "Thorne", "why": "Increases time in slow-wave deep sleep -- the phase where GH secretion peaks. #1 supplement synergy with your peptide protocol."},
                {"name": "Apigenin", "dose": "1 capsule (50mg)", "brand": "Nutricost", "why": "Mild sedative via GABA modulation. Completes sleep stack (Mg + glycine + apigenin). Better sleep = bigger GH pulses."},
            ]
        },
        "workout": {
            "label": "Intra/Post-Workout",
            "icon": "\U0001f4aa",
            "color": "#ef4444",
            "note": "Training days only. Sip EAAs during or immediately after training.",
            "supplements": [
                {"name": "EAAs (Essential Amino Acids)", "dose": "10-15g powder", "brand": "Momentous / Kion (TBD)", "why": "Superior to BCAAs -- provides all 9 essential amino acids. Protects lean mass during caloric deficit. Critical for body recomposition."},
            ]
        },
    }

    # --- Daily Timeline View ---
    st.markdown("### ⏰ Daily Protocol Timeline")

    timeline_md = """
| Time | Window | Supplements |
|---|---|---|
| **6-8 AM** | ☕ Breakfast | Collagen, NMN, Ubiquinol, Urolithin A, Omega 3, Vit C, Nattokinase, Bergamot, L-Tyrosine, Creatine, Vit E |
| **12-1 PM** | \U0001f96a Lunch | Zinc, Selenium, NAC, Tongkat Ali, Boron, 5-MTHF, Berberine, D3+K2 |
| **6-7 PM** | \U0001f957 Dinner (light) | B12, Electrolytes |
| **7-8 PM** | \U0001f489 Injection | Ipamorelin + Tesamorelin (Mon-Fri) |
| **\U0001f3cb️ Training** | \U0001f4aa Intra/Post | EAAs (training days only) |
| **9-10 PM** | \U0001f319 Bedtime | Mag Glycinate, Glycine, Apigenin |
"""
    st.markdown(timeline_md)

    st.divider()

    # --- Detailed Supplement Cards by Window ---
    st.markdown("### \U0001f4cb Supplement Details by Window")

    for window_key, window_data in SUPPLEMENT_SCHEDULE.items():
        with st.expander(f"{window_data['icon']} {window_data['label']} ({len(window_data['supplements'])} supplements)", expanded=False):
            st.caption(f"ℹ️ {window_data['note']}")
            for supp in window_data["supplements"]:
                col_name, col_dose, col_brand = st.columns([2, 2, 2])
                with col_name:
                    st.markdown(f"**{supp['name']}**")
                with col_dose:
                    st.markdown(f"`{supp['dose']}`")
                with col_brand:
                    st.caption(supp['brand'])
                st.caption(f"→ {supp['why']}")
                st.markdown("---")

    st.divider()

    # --- Daily Checklist Tracker ---
    st.markdown("### ✅ Daily Supplement Checklist")
    st.caption("Track your daily compliance. Data saved to Google Sheets.")

    today_str = date.today().isoformat()
    checklist_date = st.date_input("Select date:", value=date.today(), key="supp_date")
    checklist_date_str = checklist_date.isoformat()

    # Load existing checklist data
    def load_supplement_log():
        try:
            client = get_gsheet_client()
            sheet = client.open(SHEET_NAME)
            try:
                ws = sheet.worksheet("supplement_log")
            except:
                ws = sheet.add_worksheet(title="supplement_log", rows=1000, cols=30)
                ws.update_cell(1, 1, "date")
                ws.update_cell(1, 2, "data")
            records = ws.get_all_records()
            log = {}
            for r in records:
                if r.get("date") and r.get("data"):
                    try:
                        log[r["date"]] = json.loads(r["data"])
                    except:
                        pass
            return log
        except:
            return {}

    def save_supplement_log(date_str, checked_supplements):
        try:
            client = get_gsheet_client()
            sheet = client.open(SHEET_NAME)
            try:
                ws = sheet.worksheet("supplement_log")
            except:
                ws = sheet.add_worksheet(title="supplement_log", rows=1000, cols=30)
                ws.update_cell(1, 1, "date")
                ws.update_cell(1, 2, "data")
            records = ws.get_all_records()
            row_idx = None
            for i, r in enumerate(records):
                if r.get("date") == date_str:
                    row_idx = i + 2
                    break
            data_str = json.dumps(checked_supplements)
            if row_idx:
                ws.update_cell(row_idx, 2, data_str)
            else:
                ws.append_row([date_str, data_str])
            if "supplement_log" in st.session_state:
                del st.session_state["supplement_log"]
        except Exception as e:
            st.error(f"Error saving: {e}")

    if "supplement_log" not in st.session_state:
        st.session_state.supplement_log = load_supplement_log()

    existing_checks = st.session_state.supplement_log.get(checklist_date_str, {})

    all_supplements = []
    for window_key, window_data in SUPPLEMENT_SCHEDULE.items():
        for supp in window_data["supplements"]:
            all_supplements.append((window_data["label"], supp["name"], window_key))

    checked = {}
    for window_key, window_data in SUPPLEMENT_SCHEDULE.items():
        st.markdown(f"**{window_data['icon']} {window_data['label']}**")
        cols = st.columns(min(len(window_data["supplements"]), 4))
        for i, supp in enumerate(window_data["supplements"]):
            col = cols[i % len(cols)]
            with col:
                key = f"supp_{checklist_date_str}_{supp['name']}"
                default = existing_checks.get(supp["name"], False)
                val = st.checkbox(supp["name"], value=default, key=key)
                checked[supp["name"]] = val

    total = len(checked)
    taken = sum(1 for v in checked.values() if v)
    pct = int((taken / total) * 100) if total > 0 else 0

    st.divider()
    col_prog, col_save = st.columns([3, 1])
    with col_prog:
        st.progress(pct / 100)
        st.markdown(f"**{taken}/{total} supplements taken ({pct}%)**")
    with col_save:
        if st.button("\U0001f4be Save Checklist", type="primary", use_container_width=True):
            save_supplement_log(checklist_date_str, checked)
            st.session_state.supplement_log[checklist_date_str] = checked
            st.success("Saved!")

    # --- Key Synergies & Warnings ---
    st.divider()
    st.markdown("### ⚠️ Key Interactions & Synergies")
    st.markdown("""
**✅ Synergies (grouped intentionally):**
- **NMN + Ubiquinol + Urolithin A** (breakfast): Mitochondrial powerhouse -- clear damaged mitochondria, fuel new ones, support electron transport
- **Zinc + Selenium + NAC** (lunch): Evidence-based fertility trifecta -- protects sperm DNA and supports glutathione production
- **Tongkat Ali + Boron** (lunch): Total T boost (Tongkat) + free T boost via SHBG reduction (Boron)
- **Nattokinase + Bergamot** (breakfast): Dual-mechanism LDL management (fibrinolytic + HMG-CoA reductase inhibition)
- **Collagen + Vitamin C** (breakfast): Vitamin C is required for collagen synthesis -- always take together
- **Mag Glycinate + Glycine + Apigenin** (bedtime): Sleep architecture enhancement = maximizes GH pulse from peptide injection

**❌ Conflicts (separated intentionally):**
- **Zinc and Calcium** separated by 5+ hours (compete for absorption via DMT1 pathway)
- **Berberine at LUNCH not dinner** -- lowers blood glucose, which would interfere with peptide GH response post-dinner
- **Dinner kept light** (only B12 + electrolytes) -- elevated insulin from a heavy meal blunts the GH pulse from peptide injection
- **NMN in morning only** -- NAD+ production follows circadian rhythm; evening dosing may interfere with sleep
""")


# ============================================================
# TAB: TRAINING PROGRAM
# ============================================================
with tab_training:
    st.subheader("Training Program")
    st.caption("Modified Hypertrophy Clusters | 8-Week Periodization | Injury-Adapted")

    # --- Determine current phase ---
    today_date = date.today()
    protocol_day = (today_date - PROTOCOL_START).days + 1 if today_date >= PROTOCOL_START else 0
    if protocol_day <= 14:
        current_phase = 0
    elif protocol_day <= 28:
        current_phase = 1
    elif protocol_day <= 42:
        current_phase = 2
    else:
        current_phase = 3

    phase_info = {
        0: {"name": "Phase 0 — Corrective Foundation", "weeks": "1-2", "format": "3x8-12 straight sets, tempo 3-1-2-0, RPE 6-7", "load": "Test x 0.85"},
        1: {"name": "Phase 1 — Hypertrophy Clusters 8x5", "weeks": "3-4", "format": "8 reps x 5 clusters, 15s intra-rest, RPE 7-8", "load": "Test x 0.70"},
        2: {"name": "Phase 2 — Hypertrophy Clusters 10x4", "weeks": "5-6", "format": "10 reps x 4 clusters, 12s intra-rest, RPE 8", "load": "Test x 0.75"},
        3: {"name": "Phase 3 — Hypertrophy Clusters 12x3", "weeks": "7-8", "format": "12 reps x 3 clusters, 10s intra-rest, RPE 8-9", "load": "Test x 0.80"},
    }

    pi = phase_info[current_phase]
    st.info(f"**Current: {pi['name']}** (Weeks {pi['weeks']}) — {pi['format']} — Load: {pi['load']}")

    # --- Weekly Template ---
    st.markdown("### Weekly Template")
    week_template = {
        "Monday": "Upper A — Horizontal Push/Pull",
        "Tuesday": "Conditioning A — Zone 2 + Corrective",
        "Wednesday": "Lower A — Quad-Dominant",
        "Thursday": "Upper B — Vertical Push/Pull",
        "Friday": "Lower B — Hip-Dominant + Conditioning Finisher",
        "Saturday": "Light Work / Optional Conditioning",
        "Sunday": "REST / Active Recovery",
    }
    cols_wk = st.columns(7)
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_types = ["Upper A", "Zone 2", "Lower A", "Upper B", "Lower B", "Optional", "REST"]
    day_colors = ["#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6", "#ef4444", "#f59e0b", "#6b7280"]
    for i, (dn, dt, dc) in enumerate(zip(day_names, day_types, day_colors)):
        with cols_wk[i]:
            st.markdown(f"<div style='text-align:center;padding:8px;border-radius:8px;background:{dc}20;border:1px solid {dc}'><b>{dn}</b><br><small>{dt}</small></div>", unsafe_allow_html=True)

    st.divider()

    # --- Bracing Guide ---
    with st.expander("How to Brace (read this first)", expanded=False):
        st.markdown("""
**Step 1:** Take a breath in through your nose — NOT into your chest. Breathe into your belly and sides. Imagine inflating a belt around your entire midsection — front, sides, AND lower back.

**Step 2:** Once your midsection is "inflated," gently tighten your abs as if someone was about to poke you in the stomach. You're NOT sucking in — you're pushing OUT against that imaginary belt while also tensing.

**Step 3:** Hold that brace while you perform the rep.

**Step 4:** Exhale THROUGH your teeth (like a slow hiss: "tsssss") as you push/pull through the hardest part. Do NOT fully release the brace — maintain ~60% tension.

**Step 5:** Re-breathe and re-brace at the top/bottom of each rep.

> **Hernia modification:** NEVER do a hard Valsalva (holding breath + bearing down with max force). The "exhale through teeth" method keeps intra-abdominal pressure moderate. If you feel pressure or bulging in your right groin, STOP immediately.

> **Diastasis modification:** Instead of pushing belly OUT, draw navel toward spine FIRST (activating transversus abdominis), THEN tense the outer abs around it. This protects the linea alba.
""")

    # --- Warm-up Protocols ---
    with st.expander("Warm-Up Protocols (session-specific, 8 min max)", expanded=False):
        st.markdown("#### CNS Activation Block — Every Session (3 min)")
        cns_warmup = [
            {"name": "Iron Neck 4-Way", "detail": "1x8 each direction (flexion, extension, lateral L/R)", "setup": "Strap harness, attach band to rack at head height.", "feel": "Deep neck muscles — NOT upper traps. Reduce resistance if traps fire.", "why": "Activates cervical proprioceptors. Primes vestibular system. Reduces migraine trigger risk."},
            {"name": "Diaphragmatic Breathing", "detail": "5 breaths (4s inhale, 2s hold, 6s exhale)", "setup": "Stand tall or lie supine. One hand on chest, one on belly.", "feel": "Belly expanding 360 degrees. Chest hand stays still.", "why": "Activates TA for diastasis-safe bracing. Downregulates sympathetic nervous system."},
            {"name": "Band Pull-Apart", "detail": "15 reps", "setup": "Hold light band at shoulder width, arms straight at chest height.", "feel": "Between shoulder blades (rhomboids, lower traps), rear delts.", "why": "Counters rounded shoulder posture. Daily non-negotiable."},
        ]
        for ex in cns_warmup:
            st.markdown(f"**{ex['name']}** — {ex['detail']}")
            st.caption(f"Setup: {ex['setup']}")
            st.caption(f"Feel it: {ex['feel']}")
            st.caption(f"Why: {ex['why']}")
            st.markdown("---")

        st.markdown("#### Upper Day Warm-Up (after CNS block — 5 min)")
        upper_warmup = [
            {"name": "Seated Shoulder External Rotation (8lb DB)", "detail": "2x10/arm", "setup": "Sit on bench, legs wide. Right elbow on right knee, forearm hanging with 8lb DB. Left leg extended for stability.", "feel": "Back of shoulder, deep rotator cuff (infraspinatus/teres minor). NOT upper trap or front shoulder.", "why": "Activates external rotators before pressing. Corrects internal rotation from rounded shoulders."},
            {"name": "Prone Y-Raise (5lb DBs)", "detail": "1x8", "setup": "Bench at 30 degrees incline, lie face down, arms hanging with 5lb DBs, thumbs forward.", "feel": "Between and below shoulder blades (lower traps). If upper traps/neck fire, push shoulders DOWN.", "why": "Directly addresses scapular winging and lower trap weakness."},
            {"name": "Empty Bar Groove", "detail": "2x8 of that day's primary lift", "setup": "Empty barbell (45 lbs).", "feel": "Target muscles at low intensity.", "why": "Pattern primer."},
        ]
        for ex in upper_warmup:
            st.markdown(f"**{ex['name']}** — {ex['detail']}")
            st.caption(f"Setup: {ex['setup']}")
            st.caption(f"Feel it: {ex['feel']}")
            st.caption(f"Why: {ex['why']}")
            st.markdown("---")

        st.markdown("#### Lower Day Warm-Up (after CNS block — 5 min)")
        lower_warmup = [
            {"name": "90/90 Hip Switch", "detail": "8/side", "setup": "Sit on floor, both knees bent 90 degrees, feet wide.", "feel": "Deep hip stretch. No knee pain — reduce range if needed.", "why": "Hip rotation mobility without stressing knee."},
            {"name": "Slant Board Bodyweight Squat", "detail": "1x10", "setup": "Stand on slant board, heels elevated, feet hip width.", "feel": "Front of thighs (quads), especially VMO (inner quad above kneecap).", "why": "Ankle elevation shifts load onto VMO. Perfect squat primer."},
            {"name": "Band Terminal Knee Extension", "detail": "1x12 each leg", "setup": "Loop band behind knee on rack post. Stand on banded leg, step back for tension.", "feel": "VMO (inner quad above knee). Reduce band tension if you feel it behind the knee.", "why": "Patellar tracking rehab. VMO activation before squatting."},
            {"name": "Empty Bar Groove", "detail": "2x8 of that day's primary lift", "setup": "Empty barbell.", "feel": "Target muscles at low intensity.", "why": "Pattern primer."},
        ]
        for ex in lower_warmup:
            st.markdown(f"**{ex['name']}** — {ex['detail']}")
            st.caption(f"Setup: {ex['setup']}")
            st.caption(f"Feel it: {ex['feel']}")
            st.caption(f"Why: {ex['why']}")
            st.markdown("---")

    # --- Exercise Database ---
    EXERCISES = {
        "Barbell Bench Press": {
            "sets": "8×5 clusters (Ph1) → 10×4 (Ph2) → 12×3 (Ph3)", "load": "Ph1: 80 lbs | Ph2: 95 lbs | Ph3: 100 lbs", "rest": "30–45s between cluster sets, 3 min between exercises", "tempo": "3-1-2-0",
            "setup": "Lie on bench, eyes under bar. Feet flat on floor. Pinch shoulder blades together and push them down into the bench (imagine putting them in your back pockets). Grip slightly wider than shoulder width. Unrack.",
            "execution": "Lower bar to mid-chest (nipple line) in 3 seconds. Touch chest gently (don't bounce). Pause 1s. Press up in 2s. Lock arms. On cluster sets: complete 5 reps, re-rack, rest 30–45s, repeat for total cluster count.",
            "bracing": "Breathe in at the top. Brace core. Exhale through teeth as you press up. Re-breathe at lockout.",
            "feel": "Chest (pecs), front shoulders, triceps. If only shoulders, widen grip or retract scapulae more.",
            "notes": "T1 CLUSTER LIFT — Power Monday. Phase loads: Ph1 Wk1–3: 80 lbs (bar+2x15+2x2.5). Ph2 Wk4–6: 95 lbs (bar+2x25). Ph3 Wk7–8: 100 lbs (bar+2x25+2x2.5). Load is FIXED per phase — progress via completing all cluster sets cleanly, not by adding weight mid-phase. Week 1: if 80 feels too easy, that's normal — the cumulative volume across 8 sets is the stimulus."
        },
        "Barbell Bent-Over Row": {
            "sets": "8×5 clusters (Ph1) → 10×4 (Ph2) → 12×3 (Ph3)", "load": "Ph1: 65 lbs | Ph2: 75 lbs | Ph3: 80 lbs", "rest": "30–45s between cluster sets, 3 min between exercises", "tempo": "2-1-2-0",
            "setup": "Stand feet hip width, soft knee bend. Hinge at hips until torso is 45 degrees to floor. Arms hang straight, grip just outside knees. Head neutral — look at floor 6 feet ahead.",
            "execution": "Pull bar to lower ribs by driving elbows BACK (not up). Squeeze shoulder blades 1s at top. Lower slowly. Bar travels in a straight line. On clusters: 5 reps, rest 30–45s, repeat.",
            "bracing": "CRITICAL for lower back. Full brace before each rep. Maintain neutral spine — no rounding. If back rounds, weight is too heavy.",
            "feel": "Middle back (lats, rhomboids), rear shoulders. Lower back is a STABILIZER only. If it's the limiting factor, switch to landmine row.",
            "notes": "T1 CLUSTER LIFT — Power Monday. Phase loads: Ph1 Wk1–3: 65 lbs (bar+2x10). Ph2 Wk4–6: 75 lbs (bar+2x15). Ph3 Wk7–8: 80 lbs (bar+2x15+2x2.5). Paired with Bench Press on rest periods — while bench rests, row, and vice versa. Lumbar alternative: Landmine row."
        },
        "Barbell Incline Press": {
            "sets": "3x10-12", "load": "75 lbs (bar+2x15s) — start, double progression", "rest": "75s", "tempo": "3-1-2-0",
            "setup": "Bench at 30-45 degree incline. Set bar in rack at incline height — or clean it to shoulders. Grip slightly wider than shoulder width. Shoulder blades pinched and pushed into bench. Feet flat on floor.",
            "execution": "Lower bar to upper chest (below collarbones) in 3s. Touch lightly. Pause 1s. Press up in 2s. Bar path travels very slightly back toward the rack at the top.",
            "bracing": "Breathe in at top. Brace core. Exhale through teeth pressing up. Re-breathe at lockout.",
            "feel": "Upper chest (clavicular pec head), front shoulders. Upper chest is the most visible part of the chest — this builds the aesthetic 'shelf'. If only shoulders, retract scapulae harder before pressing.",
            "notes": "Upper chest hits far better at this angle than flat bench alone. Starting load: 65 lbs (bar+10s). Progress same as flat bench: add reps → slow tempo → add set → jump weight (+10 lbs)."
        },
        "Landmine Row (single arm)": {
            "sets": "3x10/arm", "load": "Bar only or bar+10", "rest": "60s", "tempo": "2-1-2-0",
            "setup": "Barbell in landmine. Stand perpendicular, grab fat end with one hand. Hinge at hips, free arm on knee. Feet staggered.",
            "execution": "Row bar to hip by driving elbow back. Squeeze lat at top. Lower slowly. Keep torso still — don't rotate.",
            "bracing": "Light brace. Hinge from hips, not lower back.",
            "feel": "Lat (side of back), rear shoulder. Much less lower back stress than bent-over row.",
            "notes": "Your lumbar-safe rowing pattern. Add a 10 lb plate when bar only feels comfortable."
        },
        "Dips (Matador)": {
            "sets": "3x5-6", "load": "Bodyweight", "rest": "60s", "tempo": "2-1-2-0",
            "setup": "Grip Matador handles. Jump up to locked arms. Lean torso slightly forward (~15 degrees). Cross ankles behind you.",
            "execution": "Lower by bending elbows until upper arm is roughly parallel to floor (90 degree elbow). Press back up.",
            "bracing": "Exhale through teeth as you press up. Core tight — no swinging.",
            "feel": "Chest (lower pecs), triceps, front shoulders.",
            "notes": "If right shoulder pinches BEFORE 90 degrees, stop at pain-free angle. When 3x8 at BW is easy, add 5 lbs vest."
        },
        "Barbell Curl": {
            "sets": "3x10", "load": "45 lbs (bar only)", "rest": "60s", "tempo": "2-1-2-0",
            "setup": "Stand feet hip width. Grip bar underhand, slightly wider than hips. Elbows at your sides.",
            "execution": "Curl bar up by bending elbows only. Squeeze biceps at top. Lower slowly. Elbows do NOT move forward — pin them to sides. No swinging.",
            "bracing": "Light core brace to prevent swaying.",
            "feel": "Front of upper arm (biceps). If you feel lower back, you're swinging.",
            "notes": ""
        },
        "Pallof Press": {
            "sets": "3x10/side", "load": "Medium band (50-75 lb)", "rest": "45s", "tempo": "2-2-2-0",
            "setup": "Anchor band to rack at chest height. Stand perpendicular, feet shoulder width. Hold band at chest with both hands.",
            "execution": "Press band straight out. It will try to rotate you toward the anchor — RESIST it. Hold 2s. Return to chest. Don't twist at all.",
            "bracing": "Draw navel in (TA activation), then brace against rotational pull. Exhale as you press.",
            "feel": "Deep core (obliques, transversus abdominis). Anti-rotation — core PREVENTS movement. Diastasis-safe (no flexion).",
            "notes": ""
        },
        "Dead Bug": {
            "sets": "3x8/side", "load": "Bodyweight", "rest": "45s", "tempo": "3-1-3-0",
            "setup": "Lie on back. Arms straight up. Knees bent 90 degrees, shins parallel to floor (tabletop). Lower back PRESSED INTO FLOOR — no gap.",
            "execution": "Simultaneously extend RIGHT arm overhead and LEFT leg straight out. Go slowly (3s). Hover 1-2 inches off floor. Hold 1s. Return in 3s. Switch sides.",
            "bracing": "EXHALE FULLY as you extend — the exhale activates TA.",
            "feel": "Deep core (TA). Challenge is keeping lower back on floor. If back arches, reduce extension range.",
            "notes": "Primary diastasis recti rehab exercise."
        },
        "Barbell Front Squat": {
            "sets": "1x5 @ 115 lbs (test set), then 2x6-8 @ 115 lbs", "load": "115 lbs (bar+2x35s)", "rest": "2 min", "tempo": "3-1-2-0",
            "setup": "Bar on FRONT of shoulders, across collarbones and front delts. Cross-arm grip recommended (arms crossed, hands on bar, elbows HIGH). Feet shoulder width, toes out 15-20 degrees.",
            "execution": "Squat by sitting BETWEEN legs (not back). Keep elbows HIGH — if they drop, bar rolls forward. Descend to parallel (hip crease level with knee top). Use box behind as depth check. Drive up through full foot.",
            "bracing": "Big breath at top. Full 360 degree brace. Exhale through teeth driving up. Re-breathe at top. NEVER let core go slack at bottom.",
            "feel": "Quads (front of thighs), glutes, upper back. NOT lower back — if lower back works hard, push elbows higher.",
            "notes": "Knees over toes is FINE. Don't let knees cave inward. Front squat chosen over back squat: keeps bar OFF cervical spine (migraine trigger), forces upright torso (lumbar friendly)."
        },
        "Slant Board Squat": {
            "sets": "3x12", "load": "Bodyweight (add vest when easy)", "rest": "60s", "tempo": "3-1-2-0",
            "setup": "Stand on slant board, heels elevated. Feet hip width or narrower. Arms in front for balance.",
            "execution": "Squat slowly, keeping torso very upright (nearly vertical). Let knees travel forward over toes — this is the whole point.",
            "bracing": "Light brace. Exhale on the way up.",
            "feel": "QUADS, specifically VMO (teardrop muscle on inner side of knee). This protects your knee and was likely inhibited after meniscus surgeries.",
            "notes": ""
        },
        "Barbell Reverse Lunge": {
            "sets": "3x8/leg", "load": "65 lbs (bar+10s)", "rest": "90s", "tempo": "2-1-2-0",
            "setup": "Bar on upper back (use front rack if cervical bothers you). Stand tall.",
            "execution": "Step BACK with one foot (not forward — less knee shear). Lower until back knee nearly touches floor. Front shin stays roughly vertical. Drive to standing through front heel. Alternate legs.",
            "bracing": "Brace before each step. Stay upright — don't lean forward.",
            "feel": "Front leg quad and glute. Back leg hip flexor stretches.",
            "notes": "Optional: balance pad under front foot for proprioception (not Week 1)."
        },
        "Nordic Curl Eccentric": {
            "sets": "3x5", "load": "Bodyweight", "rest": "90s", "tempo": "5s lower",
            "setup": "Kneel on pad on floor. Lock ankles under Rogue leg roller on rack. Adjust so ankles are secure, kneeling upright. Hands in front ready to catch.",
            "execution": "Start upright on knees. Keep body in STRAIGHT LINE from knees to shoulders (don't bend at hips). Slowly — 5 full seconds — lean forward, lowering toward floor. Fight gravity with hamstrings. Catch with hands. Push back up with hands.",
            "bracing": "Squeeze glutes and brace core to keep body straight. Do NOT pike at hips — that's cheating.",
            "feel": "Hamstrings — INTENSELY. Especially above and behind the knee. Loads hamstrings at long lengths, protecting the knee joint.",
            "notes": "CRITICAL FOR YOUR KNEE. Single most important exercise for left knee. Eccentric hamstring strength reduces meniscal and ACL strain. Track reps weekly. Progression: Wk1-2: 5s eccentric, hands catch + push up | Wk3-4: 6s, less hand push | Wk5-6: 8s, attempt partial concentric | Wk7-8: attempt full concentric (even partial ROM = progress)."
        },
        "Wall Sit": {
            "sets": "3x30s", "load": "Bodyweight", "rest": "45s", "tempo": "Hold",
            "setup": "Back against wall. Slide down until knees at 60 degrees (halfway to chair sit — NOT 90 degrees). Feet hip width, flat.",
            "execution": "Hold. Breathe normally. Press lower back into wall.",
            "bracing": "Maintain neutral spine against wall.",
            "feel": "Quads, especially VMO. Thighs will burn.",
            "notes": "Add 5 seconds every session. When you hit 60s at 60 degrees, progress to 90 degrees."
        },
        "Heel Slide + TA Engagement": {
            "sets": "3x8/side", "load": "Bodyweight", "rest": "45s", "tempo": "Controlled",
            "setup": "Lie on back, knees bent, feet flat. Press lower back into floor. Find hip bones, slide 1 inch inward and 1 inch down — you're palpating your TA.",
            "execution": "Gently draw navel toward spine (TA tightens but belly does NOT push out). Slowly slide one heel along floor until leg is straight. Slide back. Lower back stays pressed to floor the entire time.",
            "bracing": "TA drawing-in maneuver throughout.",
            "feel": "Deep lower abs (TA), NOT six-pack muscles. If belly pooches or ridge appears, reduce heel slide range.",
            "notes": "Direct diastasis recti rehabilitation. Re-trains deepest core layer."
        },
        "Landmine Press": {
            "sets": "3x10/arm", "load": "Bar only (~25-30 lbs feel)", "rest": "90s", "tempo": "2-1-2-0",
            "setup": "Barbell in landmine. Stand facing bar end. Grab fat end with one hand at shoulder height. Stagger feet (opposite foot forward). Slight forward lean.",
            "execution": "Press bar up and slightly forward (arc is natural). Fully extend arm. Lower slowly to shoulder.",
            "bracing": "Brace core HARD — single arm wants to rotate you. Resist it. Exhale as you press.",
            "feel": "Front shoulder, upper chest, triceps. Core works to prevent rotation (bonus anti-rotation training).",
            "notes": "Landmine arc goes slightly forward, avoiding impingement zone that aggravates right shoulder. Add a 10 when bar only is easy."
        },
        "Pull-Up (band-assisted)": {
            "sets": "3x6-8", "load": "BW minus band", "rest": "90s", "tempo": "Controlled",
            "setup": "Loop 75-120 lb band over pull-up bar. Place one knee or foot in loop. Grip just outside shoulder width, palms away. Start from dead hang — arms fully extended.",
            "execution": "FIRST pull shoulders DOWN and BACK (shoulder blades into back pockets — engages lats before arms). Then pull up by driving elbows DOWN toward hips. Chin over bar. Lower slowly (3s) to full dead hang.",
            "bracing": "Core engaged to prevent swinging.",
            "feel": "Lats (sides of back), biceps, forearms. If only arms, focus on shoulder blade cue.",
            "notes": "Progression: Wk1-2: 75-120lb band, 3x6-8 | Wk3-4: 50-75lb band, 3x5-6 | Wk5-6: 15-35lb band, 3x4-5 | Wk7-8: No band, 3x3-5. Track band + reps every session."
        },
        "DB Lateral Raise": {
            "sets": "3x12", "load": "5 lb DBs", "rest": "60s", "tempo": "2-1-2-0",
            "setup": "Stand tall, feet hip width. Hold 5 lb DBs at sides, palms facing in. Slight bend in elbows (and keep that angle locked throughout).",
            "execution": "Raise both arms out to sides until hands reach shoulder height — no higher. Lead with your ELBOWS, not your hands (imagine pouring a pitcher of water). Lower slowly in 2s. Don't swing or shrug.",
            "bracing": "Light core brace. Shoulders stay DOWN — if traps shrug up, the weight is too heavy.",
            "feel": "Side of shoulder (medial deltoid). If you feel upper traps or front shoulder, lower the weight or focus on leading with elbows.",
            "notes": "Progress: 5 lbs → 8 lbs → 10 lbs. Only go up when 3x15 is clean with no shrugging."
        },
        "Band Face Pull": {
            "sets": "3x15", "load": "Light band (15-35 lb)", "rest": "60s", "tempo": "2-2-2-0",
            "setup": "Anchor band to rack at face height. Grip with both hands, palms facing each other. Step back for tension.",
            "execution": "Pull toward face driving elbows BACK and OUT. At face, rotate fists outward (external rotation) finishing in 'hands up' pose. Squeeze shoulder blades. Hold 2s.",
            "bracing": "Light core brace.",
            "feel": "Rear delts, rotator cuff, mid-back. External rotation at end is critical.",
            "notes": "Shoulder health exercise. Don't skip the external rotation."
        },
        "Close-Grip Bench Press": {
            "sets": "3x10", "load": "65 lbs (bar+10s)", "rest": "60s", "tempo": "2-1-2-0",
            "setup": "Same as bench but grip shoulder width (hands 14-16 inches apart). Elbows tucked to sides.",
            "execution": "Lower bar to lower chest/sternum. Press up.",
            "bracing": "Same as bench press.",
            "feel": "Triceps primarily, chest secondary.",
            "notes": ""
        },
        "KB Curl": {
            "sets": "3x10/arm", "load": "25 lb KB", "rest": "60s", "tempo": "2-1-2-0",
            "setup": "Hold KB by handle at side, arm straight.",
            "execution": "Curl up by bending elbow. KB hangs below fist. Squeeze bicep at top. Lower slowly.",
            "bracing": "Light brace.",
            "feel": "Biceps and forearm (extra grip demand from KB offset center of gravity).",
            "notes": ""
        },
        "KB Hammer Curl": {
            "sets": "3x12", "load": "20-25 lb KB", "rest": "60s", "tempo": "2-1-2-0",
            "setup": "Hold one KB by the BELL (the round part), not the handle — both hands grip the sides of the bell, thumbs up, palms facing each other. This is the neutral 'hammer' grip. Stand tall, elbows pinned to sides.",
            "execution": "Curl the KB up keeping palms facing each other the entire way. Squeeze at top. Lower slowly. Elbows don't move forward.",
            "bracing": "Light core brace. No swinging.",
            "feel": "Brachialis (muscle UNDER the bicep that pushes it up) and brachioradialis (outer forearm). Hammer grip adds forearm and arm width that barbell curls miss. The brachialis is what gives arms that thick, full look from the side.",
            "notes": "This + Barbell Curl = complete arm development. Barbell targets biceps peak, KB Hammer targets arm width and thickness. Grip the BELL not the handle."
        },
        "Band Tricep Pushdown": {
            "sets": "3x12-15", "load": "Medium band (35-50 lb)", "rest": "60s", "tempo": "2-1-2-0",
            "setup": "Anchor band at top of rack or over pull-up bar. Face the anchor. Grip band with both hands, palms down, elbows at sides at 90 degrees.",
            "execution": "Push band straight down by extending elbows. Lock out fully — squeeze triceps hard at the bottom for 1s. Return to 90 degrees only — don't let elbows drift forward. Elbows stay glued to sides.",
            "bracing": "Light core brace. Slight forward lean is OK (10-15 degrees). Shoulders stay down.",
            "feel": "Triceps (all three heads, primarily lateral head — the outer horseshoe). Triceps are 2/3 of upper arm size. This is your most direct arm-size exercise after dips.",
            "notes": "Triceps respond very well to isolation. 12-15 reps + strong squeeze at lockout = maximum pump and muscle signal. Don't rush the reps."
        },
        "Side Plank": {
            "sets": "2x25s/side", "load": "Bodyweight", "rest": "45s", "tempo": "Hold",
            "setup": "On elbow, body straight, feet stacked (or top foot in front). Hips off floor.",
            "execution": "Hold position. Breathe normally. Squeeze bottom oblique. Don't let hips sag.",
            "bracing": "Engage obliques throughout.",
            "feel": "Obliques (side abs). Diastasis-safe unlike front planks.",
            "notes": ""
        },
        "Deep Neck Flexor Hold": {
            "sets": "3x15s", "load": "Bodyweight", "rest": "45s", "tempo": "Hold",
            "setup": "Lie on back, no pillow. Tuck chin (double chin).",
            "execution": "Keeping chin tucked, lift head 1 inch off floor. Hold 15s.",
            "bracing": "Chin tuck maintained throughout.",
            "feel": "FRONT of neck (deep flexors), NOT big muscles on sides (SCM). If sides cramp, head is too high.",
            "notes": "Cervicogenic migraine rehab. Deep neck flexor weakness is a primary contributor to migraines."
        },
        "Barbell RDL": {
            "sets": "3x10-12 (double progression — start 95 lbs)", "load": "95 lbs (bar+2x25s)", "rest": "2 min", "tempo": "3-1-2-0",
            "setup": "Stand feet hip width, bar in front of thighs. Grip just outside legs. Soft knee bend (15-20 degrees — knees stay at this angle, they don't move).",
            "execution": "Push HIPS BACK (like closing a car door with your butt). Bar slides down thighs, staying in contact. Go until strong hamstring stretch (mid-shin to below knee). STOP — don't round back. Drive hips FORWARD to stand. Lock hips, squeeze glutes.",
            "bracing": "MOST BRACING-CRITICAL LIFT. Full 360 degree brace. Maintain neutral spine — imagine broomstick touching head, upper back, tailbone. All three stay in contact. Exhale through teeth standing up.",
            "feel": "HAMSTRINGS on the way down, GLUTES driving up. Lower back holds steady (isometric) — NOT doing the lifting. If lower back is working, you're extending from spine instead of hips.",
            "notes": "Given L5-S1: NEVER round lower back. If back starts rounding, that's your ROM limit — come back up. Short ROM with perfect form > full ROM with rounded back."
        },
        "KB Swing": {
            "sets": "3x15", "load": "35 lb KB", "rest": "60s", "tempo": "Explosive",
            "setup": "Feet slightly wider than hip width. KB on floor 1 foot ahead. Hinge, grab handle with both hands. Flat back.",
            "execution": "Hike KB between legs (like football snap). Explosively drive hips FORWARD — NOT a squat, it's a hip hinge. Hip thrust launches KB. Arms are just ropes. KB floats to chest height. Let it fall, hinge, repeat.",
            "bracing": "Brace on hike back. Exhale sharply ('hah!') as hips lock out. Rhythmic breathing.",
            "feel": "Glutes and hamstrings on the drive. If you feel lower back, focus on VIOLENT hip snap.",
            "notes": "Use 25lb KB if 35 is too heavy for proper form."
        },
        "Barbell Hip Thrust": {
            "sets": "4x10-12 (double progression — start 95 lbs)", "load": "95 lbs (bar+2x25s)", "rest": "90s", "tempo": "2-2-2-0",
            "setup": "Sit on floor, back against bench at mid-scapula. Bar across hip crease (use balance pad as bar pad). Feet flat, shoulder width, 18 inches from butt. Toes slightly out.",
            "execution": "Drive through heels to extend hips. At top: torso parallel to floor, shins vertical. SQUEEZE GLUTES HARD — hold 2s. Lower slowly. Don't hyperextend lower back — tuck tailbone slightly (posterior pelvic tilt).",
            "bracing": "Light brace. Exhale driving up.",
            "feel": "GLUTES. If hamstrings, bring feet closer. If lower back, focus on tailbone tuck.",
            "notes": "Hernia-friendly (you're on your back). Progress weight fast — glutes can handle more than you think."
        },
        "Band Glute Kickback": {
            "sets": "3x15/leg", "load": "Medium loop band around ankles", "rest": "45s", "tempo": "2-2-2-0",
            "setup": "Anchor loop band to rack post low or use ankle strap attachment on cable. Face the anchor. Hold rack for balance. Slight forward lean at hips (15-20 degrees). Plant one foot, kick back with the other.",
            "execution": "Drive the banded leg STRAIGHT BACK — think heel to ceiling. At full extension, squeeze glute HARD for 2 full seconds. Lower slowly (don't let band snap leg forward). Keep hips square — don't rotate. Small range of motion > swinging.",
            "bracing": "Light core brace. Hips stay level — no tilting to the side.",
            "feel": "Glute max, the large outer/upper butt muscle. This is the isolation move that sculpts glute shape. You should feel NOTHING in your lower back. If you do, reduce range of motion.",
            "notes": "Glute max is the #1 aesthetic muscle for the glutes. Hip thrust builds overall size. Kickback sculpts shape. Do these slow — the squeeze is everything."
        },
        "Bird Dog": {
            "sets": "3x8/side", "load": "Bodyweight", "rest": "45s", "tempo": "3-2-3-0",
            "setup": "On hands and knees, hands under shoulders, knees under hips.",
            "execution": "Extend opposite arm and leg simultaneously. Hold 2s at top. Return. Don't let hips rotate — imagine balancing a cup of water on lower back.",
            "bracing": "Core engaged, spine neutral.",
            "feel": "Deep core stabilizers. Anti-extension challenge.",
            "notes": "McGill Big 3 staple."
        },
        "Barbell Back Squat": {
            "sets": "8×5 clusters (Ph1) → 10×4 (Ph2) → 12×3 (Ph3)", "load": "Ph1: 95 lbs | Ph2: 115 lbs | Ph3: 120 lbs", "rest": "60s between cluster sets, 3 min between exercises", "tempo": "3-1-2-0",
            "setup": "Bar on upper traps (low-bar) or upper back (high-bar). Use squat pad if needed. Feet shoulder-width, toes out 20-30 degrees. Brace before unracking. Step back — one foot, then the other. Set stance.",
            "execution": "Break at hips AND knees simultaneously — sit BETWEEN legs, not back. Knees track over toes the entire descent. Hit parallel (hip crease at or below knee cap). Drive UP through the full foot — push the floor away. Elbows slightly forward to keep torso upright. Lockout at top, squeeze glutes.",
            "bracing": "BIG breath at top. Fill belly, sides, lower back. Brace 360 degrees. HOLD BREATH through the descent AND out of the hole. Exhale through clenched teeth driving up. Re-breathe fully at lockout before next rep. NEVER lose brace at the bottom — that's when injury happens.",
            "feel": "Quads, glutes, adductors (inner thigh). Upper back works hard as a shelf. Lower back should be isometrically stable, not doing the work.",
            "notes": "T1 CLUSTER LIFT — Power Wednesday. Phase loads: Ph1: 95 lbs (bar+25s). Ph2: 115 lbs (bar+35s). Ph3: 120 lbs (bar+35s+2x2.5). WEEK 1: Start conservatively at 95 lbs. If it moves fast and clean, that's your Ph1 weight. If it grinds, drop to 75 or 85. Knees tracking over toes is correct — do NOT push knees inward. Lower back note (L5-S1): belt recommended at Ph2+. If lower back aches after, check depth (don't go below parallel until more experienced)."
        },
        "Conventional Deadlift": {
            "sets": "6×4 clusters (Ph1) → 8×4 (Ph2) → 10×3 (Ph3)", "load": "Ph1: 135 lbs | Ph2: 155 lbs | Ph3: 165 lbs", "rest": "60s between cluster sets, 4 min between exercises", "tempo": "2-1-2-0 (controlled, not slow)",
            "setup": "Feet hip-width. Bar over mid-foot (1 inch from shins). Hinge down and grip just outside legs (double overhand). Push hips back until shins contact bar. Chest up, shoulders slightly in front of bar. Arms vertical. Take the slack out of the bar by pulling UPWARD before the weight leaves the floor — you'll hear a small click.",
            "execution": "Drive the floor AWAY (think leg press, not 'pull up'). Hips and shoulders rise at the same rate — don't let hips shoot up first (that turns it into an ugly good morning). Bar stays in contact with shins/thighs the whole way up. Stand tall at lockout — hips FULLY extended, glutes squeezed. Lower under control: hinge at hips first, then bend knees when bar passes them. Reset fully between reps on clusters.",
            "bracing": "MAXIMUM BRACE. Before pulling: big breath, 360 brace, squeeze lats INTO BACK POCKETS (lat engagement prevents lumbar rounding). Hold breath through entire pull. Exhale only at full lockout. This is the most compressive lift — brace harder than anything else.",
            "feel": "Hamstrings and glutes driving the pull. Upper back (lats, traps) keeping the bar close. Quads involved in the initial drive. If lower back is the limiting factor, your hips are too low (you're trying to squat it) or too high (turning it into a back-dominant lift). Hip hinge is the Goldilocks position.",
            "notes": "T1 CLUSTER LIFT — Power Wednesday. Phase: 6×4 in Ph1 (not 8×5 like upper lifts — deadlifts are more CNS-demanding). Ph2: 8×4. Ph3: 10×3. Phase loads: 135 (bar+45s) → 155 (bar+55s — use 45+5+2.5 each side) → 165 (bar+60s each side). WEEK 1: Test at 135 lbs. If fast, that's your weight. Lumbar note (L5-S1): if lower back is sore the next day, take an extra rest day before Wednesday next week. Wear a belt from Ph2 onwards. Never round the lower back."
        },
        "Barbell OHP (Standing)": {
            "sets": "4x8-10 (double progression — start 65 lbs)", "load": "65 lbs (bar+2x10s)", "rest": "90s", "tempo": "2-1-2-0",
            "setup": "Stand feet hip-width. Clean bar to shoulder height or take from rack at shoulder level. Grip just outside shoulder width. Bar rests on front delts/collarbones. Elbows slightly in front of bar (not pointing straight down). Brace hard before every rep.",
            "execution": "Press bar STRAIGHT UP — your head moves BACK slightly to make room, then returns under the bar at lockout (the bar finishes directly over the ears/base of skull). At lockout: arms fully locked, shoulders shrugged up slightly (active lockout). Lower bar back to collarbones in 2s. Do NOT press at an angle forward — bar path is vertical.",
            "bracing": "This is a standing lift — brace is everything. Big breath. 360 brace. Squeeze glutes to prevent lumbar hyperextension. Exhale through teeth pressing up. Inhale at shoulder. Re-brace each rep. If lower back arches noticeably, brace harder or reduce weight.",
            "feel": "Front and lateral deltoids (the entire shoulder), upper chest, triceps. Upper traps at lockout. This builds shoulder width AND creates the appearance of broader shoulders from the front.",
            "notes": "T1 HYPERTROPHY — Upper Hypertrophy Thursday. Double progression: hit 4x10 on all 4 sets → add 5 lbs next session. Starting: 65 lbs (bar+10s). Progression: 65 → 70 → 75 → 80 lbs. Shoulder note: bar path is slightly forward on the way DOWN (bar clears the face) and then comes back to vertical. This is normal — don't fight it. If shoulder impingement, try a slightly wider grip or reduce range of motion slightly at the bottom."
        },
    }

    # --- Per-Exercise Breathing Guide ---
    BREATHING = {
        "Barbell Bench Press": "INHALE at the top (arms locked out). Hold breath as you lower the bar to your chest (3s down). EXHALE through clenched teeth ('tsssss') as you press the bar back up. Arms lock out → inhale again → repeat.",
        "Barbell Bent-Over Row": "INHALE and brace hard before pulling (arms hanging straight). HOLD breath as you pull bar to ribs. EXHALE through teeth as you lower the bar back down. At bottom (arms straight) → inhale, re-brace → pull again. If your back starts rounding, the brace failed — stop.",
        "Push-Up": "INHALE at the top (arms locked, plank position). Hold breath as you lower chest to floor (3s). EXHALE through teeth as you press back up. Core brace NEVER fully releases — maintain tension the entire set.",
        "Landmine Row (single arm)": "INHALE and lightly brace before pulling. Pull to hip. EXHALE through teeth as you lower weight back down. Inhale at bottom → repeat. Keep torso still — the light brace prevents rotation.",
        "Dips (Matador)": "INHALE at the top (arms locked). Hold breath as you lower yourself down. EXHALE through teeth as you press back up through the hardest part (the bottom). Inhale at top → repeat.",
        "Barbell Curl": "INHALE at the bottom (arms straight). EXHALE through teeth as you curl up. INHALE as you lower slowly. Light core brace throughout to prevent swaying — if you feel your lower back, you're swinging.",
        "Pallof Press": "INHALE with band at your chest, draw navel in (TA activation), then brace. EXHALE through teeth as you press the band out. Take small shallow breaths during the 2s hold. Pull band back → full inhale → re-brace → press again.",
        "Dead Bug": "INHALE with arms up and knees in tabletop. As you extend opposite arm and leg outward, EXHALE FULLY — empty your lungs completely. The full exhale forces your deep core (TA) to fire and keeps lower back pressed to floor. Limbs return → inhale → switch sides → exhale fully again.",
        "Barbell Front Squat": "INHALE big at the top (standing). Fill belly, sides, and lower back. Brace hard 360 degrees. HOLD BREATH as you squat down — hold it at the bottom too. EXHALE through clenched teeth as you drive up out of the hole. Stand fully → release air → fresh inhale → re-brace → descend again. NEVER let core go slack at the bottom.",
        "Slant Board Squat": "INHALE at the top (standing). Light brace. EXHALE through teeth on the way up. Simple — this is a lighter exercise.",
        "Barbell Reverse Lunge": "INHALE and brace BEFORE each step back — every rep gets a fresh brace. Hold breath as you step back and lower. EXHALE through teeth as you drive back up to standing. Re-breathe and re-brace before the next step.",
        "Nordic Curl Eccentric": "INHALE kneeling upright. Squeeze glutes, brace core. EXHALE very slowly through teeth as you lean forward — make the exhale last the ENTIRE 5-second descent. Catch with hands, push back up → inhale → re-brace → go again.",
        "Wall Sit": "BREATHE NORMALLY. Do NOT hold your breath — you're holding this for 30+ seconds. Mild brace, press lower back into wall, breathe in and out through your nose at a natural rhythm.",
        "Heel Slide + TA Engagement": "Draw navel toward spine (TA engaged — stays engaged entire time). INHALE at start. EXHALE slowly as you slide one heel out. INHALE as you slide it back in. TA never releases between reps.",
        "Landmine Press": "INHALE with bar at shoulder. Brace core HARD (single arm wants to rotate you). EXHALE through teeth as you press up. INHALE as you lower to shoulder. Re-brace each rep.",
        "Pull-Up (band-assisted)": "INHALE at dead hang (arms fully extended). Pull shoulders down and back. EXHALE through teeth as you pull up (chin over bar). INHALE as you lower slowly (3s) back to dead hang. Core stays engaged to prevent swinging.",
        "DB Lateral Raise": "INHALE with arms at your sides. EXHALE through teeth as you raise both arms out to shoulder height. INHALE as you lower slowly (2s). Light brace throughout. Shoulders stay DOWN — don't shrug.",
        "Band Face Pull": "INHALE with arms extended toward anchor. EXHALE through teeth as you pull toward your face and rotate fists outward. Hold 2s (shallow breaths). INHALE as you return to start.",
        "Close-Grip Bench Press": "Same as bench press: INHALE at the top (arms locked). Hold breath as you lower to sternum. EXHALE through teeth as you press up. Inhale at lockout → repeat.",
        "KB Curl": "INHALE at bottom (arm straight). EXHALE through teeth as you curl up. INHALE as you lower slowly. Light brace to prevent swaying.",
        "Side Plank": "BREATHE NORMALLY throughout the hold. In through nose, out through nose. Don't hold your breath — you'll pass out before the timer runs out. Keep obliques engaged while breathing.",
        "Deep Neck Flexor Hold": "BREATHE NORMALLY throughout. In through nose, out through mouth. Maintain chin tuck while breathing — the tendency is to lose the tuck when you focus on breathing. Keep it.",
        "Barbell RDL": "MOST CRITICAL BREATHING. INHALE big at the top — biggest breath of any exercise. Fill everything. Brace as hard as you can. HOLD BREATH ENTIRELY as you push hips back and lower the bar. At the bottom (hamstring stretch) you're still holding. EXHALE through clenched teeth as you drive hips forward to stand. Lock hips, squeeze glutes → release air → fresh big inhale → re-brace. If you feel the brace failing, end the set.",
        "KB Swing": "UNIQUE RHYTHM. INHALE as you hike the KB back between your legs. EXHALE sharply — a forceful 'HAH!' out of your mouth — as your hips snap forward. This is the one exercise where you exhale through your mouth with force because the movement is explosive. It becomes rhythmic: hike back (inhale) → hip snap (HAH!) → repeat.",
        "Barbell Hip Thrust": "INHALE at the bottom (hips low). Light brace. EXHALE through teeth as you drive hips up. At the top, squeeze glutes 2s — take small breaths during the hold. Lower slowly → inhale at bottom → repeat.",
        "Bird Dog": "INHALE in starting position (hands and knees). EXHALE slowly as you extend opposite arm and leg. Hold 2s at top — small breath. INHALE as you return. Core stays engaged throughout.",
        "Barbell Incline Press": "INHALE at the top (arms extended). Brace core. Hold breath as you lower bar to upper chest (3s). EXHALE through teeth as you press up. Re-breathe at lockout.",
        "KB Hammer Curl": "INHALE at the bottom (arms straight). EXHALE through teeth as you curl up. INHALE as you lower slowly. Light brace throughout — no swinging.",
        "Band Tricep Pushdown": "INHALE before rep with elbows at 90 degrees. EXHALE through teeth as you push band down and lock out. Hold the lockout 1s — take a tiny breath. Return to 90 degrees → fresh inhale → push again.",
        "Band Glute Kickback": "BREATHE NORMALLY on the plant leg. EXHALE through teeth as you kick back and squeeze at the top. The exhale helps you squeeze harder. INHALE as you return leg. Slow, controlled — this is isolation work, not cardio.",
        "Barbell Back Squat": "INHALE big at the top (standing, bar on back). Fill belly, sides, and lower back — biggest breath of any exercise. Brace 360 degrees HARD. HOLD BREATH as you descend — hold it at the bottom too. EXHALE forcefully through clenched teeth as you drive up out of the hole. Stand fully → release air → fresh deep inhale → re-brace → descend again. NEVER lose your brace at the bottom — that's the highest-risk moment. On cluster rest: breathe normally, then do the full brace sequence before each new cluster.",
        "Conventional Deadlift": "MAXIMUM BRACE PROTOCOL. Stand over bar. INHALE big — biggest possible breath. Brace 360 degrees as hard as you can — imagine someone is about to punch you in the gut AND in the lower back simultaneously. HOLD BREATH ENTIRELY through the whole pull — from liftoff through full lockout. EXHALE only when fully standing and locked out. Lower back to floor → RE-BREATHE COMPLETELY before the next rep. Never start a new rep without a full reset breath and brace. If you feel your brace failing mid-rep, don't grind — it's better to miss the lift.",
        "Barbell OHP (Standing)": "INHALE with bar at shoulders. BRACE (glutes too — prevents hyperextension). EXHALE through teeth pressing up. INHALE as you lower back to shoulders. Brace must be maintained every rep — this is a standing lift and the lower back is exposed. If you feel lower back engaging to 'lean into' the press, your brace failed — reduce weight.",
    }

    # --- Session Layouts ---
    # PHAT × Cluster Hybrid | Mon/Wed = Power (T1 clusters) | Thu/Fri = Hypertrophy (double progression)
    SESSIONS = {
        "Upper Power — Monday": [
            ("T1a", "Barbell Bench Press"),
            ("T1b", "Barbell Bent-Over Row"),
            ("T2a", "Barbell Incline Press"),
            ("T2b", "Landmine Row (single arm)"),
            ("T3a", "DB Lateral Raise"),
            ("T3b", "Band Tricep Pushdown"),
            ("T3c", "Barbell Curl"),
            ("C", "Dead Bug"),
        ],
        "Lower Power — Wednesday": [
            ("T1a", "Barbell Back Squat"),
            ("T1b", "Conventional Deadlift"),
            ("T2a", "Barbell RDL"),
            ("T2b", "Nordic Curl Eccentric"),
            ("T3", "Band Glute Kickback"),
            ("C", "Bird Dog"),
        ],
        "Upper Hypertrophy — Thursday": [
            ("T1a", "Barbell OHP (Standing)"),
            ("T1b", "Pull-Up (band-assisted)"),
            ("T2a", "Close-Grip Bench Press"),
            ("T2b", "Band Face Pull"),
            ("T3a", "KB Hammer Curl"),
            ("T3b", "Dips (Matador)"),
            ("C", "Side Plank"),
        ],
        "Lower Hypertrophy — Friday": [
            ("T1a", "Barbell Hip Thrust"),
            ("T1b", "Barbell Reverse Lunge"),
            ("T2a", "Slant Board Squat"),
            ("T2b", "KB Swing"),
            ("T3", "Band Glute Kickback"),
            ("C", "Heel Slide + TA Engagement"),
        ],
    }

    # --- Render Sessions ---
    st.markdown("### PHAT × Cluster Hybrid Program — 8-Week Protocol")
    for session_name, exercises in SESSIONS.items():
        with st.expander(f"**{session_name}**", expanded=False):
            # Determine warm-up type
            is_upper = "Upper" in session_name
            if is_upper:
                st.caption("Warm-up: CNS Block (Iron Neck + Breathing + Pull-Aparts) → Shoulder ER (8lb DB) 2x10/arm → Prone Y-Raise 1x8 → Empty Bar Groove 2x8")
            else:
                st.caption("Warm-up: CNS Block (Iron Neck + Breathing + Pull-Aparts) → 90/90 Hip Switches 8/side → Slant Board Squat 1x10 → Band TKE 1x12/leg → Empty Bar Groove 2x8")

            for order, ex_name in exercises:
                ex = EXERCISES[ex_name]
                st.markdown(f"#### {order}. {ex_name}")
                col_l, col_r = st.columns([1, 1])
                with col_l:
                    st.markdown(f"**Sets/Reps:** {ex['sets']}")
                    st.markdown(f"**Load:** {ex['load']}")
                with col_r:
                    st.markdown(f"**Rest:** {ex['rest']}")
                    st.markdown(f"**Tempo:** {ex['tempo']}")

                st.markdown(f"**Setup:** {ex['setup']}")
                st.markdown(f"**Execution:** {ex['execution']}")
                st.markdown(f"**Bracing:** {ex['bracing']}")
                if ex_name in BREATHING:
                    st.markdown(f"**🫁 Breathing:** {BREATHING[ex_name]}")
                st.markdown(f"**Where you feel it:** {ex['feel']}")
                if ex['notes']:
                    st.warning(f"{ex['notes']}")
                st.markdown("---")

            # Friday conditioning finisher
            if "Friday" in session_name:
                st.markdown("#### Conditioning Finisher")
                st.markdown("**D1. KB Complex** — 4 rounds x 5 reps each: Swing → Clean → Front Squat (NO press — shoulder safe). Use 25lb or 35lb KB. Rest 90s between rounds.")
                st.markdown("**D2. Weighted Vest Walk** — 3 x 2 min at 20 lbs vest (increase 2.5 lbs/week). Brisk pace, chest up, core braced.")
                st.markdown("**D3. Diaphragmatic Breathing** — 3 min cooldown. 10 slow breaths. Parasympathetic shift.")

    # Conditioning Tuesday
    with st.expander("**Conditioning A — Tuesday (Zone 2 + Corrective)**", expanded=False):
        st.markdown("""
**A. Zone 2 Cardio — 30 min** (HR 125-145 bpm, nasal breathing)
- Weighted vest brisk walk outdoors (20-25 lbs)
- Jump rope intervals (30s on / 30s walk) if knee tolerates
- Outdoor jog at conversation pace

**B. Corrective Flow — 15 min**
- 90/90 hip switches x8/side
- World's greatest stretch x5/side
- Cat-cow x10
- Thoracic rotation on bench x8/side
- Prone Y-T-W (5lb DBs) 1x8 each
- Serratus wall slide 1x10
- Seated shoulder ER (8lb DB) 1x10/arm
- Iron Neck — 2x10 each direction (extended session)

**C. Diastasis Recti Focus — 5 min**
- Heel slides 2x8/side
- Dead bug 2x10/side
- TA vacuum hold (draw navel in, hold 10s) x3
""")

    # --- Phase Progression Reference ---
    st.divider()
    st.markdown("### Phase Progression — PHAT × Cluster Hybrid")
    phase_df = pd.DataFrame([
        {"Phase": "Phase 1", "Weeks": "1–3", "Cluster Format": "8×5", "Bench/Row Load": "80 / 65 lbs", "Squat/DL Load": "95 / 135 lbs", "Intra-Rest": "30–45s", "Tirz": "2.5mg"},
        {"Phase": "Phase 2", "Weeks": "4–6", "Cluster Format": "10×4", "Bench/Row Load": "95 / 75 lbs", "Squat/DL Load": "115 / 155 lbs", "Intra-Rest": "30–45s", "Tirz": "5mg"},
        {"Phase": "Phase 3", "Weeks": "7–8", "Cluster Format": "12×3", "Bench/Row Load": "100 / 80 lbs", "Squat/DL Load": "120 / 165 lbs", "Intra-Rest": "30–45s", "Tirz": "5mg"},
    ])
    st.dataframe(phase_df, use_container_width=True, hide_index=True)

    st.caption("Power days (Mon/Wed): T1 compound lifts use cluster sets — fixed load per phase. Hypertrophy days (Thu/Fri): straight sets with double progression (fill all reps → add 5 lbs). Tirz 5mg weeks 5–8: prioritize protein ≥160g/day. If recovery is impaired, drop 1–2 cluster sets rather than skipping the session.")

    # --- Load Tracker ---
    st.divider()
    st.markdown("### Load Progression Tracker")
    st.caption("Enter your Testing Day results to auto-calculate phase loads.")

    def load_training_log():
        try:
            client = get_gsheet_client()
            sheet = client.open(SHEET_NAME)
            try:
                ws = sheet.worksheet("training_log")
            except:
                ws = sheet.add_worksheet(title="training_log", rows=100, cols=10)
                ws.update_cell(1, 1, "key")
                ws.update_cell(1, 2, "value")
            records = ws.get_all_records()
            log = {}
            for r in records:
                if r.get("key") and r.get("value"):
                    try:
                        log[r["key"]] = float(r["value"])
                    except:
                        log[r["key"]] = r["value"]
            return log
        except:
            return {}

    def save_training_log(log_data):
        try:
            client = get_gsheet_client()
            sheet = client.open(SHEET_NAME)
            try:
                ws = sheet.worksheet("training_log")
            except:
                ws = sheet.add_worksheet(title="training_log", rows=100, cols=10)
                ws.update_cell(1, 1, "key")
                ws.update_cell(1, 2, "value")
            rows = [["key", "value"]]
            for k, v in sorted(log_data.items()):
                rows.append([k, str(v)])
            ws.clear()
            ws.update("A1", rows)
        except Exception as e:
            st.error(f"Error saving: {e}")

    if "training_log" not in st.session_state:
        st.session_state.training_log = load_training_log()

    test_exercises = ["Bench Press", "Bent-Over Row", "Front Squat", "Barbell RDL", "Hip Thrust", "Landmine Press", "Barbell Curl"]

    with st.form("test_weights"):
        st.markdown("**Testing Day Results (8-rep RPE 7-8 weight in lbs)**")
        test_vals = {}
        cols_test = st.columns(4)
        for i, ex in enumerate(test_exercises):
            with cols_test[i % 4]:
                existing_val = st.session_state.training_log.get(f"test_{ex}", 0.0)
                test_vals[ex] = st.number_input(ex, 0.0, 500.0, float(existing_val), 5.0, key=f"test_{ex}")

        st.markdown("**Bodyweight Tests**")
        bw_cols = st.columns(3)
        with bw_cols[0]:
            pullup_reps = st.number_input("Pull-Up max reps", 0, 30, int(st.session_state.training_log.get("test_pullup_reps", 0)), key="test_pullups")
        with bw_cols[1]:
            dip_reps = st.number_input("Dip max reps", 0, 30, int(st.session_state.training_log.get("test_dip_reps", 0)), key="test_dips")
        with bw_cols[2]:
            nordic_reps = st.number_input("Nordic eccentric reps (5s)", 0, 20, int(st.session_state.training_log.get("test_nordic_reps", 0)), key="test_nordics")

        if st.form_submit_button("Save Test Results", type="primary", use_container_width=True):
            log = st.session_state.training_log
            for ex, val in test_vals.items():
                log[f"test_{ex}"] = val
            log["test_pullup_reps"] = pullup_reps
            log["test_dip_reps"] = dip_reps
            log["test_nordic_reps"] = nordic_reps
            save_training_log(log)
            st.session_state.training_log = log
            st.success("Test results saved!")

    # Show calculated loads
    has_tests = any(st.session_state.training_log.get(f"test_{ex}", 0) > 0 for ex in test_exercises)
    if has_tests:
        st.markdown("**Calculated Working Loads (lbs)**")
        load_rows = []
        for ex in test_exercises:
            test_val = st.session_state.training_log.get(f"test_{ex}", 0)
            if test_val > 0:
                load_rows.append({
                    "Exercise": ex,
                    "Test (8RM)": test_val,
                    "Ph0 (x0.85)": round(test_val * 0.85, 0),
                    "Ph1 (x0.70)": round(test_val * 0.70, 0),
                    "Ph2 (x0.75)": round(test_val * 0.75, 0),
                    "Ph3 (x0.80)": round(test_val * 0.80, 0),
                })
        st.dataframe(pd.DataFrame(load_rows), use_container_width=True, hide_index=True)

    # --- Corrective Goals ---
    st.divider()
    st.markdown("### Corrective Focus Areas")
    corr_col1, corr_col2, corr_col3 = st.columns(3)
    with corr_col1:
        st.markdown("**Diastasis Recti**")
        st.markdown("- TA activation every warm-up\n- All core = anti-movement\n- Heel slides + vacuum holds\n- No crunches/sit-ups EVER")
    with corr_col2:
        st.markdown("**Shoulder Realignment**")
        st.markdown("- Shoulder ER every upper day\n- Prone Y-raises for lower traps\n- Band pull-aparts daily\n- Landmine press (safe arc)")
    with corr_col3:
        st.markdown("**Cervicogenic Migraines**")
        st.markdown("- Iron Neck opens every session\n- Deep neck flexor holds\n- Front squat (no bar on traps)\n- No heavy shrugs")
# ============================================================
# NUTRITION — FOOD DATABASE & HELPER FUNCTIONS
# ============================================================

FOODS = {
    # PROTEINS (per 100g cooked unless noted)
    "Chicken breast (cooked)":          {"p": 31,  "f": 3.6, "c": 0,   "kcal": 165, "cat": "protein", "unit": "g"},
    "Chicken thigh skinless (cooked)":  {"p": 26,  "f": 9,   "c": 0,   "kcal": 185, "cat": "protein", "unit": "g"},
    "Ground beef 93% lean (cooked)":    {"p": 28,  "f": 7,   "c": 0,   "kcal": 175, "cat": "protein", "unit": "g"},
    "Ground turkey 99% lean (cooked)":  {"p": 29,  "f": 2,   "c": 0,   "kcal": 136, "cat": "protein", "unit": "g"},
    "Pork tenderloin (cooked)":         {"p": 26,  "f": 3,   "c": 0,   "kcal": 143, "cat": "protein", "unit": "g"},
    "Flank/skirt steak (cooked)":       {"p": 28,  "f": 8,   "c": 0,   "kcal": 185, "cat": "protein", "unit": "g"},
    "Bison ground (cooked)":            {"p": 26,  "f": 7,   "c": 0,   "kcal": 168, "cat": "protein", "unit": "g"},
    "Salmon fillet (cooked)":           {"p": 25,  "f": 13,  "c": 0,   "kcal": 208, "cat": "protein", "unit": "g"},
    "Barramundi (cooked)":              {"p": 24,  "f": 2,   "c": 0,   "kcal": 118, "cat": "protein", "unit": "g"},
    "Canned tuna in water":             {"p": 26,  "f": 1,   "c": 0,   "kcal": 116, "cat": "protein", "unit": "g"},
    "Smoked salmon":                    {"p": 18,  "f": 4,   "c": 0,   "kcal": 117, "cat": "protein", "unit": "g"},
    "Eggs whole (per 2 large)":         {"p": 12,  "f": 10,  "c": 1,   "kcal": 143, "cat": "protein", "unit": "portion (2 eggs)"},
    "Egg whites liquid (100ml)":        {"p": 11,  "f": 0,   "c": 0.4, "kcal": 48,  "cat": "protein", "unit": "g"},
    "Siggi's Icelandic yogurt (150g)":  {"p": 15,  "f": 4,   "c": 10,  "kcal": 130, "cat": "protein", "unit": "portion (150g cup)"},
    "Cottage cheese (150g)":            {"p": 17,  "f": 3,   "c": 5,   "kcal": 115, "cat": "protein", "unit": "portion (150g)"},
    "ISO100 Whey (1 scoop ~30g)":       {"p": 25,  "f": 1,   "c": 3,   "kcal": 120, "cat": "protein", "unit": "scoop"},
    # CARBS (per 100g cooked unless noted)
    "White rice (cooked)":              {"p": 2.5, "f": 0.3, "c": 28,  "kcal": 130, "cat": "carb",    "unit": "g"},
    "Sweet potato cubed (roasted)":     {"p": 1.8, "f": 0.2, "c": 20,  "kcal": 86,  "cat": "carb",    "unit": "g"},
    "Baby potato (boiled)":             {"p": 2,   "f": 0.1, "c": 17,  "kcal": 77,  "cat": "carb",    "unit": "g"},
    "Lentils (cooked)":                 {"p": 9,   "f": 0.4, "c": 20,  "kcal": 116, "cat": "carb",    "unit": "g"},
    "Quinoa (cooked)":                  {"p": 4,   "f": 2,   "c": 22,  "kcal": 120, "cat": "carb",    "unit": "g"},
    "Oats rolled dry (40g)":            {"p": 5,   "f": 3,   "c": 27,  "kcal": 152, "cat": "carb",    "unit": "portion (40g)"},
    "Banana (medium)":                  {"p": 1,   "f": 0,   "c": 23,  "kcal": 89,  "cat": "carb",    "unit": "piece"},
    "Apple":                            {"p": 0.3, "f": 0.2, "c": 21,  "kcal": 81,  "cat": "carb",    "unit": "piece"},
    # VEGETABLES & FATS (per 100g unless noted)
    "Broccoli":                         {"p": 2.8, "f": 0.4, "c": 7,   "kcal": 34,  "cat": "veg",     "unit": "g"},
    "Zucchini":                         {"p": 1.2, "f": 0.3, "c": 3,   "kcal": 17,  "cat": "veg",     "unit": "g"},
    "Cauliflower":                      {"p": 1.9, "f": 0.3, "c": 5,   "kcal": 25,  "cat": "veg",     "unit": "g"},
    "Green beans":                      {"p": 1.8, "f": 0.1, "c": 7,   "kcal": 31,  "cat": "veg",     "unit": "g"},
    "Baby spinach":                     {"p": 2.9, "f": 0.4, "c": 3.6, "kcal": 23,  "cat": "veg",     "unit": "g"},
    "Mushrooms cremini":                {"p": 2.5, "f": 0.1, "c": 3.3, "kcal": 22,  "cat": "veg",     "unit": "g"},
    "Bell peppers":                     {"p": 1,   "f": 0.3, "c": 6,   "kcal": 31,  "cat": "veg",     "unit": "g"},
    "Cherry tomatoes":                  {"p": 0.9, "f": 0.2, "c": 3.9, "kcal": 18,  "cat": "veg",     "unit": "g"},
    "Cucumber":                         {"p": 0.7, "f": 0.1, "c": 3.6, "kcal": 15,  "cat": "veg",     "unit": "g"},
    "Avocado (37g portion)":            {"p": 0.9, "f": 7,   "c": 3,   "kcal": 59,  "cat": "fat",     "unit": "portion (37g)"},
    "Olive oil (1 tsp)":                {"p": 0,   "f": 4.5, "c": 0,   "kcal": 40,  "cat": "fat",     "unit": "tsp"},
    "Avocado oil (1 tsp)":              {"p": 0,   "f": 4.5, "c": 0,   "kcal": 40,  "cat": "fat",     "unit": "tsp"},
    # RECIPES (fixed macros per serving)
    "Ground Beef Breakfast Power Bowl":      {"p": 52, "f": 22, "c": 4,  "kcal": 420, "cat": "recipe", "unit": "serving"},
    "Cottage Cheese & Siggi's Savory Bowl":  {"p": 34, "f": 8,  "c": 12, "kcal": 255, "cat": "recipe", "unit": "serving"},
    "Smoked Salmon & Avocado Plate":         {"p": 35, "f": 14, "c": 8,  "kcal": 295, "cat": "recipe", "unit": "serving"},
    "Turkish-Spiced Chicken Breakfast Bowl": {"p": 65, "f": 18, "c": 10, "kcal": 455, "cat": "recipe", "unit": "serving"},
    "Protein Smoothie Bowl":                 {"p": 48, "f": 6,  "c": 28, "kcal": 355, "cat": "recipe", "unit": "serving"},
    "Korean Ground Turkey Bowl (no rice)":   {"p": 55, "f": 8,  "c": 10, "kcal": 335, "cat": "recipe", "unit": "serving"},
    "Korean Ground Turkey Bowl (w/ rice)":   {"p": 58, "f": 8,  "c": 38, "kcal": 465, "cat": "recipe", "unit": "serving"},
    "Mexican Picanha Bowl":                  {"p": 55, "f": 14, "c": 22, "kcal": 435, "cat": "recipe", "unit": "serving"},
    "Peruvian Salmon Ceviche":               {"p": 45, "f": 14, "c": 2,  "kcal": 310, "cat": "recipe", "unit": "serving"},
    "Pan-Seared Barramundi + Spinach":       {"p": 48, "f": 12, "c": 3,  "kcal": 310, "cat": "recipe", "unit": "serving"},
    "Tuna Tartare with Avocado":             {"p": 46, "f": 18, "c": 6,  "kcal": 370, "cat": "recipe", "unit": "serving"},
    "Asian Salmon with Bok Choy":            {"p": 48, "f": 20, "c": 8,  "kcal": 400, "cat": "recipe", "unit": "serving"},
    "Greek Chicken & Cucumber Salad":        {"p": 50, "f": 18, "c": 8,  "kcal": 390, "cat": "recipe", "unit": "serving"},
    "Classic Greek Salad + Chicken 180g":    {"p": 58, "f": 12, "c": 8,  "kcal": 370, "cat": "recipe", "unit": "serving"},
    "ISO100 + Cottage Cheese Shake":         {"p": 42, "f": 4,  "c": 8,  "kcal": 235, "cat": "recipe", "unit": "serving"},
}

DAY_TYPE_TARGETS = {
    "Training Day": {"kcal": 1700, "protein": 160, "carbs": 145, "fat": 53},
    "4x4 Day":      {"kcal": 1800, "protein": 160, "carbs": 175, "fat": 53},
    "Rest Day":     {"kcal": 1350, "protein": 160, "carbs": 65,  "fat": 52},
}

NUTRITION_LOG_HEADERS = ["date", "day_type", "meal", "food_item", "amount", "protein", "carbs", "fat", "kcal", "saved_at"]


def load_nutrition_log():
    """Load nutrition log from Google Sheets."""
    try:
        ss = get_spreadsheet()
        ws = get_or_create_worksheet(ss, "nutrition_log", NUTRITION_LOG_HEADERS)
        return ws.get_all_records()
    except Exception as e:
        st.warning(f"Could not load nutrition log: {e}")
        return []


def save_nutrition_entries(date_str, day_type, meal_log):
    """Overwrite all rows for this date and save current meal log."""
    try:
        ss = get_spreadsheet()
        ws = get_or_create_worksheet(ss, "nutrition_log", NUTRITION_LOG_HEADERS)
        existing = ws.get_all_records()
        saved_at = datetime.now().isoformat()
        new_rows = [NUTRITION_LOG_HEADERS]
        for row in existing:
            if str(row.get("date", "")) != date_str:
                new_rows.append([row.get(h, "") for h in NUTRITION_LOG_HEADERS])
        for meal_name, entries in meal_log.items():
            for entry in entries:
                new_rows.append([
                    date_str, day_type, meal_name, entry["food"],
                    round(float(entry["amount"]), 2),
                    round(float(entry["protein"]), 1),
                    round(float(entry["carbs"]), 1),
                    round(float(entry["fat"]), 1),
                    round(float(entry["kcal"]), 1),
                    saved_at,
                ])
        ws.clear()
        ws.update("A1", new_rows)
        ws.format("A1:J1", {"textFormat": {"bold": True}})
        return True
    except Exception as e:
        st.error(f"Could not save nutrition log: {e}")
        return False


def calc_food_macros(food_name, amount):
    """Calculate macros for a food given the amount."""
    f = FOODS[food_name]
    factor = (amount / 100.0) if f.get("unit", "g") == "g" else float(amount)
    return {
        "protein": round(f["p"] * factor, 1),
        "fat":     round(f["f"] * factor, 1),
        "carbs":   round(f["c"] * factor, 1),
        "kcal":    round(f["kcal"] * factor, 1),
    }


# ============================================================
# TAB: NUTRITION
# ============================================================
with tab_nutrition:
    nutr_sub = st.tabs(["📅 Daily Tracker", "📚 Food Library", "📈 Trends"])

    # ── SUB-TAB 1: DAILY TRACKER ──────────────────────────────
    with nutr_sub[0]:
        if "nutr_log" not in st.session_state:
            st.session_state.nutr_log = {}

        # Step 1: Date + Day Type
        col_d, col_t = st.columns([1, 2])
        with col_d:
            sel_date = st.date_input("Date", value=date.today(), key="nutr_date_sel")
        date_str = sel_date.isoformat()
        weekday = sel_date.weekday()
        if weekday == 2:
            auto_day = "4x4 Day"
        elif weekday in (0, 3, 4):
            auto_day = "Training Day"
        else:
            auto_day = "Rest Day"
        with col_t:
            day_type_choices = ["Training Day", "4x4 Day", "Rest Day"]
            day_type = st.radio(
                "Day Type", day_type_choices,
                index=day_type_choices.index(auto_day),
                horizontal=True, key="nutr_day_type_radio",
            )

        targets = DAY_TYPE_TARGETS[day_type]

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("🔥 Calories", f"{targets['kcal']} kcal")
        mc2.metric("🥩 Protein", f"{targets['protein']}g")
        mc3.metric("🍚 Carbs", f"{targets['carbs']}g")
        mc4.metric("🧈 Fat", f"{targets['fat']}g")

        if day_type == "Rest Day":
            st.info("🛌 **Rest Day:** No starch, no white rice. Carbs from veg only. 160g protein — that never changes.")
        elif day_type == "4x4 Day":
            st.info("💪 **4x4 Day (heaviest cardio):** Highest carb day — white rice is on. Fuel the workout.")
        else:
            st.info("🏋️ **Training Day:** Moderate carbs (sweet potato / baby potato preferred). 160g protein non-negotiable.")

        if date_str not in st.session_state.nutr_log:
            st.session_state.nutr_log[date_str] = {
                m: [] for m in ("Breakfast", "Lunch", "Afternoon Shake", "Dinner")
            }
        day_log = st.session_state.nutr_log[date_str]

        FOOD_DISPLAY = {
            fn: (f"{fn} (recipe)" if fd["cat"] == "recipe"
                 else f"{fn} (per 100g)" if fd.get("unit", "g") == "g"
                 else f"{fn} (per {fd.get('unit', 'g')})")
            for fn, fd in FOODS.items()
        }
        SORTED_FOODS = sorted(FOODS.keys(), key=lambda x: FOOD_DISPLAY[x])

        # Step 2: Meal sections
        st.markdown("---")
        MEAL_ICONS = {"Breakfast": "☕", "Lunch": "🥗", "Afternoon Shake": "🥤", "Dinner": "🍽️"}

        for meal_name in ("Breakfast", "Lunch", "Afternoon Shake", "Dinner"):
            entries = day_log[meal_name]
            m_kcal    = sum(e["kcal"]    for e in entries)
            m_protein = sum(e["protein"] for e in entries)
            m_carbs   = sum(e["carbs"]   for e in entries)
            m_fat     = sum(e["fat"]     for e in entries)

            header_txt = f"{MEAL_ICONS[meal_name]} {meal_name}"
            if entries:
                header_txt += f"  —  {round(m_kcal)} kcal | P:{round(m_protein)}g C:{round(m_carbs)}g F:{round(m_fat)}g"

            with st.expander(header_txt, expanded=False):
                if entries:
                    for idx, entry in enumerate(entries):
                        rc1, rc2, rc3 = st.columns([4, 3, 1])
                        with rc1:
                            st.markdown(f"**{entry['food']}** — {entry['amount_display']}")
                        with rc2:
                            st.caption(f"P:{round(entry['protein'])}g  C:{round(entry['carbs'])}g  F:{round(entry['fat'])}g  |  {round(entry['kcal'])} kcal")
                        with rc3:
                            if st.button("✕", key=f"rm_{date_str}_{meal_name}_{idx}"):
                                day_log[meal_name].pop(idx)
                                st.rerun()
                    st.caption(f"**Meal total:** P:{round(m_protein)}g  C:{round(m_carbs)}g  F:{round(m_fat)}g  |  {round(m_kcal)} kcal")
                    st.divider()

                ac1, ac2, ac3 = st.columns([4, 2, 1])
                with ac1:
                    sel_food = st.selectbox(
                        "Food", SORTED_FOODS,
                        format_func=lambda fn: FOOD_DISPLAY[fn],
                        key=f"food_sel_{meal_name}_{date_str}",
                        label_visibility="collapsed",
                    )
                food_unit = FOODS[sel_food].get("unit", "g")
                with ac2:
                    if food_unit == "g":
                        amt_label, amt_default, amt_step = "Amount (g)", 100.0, 10.0
                    elif food_unit == "tsp":
                        amt_label, amt_default, amt_step = "# tsps", 1.0, 0.5
                    elif food_unit == "scoop":
                        amt_label, amt_default, amt_step = "# scoops", 1.0, 1.0
                    else:
                        amt_label, amt_default, amt_step = "# servings", 1.0, 1.0
                    amount_val = st.number_input(
                        amt_label, min_value=0.0, max_value=2000.0,
                        value=amt_default, step=amt_step,
                        key=f"amt_{meal_name}_{date_str}",
                        label_visibility="collapsed",
                    )
                with ac3:
                    if st.button("＋ Add", key=f"add_{meal_name}_{date_str}", type="primary"):
                        if amount_val > 0:
                            macros = calc_food_macros(sel_food, amount_val)
                            if food_unit == "g":
                                disp = f"{round(amount_val)}g"
                            elif food_unit == "tsp":
                                disp = f"{amount_val} tsp"
                            elif food_unit == "scoop":
                                disp = f"{amount_val} scoop(s)"
                            else:
                                disp = f"{amount_val}× {food_unit}"
                            day_log[meal_name].append({
                                "food": sel_food, "amount": amount_val,
                                "amount_display": disp, **macros,
                            })
                            st.rerun()

        # Step 3: Daily totals
        st.divider()
        st.markdown("### \U0001f4ca Daily Summary")

        total_kcal    = sum(e["kcal"]    for m in day_log.values() for e in m)
        total_protein = sum(e["protein"] for m in day_log.values() for e in m)
        total_carbs   = sum(e["carbs"]   for m in day_log.values() for e in m)
        total_fat     = sum(e["fat"]     for m in day_log.values() for e in m)

        def _pct(current, target):
            return min(float(current) / float(target), 1.0) if target > 0 else 0.0

        pb1, pb2, pb3, pb4 = st.columns(4)
        with pb1:
            st.markdown(f"**\U0001f525 Calories:** {round(total_kcal)} / {targets['kcal']} kcal")
            st.progress(_pct(total_kcal, targets["kcal"]))
        with pb2:
            st.markdown(f"**\U0001f969 Protein:** {round(total_protein)} / {targets['protein']}g")
            st.progress(_pct(total_protein, targets["protein"]))
        with pb3:
            st.markdown(f"**\U0001f35a Carbs:** {round(total_carbs)} / {targets['carbs']}g")
            st.progress(_pct(total_carbs, targets["carbs"]))
        with pb4:
            st.markdown(f"**\U0001f9c8 Fat:** {round(total_fat)} / {targets['fat']}g")
            st.progress(_pct(total_fat, targets["fat"]))

        sc1, sc2 = st.columns(2)
        with sc1:
            gap_p = targets["protein"] - total_protein
            if gap_p <= 0:
                st.success(f"✅ Protein on track ({round(total_protein)}g / {targets['protein']}g)")
            else:
                st.warning(f"⚠️ Need {round(gap_p)}g more protein today")
        with sc2:
            diff_k = total_kcal - targets["kcal"]
            if diff_k > 150:
                st.error(f"\U0001f534 Over by {round(diff_k)} kcal ({round(total_kcal)} / {targets['kcal']})")
            elif diff_k > 50:
                st.warning(f"\U0001f7e1 Approaching limit ({round(total_kcal)} / {targets['kcal']} kcal)")
            else:
                st.success(f"✅ Calories on track ({round(total_kcal)} / {targets['kcal']} kcal)")

        # Step 4: Save
        st.divider()
        n_entries = sum(len(v) for v in day_log.values())
        if st.button(f"\U0001f4be Save Today's Log ({n_entries} items)", type="primary",
                     use_container_width=True, key="save_nutr_log"):
            if n_entries == 0:
                st.warning("No items to save — add some foods first.")
            else:
                with st.spinner("Saving to Google Sheets…"):
                    ok = save_nutrition_entries(date_str, day_type, day_log)
                if ok:
                    st.success(f"✅ Saved {n_entries} entries for {date_str}!")

    # ── SUB-TAB 2: FOOD LIBRARY ───────────────────────────────
    with nutr_sub[1]:
        st.markdown("### \U0001f4da Food Library")
        st.caption("All foods and recipes with full macro data.")

        cat_filter = st.radio(
            "Category", ["All", "protein", "carb", "veg", "fat", "recipe"],
            horizontal=True, key="food_lib_filter",
        )

        lib_rows = []
        for fn, fd in sorted(FOODS.items()):
            if cat_filter != "All" and fd["cat"] != cat_filter:
                continue
            lib_rows.append({
                "Food": fn,
                "Category": fd["cat"],
                "Protein (g)": fd["p"],
                "Carbs (g)": fd["c"],
                "Fat (g)": fd["f"],
                "Calories": fd["kcal"],
                "Per": fd.get("unit", "g"),
            })

        if lib_rows:
            st.dataframe(pd.DataFrame(lib_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No items in this category.")

    # ── SUB-TAB 3: TRENDS ────────────────────────────────────
    with nutr_sub[2]:
        st.markdown("### \U0001f4c8 Nutrition Trends")
        st.caption("Data from saved nutrition logs. Save a few days of tracking first.")

        with st.spinner("Loading nutrition history…"):
            nut_records = load_nutrition_log()

        if not nut_records:
            st.info("No logged nutrition data yet. Use Daily Tracker → Save to populate.")
        else:
            daily_agg = {}
            for row in nut_records:
                ds = str(row.get("date", ""))
                if not ds:
                    continue
                if ds not in daily_agg:
                    daily_agg[ds] = {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0, "day_type": row.get("day_type", "")}
                for key in ("kcal", "protein", "carbs", "fat"):
                    try:
                        daily_agg[ds][key] += float(row.get(key, 0) or 0)
                    except (ValueError, TypeError):
                        pass

            if len(daily_agg) < 2:
                st.info("Log at least 2 days to see trend charts.")
            else:
                recent_dates = sorted(daily_agg.keys())[-14:]
                agg_df = pd.DataFrame([{
                    "Date": ds,
                    "Calories": round(daily_agg[ds]["kcal"]),
                    "Protein (g)": round(daily_agg[ds]["protein"]),
                    "Carbs (g)": round(daily_agg[ds]["carbs"]),
                    "Fat (g)": round(daily_agg[ds]["fat"]),
                } for ds in recent_dates]).set_index("Date")

                st.subheader("\U0001f525 Daily Calories vs Targets")
                kcal_df = agg_df[["Calories"]].copy()
                kcal_df["Training Target (1700)"] = 1700
                kcal_df["Rest Target (1350)"] = 1350
                st.line_chart(kcal_df)

                st.subheader("\U0001f969 Daily Protein vs 160g Target")
                prot_df = agg_df[["Protein (g)"]].copy()
                prot_df["Target (160g)"] = 160
                st.line_chart(prot_df)

                st.subheader("\U0001f37d️ Macro Breakdown (last 14 days)")
                st.bar_chart(agg_df[["Protein (g)", "Carbs (g)", "Fat (g)"]])

                wd = load_whoop_data()
                if wd.get("daily"):
                    st.subheader("\U0001f517 Whoop Correlation")
                    corr_rows = []
                    for ds in recent_dates:
                        if ds not in wd["daily"]:
                            continue
                        next_ds = (datetime.strptime(ds, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                        next_rec = wd["daily"].get(next_ds, {}).get("recovery")
                        wd_day = wd["daily"][ds]
                        corr_rows.append({
                            "Date": ds,
                            "Protein (g)": round(daily_agg[ds]["protein"]),
                            "Calories": round(daily_agg[ds]["kcal"]),
                            "Same-Day HRV": wd_day.get("hrv"),
                            "Next-Day Recovery": next_rec,
                            "Strain": wd_day.get("strain"),
                        })
                    if corr_rows:
                        cdf = pd.DataFrame(corr_rows).set_index("Date")
                        valid = cdf.dropna(subset=["Next-Day Recovery"])
                        if not valid.empty:
                            st.caption("Protein intake vs next-day Whoop recovery score")
                            st.line_chart(valid[["Protein (g)", "Next-Day Recovery"]])
                            st.dataframe(cdf.reset_index(), use_container_width=True, hide_index=True)
                        else:
                            st.info("Need more overlapping nutrition + Whoop data.")
                    else:
                        st.info("No overlapping Whoop + nutrition dates found.")

                st.subheader("\U0001f4cb Daily Log Summary")
                st.dataframe(agg_df.reset_index(), use_container_width=True, hide_index=True)

# ============================================================
# TAB: DEXA BASELINE
# ============================================================
with tab_dexa:
    st.subheader("DEXA Scan Baseline")
    st.caption("Fitnescity DEXA | May 11, 2026 | Baseline before peptide protocol")

    # --- Key Metrics ---
    st.markdown("### Body Composition Overview")
    dexa_cols = st.columns(5)
    with dexa_cols[0]:
        st.metric("Total Body Fat", "21.2%", help="Target: <15% by end of protocol")
    with dexa_cols[1]:
        st.metric("Lean Mass", "142.2 lbs", help="Goal: maintain or increase")
    with dexa_cols[2]:
        st.metric("Fat Mass", "38.3 lbs", help="Goal: reduce by 5-8 lbs")
    with dexa_cols[3]:
        st.metric("Total Weight", "180.4 lbs")
    with dexa_cols[4]:
        st.metric("BMI", "28.3", help="BMI is misleading for muscular builds")

    st.divider()

    # --- Regional Breakdown ---
    st.markdown("### Regional Body Fat Distribution")
    regional_data = pd.DataFrame([
        {"Region": "Arms (L)", "Fat %": "17.8%", "Fat Mass": "1.8 lbs", "Lean Mass": "8.0 lbs"},
        {"Region": "Arms (R)", "Fat %": "17.2%", "Fat Mass": "1.7 lbs", "Lean Mass": "8.0 lbs"},
        {"Region": "Trunk", "Fat %": "22.8%", "Fat Mass": "16.2 lbs", "Lean Mass": "55.0 lbs"},
        {"Region": "Legs (L)", "Fat %": "22.5%", "Fat Mass": "7.4 lbs", "Lean Mass": "25.5 lbs"},
        {"Region": "Legs (R)", "Fat %": "22.1%", "Fat Mass": "7.2 lbs", "Lean Mass": "25.7 lbs"},
    ])
    st.dataframe(regional_data, use_container_width=True, hide_index=True)

    st.info("""
**Key observations:**
- **Trunk carries the most fat** (22.8%, 16.2 lbs) — this is your belly/love handles area. Primary target.
- **Legs are close behind** (22.3% avg) — thigh fat is your second concern area.
- **Arms are leanest** (17.5% avg) — already relatively lean, will look more defined as overall BF drops.
- **Left/right symmetry is good** — no major imbalances between sides.
""")

    # --- Visceral Fat ---
    st.divider()
    st.markdown("### Visceral Adipose Tissue (VAT)")
    vat_cols = st.columns(3)
    with vat_cols[0]:
        st.metric("VAT Area", "67.2 cm²", help="<100 cm² is healthy range")
    with vat_cols[1]:
        st.metric("VAT Mass", "0.63 lbs")
    with vat_cols[2]:
        st.metric("Risk Level", "Moderate", help="Below 100 cm² is healthy. Monitor trend.")
    st.caption("Visceral fat wraps around organs. It's the most metabolically dangerous fat. Your level is within healthy range but will improve with the protocol.")

    # --- Bone Mineral Density ---
    st.divider()
    st.markdown("### Bone Mineral Density (BMD)")
    bmd_cols = st.columns(3)
    with bmd_cols[0]:
        st.metric("Total BMD", "1.18 g/cm²")
    with bmd_cols[1]:
        st.metric("T-Score", "-0.3", help="Normal: > -1.0 | Osteopenia: -1.0 to -2.5")
    with bmd_cols[2]:
        st.metric("Percentile", "33rd", help="Compared to age/sex matched population")
    st.warning("BMD at 33rd percentile is on the lower side for a 32-year-old male. D3+K2 supplementation and resistance training (especially squats, deadlifts, hip thrusts) directly improve bone density. The training program addresses this.")

    # --- Derived Metrics ---
    st.divider()
    st.markdown("### Derived Metrics")
    derived_cols = st.columns(4)
    with derived_cols[0]:
        st.metric("BMR (Katch-McArdle)", "1,740 kcal", help="Based on lean mass from DEXA")
    with derived_cols[1]:
        st.metric("BMR (Mifflin-St Jeor)", "1,688 kcal")
    with derived_cols[2]:
        st.metric("FFMI", "20.3", help="Fat-Free Mass Index. 20-22 = muscular. >25 = near genetic limit.")
    with derived_cols[3]:
        st.metric("Lean Mass Index", "22.3 kg/m²")

    # --- Goals & Targets ---
    st.divider()
    st.markdown("### End-of-Protocol Targets (July 10, 2026)")
    target_data = pd.DataFrame([
        {"Metric": "Body Fat %", "Baseline": "21.2%", "Target": "15-16%", "Change": "-5 to -6%"},
        {"Metric": "Fat Mass", "Baseline": "38.3 lbs", "Target": "~28-30 lbs", "Change": "-8 to -10 lbs"},
        {"Metric": "Lean Mass", "Baseline": "142.2 lbs", "Target": "143-145 lbs", "Change": "+1 to +3 lbs"},
        {"Metric": "Weight", "Baseline": "180.4 lbs", "Target": "~173-175 lbs", "Change": "-5 to -7 lbs"},
        {"Metric": "VAT Area", "Baseline": "67.2 cm²", "Target": "<50 cm²", "Change": "Decrease"},
        {"Metric": "FFMI", "Baseline": "20.3", "Target": "20.5-21.0", "Change": "+0.2 to +0.7"},
    ])
    st.dataframe(target_data, use_container_width=True, hide_index=True)

    st.caption("These targets account for: 15% caloric deficit, structured resistance training (4x/week), peptide protocol (Ipamorelin + Tesamorelin enhances GH → fat mobilization + lean mass preservation), and optimized sleep stack for GH pulse amplification.")

# ============================================================
# TAB: MEASUREMENT GUIDE
# ============================================================
with tab_measurements:
    st.subheader("Body Measurement Protocol")
    st.caption("Follow these for consistent, reliable measurements")

    st.markdown("""
### General Rules
- Always measure **same time**: Monday morning, before eating/drinking
- Measure on **bare skin**
- Stand **relaxed** - do NOT flex or suck in
- Pull tape **snug but not tight** (should not indent skin)
- Take each measurement **TWICE** - if >0.5cm difference, take 3rd and use middle
- Keep tape **parallel to floor** (check in mirror)
- Record in **centimeters**
    """)

    guides = {
        "Waist at Navel (primary fat-loss metric)": "Stand upright, feet shoulder-width apart. Locate belly button. Wrap tape around torso at navel height. Breathe out normally - do NOT hold breath.",
        "Waist Narrowest": "Stand upright, hands at sides. Find narrowest part of torso (between ribs and hip bones). Wrap tape around, parallel to floor.",
        "Hips Widest Point": "Stand with feet together. Find widest point of hips/glutes. Wrap tape around, keep level. Check from side in mirror.",
        "Thighs Mid Point (key for your goals)": "Stand with weight evenly distributed. Find midpoint between groin crease and top of kneecap. Wrap tape at this midpoint. Measure BOTH legs separately.",
        "Chest at Nipple Line": "Stand upright, arms relaxed. Wrap tape at nipple level. Keep tape flat across back. Breathe normally.",
        "Arms Flexed Bicep (muscle growth)": "Raise arm to side, bend elbow 90 degrees, make fist. Flex bicep. Measure around the peak.",
    }

    for title, desc in guides.items():
        with st.expander(title):
            st.markdown(desc)

    st.divider()
    st.markdown("""
### Progress Photos
- Same **lighting**, **location**, **time** every week
- Wear same fitted shorts/underwear
- **3 photos**: front, side, back (relaxed, no flexing)
- Use timer or prop phone at chest height
- Store in a dedicated phone album
    """)
