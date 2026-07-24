"""
scheduler_form.py — Autonomous SAP Pipeline for Form-Submitted Resumes
Triggered every 30 min by GitHub Actions (no Streamlit dependency).

This scheduler handles resumes submitted via the Resume_Upload.py form.
It is a PARALLEL pipeline to scheduler.py (which handles email inbox).

Flow:
  1. Fetch all Pending records from Supabase table
     (where upload_to_sap = 'Pending' AND source is form — not email)
  2. Download resume file from Supabase Storage
  3. Upload to SAP via headless browser
  4. Update upload_to_sap status in Supabase table
  5. Send notification email to the recruiter who submitted via form
"""

import io
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
SRC  = ROOT / "src"
sys.path.insert(0, str(SRC))

from notifier import send_upload_notification, _upload_report_status
from resume_repository import (
    _headers,
    download_resume,
    fetch_existing_record,
    SUPABASE_URL,
    SUPABASE_TABLE,
)
from sap_bot_headless import SAPBot
from uploader import missing_upload_fields, upload_to_sap
from resume_repository import _secret

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("scheduler_form")

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
SUBMIT_TO_SAP = os.environ.get("SCHEDULER_SUBMIT_TO_SAP", "true").lower() == "true"
SEND_EMAIL    = os.environ.get("SCHEDULER_SEND_EMAIL",    "true").lower() == "true"
MAX_RECORDS   = int(os.environ.get("SCHEDULER_MAX_RECORDS", "50"))
EMAIL_CC      = [e for e in os.environ.get("SCHEDULER_EMAIL_CC", "").split(",") if e.strip()]
SAP_WORKERS   = max(1, int(os.environ.get("SCHEDULER_WORKERS", "1")))

# When set (by repository_dispatch), only process these specific record IDs.
# Falls back to all-pending query on safety-net / manual runs (where the value is null/"").
_raw_record_ids = os.environ.get("SCHEDULER_RECORD_IDS", "")
RECORD_IDS: list[str] = []
if _raw_record_ids and _raw_record_ids.strip() not in ("", "null"):
    try:
        _parsed = json.loads(_raw_record_ids)
        if isinstance(_parsed, list):
            RECORD_IDS = [str(i).strip() for i in _parsed if i]
    except Exception:
        pass

NON_CRITICAL_SAP_ERRORS  = ["requisition id", "not found in job list"]
DEAD_SESSION_ERRORS      = ["invalid session id", "no such session", "disconnected"]
CANDIDATE_EXISTS_ERRORS  = ["candidate already exists in sap"]
ALREADY_IN_SAP_PHRASES   = ["already exists", "ownership period", "employee referral"]
SAP_SUCCESS_MESSAGES     = ["candidate has been added"]

def _classify_dup_status(err_text: str) -> str:
    t = err_text.lower()
    if "already exists in the system" in t and ("most recent resume" in t or "choose to upload" in t):
        return "Already uploaded by Volibits Team"
    return "Duplicate, if it's a subcon requirement. Please upload with an alternate Mail ID"

BUCKET = "resumes"


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def _safe(val) -> str:
    return str(val).strip() if val else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _patch_record(record_id: str, fields: dict) -> None:
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{record_id}",
        headers={**_headers(), "Prefer": "return=representation"},
        json=fields,
        timeout=15,
    )


def _start_bot() -> SAPBot:
    b = SAPBot()
    b.start()
    b.login()
    return b


def fetch_form_pending_records(limit: int = 50) -> list:
    """
    Fetch only form-submitted records that are currently Pending.
    Failed/Skipped records remain terminal until explicitly moved back to Pending.

    Key distinction from scheduler.py (email inbox):
        Email-submitted records have source_email_id populated.
        Form-submitted records have source_email_id IS NULL.
    """
    url = (
        f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
        f"?source_email_id=is.null"        # ← ONLY form-submitted rows
        f"&upload_to_sap=eq.Pending"
        f"&select=*"
        f"&limit={limit}"
    )
    resp = requests.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        raise Exception(resp.text)
    return resp.json()


