import os
from datetime import datetime
import psycopg2
from dotenv import load_dotenv
from core import state

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")


def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
    )


def insert_production(data, mo_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        state.end_time = datetime.fromisoformat(data["timestamp"])

        cursor.execute(
            """
            INSERT INTO production_history (product_id, mo_id, result, start_time, end_time)
            VALUES (%s, %s, %s, %s, %s)
        """,
            (
                data.get("product_id"),
                str(mo_id) if mo_id else None,  # Cast ke String
                data.get("result"),
                state.start_time,
                state.end_time,
            ),
        )
        conn.commit()

    except Exception as e:
        conn.rollback()
        print("[ERROR] DB Insert Error:", e)

    finally:
        cursor.close()
        conn.close()


def insert_mqtt_log(topic, payload):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        timestamp = datetime.fromisoformat(payload["timestamp"])

        cursor.execute(
            """
            INSERT INTO workcenter_log 
            (product_id, workcenter, status, result, cycle_time, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
        """,
            (
                payload.get("product_id"),
                payload.get("workcenter"),
                payload.get("status"),
                payload.get("result"),
                payload.get("cycle_time"),
                timestamp,
            ),
        )
        conn.commit()

    except Exception as e:
        conn.rollback()
        print("[ERROR] DB Insert Log Error:", e)

    finally:
        cursor.close()
        conn.close()


def get_history(workcenter=None, search=None, result_filter=None, limit=50):
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT id, product_id, workcenter, status, result, cycle_time, timestamp
        FROM workcenter_log
        WHERE 1=1
    """

    params = []

    if workcenter:
        query += " AND workcenter = %s"
        params.append(workcenter)

    if search:
        query += (
            " AND (CAST(product_id AS TEXT) ILIKE %s OR workcenter ILIKE %s)"
        )
        params.append(f"%{search}%")
        params.append(f"%{search}%")

    if result_filter and result_filter.lower() != "all":
        query += " AND LOWER(result) = %s"
        params.append(result_filter.lower())

    query += " ORDER BY timestamp DESC, id DESC LIMIT %s"
    params.append(limit)

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return [
        {
            "product_id": row[1],
            "workcenter": row[2],
            "status": row[3],
            "result": row[4],
            "cycle_time": row[5],
            "timestamp": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    ]


def get_production_history(
    mo_id=None, search=None, result_filter=None, limit=50
):
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT product_id, mo_id, result, start_time, end_time
        FROM production_history
        WHERE 1=1
    """

    params = []

    if mo_id:
        query += " AND mo_id = %s"
        params.append(str(mo_id))

    if search:
        query += " AND (CAST(product_id AS TEXT) ILIKE %s OR CAST(mo_id AS TEXT) ILIKE %s)"
        params.append(f"%{search}%")
        params.append(f"%{search}%")

    if result_filter and result_filter.lower() != "all":
        query += " AND LOWER(result) = %s"
        params.append(result_filter.lower())

    query += " ORDER BY end_time DESC LIMIT %s"
    params.append(limit)

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return [
        {
            "product_id": row[0],
            "mo_id": row[1],
            "result": row[2],
            "start_time": row[3].isoformat() if row[3] else None,
            "end_time": row[4].isoformat() if row[4] else None,
        }
        for row in rows
    ]


def get_mo_detail(mo_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # 1. Ambil production_history
        cursor.execute(
            """
            SELECT product_id, result, start_time, end_time
            FROM production_history
            WHERE mo_id = %s
            ORDER BY end_time ASC
        """,
            (str(mo_id),),
        )

        prod_rows = cursor.fetchall()

        productions = []
        product_ids = []

        for row in prod_rows:
            pid = row[0]
            product_ids.append(pid)

            productions.append({
                "product_id": pid,
                "result": row[1],
                "start_time": row[2].isoformat() if row[2] else None,
                "end_time": row[3].isoformat() if row[3] else None,
            })

        # 2. Ambil workcenter_log berdasarkan product_id
        logs = []

        if product_ids:
            format_strings = ",".join(["%s"] * len(product_ids))

            cursor.execute(
                f"""
                SELECT product_id, workcenter, status, result, cycle_time, timestamp
                FROM workcenter_log
                WHERE product_id IN ({format_strings})
                ORDER BY timestamp ASC
            """,
                tuple(product_ids),
            )

            log_rows = cursor.fetchall()

            logs = [
                {
                    "product_id": r[0],
                    "workcenter": r[1],
                    "status": r[2],
                    "result": r[3],
                    "cycle_time": r[4],
                    "timestamp": r[5].isoformat() if r[5] else None,
                }
                for r in log_rows
            ]

        # 3. Stats / Summary
        total = len(productions)
        ok = sum(1 for p in productions if p["result"] == "ok")
        ng = sum(1 for p in productions if p["result"] == "ng")
        yield_rate = (ok / total * 100) if total > 0 else 0

        return {
            "mo_id": str(mo_id),
            "summary": {
                "total": total,
                "ok": ok,
                "ng": ng,
                "yield_rate": round(yield_rate, 2),
            },
            "production_history": productions,
            "workcenter_log": logs,
        }

    except Exception as e:
        print("[ERROR] get_mo_detail error:", e)
        return None

    finally:
        cursor.close()
        conn.close()


def get_mo_history(mo_id=None, search=None, limit=50):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        query = """
            SELECT mo_id, start_time, end_time, status, total_production, ok_count, ng_count, yield_rate, duration
            FROM mo_history
            WHERE 1=1
        """
        params = []

        if mo_id:
            query += " AND mo_id = %s"
            params.append(str(mo_id))

        if search:
            query += " AND CAST(mo_id AS TEXT) ILIKE %s"
            params.append(f"%{search}%")

        query += " ORDER BY end_time DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        return [
            {
                "mo_id": row[0],
                "start_time": row[1].isoformat() if row[1] else None,
                "end_time": row[2].isoformat() if row[2] else None,
                "status": row[3],
                "total_production": row[4],
                "ok_count": row[5],
                "ng_count": row[6],
                "yield_rate": row[7],
                "duration": row[8],
            }
            for row in rows
        ]

    except Exception as e:
        print("[ERROR] get_mo_history error:", e)
        return []

    finally:
        cursor.close()
        conn.close()


def create_mo(mo_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO mo (id, start_time, status)
            VALUES (%s, NOW(), 'running')
            ON CONFLICT (id) DO NOTHING
        """,
            (str(mo_id),),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def finish_mo(mo_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE mo
            SET status = 'done',
                end_time = NOW()
            WHERE id = %s
        """,
            (str(mo_id),),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def insert_mo_history_record(
    mo_id, start_time, end_time, status, total, ok, ng, yield_rate
):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        duration_str = None
        if start_time and end_time:
            diff = end_time - start_time
            duration_str = str(diff).split(".")[0]

        cursor.execute(
            """
            INSERT INTO mo_history 
            (mo_id, start_time, end_time, status, total_production, ok_count, ng_count, yield_rate, duration)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                str(mo_id),
                start_time,
                end_time,
                status,
                total,
                ok,
                ng,
                yield_rate,
                duration_str,
            ),
        )
        conn.commit()
        print(f"[DB] Sukses menyimpan history akhir untuk MO: {mo_id}")

    except Exception as e:
        conn.rollback()
        print("[ERROR] Gagal Insert MO History:", e)

    finally:
        cursor.close()
        conn.close()