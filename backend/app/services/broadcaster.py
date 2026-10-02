"""
app/services/broadcaster.py — In-memory Pub/Sub Broadcaster for Real-Time SSE
Memungkinkan streaming event absensi langsung ke antarmuka web (browser) dengan zero-latency.
"""
import asyncio
import json
import logging
from typing import Set, Dict, Any, Optional

logger = logging.getLogger(__name__)


class AttendanceBroadcaster:
    """
    Manajer Pub/Sub berbasis asyncio.Queue untuk Server-Sent Events (SSE).
    Mendukung banyak subscriber/klien secara konkuren dengan non-blocking delivery.
    """
    def __init__(self):
        self._subscribers: Set[asyncio.Queue] = set()

    async def subscribe(self) -> asyncio.Queue:
        """Mendaftarkan client baru dan mengembalikan antrean asyncio miliknya."""
        # maxsize=100 untuk mencegah penumpukan memori jika browser klien lag
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        logger.info(f"[SSE BROADCASTER] Klien baru terhubung. Total pendengar aktif: {len(self._subscribers)}")
        return q

    async def unsubscribe(self, q: asyncio.Queue):
        """Menghapus antrean client yang telah terputus."""
        if q in self._subscribers:
            self._subscribers.remove(q)
            logger.info(f"[SSE BROADCASTER] Klien terputus. Sisa pendengar: {len(self._subscribers)}")

    def count(self) -> int:
        """Mengembalikan jumlah subscriber aktif saat ini."""
        return len(self._subscribers)

    async def broadcast(self, event: str, data: Dict[str, Any], stats: Optional[Dict[str, Any]] = None):
        """
        Menyiarkan pesan event ke seluruh client SSE yang terhubung.
        
        Format payload:
        {
            "event": "punch" | "device_status" | "ping",
            "data": { ... },
            "stats": { ... } (opsional statistik terkini)
        }
        """
        if not self._subscribers:
            return

        message = {
            "event": event,
            "data": data,
        }
        if stats is not None:
            message["stats"] = stats

        dead_queues = []
        for q in list(self._subscribers):
            try:
                # Jika queue penuh, buang item tertua agar klien tetap menerima data paling mutakhir
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(message)
            except Exception as e:
                logger.warning(f"[SSE BROADCASTER] Gagal push ke antrean client: {e}")
                dead_queues.append(q)

        # Bersihkan antrean yang rusak
        for dead_q in dead_queues:
            self._subscribers.discard(dead_q)


# Singleton instance broadcaster untuk seluruh aplikasi
broadcaster = AttendanceBroadcaster()
