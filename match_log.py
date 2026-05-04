import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

MATCH_LOG_FILE = "match_log.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_match_id(prefix: str = "M") -> str:
    """Return a short, human-friendly match id like S-A1B2C3D4."""
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def load_match_log() -> Dict[str, Any]:
    try:
        with open(MATCH_LOG_FILE, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"matches": []}

    if not isinstance(data, dict):
        data = {"matches": []}
    data.setdefault("matches", [])
    return data


def save_match_log(log: Dict[str, Any]) -> None:
    with open(MATCH_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def snapshot_players(data: Dict[str, Any], player_ids) -> Dict[str, Any]:
    """Deep-copy the full saved stats for the given players."""
    return {str(pid): copy.deepcopy(data[str(pid)]) for pid in player_ids if str(pid) in data}


def record_match(
    *,
    match_id: str,
    kind: str,
    guild_id: Optional[int],
    channel_id: Optional[int],
    logged_by_id: int,
    players,
    before: Dict[str, Any],
    after: Dict[str, Any],
    summary: Dict[str, Any],
    edited_from: Optional[str] = None,
) -> Dict[str, Any]:
    """Create and persist one match-log record."""
    log = load_match_log()
    rec = {
        "match_id": match_id,
        "kind": kind,  # singles or doubles
        "status": "active",
        "created_at": utc_now_iso(),
        "guild_id": str(guild_id) if guild_id is not None else None,
        "channel_id": str(channel_id) if channel_id is not None else None,
        "logged_by_id": str(logged_by_id),
        "players": [str(p) for p in players],
        "before": copy.deepcopy(before),
        "after": copy.deepcopy(after),
        "summary": copy.deepcopy(summary),
    }
    if edited_from:
        rec["edited_from"] = edited_from

    
    log["matches"].insert(0, rec)
    save_match_log(log)
    return rec


def find_match(log: Dict[str, Any], match_id: str) -> Optional[Dict[str, Any]]:
    wanted = match_id.strip().upper()
    for rec in log.get("matches", []):
        if rec.get("match_id", "").upper() == wanted:
            return rec
    return None


def get_last_active_match(
    log: Dict[str, Any],
    *,
    guild_id: Optional[int] = None,
    kind: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    guild = str(guild_id) if guild_id is not None else None
    for rec in log.get("matches", []):
        if rec.get("status") != "active":
            continue
        if guild is not None and rec.get("guild_id") != guild:
            continue
        if kind is not None and rec.get("kind") != kind:
            continue
        return rec
    return None


def can_restore_match(data: Dict[str, Any], rec: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Safety check before undo/void/edit.

    We only restore a match if the current saved records for every affected
    player still match that match's recorded after-snapshot. This prevents
    accidentally erasing later matches or manual stat edits.
    """
    after = rec.get("after", {})
    for pid, after_snapshot in after.items():
        if pid not in data:
            return False, f"Player {pid} is missing from the current data file."
        if data[pid] != after_snapshot:
            return False, (
                "Cannot safely undo this match because at least one affected "
                "player has changed since this match was logged. Undo the newer "
                "match first, or manually adjust the stats."
            )
    return True, "OK"


def restore_before_snapshot(data: Dict[str, Any], rec: Dict[str, Any]) -> None:
    for pid, before_snapshot in rec.get("before", {}).items():
        data[pid] = copy.deepcopy(before_snapshot)


def mark_match_voided(
    log: Dict[str, Any],
    rec: Dict[str, Any],
    *,
    voided_by_id: int,
    reason: str,
) -> None:
    rec["status"] = "voided"
    rec["voided_at"] = utc_now_iso()
    rec["voided_by_id"] = str(voided_by_id)
    rec["void_reason"] = reason
    save_match_log(log)