def fetch_records_by_ids(ids: list) -> list:
    """
    Fetch specific records by UUID, filtered to Pending status.
    Used when triggered by a form-submission dispatch so only the profiles
    submitted in that session are processed (not the whole queue).
    """
    id_list = ",".join(ids)
    url = (
        f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
        f"?id=in.({id_list})"
        f"&upload_to_sap=eq.Pending"
        f"&select=*"
    )
    resp = requests.get(url, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        raise Exception(resp.text)
    return resp.json()


def _resolve_recruiter_email(record: dict) -> str:
    """
    Figure out where to send the upload-completion notification.
    Priority: created_by → recruiter_email → modified_by.
    """
    for key in ("created_by", "recruiter_email", "modified_by"):
        val = _safe(record.get(key))
        if val and "@" in val:
            return val
    return ""


def _add_result(by_recruiter, recruiter_email, file_name, status, error_msg="", screenshots=None,
                jr_no="", client_recruiter="", skill=""):
    if recruiter_email not in by_recruiter:
        is_external = not recruiter_email.lower().endswith("@volibits.com")
        by_recruiter[recruiter_email] = {"results": [], "screenshots": [], "is_external": is_external}
    by_recruiter[recruiter_email]["results"].append({
        "File": file_name, "Status": status, "Error": error_msg or "",
        "JR No": jr_no, "Client Recruiter": client_recruiter, "Skill": skill,
    })
    if screenshots:
        by_recruiter[recruiter_email]["screenshots"].extend(screenshots)


def _report_status(sap_status: str, sap_error: str = "") -> str:
    if sap_status == "Failed":
        return _upload_report_status(sap_error)
    return _upload_report_status(sap_status)


def _mark_skipped_silent(record_id: str) -> None:
    _patch_record(record_id, {
        "upload_to_sap": "Skipped",
        "error_message": "",
        "modified_at": _now_iso(),
    })


# ─────────────────────────────────────────────────────────────
# CHUNK PROCESSOR  (one per parallel worker)
# ─────────────────────────────────────────────────────────────
def _process_chunk(records: list, submit_to_sap: bool) -> tuple[dict, dict]:
    """Start a dedicated SAP bot and process the given slice of records."""
    by_recruiter: dict[str, dict] = {}
    counts = {"done": 0, "skipped": 0, "failed": 0, "errors": []}

    # Start bot
    bot = None
    for attempt in range(2):
        try:
            bot = _start_bot()
            log.info("SAP bot connected ✅")
            break
        except Exception as e:
            if attempt < 1:
                log.warning(f"SAP bot failed (attempt {attempt + 1}), retrying…")
            else:
                log.error(f"SAP bot failed to start: {e}")
                counts["errors"].append(f"SAP start: {e}")

    for record in records:
        record_id        = _safe(record.get("id"))
        jr_no            = _safe(record.get("jr_number"))
        first_name       = _safe(record.get("first_name"))
        last_name        = _safe(record.get("last_name"))
        email            = _safe(record.get("email"))
        phone            = _safe(record.get("phone"))
        resume_path      = _safe(record.get("resume_path"))
        file_name        = _safe(record.get("file_name"))
        client_recruiter = _safe(record.get("client_recruiter"))
        skill            = _safe(record.get("skill"))
        recruiter_email  = _resolve_recruiter_email(record)
        cand_label       = f"{first_name} {last_name}".strip() or file_name

        log.info(f"  → {cand_label} | JR: {jr_no} | id: {record_id}")

        missing = missing_upload_fields({
            "jr_number": jr_no, "first_name": first_name, "last_name": last_name,
            "email": email, "phone": phone, "resume_file": resume_path,
        })
        if missing:
            log.info(f"     Skipping silently - missing required data: {', '.join(missing)}")
            try:
                _mark_skipped_silent(record_id)
            except Exception as e:
                log.warning(f"     Failed to mark incomplete record as Skipped: {e}")
            counts["skipped"] += 1
            continue

        duplicate = fetch_existing_record(jr_no, email, phone)
        is_duplicate = False
        if duplicate:
            dup_id     = str(duplicate.get("id", "")).strip()
            dup_status = str(duplicate.get("upload_to_sap", "")).strip().lower()
            if dup_id != record_id and dup_status in ("failed", "skipped"):
                log.info(f"     Duplicate found (id: {dup_id}) with status={dup_status} — retrying upload")
                record_id    = dup_id
                is_duplicate = True
            elif dup_id != record_id:
                log.info(f"     Duplicate found with status={dup_status} — skipping")
                counts["skipped"] += 1
                _add_result(by_recruiter, recruiter_email, file_name, "Failed",
                            jr_no=jr_no, client_recruiter=client_recruiter, skill=skill)
                continue

        file_bytes = None
        if resume_path:
            clean_path = resume_path
            if clean_path.startswith("/object/sign/"):
                clean_path = clean_path.replace("/object/sign/resumes/", "").split("?")[0]
            log.info(f"     Resume path: {clean_path}")
            try:
                file_bytes = download_resume(clean_path)
                log.info(f"     Downloaded resume ({len(file_bytes):,} bytes)")
            except Exception as e:
                log.warning(f"     Resume download failed: {e}")

        if not file_bytes:
            log.info("     Skipping silently - resume missing or could not be downloaded")
            try:
                _mark_skipped_silent(record_id)
            except Exception as e:
                log.warning(f"     Failed to mark missing-resume record as Skipped: {e}")
            counts["skipped"] += 1
            continue

        if not bot:
            log.warning("     SAP bot unavailable - leaving record Pending")
            counts["skipped"] += 1
            _add_result(by_recruiter, recruiter_email, file_name, "Failed",
                        jr_no=jr_no, client_recruiter=client_recruiter, skill=skill)
            continue

        sap_status          = "Failed"
        sap_error           = ""
        sap_screen_error    = ""
        failed_screenshots  = []
        screenshot_captured = False

        for attempt in range(2):
            try:
                file_obj      = io.BytesIO(file_bytes)
                file_obj.name = file_name
                upload_to_sap(bot, {
                    "jr_number":   jr_no,   "first_name":  first_name,
                    "last_name":   last_name, "email":     email,
                    "phone":       phone,   "resume_file": file_obj,
                    "submit":      submit_to_sap,
                })
                sap_status = "Succeeded"
                log.info(f"     ✅ SAP upload success: {cand_label}")
                break
            except Exception as e:
                sap_error = str(e)

                if any(err in sap_error.lower() for err in CANDIDATE_EXISTS_ERRORS):
                    sap_screen_error = sap_error.split("|", 1)[1] if "|" in sap_error else ""
                    sap_status = _classify_dup_status(sap_screen_error or sap_error)
                    log.warning(f"     ⚠ Candidate exists ({sap_status}): {cand_label}")
                    if not screenshot_captured and bot:
                        p = getattr(bot, "last_screenshot_path", None)
                        if p and p.exists():
                            failed_screenshots.append({"name": p.name, "content": p.read_bytes()})
                            screenshot_captured = True
                    break

                if any(err in sap_error.lower() for err in NON_CRITICAL_SAP_ERRORS):
                    sap_status = "Skipped"
                    log.warning(f"     ⚠ SAP skipped (non-critical): {sap_error}")
                    break

                if any(err in sap_error.lower() for err in DEAD_SESSION_ERRORS):
                    sap_status = "Pending"
                    log.warning(f"     Session dead (attempt {attempt + 1}) — restarting bot…")
                    try: bot.quit()
                    except Exception: pass
                    try:
                        bot = _start_bot()
                        log.info("     SAP bot restarted.")
                    except Exception as re_err:
                        log.error(f"     Bot restart failed: {re_err}")
                        bot = None
                        break
                    continue

                sap_status = "Failed"
                if not screenshot_captured:
                    try:
                        snap_name = f"{jr_no}_{cand_label}"
                        snap_path = bot._screenshot(snap_name)
                        failed_screenshots.append({
                            "name": f"{snap_name}.png", "content": snap_path.read_bytes(),
                        })
                        screenshot_captured = True
                        extracted = bot._extract_screen_error()
                        if extracted:
                            sap_screen_error = extracted
                            log.info(f"     SAP screen error: {sap_screen_error}")
                    except Exception:
                        pass
                log.error(f"     ❌ SAP upload failed (attempt {attempt + 1}): {sap_error}")

                if sap_screen_error and any(msg in sap_screen_error.lower() for msg in SAP_SUCCESS_MESSAGES):
                    log.info(f"     Reclassified as 'Succeeded' — SAP confirmed: {sap_screen_error}")
                    sap_status = "Succeeded"
                    sap_screen_error = ""
                    failed_screenshots = []
                    break

                if sap_screen_error:
                    break

        _err_text = (sap_screen_error or sap_error or "").lower()
        if sap_status == "Failed" and any(p in _err_text for p in ALREADY_IN_SAP_PHRASES):
            sap_status = _classify_dup_status(sap_screen_error or sap_error or "")
            log.info(f"     Reclassified as '{sap_status}' based on: {sap_screen_error or sap_error}")

        if sap_status != "Succeeded" and not screenshot_captured and bot:
            try:
                snap_name = f"{jr_no}_{cand_label}_{sap_status.replace(' ', '_')}"
                snap_path = bot._screenshot(snap_name)
                failed_screenshots.append({
                    "name": f"{snap_name}.png", "content": snap_path.read_bytes(),
                })
                if not sap_screen_error:
                    extracted = bot._extract_screen_error()
                    if extracted:
                        sap_screen_error = extracted
                        log.info(f"     SAP screen error: {sap_screen_error}")
            except Exception:
                pass

        patch = {"modified_at": _now_iso()}
        if sap_status == "Succeeded":
            patch["upload_to_sap"] = "Succeeded"
            try:
                existing = requests.get(
                    f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{record_id}&select=error_message",
                    headers=_headers(), timeout=10,
                ).json()
                if existing and existing[0].get("error_message"):
                    old_msg = existing[0]["error_message"]
                    patch["error_message"] = f"{old_msg}; upload successful on rerun at {_now_iso()}"
            except Exception:
                pass
        elif sap_status == "Pending":
            patch["upload_to_sap"] = "Pending"
            if sap_error:
                patch["error_message"] = sap_error[:500]
        else:
            patch["upload_to_sap"] = sap_status
            error_to_store = sap_screen_error or sap_error
            if error_to_store:
                patch["error_message"] = error_to_store[:500]

        try:
            _patch_record(record_id, patch)
            log.info(f"     DB updated → upload_to_sap = {patch.get('upload_to_sap', sap_status)}")
        except Exception as e:
            log.warning(f"     DB update failed: {e}")

        _add_result(
            by_recruiter, recruiter_email, file_name,
            _report_status(sap_status, sap_error),
            error_msg=sap_screen_error,
            screenshots=failed_screenshots,
            jr_no=jr_no,
            client_recruiter=client_recruiter,
            skill=skill,
        )

        if   sap_status == "Succeeded": counts["done"]    += 1
        elif sap_status == "Skipped":   counts["skipped"] += 1
        elif sap_status == "Pending":   counts["skipped"] += 1
        else:                           counts["failed"]  += 1

    if bot:
        try: bot.quit()
        except Exception: pass

    return by_recruiter, counts


# ─────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────
def run_pipeline() -> dict:
    run_start = datetime.now(timezone.utc)
    summary   = {
        "started_at": run_start.isoformat(),
        "total": 0, "done": 0, "skipped": 0, "failed": 0, "errors": [],
    }

    log.info("=" * 60)
    log.info(f"Form scheduler run started — submit_to_sap={SUBMIT_TO_SAP}, workers={SAP_WORKERS}")
    if RECORD_IDS:
        log.info(f"Targeted run — processing {len(RECORD_IDS)} specific record(s): {RECORD_IDS}")
    else:
        log.info("Safety-net / manual run — processing all pending records")

    # ── 1. Fetch Pending form-submitted records ───────────────
    try:
        if RECORD_IDS:
            pending = fetch_records_by_ids(RECORD_IDS)
        else:
            pending = fetch_form_pending_records(limit=MAX_RECORDS)
    except Exception as e:
        log.error(f"Failed to fetch pending records: {e}")
        summary["errors"].append(f"Fetch records: {e}")
        return summary

    log.info(f"Found {len(pending)} pending form-submitted record(s)")
    summary["total"] = len(pending)

    if not pending:
        log.info("Nothing to process.")
        return summary

    # ── 2. Split records and dispatch to parallel workers ────────
    chunks = [pending[i::SAP_WORKERS] for i in range(SAP_WORKERS) if pending[i::SAP_WORKERS]]
    log.info(f"Dispatching {len(pending)} record(s) across {len(chunks)} worker(s)")

    by_recruiter: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = {executor.submit(_process_chunk, chunk, SUBMIT_TO_SAP): idx
                   for idx, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            worker_idx = futures[future]
            try:
                chunk_by_recruiter, counts = future.result()
                for email, info in chunk_by_recruiter.items():
                    if email not in by_recruiter:
                        by_recruiter[email] = {"results": [], "screenshots": [], "is_external": info.get("is_external", False)}
                    by_recruiter[email]["results"].extend(info["results"])
                    by_recruiter[email]["screenshots"].extend(info["screenshots"])
                summary["done"]    += counts["done"]
                summary["skipped"] += counts["skipped"]
                summary["failed"]  += counts["failed"]
                summary["errors"].extend(counts.get("errors", []))
            except Exception as e:
                log.error(f"Worker {worker_idx} raised an exception: {e}")
                summary["errors"].append(f"Worker {worker_idx}: {e}")

    # ── 3. Send notification per recruiter ────────────────────
    if not SEND_EMAIL:
        log.info("Email notifications disabled (SCHEDULER_SEND_EMAIL=false) — skipping.")
        return summary

    for recruiter_email, info in by_recruiter.items():
        recruiter_email = recruiter_email.strip() if recruiter_email else ""

        if not recruiter_email:
            log.warning(f"Skipping notification — no recruiter email found (results: {info['results']})")
            continue

        report_user = {
            "email":        recruiter_email,
            "name":         recruiter_email,
            "access_token": "",
        }
        try:
            ok, msg = send_upload_notification(
                access_token="",
                user=report_user,
                results=info["results"],
                submit_mode=SUBMIT_TO_SAP,
                attachments=info["screenshots"],
                cc=EMAIL_CC if EMAIL_CC else None,
                is_external=info.get("is_external", False),
            )
            if ok:
                log.info(f"📧 Notification sent to {recruiter_email}")
            else:
                log.warning(f"Notification failed for {recruiter_email}: {msg}")
        except Exception as e:
            log.warning(f"Notification exception for {recruiter_email}: {e}")

    elapsed = (datetime.now(timezone.utc) - run_start).total_seconds()
    log.info(
        f"Run complete in {elapsed:.1f}s — "
        f"total={summary['total']} done={summary['done']} "
        f"skipped={summary['skipped']} failed={summary['failed']}"
    )
    log.info("=" * 60)
    return summary


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_pipeline()
