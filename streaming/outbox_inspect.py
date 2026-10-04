"""Read-only queue diagnostics; requires no destination URL or Redis connection."""
import argparse
import json
from pathlib import Path
import sqlite3
import time


def inspect_queue(path, state=None, ident=None, limit=50):
    if state not in (None, "pending", "inflight", "sent", "dead") or not 1 <= limit <= 1000:
        raise ValueError("Invalid state or limit")
    path = Path(path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")  # One consistent view across counts, messages and audit.
        conditions, params = [], []
        if state:
            conditions.append("state=?")
            params.append(state)
        if ident:
            conditions.append("id=?")
            params.append(ident)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = [dict(row) for row in connection.execute(
            "SELECT id,state,attempts,due,lease,error FROM messages" + where + " ORDER BY due,id LIMIT ?",
            [*params, limit])]
        now = time.time()
        for row in rows:
            row["retry_due"] = row["state"] == "pending" and row["due"] <= now
            row["lease_expired"] = row["state"] == "inflight" and row["lease"] <= now
        audit = []
        if ident:
            audit = [dict(row) for row in connection.execute(
                "SELECT seq,id,at,event,detail FROM audit WHERE id=? ORDER BY seq DESC LIMIT ?", (ident, limit))]
        return {"counts": dict(connection.execute("SELECT state,count(*) FROM messages GROUP BY state").fetchall()),
                "matched_count": connection.execute("SELECT count(*) FROM messages" + where, params).fetchone()[0],
                "messages": rows, "audit": audit, "limit": limit,
                "ready_pending": connection.execute("SELECT count(*) FROM messages WHERE state='pending' AND due<=?", (now,)).fetchone()[0],
                "expired_inflight": connection.execute("SELECT count(*) FROM messages WHERE state='inflight' AND lease<=?", (now,)).fetchone()[0]}
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--state", choices=["pending", "inflight", "sent", "dead"])
    parser.add_argument("--id")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    print(json.dumps(inspect_queue(args.db, args.state, args.id, args.limit), ensure_ascii=False, indent=2))
