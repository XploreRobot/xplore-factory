import React, { useState, useEffect } from 'react';
import { Factory, Plus, ClipboardList, Package, Hash, Loader2, Clock, CheckCircle2, PlayCircle, XCircle, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';

const ManufacturingOrders = () => {
    // State untuk Data
    const [products, setProducts] = useState([]);
    const [orders, setOrders] = useState([]);
    
    // State untuk Form & Loading
    const [selectedProduct, setSelectedProduct] = useState('');
    const [quantity, setQuantity] = useState(1);
    const [loadingData, setLoadingData] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    
    // NEW: State untuk loading per baris saat proses pembatalan
    const [cancellingId, setCancellingId] = useState(null);

    // Konfigurasi API
    const host = import.meta.env.VITE_BACK_HOST || 'localhost';
    const backendUrl = host.includes(':') ? `http://${host}` : `http://${host}:8000`;
    const getToken = () => localStorage.getItem('token');

    // Mengambil data Produk dan MO dari Backend
    const fetchData = async () => {
        setLoadingData(true);
        const token = getToken();
        const headers = { 'Authorization': `Bearer ${token}` };

        try {
            // Fetch Products
            const prodRes = await fetch(`${backendUrl}/api/products`, { headers });
            if (prodRes.ok) {
                const prodData = await prodRes.json();
                setProducts(prodData);
            }

            // Fetch MOs
            const moRes = await fetch(`${backendUrl}/api/mo`, { headers });
            if (moRes.ok) {
                const moData = await moRes.json();
                setOrders(moData);
            }
        } catch (error) {
            toast.error('Gagal terhubung ke server');
            console.error("Fetch error:", error);
        } finally {
            setLoadingData(false);
        }
    };

    useEffect(() => {
        fetchData();
    }, []);

    // Handle Submit Form Pembuatan MO
    const handleCreateMO = async (e) => {
        e.preventDefault();
        if (!selectedProduct) {
            toast.error('Silakan pilih produk terlebih dahulu');
            return;
        }

        setSubmitting(true);
        const token = getToken();

        try {
            const response = await fetch(`${backendUrl}/api/mo`, {
                method: 'POST',
                headers: { 
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    product_id: parseInt(selectedProduct),
                    product_qty: quantity
                })
            });

            if (!response.ok) {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.detail || 'Gagal membuat Manufacturing Order di Odoo');
            }

            toast.success('Manufacturing Order berhasil dibuat di Odoo!');
            setQuantity(1);
            setSelectedProduct('');
            fetchData(); // Refresh tabel setelah berhasil
            
        } catch (error) {
            toast.error(error.message);
        } finally {
            setSubmitting(false);
        }
    };

    // NEW: Handle Cancel MO
    const handleCancelMO = async (mo_id, mo_name) => {
        if (!window.confirm(`Are you sure you want to cancel MO: ${mo_name}?`)) return;

        setCancellingId(mo_id);
        const token = getToken();

        try {
            const response = await fetch(`${backendUrl}/cancel_mo/${mo_id}`, {
                method: 'POST',
                headers: { 
                    'Authorization': `Bearer ${token}`
                }
            });

            if (!response.ok) {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.detail || 'Gagal membatalkan Manufacturing Order');
            }

            toast.success(`MO ${mo_name} berhasil dibatalkan`);
            fetchData(); // Refresh tabel setelah dibatalkan
            
        } catch (error) {
            toast.error(error.message);
            console.error("Cancel MO Error:", error);
        } finally {
            setCancellingId(null);
        }
    };

    // Helper untuk warna badge status Odoo
    const getStatusBadge = (status) => {
        switch (status) {
            case 'done':
                return <span className="flex items-center gap-1 w-fit px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-success-container text-on-success-container"><CheckCircle2 size={12} /> Done</span>;
            case 'confirmed':
            case 'progress':
                return <span className="flex items-center gap-1 w-fit px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-primary-container text-on-primary-container"><PlayCircle size={12} /> In Progress</span>;
            case 'cancel':
                return <span className="flex items-center gap-1 w-fit px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-error-container text-on-error-container"><XCircle size={12} /> Cancelled</span>;
            case 'draft':
            default:
                return <span className="flex items-center gap-1 w-fit px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-surface-container-high text-on-surface-variant"><Clock size={12} /> Draft</span>;
        }
    };

    return (
        <div className="p-8 max-w-7xl mx-auto">
            <div className="flex justify-between items-end mb-8">
                <div>
                    <h1 className="text-3xl font-bold text-primary flex items-center gap-3">
                        <Factory className="text-secondary" /> Manufacturing Orders
                    </h1>
                    <p className="text-on-surface-variant mt-2">Manage production orders and sync directly with Odoo MRP.</p>
                </div>
                <button 
                    onClick={fetchData}
                    className="p-2 bg-surface-container-low border border-outline-variant rounded-lg text-primary hover:bg-surface-container transition-all"
                    title="Refresh Data"
                >
                    <RefreshCw size={20} className={loadingData ? "animate-spin" : ""} />
                </button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Form Buat MO Baru */}
                <div className="bg-surface-container-lowest p-6 rounded-2xl border border-outline-variant shadow-sm h-fit">
                    <h2 className="text-xl font-bold text-primary mb-6 flex items-center gap-2">
                        <Plus size={20} /> Create New MO
                    </h2>
                    <form onSubmit={handleCreateMO} className="space-y-4">
                        <div className="space-y-1">
                            <label className="text-xs font-bold text-on-surface-variant uppercase tracking-wider flex items-center gap-1">
                                <Package size={14} /> Product
                            </label>
                            <select 
                                value={selectedProduct}
                                onChange={(e) => setSelectedProduct(e.target.value)}
                                className="w-full px-4 py-2.5 rounded-xl bg-surface-container-low border border-outline-variant focus:border-primary outline-none transition-all"
                                required
                                disabled={loadingData}
                            >
                                <option value="" disabled>-- Select a Product --</option>
                                {products.map(p => (
                                    <option key={p.id} value={p.id}>{p.display_name}</option>
                                ))}
                            </select>
                        </div>
                        <div className="space-y-1">
                            <label className="text-xs font-bold text-on-surface-variant uppercase tracking-wider flex items-center gap-1">
                                <Hash size={14} /> Target Quantity
                            </label>
                            <input 
                                type="number"
                                min="1"
                                step="1"
                                value={quantity}
                                onChange={(e) => setQuantity(Number(e.target.value))}
                                className="w-full px-4 py-2.5 rounded-xl bg-surface-container-low border border-outline-variant focus:border-primary outline-none transition-all"
                                placeholder="Enter quantity"
                                required
                                disabled={loadingData}
                            />
                        </div>
                        <button 
                            disabled={submitting || loadingData}
                            className="w-full py-3 bg-primary text-on-primary rounded-xl font-bold hover:bg-primary-container transition-all flex items-center justify-center gap-2 mt-4 disabled:opacity-50"
                        >
                            {submitting ? <Loader2 className="animate-spin" size={20} /> : <ClipboardList size={20} />}
                            Create Order
                        </button>
                    </form>
                </div>

                {/* Tabel Daftar MO */}
                <div className="lg:col-span-2 bg-surface-container-lowest rounded-2xl border border-outline-variant shadow-sm overflow-hidden">
                    <div className="p-6 border-b border-outline-variant flex justify-between items-center bg-surface-container-low">
                        <h2 className="text-xl font-bold text-primary flex items-center gap-2">
                            <ClipboardList size={20} /> Order History
                        </h2>
                        <span className="px-3 py-1 bg-primary/10 text-primary text-xs font-bold rounded-full">
                            {orders.length} Records
                        </span>
                    </div>
                    
                    <div className="overflow-x-auto">
                        <table className="w-full text-left">
                            <thead className="bg-surface-container text-on-surface-variant text-xs font-bold uppercase tracking-wider">
                                <tr>
                                    <th className="px-6 py-4">Reference</th>
                                    <th className="px-6 py-4">Product</th>
                                    <th className="px-6 py-4 text-center">Target Qty</th>
                                    <th className="px-6 py-4">Status</th>
                                    <th className="px-6 py-4">Date</th>
                                    {/* NEW: Kolom Action */}
                                    <th className="px-6 py-4 text-center">Action</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-outline-variant">
                                {loadingData && orders.length === 0 ? (
                                    <tr>
                                        <td colSpan="6" className="px-6 py-12 text-center text-on-surface-variant">
                                            <Loader2 className="animate-spin mx-auto mb-2" size={24} />
                                            Loading data from Odoo...
                                        </td>
                                    </tr>
                                ) : orders.map((order) => (
                                    <tr key={order.id} className="hover:bg-surface-container-low transition-colors">
                                        <td className="px-6 py-4 font-semibold text-primary">
                                            {order.name}
                                        </td>
                                        <td className="px-6 py-4 text-sm font-medium text-on-surface">
                                            {order.product}
                                        </td>
                                        <td className="px-6 py-4 text-center font-bold text-on-surface">
                                            {order.qty}
                                        </td>
                                        <td className="px-6 py-4">
                                            {getStatusBadge(order.status)}
                                        </td>
                                        <td className="px-6 py-4 text-xs text-on-surface-variant font-medium">
                                            {order.date}
                                        </td>
                                        
                                        {/* NEW: Kolom Tombol Cancel */}
                                        <td className="px-6 py-4 text-center">
                                            {/* Hanya tampilkan tombol cancel jika status bukan done atau cancel */}
                                            {['draft', 'confirmed', 'progress'].includes(order.status) && (
                                                <button
                                                    onClick={() => handleCancelMO(order.id, order.name)}
                                                    disabled={cancellingId === order.id}
                                                    className="inline-flex items-center justify-center p-1.5 bg-error/10 text-error hover:bg-error hover:text-white rounded-lg transition-colors disabled:opacity-50"
                                                    title="Cancel MO"
                                                >
                                                    {cancellingId === order.id ? (
                                                        <Loader2 size={16} className="animate-spin" />
                                                    ) : (
                                                        <XCircle size={16} />
                                                    )}
                                                </button>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default ManufacturingOrders;