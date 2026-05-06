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
# DATA PERSISTENCE - Google Sheets Backend
# ============================================================
import gspread
from google.oauth2.service_account import Credentials

SHEET_NAME = st.secrets.get("GOOGLE_SHEET_NAME", "Operation Phoenix Data")

@st.cache_resource(ttl=300)
def get_gsheet_client():
    """Create authenticated Google Sheets client from service account credentials."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    return gspread.authorize(creds)


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
def get_saved_token():
    """Get saved Whoop access token from Google Sheets."""
    try:
        ss = get_spreadsheet()
        ws_meta = get_or_create_worksheet(ss, "Meta", META_HEADERS)
        rows = ws_meta.get_all_records()
        for row in rows:
            if row.get("key") == "whoop_access_token":
                return row.get("value")
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

tab_overview, tab_whoop, tab_checkin, tab_injections, tab_measurements = st.tabs([
    "\U0001f4ca Overview", "\U0001f4f1 Whoop Data", "\U0001f4cb Weekly Check-in", "\U0001f489 Injection Log", "\U0001f4cf Measurement Guide"
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
            weight = st.number_input("Weight (kg)", 50.0, 150.0, float(existing.get("weight", 82.0)), 0.1)
        with c2:
            body_fat = st.number_input("Body Fat % (DEXA)", 0.0, 50.0, float(existing.get("body_fat", 0.0)), 0.1)

        st.markdown("**Measurements (cm)** - [See Measurement Guide tab for instructions]")
        measurements = {}
        cols = st.columns(4)
        for i, site in enumerate(MEASUREMENT_SITES):
            with cols[i % 4]:
                v = existing.get("measurements", {}).get(site, 0.0)
                measurements[site] = st.number_input(site, 0.0, 200.0, float(v), 0.1, key=f"m_{site}")

        st.markdown("**How are you feeling? (1-10)**")
        subjective = {}
        sub_items = ["Energy", "Sleep Quality", "Mood", "Libido", "Appetite", "Injection Comfort"]
        cols = st.columns(3)
        for i, item in enumerate(sub_items):
            with cols[i % 3]:
                v = existing.get("subjective", {}).get(item, 5)
                subjective[item] = st.slider(item, 1, 10, int(v), key=f"s_{item}")

        st.markdown("**Progress Photos**")
        photos = {}
        cols = st.columns(3)
        for i, view in enumerate(["Front", "Side", "Back"]):
            with cols[i]:
                photos[view] = st.checkbox(f"{view} photo taken", existing.get("photos", {}).get(view, False), key=f"p_{view}")

        notes = st.text_area("Notes", existing.get("notes", ""), key="notes")

        if st.form_submit_button("Save Check-in", type="primary", use_container_width=True):
            checkin_data["checkins"][week_name] = {
                "date": week_start.isoformat(),
                "weight": weight,
                "body_fat": body_fat if body_fat > 0 else None,
                "measurements": measurements,
                "subjective": subjective,
                "photos": photos,
                "notes": notes,
                "saved_at": datetime.now().isoformat(),
            }
            save_checkin_data(checkin_data)
            st.success(f"{week_name} check-in saved!")

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
