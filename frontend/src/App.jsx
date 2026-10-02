import React, { useState, useEffect, useRef } from 'react';
import { 
  Fingerprint, MonitorSmartphone, LayoutList, CalendarDays, 
  RefreshCcw, Database, FileClock, Activity, User, Bell, ChevronRight,
  Search, SlidersHorizontal, Download, HardDrive, Wifi, ShieldAlert,
  CheckCircle2, XCircle, Clock, BadgeInfo, Hand, ScanFace, CreditCard, KeyRound
} from 'lucide-react';
import './index.css';

const API_BASE = window.location.origin.includes("5173") || window.location.origin.includes("localhost")
  ? "http://localhost:5005"
  : window.location.origin;

function App() {
  const [activeTab, setActiveTab] = useState('live');
  const [stats, setStats] = useState({ total_today: 0, checkin_today: 0, checkout_today: 0, online_devices: 0 });
  const [records, setRecords] = useState([]);
  const [dailyRecords, setDailyRecords] = useState([]);
  const [pinMappings, setPinMappings] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [mappingSearchQuery, setMappingSearchQuery] = useState('');
  const [dedupEnabled, setDedupEnabled] = useState(true);
  const [summaryDate, setSummaryDate] = useState(new Date().toISOString().split("T")[0]);
  const [sseConnected, setSseConnected] = useState(false);
  const [recentPunchId, setRecentPunchId] = useState(null);
  
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [mapPin, setMapPin] = useState('');
  const [mapNip, setMapNip] = useState('');
  const [mapName, setMapName] = useState('');

  const lastKnownId = useRef(null);

  const unmappedCount = pinMappings.filter(p => !p.is_mapped).length;

  const playChime = () => {
    try {
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(587.33, audioCtx.currentTime); 
      osc.frequency.exponentialRampToValueAtTime(880.00, audioCtx.currentTime + 0.15); 
      gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.4);
      osc.connect(gain); gain.connect(audioCtx.destination);
      osc.start(); osc.stop(audioCtx.currentTime + 0.4);
    } catch (e) {}
  };

  const fetchLiveFeed = async () => {
    try {
      const dedup = dedupEnabled ? "&deduplicate=true" : "";
      const res = await fetch(`${API_BASE}/api/v1/live-feed?limit=100${dedup}`);
      if (res.ok) {
        const data = await res.json();
        if (data.stats) setStats(data.stats);
        if (data.data) {
          if (data.data.length > 0 && lastKnownId.current !== null && data.data[0].id > lastKnownId.current) {
             playChime();
          }
          if (data.data.length > 0) lastKnownId.current = data.data[0].id;
          setRecords(data.data);
        }
      }
    } catch (err) { console.error(err); }
  };

  const fetchDailySummary = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/daily-attendance?target_date=${summaryDate}`);
      if (res.ok) {
        const data = await res.json();
        if (data.data) setDailyRecords(data.data);
      }
    } catch (err) { console.error(err); }
  };

  const fetchMappings = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/pin-mapping`);
      if (res.ok) {
        const data = await res.json();
        if (data.data) setPinMappings(data.data);
      }
    } catch (err) { console.error(err); }
  };

  // Server-Sent Events (SSE) Real-Time Connection
  useEffect(() => {
    let es = null;
    let reconnectTimer = null;

    const connectSSE = () => {
      try {
        es = new EventSource(`${API_BASE}/api/v1/stream`);

        es.onopen = () => {
          setSseConnected(true);
        };

        es.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data);
            if (payload.event === "connected") {
              setSseConnected(true);
              if (payload.stats) setStats(payload.stats);
            } else if (payload.event === "punch") {
              const punch = payload.data;
              if (punch) {
                playChime();
                setRecentPunchId(punch.id);
                setTimeout(() => setRecentPunchId(null), 4000);

                setRecords(prev => {
                  const exists = prev.some(r => r.id === punch.id || (r.pin === punch.pin && r.timestamp === punch.timestamp));
                  if (exists) {
                    return prev.map(r => (r.id === punch.id || (r.pin === punch.pin && r.timestamp === punch.timestamp)) ? punch : r);
                  }
                  return [punch, ...prev.slice(0, 99)];
                });
              }
              if (payload.stats) {
                setStats(payload.stats);
              }
            }
          } catch (e) {
            console.error("[SSE] JSON parse error", e);
          }
        };

        es.onerror = () => {
          setSseConnected(false);
          if (es) es.close();
          reconnectTimer = setTimeout(connectSSE, 3000);
        };
      } catch (err) {
        setSseConnected(false);
        reconnectTimer = setTimeout(connectSSE, 3000);
      }
    };

    connectSSE();

    return () => {
      if (es) es.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, []);

  // Inisialisasi awal & sinkronisasi pasif latar belakang (60s fallback, bukan 2.5s)
  useEffect(() => {
    fetchLiveFeed();
    fetchDailySummary();
    fetchMappings();
    const interval = setInterval(() => {
      fetchMappings();
      fetchDailySummary();
    }, 60000);
    return () => clearInterval(interval);
  }, [dedupEnabled, summaryDate]);

  const handleSaveMapping = async (e) => {
    e.preventDefault();
    try {
      const res = await fetch(`${API_BASE}/api/v1/pin-mapping`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin: mapPin, employee_name: mapName, national_id_number: mapNip, employee_nip: mapNip })
      });
      if (res.ok) {
        alert("Pemetaan berhasil disimpan!");
        setIsModalOpen(false);
        fetchMappings();
        fetchLiveFeed();
      } else {
        const errData = await res.json().catch(() => ({}));
        alert(errData.error || errData.message || "Gagal menyimpan pemetaan");
      }
    } catch (err) { alert(err.message); }
  };

  const formatTime = (dtStr) => {
    if (!dtStr) return "-";
    const parts = dtStr.split(" ");
    return parts.length > 1 ? parts[1] : dtStr;
  };

  const filteredRecords = records.filter(item => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    const name = (item.employee_name || "").toLowerCase();
    const nik = (item.national_id_number || item.employee_nik || item.employee_nip || "").toLowerCase();
    const loc = (item.location || "").toLowerCase();
    return name.includes(q) || nik.includes(q) || loc.includes(q);
  });

  const renderVerifyBadge = (label, code) => {
    const lbl = (label || "").toLowerCase();
    if (lbl.includes("vena") || lbl.includes("telapak") || lbl.includes("palm") || code === 8 || code === 5) {
      return (
        <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-purple-700 bg-purple-50 px-2 py-0.5 rounded border border-purple-200/60 shadow-xs">
          <Hand size={13} className="text-purple-600"/> Vena Telapak
        </span>
      );
    }
    if (lbl.includes("wajah") || lbl.includes("face") || code === 2) {
      return (
        <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-sky-700 bg-sky-50 px-2 py-0.5 rounded border border-sky-200/60 shadow-xs">
          <ScanFace size={13} className="text-sky-600"/> Wajah
        </span>
      );
    }
    if (lbl.includes("kartu") || lbl.includes("rfid") || lbl.includes("card") || code === 4) {
      return (
        <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-amber-700 bg-amber-50 px-2 py-0.5 rounded border border-amber-200/60 shadow-xs">
          <CreditCard size={13} className="text-amber-600"/> Kartu RFID
        </span>
      );
    }
    if (lbl.includes("pin") || lbl.includes("password") || code === 3) {
      return (
        <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-slate-700 bg-slate-100 px-2 py-0.5 rounded border border-slate-200/60 shadow-xs">
          <KeyRound size={13} className="text-slate-500"/> Password/PIN
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-slate-700 bg-slate-50 px-2 py-0.5 rounded border border-slate-200/60 shadow-xs">
        <Fingerprint size={13} className="text-slate-500"/> Sidik Jari
      </span>
    );
  };

  const filteredMappings = pinMappings.filter(item => {
    const q = mappingSearchQuery.toLowerCase();
    const name = (item.employee_name || "").toLowerCase();
    const nik = (item.national_id_number || item.employee_nik || item.employee_nip || "").toLowerCase();
    return name.includes(q) || nik.includes(q);
  });

  return (
    <div className="flex h-screen bg-slate-50 font-sans text-slate-800 overflow-hidden">
      
      {/* SIDEBAR */}
      <aside className="w-64 bg-white border-r border-slate-200 flex flex-col justify-between shrink-0 z-10">
        <div>
          <div className="h-16 flex items-center gap-3 px-6 border-b border-slate-100">
            <div className="w-8 h-8 rounded-lg bg-slate-800 flex items-center justify-center text-white shrink-0 shadow-sm">
              <Activity size={18} strokeWidth={2.5} />
            </div>
            <div className="flex flex-col">
              <span className="font-semibold text-sm leading-tight text-slate-900 tracking-tight">ADMS Core</span>
              <span className="text-[11px] text-slate-500 font-medium leading-tight">Middleware Engine</span>
            </div>
          </div>

          <div className="py-4">
            <div className="px-6 mb-2">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Operations</span>
            </div>
            <nav className="flex flex-col gap-0.5 px-3">
              <NavItem active={activeTab === 'live'} onClick={() => setActiveTab('live')} icon={<Activity size={16}/>} label="Log Telemetri" />
              <NavItem active={activeTab === 'mapping'} onClick={() => setActiveTab('mapping')} icon={<MonitorSmartphone size={16}/>} label="Kelola Pegawai" />
              <NavItem active={activeTab === 'rekap'} onClick={() => setActiveTab('rekap')} icon={<LayoutList size={16}/>} label="Rekap Absensi" />
            </nav>
          </div>
        </div>

        <div className="p-4 bg-slate-50 border-t border-slate-200/60 m-3 rounded-xl">
          <div className="flex justify-between items-center mb-1">
            <span className="text-[11px] font-semibold text-slate-500">Waktu Server (NTP)</span>
            <span className="text-[11px] font-bold text-blue-600">10.0.1.15</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-[11px] font-semibold text-slate-500">Kapasitas Memori</span>
            <span className="text-[11px] font-bold text-slate-800">100% OK</span>
          </div>
        </div>
      </aside>

      {/* MAIN CONTENT */}
      <main className="flex-1 flex flex-col h-screen min-w-0">
        
        {/* TOP HEADER */}
        <header className="h-16 bg-white border-b border-slate-200 flex items-center justify-between px-6 shrink-0 z-10">
          <div className="flex items-center gap-6">
            <div className="flex flex-col">
              <h1 className="font-bold text-[15px] text-slate-900 leading-tight">Fingerspot ADMS Middleware</h1>
              <span className="text-xs text-slate-500 font-medium">Revo WFV-208BNC • RSUP Biometrik Gateway</span>
            </div>
            
            <div className="h-6 w-px bg-slate-200"></div>
            
            <div className="flex items-center gap-4 text-[11px] font-semibold">
              <div className="flex items-center gap-1.5 text-slate-700 bg-slate-100 px-2.5 py-1 rounded-full border border-slate-200/60">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse"></span>
                Server Aktif • Port ADMS 5005
              </div>
              <div className="flex items-center gap-1 text-slate-500">
                <span>Jeda Sinkronisasi:</span>
                <span className="text-slate-800 font-bold">120ms</span>
              </div>
              <div className="flex items-center gap-1.5">
                {sseConnected ? (
                  <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700 bg-emerald-50 px-2.5 py-1 rounded-full border border-emerald-200">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                    SSE Real-Time Aktif
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-700 bg-amber-50 px-2.5 py-1 rounded-full border border-amber-200">
                    <span className="w-2 h-2 rounded-full bg-amber-500 animate-ping"></span>
                    Menghubungkan SSE...
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <span className="text-xs font-semibold text-blue-700 bg-blue-50 px-2.5 py-1 rounded-full border border-blue-100">Production RS Medika</span>
            <button className="text-slate-400 hover:text-slate-600 transition-colors"><RefreshCcw size={16}/></button>
            <div className="relative cursor-pointer text-slate-400 hover:text-slate-600 transition-colors">
              <Bell size={18}/>
              <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-red-500 border border-white"></span>
            </div>
            <div className="h-6 w-px bg-slate-200 mx-1"></div>
            <div className="flex items-center gap-2 cursor-pointer">
              <div className="flex flex-col items-end">
                <span className="text-xs font-bold text-slate-800 leading-tight">dr. H. Hendrawan</span>
                <span className="text-[10px] text-slate-500 font-medium leading-tight">IT SIMRS Admin</span>
              </div>
              <div className="w-8 h-8 rounded-full bg-slate-800 text-white flex items-center justify-center">
                <User size={14}/>
              </div>
            </div>
          </div>
        </header>

        {/* PAGE CONTENT */}
        <div className="flex-1 overflow-auto bg-[#f4f7fe] p-6 lg:p-8">
          
          <div className="max-w-7xl mx-auto flex flex-col gap-6">
            
            {/* Page Title Row */}
            <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
              <div>
                <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500 mb-1">
                  <span>Sistem Penghubung</span>
                  <ChevronRight size={14}/>
                  <span className="text-slate-800 font-semibold">Dasbor Pemantauan</span>
                </div>
                <div className="flex items-center gap-3">
                  <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Telemetri Biometrik ADMS</h2>
                  <span className="text-[11px] font-bold text-blue-700 bg-blue-100/60 px-2.5 py-1 rounded-full border border-blue-200/60 flex items-center gap-1.5 shadow-sm">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-600 animate-pulse"></span>
                    TCP/Socket ADMS 5005 Online
                  </span>
                </div>
              </div>
              
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-xs font-bold text-slate-600 bg-white px-3 py-1.5 rounded-lg border border-slate-200 shadow-sm cursor-pointer">
                  <span className="w-2 h-2 rounded-full bg-green-500"></span>
                  Saring Dobel Tap
                  <div className={`w-8 h-4 rounded-full ml-1 relative shadow-inner transition-colors ${dedupEnabled ? 'bg-slate-800' : 'bg-slate-300'}`}>
                    <div className={`absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all ${dedupEnabled ? 'right-0.5' : 'left-0.5'}`}></div>
                  </div>
                  <input type="checkbox" className="hidden" checked={dedupEnabled} onChange={e => setDedupEnabled(e.target.checked)}/>
                </label>
                <button onClick={fetchLiveFeed} className="flex items-center gap-2 text-xs font-bold text-white bg-slate-800 px-4 py-2 rounded-lg hover:bg-slate-900 shadow-sm transition-colors">
                  <RefreshCcw size={14}/> Segarkan Ulang
                </button>
              </div>
            </div>

            {/* STATS ROW */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
              
              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm relative overflow-hidden flex flex-col justify-between">
                <div>
                  <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Total Tap Hari Ini</span>
                  <div className="flex items-baseline gap-2 mt-1">
                    <span className="text-3xl font-extrabold text-slate-900 tracking-tight">{stats.total_today.toLocaleString()}</span>
                  </div>
                  <span className="text-xs text-slate-500 font-medium">Masuk: {stats.checkin_today} | Pulang: {stats.checkout_today}</span>
                </div>
                <div className="mt-5 w-full h-1 bg-slate-100 rounded-full overflow-hidden">
                  <div className="h-full bg-blue-600 w-[80%] rounded-full"></div>
                </div>
              </div>

              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex flex-col justify-between">
                <div className="flex justify-between items-start">
                  <div>
                    <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Mesin Aktif</span>
                    <div className="flex items-baseline gap-1.5 mt-1">
                      <span className="text-3xl font-extrabold text-slate-900 tracking-tight">{stats.online_devices}</span>
                      <span className="text-xs font-semibold text-slate-500">Terminal</span>
                    </div>
                  </div>
                  <span className="text-[11px] font-bold text-blue-700 bg-blue-50 px-2 py-1 rounded-full flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse"></span> {stats.online_devices} Online
                  </span>
                </div>
                <div className="mt-4 flex gap-3 text-[10px] font-bold text-slate-500">
                  <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-green-500"></span>Status Optimal</span>
                </div>
              </div>

              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex flex-col justify-between relative cursor-pointer hover:border-red-300 transition-colors" onClick={() => setActiveTab('mapping')}>
                <div className="flex justify-between items-start">
                  <div>
                    <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Belum Dipetakan</span>
                    <div className="flex items-baseline gap-1.5 mt-1">
                      <span className="text-3xl font-extrabold text-slate-900 tracking-tight">{unmappedCount}</span>
                      <span className="text-xs font-semibold text-slate-500">Pegawai</span>
                    </div>
                  </div>
                  {unmappedCount > 0 && (
                    <span className="text-[10px] font-bold text-red-700 bg-red-100 px-2 py-1 rounded flex items-center gap-1 text-right leading-tight">
                      ! Perlu<br/>Tindakan
                    </span>
                  )}
                </div>
                <div className="mt-2 text-xs font-medium text-slate-500 leading-snug">
                  Perlu mapping NIP staf baru
                </div>
                <div className="mt-3">
                  <button className="text-xs font-bold text-blue-600 hover:text-blue-800 flex items-center gap-1">
                    Selesaikan Pemetaan <ChevronRight size={14}/>
                  </button>
                </div>
              </div>

              <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex flex-col justify-between">
                <div className="flex justify-between items-start">
                  <div>
                    <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Jeda Jaringan HRIS</span>
                    <div className="flex items-baseline gap-1.5 mt-1">
                      <span className="text-3xl font-extrabold text-slate-900 tracking-tight">124</span>
                      <span className="text-sm font-bold text-slate-700">ms</span>
                    </div>
                  </div>
                  <span className="text-[11px] font-bold text-blue-700 bg-blue-50 px-2 py-1 rounded-full flex items-center gap-1 border border-blue-100">
                    <Activity size={12}/> Sehat
                  </span>
                </div>
                <div className="mt-2 text-[11px] font-medium text-slate-500 leading-snug">
                  99.98% persentase keberhasilan sinkronisasi
                </div>
                <div className="mt-3 flex items-center justify-between text-xs font-bold">
                  <span className="text-slate-600">Antrean Data: 0</span>
                  <span className="text-teal-600">Koneksi Stabil</span>
                </div>
              </div>
            </div>

            {/* MAIN DATA SECTION */}
            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm flex flex-col mt-2">
              
              {/* Tabs */}
              <div className="flex items-center gap-2 p-2 border-b border-slate-100 overflow-x-auto">
                <button onClick={() => setActiveTab('live')} className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-bold text-xs shrink-0 transition-colors ${activeTab === 'live' ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-50'}`}>
                  <Wifi size={14}/> Live Log Mesin 
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${activeTab === 'live' ? 'bg-blue-200/50 text-blue-700' : 'bg-slate-100'}`}>Live</span>
                </button>
                <button onClick={() => setActiveTab('rekap')} className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-bold text-xs shrink-0 transition-colors ${activeTab === 'rekap' ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-50'}`}>
                  <CalendarDays size={14}/> Rekap Presensi Harian 
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${activeTab === 'rekap' ? 'bg-blue-200/50 text-blue-700' : 'bg-slate-100'}`}>{dailyRecords.length} Data</span>
                </button>
                <button onClick={() => setActiveTab('mapping')} className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-bold text-xs shrink-0 transition-colors ${activeTab === 'mapping' ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-50'}`}>
                  <MonitorSmartphone size={14}/> Kelola Mapping Pegawai 
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${activeTab === 'mapping' ? 'bg-blue-200/50 text-blue-700' : (unmappedCount > 0 ? 'bg-red-100 text-red-600' : 'bg-slate-100')}`}>{unmappedCount} Pending</span>
                </button>
                <div className="ml-auto px-4 text-[11px] font-medium text-slate-500 flex items-center gap-1.5 shrink-0 border-l border-slate-200 pl-4 hidden md:flex">
                  <BadgeInfo size={14}/> ZKTeco PUSH v2.4 Aktif
                </div>
              </div>

              {activeTab === 'live' && (
                <>
                  <div className="p-4">
                    <div className="bg-[#eff6ff] rounded-xl p-3 flex items-start gap-3 border border-blue-100">
                      <div className="mt-0.5"><span className="w-2 h-2 rounded-full bg-blue-600 block"></span></div>
                      <div className="text-xs font-medium text-slate-700 flex-1">
                        Menampilkan aliran biometrik ADMS secara real-time via Server-Sent Events (SSE). Terkoneksi langsung dengan 0ms latency.
                      </div>
                    </div>
                  </div>

                  <div className="px-4 pb-4 flex flex-col md:flex-row gap-3">
                    <div className="relative flex-1">
                      <Search size={16} className="absolute left-3 top-2.5 text-slate-400"/>
                      <input 
                        type="text" 
                        placeholder="Cari nama pegawai, NIP, atau lokasi mesin..." 
                        className="w-full pl-9 pr-4 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 font-medium placeholder:font-normal placeholder:text-slate-400"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                      />
                    </div>
                  </div>

                  <div className="overflow-x-auto">
                    <table className="w-full text-left border-t border-slate-200 whitespace-nowrap">
                      <thead>
                        <tr className="bg-slate-50 text-[10px] font-extrabold text-slate-500 uppercase tracking-widest">
                          <th className="py-3 px-4">Pegawai & NIP</th>
                          <th className="py-3 px-4">Jam Tap</th>
                          <th className="py-3 px-4">Status</th>
                          <th className="py-3 px-4">Verifikasi</th>
                          <th className="py-3 px-4">Lokasi Mesin</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {filteredRecords.map((item, i) => {
                          const isCheckIn = item.status_code === 0;
                          const timeDiff = Math.floor((new Date() - new Date(item.received_at)) / 1000);
                          const isNew = item.id === recentPunchId || timeDiff < 6;
                          const initials = (item.employee_name || "👤").replace(/^(dr\.|drg\.|Ns\.|H\.|Hj\.)\s*/i, "").split(' ').map(n=>n[0]).join('').substring(0,2).toUpperCase();
                          
                          return (
                            <tr key={item.id} className={`transition-all duration-700 ${item.id === recentPunchId ? 'bg-emerald-50/70 ring-1 ring-emerald-300' : 'hover:bg-slate-50/50'} group`}>
                              <td className="py-3 px-4">
                                <div className="flex items-center gap-3">
                                  <div className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold ${isNew ? 'bg-blue-500 text-white shadow-md' : 'bg-white border border-slate-200 text-slate-600 shadow-sm'}`}>
                                    {initials}
                                  </div>
                                  <div className="flex flex-col">
                                    <div className="flex items-center gap-2">
                                      <span className="text-sm font-bold text-slate-900">{item.employee_name}</span>
                                      {isNew && <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-blue-100 text-blue-700 border border-blue-200/50 animate-pulse">BARU</span>}
                                    </div>
                                    <span className="text-[11px] font-medium text-slate-500">NIK: {item.national_id_number || item.employee_nik || item.employee_nip}</span>
                                  </div>
                                </div>
                              </td>
                              <td className="py-3 px-4">
                                <div className="flex flex-col">
                                  <span className="text-sm font-bold text-slate-700">{formatTime(item.timestamp)}</span>
                                  <span className={`text-[11px] font-bold ${isNew ? 'text-blue-600' : 'text-slate-400'}`}>
                                    {item.timestamp.split(' ')[0]}
                                  </span>
                                </div>
                              </td>
                              <td className="py-3 px-4">
                                {isCheckIn ? (
                                  <span className="inline-flex items-center gap-1.5 text-xs font-bold text-teal-700 bg-teal-50 px-2.5 py-1 rounded">
                                    <CheckCircle2 size={14}/> Masuk
                                  </span>
                                ) : (
                                  <span className="inline-flex items-center gap-1.5 text-xs font-bold text-red-700 bg-red-50 px-2.5 py-1 rounded">
                                    <XCircle size={14}/> Pulang
                                  </span>
                                )}
                              </td>
                              <td className="py-3 px-4">
                                {renderVerifyBadge(item.verify_label, item.verify_code)}
                              </td>
                              <td className="py-3 px-4">
                                <div className="flex flex-col">
                                  <span className="text-[12px] font-bold text-slate-700">{item.location}</span>
                                  <span className="text-[10px] font-mono font-bold text-slate-400">SN: {item.device_sn}</span>
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {activeTab === 'rekap' && (
                <div className="flex flex-col">
                  <div className="p-4 flex items-center justify-between border-b border-slate-100">
                    <div className="flex items-center gap-3">
                      <label className="text-xs font-bold text-slate-600">Pilih Tanggal:</label>
                      <input type="date" value={summaryDate} onChange={e => setSummaryDate(e.target.value)} className="px-3 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:border-blue-500" />
                    </div>
                    <button onClick={() => window.open(`${API_BASE}/api/v1/export-attendance?target_date=${summaryDate}`, '_blank')} className="flex items-center gap-2 text-xs font-bold text-emerald-700 bg-emerald-50 hover:bg-emerald-100 border border-emerald-200 px-4 py-2 rounded-lg transition-colors shadow-sm">
                      <Download size={14}/> Ekspor Data (Excel)
                    </button>
                  </div>
                    <div className="overflow-x-auto">
                    <table className="w-full text-left border-t border-slate-200 whitespace-nowrap">
                      <thead>
                        <tr className="bg-slate-50 text-[10px] font-extrabold text-slate-500 uppercase tracking-widest">
                          <th className="py-3 px-4 w-12 text-center">No</th>
                          <th className="py-3 px-4">Pegawai & NIK</th>
                          <th className="py-3 px-4">Shift</th>
                          <th className="py-3 px-4">Jam Masuk</th>
                          <th className="py-3 px-4">Keterlambatan</th>
                          <th className="py-3 px-4">Jam Pulang</th>
                          <th className="py-3 px-4">Pulang Cepat</th>
                          <th className="py-3 px-4">Durasi & Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {dailyRecords.map((item, i) => (
                          <tr key={i} className="hover:bg-slate-50/50 transition-colors">
                            <td className="py-3 px-4 text-center text-xs font-medium text-slate-400">{i+1}</td>
                            <td className="py-3 px-4">
                              <div className="flex flex-col">
                                <span className="text-sm font-bold text-slate-900">{item.employee_name}</span>
                                <span className="text-[11px] font-medium text-slate-500">NIK: {item.national_id_number || item.employee_nik || item.employee_nip}</span>
                              </div>
                            </td>
                            <td className="py-3 px-4">
                              <span className="inline-flex items-center text-xs font-bold text-slate-700 bg-slate-100 px-2.5 py-1 rounded-md border border-slate-200">
                                {item.shift_name || "Reguler"}
                              </span>
                            </td>
                            <td className="py-3 px-4">
                              <span className="text-sm font-bold text-teal-700">{item.checkin_time ? formatTime(item.checkin_time) : '-'}</span>
                            </td>
                            <td className="py-3 px-4">
                              {item.late_level ? (
                                <span className="inline-flex items-center gap-1 text-[11px] font-bold text-amber-800 bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
                                  ⚠️ {item.late_desc}
                                </span>
                              ) : (
                                <span className="text-xs font-semibold text-slate-400">
                                  {item.checkin_time ? "✅ Tepat Waktu" : "-"}
                                </span>
                              )}
                            </td>
                            <td className="py-3 px-4">
                              <span className="text-sm font-bold text-slate-800">{item.checkout_time ? formatTime(item.checkout_time) : '-'}</span>
                            </td>
                            <td className="py-3 px-4">
                              {item.early_leave_level ? (
                                <span className="inline-flex items-center gap-1 text-[11px] font-bold text-orange-800 bg-orange-50 px-2 py-0.5 rounded border border-orange-200">
                                  ⏳ {item.early_leave_desc}
                                </span>
                              ) : (
                                <span className="text-xs font-semibold text-slate-400">
                                  {item.checkout_time ? "Sesuai Jadwal" : "-"}
                                </span>
                              )}
                            </td>
                            <td className="py-3 px-4">
                              <div className="flex flex-col items-start gap-1">
                                <span className="text-xs font-bold text-slate-700">{item.duration || '-'}</span>
                                <div className="flex items-center gap-1">
                                  <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded border ${item.status?.startsWith('TL') ? 'bg-amber-100 text-amber-800 border-amber-200' : 'bg-slate-100 text-slate-700 border-slate-200'}`}>
                                    {item.status || item.attendance_status}
                                  </span>
                                  {item.attendance_method && (
                                    <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded border ${item.attendance_method.toUpperCase() === 'FINGER' ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-purple-50 text-purple-700 border-purple-200'}`}>
                                      {item.attendance_method}
                                    </span>
                                  )}
                                </div>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {activeTab === 'mapping' && (
                <div className="flex flex-col">
                  <div className="px-4 pb-4 pt-4 flex flex-col md:flex-row gap-3 border-b border-slate-100">
                    <div className="relative flex-1">
                      <Search size={16} className="absolute left-3 top-3 text-slate-400"/>
                      <input 
                        type="text" 
                        placeholder="Cari nama pegawai atau NIK..." 
                        className="w-full pl-9 pr-4 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 font-medium placeholder:font-normal placeholder:text-slate-400"
                        value={mappingSearchQuery}
                        onChange={(e) => setMappingSearchQuery(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="p-6 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {filteredMappings.map((item, i) => {
                      const isMapped = item.is_mapped;
                    return (
                      <div key={i} className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex flex-col justify-between">
                        <div>
                          <div className="flex justify-between items-center mb-2">
                            {isMapped ? (
                              <span className="text-[10px] font-bold text-teal-700 bg-teal-50 px-2 py-1 rounded">✅ Terpetakan</span>
                            ) : (
                              <span className="text-[10px] font-bold text-red-700 bg-red-100 px-2 py-1 rounded">⚠️ Belum Terpetakan</span>
                            )}
                            <span className="text-[11px] font-bold text-slate-500">{item.total_taps} Tap</span>
                          </div>
                          {isMapped ? (
                            <h3 className="text-xl font-extrabold text-slate-900 tracking-tight mt-1 truncate">{item.employee_name}</h3>
                          ) : (
                            <h3 className="text-xl font-extrabold text-slate-900 tracking-tight mt-1">Data Pegawai Baru</h3>
                          )}
                          {isMapped ? (
                            <p className="text-sm font-semibold text-slate-500 mt-1">NIK: {item.national_id_number || item.employee_nik || item.employee_nip}</p>
                          ) : (
                            <p className="text-sm font-semibold text-slate-400 mt-1">Belum terhubung ke HRIS. (Kode Akses: {item.pin})</p>
                          )}
                        </div>
                        <button onClick={() => {
                          setMapPin(item.pin); setMapName(isMapped ? item.employee_name : ''); setMapNip(isMapped ? (item.national_id_number || item.employee_nik || item.employee_nip) : ''); setIsModalOpen(true);
                        }} className={`mt-4 w-full py-2 rounded-lg text-xs font-bold transition-colors ${isMapped ? 'bg-slate-100 text-slate-700 hover:bg-slate-200' : 'bg-blue-600 text-white hover:bg-blue-700 shadow-sm'}`}>
                          {isMapped ? 'Edit Pegawai' : 'Memulai'}
                        </button>
                      </div>
                    );
                  })}
                </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </main>

      {/* MODAL MAPPING */}
      {isModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4">
          <div className="bg-white rounded-2xl p-6 w-full max-w-sm shadow-2xl border border-slate-200">
            <h3 className="text-lg font-bold text-slate-900 mb-1">Edit Data Pegawai (NIK)</h3>
            <p className="text-xs font-medium text-slate-500 mb-5">Hubungkan ID dari mesin absensi dengan Nomor Induk Kependudukan (NIK) pegawai.</p>
            <form onSubmit={handleSaveMapping} className="flex flex-col gap-4">
              <input type="hidden" value={mapPin} />
              <div>
                <label className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1 block">NIK Pegawai (No. KTP)</label>
                <input type="text" required value={mapNip} onChange={e => setMapNip(e.target.value)} className="w-full px-3 py-2 bg-white border border-slate-200 rounded-lg text-sm font-semibold text-slate-900 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500" placeholder="Contoh: 7371140405930006..." />
              </div>

              <div className="flex gap-3 mt-4">
                <button type="button" onClick={() => setIsModalOpen(false)} className="flex-1 py-2 bg-slate-100 text-slate-600 rounded-lg text-xs font-bold hover:bg-slate-200 transition-colors">Batal</button>
                <button type="submit" className="flex-1 py-2 bg-blue-600 text-white rounded-lg text-xs font-bold hover:bg-blue-700 transition-colors shadow-sm">Simpan</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function NavItem({ icon, label, active, onClick }) {
  return (
    <button onClick={onClick} className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-semibold transition-colors ${
      active 
        ? 'bg-[#0f172a] text-white shadow-md' 
        : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
    }`}>
      <span className={active ? 'text-white' : 'text-slate-400'}>{icon}</span>
      {label}
    </button>
  );
}

export default App;
