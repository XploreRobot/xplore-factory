import xmlrpc.client
import socket


class OdooService:
    def __init__(self, url, db, username, password):
        self.url = url
        self.db = db
        self.username = username
        self.password = password

        self.uid = None
        self.last_mo_id = None

        self.connect()

    # =============================
    # CONNECT
    # =============================
    def connect(self):
        try:
            print("[ODOO] Connecting to Odoo...")

            common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")

            self.uid = common.authenticate(
                self.db,
                self.username,
                self.password,
                {}
            )

            if not self.uid:
                print("[ERROR] Login Failed (username/password/db salah)")
                return False

            print("[OK] Connected to Odoo")
            print(f"[USER] UID: {self.uid}")

            return True

        except socket.error:
            print("[ERROR] Connection Failed (server mati / URL salah)")
            return False

        except Exception as e:
            print("[ERROR] Unknown Error:", e)
            return False

    # =============================
    # SAFE EXECUTE
    # =============================
    # =============================
    # SAFE EXECUTE
    # =============================
    def execute(self, model, method, args=None, kwargs=None):
        if args is None:
            args = []
        if kwargs is None:
            kwargs = {}

        try:
            models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

            return models.execute_kw(
                self.db,
                self.uid,
                self.password,
                model,
                method,
                args,
                kwargs
            )

        except Exception as e:
            err_str = str(e)
            
            # --- TAMBAHAN BARU: Tangkap error 'cannot marshal None' ---
            if "cannot marshal None" in err_str:
                print(f"[OK] Odoo executed {model}.{method} successfully (Returned None).")
                return True
            # ----------------------------------------------------------

            print(f"[ERROR] Odoo Error ({model}.{method}):", e)

            # coba reconnect
            if self.connect():
                try:
                    models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
                    return models.execute_kw(
                        self.db,
                        self.uid,
                        self.password,
                        model,
                        method,
                        args,
                        kwargs
                    )
                except Exception as e2:
                    # Tangkap juga saat retry
                    if "cannot marshal None" in str(e2):
                        print(f"[OK] Odoo executed {model}.{method} successfully on retry.")
                        return True
                        
                    print("[ERROR] Retry Failed:", e2)

            return None

    # =============================
    # GET ACTIVE MO
    # =============================
    def get_active_mo(self):
        result = self.execute(
            'mrp.production',
            'search_read',
            [[['state', 'in', ['progress']]]],
            {'fields': ['id', 'name', 'product_qty', 'qty_produced']}
        )

        if not result:
            print("[INFO] No active Manufacturing Order")
            return None

        mo = result[0]

        print(f"[OK] Active MO: {mo['name']} | Target: {mo['product_qty']}")

        return mo

    # =============================
    # UPDATE QTY
    # =============================
    def update_production_qty(self, mo_id, qty_done):
        result = self.execute(
            'mrp.production',
            'write',
            [[mo_id], {'qty_produced': qty_done}]
        )

        if result:
            print(f"[UPDATE] Updated qty_produced = {qty_done}")

    # =============================
    # MARK DONE
    # =============================
    def mark_mo_done(self, mo_id):
        result = self.execute(
            'mrp.production',
            'button_mark_done',
            [[mo_id]]
        )

        if result is not None:
            print("[OK] MO marked as DONE")

    # =============================
    # GET CONFIRMED MO
    # =============================
    def get_confirmed_mo(self):
        # Mencari MO yang statusnya 'confirmed' (Dikonfirmasi)
        result = self.execute(
            'mrp.production',
            'search_read',
            [[['state', '=', 'confirmed']]],
            {'fields': ['id', 'name', 'product_qty', 'qty_produced'], 'limit': 1}
        )

        if not result:
            return None

        mo = result[0]
        print(f"[NEW] Confirmed MO found: {mo['name']} | Target: {mo['product_qty']}")
        return mo

    # =============================
    # START MO (Klik tombol "Mulai")
    # =============================
    def start_mo(self, mo_id):
        # Catatan: Nama method di bawah ('action_start') bisa berbeda tergantung versi Odoo Anda.
        # Jika tombol "Mulai" tidak merespons, kita perlu mengecek nama teknisnya di Odoo.
        result = self.execute(
            'mrp.production',
            'action_start', 
            [[mo_id]]
        )
        
        if result is not None:
            print(f"[ACTION] MO {mo_id} has been started via API")
        return result        

    # =============================
    # GET PRODUCTS (Untuk Form MO)
    # =============================
    def get_products(self):
        result = self.execute(
            'product.product',
            'search_read',
            [[['type', 'in', ['product', 'consu']]]],
            {'fields': ['id', 'display_name'], 'limit': 50}
        )
        return result if result else []

    # =============================
    # GET ALL MOs (Untuk Tabel History)
    # =============================
    def get_mos(self):
        result = self.execute(
            'mrp.production',
            'search_read',
            [[]],
            {
                'fields': ['id', 'name', 'product_id', 'product_qty', 'state', 'date_start'], 
                'limit': 50, 
                'order': 'id desc'
            }
        )
        return result if result else []

    # =============================
    # CREATE NEW MO
    # =============================
    def create_mo_record(self, product_id, qty):
        result = self.execute(
            'mrp.production',
            'create',
            [{
                'product_id': product_id,
                'product_qty': qty,
            }]
        )
        if result:
            print(f"[CREATE] New MO created with ID: {result}")
        return result

    # =============================
    # CONFIRM MO (Otomatis Konfirmasi)
    # =============================
    def confirm_mo(self, mo_id):
        # Memanggil fungsi 'action_confirm' di Odoo untuk mengubah Draft menjadi Confirmed
        result = self.execute(
            'mrp.production',
            'action_confirm',
            [[mo_id]]
        )
        if result is not None:
            print(f"[ACTION] MO {mo_id} has been confirmed via API")
        return result