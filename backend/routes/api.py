from pydantic import BaseModel  # <--- TAMBAHKAN BARIS INI
from core import state
from fastapi import APIRouter, Depends, HTTPException
from routes.auth import decode_access_token, oauth2_scheme
from services.db_service import (
    finish_mo,
    get_history,
    get_mo_detail,
    get_mo_history,
    get_production_history,
)
from services.mqtt_service import publish

router = APIRouter()


@router.post("/control")
async def control_machine(command: str, token: str = Depends(oauth2_scheme)):
    payload = decode_access_token(token)
    if not payload or payload.get("role") not in ["admin", "operator"]:
        raise HTTPException(
            status_code=403, detail="Sesi berakhir atau Anda tidak memiliki akses"
        )

    # PERBAIKAN: Samakan topik dan payload dengan sistem Odoo listener
    topic = "mes/control"
    
    # PERBAIKAN: Gunakan format dictionary (JSON) agar sesuai dengan mesin
    publish(topic, {"command": command})
    
    return {"status": "success", "command": command}


@router.get("/")
def root():
    return {"status": "backend running"}


@router.get("/history")
def history(
    limit: int = 20, search: str = None, result: str = None
):
    data = get_history(search=search, result_filter=result, limit=limit)
    return {"data": data}


@router.get("/production_history")
def production_history(
    limit: int = 20, search: str = None, result: str = None
):
    data = get_production_history(
        search=search, result_filter=result, limit=limit
    )
    return {"data": data}


@router.get("/mo_detail/{mo_id}")
def mo_detail(mo_id: str):  # <-- PERBAIKAN 1: Mengubah int menjadi str
    data = get_mo_detail(mo_id)

    if not data:
        raise HTTPException(status_code=404, detail="MO detail not found")

    return {"status": "success", "data": data}


@router.get("/mo_history")
def mo_history(limit: int = 20, search: str = None):
    # <-- PERBAIKAN 2: Menghapus result_filter agar cocok dengan db_service.py
    data = get_mo_history(search=search, limit=limit)

    # <-- PERBAIKAN 3: Langsung kembalikan array (walau kosong tetap 'success')
    return {"status": "success", "data": data}

# ==========================================
# ENDPOINT UNTUK MANUFACTURING ORDERS (REACT)
# ==========================================

class MOCreate(BaseModel):
    product_id: int
    product_qty: float

@router.get("/api/products")
async def api_get_products():
    if not state.odoo:
        raise HTTPException(status_code=503, detail="Odoo is not connected")
    return state.odoo.get_products()

@router.get("/api/mo")
async def api_get_mos():
    if not state.odoo:
        raise HTTPException(status_code=503, detail="Odoo is not connected")
    
    mos = state.odoo.get_mos()
    result = []
    
    for mo in mos:
        result.append({
            "id": mo['id'],
            "name": mo['name'],
            # product_id berbentuk [1, "Nama Produk"] dari Odoo
            "product": mo['product_id'][1] if mo.get('product_id') else "Unknown Product",
            "qty": mo.get('product_qty', 0),
            "status": mo.get('state', 'draft'),
            "date": mo.get('date_start', '-').split(' ')[0] if mo.get('date_start') else "-"
        })
    return result

# HANYA GUNAKAN SATU ROUTE POST INI:
@router.post("/api/mo")
async def api_create_mo(mo_data: MOCreate):
    if not state.odoo:
        raise HTTPException(status_code=503, detail="Odoo is not connected")
    
    try:
        # 1. Buat MO baru di Odoo
        mo_id = state.odoo.create_mo_record(mo_data.product_id, mo_data.product_qty)
        
        # Jaga-jaga jika Odoo mengembalikan ID dalam bentuk List (misal: [22]) bukan Integer (22)
        if isinstance(mo_id, list) and len(mo_id) > 0:
            mo_id = mo_id[0]
            
        if not mo_id:
            raise HTTPException(status_code=500, detail="Gagal membuat MO di Odoo")
            
        print(f"[API] MO berhasil dibuat dengan ID: {mo_id}. Memulai auto-konfirmasi...")
        
        # 2. Otomatis klik tombol Konfirmasi via API
        state.odoo.confirm_mo(mo_id)
        
        return {"message": "Manufacturing Order created and confirmed successfully", "mo_id": mo_id}
        
    except Exception as e:
        print(f"[API ERROR] Gagal memproses pesanan: {e}")
        raise HTTPException(status_code=500, detail=str(e))