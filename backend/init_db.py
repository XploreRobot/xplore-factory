from services.db_service import get_connection
from core.auth import get_password_hash

def init_database():
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # 1. Buat Tabel Users (sekarang dilengkapi created_at)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(50) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 2. Buat Tabel MO
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mo (
            id VARCHAR(100) PRIMARY KEY,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            status VARCHAR(50) NOT NULL
        );
        """)

        # 3. Buat Tabel Production History
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS production_history (
            id SERIAL PRIMARY KEY,
            product_id VARCHAR(100) NOT NULL,
            mo_id VARCHAR(100) REFERENCES mo(id) ON DELETE CASCADE,
            result VARCHAR(20),
            start_time TIMESTAMP,
            end_time TIMESTAMP
        );
        """)

        # 4. Buat Tabel Workcenter Log
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS workcenter_log (
            id SERIAL PRIMARY KEY,
            product_id VARCHAR(100) NOT NULL,
            workcenter VARCHAR(100) NOT NULL,
            status VARCHAR(50),
            result VARCHAR(20),
            cycle_time NUMERIC(10, 2),
            timestamp TIMESTAMP NOT NULL
        );
        """)

        # 5. Buat Tabel MO History
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mo_history (
            mo_id VARCHAR(100) PRIMARY KEY REFERENCES mo(id) ON DELETE CASCADE,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            status VARCHAR(50),
            total_production INTEGER DEFAULT 0,
            ok_count INTEGER DEFAULT 0,
            ng_count INTEGER DEFAULT 0,
            yield_rate NUMERIC(5, 2) DEFAULT 0,
            duration VARCHAR(50)
        );
        """)

        # 6. Generate Password Hash dan Insert User Admin
        hashed_pw = get_password_hash("admin123")
        cursor.execute("""
            INSERT INTO users (username, password_hash, role) 
            VALUES ('admin', %s, 'admin')
            ON CONFLICT (username) DO NOTHING;
        """, (hashed_pw,))

        conn.commit()
        print("✅ Berhasil! Semua tabel dan user admin telah dibuat di database teaching_aid_db.")

    except Exception as e:
        conn.rollback()
        print("❌ Terjadi kesalahan saat membuat database:", e)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    init_database()