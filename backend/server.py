from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, UploadFile, File

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import Response, StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime, timezone, timedelta

# Local timezone for Pondok Pesantren (WIB / Asia-Jakarta, UTC+7)
LOCAL_TZ = timezone(timedelta(hours=7))


def get_today_local_iso() -> str:
    """Return today's date in local WIB timezone as YYYY-MM-DD string.

    This avoids issues where server timezone is UTC but users operate in WIB.
    """
    return datetime.now(LOCAL_TZ).date().isoformat()
from passlib.context import CryptContext
from jose import JWTError, jwt
import os
import logging
import uuid
import qrcode
import io
import base64
import aiohttp
import pandas as pd
from pathlib import Path
import re
import json

import firebase_admin
from firebase_admin import credentials, messaging

# ==================== SETUP ====================
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

WHATSAPP_BOT_URL = os.environ.get('WHATSAPP_BOT_URL')  # optional: URL service bot WA Web

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'absensi_sholat')]

SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your-secret-key-change-this-in-production')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# Initialize Firebase Admin for FCM
firebase_app = None
firebase_cred_path = ROOT_DIR / 'firebase_config.json'
firebase_config_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')

try:
    if firebase_config_json:
        # Prefer service account JSON from environment variable in production
        cred_info = json.loads(firebase_config_json)
        firebase_cred = credentials.Certificate(cred_info)
        if not firebase_admin._apps:
            firebase_app = firebase_admin.initialize_app(firebase_cred)
    elif firebase_cred_path.exists():
        # Fallback to local file (e.g. in development/sandbox)
        firebase_cred = credentials.Certificate(str(firebase_cred_path))
        if not firebase_admin._apps:
            firebase_app = firebase_admin.initialize_app(firebase_cred)
    else:
        logging.warning("Firebase config not provided; FCM notifications will be disabled.")
except Exception as e:
    logging.error(f"Failed to initialize Firebase Admin: {e}")

security = HTTPBearer()

app = FastAPI(title="Absensi Sholat API")
api_router = APIRouter(prefix="/api")


@api_router.get("/")
async def root_health_get():
    """Simple health-check endpoint for deployment probes (GET)."""
    return {"status": "ok"}


@api_router.post("/")
async def root_health_post():
    """Simple health-check endpoint for deployment probes (POST)."""
    return {"status": "ok"}

# ==================== MODELS ====================

class Admin(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    username: str
    nama: str
    password_hash: str
    role: str = "superadmin"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AdminCreate(BaseModel):
    username: str
    nama: str
    password: str

class AdminResponse(BaseModel):
    id: str
    username: str
    nama: str
    role: str = "superadmin"
    created_at: datetime


class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AdminResponse

# Asrama Models
class Asrama(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    gender: Literal["putra", "putri"]
    kapasitas: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AsramaCreate(BaseModel):
    nama: str
    gender: Literal["putra", "putri"]
    kapasitas: int

class AsramaUpdate(BaseModel):
    nama: Optional[str] = None
    gender: Optional[Literal["putra", "putri"]] = None
    kapasitas: Optional[int] = None

# REVISED: Santri Models - dengan data wali inline
class Santri(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    nis: str
    gender: Literal["putra", "putri"]
    asrama_id: str
    nfc_uid: Optional[str] = None
    # Data wali inline
    nama_wali: str
    nomor_hp_wali: str
    email_wali: Optional[str] = None
    qr_code: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SantriCreate(BaseModel):
    nama: str
    nis: str
    gender: Literal["putra", "putri"]
    asrama_id: str
    nama_wali: str
    nomor_hp_wali: str
    email_wali: Optional[str] = None
    nfc_uid: Optional[str] = None

class SantriUpdate(BaseModel):
    nama: Optional[str] = None
    nis: Optional[str] = None
    gender: Optional[Literal["putra", "putri"]] = None
    asrama_id: Optional[str] = None
    nama_wali: Optional[str] = None
    nomor_hp_wali: Optional[str] = None
    email_wali: Optional[str] = None
    nfc_uid: Optional[str] = None

class SantriResponse(BaseModel):
    id: str
    nama: str
    nis: str
    gender: str
    asrama_id: str
    nama_wali: str
    nomor_hp_wali: str
    email_wali: Optional[str]
    nfc_uid: Optional[str] = None
    created_at: datetime
    updated_at: datetime

# Wali Santri Models - AUTO GENERATED
class WaliSantri(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    nama: str
    username: str
    password_hash: str
    nomor_hp: str
    email: Optional[str] = None
    jumlah_anak: int = 0
    nama_anak: List[str] = []
    created_at: datetime
    updated_at: datetime

class WaliSantriUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None

class WaliSantriResponse(BaseModel):
    id: str
    nama: str
    username: str
    nomor_hp: str
    email: Optional[str]
    jumlah_anak: int
    nama_anak: List[str]
    created_at: datetime
    updated_at: datetime

# REVISED: Pengabsen Models - multi asrama
class Pengabsen(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str  # Changed from password_hash to kode_akses
    asrama_ids: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class PengabsenCreate(BaseModel):
    nama: str
    email_atau_hp: str
    username: str
    asrama_ids: List[str]

class PengabsenUpdate(BaseModel):
    nama: Optional[str] = None
    email_atau_hp: Optional[str] = None
    username: Optional[str] = None
    asrama_ids: Optional[List[str]] = None

class PengabsenResponse(BaseModel):
    id: str
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str  # Include kode_akses for Admin to see
    asrama_ids: List[str]
    created_at: datetime

class PengabsenLoginRequest(BaseModel):
    username: str
    kode_akses: str

# REVISED: Pembimbing Models - tambah kontak
class Pembimbing(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    username: str
    kode_akses: str  # Changed from password_hash to kode_akses (access code)
    email_atau_hp: str
    asrama_ids: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class PembimbingCreate(BaseModel):
    nama: str
    username: str
    email_atau_hp: str
    asrama_ids: List[str] = []

class PembimbingUpdate(BaseModel):
    nama: Optional[str] = None
    username: Optional[str] = None
    email_atau_hp: Optional[str] = None
    asrama_ids: Optional[List[str]] = None

class PembimbingResponse(BaseModel):
    id: str
    nama: str
    username: str
    kode_akses: str  # Include kode_akses in response for Admin to see
    email_atau_hp: str
    asrama_ids: List[str]
    created_at: datetime


# Pembimbing PWA Models
class PembimbingLoginRequest(BaseModel):
    username: str
    kode_akses: str

class PembimbingMeResponse(BaseModel):
    id: str
    nama: str
    username: str
    email_atau_hp: Optional[str] = ''
    asrama_ids: List[str] = []
    created_at: datetime

class PembimbingTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PembimbingMeResponse
class PengabsenMeResponse(BaseModel):
    id: str
    nama: str
    username: str
    email_atau_hp: Optional[str] = ''
    asrama_ids: List[str] = []
    created_at: datetime

class PengabsenTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PengabsenMeResponse



class PengabsenAliyahLoginRequest(BaseModel):
    username: str
    kode_akses: str


class PengabsenAliyahMeResponse(BaseModel):
    id: str
    nama: str
    username: str
    email_atau_hp: Optional[str] = ""
    kelas_ids: List[str] = []
    created_at: datetime


class PengabsenAliyahNFCRequest(BaseModel):
    nfc_uid: str
    jenis: Optional[str] = "pagi"
    tanggal: Optional[str] = None


class PengabsenPMQLoginRequest(BaseModel):
    username: str
    kode_akses: str


class PengabsenPMQMeResponse(BaseModel):
    id: str
    nama: str
    username: str
    email_atau_hp: Optional[str] = ""
    tingkatan_keys: List[str] = []
    kelompok_ids: List[str] = []
    created_at: datetime


class PengabsenPMQNFCRequest(BaseModel):
    nfc_uid: str
    sesi: Optional[str] = None
    tanggal: Optional[str] = None


class PengabsenPMQTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PengabsenPMQMeResponse



class PengabsenAliyahTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PengabsenAliyahMeResponse


class MonitoringAliyahLoginRequest(BaseModel):
    username: str
    kode_akses: str


class MonitoringAliyahMeResponse(BaseModel):
    id: str
    nama: str
    username: str
    email_atau_hp: Optional[str] = ""
    kelas_ids: List[str] = []
    created_at: datetime


class MonitoringAliyahTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: MonitoringAliyahMeResponse




# Absensi Models
class Absensi(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    santri_id: str
    waktu_sholat: Literal["subuh", "dzuhur", "ashar", "maghrib", "isya"]
    status: Literal["hadir", "alfa", "sakit", "izin", "haid", "istihadhoh", "masbuq"]
    tanggal: str
    waktu_absen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pengabsen_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AbsensiResponse(BaseModel):
    id: str
    santri_id: str
    waktu_sholat: str
    status: str
    tanggal: str
    waktu_absen: datetime
    pengabsen_id: Optional[str]
    created_at: datetime


class NFCAbsensiRequest(BaseModel):
    nfc_uid: str
    waktu_sholat: str
    status: Optional[str] = None
    tanggal: Optional[str] = None


class WhatsAppRekapItem(BaseModel):
    santri_id: str
    nama_santri: str
    nama_wali: str
    nomor_hp_wali: str
    asrama_id: str
    asrama_nama: str
    gender: str
    rekap: Dict[str, str]


class WhatsAppSendRequest(BaseModel):
    santri_id: str
    tanggal: str


class WhatsAppHistoryItem(BaseModel):
    id: str
    santri_id: str
    nama_santri: str
    nama_wali: str = "-"
    nomor_hp_wali: str = ""
    asrama_id: str
    asrama_nama: str
    gender: str
    tanggal: str
    sent_at: datetime
    admin_id: str
    admin_nama: str
    rekap: Dict[str, str] = {}


class WhatsAppResendRequest(BaseModel):
    history_id: str

# Waktu Sholat Models
class WaktuSholat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tanggal: str
    subuh: str
    dzuhur: str
    ashar: str
    maghrib: str
    isya: str
    lokasi: str = "Lampung Selatan"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class WaktuSholatResponse(BaseModel):
    id: str
    tanggal: str
    subuh: str
    dzuhur: str
    ashar: str
    maghrib: str
    isya: str
    lokasi: str

# ==================== MADRASAH DINIYAH MODELS ====================

# Kelas Models
class Kelas(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    jadwal: List[str] = []  # ["senin", "selasa", "rabu", "jumat", "sabtu", "minggu"]
    jam_mulai: str = "20:00"
    jam_selesai: str = "20:30"
    kapasitas: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class KelasCreate(BaseModel):
    nama: str
    jadwal: List[str]
    jam_mulai: str = "20:00"
    jam_selesai: str = "20:30"
    kapasitas: int

class KelasUpdate(BaseModel):
    nama: Optional[str] = None
    jadwal: Optional[List[str]] = None
    jam_mulai: Optional[str] = None
    jam_selesai: Optional[str] = None
    kapasitas: Optional[int] = None

class KelasResponse(BaseModel):
    id: str
    nama: str
    jadwal: List[str]
    jam_mulai: str
    jam_selesai: str
    kapasitas: int
    jumlah_siswa: int = 0
    created_at: datetime

# Siswa Madrasah Models
class SiswaMadrasah(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    nis: Optional[str] = None
    gender: Literal["putra", "putri"]
    kelas_id: Optional[str] = None
    santri_id: Optional[str] = None  # Link ke menu Santri (optional)
    qr_code: Optional[str] = None  # Only if santri_id is None
    nfc_uid: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SiswaMadrasahCreate(BaseModel):
    nama: str
    nis: Optional[str] = None
    gender: Literal["putra", "putri"]
    kelas_id: Optional[str] = None
    santri_id: Optional[str] = None
    nfc_uid: Optional[str] = None

class SiswaMadrasahUpdate(BaseModel):
    nama: Optional[str] = None
    nis: Optional[str] = None
    gender: Optional[Literal["putra", "putri"]] = None
    kelas_id: Optional[str] = None
    santri_id: Optional[str] = None
    nfc_uid: Optional[str] = None


# REMOVED DUPLICATE CLASS


# ==================== KELAS ALIYAH MODELS ====================

class KelasAliyah(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    tingkat: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class KelasAliyahCreate(BaseModel):
    nama: str
    tingkat: Optional[str] = None

class KelasAliyahUpdate(BaseModel):
    nama: Optional[str] = None
    tingkat: Optional[str] = None

class KelasAliyahResponse(BaseModel):
    id: str
    nama: str
    tingkat: Optional[str] = None
    created_at: datetime
    jumlah_siswa: int = 0


class SiswaMadrasahResponse(BaseModel):
    id: str
    nama: str
    nis: Optional[str]
    gender: str
    kelas_id: Optional[str]
    kelas_nama: Optional[str] = None
    santri_id: Optional[str]
    has_qr: bool = False
    nfc_uid: Optional[str] = None
    created_at: datetime
    updated_at: datetime

# Absensi Kelas Models
class AbsensiKelas(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    siswa_id: str
    kelas_id: str
    tanggal: str  # YYYY-MM-DD
    status: Literal["hadir", "alfa", "izin", "sakit", "telat"]  # a: alfa, i: izin, s: sakit, t: telat
# ==================== DAILY WHATSAPP REPORT MODELS ====================

class DailyWaliReportAnak(BaseModel):
    nama: str
    kelas: Optional[str] = None
    subuh: str
    dzuhur: str
    ashar: str
    maghrib: str
    isya: str

class DailyWaliReport(BaseModel):
    wali_nama: str
    wali_nomor: str
    tanggal: str
    anak: List[DailyWaliReportAnak]

class DailyWaliReportBatch(BaseModel):
    tanggal: str
    reports: List[DailyWaliReport]



# ==================== SISWA ALIYAH MODELS ====================

class SiswaAliyah(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    nis: Optional[str] = None
    gender: Literal["putra", "putri"]
    kelas_id: Optional[str] = None
    santri_id: Optional[str] = None
    wali_nama: Optional[str] = None
    wali_wa: Optional[str] = None
    qr_code: Optional[str] = None
    nfc_uid: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SiswaAliyahCreate(BaseModel):
    nama: str
    nis: Optional[str] = None
    gender: Literal["putra", "putri"]
    kelas_id: Optional[str] = None
    santri_id: Optional[str] = None
    wali_nama: Optional[str] = None
    wali_wa: Optional[str] = None
    nfc_uid: Optional[str] = None

class SiswaAliyahUpdate(BaseModel):
    nama: Optional[str] = None
    nis: Optional[str] = None
    gender: Optional[Literal["putra", "putri"]] = None
    kelas_id: Optional[str] = None
    santri_id: Optional[str] = None
    wali_nama: Optional[str] = None
    wali_wa: Optional[str] = None
    nfc_uid: Optional[str] = None

class SiswaAliyahResponse(BaseModel):
    id: str
    nama: str
    nis: Optional[str]
    gender: str
    kelas_id: Optional[str]
    kelas_nama: Optional[str] = None
    santri_id: Optional[str]
    wali_nama: Optional[str] = None
    wali_wa: Optional[str] = None
    nfc_uid: Optional[str] = None
    has_qr: bool = False

    waktu_absen: Optional[datetime] = None
    pengabsen_kelas_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AbsensiKelasCreate(BaseModel):
    siswa_id: str
    kelas_id: str
    tanggal: str
    status: Literal["hadir", "alfa", "izin", "sakit", "telat"]


class AbsensiKelasNFCRequest(BaseModel):
    nfc_uid: str
    status: Optional[Literal["hadir", "alfa", "izin", "sakit", "telat"]] = "hadir"
    tanggal: Optional[str] = None

class AbsensiKelasUpdate(BaseModel):
    status: Literal["hadir", "alfa", "izin", "sakit", "telat"]

class AbsensiKelasResponse(BaseModel):
    id: str
    siswa_id: str
    siswa_nama: str
    kelas_id: str
    kelas_nama: str
    tanggal: str
    status: str
    waktu_absen: Optional[datetime]
    created_at: datetime

# Pengabsen Kelas Models
class PengabsenKelas(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str
    kelas_ids: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class PengabsenKelasCreate(BaseModel):
    nama: str
    email_atau_hp: str
    username: str
    kelas_ids: List[str]

class PengabsenKelasUpdate(BaseModel):
    nama: Optional[str] = None
    email_atau_hp: Optional[str] = None
    username: Optional[str] = None
    kelas_ids: Optional[List[str]] = None

class PengabsenKelasResponse(BaseModel):
    id: str
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str
    kelas_ids: List[str]
    created_at: datetime

class PengabsenKelasLoginRequest(BaseModel):
    username: str
    kode_akses: str

class PengabsenKelasMeResponse(BaseModel):
    id: str
    nama: str
    username: str
    email_atau_hp: str
    kelas_ids: List[str]
    created_at: datetime

class PengabsenKelasTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PengabsenKelasMeResponse

# Pembimbing Kelas (Monitoring) Models
class PembimbingKelas(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    username: str
    kode_akses: str
    email_atau_hp: str
    kelas_ids: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class PembimbingKelasCreate(BaseModel):
    nama: str
    username: str
    email_atau_hp: str
    kelas_ids: List[str] = []

class PembimbingKelasUpdate(BaseModel):
    nama: Optional[str] = None
    username: Optional[str] = None
    email_atau_hp: Optional[str] = None
    kelas_ids: Optional[List[str]] = None

class PembimbingKelasResponse(BaseModel):
    id: str

# ==================== PENGABSEN & MONITORING ALIYAH MODELS ====================

class PengabsenAliyah(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str
    kelas_ids: List[str] = []  # refer ke kelas_aliyah.id
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PengabsenAliyahCreate(BaseModel):
    nama: str
    email_atau_hp: str
    username: str
    kelas_ids: List[str]


class PengabsenAliyahUpdate(BaseModel):
    nama: Optional[str] = None
    email_atau_hp: Optional[str] = None
    username: Optional[str] = None
    kelas_ids: Optional[List[str]] = None


class PengabsenAliyahResponse(BaseModel):
    id: str
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str
    kelas_ids: List[str]
    created_at: datetime


class MonitoringAliyah(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str
    kelas_ids: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MonitoringAliyahCreate(BaseModel):
    nama: str
    email_atau_hp: str
    username: str
    kelas_ids: List[str] = []




# Helper auth dependency for Pengabsen & Monitoring Aliyah (must be defined before endpoints)

async def get_current_pengabsen_aliyah(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        pengabsen_id: str = payload.get("sub")
        if pengabsen_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        pengabsen = await db.pengabsen_aliyah.find_one({"id": pengabsen_id}, {"_id": 0})
        if pengabsen is None:
            raise HTTPException(status_code=401, detail="Pengabsen Aliyah not found")

        return pengabsen
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")


async def get_current_monitoring_aliyah(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        monitoring_id: str = payload.get("sub")
        if monitoring_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        monitoring = await db.pembimbing_aliyah.find_one({"id": monitoring_id}, {"_id": 0})
        if monitoring is None:
            raise HTTPException(status_code=401, detail="Monitoring Aliyah not found")

        return monitoring
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")



async def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        admin_id: str = payload.get("sub")
        if admin_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        admin = await db.admins.find_one({"id": admin_id}, {"_id": 0})
        if admin is None:
            raise HTTPException(status_code=401, detail="Admin not found")

        # Pastikan field role selalu ada di objek admin yang dikembalikan
        if "role" not in admin:
            admin["role"] = payload.get("role", "superadmin")

        return admin
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

# ==================== PMQ MODELS ====================

PMQ_TINGKATAN = [
    {"key": "jet_tempur", "label": "Jet Tempur"},
    {"key": "persiapan", "label": "Persiapan"},
    {"key": "jazariyah", "label": "Jazariyah"},
    {"key": "al_quran", "label": "Al-Qur'an"},
]


class PMQKelompok(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tingkatan_key: str
    nama: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PMQKelompokCreate(BaseModel):
    tingkatan_key: str
    nama: str


class PMQKelompokUpdate(BaseModel):
    nama: Optional[str] = None


class PMQKelompokResponse(BaseModel):
    id: str
    tingkatan_key: str
    nama: str
    created_at: datetime


class SiswaPMQ(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    gender: Optional[Literal["putra", "putri"]] = None
    tingkatan_key: str
    kelompok_id: Optional[str] = None
    santri_id: Optional[str] = None
    qr_code: Optional[str] = None
    nfc_uid: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SiswaPMQCreate(BaseModel):
    nama: Optional[str] = None
    gender: Optional[Literal["putra", "putri"]] = None
    tingkatan_key: str
    kelompok_id: Optional[str] = None
    santri_id: Optional[str] = None
    nfc_uid: Optional[str] = None


class SiswaPMQUpdate(BaseModel):
    nama: Optional[str] = None
    gender: Optional[Literal["putra", "putri"]] = None
    tingkatan_key: Optional[str] = None
    kelompok_id: Optional[str] = None
    nfc_uid: Optional[str] = None


class SiswaPMQResponse(BaseModel):
    id: str
    nama: str
    gender: Optional[str] = None
    tingkatan_key: str
    tingkatan_label: str
    kelompok_id: Optional[str]
    kelompok_nama: Optional[str] = None
    santri_id: Optional[str]
    has_qr: bool = False
    qr_code: Optional[str] = None
    nfc_uid: Optional[str] = None
    created_at: datetime


class PengabsenPMQ(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str
    tingkatan_keys: List[str] = []
    kelompok_ids: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PengabsenPMQCreate(BaseModel):
    nama: str
    email_atau_hp: str
    username: str
    tingkatan_keys: List[str]
    kelompok_ids: List[str]


class PengabsenPMQUpdate(BaseModel):
    nama: Optional[str] = None
    email_atau_hp: Optional[str] = None
    username: Optional[str] = None
    tingkatan_keys: Optional[List[str]] = None
    kelompok_ids: Optional[List[str]] = None


class PengabsenPMQResponse(BaseModel):
    id: str
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str
    tingkatan_keys: List[str]
    kelompok_ids: List[str]
    created_at: datetime




class PMQSesi(BaseModel):
    key: str
    label: str
    start_time: str  # HH:MM
    end_time: str  # HH:MM
    active: bool = True


class PMQWaktuSettings(BaseModel):
    id: str = Field(default="pmq_waktu")
    sesi: List[PMQSesi] = [
        PMQSesi(key="pagi", label="Sesi Pagi", start_time="06:30", end_time="07:30", active=True),
        PMQSesi(key="malam", label="Sesi Malam", start_time="19:30", end_time="20:30", active=True),
    ]
    updated_at: Optional[str] = None

# ==================== PMQ ENDPOINTS ====================


class PMQAbsensi(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    siswa_id: str
    tanggal: str
    sesi: Literal["pagi", "malam"]
    status: Literal["hadir", "alfa", "sakit", "izin", "terlambat"]
    kelompok_id: Optional[str] = None
    pengabsen_id: Optional[str] = None
    waktu_absen: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PMQAbsensiRiwayatRow(BaseModel):
    id: Optional[str]
    siswa_id: str
    siswa_nama: str
    tingkatan_key: str
    tingkatan_label: str
    kelompok_id: Optional[str]
    kelompok_nama: Optional[str]
    tanggal: str
    sesi: str
    status: str
    waktu_absen: Optional[str]


class PMQAbsensiRiwayatResponse(BaseModel):
    summary: Dict[str, int]
    detail: List[PMQAbsensiRiwayatRow]


@api_router.get("/pmq/tingkatan")
async def get_pmq_tingkatan(_: dict = Depends(get_current_admin)):
    """Daftar tingkatan PMQ statis."""
    return PMQ_TINGKATAN


@api_router.get("/pmq/kelompok", response_model=List[PMQKelompokResponse])
async def get_pmq_kelompok(
    tingkatan_key: Optional[str] = None,
    _: dict = Depends(get_current_admin),
):
    query: dict = {}
    if tingkatan_key:
        query["tingkatan_key"] = tingkatan_key
    docs = await db.pmq_kelompok.find(query, {"_id": 0}).to_list(1000)
    return [PMQKelompokResponse(**doc) for doc in docs]


@api_router.post("/pmq/kelompok", response_model=PMQKelompokResponse)
async def create_pmq_kelompok(payload: PMQKelompokCreate, _: dict = Depends(get_current_admin)):
    if payload.tingkatan_key not in [t["key"] for t in PMQ_TINGKATAN]:
        raise HTTPException(status_code=400, detail="Tingkatan PMQ tidak valid")
    kelompok = PMQKelompok(**payload.model_dump())
    doc = kelompok.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    await db.pmq_kelompok.insert_one(doc)
    return PMQKelompokResponse(**kelompok.model_dump())


@api_router.put("/pmq/kelompok/{kelompok_id}", response_model=PMQKelompokResponse)
async def update_pmq_kelompok(kelompok_id: str, payload: PMQKelompokUpdate, _: dict = Depends(get_current_admin)):
    kelompok = await db.pmq_kelompok.find_one({"id": kelompok_id}, {"_id": 0})
    if not kelompok:
        raise HTTPException(status_code=404, detail="Kelompok PMQ tidak ditemukan")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "nama" in update_data:
        nama = update_data["nama"].strip()
        if not nama:
            raise HTTPException(status_code=400, detail="Nama kelompok wajib diisi")
        update_data["nama"] = nama

    if not update_data:
        raise HTTPException(status_code=400, detail="Tidak ada data untuk diperbarui")

    await db.pmq_kelompok.update_one({"id": kelompok_id}, {"$set": update_data})
    kelompok.update(update_data)
    created_at_val = kelompok.get("created_at")
    if isinstance(created_at_val, str):
        created_at_val = datetime.fromisoformat(created_at_val)
    kelompok["created_at"] = created_at_val or datetime.now(timezone.utc)
    return PMQKelompokResponse(**kelompok)


@api_router.delete("/pmq/kelompok/{kelompok_id}")
async def delete_pmq_kelompok(kelompok_id: str, _: dict = Depends(get_current_admin)):
    result = await db.pmq_kelompok.delete_one({"id": kelompok_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kelompok PMQ tidak ditemukan")
    # Optionally, siswa_pmq yang refer ke kelompok ini bisa dibiarkan dengan kelompok_id None
    await db.siswa_pmq.update_many({"kelompok_id": kelompok_id}, {"$set": {"kelompok_id": None}})
    return {"message": "Kelompok PMQ berhasil dihapus"}


@api_router.get("/pmq/siswa", response_model=List[SiswaPMQResponse])
async def get_siswa_pmq(
    search: Optional[str] = None,
    tingkatan_key: Optional[str] = None,
    kelompok_id: Optional[str] = None,
    gender: Optional[str] = None,
    _: dict = Depends(get_current_admin),
):
    query: dict = {}
    if tingkatan_key:
        query["tingkatan_key"] = tingkatan_key
    if kelompok_id:
        query["kelompok_id"] = kelompok_id
    if gender in ["putra", "putri"]:
        query["gender"] = gender

    if search:
        query["$or"] = [
            {"nama": {"$regex": search, "$options": "i"}},
            {"nfc_uid": {"$regex": search, "$options": "i"}},
        ]

    docs = await db.siswa_pmq.find(query, {"_id": 0}).to_list(5000)

    # Join kelompok & tingkatan label
    kelompok_docs = await db.pmq_kelompok.find({}, {"_id": 0}).to_list(1000)
    kelompok_map = {k["id"]: k for k in kelompok_docs}
    tingkatan_map = {t["key"]: t["label"] for t in PMQ_TINGKATAN}

    results: List[SiswaPMQResponse] = []
    for doc in docs:
        if search:
            q = search.lower()
            if q not in (doc.get("nama", "").lower()):
                continue
        kelompok = kelompok_map.get(doc.get("kelompok_id"))
        results.append(
            SiswaPMQResponse(
                id=doc["id"],
                nama=doc["nama"],
                tingkatan_key=doc["tingkatan_key"],
                tingkatan_label=tingkatan_map.get(doc["tingkatan_key"], doc["tingkatan_key"]),
                kelompok_id=doc.get("kelompok_id"),
                kelompok_nama=kelompok.get("nama") if kelompok else None,
                santri_id=doc.get("santri_id"),
                has_qr=bool(doc.get("qr_code")),
                qr_code=doc.get("qr_code"),
                nfc_uid=doc.get("nfc_uid"),
                gender=doc.get("gender"),
                created_at=doc.get("created_at", datetime.now(timezone.utc)),
            )
        )

    # Urutkan: tingkatan, kelompok, nama
    results.sort(key=lambda x: (x.tingkatan_key, x.kelompok_nama or "", x.nama))
    return results


@api_router.get("/pmq/santri-available")
async def get_santri_available_for_pmq(
    search: Optional[str] = None,
    _: dict = Depends(get_current_admin),
):
    siswa_docs = await db.siswa_pmq.find({}, {"_id": 0, "santri_id": 1}).to_list(5000)
    used_santri_ids = {d["santri_id"] for d in siswa_docs if d.get("santri_id")}

    santri_query: dict = {}
    if search:
        santri_query["nama"] = {"$regex": search, "$options": "i"}

    santri_docs = await db.santri.find(santri_query, {"_id": 0}).to_list(5000)
    available = [s for s in santri_docs if s["id"] not in used_santri_ids]
    return available


@api_router.post("/pmq/siswa", response_model=SiswaPMQResponse)
async def create_siswa_pmq(payload: SiswaPMQCreate, _: dict = Depends(get_current_admin)):
    if payload.tingkatan_key not in [t["key"] for t in PMQ_TINGKATAN]:
        raise HTTPException(status_code=400, detail="Tingkatan PMQ tidak valid")

    data = payload.model_dump()

    if data.get("nfc_uid"):
        nfc_uid = data["nfc_uid"].strip()
        if nfc_uid:
            existing_nfc = await db.siswa_pmq.find_one({"nfc_uid": nfc_uid})
            if existing_nfc:
                raise HTTPException(status_code=400, detail="NFC UID sudah digunakan")
            data["nfc_uid"] = nfc_uid
        else:
            data["nfc_uid"] = None

    # Jika link dari santri, ambil nama dari santri dan pastikan belum terdaftar
    if data.get("santri_id"):
        existing = await db.siswa_pmq.find_one({"santri_id": data["santri_id"]}, {"_id": 0})
        if existing:
            raise HTTPException(status_code=400, detail="Santri sudah terdaftar di PMQ")

        santri = await db.santri.find_one({"id": data["santri_id"]}, {"_id": 0})
        if not santri:
            raise HTTPException(status_code=404, detail="Santri tidak ditemukan")
        data["nama"] = santri["nama"]
        # Opsional: gunakan QR santri jika ada
        if santri.get("qr_code"):
            data["qr_code"] = santri["qr_code"]
        # Bawa juga gender santri jika ada
        if santri.get("gender"):
            data["gender"] = santri["gender"]
        # NFC ikut santri jika ada
        if santri.get("nfc_uid"):
            data["nfc_uid"] = santri["nfc_uid"].strip()
    else:
        if not data.get("nama"):
            raise HTTPException(status_code=400, detail="Nama wajib diisi untuk siswa PMQ baru")

    siswa = SiswaPMQ(**data)

    if siswa.nfc_uid:
        siswa.nfc_uid = siswa.nfc_uid.strip()

    # Generate QR baru untuk siswa manual (tanpa santri_id) bila belum ada
    if not siswa.santri_id and not siswa.qr_code:
        siswa.qr_code = generate_qr_code({"type": "siswa_pmq", "id": siswa.id})

    doc = siswa.model_dump()
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    await db.siswa_pmq.insert_one(doc)

    # Build response
    tingkatan_map = {t["key"]: t["label"] for t in PMQ_TINGKATAN}
    kelompok = None
    if siswa.kelompok_id:
        kelompok = await db.pmq_kelompok.find_one({"id": siswa.kelompok_id}, {"_id": 0})

    return SiswaPMQResponse(
        id=siswa.id,
        nama=siswa.nama,
        gender=siswa.gender,
        tingkatan_key=siswa.tingkatan_key,
        tingkatan_label=tingkatan_map.get(siswa.tingkatan_key, siswa.tingkatan_key),
        kelompok_id=siswa.kelompok_id,
        kelompok_nama=kelompok.get("nama") if kelompok else None,
        santri_id=siswa.santri_id,
        has_qr=bool(siswa.qr_code),
        qr_code=siswa.qr_code,
        created_at=siswa.created_at,
    )


@api_router.put("/pmq/siswa/{siswa_id}", response_model=SiswaPMQResponse)
async def update_siswa_pmq(siswa_id: str, payload: SiswaPMQUpdate, _: dict = Depends(get_current_admin)):
    siswa = await db.siswa_pmq.find_one({"id": siswa_id}, {"_id": 0})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa PMQ tidak ditemukan")

    data = payload.model_dump(exclude_unset=True)

    if "nfc_uid" in data:
        nfc_uid = (data.get("nfc_uid") or "").strip()
        if nfc_uid:
            existing_nfc = await db.siswa_pmq.find_one({"nfc_uid": nfc_uid, "id": {"$ne": siswa_id}})
            if existing_nfc:
                raise HTTPException(status_code=400, detail="NFC UID sudah digunakan")
            data["nfc_uid"] = nfc_uid
        else:
            data["nfc_uid"] = None

    # Jika link ke santri, nama tidak boleh diedit
    if siswa.get("santri_id") and "nama" in data:
        data.pop("nama")

    if "tingkatan_key" in data and data["tingkatan_key"] not in [t["key"] for t in PMQ_TINGKATAN]:
        raise HTTPException(status_code=400, detail="Tingkatan PMQ tidak valid")

    if data:
        await db.siswa_pmq.update_one({"id": siswa_id}, {"$set": data})
        siswa.update(data)

    tingkatan_map = {t["key"]: t["label"] for t in PMQ_TINGKATAN}
    kelompok = None
    if siswa.get("kelompok_id"):
        kelompok = await db.pmq_kelompok.find_one({"id": siswa["kelompok_id"]}, {"_id": 0})

    created_at_val = siswa.get("created_at")
    if isinstance(created_at_val, str):
        created_at_val = datetime.fromisoformat(created_at_val)

    return SiswaPMQResponse(
        id=siswa["id"],
        nama=siswa["nama"],
        gender=siswa.get("gender"),
        tingkatan_key=siswa["tingkatan_key"],
        tingkatan_label=tingkatan_map.get(siswa["tingkatan_key"], siswa["tingkatan_key"]),
        kelompok_id=siswa.get("kelompok_id"),
        kelompok_nama=kelompok.get("nama") if kelompok else None,
        santri_id=siswa.get("santri_id"),
        has_qr=bool(siswa.get("qr_code")),
        qr_code=siswa.get("qr_code"),
        nfc_uid=siswa.get("nfc_uid"),
        created_at=created_at_val or datetime.now(timezone.utc),
    )


@api_router.delete("/pmq/siswa/{siswa_id}")
async def delete_siswa_pmq(siswa_id: str, _: dict = Depends(get_current_admin)):
    result = await db.siswa_pmq.delete_one({"id": siswa_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Siswa PMQ tidak ditemukan")
    return {"message": "Siswa PMQ berhasil dihapus"}


@api_router.get("/pmq/pengabsen", response_model=List[PengabsenPMQResponse])
async def get_pengabsen_pmq(_: dict = Depends(get_current_admin)):
    docs = await db.pengabsen_pmq.find({}, {"_id": 0}).to_list(1000)
    results: List[PengabsenPMQResponse] = []
    for doc in docs:
        created_at_val = doc.get("created_at")
        if isinstance(created_at_val, str):
            created_at_val = datetime.fromisoformat(created_at_val)
        results.append(
            PengabsenPMQResponse(
                id=doc["id"],
                nama=doc["nama"],
                email_atau_hp=doc.get("email_atau_hp", ""),
                username=doc["username"],
                kode_akses=doc.get("kode_akses", ""),
                tingkatan_keys=doc.get("tingkatan_keys", []),
                kelompok_ids=doc.get("kelompok_ids", []),
                created_at=created_at_val or datetime.now(timezone.utc),
            )
        )
    return results


@api_router.post("/pmq/pengabsen", response_model=PengabsenPMQResponse)
async def create_pengabsen_pmq(payload: PengabsenPMQCreate, _: dict = Depends(get_current_admin)):
    # Validasi tingkatan
    valid_keys = {t["key"] for t in PMQ_TINGKATAN}
    if not set(payload.tingkatan_keys).issubset(valid_keys):
        raise HTTPException(status_code=400, detail="Tingkatan PMQ tidak valid")

    existing = await db.pengabsen_pmq.find_one({"username": payload.username}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Username sudah digunakan")

    data = payload.model_dump()
    data["kode_akses"] = generate_kode_akses()
    pengabsen = PengabsenPMQ(**data)
    doc = pengabsen.model_dump()
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    await db.pengabsen_pmq.insert_one(doc)

    return PengabsenPMQResponse(**pengabsen.model_dump())


@api_router.put("/pmq/pengabsen/{pengabsen_id}", response_model=PengabsenPMQResponse)
async def update_pengabsen_pmq(pengabsen_id: str, payload: PengabsenPMQUpdate, _: dict = Depends(get_current_admin)):
    pengabsen = await db.pengabsen_pmq.find_one({"id": pengabsen_id}, {"_id": 0})
    if not pengabsen:
        raise HTTPException(status_code=404, detail="Pengabsen PMQ tidak ditemukan")

    data = payload.model_dump(exclude_unset=True)

    if "tingkatan_keys" in data:
        valid_keys = {t["key"] for t in PMQ_TINGKATAN}
        if not set(data["tingkatan_keys"]).issubset(valid_keys):
            raise HTTPException(status_code=400, detail="Tingkatan PMQ tidak valid")

    if data:
        await db.pengabsen_pmq.update_one({"id": pengabsen_id}, {"$set": data})
        pengabsen.update(data)

    created_at_val = pengabsen.get("created_at")
    if isinstance(created_at_val, str):
        created_at_val = datetime.fromisoformat(created_at_val)

    return PengabsenPMQResponse(
        id=pengabsen["id"],
        nama=pengabsen["nama"],
        email_atau_hp=pengabsen.get("email_atau_hp", ""),
        username=pengabsen["username"],
        kode_akses=pengabsen.get("kode_akses", ""),
        tingkatan_keys=pengabsen.get("tingkatan_keys", []),
        kelompok_ids=pengabsen.get("kelompok_ids", []),
        created_at=created_at_val or datetime.now(timezone.utc),
    )


@api_router.post("/pmq/pengabsen/{pengabsen_id}/regenerate-kode-akses", response_model=PengabsenPMQResponse)
async def regenerate_kode_akses_pmq(pengabsen_id: str, _: dict = Depends(get_current_admin)):
    pengabsen = await db.pengabsen_pmq.find_one({"id": pengabsen_id}, {"_id": 0})
    if not pengabsen:
        raise HTTPException(status_code=404, detail="Pengabsen PMQ tidak ditemukan")

    new_kode = generate_kode_akses()
    await db.pengabsen_pmq.update_one({"id": pengabsen_id}, {"$set": {"kode_akses": new_kode}})
    pengabsen["kode_akses"] = new_kode

    created_at_val = pengabsen.get("created_at")
    if isinstance(created_at_val, str):
        created_at_val = datetime.fromisoformat(created_at_val)

    return PengabsenPMQResponse(
        id=pengabsen["id"],
        nama=pengabsen["nama"],
        email_atau_hp=pengabsen.get("email_atau_hp", ""),
        username=pengabsen["username"],
        kode_akses=pengabsen.get("kode_akses", ""),
        tingkatan_keys=pengabsen.get("tingkatan_keys", []),
        kelompok_ids=pengabsen.get("kelompok_ids", []),
        created_at=created_at_val or datetime.now(timezone.utc),
    )


@api_router.get("/pmq/absensi/riwayat", response_model=PMQAbsensiRiwayatResponse)
async def get_pmq_absensi_riwayat(
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    tingkatan_key: Optional[str] = None,
    kelompok_id: Optional[str] = None,
    sesi: Optional[str] = None,
    _: dict = Depends(get_current_admin),
):
    """Riwayat absensi PMQ untuk admin.

    Mengembalikan summary per status dan detail list absensi per siswa.
    """
    if not tanggal_end:
        tanggal_end = tanggal_start

    query: Dict[str, Any] = {
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end},
    }
    if tingkatan_key:
        query["tingkatan_key"] = tingkatan_key
    if kelompok_id:
        query["kelompok_id"] = kelompok_id
    if sesi in ["pagi", "malam"]:
        query["sesi"] = sesi

    # Ambil data absensi PMQ mentah
    raw_list = await db.absensi_pmq.find(query, {"_id": 0}).to_list(10000)

    if not raw_list:
        return PMQAbsensiRiwayatResponse(
            summary={"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "terlambat": 0},
            detail=[],
        )

    # Ambil siswa & kelompok untuk enrichment
    siswa_ids = list({a["siswa_id"] for a in raw_list if a.get("siswa_id")})
    siswa_list = await db.siswa_pmq.find({"id": {"$in": siswa_ids}}, {"_id": 0}).to_list(5000)
    siswa_map = {s["id"]: s for s in siswa_list}

    kelompok_ids = list({a.get("kelompok_id") for a in raw_list if a.get("kelompok_id")})
    kelompok_list = await db.pmq_kelompok.find({"id": {"$in": kelompok_ids}}, {"_id": 0}).to_list(1000)
    kelompok_map = {k["id"]: k for k in kelompok_list}

    tingkatan_map = {t["key"]: t["label"] for t in PMQ_TINGKATAN}

    summary: Dict[str, int] = {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "terlambat": 0}
    detail: List[PMQAbsensiRiwayatRow] = []

    for a in raw_list:
        siswa = siswa_map.get(a["siswa_id"])
        if not siswa:
            continue

        status = a.get("status") or ""
        if status in summary:
            summary[status] += 1

        k_id = a.get("kelompok_id")
        kelompok = kelompok_map.get(k_id)

        row = PMQAbsensiRiwayatRow(
            id=a.get("id"),
            siswa_id=a["siswa_id"],
            siswa_nama=siswa.get("nama", "-"),
            tingkatan_key=siswa.get("tingkatan_key", ""),
            tingkatan_label=tingkatan_map.get(siswa.get("tingkatan_key", ""), siswa.get("tingkatan_key", "")),
            kelompok_id=k_id,
            kelompok_nama=kelompok.get("nama") if kelompok else None,
            tanggal=a.get("tanggal", ""),
            sesi=a.get("sesi", ""),
            status=status,
            waktu_absen=a.get("waktu_absen"),
        )
        detail.append(row)

    # Urutkan berdasarkan tanggal, tingkatan, kelompok, nama
    detail.sort(key=lambda r: (r.tanggal, r.tingkatan_key, r.kelompok_nama or "", r.siswa_nama))

    return PMQAbsensiRiwayatResponse(summary=summary, detail=detail)


@api_router.delete("/pmq/pengabsen/{pengabsen_id}")
async def delete_pengabsen_pmq(pengabsen_id: str, _: dict = Depends(get_current_admin)):
    result = await db.pengabsen_pmq.delete_one({"id": pengabsen_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Pengabsen PMQ tidak ditemukan")
    return {"message": "Pengabsen PMQ berhasil dihapus"}






# ==================== ABSENSI ALIYAH PENGABSEN PWA ENDPOINTS ====================

@api_router.get("/aliyah/pengabsen/absensi-hari-ini")
async def get_aliyah_pengabsen_absensi_hari_ini(
    jenis: Literal["pagi", "dzuhur"],
    tanggal: Optional[str] = None,
    current_pengabsen: dict = Depends(get_current_pengabsen_aliyah),
):
    if not tanggal:
        tanggal = get_today_local_iso()

    kelas_ids = current_pengabsen.get("kelas_ids", []) or []
    if not kelas_ids:
        return {"tanggal": tanggal, "jenis": jenis, "data": []}

    siswa_list = await db.siswa_aliyah.find({"kelas_id": {"$in": kelas_ids}}, {"_id": 0}).to_list(5000)
    siswa_by_id = {s["id"]: s for s in siswa_list}

    absensi_list = await db.absensi_aliyah.find(
        {"tanggal": tanggal, "jenis": jenis, "siswa_id": {"$in": list(siswa_by_id.keys())}},
        {"_id": 0},
    ).to_list(10000)

    status_map = {a["siswa_id"]: a["status"] for a in absensi_list}

    kelas_docs = await db.kelas_aliyah.find({"id": {"$in": kelas_ids}}, {"_id": 0}).to_list(1000)
    kelas_map = {k["id"]: k["nama"] for k in kelas_docs}

    data = []
    for siswa in siswa_list:
        data.append(
            {
                "siswa_id": siswa["id"],
                "nama": siswa["nama"],
                "nis": siswa.get("nis"),
                "kelas_id": siswa.get("kelas_id"),
                "kelas_nama": kelas_map.get(siswa.get("kelas_id"), "-"),
                "gender": siswa.get("gender"),
                "status": status_map.get(siswa["id"]),
            }
        )

    data.sort(key=lambda x: (x["kelas_nama"], x["nama"]))

    return {"tanggal": tanggal, "jenis": jenis, "data": data}


class AliyahAbsensiUpsertRequest(BaseModel):
    siswa_id: str
    kelas_id: str
    tanggal: str
    jenis: Literal["pagi", "dzuhur"]
    status: Optional[Literal["hadir", "alfa", "sakit", "izin", "dispensasi", "bolos"]] = None


@api_router.post("/aliyah/pengabsen/absensi")
async def upsert_aliyah_absensi(
    payload: AliyahAbsensiUpsertRequest,
    current_pengabsen: dict = Depends(get_current_pengabsen_aliyah),
):
    # Pastikan siswa ada dan termasuk kelas yang bisa diakses pengabsen
    siswa = await db.siswa_aliyah.find_one({"id": payload.siswa_id}, {"_id": 0})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")

    kelas_ids = current_pengabsen.get("kelas_ids", []) or []
    if payload.kelas_id not in kelas_ids:
        raise HTTPException(status_code=403, detail="Tidak boleh mengabsen kelas ini")

    if payload.status is None:
        # Hapus absensi jika status kosong
        await db.absensi_aliyah.delete_one(
            {
                "siswa_id": payload.siswa_id,
                "tanggal": payload.tanggal,
                "jenis": payload.jenis,
            }
        )
        return {"message": "Absensi dihapus"}

    existing = await db.absensi_aliyah.find_one(
        {
            "siswa_id": payload.siswa_id,
            "tanggal": payload.tanggal,
            "jenis": payload.jenis,
        },
        {"_id": 0},
    )

    now = datetime.now(timezone.utc)

    if existing:
        await db.absensi_aliyah.update_one(
            {"id": existing["id"]},
            {"$set": {"status": payload.status, "kelas_id": payload.kelas_id, "waktu_absen": now}},
        )
    else:
        absensi = AbsensiAliyah(
            siswa_id=payload.siswa_id,
            kelas_id=payload.kelas_id,
            tanggal=payload.tanggal,
            jenis=payload.jenis,
            status=payload.status,
        )
        doc = absensi.model_dump()
        doc["created_at"] = doc["created_at"].isoformat()
        doc["waktu_absen"] = now.isoformat()
        await db.absensi_aliyah.insert_one(doc)

    return {"message": "Absensi disimpan"}


class AliyahAbsensiScanPayload(BaseModel):
    id: str
    type: Optional[str] = None


@api_router.post("/aliyah/pengabsen/absensi/scan")
async def scan_aliyah_absensi(
    payload: AliyahAbsensiScanPayload,
    jenis: Literal["pagi", "dzuhur"],
    current_pengabsen: dict = Depends(get_current_pengabsen_aliyah),
):
    # Batasi scan absensi pagi berdasarkan pengaturan global
    if jenis == "pagi":
        settings = await db.settings.find_one({"id": "aliyah_absensi_pagi"}, {"_id": 0})
        if not settings:
            settings = AliyahAbsensiPagiSettings().model_dump()

        start_time = settings.get("start_time", "06:30")
        end_time = settings.get("end_time", "07:15")

        try:
            # Konversi ke menit
            start_h, start_m = [int(x) for x in start_time.split(":")]
            end_h, end_m = [int(x) for x in end_time.split(":")]
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            now_utc = datetime.now(timezone.utc)
            # Konversi ke WIB (UTC+7)
            now_wib = now_utc + timedelta(hours=7)
            current_minutes = now_wib.hour * 60 + now_wib.minute

            if current_minutes < start_minutes or current_minutes > end_minutes:
                raise HTTPException(
                    status_code=400,
                    detail=f"Scan Kehadiran Pagi hanya diizinkan antara {start_time} - {end_time} WIB",
                )
        except ValueError:
            # Jika format jam salah di database, jangan blokir scan
            pass

    siswa: Optional[Dict[str, Any]] = None

    if payload.type == "siswa_aliyah":
        siswa = await db.siswa_aliyah.find_one({"id": payload.id}, {"_id": 0})
    else:
        # Asumsikan QR dari santri, cari siswa_aliyah yang linked dengan santri_id
        santri = await db.santri.find_one({"id": payload.id}, {"_id": 0})
        if santri:
            siswa = await db.siswa_aliyah.find_one({"santri_id": santri["id"]}, {"_id": 0})

    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa Aliyah tidak ditemukan untuk QR ini")

    kelas_ids = current_pengabsen.get("kelas_ids", []) or []
    if siswa.get("kelas_id") not in kelas_ids:
        raise HTTPException(status_code=403, detail="Siswa ini bukan dari kelas yang Anda pegang")

    tanggal = get_today_local_iso()

    existing = await db.absensi_aliyah.find_one(
        {
            "siswa_id": siswa["id"],
            "tanggal": tanggal,
            "jenis": jenis,
        },
        {"_id": 0},
    )

    now = datetime.now(timezone.utc)

    if existing:
        await db.absensi_aliyah.update_one(
            {"id": existing["id"]},
            {"$set": {"status": "hadir", "kelas_id": siswa.get("kelas_id"), "waktu_absen": now.isoformat()}},
        )
    else:
        absensi = AbsensiAliyah(
            siswa_id=siswa["id"],
            kelas_id=siswa.get("kelas_id"),
            tanggal=tanggal,
            jenis=jenis,
            status="hadir",
        )
        doc = absensi.model_dump()
        doc["created_at"] = doc["created_at"].isoformat()
        doc["waktu_absen"] = now.isoformat()
        await db.absensi_aliyah.insert_one(doc)

# ==================== AUTH PENGABSEN PMQ (PWA) ====================


async def get_current_pengabsen_pmq(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        pengabsen_id: str = payload.get("sub")
        if pengabsen_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        pengabsen = await db.pengabsen_pmq.find_one({"id": pengabsen_id}, {"_id": 0})
        if pengabsen is None:
            raise HTTPException(status_code=401, detail="Pengabsen PMQ not found")

        return pengabsen
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")




@api_router.post("/pmq/pengabsen/absensi/nfc")
async def pengabsen_pmq_absensi_nfc(
    payload: PengabsenPMQNFCRequest,
    sesi: str,
    tanggal: Optional[str] = None,
    current_pengabsen: dict = Depends(get_current_pengabsen_pmq),
):
    nfc_uid = payload.nfc_uid.strip() if payload.nfc_uid else ""
    if not nfc_uid:
        raise HTTPException(status_code=400, detail="NFC UID wajib diisi")

    # Validasi sesi mengikuti konfigurasi PMQWaktuSettings (misalnya: pagi, malam)
    if sesi not in ["pagi", "malam"]:
        raise HTTPException(status_code=400, detail="Sesi tidak valid")

    siswa = await db.siswa_pmq.find_one({"nfc_uid": nfc_uid}, {"_id": 0})
    if not siswa:
        # Fallback: cari santri yang memiliki NFC ini lalu temukan siswa PMQ yang link ke santri tersebut
        santri = await db.santri.find_one({"nfc_uid": nfc_uid}, {"_id": 0})
        if not santri:
            raise HTTPException(status_code=404, detail="Kartu NFC belum terdaftar")
        siswa = await db.siswa_pmq.find_one({"santri_id": santri["id"]}, {"_id": 0})
        if not siswa:
            raise HTTPException(status_code=404, detail="Kartu NFC belum terdaftar")

    if siswa.get("tingkatan_key") not in current_pengabsen.get("tingkatan_keys", []):
        raise HTTPException(status_code=403, detail="Siswa bukan tingkatan yang Anda kelola")
    if siswa.get("kelompok_id") not in current_pengabsen.get("kelompok_ids", []):
        raise HTTPException(status_code=403, detail="Siswa bukan kelompok yang Anda kelola")

    tanggal_final = tanggal or get_today_local_iso()
    existing = await db.absensi_pmq.find_one({
        "siswa_id": siswa["id"],
        "tanggal": tanggal_final,
        "sesi": sesi,
    }, {"_id": 0})

    now = datetime.now(timezone.utc)
    if existing:
        await db.absensi_pmq.update_one(
            {"id": existing["id"]},
            {"$set": {"status": "hadir", "waktu_absen": now.isoformat(), "pengabsen_id": current_pengabsen["id"]}},
        )
    else:
        absensi = PMQAbsensi(
            siswa_id=siswa["id"],
            tanggal=tanggal_final,
            sesi=sesi,
            status="hadir",
            kelompok_id=siswa.get("kelompok_id"),
            pengabsen_id=current_pengabsen["id"],
            waktu_absen=now.isoformat(),
        )
        doc = absensi.model_dump()
        doc["created_at"] = doc["created_at"].isoformat()
        await db.absensi_pmq.insert_one(doc)

    return {"message": "Absensi NFC berhasil dicatat", "siswa_nama": siswa.get("nama")}


@api_router.post("/aliyah/pengabsen/absensi/nfc")
async def pengabsen_aliyah_absensi_nfc(
    payload: PengabsenAliyahNFCRequest,
    current_pengabsen: dict = Depends(get_current_pengabsen_aliyah)
):
    nfc_uid = payload.nfc_uid.strip() if payload.nfc_uid else ""
    if not nfc_uid:
        raise HTTPException(status_code=400, detail="NFC UID wajib diisi")

    jenis = payload.jenis if payload.jenis in ["pagi", "dzuhur"] else "pagi"
    tanggal = payload.tanggal or get_today_local_iso()

    siswa = await db.siswa_aliyah.find_one({"nfc_uid": nfc_uid}, {"_id": 0})
    if not siswa:
        # Fallback: cari santri yang memiliki NFC ini lalu temukan siswa Aliyah yang link ke santri tersebut
        santri = await db.santri.find_one({"nfc_uid": nfc_uid}, {"_id": 0})
        if not santri:
            raise HTTPException(status_code=404, detail="Kartu NFC belum terdaftar")
        siswa = await db.siswa_aliyah.find_one({"santri_id": santri["id"]}, {"_id": 0})
        if not siswa:
            raise HTTPException(status_code=404, detail="Kartu NFC belum terdaftar")

    kelas_ids = current_pengabsen.get("kelas_ids", []) or []
    if siswa.get("kelas_id") not in kelas_ids:
        raise HTTPException(status_code=403, detail="Siswa ini bukan dari kelas yang Anda pegang")

    existing = await db.absensi_aliyah.find_one(
        {"siswa_id": siswa["id"], "tanggal": tanggal, "jenis": jenis},
        {"_id": 0},
    )

    now = datetime.now(timezone.utc)
    if existing:
        await db.absensi_aliyah.update_one(
            {"id": existing["id"]},
            {"$set": {"status": "hadir", "kelas_id": siswa.get("kelas_id"), "waktu_absen": now.isoformat()}},
        )
    else:
        absensi = AbsensiAliyah(
            siswa_id=siswa["id"],
            kelas_id=siswa.get("kelas_id"),
            tanggal=tanggal,
            jenis=jenis,
            status="hadir",
        )
        doc = absensi.model_dump()
        doc["created_at"] = doc["created_at"].isoformat()
        doc["waktu_absen"] = now.isoformat()
        await db.absensi_aliyah.insert_one(doc)

    return {"message": "Absensi NFC berhasil dicatat", "siswa_nama": siswa.get("nama")}


class MonitoringAliyahUpdate(BaseModel):
    nama: Optional[str] = None
    email_atau_hp: Optional[str] = None
    username: Optional[str] = None
    kelas_ids: Optional[List[str]] = None


class MonitoringAliyahResponse(BaseModel):
    id: str
    nama: str
    email_atau_hp: str
    username: str
    kode_akses: str
    kelas_ids: List[str]
    created_at: datetime


class AbsensiAliyah(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    siswa_id: str
    kelas_id: str
    tanggal: str  # YYYY-MM-DD
    jenis: Literal["pagi", "dzuhur"]
    status: Literal["hadir", "alfa", "sakit", "izin", "dispensasi", "bolos"]
    waktu_absen: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AbsensiAliyahResponse(BaseModel):
    id: str
    siswa_id: str
    siswa_nama: str
    kelas_id: str
    kelas_nama: str
    tanggal: str
    status: str
    gender: Optional[str] = None
    waktu_absen: Optional[datetime] = None


    nama: str
    username: str
    kode_akses: str
    email_atau_hp: str
    kelas_ids: List[str]
    created_at: datetime

class PembimbingKelasLoginRequest(BaseModel):
    username: str
    kode_akses: str

class PembimbingKelasMeResponse(BaseModel):
    id: str
    nama: str
    username: str
    email_atau_hp: str
    kelas_ids: List[str]
    created_at: datetime

class PembimbingKelasTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PembimbingKelasMeResponse

# ==================== UTILITY FUNCTIONS ====================

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def send_wali_push_notification(wali: dict, title: str, body: str):
    """Send FCM push notification to all tokens of a wali (if configured)."""
    try:
        tokens: list[str] = wali.get("fcm_tokens", []) or []
        if not tokens:
            return

        # Build individual messages for each token (firebase-admin 7.x uses send_each)
        messages = [
            messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                token=token,
            )
            for token in tokens
        ]
        
        # Send each message
        response = messaging.send_each(messages)
        logging.info(f"Sent FCM to wali {wali.get('id')} count={len(tokens)} success={response.success_count}")
    except Exception as e:
        logging.error(f"Failed to send FCM notification: {e}")


async def get_current_pengabsen(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        pengabsen_id: str = payload.get("sub")
        if pengabsen_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        pengabsen = await db.pengabsen.find_one({"id": pengabsen_id}, {"_id": 0})
        if pengabsen is None:
            raise HTTPException(status_code=401, detail="Pengabsen not found")

        return pengabsen
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")





async def get_current_pengabsen_kelas(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        pengabsen_kelas_id: str = payload.get("sub")
        if pengabsen_kelas_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        pengabsen = await db.pengabsen_kelas.find_one({"id": pengabsen_kelas_id}, {"_id": 0})
        if pengabsen is None:
            raise HTTPException(status_code=401, detail="Pengabsen kelas not found")

        return pengabsen
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")


async def get_current_pembimbing_kelas(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        pembimbing_kelas_id: str = payload.get("sub")
        if pembimbing_kelas_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        pembimbing = await db.pembimbing_kelas.find_one({"id": pembimbing_kelas_id}, {"_id": 0})
        if pembimbing is None:
            raise HTTPException(status_code=401, detail="Pembimbing kelas not found")

        return pembimbing
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")


def generate_kode_akses() -> str:
    """Generate random 9-digit access code for Pembimbing"""
    import random
    return ''.join([str(random.randint(0, 9)) for _ in range(9)])


def generate_qr_code(data: dict) -> str:
    import json
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(json.dumps(data))
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    img_str = base64.b64encode(buffer.getvalue()).decode()
    
    return img_str

def generate_username(nama: str, nomor_hp: str) -> str:
    """Generate username dari nama dan nomor HP"""
    # Ambil nama depan dan bersihkan
    nama_clean = re.sub(r'[^a-zA-Z]', '', nama.split()[0].lower())
    # Ambil 4 digit terakhir HP
    hp_suffix = nomor_hp[-4:] if len(nomor_hp) >= 4 else nomor_hp
    return f"{nama_clean}{hp_suffix}"

async def sync_wali_santri():
    """Sinkronisasi data wali dari santri - termasuk menghapus wali tanpa anak"""
    # Aggregate santri by wali
    pipeline = [
        {
            "$group": {
                "_id": {
                    "nama_wali": "$nama_wali",
                    "nomor_hp_wali": "$nomor_hp_wali"
                },
                "email_wali": {"$first": "$email_wali"},
                "nama_anak": {"$push": "$nama"},
                "anak_ids": {"$push": "$id"},
                "jumlah_anak": {"$sum": 1},
                "first_created": {"$min": "$created_at"}
            }
        }
    ]
    
    wali_groups = await db.santri.aggregate(pipeline).to_list(1000)
    
    # Collect valid wali IDs from current santri
    valid_wali_ids = set()
    
    for group in wali_groups:
        nama_wali = group["_id"]["nama_wali"]
        nomor_hp = group["_id"]["nomor_hp_wali"]
        email = group.get("email_wali")
        anak_ids = group.get("anak_ids", [])
        
        # Generate wali_id
        wali_id = f"wali_{nomor_hp}"
        valid_wali_ids.add(wali_id)
        
        # Check if wali exists
        existing_wali = await db.wali_santri.find_one({"id": wali_id}, {"_id": 0})
        
        if existing_wali:
            # Update existing
            await db.wali_santri.update_one(
                {"id": wali_id},
                {
                    "$set": {
                        "nama": nama_wali,
                        "nomor_hp": nomor_hp,
                        "email": email,
                        "jumlah_anak": group["jumlah_anak"],
                        "nama_anak": group["nama_anak"],
                        "anak_ids": anak_ids,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                }
            )
        else:
            # Create new
            username = generate_username(nama_wali, nomor_hp)
            # Check username uniqueness
            counter = 1
            original_username = username
            while await db.wali_santri.find_one({"username": username}):
                username = f"{original_username}{counter}"
                counter += 1
            
            wali_doc = {
                "id": wali_id,
                "nama": nama_wali,
                "username": username,
                "password_hash": hash_password("12345"),  # default password
                "nomor_hp": nomor_hp,
                "email": email,
                "jumlah_anak": group["jumlah_anak"],
                "nama_anak": group["nama_anak"],
                "anak_ids": anak_ids,
                "created_at": group["first_created"],
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await db.wali_santri.insert_one(wali_doc)
    
    # Delete wali yang tidak punya santri lagi
    if valid_wali_ids:
        # Delete all wali whose ID is NOT in the valid set
        delete_result = await db.wali_santri.delete_many({"id": {"$nin": list(valid_wali_ids)}})
        if delete_result.deleted_count > 0:
            logging.info(f"Deleted {delete_result.deleted_count} wali without santri")
    else:
        # No santri at all, delete all wali
        delete_result = await db.wali_santri.delete_many({})
        if delete_result.deleted_count > 0:
            logging.info(f"Deleted all {delete_result.deleted_count} wali (no santri left)")

async def fetch_prayer_times(date: str) -> Optional[dict]:
    try:
        url = "http://api.aladhan.com/v1/timingsByAddress"
        params = {
            "address": "Desa Cintamulya, Candipuro, Lampung Selatan, Lampung, Indonesia",
            "method": 2,
            "date": date
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    timings = data['data']['timings']
                    return {
                        'subuh': timings['Fajr'],
                        'dzuhur': timings['Dhuhr'],
                        'ashar': timings['Asr'],
                        'maghrib': timings['Maghrib'],
                        'isya': timings['Isha']
                    }
        return None
    except Exception as e:
        logging.error(f"Error fetching prayer times: {e}")
        return None

# ==================== DAILY WHATSAPP REPORT ENDPOINT ====================

@api_router.post("/notifications/whatsapp/daily-report", dependencies=[Depends(get_current_admin)])
async def trigger_daily_whatsapp_report(tanggal: Optional[str] = None):
    """Bangun rekap absensi sholat per wali untuk 1 hari dan kirim ke WA Bot service.

    Endpoint ini TIDAK mengirim WhatsApp langsung, hanya memanggil service bot eksternal
    melalui WHATSAPP_BOT_URL (kalau diset). Kalau tidak diset, hanya mengembalikan
    payload yang akan dikirim, supaya mudah dites.
    """
    if not tanggal:
        tanggal = get_today_local_iso()

    # Ambil semua absensi sholat (koleksi 'absensi') pada tanggal tsb
    absensi_list = await db.absensi.find({"tanggal": tanggal}, {"_id": 0}).to_list(100000)
    if not absensi_list:
        return {"tanggal": tanggal, "reports": [], "message": "Tidak ada data absensi untuk tanggal ini"}

    # Ambil semua santri yang muncul di absensi
    santri_ids = list({a["santri_id"] for a in absensi_list if a.get("santri_id")})
    santri_docs = await db.santri.find({"id": {"$in": santri_ids}}, {"_id": 0}).to_list(len(santri_ids))
    santri_map = {s["id"]: s for s in santri_docs}

    # Ambil kelas untuk info nama kelas
    asrama_ids = list({s["asrama_id"] for s in santri_docs if s.get("asrama_id")})
    asrama_docs = await db.asrama.find({"id": {"$in": asrama_ids}}, {"_id": 0}).to_list(len(asrama_ids))
    asrama_map = {a["id"]: a["nama"] for a in asrama_docs}

    # Group per wali -> per anak -> per waktu sholat
    per_wali: Dict[str, Dict[str, Any]] = {}

    for rec in absensi_list:
        santri = santri_map.get(rec["santri_id"])
        if not santri:
            continue

        wali_nomor = santri.get("nomor_hp_wali")
        wali_nama = santri.get("nama_wali")
        if not wali_nomor:
            continue

        wali_key = wali_nomor
        if wali_key not in per_wali:
            per_wali[wali_key] = {
                "wali_nama": wali_nama or "Wali Santri",
                "wali_nomor": wali_nomor,
                "anak": {},
            }

        anak_key = santri["id"]
        if anak_key not in per_wali[wali_key]["anak"]:
            per_wali[wali_key]["anak"][anak_key] = {
                "nama": santri["nama"],
                "kelas": asrama_map.get(santri.get("asrama_id"), "-"),
                "subuh": "-",
                "dzuhur": "-",
                "ashar": "-",
                "maghrib": "-",
                "isya": "-",
            }

        waktu = rec.get("waktu_sholat")
        status = rec.get("status")
        if waktu in ["subuh", "dzuhur", "ashar", "maghrib", "isya"] and status:
            per_wali[wali_key]["anak"][anak_key][waktu] = status

    reports: List[DailyWaliReport] = []
    for wali in per_wali.values():
        anak_items = [
            DailyWaliReportAnak(**anak) for anak in wali["anak"].values()
        ]

        reports.append(
            DailyWaliReport(
                wali_nama=wali["wali_nama"],
                wali_nomor=wali["wali_nomor"],
                tanggal=tanggal,
                anak=anak_items,
            )
        )

    batch = DailyWaliReportBatch(tanggal=tanggal, reports=reports)

    # Kalau env WA bot tidak diset, hanya kembalikan payload (untuk debugging)
    if not WHATSAPP_BOT_URL:
        return {"whatsapp_bot_url": None, "payload": batch.model_dump()}

    # Kirim ke WA Bot service via HTTP POST
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WHATSAPP_BOT_URL, json=batch.model_dump()) as resp:
                text = await resp.text()
                return {
                    "whatsapp_bot_url": WHATSAPP_BOT_URL,
                    "status_code": resp.status,
                    "response_text": text,
                    "payload_count": len(reports),
                }
    except Exception as e:
        logging.error(f"Gagal mengirim ke WhatsApp Bot: {e}")
        return {
            "whatsapp_bot_url": WHATSAPP_BOT_URL,
            "error": str(e),
            "payload": batch.model_dump(),
        }



# ==================== AUTHENTIKASI PENGABSEN (PWA - Kode Akses) ====================

@api_router.post("/pengabsen/login", response_model=PengabsenTokenResponse)
async def login_pengabsen(request: PengabsenLoginRequest):
    pengabsen = await db.pengabsen.find_one({"username": request.username}, {"_id": 0})

    if not pengabsen:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau kode akses salah"
        )
    
    # Verify kode_akses
    stored_kode = pengabsen.get("kode_akses", "")
    if request.kode_akses != stored_kode:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau kode akses salah"
        )

    access_token = create_access_token(data={"sub": pengabsen['id'], "role": "pengabsen"})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": PengabsenMeResponse(**{k: v for k, v in pengabsen.items() if k not in ['password_hash', 'kode_akses']})
    }


@api_router.get("/pengabsen/me", response_model=PengabsenMeResponse)
async def get_pengabsen_me(current_pengabsen: dict = Depends(get_current_pengabsen)):
    return PengabsenMeResponse(**{k: v for k, v in current_pengabsen.items() if k not in ['password_hash', 'kode_akses']})


# ==================== AUTHENTICATION ENDPOINTS ====================

@api_router.post("/auth/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    admin = await db.admins.find_one({"username": request.username}, {"_id": 0})
    
    if not admin or not verify_password(request.password, admin.get('password_hash', '')):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password salah"
        )
    
    # Pastikan setiap admin memiliki role (default superadmin jika belum ada)
    role = admin.get("role", "superadmin")
    if "role" not in admin:
        await db.admins.update_one({"id": admin["id"]}, {"$set": {"role": role}})
        admin["role"] = role

    access_token = create_access_token(data={"sub": admin["id"], "role": role})

    user_data = dict(admin)
    user_data.setdefault("role", role)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": AdminResponse(**user_data),
    }


@api_router.post("/pmq/pengabsen/login", response_model=PengabsenPMQTokenResponse)
async def login_pengabsen_pmq(request: PengabsenPMQLoginRequest):
    pengabsen = await db.pengabsen_pmq.find_one({"username": request.username}, {"_id": 0})

    if not pengabsen or pengabsen.get("kode_akses") != request.kode_akses:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau kode akses salah",
        )

    access_token = create_access_token(data={"sub": pengabsen["id"], "role": "pengabsen_pmq"})

    user_data = PengabsenPMQMeResponse(
        id=pengabsen["id"],
        nama=pengabsen["nama"],
        username=pengabsen["username"],
        email_atau_hp=pengabsen.get("email_atau_hp", ""),
        tingkatan_keys=pengabsen.get("tingkatan_keys", []),
        kelompok_ids=pengabsen.get("kelompok_ids", []),
        created_at=pengabsen["created_at"],
    )

    return PengabsenPMQTokenResponse(access_token=access_token, user=user_data)


@api_router.get("/pmq/pengabsen/me", response_model=PengabsenPMQMeResponse)
async def get_pengabsen_pmq_me(current_pengabsen: dict = Depends(get_current_pengabsen_pmq)):
    return PengabsenPMQMeResponse(**current_pengabsen)


@api_router.get("/auth/me", response_model=AdminResponse)
async def get_me(current_admin: dict = Depends(get_current_admin)):
    return AdminResponse(**current_admin)

@api_router.post("/auth/logout")
async def logout(current_admin: dict = Depends(get_current_admin)):
    return {"message": "Logout berhasil"}


# ==================== AUTH PENGABSEN & MONITORING ALIYAH (PWA) ====================

@api_router.post("/aliyah/pengabsen/login", response_model=PengabsenAliyahTokenResponse)
async def login_pengabsen_aliyah(request: PengabsenAliyahLoginRequest):
    pengabsen = await db.pengabsen_aliyah.find_one({"username": request.username}, {"_id": 0})

    if not pengabsen or pengabsen.get("kode_akses") != request.kode_akses:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau kode akses salah",
        )

    access_token = create_access_token(data={"sub": pengabsen["id"], "role": "pengabsen_aliyah"})

    user_data = PengabsenAliyahMeResponse(
        id=pengabsen["id"],
        nama=pengabsen["nama"],
        username=pengabsen["username"],
        email_atau_hp=pengabsen.get("email_atau_hp", ""),
        kelas_ids=pengabsen.get("kelas_ids", []),
        created_at=pengabsen["created_at"],
    )

    return PengabsenAliyahTokenResponse(access_token=access_token, user=user_data)


@api_router.get("/aliyah/pengabsen/me", response_model=PengabsenAliyahMeResponse)
async def get_pengabsen_aliyah_me(current_pengabsen: dict = Depends(get_current_pengabsen_aliyah)):
    return PengabsenAliyahMeResponse(**current_pengabsen)


@api_router.post("/aliyah/monitoring/login", response_model=MonitoringAliyahTokenResponse)
async def login_monitoring_aliyah(request: MonitoringAliyahLoginRequest):
    pembimbing = await db.pembimbing_aliyah.find_one({"username": request.username}, {"_id": 0})

    if not pembimbing or pembimbing.get("kode_akses") != request.kode_akses:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau kode akses salah",
        )

    access_token = create_access_token(data={"sub": pembimbing["id"], "role": "monitoring_aliyah"})

    user_data = MonitoringAliyahMeResponse(
        id=pembimbing["id"],
        nama=pembimbing["nama"],
        username=pembimbing["username"],
        email_atau_hp=pembimbing.get("email_atau_hp", ""),
        kelas_ids=pembimbing.get("kelas_ids", []),
        created_at=pembimbing["created_at"],
    )

    return MonitoringAliyahTokenResponse(access_token=access_token, user=user_data)


@api_router.get("/aliyah/monitoring/me", response_model=MonitoringAliyahMeResponse)
async def get_monitoring_aliyah_me(current_monitoring: dict = Depends(get_current_monitoring_aliyah)):
    return MonitoringAliyahMeResponse(**current_monitoring)


# ==================== ASRAMA ENDPOINTS ====================

@api_router.get("/asrama", response_model=List[Asrama])
async def get_asrama(gender: Optional[str] = None, _: dict = Depends(get_current_admin)):
    query = {}
    if gender:
        query['gender'] = gender
    
    asrama_list = await db.asrama.find(query, {"_id": 0}).to_list(1000)
    return asrama_list

@api_router.post("/asrama", response_model=Asrama)
async def create_asrama(data: AsramaCreate, _: dict = Depends(get_current_admin)):
    asrama_obj = Asrama(**data.model_dump())
    doc = asrama_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.asrama.insert_one(doc)
    return asrama_obj

@api_router.put("/asrama/{asrama_id}", response_model=Asrama)
async def update_asrama(asrama_id: str, data: AsramaUpdate, _: dict = Depends(get_current_admin)):
    asrama = await db.asrama.find_one({"id": asrama_id}, {"_id": 0})
    if not asrama:
        raise HTTPException(status_code=404, detail="Asrama tidak ditemukan")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}

    if "nfc_uid" in update_data:
        nfc_uid = (update_data.get("nfc_uid") or "").strip()
        if nfc_uid:
            # Catatan: asrama saat ini tidak menggunakan nfc_uid, blok ini diabaikan
            update_data["nfc_uid"] = nfc_uid
        else:
            update_data["nfc_uid"] = None
    if update_data:
        await db.asrama.update_one({"id": asrama_id}, {"$set": update_data})
        asrama.update(update_data)
    
    if isinstance(asrama['created_at'], str):
        asrama['created_at'] = datetime.fromisoformat(asrama['created_at'])
    
    return Asrama(**asrama)

@api_router.delete("/asrama/{asrama_id}")
async def delete_asrama(asrama_id: str, _: dict = Depends(get_current_admin)):
    result = await db.asrama.delete_one({"id": asrama_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Asrama tidak ditemukan")
    return {"message": "Asrama berhasil dihapus"}

# ==================== SANTRI ENDPOINTS (REVISED) ====================

@api_router.get("/santri", response_model=List[SantriResponse])
async def get_santri(
    gender: Optional[str] = None,
    asrama_id: Optional[str] = None,
    _: dict = Depends(get_current_admin)
):
    query = {}
    if gender:
        query['gender'] = gender
    if asrama_id:
        query['asrama_id'] = asrama_id
    
    santri_list = await db.santri.find(query, {"_id": 0, "qr_code": 0}).to_list(1000)
    
    # Get all santri IDs that are linked to madrasah
    santri_ids = [s["id"] for s in santri_list]
    linked_santri = await db.siswa_madrasah.find(
        {"santri_id": {"$in": santri_ids}},
        {"_id": 0, "santri_id": 1}
    ).to_list(1000)
    linked_ids = set([s["santri_id"] for s in linked_santri])
    
    for santri in santri_list:
        if isinstance(santri['created_at'], str):
            santri['created_at'] = datetime.fromisoformat(santri['created_at'])
        if isinstance(santri['updated_at'], str):
            santri['updated_at'] = datetime.fromisoformat(santri['updated_at'])
        
        # Add is_in_madrasah flag
        santri['is_in_madrasah'] = santri["id"] in linked_ids
    
    return santri_list

@api_router.post("/santri", response_model=SantriResponse)
async def create_santri(data: SantriCreate, _: dict = Depends(get_current_admin)):
    # Check if NIS already exists
    existing = await db.santri.find_one({"nis": data.nis})
    if existing:
        raise HTTPException(status_code=400, detail="NIS sudah digunakan")

    if data.nfc_uid:
        nfc_uid = data.nfc_uid.strip()
        if nfc_uid:
            existing_nfc = await db.santri.find_one({"nfc_uid": nfc_uid})
            if existing_nfc:
                raise HTTPException(status_code=400, detail="NFC UID sudah digunakan")
    
    # Verify asrama exists
    asrama = await db.asrama.find_one({"id": data.asrama_id})
    if not asrama:
        raise HTTPException(status_code=404, detail="Asrama tidak ditemukan")
    
    santri_id = str(uuid.uuid4())
    
    # Generate QR code
    qr_data = {
        "santri_id": santri_id,
        "nama": data.nama,
        "nis": data.nis
    }
    qr_code = generate_qr_code(qr_data)
    
    santri_dict = data.model_dump()
    if santri_dict.get("nfc_uid"):
        santri_dict["nfc_uid"] = santri_dict["nfc_uid"].strip()
    else:
        santri_dict["nfc_uid"] = None
    santri_dict['id'] = santri_id
    santri_dict['qr_code'] = qr_code
    santri_dict['created_at'] = datetime.now(timezone.utc)
    santri_dict['updated_at'] = datetime.now(timezone.utc)
    
    santri_obj = Santri(**santri_dict)
    doc = santri_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    doc['updated_at'] = doc['updated_at'].isoformat()
    
    await db.santri.insert_one(doc)
    
    # Sync wali santri
    await sync_wali_santri()
    
    return SantriResponse(**{k: v for k, v in santri_obj.model_dump().items() if k != 'qr_code'})

@api_router.get("/santri/{santri_id}/qr-code")
async def get_santri_qr_code(santri_id: str, _: dict = Depends(get_current_admin)):
    santri = await db.santri.find_one({"id": santri_id}, {"_id": 0})
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")
    
    img_data = base64.b64decode(santri['qr_code'])
    return Response(content=img_data, media_type="image/png")

@api_router.put("/santri/{santri_id}", response_model=SantriResponse)
async def update_santri(santri_id: str, data: SantriUpdate, _: dict = Depends(get_current_admin)):
    santri = await db.santri.find_one({"id": santri_id}, {"_id": 0})
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}

    if "nfc_uid" in update_data:
        nfc_uid = (update_data.get("nfc_uid") or "").strip()
        if nfc_uid:
            existing_nfc = await db.santri.find_one({"nfc_uid": nfc_uid, "id": {"$ne": santri_id}})
            if existing_nfc:
                raise HTTPException(status_code=400, detail="NFC UID sudah digunakan")
            update_data["nfc_uid"] = nfc_uid
        else:
            update_data["nfc_uid"] = None
    
    if 'asrama_id' in update_data:
        asrama = await db.asrama.find_one({"id": update_data['asrama_id']})
        if not asrama:
            raise HTTPException(status_code=404, detail="Asrama tidak ditemukan")
    
    if update_data:
        update_data['updated_at'] = datetime.now(timezone.utc).isoformat()
        await db.santri.update_one({"id": santri_id}, {"$set": update_data})
        santri.update(update_data)
    
    # Sync wali if wali data changed
    if any(k in update_data for k in ['nama_wali', 'nomor_hp_wali', 'email_wali']):
        await sync_wali_santri()
    
    # Sync siswa_madrasah if linked
    if any(k in update_data for k in ['nama', 'nis', 'gender']):
        await db.siswa_madrasah.update_many(
            {"santri_id": santri_id},
            {"$set": {
                k: v for k, v in update_data.items() 
                if k in ['nama', 'nis', 'gender']
            }}
        )
    
    if isinstance(santri['created_at'], str):
        santri['created_at'] = datetime.fromisoformat(santri['created_at'])
    if isinstance(santri['updated_at'], str):
        santri['updated_at'] = datetime.fromisoformat(santri['updated_at'])
    
    return SantriResponse(**{k: v for k, v in santri.items() if k != 'qr_code'})



@api_router.post("/santri/{santri_id}/link-to-madrasah")
async def link_santri_to_madrasah(santri_id: str, kelas_id: str = None, _: dict = Depends(get_current_admin)):
    """Link santri to Madrasah Diniyah"""
    santri = await db.santri.find_one({"id": santri_id}, {"_id": 0})
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")
    
    # Check if already linked
    existing = await db.siswa_madrasah.find_one({"santri_id": santri_id}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Santri sudah terdaftar di Madrasah Diniyah")
    
    # Create siswa_madrasah
    siswa_data = {
        "nama": santri["nama"],
        "nis": santri.get("nis"),
        "gender": santri["gender"],
        "kelas_id": kelas_id,
        "santri_id": santri_id
    }
    
    from .server import SiswaMadrasahCreate
    siswa = SiswaMadrasah(**siswa_data)
    
    doc = siswa.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    doc['updated_at'] = doc['updated_at'].isoformat()
    await db.siswa_madrasah.insert_one(doc)
    
    return {"message": "Santri berhasil didaftarkan ke Madrasah Diniyah", "siswa_id": siswa.id}

@api_router.get("/santri/{santri_id}/madrasah-status")
async def get_santri_madrasah_status(santri_id: str, _: dict = Depends(get_current_admin)):
    """Check if santri is linked to Madrasah Diniyah"""
    siswa = await db.siswa_madrasah.find_one({"santri_id": santri_id}, {"_id": 0})
    
    if siswa:
        kelas_nama = None
        if siswa.get("kelas_id"):
            kelas = await db.kelas.find_one({"id": siswa["kelas_id"]}, {"_id": 0})
            kelas_nama = kelas["nama"] if kelas else None
        
        return {
            "is_linked": True,
            "siswa_id": siswa["id"],
            "kelas_id": siswa.get("kelas_id"),
            "kelas_nama": kelas_nama
        }
    
    return {"is_linked": False}

@api_router.delete("/santri/{santri_id}")
async def delete_santri(santri_id: str, _: dict = Depends(get_current_admin)):
    result = await db.santri.delete_one({"id": santri_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")
    
    # Sync wali after deletion
    await sync_wali_santri()
    
    # Also delete siswa_madrasah yang linked ke santri ini
    await db.siswa_madrasah.delete_many({"santri_id": santri_id})
    
    return {"message": "Santri berhasil dihapus"}

# EXCEL IMPORT/EXPORT
@api_router.get("/santri/template/download")
async def download_santri_template(_: dict = Depends(get_current_admin)):
    """Download Excel template untuk import santri"""
    # Create template DataFrame
    template_data = {
        'nama': ['Muhammad Zaki', 'Fatimah Azzahra'],
        'nis': ['001', '002'],
        'gender': ['putra', 'putri'],
        'asrama_id': ['<ID_ASRAMA>', '<ID_ASRAMA>'],
        'nama_wali': ['Ahmad Hidayat', 'Ahmad Hidayat'],
        'nomor_hp_wali': ['081234567890', '081234567890'],
        'email_wali': ['ahmad@email.com', 'ahmad@email.com']
    }
    
    df = pd.DataFrame(template_data)
    
    # Create Excel file in memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Template Santri')
    output.seek(0)
    
    return StreamingResponse(
        output,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename=template_santri.xlsx'}
    )

@api_router.post("/santri/import")
async def import_santri(file: UploadFile = File(...), _: dict = Depends(get_current_admin)):
    """Import santri dari Excel"""
    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        
        required_columns = ['nama', 'nis', 'gender', 'asrama_id', 'nama_wali', 'nomor_hp_wali']
        if not all(col in df.columns for col in required_columns):
            raise HTTPException(status_code=400, detail="Format Excel tidak sesuai template")
        
        success_count = 0
        error_list = []
        
        for idx, row in df.iterrows():
            try:
                # Check if NIS exists
                existing = await db.santri.find_one({"nis": str(row['nis'])})
                if existing:
                    error_list.append(f"Baris {idx+2}: NIS {row['nis']} sudah ada")
                    continue
                
                # Verify asrama
                asrama = await db.asrama.find_one({"id": str(row['asrama_id'])})
                if not asrama:
                    error_list.append(f"Baris {idx+2}: Asrama ID tidak ditemukan")
                    continue
                
                santri_id = str(uuid.uuid4())
                qr_data = {"santri_id": santri_id, "nama": str(row['nama']), "nis": str(row['nis'])}
                qr_code = generate_qr_code(qr_data)
                
                santri_doc = {
                    'id': santri_id,
                    'nama': str(row['nama']),
                    'nis': str(row['nis']),
                    'gender': str(row['gender']),
                    'asrama_id': str(row['asrama_id']),
                    'nama_wali': str(row['nama_wali']),
                    'nomor_hp_wali': str(row['nomor_hp_wali']),
                    'email_wali': str(row.get('email_wali', '')),
                    'qr_code': qr_code,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }
                
                await db.santri.insert_one(santri_doc)
                success_count += 1
                
            except Exception as e:
                error_list.append(f"Baris {idx+2}: {str(e)}")
        
        # Sync wali after import
        await sync_wali_santri()
        
        return {
            "message": "Import selesai",
            "success": success_count,
            "errors": error_list
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error processing file: {str(e)}")

@api_router.get("/santri/export")
async def export_santri(_: dict = Depends(get_current_admin)):
    """Export semua santri ke Excel"""
    santri_list = await db.santri.find({}, {"_id": 0, "qr_code": 0}).to_list(10000)
    
    if not santri_list:
        raise HTTPException(status_code=404, detail="Tidak ada data santri")
    
    df = pd.DataFrame(santri_list)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Data Santri')
    output.seek(0)
    
    return StreamingResponse(
        output,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename=data_santri.xlsx'}
    )

# ==================== WALI SANTRI ENDPOINTS (AUTO-GENERATED) ====================


class WaliLoginRequest(BaseModel):
    username: str
    password: str


class WaliMeResponse(BaseModel):
    id: str
    nama: str
    username: str
    nomor_hp: str
    email: Optional[str]
    anak_ids: List[str]


class WaliTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: WaliMeResponse


@api_router.get("/wali", response_model=List[WaliSantriResponse])
async def get_wali(_: dict = Depends(get_current_admin)):
    """Get all wali santri (auto-generated from santri data)"""
    # Trigger sync first
    await sync_wali_santri()
    
    wali_list = await db.wali_santri.find({}, {"_id": 0, "password_hash": 0}).to_list(1000)
    
    for wali in wali_list:
        if isinstance(wali['created_at'], str):
            wali['created_at'] = datetime.fromisoformat(wali['created_at'])
        if isinstance(wali['updated_at'], str):
            wali['updated_at'] = datetime.fromisoformat(wali['updated_at'])
    
    return wali_list


async def get_current_wali(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        wali_id: str = payload.get("sub")
        if wali_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        wali = await db.wali_santri.find_one({"id": wali_id}, {"_id": 0})
        if wali is None:
            raise HTTPException(status_code=401, detail="Wali tidak ditemukan")

        return wali
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")


class WaliFcmTokenRequest(BaseModel):
    token: str


@api_router.post("/wali/fcm-token")
async def register_wali_fcm_token(payload: WaliFcmTokenRequest, current_wali: dict = Depends(get_current_wali)):
    token = payload.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token tidak boleh kosong")

    wali_id = current_wali["id"]
    wali = await db.wali_santri.find_one({"id": wali_id}, {"_id": 0})
    if not wali:
        raise HTTPException(status_code=404, detail="Wali tidak ditemukan")

    tokens: list[str] = wali.get("fcm_tokens", []) or []
    if token not in tokens:
        tokens.append(token)
        await db.wali_santri.update_one({"id": wali_id}, {"$set": {"fcm_tokens": tokens}})

    return {"status": "ok"}


@api_router.put("/wali/{wali_id}", response_model=WaliSantriResponse)
async def update_wali(wali_id: str, data: WaliSantriUpdate, _: dict = Depends(get_current_admin)):
    """Update wali (only username and password)"""
    wali = await db.wali_santri.find_one({"id": wali_id}, {"_id": 0})
    if not wali:
        raise HTTPException(status_code=404, detail="Wali santri tidak ditemukan")
    
    update_data = {}
    if data.username:
        # Check username uniqueness
        existing = await db.wali_santri.find_one({"username": data.username, "id": {"$ne": wali_id}})
        if existing:
            raise HTTPException(status_code=400, detail="Username sudah digunakan")
        update_data['username'] = data.username
    
    if data.password:
        update_data['password_hash'] = hash_password(data.password)
    
    if update_data:
        update_data['updated_at'] = datetime.now(timezone.utc).isoformat()
        await db.wali_santri.update_one({"id": wali_id}, {"$set": update_data})
        wali.update(update_data)
    
    if isinstance(wali['created_at'], str):
        wali['created_at'] = datetime.fromisoformat(wali['created_at'])
    if isinstance(wali['updated_at'], str):
        wali['updated_at'] = datetime.fromisoformat(wali['updated_at'])
    
    return WaliSantriResponse(**{k: v for k, v in wali.items() if k != 'password_hash'})


@api_router.post("/wali/login", response_model=WaliTokenResponse)
async def login_wali(request: WaliLoginRequest):
    # Pastikan data wali dan anak_ids sudah tersinkron dari data santri
    await sync_wali_santri()

    # Temukan wali berdasarkan username
    wali = await db.wali_santri.find_one({"username": request.username}, {"_id": 0})

    if not wali:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Username atau password salah")

    # Password default untuk SEMUA wali: "12345".
    # Jika wali sudah punya password_hash khusus, ia juga boleh login dengan password tersebut.
    password_hash = wali.get("password_hash")
    default_password = "12345"

    if request.password == default_password:
        # Selalu terima password default, terlepas dari ada/tidaknya password_hash khusus
        pass
    elif password_hash:
        if not verify_password(request.password, password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Username atau password salah")
    else:
        # Tidak pakai default dan belum ada password tersimpan
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Username atau password salah")

    access_token = create_access_token(data={"sub": wali["id"], "role": "wali"})

    user_data = WaliMeResponse(
        id=wali["id"],
        nama=wali["nama"],
        username=wali["username"],
        nomor_hp=wali["nomor_hp"],
        email=wali.get("email"),
        anak_ids=wali.get("anak_ids", []),
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user_data,
    }


@api_router.get("/wali/anak-absensi-hari-ini")
async def get_wali_anak_absensi_hari_ini(current_wali: dict = Depends(get_current_wali)):
    today = get_today_local_iso()
    anak_ids: List[str] = current_wali.get("anak_ids", [])
    if not anak_ids:
        return {"tanggal": today, "data": []}

    santri_list = await db.santri.find({"id": {"$in": anak_ids}}, {"_id": 0}).to_list(100)
    santri_by_id = {s["id"]: s for s in santri_list}

    asrama_map = {a["id"]: a["nama"] for a in await db.asrama.find({}, {"_id": 0}).to_list(1000)}

    absensi_list = await db.absensi.find(
        {"tanggal": today, "santri_id": {"$in": list(santri_by_id.keys())}}, {"_id": 0}
    ).to_list(1000)

    status_by_santri: dict = {sid: {} for sid in santri_by_id.keys()}
    for a in absensi_list:
        sid = a["santri_id"]
        waktu_sholat = a["waktu_sholat"]
        status_val = a["status"]
        if sid in status_by_santri:
            status_by_santri[sid][waktu_sholat] = status_val

    # Map pengabsen_id -> nama untuk referensi
    pengabsen_map = {
        p["id"]: p.get("nama", "-")
        for p in await db.pengabsen.find({}, {"_id": 0, "id": 1, "nama": 1}).to_list(1000)
    }

    # Hitung pengabsen "utama" per santri (mis. entry terakhir hari itu)
    pengabsen_by_santri: dict[str, Optional[str]] = {}
    for a in absensi_list:
        sid = a["santri_id"]
        pid = a.get("pengabsen_id")
        if sid in santri_by_id and pid:
            pengabsen_by_santri[sid] = pengabsen_map.get(pid, "-")

    result = []
    for sid, santri in santri_by_id.items():
        result.append(
            {
                "santri_id": sid,
                "nama": santri["nama"],
                "nis": santri["nis"],
                "asrama_id": santri["asrama_id"],
                "nama_asrama": asrama_map.get(santri["asrama_id"], "-"),
                "status": status_by_santri.get(sid, {}),
                "pengabsen_nama": pengabsen_by_santri.get(sid),
            }
        )

    return {"tanggal": today, "data": result}


@api_router.get("/wali/anak-absensi-riwayat")
async def get_wali_anak_absensi_riwayat(tanggal: str, current_wali: dict = Depends(get_current_wali)):
    anak_ids: List[str] = current_wali.get("anak_ids", [])
    if not anak_ids:
        return {"tanggal": tanggal, "data": []}

    santri_list = await db.santri.find({"id": {"$in": anak_ids}}, {"_id": 0}).to_list(100)
    santri_by_id = {s["id"]: s for s in santri_list}

    asrama_map = {a["id"]: a["nama"] for a in await db.asrama.find({}, {"_id": 0}).to_list(1000)}

    absensi_list = await db.absensi.find(
        {"tanggal": tanggal, "santri_id": {"$in": list(santri_by_id.keys())}}, {"_id": 0}
    ).to_list(1000)

    status_by_santri: dict = {sid: {} for sid in santri_by_id.keys()}
    for a in absensi_list:
        sid = a["santri_id"]
        waktu_sholat = a["waktu_sholat"]
        status_val = a["status"]
        if sid in status_by_santri:
            status_by_santri[sid][waktu_sholat] = status_val

    # Map pengabsen_id -> nama untuk referensi
    pengabsen_map = {
        p["id"]: p.get("nama", "-")
        for p in await db.pengabsen.find({}, {"_id": 0, "id": 1, "nama": 1}).to_list(1000)
    }

    pengabsen_by_santri: dict[str, Optional[str]] = {}
    for a in absensi_list:
        sid = a["santri_id"]
        pid = a.get("pengabsen_id")
        if sid in santri_by_id and pid:
            pengabsen_by_santri[sid] = pengabsen_map.get(pid, "-")

    result = []
    for sid, santri in santri_by_id.items():
        result.append(
            {
                "santri_id": sid,
                "nama": santri["nama"],
                "nis": santri["nis"],
                "asrama_id": santri["asrama_id"],
                "nama_asrama": asrama_map.get(santri["asrama_id"], "-"),
                "status": status_by_santri.get(sid, {}),
                "pengabsen_nama": pengabsen_by_santri.get(sid),
            }
        )

    return {"tanggal": tanggal, "data": result}


@api_router.get("/wali/me", response_model=WaliMeResponse)
async def get_wali_me(current_wali: dict = Depends(get_current_wali)):
    return WaliMeResponse(
        id=current_wali["id"],
        nama=current_wali["nama"],
        username=current_wali["username"],
        nomor_hp=current_wali["nomor_hp"],
        email=current_wali.get("email"),
        anak_ids=current_wali.get("anak_ids", []),
    )


@api_router.get("/wali-app/absensi-kelas")
async def get_wali_absensi_kelas(
    tanggal: str,
    current_wali: dict = Depends(get_current_wali)
):
    """Get absensi kelas for wali's children"""
    anak_ids = current_wali.get("anak_ids", [])
    
    if not anak_ids:
        return []
    
    # Get all siswa_madrasah linked to these santri
    siswa_list = await db.siswa_madrasah.find(
        {"santri_id": {"$in": anak_ids}},
        {"_id": 0}
    ).to_list(100)
    
    if not siswa_list:
        return []
    
    # Get absensi for these siswa on the given date
    siswa_ids = [siswa["id"] for siswa in siswa_list]
    absensi_list = await db.absensi_kelas.find(
        {"siswa_id": {"$in": siswa_ids}, "tanggal": tanggal},
        {"_id": 0}
    ).to_list(100)
    
    # Get kelas info
    kelas_ids = list(set([siswa.get("kelas_id") for siswa in siswa_list if siswa.get("kelas_id")]))
    kelas_map = {}
    if kelas_ids:
        kelas_list = await db.kelas.find({"id": {"$in": kelas_ids}}, {"_id": 0}).to_list(100)
        for kelas in kelas_list:
            kelas_map[kelas["id"]] = kelas["nama"]
    
    # Build response
    result = []
    for siswa in siswa_list:
        absensi = next((a for a in absensi_list if a["siswa_id"] == siswa["id"]), None)
        
        result.append({
            "siswa_id": siswa["id"],
            "siswa_nama": siswa["nama"],
            "kelas_nama": kelas_map.get(siswa.get("kelas_id")),
            "status": absensi["status"] if absensi else None,
            "waktu_absen": absensi.get("waktu_absen") if absensi else None
        })
    
    return result



@api_router.get("/wali/{wali_id}/whatsapp-message")
async def get_wali_whatsapp_message(wali_id: str, _: dict = Depends(get_current_admin)):
    """Generate WhatsApp message for wali"""
    wali = await db.wali_santri.find_one({"id": wali_id}, {"_id": 0})
    if not wali:
        raise HTTPException(status_code=404, detail="Wali santri tidak ditemukan")
    
    message = f"""Assalamu'alaikum Bapak/Ibu {wali['nama']}

Berikut informasi akun Wali Santri Anda:

👤 Nama: {wali['nama']}
📱 Username: {wali['username']}
🔑 Password: 12345
📞 Nomor HP: {wali['nomor_hp']}
📧 Email: {wali.get('email', '-')}

👨‍👩‍👧‍👦 Anak Anda ({wali['jumlah_anak']} orang):
{chr(10).join(f"- {nama}" for nama in wali['nama_anak'])}

Silakan login ke aplikasi Wali Santri untuk melihat absensi sholat anak Anda.

⚠️ Harap ganti password setelah login pertama kali.

Terima kasih
Pondok Pesantren"""
    
    # WhatsApp link
    nomor_wa = wali['nomor_hp'].replace('+', '').replace('-', '').replace(' ', '')
    if nomor_wa.startswith('0'):
        nomor_wa = '62' + nomor_wa[1:]
    
    # URL encode message
    import urllib.parse
    encoded_message = urllib.parse.quote(message)
    wa_link = f"https://wa.me/{nomor_wa}?text={encoded_message}"
    
    return {
        "message": message,
        "whatsapp_link": wa_link,
        "nomor_whatsapp": nomor_wa
    }


# ==================== PENGABSEN ENDPOINTS (REVISED - Kode Akses) ====================

@api_router.get("/pengabsen", response_model=List[PengabsenResponse])
async def get_pengabsen(_: dict = Depends(get_current_admin)):
    raw_list = await db.pengabsen.find({}, {"_id": 0}).to_list(1000)

    normalized: List[PengabsenResponse] = []
    for pengabsen in raw_list:
        # Backward compatibility untuk data lama
        if 'email_atau_hp' not in pengabsen:
            pengabsen['email_atau_hp'] = pengabsen.get('nip', '')
        if 'asrama_ids' not in pengabsen:
            if 'asrama_id' in pengabsen:
                pengabsen['asrama_ids'] = [pengabsen['asrama_id']]
            else:
                pengabsen['asrama_ids'] = []
        # Migrate from password_hash to kode_akses if needed
        if 'kode_akses' not in pengabsen:
            pengabsen['kode_akses'] = generate_kode_akses()
            await db.pengabsen.update_one({"id": pengabsen['id']}, {"$set": {"kode_akses": pengabsen['kode_akses']}})

        if isinstance(pengabsen.get('created_at'), str):
            pengabsen['created_at'] = datetime.fromisoformat(pengabsen['created_at'])

        data = {k: v for k, v in pengabsen.items() if k != 'password_hash'}
        normalized.append(PengabsenResponse(**data))

    return normalized

@api_router.post("/pengabsen", response_model=PengabsenResponse)
async def create_pengabsen(data: PengabsenCreate, _: dict = Depends(get_current_admin)):
    existing = await db.pengabsen.find_one({"username": data.username})
    if existing:
        raise HTTPException(status_code=400, detail="Username sudah digunakan")
    
    for asrama_id in data.asrama_ids:
        asrama = await db.asrama.find_one({"id": asrama_id})
        if not asrama:
            raise HTTPException(status_code=404, detail=f"Asrama {asrama_id} tidak ditemukan")
    
    pengabsen_dict = data.model_dump()
    pengabsen_dict['kode_akses'] = generate_kode_akses()  # Auto-generate kode akses
    
    pengabsen_obj = Pengabsen(**pengabsen_dict)
    doc = pengabsen_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.pengabsen.insert_one(doc)
    
    return PengabsenResponse(**pengabsen_obj.model_dump())

@api_router.put("/pengabsen/{pengabsen_id}", response_model=PengabsenResponse)
async def update_pengabsen(pengabsen_id: str, data: PengabsenUpdate, _: dict = Depends(get_current_admin)):
    pengabsen = await db.pengabsen.find_one({"id": pengabsen_id}, {"_id": 0})
    if not pengabsen:
        raise HTTPException(status_code=404, detail="Pengabsen tidak ditemukan")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    
    if 'asrama_ids' in update_data:
        for asrama_id in update_data['asrama_ids']:
            asrama = await db.asrama.find_one({"id": asrama_id})
            if not asrama:
                raise HTTPException(status_code=404, detail=f"Asrama {asrama_id} tidak ditemukan")
    
    if update_data:
        await db.pengabsen.update_one({"id": pengabsen_id}, {"$set": update_data})
        pengabsen.update(update_data)
    
    # Ensure kode_akses exists
    if 'kode_akses' not in pengabsen:
        pengabsen['kode_akses'] = generate_kode_akses()
        await db.pengabsen.update_one({"id": pengabsen_id}, {"$set": {"kode_akses": pengabsen['kode_akses']}})
    
    if isinstance(pengabsen.get('created_at'), str):
        pengabsen['created_at'] = datetime.fromisoformat(pengabsen['created_at'])
    
    return PengabsenResponse(**{k: v for k, v in pengabsen.items() if k != 'password_hash'})


@api_router.post("/pengabsen/{pengabsen_id}/regenerate-kode-akses", response_model=PengabsenResponse)
async def regenerate_pengabsen_kode_akses(pengabsen_id: str, _: dict = Depends(get_current_admin)):
    """Regenerate kode akses untuk pengabsen"""
    pengabsen = await db.pengabsen.find_one({"id": pengabsen_id}, {"_id": 0})
    if not pengabsen:
        raise HTTPException(status_code=404, detail="Pengabsen tidak ditemukan")
    
    new_kode = generate_kode_akses()
    await db.pengabsen.update_one({"id": pengabsen_id}, {"$set": {"kode_akses": new_kode}})
    pengabsen['kode_akses'] = new_kode
    
    if isinstance(pengabsen['created_at'], str):
        pengabsen['created_at'] = datetime.fromisoformat(pengabsen['created_at'])
    
    return PengabsenResponse(**{k: v for k, v in pengabsen.items() if k != 'password_hash'})


@api_router.post("/pengabsen/absensi")
async def upsert_absensi_pengabsen(
    santri_id: str,
    waktu_sholat: Literal["subuh", "dzuhur", "ashar", "maghrib", "isya"],
    status_absen: Literal["hadir", "alfa", "sakit", "izin", "haid", "istihadhoh", "masbuq"] = "hadir",
    current_pengabsen: dict = Depends(get_current_pengabsen)
):
    # Gunakan tanggal lokal (WIB) agar konsisten dengan persepsi pengguna
    today = get_today_local_iso()

    santri = await db.santri.find_one({"id": santri_id}, {"_id": 0})
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    if santri['asrama_id'] not in current_pengabsen.get('asrama_ids', []):
        raise HTTPException(status_code=403, detail="Santri bukan asrama yang Anda kelola")

    existing = await db.absensi.find_one({
        "santri_id": santri_id,
        "waktu_sholat": waktu_sholat,
        "tanggal": today
    })

    doc = {
        "santri_id": santri_id,
        "waktu_sholat": waktu_sholat,
        "status": status_absen,
        "tanggal": today,
        "pengabsen_id": current_pengabsen['id'],
        "waktu_absen": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    if existing:
        await db.absensi.update_one({"id": existing.get("id")}, {"$set": doc})
    else:
        doc["id"] = str(uuid.uuid4())
        await db.absensi.insert_one(doc)

    # Kirim notifikasi ke wali terkait
    try:
        # Cari wali yang memiliki santri ini
        wali_list = await db.wali_santri.find({"anak_ids": santri_id}, {"_id": 0}).to_list(100)
        if wali_list:
            # Ambil template notifikasi dari settings (fallback ke default jika belum di-set)
            settings_doc = await db.settings.find_one({"id": "wali_notifikasi"}, {"_id": 0}) or {}
            templates = {
                "hadir": settings_doc.get(
                    "hadir",
                    "{nama} hadir pada waktu sholat {waktu} hari ini, alhamdulillah (hadir)",
                ),
                "alfa": settings_doc.get(
                    "alfa",
                    "{nama} tidak mengikuti/membolos sholat {waktu} pada hari ini (alfa)",
                ),
                "sakit": settings_doc.get(
                    "sakit",
                    "{nama} tidak mengikuti sholat {waktu} pada hari ini karena sedang sakit (sakit)",
                ),
                "izin": settings_doc.get(
                    "izin",
                    "{nama} tidak mengikuti sholat {waktu} pada hari ini karena izin (izin)",
                ),
                "haid": settings_doc.get(
                    "haid",
                    "{nama} tidak mengikuti sholat {waktu} pada hari ini karena sedang haid (haid)",
                ),
                "istihadhoh": settings_doc.get(
                    "istihadhoh",
                    "{nama} tidak mengikuti sholat {waktu} pada hari ini karena sedang istihadhoh (istihadhoh)",
                ),
            }

            waktu_label_map = {
                "subuh": "subuh",
                "dzuhur": "dzuhur",
                "ashar": "ashar",
                "maghrib": "maghrib",
                "isya": "isya",
            }

            template = templates.get(status_absen)
            if template:
                body = template.format(nama=santri["nama"], waktu=waktu_label_map.get(waktu_sholat, waktu_sholat))
                title = f"Absensi Sholat {waktu_label_map.get(waktu_sholat, waktu_sholat).capitalize()}"
                for wali in wali_list:
                    await send_wali_push_notification(wali, title=title, body=body)
    except Exception as e:
        logging.error(f"Failed to send wali notification: {e}")

    return {"message": "Absensi tersimpan", "tanggal": today}


@api_router.post("/pengabsen/absensi/nfc")
async def absensi_pengabsen_nfc(
    payload: NFCAbsensiRequest,
    current_pengabsen: dict = Depends(get_current_pengabsen),
):
    nfc_uid = payload.nfc_uid.strip() if payload.nfc_uid else ""
    if not nfc_uid:
        raise HTTPException(status_code=400, detail="NFC UID wajib diisi")

    waktu_sholat = payload.waktu_sholat
    valid_waktu = ["subuh", "dzuhur", "ashar", "maghrib", "isya"]
    if waktu_sholat not in valid_waktu:
        raise HTTPException(status_code=400, detail="Waktu sholat tidak valid")

    status_absen = payload.status or "hadir"
    valid_status = ["hadir", "alfa", "sakit", "izin", "haid", "istihadhoh", "masbuq"]
    if status_absen not in valid_status:
        raise HTTPException(status_code=400, detail="Status absensi tidak valid")

    santri = await db.santri.find_one({"nfc_uid": nfc_uid}, {"_id": 0})
    if not santri:
        raise HTTPException(status_code=404, detail="Kartu NFC belum terdaftar")

    if santri['asrama_id'] not in current_pengabsen.get('asrama_ids', []):
        raise HTTPException(status_code=403, detail="Santri bukan asrama yang Anda kelola")

    tanggal = payload.tanggal or get_today_local_iso()

    existing = await db.absensi.find_one({
        "santri_id": santri["id"],
        "waktu_sholat": waktu_sholat,
        "tanggal": tanggal,
    })

    doc = {
        "santri_id": santri["id"],
        "waktu_sholat": waktu_sholat,
        "status": status_absen,
        "tanggal": tanggal,
        "pengabsen_id": current_pengabsen['id'],
        "waktu_absen": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if existing:
        await db.absensi.update_one({"id": existing.get("id")}, {"$set": doc})
        return {
            "message": "Santri sudah diabsen pada waktu ini",
            "status": existing.get("status"),
            "tanggal": tanggal,
            "santri_nama": santri.get("nama"),
        }
    else:
        doc["id"] = str(uuid.uuid4())
        await db.absensi.insert_one(doc)
        return {
            "message": "Absensi tersimpan",
            "tanggal": tanggal,
            "santri_nama": santri.get("nama"),
        }

    try:
        wali_list = await db.wali_santri.find({"anak_ids": santri["id"]}, {"_id": 0}).to_list(100)
        if wali_list:
            settings_doc = await db.settings.find_one({"id": "wali_notifikasi"}, {"_id": 0}) or {}
            templates = {
                "hadir": settings_doc.get(
                    "hadir",
                    "{nama} hadir pada waktu sholat {waktu} hari ini, alhamdulillah (hadir)",
                ),
                "alfa": settings_doc.get(
                    "alfa",
                    "{nama} tidak mengikuti/membolos sholat {waktu} pada hari ini (alfa)",
                ),
                "sakit": settings_doc.get(
                    "sakit",
                    "{nama} tidak mengikuti sholat {waktu} pada hari ini karena sedang sakit (sakit)",
                ),
                "izin": settings_doc.get(
                    "izin",
                    "{nama} tidak mengikuti sholat {waktu} pada hari ini karena izin (izin)",
                ),
                "haid": settings_doc.get(
                    "haid",
                    "{nama} tidak mengikuti sholat {waktu} pada hari ini karena sedang haid (haid)",
                ),
                "istihadhoh": settings_doc.get(
                    "istihadhoh",
                    "{nama} tidak mengikuti sholat {waktu} pada hari ini karena sedang istihadhoh (istihadhoh)",
                ),
                "masbuq": settings_doc.get(
                    "masbuq",
                    "{nama} mengikuti sholat {waktu} hari ini namun datang masbuq (masbuq)",
                ),
            }
            waktu_label_map = {
                "subuh": "subuh",
                "dzuhur": "dzuhur",
                "ashar": "ashar",
                "maghrib": "maghrib",
                "isya": "isya",
            }
            template = templates.get(status_absen)
            if template:
                body = template.format(nama=santri["nama"], waktu=waktu_label_map.get(waktu_sholat, waktu_sholat))
                title = f"Absensi Sholat {waktu_label_map.get(waktu_sholat, waktu_sholat).capitalize()}"
                for wali in wali_list:
                    await send_wali_push_notification(wali, title=title, body=body)
    except Exception as e:
        logging.error(f"Failed to send wali notification (NFC): {e}")

    return {"message": "Absensi NFC tersimpan", "tanggal": tanggal}


@api_router.delete("/pengabsen/absensi")
async def delete_absensi_pengabsen(
    santri_id: str,
    waktu_sholat: Literal["subuh", "dzuhur", "ashar", "maghrib", "isya"],
    current_pengabsen: dict = Depends(get_current_pengabsen)
):
    today = get_today_local_iso()

    santri = await db.santri.find_one({"id": santri_id}, {"_id": 0})
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    if santri['asrama_id'] not in current_pengabsen.get('asrama_ids', []):
        raise HTTPException(status_code=403, detail="Santri bukan asrama yang Anda kelola")

    result = await db.absensi.delete_one({
        "santri_id": santri_id,
        "waktu_sholat": waktu_sholat,
        "tanggal": today
    })

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Data absensi tidak ditemukan")

    return {"message": "Absensi dihapus", "tanggal": today}


@api_router.get("/pengabsen/santri-absensi-hari-ini")
async def get_santri_absensi_hari_ini(
    waktu_sholat: Literal["subuh", "dzuhur", "ashar", "maghrib", "isya"],
    current_pengabsen: dict = Depends(get_current_pengabsen)
):
    today = get_today_local_iso()

    asrama_ids = current_pengabsen.get('asrama_ids', [])
    santri_list = await db.santri.find({"asrama_id": {"$in": asrama_ids}}, {"_id": 0}).to_list(10000)
    santri_by_id = {s['id']: s for s in santri_list}

    absensi_list = await db.absensi.find({
        "tanggal": today,
        "waktu_sholat": waktu_sholat,
        "santri_id": {"$in": list(santri_by_id.keys())}
    }, {"_id": 0}).to_list(10000)

    absensi_by_santri = {a['santri_id']: a for a in absensi_list}

    asrama_map = {a['id']: a['nama'] for a in await db.asrama.find({}, {"_id": 0}).to_list(10000)}

    result = []
    for sid, santri in santri_by_id.items():
        absensi = absensi_by_santri.get(sid)
        status_val = absensi['status'] if absensi else None
        result.append({
            "santri_id": sid,
            "nama": santri['nama'],
            "nis": santri['nis'],
            "asrama_id": santri['asrama_id'],
            "nama_asrama": asrama_map.get(santri['asrama_id'], "-"),
            "status": status_val
        })

    return {"tanggal": today, "waktu_sholat": waktu_sholat, "data": result}


@api_router.post("/admin/fix-absensi-subuh-kemarin-ke-hari-ini")
async def fix_absensi_subuh_kemarin_ke_hari_ini(_: dict = Depends(get_current_admin)):
    """Perbaiki data absensi subuh yang salah tanggal (kemarin -> hari ini) untuk rentang jam 03.30–05.30 WIB.

    Logika:
    - Ambil `today` berdasarkan WIB.
    - `yesterday` = today - 1 hari.
    - Konversi jam 03:30–05:30 WIB (today) ke UTC untuk dipakai sebagai filter `waktu_absen`.
    - Update semua dokumen `absensi` dengan:
        * tanggal == yesterday
        * waktu_sholat == 'subuh'
        * waktu_absen di antara 03:30–05:30 WIB (dalam UTC)
      menjadi tanggal == today.

    Endpoint ini idempotent: jika dijalankan ulang setelah perbaikan, tidak ada dokumen tambahan yang terkena
    karena filter selalu mencari tanggal == yesterday.
    """
    today_local = datetime.now(LOCAL_TZ).date()
    yesterday_local = today_local - timedelta(days=1)

    start_wib = datetime(
        year=today_local.year,
        month=today_local.month,
        day=today_local.day,
        hour=3,
        minute=30,
        tzinfo=LOCAL_TZ,
    )
    end_wib = datetime(
        year=today_local.year,
        month=today_local.month,
        day=today_local.day,
        hour=5,
        minute=30,
        tzinfo=LOCAL_TZ,
    )

    start_utc = start_wib.astimezone(timezone.utc).isoformat()
    end_utc = end_wib.astimezone(timezone.utc).isoformat()

    result = await db.absensi.update_many(
        {
            "tanggal": yesterday_local.isoformat(),
            "waktu_sholat": "subuh",
            "waktu_absen": {"$gte": start_utc, "$lte": end_utc},
        },
        {"$set": {"tanggal": today_local.isoformat()}},
    )

    return {
        "from_date": yesterday_local.isoformat(),
        "to_date": today_local.isoformat(),
        "window_wib": {
            "start": start_wib.isoformat(),
            "end": end_wib.isoformat(),
        },
        "matched": result.matched_count,
        "modified": result.modified_count,
    }



@api_router.get("/pengabsen/riwayat-detail")
async def get_pengabsen_riwayat_detail(
    tanggal: str,
    waktu_sholat: Literal["subuh", "dzuhur", "ashar", "maghrib", "isya"],
    asrama_id: Optional[str] = None,
    current_pengabsen: dict = Depends(get_current_pengabsen),
):
    """Detail santri untuk satu kombinasi tanggal/waktu_sholat/asrama yang dicatat oleh pengabsen saat ini."""
    query = {
        "pengabsen_id": current_pengabsen["id"],
        "tanggal": tanggal,
        "waktu_sholat": waktu_sholat,
    }
    absensi_list = await db.absensi.find(query, {"_id": 0}).to_list(10000)

    if not absensi_list:
        return {"tanggal": tanggal, "waktu_sholat": waktu_sholat, "data": []}

    santri_ids = list({a["santri_id"] for a in absensi_list})
    santri_docs = await db.santri.find({"id": {"$in": santri_ids}}, {"_id": 0}).to_list(10000)
    santri_by_id = {s["id"]: s for s in santri_docs}

    items = []
    for a in absensi_list:
        sid = a.get("santri_id")
        santri = santri_by_id.get(sid)
        if not santri:
            continue

        # Pastikan hanya asrama yang dikelola pengabsen
        asrama_santri = santri.get("asrama_id")
        if asrama_santri not in current_pengabsen.get("asrama_ids", []):
            continue

        if asrama_id and asrama_santri != asrama_id:
            continue

        items.append(
            {
                "santri_id": sid,
                "nama": santri["nama"],
                "nis": santri["nis"],
                "asrama_id": asrama_santri,
                "status": a.get("status"),
            }
        )

    return {"tanggal": tanggal, "waktu_sholat": waktu_sholat, "data": items}


@api_router.delete("/pengabsen/{pengabsen_id}")
async def delete_pengabsen(pengabsen_id: str, _: dict = Depends(get_current_admin)):
    result = await db.pengabsen.delete_one({"id": pengabsen_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Pengabsen tidak ditemukan")
    return {"message": "Pengabsen berhasil dihapus"}


@api_router.get("/pengabsen/riwayat")
async def get_pengabsen_riwayat(
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    asrama_id: Optional[str] = None,
    current_pengabsen: dict = Depends(get_current_pengabsen),
):
    """Riwayat absensi yang dicatat oleh pengabsen tertentu (ringkasan per tanggal/waktu/asrama)."""
    if not tanggal_end:
      tanggal_end = tanggal_start

    # Ambil semua absensi milik pengabsen dalam rentang tanggal
    query = {
        "pengabsen_id": current_pengabsen["id"],
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end},
    }
    absensi_list = await db.absensi.find(query, {"_id": 0}).to_list(10000)

    if not absensi_list:
        return {"items": []}

    santri_ids = list({a["santri_id"] for a in absensi_list})
    santri_docs = await db.santri.find({"id": {"$in": santri_ids}}, {"_id": 0}).to_list(10000)
    santri_by_id = {s["id"]: s for s in santri_docs}

    asrama_docs = await db.asrama.find({}, {"_id": 0}).to_list(1000)
    asrama_map = {a["id"]: a["nama"] for a in asrama_docs}

    # Grouping: (tanggal, asrama_id, waktu_sholat) -> counts per status
    groups: dict = {}
    for a in absensi_list:
        sid = a.get("santri_id")
        santri = santri_by_id.get(sid)
        if not santri:
            continue

        asrama_santri = santri.get("asrama_id")
        # Batasi hanya asrama yang dikelola pengabsen
        if asrama_santri not in current_pengabsen.get("asrama_ids", []):
            continue

        # Filter asrama jika diminta
        if asrama_id and asrama_santri != asrama_id:
            continue

        key = (a["tanggal"], asrama_santri, a["waktu_sholat"])
        if key not in groups:
            groups[key] = {
                "tanggal": a["tanggal"],
                "asrama_id": asrama_santri,
                "nama_asrama": asrama_map.get(asrama_santri, "-"),
                "waktu_sholat": a["waktu_sholat"],
                "hadir": 0,
                "alfa": 0,
                "sakit": 0,
                "izin": 0,
                "haid": 0,
                "istihadhoh": 0,
                "masbuq": 0,
            }

        status_val = a.get("status")
        if status_val in ["hadir", "alfa", "sakit", "izin", "haid", "istihadhoh", "masbuq"]:
            groups[key][status_val] += 1

    items = list(groups.values())
    # Urutkan berdasarkan tanggal lalu waktu sholat
    items.sort(key=lambda x: (x["tanggal"], x["waktu_sholat"]))

    return {"items": items}


# ==================== PEMBIMBING ENDPOINTS (REVISED - Kode Akses) ====================

@api_router.get("/pembimbing", response_model=List[PembimbingResponse])
async def get_pembimbing(_: dict = Depends(get_current_admin)):
    raw_list = await db.pembimbing.find({}, {"_id": 0}).to_list(1000)

    normalized: List[PembimbingResponse] = []
    for pembimbing in raw_list:
        # Backward compatibility untuk data lama
        if 'email_atau_hp' not in pembimbing:
            pembimbing['email_atau_hp'] = ''
        if 'asrama_ids' not in pembimbing:
            if 'asrama_id' in pembimbing:
                pembimbing['asrama_ids'] = [pembimbing['asrama_id']]
            else:
                pembimbing['asrama_ids'] = []
        # Migrate from password_hash to kode_akses if needed
        if 'kode_akses' not in pembimbing:
            pembimbing['kode_akses'] = generate_kode_akses()
            await db.pembimbing.update_one({"id": pembimbing['id']}, {"$set": {"kode_akses": pembimbing['kode_akses']}})

        if isinstance(pembimbing.get('created_at'), str):
            pembimbing['created_at'] = datetime.fromisoformat(pembimbing['created_at'])

        # Filter out password_hash if exists (old data)
        data = {k: v for k, v in pembimbing.items() if k != 'password_hash'}
        normalized.append(PembimbingResponse(**data))

    return normalized

@api_router.post("/pembimbing", response_model=PembimbingResponse)
async def create_pembimbing(data: PembimbingCreate, _: dict = Depends(get_current_admin)):
    existing = await db.pembimbing.find_one({"username": data.username})
    if existing:
        raise HTTPException(status_code=400, detail="Username sudah digunakan")
    
    pembimbing_dict = data.model_dump()
    pembimbing_dict['kode_akses'] = generate_kode_akses()  # Auto-generate kode akses
    
    pembimbing_obj = Pembimbing(**pembimbing_dict)
    doc = pembimbing_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.pembimbing.insert_one(doc)
    
    return PembimbingResponse(**pembimbing_obj.model_dump())

@api_router.put("/pembimbing/{pembimbing_id}", response_model=PembimbingResponse)
async def update_pembimbing(pembimbing_id: str, data: PembimbingUpdate, _: dict = Depends(get_current_admin)):
    pembimbing = await db.pembimbing.find_one({"id": pembimbing_id}, {"_id": 0})
    if not pembimbing:
        raise HTTPException(status_code=404, detail="Pembimbing tidak ditemukan")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    
    if update_data:
        await db.pembimbing.update_one({"id": pembimbing_id}, {"$set": update_data})
        pembimbing.update(update_data)
    
    # Ensure kode_akses exists
    if 'kode_akses' not in pembimbing:
        pembimbing['kode_akses'] = generate_kode_akses()
        await db.pembimbing.update_one({"id": pembimbing_id}, {"$set": {"kode_akses": pembimbing['kode_akses']}})
    
    if isinstance(pembimbing['created_at'], str):
        pembimbing['created_at'] = datetime.fromisoformat(pembimbing['created_at'])
    
    return PembimbingResponse(**{k: v for k, v in pembimbing.items() if k != 'password_hash'})

@api_router.post("/pembimbing/{pembimbing_id}/regenerate-kode-akses", response_model=PembimbingResponse)
async def regenerate_kode_akses(pembimbing_id: str, _: dict = Depends(get_current_admin)):
    """Regenerate kode akses untuk pembimbing"""
    pembimbing = await db.pembimbing.find_one({"id": pembimbing_id}, {"_id": 0})
    if not pembimbing:
        raise HTTPException(status_code=404, detail="Pembimbing tidak ditemukan")
    
    new_kode = generate_kode_akses()
    await db.pembimbing.update_one({"id": pembimbing_id}, {"$set": {"kode_akses": new_kode}})
    pembimbing['kode_akses'] = new_kode
    
    if isinstance(pembimbing['created_at'], str):
        pembimbing['created_at'] = datetime.fromisoformat(pembimbing['created_at'])
    
    return PembimbingResponse(**{k: v for k, v in pembimbing.items() if k != 'password_hash'})

@api_router.delete("/pembimbing/{pembimbing_id}")
async def delete_pembimbing(pembimbing_id: str, _: dict = Depends(get_current_admin)):
    result = await db.pembimbing.delete_one({"id": pembimbing_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Pembimbing tidak ditemukan")
    return {"message": "Pembimbing berhasil dihapus"}


# ==================== PEMBIMBING PWA ENDPOINTS ====================

async def get_current_pembimbing(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        pembimbing_id: str = payload.get("sub")
        if pembimbing_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")

        pembimbing = await db.pembimbing.find_one({"id": pembimbing_id}, {"_id": 0})
        if pembimbing is None:
            raise HTTPException(status_code=401, detail="Pembimbing tidak ditemukan")

        return pembimbing
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")


@api_router.post("/pembimbing/login", response_model=PembimbingTokenResponse)
async def login_pembimbing(request: PembimbingLoginRequest):
    """Login pembimbing using username and kode_akses"""
    pembimbing = await db.pembimbing.find_one({"username": request.username}, {"_id": 0})

    if not pembimbing:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau kode akses salah"
        )
    
    # Verify kode_akses
    stored_kode = pembimbing.get("kode_akses", "")
    if request.kode_akses != stored_kode:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau kode akses salah"
        )

    access_token = create_access_token(data={"sub": pembimbing['id'], "role": "pembimbing"})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": PembimbingMeResponse(**{k: v for k, v in pembimbing.items() if k not in ['password_hash', 'kode_akses']})
    }


@api_router.get("/pembimbing/me", response_model=PembimbingMeResponse)
async def get_pembimbing_me(current_pembimbing: dict = Depends(get_current_pembimbing)):
    return PembimbingMeResponse(**{k: v for k, v in current_pembimbing.items() if k not in ['password_hash', 'kode_akses']})


@api_router.get("/pembimbing/santri-absensi-hari-ini")
async def get_pembimbing_santri_absensi_hari_ini(
    waktu_sholat: Optional[Literal["subuh", "dzuhur", "ashar", "maghrib", "isya"]] = None,
    current_pembimbing: dict = Depends(get_current_pembimbing)
):
    """Get today's attendance for santri in pembimbing's asrama"""
    today = get_today_local_iso()
    
    asrama_ids = current_pembimbing.get('asrama_ids', [])
    if not asrama_ids:
        return {"tanggal": today, "waktu_sholat": waktu_sholat, "data": []}
    
    # Get all santri in pembimbing's asrama
    santri_list = await db.santri.find({"asrama_id": {"$in": asrama_ids}}, {"_id": 0}).to_list(10000)
    santri_by_id = {s['id']: s for s in santri_list}
    
    # Get attendance for today
    absensi_query = {"tanggal": today, "santri_id": {"$in": list(santri_by_id.keys())}}
    if waktu_sholat:
        absensi_query["waktu_sholat"] = waktu_sholat
    
    absensi_list = await db.absensi.find(absensi_query, {"_id": 0}).to_list(10000)
    
    # Build status map: santri_id -> {waktu_sholat: status}
    status_by_santri = {sid: {} for sid in santri_by_id.keys()}
    for a in absensi_list:
        sid = a['santri_id']
        ws = a['waktu_sholat']
        if sid in status_by_santri:
            status_by_santri[sid][ws] = a['status']
    
    # Get asrama names
    asrama_map = {a['id']: a['nama'] for a in await db.asrama.find({}, {"_id": 0}).to_list(1000)}
    
    # Get pengabsen names
    pengabsen_map = {
        p["id"]: p.get("nama", "-")
        for p in await db.pengabsen.find({}, {"_id": 0, "id": 1, "nama": 1}).to_list(1000)
    }
    
    # Build pengabsen per santri (last entry)
    pengabsen_by_santri = {}
    for a in absensi_list:
        sid = a.get("santri_id")
        pid = a.get("pengabsen_id")
        if sid in santri_by_id and pid:
            pengabsen_by_santri[sid] = pengabsen_map.get(pid, "-")
    
    result = []
    for sid, santri in santri_by_id.items():
        result.append({
            "santri_id": sid,
            "nama": santri['nama'],
            "nis": santri['nis'],
            "asrama_id": santri['asrama_id'],
            "nama_asrama": asrama_map.get(santri['asrama_id'], "-"),
            "status": status_by_santri.get(sid, {}),
            "pengabsen_nama": pengabsen_by_santri.get(sid)
        })
    
    # Sort by asrama then name
    result.sort(key=lambda x: (x['nama_asrama'], x['nama']))
    
    return {"tanggal": today, "waktu_sholat": waktu_sholat, "data": result}


@api_router.get("/pembimbing/absensi-riwayat")
async def get_pembimbing_absensi_riwayat(
    tanggal: str,
    waktu_sholat: Optional[Literal["subuh", "dzuhur", "ashar", "maghrib", "isya"]] = None,
    asrama_id: Optional[str] = None,
    current_pembimbing: dict = Depends(get_current_pembimbing)
):
    """Get historical attendance for a specific date"""
    asrama_ids = current_pembimbing.get('asrama_ids', [])
    if not asrama_ids:
        return {"tanggal": tanggal, "waktu_sholat": waktu_sholat, "data": []}
    
    # Filter by specific asrama if provided
    if asrama_id and asrama_id in asrama_ids:
        filter_asrama_ids = [asrama_id]
    else:
        filter_asrama_ids = asrama_ids
    
    # Get santri
    santri_list = await db.santri.find({"asrama_id": {"$in": filter_asrama_ids}}, {"_id": 0}).to_list(10000)
    santri_by_id = {s['id']: s for s in santri_list}
    
    # Get attendance
    absensi_query = {"tanggal": tanggal, "santri_id": {"$in": list(santri_by_id.keys())}}
    if waktu_sholat:
        absensi_query["waktu_sholat"] = waktu_sholat
    
    absensi_list = await db.absensi.find(absensi_query, {"_id": 0}).to_list(10000)
    
    # Build status map
    status_by_santri = {sid: {} for sid in santri_by_id.keys()}
    for a in absensi_list:
        sid = a['santri_id']
        ws = a['waktu_sholat']
        if sid in status_by_santri:
            status_by_santri[sid][ws] = a['status']
    
    asrama_map = {a['id']: a['nama'] for a in await db.asrama.find({}, {"_id": 0}).to_list(1000)}
    
    pengabsen_map = {
        p["id"]: p.get("nama", "-")
        for p in await db.pengabsen.find({}, {"_id": 0, "id": 1, "nama": 1}).to_list(1000)
    }
    
    pengabsen_by_santri = {}
    for a in absensi_list:
        sid = a.get("santri_id")
        pid = a.get("pengabsen_id")
        if sid in santri_by_id and pid:
            pengabsen_by_santri[sid] = pengabsen_map.get(pid, "-")
    
    result = []
    for sid, santri in santri_by_id.items():
        result.append({
            "santri_id": sid,
            "nama": santri['nama'],
            "nis": santri['nis'],
            "asrama_id": santri['asrama_id'],
            "nama_asrama": asrama_map.get(santri['asrama_id'], "-"),
            "status": status_by_santri.get(sid, {}),
            "pengabsen_nama": pengabsen_by_santri.get(sid)
        })
    
    result.sort(key=lambda x: (x['nama_asrama'], x['nama']))
    
    return {"tanggal": tanggal, "waktu_sholat": waktu_sholat, "data": result}


@api_router.get("/pembimbing/statistik")
async def get_pembimbing_statistik(
    tanggal: str,
    current_pembimbing: dict = Depends(get_current_pembimbing)
):
    """Get attendance statistics for a specific date"""
    asrama_ids = current_pembimbing.get('asrama_ids', [])
    if not asrama_ids:
        return {"tanggal": tanggal, "total_santri": 0, "stats": {}}
    
    # Get santri count
    santri_list = await db.santri.find({"asrama_id": {"$in": asrama_ids}}, {"_id": 0, "id": 1}).to_list(10000)
    santri_ids = [s['id'] for s in santri_list]
    total_santri = len(santri_ids)
    
    # Get attendance stats
    absensi_list = await db.absensi.find(
        {"tanggal": tanggal, "santri_id": {"$in": santri_ids}},
        {"_id": 0}
    ).to_list(10000)
    
    # Calculate stats per waktu sholat
    waktu_list = ["subuh", "dzuhur", "ashar", "maghrib", "isya"]
    stats = {}
    for waktu in waktu_list:
        waktu_absensi = [a for a in absensi_list if a['waktu_sholat'] == waktu]
        stats[waktu] = {
            "hadir": len([a for a in waktu_absensi if a['status'] == 'hadir']),
            "alfa": len([a for a in waktu_absensi if a['status'] == 'alfa']),
            "sakit": len([a for a in waktu_absensi if a['status'] == 'sakit']),
            "izin": len([a for a in waktu_absensi if a['status'] == 'izin']),
            "haid": len([a for a in waktu_absensi if a['status'] == 'haid']),
            "istihadhoh": len([a for a in waktu_absensi if a['status'] == 'istihadhoh']),
            "masbuq": len([a for a in waktu_absensi if a['status'] == 'masbuq']),
            "belum": total_santri - len(waktu_absensi)
        }
    
    return {"tanggal": tanggal, "total_santri": total_santri, "stats": stats}


# ==================== ABSENSI ENDPOINTS (REVISED) ====================

@api_router.get("/absensi", response_model=List[AbsensiResponse])
async def get_absensi(
    tanggal_start: Optional[str] = None,
    tanggal_end: Optional[str] = None,
    santri_id: Optional[str] = None,
    waktu_sholat: Optional[str] = None,
    asrama_id: Optional[str] = None,
    gender: Optional[str] = None,
    _: dict = Depends(get_current_admin)
):
    """Get absensi with advanced filters"""
    query = {}
    
    # Date range filter
    if tanggal_start and tanggal_end:
        query['tanggal'] = {"$gte": tanggal_start, "$lte": tanggal_end}
    elif tanggal_start:
        query['tanggal'] = tanggal_start
    
    if santri_id:
        query['santri_id'] = santri_id
    if waktu_sholat:
        query['waktu_sholat'] = waktu_sholat
    
    absensi_list = await db.absensi.find(query, {"_id": 0}).to_list(10000)
    
    # Filter by asrama or gender if needed
    if asrama_id or gender:
        santri_query = {}
        if asrama_id:
            santri_query['asrama_id'] = asrama_id
        if gender:
            santri_query['gender'] = gender
        
        santri_ids = [s['id'] for s in await db.santri.find(santri_query, {"_id": 0, "id": 1}).to_list(10000)]
        absensi_list = [a for a in absensi_list if a['santri_id'] in santri_ids]
    
    for absensi in absensi_list:
        if isinstance(absensi['waktu_absen'], str):
            absensi['waktu_absen'] = datetime.fromisoformat(absensi['waktu_absen'])
        if isinstance(absensi['created_at'], str):
            absensi['created_at'] = datetime.fromisoformat(absensi['created_at'])
    
    return absensi_list

@api_router.get("/absensi/stats")
async def get_absensi_stats(
    tanggal_start: Optional[str] = None,
    tanggal_end: Optional[str] = None,
    asrama_id: Optional[str] = None,
    gender: Optional[str] = None,
    _: dict = Depends(get_current_admin)
):
    """Get absensi statistics with filters"""
    query = {}
    
    if tanggal_start and tanggal_end:
        query['tanggal'] = {"$gte": tanggal_start, "$lte": tanggal_end}
    elif tanggal_start:
        query['tanggal'] = tanggal_start
    
    # Filter by asrama or gender
    if asrama_id or gender:
        santri_query = {}
        if asrama_id:
            santri_query['asrama_id'] = asrama_id
        if gender:
            santri_query['gender'] = gender
        
        santri_ids = [s['id'] for s in await db.santri.find(santri_query, {"_id": 0, "id": 1}).to_list(10000)]
        query['santri_id'] = {"$in": santri_ids}
    
    total = await db.absensi.count_documents(query)
    hadir = await db.absensi.count_documents({**query, "status": "hadir"})
    alfa = await db.absensi.count_documents({**query, "status": "alfa"})
    sakit = await db.absensi.count_documents({**query, "status": "sakit"})
    izin = await db.absensi.count_documents({**query, "status": "izin"})
    haid = await db.absensi.count_documents({**query, "status": "haid"})
    istihadhoh = await db.absensi.count_documents({**query, "status": "istihadhoh"})
    masbuq = await db.absensi.count_documents({**query, "status": "masbuq"})
    
    return {
        "total": total,
        "hadir": hadir,
        "alfa": alfa,
        "sakit": sakit,
        "izin": izin,
        "haid": haid,
        "istihadhoh": istihadhoh,
        "masbuq": masbuq,
    }

@api_router.get("/absensi/detail")
async def get_absensi_detail(
    tanggal: str,
    asrama_id: Optional[str] = None,
    gender: Optional[str] = None,
    _: dict = Depends(get_current_admin)
):
    """Get detailed absensi per waktu sholat for specific date (legacy endpoint)."""
    # Gunakan endpoint baru /absensi/riwayat di belakang layar untuk menjaga satu sumber logika
    result = await get_absensi_riwayat(
        tanggal_start=tanggal,
        tanggal_end=tanggal,
        asrama_id=asrama_id,
        gender=gender,
        _=_,
    )
    return result
@api_router.get("/absensi/riwayat")
async def get_absensi_riwayat(
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    asrama_id: Optional[str] = None,
    gender: Optional[str] = None,
    _: dict = Depends(get_current_admin),
):
    """Riwayat absensi sholat untuk admin (ringkasan & detail, rentang tanggal)."""

    if not tanggal_end:
        tanggal_end = tanggal_start

    # Get all santri with filters
    santri_query: Dict[str, Any] = {}
    if asrama_id:
        santri_query["asrama_id"] = asrama_id
    if gender:
        santri_query["gender"] = gender

    all_santri = await db.santri.find(santri_query, {"_id": 0}).to_list(10000)
    santri_dict = {s["id"]: s for s in all_santri}

    # Get absensi for the date range
    absensi_query = {"tanggal": {"$gte": tanggal_start, "$lte": tanggal_end}}
    absensi_list = await db.absensi.find(absensi_query, {"_id": 0}).to_list(10000)

    # Map pengabsen_id -> nama
    pengabsen_map = {
        p["id"]: p.get("nama", "-")
        for p in await db.pengabsen.find({}, {"_id": 0, "id": 1, "nama": 1}).to_list(1000)
    }

    # Organize by waktu sholat and status
    waktu_sholat_list = ["subuh", "dzuhur", "ashar", "maghrib", "isya"]
    status_list = ["hadir", "alfa", "sakit", "izin", "haid", "istihadhoh", "masbuq"]

    # Initialize summary
    summary = {
        "total_records": len(absensi_list),
        "by_waktu": {},
    }

    for waktu in waktu_sholat_list:
        summary["by_waktu"][waktu] = {}
        for st in status_list:
            summary["by_waktu"][waktu][st] = 0

    # Initialize detail structure
    detail: Dict[str, Dict[str, list]] = {}
    for waktu in waktu_sholat_list:
        detail[waktu] = {}
        for st in status_list:
            detail[waktu][st] = []

    # Process absensi data
    for a in absensi_list:
        waktu = a.get("waktu_sholat")
        status = a.get("status")
        santri_id = a.get("santri_id")

        if (
            waktu in waktu_sholat_list
            and status in status_list
            and santri_id in santri_dict
        ):
            # Update summary
            summary["by_waktu"][waktu][status] += 1

            # Add to detail
            pengabsen_id = a.get("pengabsen_id")
            santri = santri_dict[santri_id]

            detail[waktu][status].append(
                {
                    "santri_id": santri_id,
                    "nama": santri["nama"],
                    "nis": santri["nis"],
                    "asrama_id": santri["asrama_id"],
                    "tanggal": a.get("tanggal"),
                    "pengabsen_id": pengabsen_id,
                    "pengabsen_nama": pengabsen_map.get(pengabsen_id, "-")
                    if pengabsen_id
                    else "-",
                }
            )

    return {"summary": summary, "detail": detail}


@api_router.get("/whatsapp/rekap", response_model=List[WhatsAppRekapItem])
async def get_whatsapp_rekap(
    tanggal: str,
    asrama_id: Optional[str] = None,
    gender: Optional[str] = None,
    q: Optional[str] = None,
    _: dict = Depends(get_current_admin),
):
    if not tanggal:
        raise HTTPException(status_code=400, detail="Tanggal wajib diisi")

    santri_query: Dict[str, Any] = {}
    if asrama_id:
        santri_query["asrama_id"] = asrama_id
    if gender:
        santri_query["gender"] = gender
    if q:
        santri_query["$or"] = [
            {"nama": {"$regex": q, "$options": "i"}},
            {"nis": {"$regex": q, "$options": "i"}},
        ]

    santri_list = await db.santri.find(santri_query, {"_id": 0}).to_list(10000)
    if not santri_list:
        return []

    sent_records = await db.whatsapp_history.find({"tanggal": tanggal}, {"_id": 0, "santri_id": 1}).to_list(10000)
    sent_ids = {s["santri_id"] for s in sent_records}
    santri_list = [s for s in santri_list if s["id"] not in sent_ids]
    if not santri_list:
        return []

    santri_ids = [s["id"] for s in santri_list]
    absensi_list = await db.absensi.find(
        {"tanggal": tanggal, "santri_id": {"$in": santri_ids}},
        {"_id": 0, "santri_id": 1, "waktu_sholat": 1, "status": 1},
    ).to_list(10000)

    absensi_map = {(a["santri_id"], a["waktu_sholat"]): a.get("status", "belum") for a in absensi_list}

    asrama_ids = list({s["asrama_id"] for s in santri_list})
    asrama_list = await db.asrama.find({"id": {"$in": asrama_ids}}, {"_id": 0, "id": 1, "nama": 1}).to_list(10000)
    asrama_map = {a["id"]: a.get("nama", "-") for a in asrama_list}

    waktu_order = ["dzuhur", "ashar", "maghrib", "isya", "subuh"]
    result: List[WhatsAppRekapItem] = []
    for s in santri_list:
        rekap = {w: absensi_map.get((s["id"], w), "belum") for w in waktu_order}
        result.append(
            WhatsAppRekapItem(
                santri_id=s["id"],
                nama_santri=s["nama"],
                nama_wali=s.get("nama_wali", "-"),
                nomor_hp_wali=s.get("nomor_hp_wali", ""),
                asrama_id=s.get("asrama_id", ""),
                asrama_nama=asrama_map.get(s.get("asrama_id", ""), "-"),
                gender=s.get("gender", ""),
                rekap=rekap,
            )
        )

    return result


@api_router.post("/whatsapp/rekap/send", response_model=WhatsAppHistoryItem)
async def record_whatsapp_send(
    payload: WhatsAppSendRequest,
    current_admin: dict = Depends(get_current_admin),
):
    today = get_today_local_iso()
    if payload.tanggal >= today:
        raise HTTPException(status_code=400, detail="Rekap hanya dapat dikirim untuk hari sebelumnya")

    existing = await db.whatsapp_history.find_one(
        {"santri_id": payload.santri_id, "tanggal": payload.tanggal},
        {"_id": 0},
    )
    if existing:
        if isinstance(existing.get("sent_at"), str):
            existing["sent_at"] = datetime.fromisoformat(existing["sent_at"])
        return WhatsAppHistoryItem(**existing)

    santri = await db.santri.find_one({"id": payload.santri_id}, {"_id": 0})
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    asrama = await db.asrama.find_one({"id": santri.get("asrama_id")}, {"_id": 0})
    asrama_nama = asrama.get("nama", "-") if asrama else "-"

    absensi_list = await db.absensi.find(
        {"tanggal": payload.tanggal, "santri_id": payload.santri_id},
        {"_id": 0, "waktu_sholat": 1, "status": 1},
    ).to_list(100)
    absensi_map = {a.get("waktu_sholat"): a.get("status", "belum") for a in absensi_list}
    waktu_order = ["dzuhur", "ashar", "maghrib", "isya", "subuh"]
    rekap = {w: absensi_map.get(w, "belum") for w in waktu_order}

    record = {
        "id": str(uuid.uuid4()),
        "santri_id": santri["id"],
        "nama_santri": santri.get("nama", "-"),
        "nama_wali": santri.get("nama_wali", "-"),
        "nomor_hp_wali": santri.get("nomor_hp_wali", ""),
        "asrama_id": santri.get("asrama_id", ""),
        "asrama_nama": asrama_nama,
        "gender": santri.get("gender", ""),
        "tanggal": payload.tanggal,
        "sent_at": datetime.now(timezone.utc),
        "admin_id": current_admin.get("id", ""),
        "admin_nama": current_admin.get("nama") or current_admin.get("username", "admin"),
        "rekap": rekap,
    }

    await db.whatsapp_history.insert_one({**record, "sent_at": record["sent_at"].isoformat()})
    return WhatsAppHistoryItem(**record)


@api_router.get("/whatsapp/history", response_model=List[WhatsAppHistoryItem])
async def get_whatsapp_history(
    tanggal: Optional[str] = None,
    asrama_id: Optional[str] = None,
    gender: Optional[str] = None,
    q: Optional[str] = None,
    _: dict = Depends(get_current_admin),
):
    query: Dict[str, Any] = {}
    if tanggal:
        query["tanggal"] = tanggal
    if asrama_id:
        query["asrama_id"] = asrama_id
    if gender:
        query["gender"] = gender
    if q:
        query["nama_santri"] = {"$regex": q, "$options": "i"}

    records = await db.whatsapp_history.find(query, {"_id": 0}).sort("sent_at", -1).to_list(10000)
    for rec in records:
        if isinstance(rec.get("sent_at"), str):
            rec["sent_at"] = datetime.fromisoformat(rec["sent_at"])
        rec.setdefault("nama_wali", "-")
        rec.setdefault("nomor_hp_wali", "")
        rec.setdefault("rekap", {})
    return records


@api_router.post("/whatsapp/history/resend", response_model=WhatsAppHistoryItem)
async def resend_whatsapp_history(
    payload: WhatsAppResendRequest,
    current_admin: dict = Depends(get_current_admin),
):
    record = await db.whatsapp_history.find_one({"id": payload.history_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="History tidak ditemukan")

    if not record.get("nomor_hp_wali") or not record.get("rekap"):
        santri = await db.santri.find_one({"id": record.get("santri_id")}, {"_id": 0})
        if santri:
            record["nama_wali"] = santri.get("nama_wali", record.get("nama_wali", "-"))
            record["nomor_hp_wali"] = santri.get("nomor_hp_wali", record.get("nomor_hp_wali", ""))
            absensi_list = await db.absensi.find(
                {"tanggal": record.get("tanggal"), "santri_id": record.get("santri_id")},
                {"_id": 0, "waktu_sholat": 1, "status": 1},
            ).to_list(100)
            absensi_map = {a.get("waktu_sholat"): a.get("status", "belum") for a in absensi_list}
            waktu_order = ["dzuhur", "ashar", "maghrib", "isya", "subuh"]
            record["rekap"] = {w: absensi_map.get(w, "belum") for w in waktu_order}

    updated = {
        "sent_at": datetime.now(timezone.utc),
        "admin_id": current_admin.get("id", ""),
        "admin_nama": current_admin.get("nama") or current_admin.get("username", "admin"),
        "nama_wali": record.get("nama_wali", "-"),
        "nomor_hp_wali": record.get("nomor_hp_wali", ""),
        "rekap": record.get("rekap", {}),
    }

    await db.whatsapp_history.update_one(
        {"id": payload.history_id},
        {"$set": {**updated, "sent_at": updated["sent_at"].isoformat()}},
    )
    record.update(updated)
    return WhatsAppHistoryItem(**record)


@api_router.get("/pmq/settings/waktu")
async def get_pmq_waktu_settings(_: dict = Depends(get_current_admin)):
    settings = await db.settings.find_one({"id": "pmq_waktu"}, {"_id": 0})
    if not settings:
        return PMQWaktuSettings().model_dump()
    return settings


@api_router.get("/pmq/pengabsen/settings/waktu")
async def get_pmq_waktu_settings_for_pengabsen(_: dict = Depends(get_current_pengabsen_pmq)):
    """Endpoint untuk PWA pengabsen PMQ mengambil daftar sesi waktu.

    Menggunakan setting yang sama dengan admin, tetapi auth-nya via pengabsen PMQ.
    """
    settings = await db.settings.find_one({"id": "pmq_waktu"}, {"_id": 0})
    if not settings:
        return PMQWaktuSettings().model_dump()
    return settings


@api_router.put("/pmq/settings/waktu")
async def update_pmq_waktu_settings(data: PMQWaktuSettings, _: dict = Depends(get_current_admin)):
    payload = data.model_dump()
    payload["id"] = "pmq_waktu"
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    await db.settings.update_one({"id": "pmq_waktu"}, {"$set": payload}, upsert=True)
    return {"message": "Pengaturan waktu PMQ berhasil disimpan"}


@api_router.delete("/absensi/{absensi_id}")
async def delete_absensi(absensi_id: str, _: dict = Depends(get_current_admin)):
    result = await db.absensi.delete_one({"id": absensi_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Data absensi tidak ditemukan")
    return {"message": "Data absensi berhasil dihapus"}

# ==================== WAKTU SHOLAT ENDPOINTS ====================

@api_router.get("/waktu-sholat", response_model=WaktuSholatResponse)
async def get_waktu_sholat(tanggal: str, _: dict = Depends(get_current_admin)):
    waktu = await db.waktu_sholat.find_one({"tanggal": tanggal}, {"_id": 0})
    
    if waktu:
        if isinstance(waktu['created_at'], str):
            waktu['created_at'] = datetime.fromisoformat(waktu['created_at'])
        return WaktuSholatResponse(**waktu)
    
    # Fetch from API
    date_parts = tanggal.split('-')
    api_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
    
    prayer_times = await fetch_prayer_times(api_date)
    if not prayer_times:
        raise HTTPException(status_code=500, detail="Gagal mengambil data waktu sholat")
    
    waktu_obj = WaktuSholat(tanggal=tanggal, **prayer_times)
    doc = waktu_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.waktu_sholat.insert_one(doc)
    
    return WaktuSholatResponse(**waktu_obj.model_dump())

@api_router.post("/waktu-sholat/sync")
async def sync_waktu_sholat(tanggal: str, _: dict = Depends(get_current_admin)):
    date_parts = tanggal.split('-')
    api_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
    
    prayer_times = await fetch_prayer_times(api_date)
    if not prayer_times:
        raise HTTPException(status_code=500, detail="Gagal mengambil data waktu sholat dari API")
    
    await db.waktu_sholat.delete_one({"tanggal": tanggal})
    
    waktu_obj = WaktuSholat(tanggal=tanggal, **prayer_times)
    doc = waktu_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.waktu_sholat.insert_one(doc)
    
    return WaktuSholatResponse(**waktu_obj.model_dump())




# ==================== PENGATURAN ABSENSI PAGI ALIYAH ====================

class AliyahAbsensiPagiSettings(BaseModel):
    id: str = Field(default="aliyah_absensi_pagi")
    start_time: str = Field(default="06:30")  # HH:MM
    end_time: str = Field(default="07:15")  # HH:MM
    updated_at: Optional[str] = None


@api_router.get("/aliyah/settings/absensi-pagi")
async def get_aliyah_absensi_pagi_settings():
    settings = await db.settings.find_one({"id": "aliyah_absensi_pagi"}, {"_id": 0})
    if not settings:
        return AliyahAbsensiPagiSettings().model_dump()
    return settings


@api_router.put("/aliyah/settings/absensi-pagi")
async def update_aliyah_absensi_pagi_settings(data: AliyahAbsensiPagiSettings, _: dict = Depends(get_current_admin)):
    payload = data.model_dump()
    payload["id"] = "aliyah_absensi_pagi"
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    await db.settings.update_one({"id": "aliyah_absensi_pagi"}, {"$set": payload}, upsert=True)
    return {"message": "Pengaturan absensi pagi Aliyah berhasil disimpan"}


# ==================== SETTINGS ENDPOINTS ====================

class WaliNotifikasiSettings(BaseModel):
    hadir: str = "{nama} hadir pada waktu sholat {waktu} hari ini, alhamdulillah (hadir)"
    alfa: str = "{nama} tidak mengikuti/membolos sholat {waktu} pada hari ini (alfa)"
    sakit: str = "{nama} tidak mengikuti sholat {waktu} pada hari ini karena sedang sakit (sakit)"
    izin: str = "{nama} tidak mengikuti sholat {waktu} pada hari ini karena izin (izin)"
    haid: str = "{nama} tidak mengikuti sholat {waktu} pada hari ini karena sedang haid (haid)"
    istihadhoh: str = "{nama} tidak mengikuti sholat {waktu} pada hari ini karena sedang istihadhoh (istihadhoh)"


class WhatsAppTemplateSettings(BaseModel):
    template: str


WHATSAPP_DEFAULT_TEMPLATE = (
    "Assalamu'alaikum {nama_wali}.\n"
    "Rekap sholat ananda {nama_santri} pada {tanggal}:\n"
    "Dzuhur: {dzuhur}\n"
    "Ashar: {ashar}\n"
    "Maghrib: {maghrib}\n"
    "Isya: {isya}\n"
    "Subuh: {subuh}\n"
    "Terima kasih."
)


# App Settings Models
class AppSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default="default")
    admin_title: str = Field(default="Admin Panel Absensi Santri Dan Siswa")
    wali_title: str = Field(default="Wali Santri Ponpes Al-Hamid")
    pengabsen_title: str = Field(default="Pengabsen Sholat Ponpes Al-Hamid")
    pembimbing_title: str = Field(default="Monitoring Sholat Ponpes Al-Hamid")
    pengabsen_kelas_title: str = Field(default="Pengabsen Kelas Madin")
    monitoring_kelas_title: str = Field(default="Monitoring Kelas Madin")
    pengabsen_aliyah_title: str = Field(default="Pengabsen Kelas Aliyah")
    monitoring_aliyah_title: str = Field(default="Monitoring Kelas Aliyah")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AppSettingsUpdate(BaseModel):
    admin_title: Optional[str] = None
    wali_title: Optional[str] = None
    pengabsen_title: Optional[str] = None
    pembimbing_title: Optional[str] = None
    pengabsen_kelas_title: Optional[str] = None
    monitoring_kelas_title: Optional[str] = None
    pengabsen_aliyah_title: Optional[str] = None
    monitoring_aliyah_title: Optional[str] = None


@api_router.get("/settings/wali-notifikasi")
async def get_wali_notifikasi_settings(_: dict = Depends(get_current_admin)):
    """Get notification template settings for Wali Santri"""
    settings = await db.settings.find_one({"id": "wali_notifikasi"}, {"_id": 0})
    if not settings:
        # Return defaults
        return WaliNotifikasiSettings().model_dump()
    
    return {
        "hadir": settings.get("hadir", WaliNotifikasiSettings().hadir),
        "alfa": settings.get("alfa", WaliNotifikasiSettings().alfa),
        "sakit": settings.get("sakit", WaliNotifikasiSettings().sakit),
        "izin": settings.get("izin", WaliNotifikasiSettings().izin),
        "haid": settings.get("haid", WaliNotifikasiSettings().haid),
        "istihadhoh": settings.get("istihadhoh", WaliNotifikasiSettings().istihadhoh),
    }


@api_router.put("/settings/wali-notifikasi")
async def update_wali_notifikasi_settings(data: WaliNotifikasiSettings, _: dict = Depends(get_current_admin)):
    """Update notification template settings for Wali Santri"""
    settings_data = data.model_dump()
    settings_data["id"] = "wali_notifikasi"
    settings_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    await db.settings.update_one(
        {"id": "wali_notifikasi"},
        {"$set": settings_data},
        upsert=True
    )
    
    return {"message": "Pengaturan notifikasi berhasil disimpan"}


@api_router.get("/settings/whatsapp-template")
async def get_whatsapp_template_settings(_: dict = Depends(get_current_admin)):
    settings = await db.settings.find_one({"id": "whatsapp_template"}, {"_id": 0})
    if not settings:
        return {"template": WHATSAPP_DEFAULT_TEMPLATE}
    return {"template": settings.get("template", WHATSAPP_DEFAULT_TEMPLATE)}


@api_router.put("/settings/whatsapp-template")
async def update_whatsapp_template_settings(data: WhatsAppTemplateSettings, _: dict = Depends(get_current_admin)):
    payload = {
        "id": "whatsapp_template",
        "template": data.template,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.settings.update_one({"id": "whatsapp_template"}, {"$set": payload}, upsert=True)
    return {"message": "Template WhatsApp berhasil disimpan"}


# ==================== APP SETTINGS ENDPOINTS ====================

@api_router.get("/settings/app")
async def get_app_settings():
    """Get application title settings (public endpoint)"""
    settings = await db.settings.find_one({"id": "app_settings"}, {"_id": 0})
    if not settings:
        # Return defaults
        defaults = AppSettings()
        return defaults.model_dump()
    
    return {
        "id": settings.get("id", "default"),
        "admin_title": settings.get("admin_title", "Admin Panel Absensi Santri Dan Siswa"),
        "wali_title": settings.get("wali_title", "Wali Santri Ponpes Al-Hamid"),
        "pengabsen_title": settings.get("pengabsen_title", "Pengabsen Sholat Ponpes Al-Hamid"),
        "pembimbing_title": settings.get("pembimbing_title", "Monitoring Sholat Ponpes Al-Hamid"),
        "pengabsen_kelas_title": settings.get("pengabsen_kelas_title", "Pengabsen Kelas Madin"),
        "monitoring_kelas_title": settings.get("monitoring_kelas_title", "Monitoring Kelas Madin"),
        "pengabsen_aliyah_title": settings.get("pengabsen_aliyah_title", "Pengabsen Kelas Aliyah"),
        "monitoring_aliyah_title": settings.get("monitoring_aliyah_title", "Monitoring Kelas Aliyah"),
        "updated_at": settings.get("updated_at", datetime.now(timezone.utc).isoformat())
    }


@api_router.put("/settings/app")
async def update_app_settings(data: AppSettingsUpdate, _: dict = Depends(get_current_admin)):
    """Update application title settings (admin only)"""
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No data to update")
    
    update_data["id"] = "app_settings"
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    await db.settings.update_one(
        {"id": "app_settings"},
        {"$set": update_data},
        upsert=True
    )
    
    return {"message": "App settings berhasil diupdate"}


async def ensure_admin_account(username: str, nama: str, password: str, role: str) -> None:
    """Ensure an admin account exists with given username and role.

    - If not exists: create new admin with given password and role.
    - If exists: ensure it has the correct role and a valid password hash.
    """
    admin = await db.admins.find_one({"username": username})
    password_hash = hash_password(password)

    if not admin:
        admin_obj = Admin(
            username=username,
            nama=nama,
            password_hash=password_hash,
            role=role,
        )
        doc = admin_obj.model_dump()
        # Store datetime as ISO string for MongoDB
        doc["created_at"] = doc["created_at"].isoformat()
        await db.admins.insert_one(doc)
        return

    updates: Dict[str, Any] = {}

    # Pastikan role sesuai
    if admin.get("role") != role:
        updates["role"] = role

    # Pastikan password hash valid untuk password yang diinginkan
    existing_hash = admin.get("password_hash")
    if existing_hash:
        try:
            if not verify_password(password, existing_hash):
                updates["password_hash"] = password_hash
        except Exception:
            # Jika hash lama tidak valid, ganti dengan hash baru
            updates["password_hash"] = password_hash
    else:
        updates["password_hash"] = password_hash

    if updates:
        await db.admins.update_one({"id": admin["id"]}, {"$set": updates})



# ==================== INITIALIZATION ====================

@api_router.get("/")
async def root():
    return {"message": "Absensi Sholat API - Admin Panel v2"}

@api_router.post("/init/admin")
async def initialize_admin():
    """Inisialisasi akun-akun admin utama.

    Endpoint ini aman dipanggil berulang kali (idempotent). Ia akan:
    - Memastikan akun lama `admin/admin123` tetap ada sebagai superadmin.
    - Membuat / mengupdate 4 akun baru utama:
      * alhamidcintamulya / alhamidku123 -> superadmin
      * alhamid / alhamidku123 -> pesantren
      * madin / madinku123 -> madin
      * aliyah / aliyah123 -> aliyah (khusus modul Madrasah Aliyah)
    """
    # Pastikan akun default lama tetap ada, tapi jangan paksa ganti password jika sudah diubah
    existing_default = await db.admins.find_one({"username": "admin"})
    if not existing_default:
        await ensure_admin_account(
            username="admin",
            nama="Administrator",
            password="admin123",
            role="superadmin",
        )
    else:
        if existing_default.get("role") != "superadmin":
            await db.admins.update_one(
                {"id": existing_default["id"]},
                {"$set": {"role": "superadmin"}},
            )

    # Akun-akun baru sesuai requirement
    await ensure_admin_account(
        username="alhamidcintamulya",
        nama="Super Admin Pesantren",
        password="alhamidku123",
        role="superadmin",
    )
    await ensure_admin_account(
        username="alhamid",
        nama="Admin Pesantren",
        password="alhamidku123",
        role="pesantren",
    )
    await ensure_admin_account(
        username="madin",
        nama="Admin Madrasah Diniyah",
        password="madinku123",
        role="madin",
    )
    await ensure_admin_account(
        username="aliyah",
        nama="Admin Madrasah Aliyah",
        password="aliyah123",
        role="aliyah",
    )
    await ensure_admin_account(
        username="pmq",
        nama="Admin PMQ",
        password="pmq123",
        role="pmq",
    )

    return {"message": "Akun-akun admin berhasil diinisialisasi"}


# ==================== KELAS ENDPOINTS ====================

@api_router.get("/kelas", response_model=List[KelasResponse])
async def get_kelas_list(_: dict = Depends(get_current_admin)):
    kelas_list = await db.kelas.find({}, {"_id": 0}).to_list(1000)
    
    # Count students for each kelas
    result = []
    for kelas in kelas_list:
        jumlah_siswa = await db.siswa_madrasah.count_documents({"kelas_id": kelas["id"]})
        kelas_response = KelasResponse(**kelas, jumlah_siswa=jumlah_siswa)
        result.append(kelas_response)
    
    return result

@api_router.post("/kelas", response_model=KelasResponse)
async def create_kelas(data: KelasCreate, _: dict = Depends(get_current_admin)):
    kelas = Kelas(**data.model_dump())
    doc = kelas.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.kelas.insert_one(doc)
    
    return KelasResponse(**kelas.model_dump(), jumlah_siswa=0)

@api_router.get("/kelas/{kelas_id}", response_model=KelasResponse)
async def get_kelas_detail(kelas_id: str, _: dict = Depends(get_current_admin)):
    kelas = await db.kelas.find_one({"id": kelas_id}, {"_id": 0})
    if not kelas:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")
    
    jumlah_siswa = await db.siswa_madrasah.count_documents({"kelas_id": kelas_id})
    return KelasResponse(**kelas, jumlah_siswa=jumlah_siswa)

@api_router.put("/kelas/{kelas_id}", response_model=KelasResponse)
async def update_kelas(kelas_id: str, data: KelasUpdate, _: dict = Depends(get_current_admin)):
    kelas = await db.kelas.find_one({"id": kelas_id}, {"_id": 0})
    if not kelas:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if update_data:
        await db.kelas.update_one({"id": kelas_id}, {"$set": update_data})
    
    updated_kelas = await db.kelas.find_one({"id": kelas_id}, {"_id": 0})
    jumlah_siswa = await db.siswa_madrasah.count_documents({"kelas_id": kelas_id})
    return KelasResponse(**updated_kelas, jumlah_siswa=jumlah_siswa)

@api_router.delete("/kelas/{kelas_id}")
async def delete_kelas(kelas_id: str, _: dict = Depends(get_current_admin)):
    # Set siswa yang ada di kelas ini menjadi kelas_id = None
    await db.siswa_madrasah.update_many(
        {"kelas_id": kelas_id},
        {"$set": {"kelas_id": None}}
    )
    
    result = await db.kelas.delete_one({"id": kelas_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")
    
    return {"message": "Kelas berhasil dihapus"}

@api_router.get("/kelas/{kelas_id}/siswa", response_model=List[SiswaMadrasahResponse])
async def get_kelas_siswa(kelas_id: str, _: dict = Depends(get_current_admin)):
    kelas = await db.kelas.find_one({"id": kelas_id}, {"_id": 0})
    if not kelas:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")
    
    siswa_list = await db.siswa_madrasah.find({"kelas_id": kelas_id}, {"_id": 0}).to_list(1000)
    
    result = []
    for siswa in siswa_list:
        has_qr = False
        if siswa.get("santri_id"):
            # Linked to santri, has QR
            has_qr = True
        elif siswa.get("qr_code"):
            # Standalone with QR
            has_qr = True
        
        result.append(SiswaMadrasahResponse(
            **siswa,
            kelas_nama=kelas["nama"],
            has_qr=has_qr
        ))
    
    return result


# ==================== KELAS ALIYAH ENDPOINTS ====================

@api_router.get("/aliyah/kelas", response_model=List[KelasAliyahResponse])
async def get_kelas_aliyah_list(_: dict = Depends(get_current_admin)):
    kelas_list = await db.kelas_aliyah.find({}, {"_id": 0}).to_list(1000)

    result: List[KelasAliyahResponse] = []
    for kelas in kelas_list:
        jumlah_siswa = await db.siswa_aliyah.count_documents({"kelas_id": kelas["id"]})
        result.append(KelasAliyahResponse(**kelas, jumlah_siswa=jumlah_siswa))

    return result


@api_router.post("/aliyah/kelas", response_model=KelasAliyahResponse)
async def create_kelas_aliyah(data: KelasAliyahCreate, _: dict = Depends(get_current_admin)):
    kelas = KelasAliyah(**data.model_dump())
    doc = kelas.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    await db.kelas_aliyah.insert_one(doc)

    return KelasAliyahResponse(**kelas.model_dump(), jumlah_siswa=0)


@api_router.get("/aliyah/kelas/{kelas_id}", response_model=KelasAliyahResponse)
async def get_kelas_aliyah_detail(kelas_id: str, _: dict = Depends(get_current_admin)):
    kelas = await db.kelas_aliyah.find_one({"id": kelas_id}, {"_id": 0})
    if not kelas:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")

    jumlah_siswa = await db.siswa_aliyah.count_documents({"kelas_id": kelas_id})
    return KelasAliyahResponse(**kelas, jumlah_siswa=jumlah_siswa)


@api_router.put("/aliyah/kelas/{kelas_id}", response_model=KelasAliyahResponse)
async def update_kelas_aliyah(kelas_id: str, data: KelasAliyahUpdate, _: dict = Depends(get_current_admin)):
    kelas = await db.kelas_aliyah.find_one({"id": kelas_id}, {"_id": 0})
    if not kelas:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")

    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if update_data:
        await db.kelas_aliyah.update_one({"id": kelas_id}, {"$set": update_data})

    updated_kelas = await db.kelas_aliyah.find_one({"id": kelas_id}, {"_id": 0})
    jumlah_siswa = await db.siswa_aliyah.count_documents({"kelas_id": kelas_id})
    return KelasAliyahResponse(**updated_kelas, jumlah_siswa=jumlah_siswa)


@api_router.delete("/aliyah/kelas/{kelas_id}")
async def delete_kelas_aliyah(kelas_id: str, _: dict = Depends(get_current_admin)):
    # Set siswa aliyah yang ada di kelas ini menjadi kelas_id = None
    await db.siswa_aliyah.update_many(
        {"kelas_id": kelas_id},
        {"$set": {"kelas_id": None}},
    )

    result = await db.kelas_aliyah.delete_one({"id": kelas_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")

    return {"message": "Kelas Aliyah berhasil dihapus"}


@api_router.get("/pengabsen-kelas/kelas-saya", response_model=List[KelasResponse])
async def get_kelas_saya(current_pengabsen: dict = Depends(get_current_pengabsen_kelas)):
    """Daftar kelas yang dapat diakses oleh Pengabsen Kelas (berdasarkan kelas_ids)."""
    kelas_ids = current_pengabsen.get("kelas_ids", []) or []
    if not kelas_ids:
        return []

    kelas_list = await db.kelas.find({"id": {"$in": kelas_ids}}, {"_id": 0}).to_list(1000)

    result: List[KelasResponse] = []
    for kelas in kelas_list:
        jumlah_siswa = await db.siswa_madrasah.count_documents({"kelas_id": kelas["id"]})
        result.append(KelasResponse(**kelas, jumlah_siswa=jumlah_siswa))

    return result


# ==================== SISWA MADRASAH ENDPOINTS ====================

@api_router.get("/siswa-madrasah", response_model=List[SiswaMadrasahResponse])
async def get_siswa_madrasah_list(_: dict = Depends(get_current_admin)):
    siswa_list = await db.siswa_madrasah.find({}, {"_id": 0}).to_list(1000)
    # Map santri NFC ke siswa yang link (untuk kasus santri dapat NFC belakangan)
    santri_ids = [s["santri_id"] for s in siswa_list if s.get("santri_id")]
    santri_nfc_map = {}
    if santri_ids:
        santri_docs = await db.santri.find({"id": {"$in": santri_ids}}, {"_id": 0, "id": 1, "nfc_uid": 1}).to_list(5000)
        for santri in santri_docs:
            if santri.get("nfc_uid"):
                santri_nfc_map[santri["id"]] = santri["nfc_uid"].strip()


    
    # Get kelas names
    kelas_map = {}
    kelas_list = await db.kelas.find({}, {"_id": 0}).to_list(1000)
    for kelas in kelas_list:
        kelas_map[kelas["id"]] = kelas["nama"]
    
    result = []
    for siswa in siswa_list:
        # Jika link santri dan siswa belum punya nfc_uid sendiri, ikutkan dari santri
        if siswa.get("santri_id") and not siswa.get("nfc_uid"):
            nfc_from_santri = santri_nfc_map.get(siswa["santri_id"])
            if nfc_from_santri:
                siswa["nfc_uid"] = nfc_from_santri

        kelas_nama = kelas_map.get(siswa.get("kelas_id")) if siswa.get("kelas_id") else None
        has_qr = False
        if siswa.get("santri_id"):
            has_qr = True
        elif siswa.get("qr_code"):
            has_qr = True
        
        result.append(SiswaMadrasahResponse(
            **siswa,
            kelas_nama=kelas_nama,
            has_qr=has_qr
        ))
    
    return result

@api_router.post("/siswa-madrasah", response_model=SiswaMadrasahResponse)
async def create_siswa_madrasah(data: SiswaMadrasahCreate, _: dict = Depends(get_current_admin)):
    if data.kelas_id:
        kelas = await db.kelas.find_one({"id": data.kelas_id}, {"_id": 0})
        if not kelas:
            raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")

    if data.santri_id:
        santri = await db.santri.find_one({"id": data.santri_id}, {"_id": 0})
        if not santri:
            raise HTTPException(status_code=404, detail="Santri tidak ditemukan")
        existing = await db.siswa_madrasah.find_one({"santri_id": data.santri_id})
        if existing:
            raise HTTPException(status_code=400, detail="Santri sudah terdaftar sebagai siswa Madrasah")
        # NFC ikut santri jika ada
        if santri.get("nfc_uid"):
            data.nfc_uid = santri["nfc_uid"].strip()

    if data.nfc_uid:
        nfc_uid = data.nfc_uid.strip()
        if nfc_uid:
            existing_nfc = await db.siswa_madrasah.find_one({"nfc_uid": nfc_uid})
            if existing_nfc:
                raise HTTPException(status_code=400, detail="NFC UID sudah digunakan")

    qr_payload = {
        "id": str(uuid.uuid4()),
        "nama": data.nama,
        "type": "siswa_madrasah",
    }
    qr_code = generate_qr_code(qr_payload) if not data.santri_id else None

    siswa_dict = data.model_dump()
    siswa_dict["id"] = qr_payload["id"]
    siswa_dict["qr_code"] = qr_code
    if siswa_dict.get("nfc_uid"):
        siswa_dict["nfc_uid"] = siswa_dict["nfc_uid"].strip()
    else:
        siswa_dict["nfc_uid"] = None
    siswa_dict["created_at"] = datetime.now(timezone.utc)
    siswa_dict["updated_at"] = datetime.now(timezone.utc)

    await db.siswa_madrasah.insert_one(siswa_dict)

    kelas_nama = None
    if data.kelas_id:
        kelas = await db.kelas.find_one({"id": data.kelas_id}, {"_id": 0})
        kelas_nama = kelas.get("nama") if kelas else None

    return SiswaMadrasahResponse(
        **siswa_dict,
        kelas_nama=kelas_nama,
        has_qr=bool(qr_code) or bool(data.santri_id),
    )
    


# ==================== SISWA ALIYAH ENDPOINTS ====================

@api_router.get("/aliyah/siswa", response_model=List[SiswaAliyahResponse])
async def get_siswa_aliyah_list(_: dict = Depends(get_current_admin)):
    siswa_list = await db.siswa_aliyah.find({}, {"_id": 0}).to_list(1000)

    # Map santri NFC ke siswa yang link (untuk kasus santri dapat NFC belakangan)
    santri_ids = [s["santri_id"] for s in siswa_list if s.get("santri_id")]
    santri_nfc_map: Dict[str, str] = {}
    if santri_ids:
        santri_docs = await db.santri.find({"id": {"$in": santri_ids}}, {"_id": 0, "id": 1, "nfc_uid": 1}).to_list(5000)
        for santri in santri_docs:
            if santri.get("nfc_uid"):
                santri_nfc_map[santri["id"]] = santri["nfc_uid"].strip()


    # Get kelas aliyah names
    kelas_map: Dict[str, str] = {}
    kelas_list = await db.kelas_aliyah.find({}, {"_id": 0}).to_list(1000)
    for kelas in kelas_list:
        kelas_map[kelas["id"]] = kelas["nama"]

    result: List[SiswaAliyahResponse] = []
    for siswa in siswa_list:
        # Jika link santri dan siswa belum punya nfc_uid sendiri, ikutkan dari santri
        if siswa.get("santri_id") and not siswa.get("nfc_uid"):
            nfc_from_santri = santri_nfc_map.get(siswa["santri_id"])
            if nfc_from_santri:
                siswa["nfc_uid"] = nfc_from_santri

        kelas_nama = kelas_map.get(siswa.get("kelas_id")) if siswa.get("kelas_id") else None
        has_qr = False
        if siswa.get("santri_id"):
            has_qr = True
        elif siswa.get("qr_code"):
            has_qr = True

        result.append(
            SiswaAliyahResponse(
                **siswa,
                kelas_nama=kelas_nama,
                has_qr=has_qr,
            )
        )

    return result


@api_router.post("/aliyah/siswa", response_model=SiswaAliyahResponse)
async def create_siswa_aliyah(data: SiswaAliyahCreate, _: dict = Depends(get_current_admin)):
    # Validate kelas_id if provided
    if data.kelas_id:
        kelas = await db.kelas_aliyah.find_one({"id": data.kelas_id}, {"_id": 0})
        if not kelas:
            raise HTTPException(status_code=404, detail="Kelas Aliyah tidak ditemukan")

    # Validate santri_id if provided
    if data.santri_id:
        santri = await db.santri.find_one({"id": data.santri_id}, {"_id": 0})
        if not santri:
            raise HTTPException(status_code=404, detail="Santri tidak ditemukan")
        # NFC ikut santri jika ada
        if santri.get("nfc_uid"):
            data.nfc_uid = santri["nfc_uid"].strip()

    if data.nfc_uid:
        nfc_uid = data.nfc_uid.strip()
        if nfc_uid:
            existing_nfc = await db.siswa_aliyah.find_one({"nfc_uid": nfc_uid})
            if existing_nfc:
                raise HTTPException(status_code=400, detail="NFC UID sudah digunakan")

    siswa = SiswaAliyah(**data.model_dump())
    if siswa.nfc_uid:
        siswa.nfc_uid = siswa.nfc_uid.strip()

    # Generate QR code only if no santri_id
    if not siswa.santri_id:
        qr_data = {
            "id": siswa.id,
            "nama": siswa.nama,
            "type": "siswa_aliyah",
        }
        siswa.qr_code = generate_qr_code(qr_data)

    doc = siswa.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    doc["updated_at"] = doc["updated_at"].isoformat()
    await db.siswa_aliyah.insert_one(doc)

    kelas_nama = None
    if siswa.kelas_id:
        kelas = await db.kelas_aliyah.find_one({"id": siswa.kelas_id}, {"_id": 0})
        kelas_nama = kelas["nama"] if kelas else None

    has_qr = bool(siswa.santri_id or siswa.qr_code)

    return SiswaAliyahResponse(**siswa.model_dump(), kelas_nama=kelas_nama, has_qr=has_qr)


@api_router.get("/aliyah/siswa/{siswa_id}/qr-code")
async def get_siswa_aliyah_qr_code(siswa_id: str, _: dict = Depends(get_current_admin)):
    siswa = await db.siswa_aliyah.find_one({"id": siswa_id}, {"_id": 0})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")

    # If linked to santri, get QR from santri
    if siswa.get("santri_id"):
        santri = await db.santri.find_one({"id": siswa["santri_id"]}, {"_id": 0})
        if not santri or not santri.get("qr_code"):
            raise HTTPException(status_code=404, detail="QR Code tidak ditemukan")
        img_data = base64.b64decode(santri["qr_code"])
    elif siswa.get("qr_code"):
        img_data = base64.b64decode(siswa["qr_code"])
    else:
        raise HTTPException(status_code=404, detail="QR Code tidak ditemukan")

    return Response(content=img_data, media_type="image/png")


@api_router.put("/aliyah/siswa/{siswa_id}", response_model=SiswaAliyahResponse)
async def update_siswa_aliyah(siswa_id: str, data: SiswaAliyahUpdate, _: dict = Depends(get_current_admin)):
    siswa = await db.siswa_aliyah.find_one({"id": siswa_id}, {"_id": 0})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")

    update_data = {k: v for k, v in data.model_dump().items() if v is not None}

    if "kelas_id" in update_data:
        kelas = await db.kelas_aliyah.find_one({"id": update_data["kelas_id"]}, {"_id": 0})
        if not kelas:
            raise HTTPException(status_code=404, detail="Kelas Aliyah tidak ditemukan")

    if "santri_id" in update_data:
        santri = await db.santri.find_one({"id": update_data["santri_id"]}, {"_id": 0})
        if not santri:
            raise HTTPException(status_code=404, detail="Santri tidak ditemukan")

    if "nfc_uid" in update_data:
        nfc_uid = (update_data.get("nfc_uid") or "").strip()
        if nfc_uid:
            existing_nfc = await db.siswa_aliyah.find_one({"nfc_uid": nfc_uid, "id": {"$ne": siswa_id}}, {"_id": 0})
            if existing_nfc:
                raise HTTPException(status_code=400, detail="NFC UID sudah digunakan")
            update_data["nfc_uid"] = nfc_uid
        else:
            update_data["nfc_uid"] = None

    if update_data:
        await db.siswa_aliyah.update_one({"id": siswa_id}, {"$set": update_data})

    updated_siswa = await db.siswa_aliyah.find_one({"id": siswa_id}, {"_id": 0})

    kelas_nama = None
    if updated_siswa.get("kelas_id"):
        kelas = await db.kelas_aliyah.find_one({"id": updated_siswa["kelas_id"]}, {"_id": 0})
        kelas_nama = kelas["nama"] if kelas else None

    has_qr = bool(updated_siswa.get("santri_id") or updated_siswa.get("qr_code"))

    return SiswaAliyahResponse(**updated_siswa, kelas_nama=kelas_nama, has_qr=has_qr)


@api_router.delete("/aliyah/siswa/{siswa_id}")
async def delete_siswa_aliyah(siswa_id: str, _: dict = Depends(get_current_admin)):
    result = await db.siswa_aliyah.delete_one({"id": siswa_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")

    return {"message": "Siswa Aliyah berhasil dihapus"}


@api_router.get("/siswa-madrasah/{siswa_id}/qr-code")
async def get_siswa_madrasah_qr_code(siswa_id: str, _: dict = Depends(get_current_admin)):
    siswa = await db.siswa_madrasah.find_one({"id": siswa_id}, {"_id": 0})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")
    
    # If linked to santri, get QR from santri
    if siswa.get("santri_id"):
        santri = await db.santri.find_one({"id": siswa["santri_id"]}, {"_id": 0})
        if not santri or not santri.get("qr_code"):
            raise HTTPException(status_code=404, detail="QR Code tidak ditemukan")
        img_data = base64.b64decode(santri['qr_code'])
    elif siswa.get("qr_code"):
        img_data = base64.b64decode(siswa['qr_code'])
    else:
        raise HTTPException(status_code=404, detail="QR Code tidak ditemukan")
    
    return Response(content=img_data, media_type="image/png")

@api_router.put("/siswa-madrasah/{siswa_id}", response_model=SiswaMadrasahResponse)
async def update_siswa_madrasah(siswa_id: str, data: SiswaMadrasahUpdate, _: dict = Depends(get_current_admin)):
    siswa = await db.siswa_madrasah.find_one({"id": siswa_id}, {"_id": 0})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    
    # Validate kelas_id if changed
    if "kelas_id" in update_data and update_data["kelas_id"]:
        kelas = await db.kelas.find_one({"id": update_data["kelas_id"]}, {"_id": 0})
        if not kelas:
            raise HTTPException(status_code=404, detail="Kelas tidak ditemukan")
    
    # If santri_id is added/changed, remove qr_code
    if "santri_id" in update_data and update_data["santri_id"]:
        santri = await db.santri.find_one({"id": update_data["santri_id"]}, {"_id": 0})
        if not santri:
            raise HTTPException(status_code=404, detail="Santri tidak ditemukan")
        update_data["qr_code"] = None

    if "nfc_uid" in update_data:
        nfc_uid = (update_data.get("nfc_uid") or "").strip()
        if nfc_uid:
            existing_nfc = await db.siswa_madrasah.find_one({"nfc_uid": nfc_uid, "id": {"$ne": siswa_id}})
            if existing_nfc:
                raise HTTPException(status_code=400, detail="NFC UID sudah digunakan")
            update_data["nfc_uid"] = nfc_uid
        else:
            update_data["nfc_uid"] = None
    
    if update_data:
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.siswa_madrasah.update_one({"id": siswa_id}, {"$set": update_data})
    
    updated_siswa = await db.siswa_madrasah.find_one({"id": siswa_id}, {"_id": 0})
    
    kelas_nama = None
    if updated_siswa.get("kelas_id"):
        kelas = await db.kelas.find_one({"id": updated_siswa["kelas_id"]}, {"_id": 0})
        kelas_nama = kelas["nama"] if kelas else None
    
    has_qr = bool(updated_siswa.get("santri_id") or updated_siswa.get("qr_code"))
    
    if isinstance(updated_siswa.get('created_at'), str):
        updated_siswa['created_at'] = datetime.fromisoformat(updated_siswa['created_at'])
    if isinstance(updated_siswa.get('updated_at'), str):
        updated_siswa['updated_at'] = datetime.fromisoformat(updated_siswa['updated_at'])
    
    return SiswaMadrasahResponse(**updated_siswa, kelas_nama=kelas_nama, has_qr=has_qr)

@api_router.get("/pengabsen-kelas/siswa-saya")
async def get_pengabsen_kelas_siswa_saya(current_pengabsen: dict = Depends(get_current_pengabsen_kelas)):
    """Daftar siswa madrasah untuk semua kelas yang dikelola Pengabsen Kelas."""
    kelas_ids = current_pengabsen.get("kelas_ids", []) or []
    if not kelas_ids:
        return []

    siswa_list = await db.siswa_madrasah.find({"kelas_id": {"$in": kelas_ids}}, {"_id": 0}).to_list(1000)
    # Ambil nama kelas
    kelas_docs = await db.kelas.find({"id": {"$in": kelas_ids}}, {"_id": 0}).to_list(1000)
    kelas_map = {k["id"]: k["nama"] for k in kelas_docs}

    result = []
    for siswa in siswa_list:
        result.append(
            {
                "siswa_id": siswa["id"],
                "siswa_nama": siswa["nama"],
                "kelas_id": siswa.get("kelas_id"),
                "kelas_nama": kelas_map.get(siswa.get("kelas_id"), ""),
            }
        )

    return result

@api_router.delete("/siswa-madrasah/{siswa_id}")
async def delete_siswa_madrasah(siswa_id: str, _: dict = Depends(get_current_admin)):
    result = await db.siswa_madrasah.delete_one({"id": siswa_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")
    
    # Also delete all absensi kelas for this siswa
    await db.absensi_kelas.delete_many({"siswa_id": siswa_id})
    
    return {"message": "Siswa berhasil dihapus"}

@api_router.post("/siswa-madrasah/{siswa_id}/link-to-santri")
async def link_siswa_to_santri(siswa_id: str, santri_id: str, _: dict = Depends(get_current_admin)):
    siswa = await db.siswa_madrasah.find_one({"id": siswa_id}, {"_id": 0})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")
    
    santri = await db.santri.find_one({"id": santri_id}, {"_id": 0})
    if not santri:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan")
    
    # Link and remove standalone QR
    await db.siswa_madrasah.update_one(
        {"id": siswa_id},
        {"$set": {"santri_id": santri_id, "qr_code": None, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    
    return {"message": "Siswa berhasil di-link ke Santri"}

# ==================== ABSENSI KELAS ENDPOINTS ====================

@api_router.post("/absensi-kelas/scan")
async def scan_qr_absensi_kelas(
    qr_data: dict,
    current_pengabsen: dict = Depends(get_current_pengabsen_kelas)
):
    """Scan QR code for kelas attendance - auto mark as Hadir"""
    tanggal = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Determine siswa_id from QR code
    siswa_id = None
    if qr_data.get("type") == "siswa_madrasah":
        siswa_id = qr_data.get("id")
    else:
        # QR is from santri, find siswa_madrasah linked to this santri
        santri_id = qr_data.get("id")
        siswa = await db.siswa_madrasah.find_one({"santri_id": santri_id}, {"_id": 0})
        if siswa:
            siswa_id = siswa["id"]
    
    if not siswa_id:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")
    
    # Get siswa info
    siswa = await db.siswa_madrasah.find_one({"id": siswa_id}, {"_id": 0})
    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa tidak ditemukan")
    
    if not siswa.get("kelas_id"):
        raise HTTPException(status_code=400, detail="Siswa belum memiliki kelas")
    
    # Verify pengabsen has access to this kelas
    if siswa["kelas_id"] not in current_pengabsen.get("kelas_ids", []):
        raise HTTPException(status_code=403, detail="Anda tidak memiliki akses ke kelas ini")
    
    # Check if already marked today
    existing = await db.absensi_kelas.find_one({
        "siswa_id": siswa_id,
        "tanggal": tanggal
    }, {"_id": 0})
    
    if existing:
        return {"message": "Siswa sudah diabsen hari ini", "status": existing["status"], "siswa_nama": siswa["nama"]}
    
    # Create new absensi
    absensi = AbsensiKelas(
        siswa_id=siswa_id,
        kelas_id=siswa["kelas_id"],
        tanggal=tanggal,
        status="hadir",
        waktu_absen=datetime.now(timezone.utc),
        pengabsen_kelas_id=current_pengabsen["id"]
    )
    
    doc = absensi.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    if doc.get('waktu_absen'):
        doc['waktu_absen'] = doc['waktu_absen'].isoformat()
    
    await db.absensi_kelas.insert_one(doc)
    
    return {"message": "Absensi berhasil dicatat", "siswa_nama": siswa["nama"], "status": "hadir"}


@api_router.post("/absensi-kelas/nfc")
async def absensi_kelas_nfc(
    payload: AbsensiKelasNFCRequest,
    current_pengabsen: dict = Depends(get_current_pengabsen_kelas)
):
    nfc_uid = payload.nfc_uid.strip() if payload.nfc_uid else ""
    if not nfc_uid:
        raise HTTPException(status_code=400, detail="NFC UID wajib diisi")

    siswa = await db.siswa_madrasah.find_one({"nfc_uid": nfc_uid}, {"_id": 0})
    if not siswa:
        raise HTTPException(status_code=404, detail="Kartu NFC belum terdaftar")

    if siswa.get("kelas_id") not in current_pengabsen.get("kelas_ids", []):
        raise HTTPException(status_code=403, detail="Siswa tidak termasuk kelas Anda")

    tanggal = payload.tanggal or get_today_local_iso()
    status = payload.status or "hadir"

    existing = await db.absensi_kelas.find_one({
        "siswa_id": siswa["id"],
        "tanggal": tanggal,
    }, {"_id": 0})

    if existing:
        return {"message": "Siswa sudah diabsen hari ini", "status": existing["status"], "siswa_nama": siswa["nama"]}

    now = datetime.now(timezone.utc)
    doc = {
        "id": str(uuid.uuid4()),
        "siswa_id": siswa["id"],
        "kelas_id": siswa.get("kelas_id"),
        "tanggal": tanggal,
        "status": status,
        "waktu_absen": now.isoformat(),
        "pengabsen_kelas_id": current_pengabsen["id"],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    await db.absensi_kelas.insert_one(doc)

    return {"message": "Absensi NFC berhasil dicatat", "siswa_nama": siswa["nama"], "status": status}

@api_router.get("/absensi-kelas/riwayat")
async def get_absensi_kelas_riwayat(
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    kelas_id: Optional[str] = None,
    current_pengabsen: dict = Depends(get_current_pengabsen_kelas)
):
    """Get attendance history for Pengabsen Kelas"""
    if not tanggal_end:
        tanggal_end = tanggal_start
    
    query = {
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end}
    }
    
    if kelas_id:
        query["kelas_id"] = kelas_id
    else:
        # Filter by pengabsen's kelas
        query["kelas_id"] = {"$in": current_pengabsen.get("kelas_ids", [])}
    
    absensi_list = await db.absensi_kelas.find(query, {"_id": 0}).to_list(10000)
    
    # Enrich with siswa and kelas names
    siswa_map = {}
    siswa_list = await db.siswa_madrasah.find({}, {"_id": 0}).to_list(10000)
    for siswa in siswa_list:
        siswa_map[siswa["id"]] = siswa["nama"]
    
    kelas_map = {}
    kelas_list = await db.kelas.find({}, {"_id": 0}).to_list(1000)
    for kelas in kelas_list:
        kelas_map[kelas["id"]] = kelas["nama"]
    
    result = []
    for absensi in absensi_list:
        result.append(AbsensiKelasResponse(
            **absensi,
            siswa_nama=siswa_map.get(absensi["siswa_id"], "Unknown"),
            kelas_nama=kelas_map.get(absensi["kelas_id"], "Unknown")
        ))
    
    return result

@api_router.get("/absensi-kelas/grid")
async def get_absensi_kelas_grid(
    bulan: str,  # YYYY-MM
    kelas_id: str,
    current_pengabsen: dict = Depends(get_current_pengabsen_kelas)
):
    """Get monthly grid for manual input - for Pengabsen Kelas"""
    # Verify access
    if kelas_id not in current_pengabsen.get("kelas_ids", []):
        raise HTTPException(status_code=403, detail="Anda tidak memiliki akses ke kelas ini")
    
    # Get all siswa in this kelas
    siswa_list = await db.siswa_madrasah.find({"kelas_id": kelas_id}, {"_id": 0}).to_list(1000)
    
    # Get all absensi for this month
    tanggal_start = f"{bulan}-01"
    tanggal_end = f"{bulan}-31"
    
    absensi_list = await db.absensi_kelas.find({
        "kelas_id": kelas_id,
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end}
    }, {"_id": 0}).to_list(10000)
    
    # Build grid
    absensi_map = {}
    for absensi in absensi_list:
        key = f"{absensi['siswa_id']}_{absensi['tanggal']}"
        absensi_map[key] = absensi
    
    result = []
    for siswa in siswa_list:
        siswa_data = {
            "siswa_id": siswa["id"],
            "siswa_nama": siswa["nama"],
            "kelas_id": kelas_id,
            "absensi": []
        }
        
        # Generate all days in month
        import calendar
        year, month = map(int, bulan.split("-"))
        days_in_month = calendar.monthrange(year, month)[1]
        
        for day in range(1, days_in_month + 1):
            tanggal = f"{bulan}-{day:02d}"
            key = f"{siswa['id']}_{tanggal}"
            
            if key in absensi_map:
                siswa_data["absensi"].append({
                    "tanggal": tanggal,
                    "status": absensi_map[key]["status"],
                    "absensi_id": absensi_map[key]["id"]
                })
            else:
                siswa_data["absensi"].append({
                    "tanggal": tanggal,
                    "status": None,
                    "absensi_id": None
                })
        
        result.append(siswa_data)
    
    return result

@api_router.put("/absensi-kelas/{absensi_id}", response_model=AbsensiKelasResponse)
async def update_absensi_kelas(
    absensi_id: str,
    data: AbsensiKelasUpdate,
    current_pengabsen: dict = Depends(get_current_pengabsen_kelas)
):
    """Update attendance status manually"""
    absensi = await db.absensi_kelas.find_one({"id": absensi_id}, {"_id": 0})
    if not absensi:
        raise HTTPException(status_code=404, detail="Absensi tidak ditemukan")
    
    # Verify access
    if absensi["kelas_id"] not in current_pengabsen.get("kelas_ids", []):
        raise HTTPException(status_code=403, detail="Anda tidak memiliki akses ke kelas ini")
    
    await db.absensi_kelas.update_one(
        {"id": absensi_id},
        {"$set": {"status": data.status}}
    )
    
    updated_absensi = await db.absensi_kelas.find_one({"id": absensi_id}, {"_id": 0})
    
    # Get names
    siswa = await db.siswa_madrasah.find_one({"id": updated_absensi["siswa_id"]}, {"_id": 0})
    kelas = await db.kelas.find_one({"id": updated_absensi["kelas_id"]}, {"_id": 0})
    
    return AbsensiKelasResponse(
        **updated_absensi,
        siswa_nama=siswa["nama"] if siswa else "Unknown",
        kelas_nama=kelas["nama"] if kelas else "Unknown"
    )

@api_router.post("/absensi-kelas/manual")
async def create_absensi_kelas_manual(
    data: AbsensiKelasCreate,
    current_pengabsen: dict = Depends(get_current_pengabsen_kelas)
):
    """Create attendance manually from grid"""
    # Verify access
    if data.kelas_id not in current_pengabsen.get("kelas_ids", []):
        raise HTTPException(status_code=403, detail="Anda tidak memiliki akses ke kelas ini")
    
    # Check if already exists
    existing = await db.absensi_kelas.find_one({
        "siswa_id": data.siswa_id,
        "kelas_id": data.kelas_id,
        "tanggal": data.tanggal
    }, {"_id": 0})
    
    if existing:
        # Update instead
        await db.absensi_kelas.update_one(
            {"id": existing["id"]},
            {"$set": {"status": data.status}}
        )
        return {"message": "Absensi berhasil diupdate", "absensi_id": existing["id"]}
    
    # Create new
    absensi = AbsensiKelas(
        siswa_id=data.siswa_id,
        kelas_id=data.kelas_id,
        tanggal=data.tanggal,
        status=data.status,
        pengabsen_kelas_id=current_pengabsen["id"]
    )
    
    doc = absensi.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    if doc.get('waktu_absen'):
        doc['waktu_absen'] = doc['waktu_absen'].isoformat()
    
    await db.absensi_kelas.insert_one(doc)
    
    return {"message": "Absensi berhasil dicatat", "absensi_id": absensi.id}

@api_router.delete("/absensi-kelas/{absensi_id}")
async def delete_absensi_kelas_route(
    absensi_id: str,
    current_pengabsen: dict = Depends(get_current_pengabsen_kelas),
):
    """Endpoint publik untuk hapus absensi_kelas (dipakai Pengabsen Kelas)."""
    return await delete_absensi_kelas(absensi_id, current_pengabsen)


@api_router.get("/pembimbing-kelas/kelas-saya", response_model=List[KelasResponse])
async def get_pembimbing_kelas_kelas_saya(current_pembimbing: dict = Depends(get_current_pembimbing_kelas)):
    """Daftar kelas yang dapat diakses oleh Pembimbing Kelas (berdasarkan kelas_ids)."""
    kelas_ids = current_pembimbing.get("kelas_ids", []) or []
    if not kelas_ids:
        return []

    kelas_list = await db.kelas.find({"id": {"$in": kelas_ids}}, {"_id": 0}).to_list(1000)

    result: List[KelasResponse] = []
    for kelas in kelas_list:
        jumlah_siswa = await db.siswa_madrasah.count_documents({"kelas_id": kelas["id"]})
        result.append(KelasResponse(**kelas, jumlah_siswa=jumlah_siswa))

    return result


async def delete_absensi_kelas(absensi_id: str, current_pengabsen: dict = Depends(get_current_pengabsen_kelas)):
    """Hapus absensi kelas sehingga kembali menjadi '-' di grid"""
    absensi = await db.absensi_kelas.find_one({"id": absensi_id}, {"_id": 0})
    if not absensi:
        # Jika sudah tidak ada, anggap sukses supaya UI tidak error
        return {"message": "Absensi sudah tidak ada"}

    if absensi["kelas_id"] not in current_pengabsen.get("kelas_ids", []):
        raise HTTPException(status_code=403, detail="Anda tidak memiliki akses ke kelas ini")

    await db.absensi_kelas.delete_one({"id": absensi_id})
    return {"message": "Absensi berhasil dihapus"}



# ==================== PENGABSEN & MONITORING ALIYAH ENDPOINTS (ADMIN) ====================

@api_router.get("/aliyah/pengabsen", response_model=List[PengabsenAliyahResponse])
async def get_pengabsen_aliyah_list(_: dict = Depends(get_current_admin)):
    pengabsen_list = await db.pengabsen_aliyah.find({}, {"_id": 0}).to_list(1000)
    return [PengabsenAliyahResponse(**p) for p in pengabsen_list]


@api_router.post("/aliyah/pengabsen", response_model=PengabsenAliyahResponse)
async def create_pengabsen_aliyah(data: PengabsenAliyahCreate, _: dict = Depends(get_current_admin)):
    # Validate kelas_ids terhadap kelas_aliyah
    for kelas_id in data.kelas_ids:
        kelas = await db.kelas_aliyah.find_one({"id": kelas_id}, {"_id": 0})
        if not kelas:
            raise HTTPException(status_code=404, detail=f"Kelas Aliyah {kelas_id} tidak ditemukan")

    # Cek username unik
    existing = await db.pengabsen_aliyah.find_one({"username": data.username})
    if existing:
        raise HTTPException(status_code=400, detail="Username sudah digunakan")

    pengabsen = PengabsenAliyah(
        **data.model_dump(),
        kode_akses=generate_kode_akses(),
    )

    doc = pengabsen.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    await db.pengabsen_aliyah.insert_one(doc)

    return PengabsenAliyahResponse(**pengabsen.model_dump())


@api_router.put("/aliyah/pengabsen/{pengabsen_id}", response_model=PengabsenAliyahResponse)
async def update_pengabsen_aliyah(pengabsen_id: str, data: PengabsenAliyahUpdate, _: dict = Depends(get_current_admin)):
    pengabsen = await db.pengabsen_aliyah.find_one({"id": pengabsen_id}, {"_id": 0})
    if not pengabsen:
        raise HTTPException(status_code=404, detail="Pengabsen Aliyah tidak ditemukan")

    update_data = {k: v for k, v in data.model_dump().items() if v is not None}

    if "username" in update_data:
        existing = await db.pengabsen_aliyah.find_one({"username": update_data["username"], "id": {"$ne": pengabsen_id}})
        if existing:
            raise HTTPException(status_code=400, detail="Username sudah digunakan")

    if "kelas_ids" in update_data:
        for kelas_id in update_data["kelas_ids"]:
            kelas = await db.kelas_aliyah.find_one({"id": kelas_id}, {"_id": 0})
            if not kelas:
                raise HTTPException(status_code=404, detail=f"Kelas Aliyah {kelas_id} tidak ditemukan")

    if update_data:
        await db.pengabsen_aliyah.update_one({"id": pengabsen_id}, {"$set": update_data})

    updated = await db.pengabsen_aliyah.find_one({"id": pengabsen_id}, {"_id": 0})
    return PengabsenAliyahResponse(**updated)


@api_router.post("/aliyah/pengabsen/{pengabsen_id}/regenerate-kode-akses", response_model=PengabsenAliyahResponse)
async def regenerate_pengabsen_aliyah_kode(pengabsen_id: str, _: dict = Depends(get_current_admin)):
    pengabsen = await db.pengabsen_aliyah.find_one({"id": pengabsen_id}, {"_id": 0})
    if not pengabsen:
        raise HTTPException(status_code=404, detail="Pengabsen Aliyah tidak ditemukan")

    new_kode = generate_kode_akses()
    await db.pengabsen_aliyah.update_one({"id": pengabsen_id}, {"$set": {"kode_akses": new_kode}})

    updated = await db.pengabsen_aliyah.find_one({"id": pengabsen_id}, {"_id": 0})
    return PengabsenAliyahResponse(**updated)


@api_router.delete("/aliyah/pengabsen/{pengabsen_id}")
async def delete_pengabsen_aliyah(pengabsen_id: str, _: dict = Depends(get_current_admin)):
    result = await db.pengabsen_aliyah.delete_one({"id": pengabsen_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Pengabsen Aliyah tidak ditemukan")
    return {"message": "Pengabsen Aliyah berhasil dihapus"}


@api_router.get("/aliyah/monitoring", response_model=List[MonitoringAliyahResponse])
async def get_monitoring_aliyah_list(_: dict = Depends(get_current_admin)):
    pembimbing_list = await db.pembimbing_aliyah.find({}, {"_id": 0}).to_list(1000)
    return [MonitoringAliyahResponse(**p) for p in pembimbing_list]


@api_router.post("/aliyah/monitoring", response_model=MonitoringAliyahResponse)
async def create_monitoring_aliyah(data: MonitoringAliyahCreate, _: dict = Depends(get_current_admin)):
    # Validate kelas_ids ke kelas_aliyah
    for kelas_id in data.kelas_ids:
        kelas = await db.kelas_aliyah.find_one({"id": kelas_id}, {"_id": 0})
        if not kelas:
            raise HTTPException(status_code=404, detail=f"Kelas Aliyah {kelas_id} tidak ditemukan")

    existing = await db.pembimbing_aliyah.find_one({"username": data.username})
    if existing:
        raise HTTPException(status_code=400, detail="Username sudah digunakan")

    pembimbing = MonitoringAliyah(
        **data.model_dump(),
        kode_akses=generate_kode_akses(),
    )

    doc = pembimbing.model_dump()
    doc["created_at"] = doc["created_at"].isoformat()
    await db.pembimbing_aliyah.insert_one(doc)

    return MonitoringAliyahResponse(**pembimbing.model_dump())


@api_router.put("/aliyah/monitoring/{pembimbing_id}", response_model=MonitoringAliyahResponse)
async def update_monitoring_aliyah(pembimbing_id: str, data: MonitoringAliyahUpdate, _: dict = Depends(get_current_admin)):
    pembimbing = await db.pembimbing_aliyah.find_one({"id": pembimbing_id}, {"_id": 0})
    if not pembimbing:
        raise HTTPException(status_code=404, detail="Monitoring Aliyah tidak ditemukan")

    update_data = {k: v for k, v in data.model_dump().items() if v is not None}

    if "username" in update_data:
        existing = await db.pembimbing_aliyah.find_one({"username": update_data["username"], "id": {"$ne": pembimbing_id}})
        if existing:
            raise HTTPException(status_code=400, detail="Username sudah digunakan")

    if "kelas_ids" in update_data:
        for kelas_id in update_data["kelas_ids"]:
            kelas = await db.kelas_aliyah.find_one({"id": kelas_id}, {"_id": 0})
            if not kelas:
                raise HTTPException(status_code=404, detail=f"Kelas Aliyah {kelas_id} tidak ditemukan")

    if update_data:
        await db.pembimbing_aliyah.update_one({"id": pembimbing_id}, {"$set": update_data})

    updated = await db.pembimbing_aliyah.find_one({"id": pembimbing_id}, {"_id": 0})
    return MonitoringAliyahResponse(**updated)


@api_router.post("/aliyah/monitoring/{pembimbing_id}/regenerate-kode-akses", response_model=MonitoringAliyahResponse)
async def regenerate_monitoring_aliyah_kode(pembimbing_id: str, _: dict = Depends(get_current_admin)):
    pembimbing = await db.pembimbing_aliyah.find_one({"id": pembimbing_id}, {"_id": 0})
    if not pembimbing:
        raise HTTPException(status_code=404, detail="Monitoring Aliyah tidak ditemukan")

    new_kode = generate_kode_akses()
    await db.pembimbing_aliyah.update_one({"id": pembimbing_id}, {"$set": {"kode_akses": new_kode}})

    updated = await db.pembimbing_aliyah.find_one({"id": pembimbing_id}, {"_id": 0})
    return MonitoringAliyahResponse(**updated)


# ==================== ABSENSI ALIYAH RIWAYAT (ADMIN) ====================

@api_router.get("/aliyah/absensi/riwayat")
async def get_aliyah_absensi_riwayat(
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    kelas_id: Optional[str] = None,
    gender: Optional[str] = None,
    jenis: Optional[str] = None,
    _: dict = Depends(get_current_admin),
):
    """Riwayat absensi Madrasah Aliyah untuk admin.

    Mirip dengan madin namun memakai koleksi siswa_aliyah, kelas_aliyah, dan absensi_aliyah,
    dengan status: hadir, alfa, sakit, izin, dispensasi, bolos.
    """
    if not tanggal_end:
        tanggal_end = tanggal_start

    query: Dict[str, Any] = {
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end},
    }
    if kelas_id:
        query["kelas_id"] = kelas_id
    if jenis and jenis in ["pagi", "dzuhur"]:
        query["jenis"] = jenis

    absensi_list = await db.absensi_aliyah.find(query, {"_id": 0}).to_list(10000)

    # Group by (siswa_id, tanggal, jenis)
    dedup_map: Dict[tuple, Dict[str, Any]] = {}
    for a in absensi_list:
        key = (a.get("siswa_id"), a.get("tanggal"), a.get("jenis"))
        current_best = dedup_map.get(key)

        # Determine effective timestamp
        wa = a.get("waktu_absen")
        ca = a.get("created_at")
        # stored as iso strings, compare lexicographically
        ts = wa or ca or ""

        if current_best is None:
            dedup_map[key] = a
        else:
            wa_best = current_best.get("waktu_absen")
            ca_best = current_best.get("created_at")
            ts_best = wa_best or ca_best or ""
            if ts > ts_best:
                dedup_map[key] = a

    absensi_list = list(dedup_map.values())


    # Deduplicate by siswa_id, tanggal, jenis -> keep latest by waktu_absen / created_at
    if not absensi_list:
        return {
            "summary": {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "dispensasi": 0, "bolos": 0},
            "detail": [],
        }

    # Ambil siswa & kelas untuk enrichment + filter gender
    siswa_map: Dict[str, Dict[str, Any]] = {}
    siswa_list = await db.siswa_aliyah.find({}, {"_id": 0}).to_list(10000)
    for siswa in siswa_list:
        siswa_map[siswa["id"]] = siswa

    kelas_map: Dict[str, Dict[str, Any]] = {}
    kelas_list = await db.kelas_aliyah.find({}, {"_id": 0}).to_list(1000)
    for kelas in kelas_list:
        kelas_map[kelas["id"]] = kelas

    detail: List[Dict[str, Any]] = []
    summary = {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "dispensasi": 0, "bolos": 0}

    for absensi in absensi_list:
        siswa = siswa_map.get(absensi["siswa_id"])
        kelas = kelas_map.get(absensi["kelas_id"])

        if not siswa:
            continue

        siswa_gender = siswa.get("gender") or ""

        if gender and gender != "all":
            if siswa_gender != gender:
                continue

        status = absensi.get("status", "")
        if status in summary:
            summary[status] += 1

        row = {
            "id": absensi.get("id"),
            "siswa_id": absensi.get("siswa_id"),
            "siswa_nama": siswa.get("nama") if siswa else "Unknown",
            "kelas_id": absensi.get("kelas_id"),
            "kelas_nama": kelas.get("nama") if kelas else "Unknown",
            "tanggal": absensi.get("tanggal"),
            "status": status,
            "gender": siswa_gender,
            "waktu_absen": absensi.get("waktu_absen"),
        }
        detail.append(row)

    return {"summary": summary, "detail": detail}


# ==================== MONITORING ALIYAH PWA ABSENSI & RIWAYAT ====================

@api_router.get("/aliyah/monitoring/absensi-hari-ini")
async def get_aliyah_monitoring_absensi_hari_ini(
    jenis: Literal["pagi", "dzuhur"],
    tanggal: Optional[str] = None,
    kelas_id: Optional[str] = None,
    current_monitoring: dict = Depends(get_current_monitoring_aliyah),
):
    if not tanggal:
        tanggal = get_today_local_iso()

    kelas_ids = current_monitoring.get("kelas_ids", []) or []
    if not kelas_ids:
        return {"tanggal": tanggal, "jenis": jenis, "data": []}

    if kelas_id and kelas_id not in kelas_ids:
        raise HTTPException(status_code=403, detail="Tidak boleh melihat kelas ini")

    target_kelas_ids = [kelas_id] if kelas_id else kelas_ids

    siswa_list = await db.siswa_aliyah.find({"kelas_id": {"$in": target_kelas_ids}}, {"_id": 0}).to_list(5000)
    siswa_by_id = {s["id"]: s for s in siswa_list}

    absensi_list = await db.absensi_aliyah.find(
        {"tanggal": tanggal, "jenis": jenis, "siswa_id": {"$in": list(siswa_by_id.keys())}},
        {"_id": 0},
    ).to_list(10000)

    status_map: Dict[str, str] = {a["siswa_id"]: a.get("status", "") for a in absensi_list}

    kelas_docs = await db.kelas_aliyah.find({"id": {"$in": target_kelas_ids}}, {"_id": 0}).to_list(1000)
    kelas_map = {k["id"]: k["nama"] for k in kelas_docs}

    data = []
    for siswa in siswa_list:
        data.append(
            {
                "siswa_id": siswa["id"],
                "nama": siswa["nama"],
                "nis": siswa.get("nis"),
                "kelas_id": siswa.get("kelas_id"),
                "kelas_nama": kelas_map.get(siswa.get("kelas_id"), "-"),
                "gender": siswa.get("gender"),
                "status": status_map.get(siswa["id"]),
            }
        )

    data.sort(key=lambda x: (x["kelas_nama"], x["nama"]))

    return {"tanggal": tanggal, "jenis": jenis, "data": data}


@api_router.get("/aliyah/monitoring/absensi-riwayat")
async def get_aliyah_monitoring_absensi_riwayat(
    jenis: Literal["pagi", "dzuhur"],
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    kelas_id: Optional[str] = None,
    current_monitoring: dict = Depends(get_current_monitoring_aliyah),
):
    if not tanggal_end:
        tanggal_end = tanggal_start

    kelas_ids = current_monitoring.get("kelas_ids", []) or []
    if not kelas_ids:
        return {"summary": {}, "detail": []}

    if kelas_id and kelas_id not in kelas_ids:
        raise HTTPException(status_code=403, detail="Tidak boleh melihat kelas ini")

    target_kelas_ids = [kelas_id] if kelas_id else kelas_ids

    query = {
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end},
        "jenis": jenis,
        "kelas_id": {"$in": target_kelas_ids},
    }

    absensi_list = await db.absensi_aliyah.find(query, {"_id": 0}).to_list(20000)

    # Deduplicate by (siswa_id, tanggal, jenis) keeping the latest record
    if not absensi_list:
        return {
            "summary": {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "dispensasi": 0, "bolos": 0},
            "detail": [],
        }

    siswa_ids = list({a["siswa_id"] for a in absensi_list})
    siswa_list = await db.siswa_aliyah.find({"id": {"$in": siswa_ids}}, {"_id": 0}).to_list(10000)
    siswa_map = {s["id"]: s for s in siswa_list}

    kelas_docs = await db.kelas_aliyah.find({"id": {"$in": target_kelas_ids}}, {"_id": 0}).to_list(1000)
    kelas_map = {k["id"]: k["nama"] for k in kelas_docs}

    summary = {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "dispensasi": 0, "bolos": 0}
    detail: List[Dict[str, Any]] = []

    for a in absensi_list:
        status = a.get("status", "")
        if status in summary:
            summary[status] += 1
    # Deduplicate by (siswa_id, tanggal, jenis) keeping the latest record
    dedup_map: Dict[tuple, Dict[str, Any]] = {}
    for a in absensi_list:
        key = (a.get("siswa_id"), a.get("tanggal"), a.get("jenis"))
        current_best = dedup_map.get(key)

        wa = a.get("waktu_absen")
        ca = a.get("created_at")
        ts = wa or ca or ""

        if current_best is None:
            dedup_map[key] = a
        else:
            wa_best = current_best.get("waktu_absen")
            ca_best = current_best.get("created_at")
            ts_best = wa_best or ca_best or ""
            if ts > ts_best:
                dedup_map[key] = a

    absensi_list = list(dedup_map.values())

    summary = {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "dispensasi": 0, "bolos": 0}
    detail: List[Dict[str, Any]] = []

    for a in absensi_list:
        status = a.get("status", "")
        if status in summary:
            summary[status] += 1

        siswa = siswa_map.get(a["siswa_id"])
        kelas_nama = kelas_map.get(a.get("kelas_id"), "-")

        row = {
            "id": a.get("id"),
            "siswa_id": a.get("siswa_id"),
            "siswa_nama": siswa.get("nama") if siswa else "Unknown",
            "kelas_id": a.get("kelas_id"),
            "kelas_nama": kelas_nama,
            "tanggal": a.get("tanggal"),
            "status": status,
            "gender": siswa.get("gender") if siswa else None,
            "waktu_absen": a.get("waktu_absen"),
        }
        detail.append(row)

    # Sort by tanggal, kelas, nama
    detail.sort(key=lambda x: (x["tanggal"], x["kelas_nama"], x["siswa_nama"]))

    return {"summary": summary, "detail": detail}


@api_router.delete("/aliyah/monitoring/{pembimbing_id}")
async def delete_monitoring_aliyah(pembimbing_id: str, _: dict = Depends(get_current_admin)):
    result = await db.pembimbing_aliyah.delete_one({"id": pembimbing_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Monitoring Aliyah tidak ditemukan")
    return {"message": "Monitoring Aliyah berhasil dihapus"}


# ==================== PENGABSEN KELAS ENDPOINTS ====================

@api_router.get("/pengabsen-kelas", response_model=List[PengabsenKelasResponse])
async def get_pengabsen_kelas_list(_: dict = Depends(get_current_admin)):
    pengabsen_list = await db.pengabsen_kelas.find({}, {"_id": 0}).to_list(1000)
    return [PengabsenKelasResponse(**p) for p in pengabsen_list]

@api_router.post("/pengabsen-kelas", response_model=PengabsenKelasResponse)
async def create_pengabsen_kelas(data: PengabsenKelasCreate, _: dict = Depends(get_current_admin)):
    # Validate kelas_ids
    for kelas_id in data.kelas_ids:
        kelas = await db.kelas.find_one({"id": kelas_id}, {"_id": 0})
        if not kelas:
            raise HTTPException(status_code=404, detail=f"Kelas {kelas_id} tidak ditemukan")
    
    # Check username uniqueness
    existing = await db.pengabsen_kelas.find_one({"username": data.username})
    if existing:
        raise HTTPException(status_code=400, detail="Username sudah digunakan")
    
    pengabsen = PengabsenKelas(
        **data.model_dump(),
        kode_akses=generate_kode_akses()
    )
    
    doc = pengabsen.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.pengabsen_kelas.insert_one(doc)
    
    return PengabsenKelasResponse(**pengabsen.model_dump())


# ==================== PENGABSEN ALIYAH PWA RIWAYAT ====================

@api_router.get("/aliyah/pengabsen/riwayat")
async def get_aliyah_pengabsen_riwayat(
    jenis: Literal["pagi", "dzuhur"],
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    current_pengabsen: dict = Depends(get_current_pengabsen_aliyah),
):
    if not tanggal_end:
        tanggal_end = tanggal_start

    kelas_ids = current_pengabsen.get("kelas_ids", []) or []
    if not kelas_ids:
        return {"summary": {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "dispensasi": 0, "bolos": 0}, "detail": []}

    query = {
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end},
        "jenis": jenis,
        "kelas_id": {"$in": kelas_ids},
    }

    absensi_list = await db.absensi_aliyah.find(query, {"_id": 0}).to_list(20000)

    if not absensi_list:
        return {
            "summary": {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "dispensasi": 0, "bolos": 0},
            "detail": [],
        }

    # Dedup per siswa/tanggal/jenis
    dedup_map: Dict[tuple, Dict[str, Any]] = {}
    for a in absensi_list:
        key = (a.get("siswa_id"), a.get("tanggal"), a.get("jenis"))
        current_best = dedup_map.get(key)

        wa = a.get("waktu_absen")
        ca = a.get("created_at")
        ts = wa or ca or ""

        if current_best is None:
            dedup_map[key] = a
        else:
            wa_best = current_best.get("waktu_absen")
            ca_best = current_best.get("created_at")
            ts_best = wa_best or ca_best or ""
            if ts > ts_best:
                dedup_map[key] = a

    absensi_list = list(dedup_map.values())

    siswa_ids = list({a["siswa_id"] for a in absensi_list})
    siswa_list = await db.siswa_aliyah.find({"id": {"$in": siswa_ids}}, {"_id": 0}).to_list(10000)
    siswa_map = {s["id"]: s for s in siswa_list}

    kelas_docs = await db.kelas_aliyah.find({"id": {"$in": kelas_ids}}, {"_id": 0}).to_list(1000)
    kelas_map = {k["id"]: k["nama"] for k in kelas_docs}

    summary = {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "dispensasi": 0, "bolos": 0}
    detail: List[Dict[str, Any]] = []

    for a in absensi_list:
        status = a.get("status", "")
        if status in summary:
            summary[status] += 1

        siswa = siswa_map.get(a["siswa_id"])
        kelas_nama = kelas_map.get(a.get("kelas_id"), "-")

        row = {
            "id": a.get("id"),
            "siswa_id": a.get("siswa_id"),
            "siswa_nama": siswa.get("nama") if siswa else "Unknown",
            "kelas_id": a.get("kelas_id"),
            "kelas_nama": kelas_nama,
            "tanggal": a.get("tanggal"),
            "status": status,
            "gender": siswa.get("gender") if siswa else None,
            "waktu_absen": a.get("waktu_absen"),
        }
        detail.append(row)

    # Urutkan agar rapih
    detail.sort(key=lambda x: (x["tanggal"], x["kelas_nama"], x["siswa_nama"]))

    return {"summary": summary, "detail": detail}


@api_router.put("/pengabsen-kelas/{pengabsen_id}", response_model=PengabsenKelasResponse)
async def update_pengabsen_kelas(pengabsen_id: str, data: PengabsenKelasUpdate, _: dict = Depends(get_current_admin)):
    pengabsen = await db.pengabsen_kelas.find_one({"id": pengabsen_id}, {"_id": 0})
    if not pengabsen:
        raise HTTPException(status_code=404, detail="Pengabsen kelas tidak ditemukan")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    
    if "username" in update_data:
        existing = await db.pengabsen_kelas.find_one({"username": update_data["username"], "id": {"$ne": pengabsen_id}})
        if existing:
            raise HTTPException(status_code=400, detail="Username sudah digunakan")
    
    if "kelas_ids" in update_data:
        for kelas_id in update_data["kelas_ids"]:
            kelas = await db.kelas.find_one({"id": kelas_id}, {"_id": 0})
            if not kelas:
                raise HTTPException(status_code=404, detail=f"Kelas {kelas_id} tidak ditemukan")
    
    if update_data:
        await db.pengabsen_kelas.update_one({"id": pengabsen_id}, {"$set": update_data})
        pengabsen.update(update_data)

    return PengabsenKelasResponse(**pengabsen)


# ==================== PENGABSEN PMQ PWA ABSENSI ====================


@api_router.get("/pmq/pengabsen/absensi-hari-ini")
async def get_pmq_pengabsen_absensi_hari_ini(
    tanggal: str,
    sesi: str,
    current_pengabsen: dict = Depends(get_current_pengabsen_pmq),
):
    """Data absensi hari ini per siswa untuk pengabsen PMQ.

    Hanya mengembalikan siswa di kelompok yang dimiliki pengabsen.
    """
    kelompok_ids = current_pengabsen.get("kelompok_ids", []) or []
    if not kelompok_ids:
        return {"data": []}

    # Ambil siswa PMQ di kelompok tersebut
    siswa_list = await db.siswa_pmq.find(
        {"kelompok_id": {"$in": kelompok_ids}},
        {"_id": 0},
    ).to_list(5000)

    if not siswa_list:
        return {"data": []}

    siswa_ids = [s["id"] for s in siswa_list]

    # Ambil absensi existing
    absensi_list = await db.absensi_pmq.find(
        {"siswa_id": {"$in": siswa_ids}, "tanggal": tanggal, "sesi": sesi},
        {"_id": 0},
    ).to_list(20000)
    abs_map = {(a["siswa_id"], a.get("kelompok_id")): a for a in absensi_list}

    # Map kelompok & tingkatan untuk label
    kelompok_docs = await db.pmq_kelompok.find({"id": {"$in": kelompok_ids}}, {"_id": 0}).to_list(1000)
    kelompok_map = {k["id"]: k for k in kelompok_docs}
    tingkatan_map = {t["key"]: t["label"] for t in PMQ_TINGKATAN}

    result = []
    for s in siswa_list:
        k_id = s.get("kelompok_id")
        a = abs_map.get((s["id"], k_id))
        kelompok = kelompok_map.get(k_id)

        result.append(
            {
                "siswa_id": s["id"],
                "nama": s.get("nama", "-"),
                "tingkatan_key": s.get("tingkatan_key", ""),
                "tingkatan_label": tingkatan_map.get(s.get("tingkatan_key", ""), s.get("tingkatan_key", "")),
                "kelompok_id": k_id,
                "kelompok_nama": kelompok.get("nama") if kelompok else None,
                "status": a.get("status") if a else None,
            }
        )

    return {"data": result}


@api_router.post("/pmq/pengabsen/absensi")
async def upsert_pmq_pengabsen_absensi(
    data: PMQAbsensi,
    current_pengabsen: dict = Depends(get_current_pengabsen_pmq),
):
    """Upsert absensi PMQ untuk satu siswa pada tanggal + sesi tertentu."""
    if data.kelompok_id and data.kelompok_id not in (current_pengabsen.get("kelompok_ids") or []):
        raise HTTPException(status_code=403, detail="Tidak boleh mengakses kelompok ini")

    # Upsert by siswa + tanggal + sesi
    query = {"siswa_id": data.siswa_id, "tanggal": data.tanggal, "sesi": data.sesi}
    update = {
        "$set": {
            "status": data.status,
            "kelompok_id": data.kelompok_id,
            "pengabsen_id": current_pengabsen["id"],
            "waktu_absen": datetime.now(timezone.utc).isoformat(),
        }
    }

    await db.absensi_pmq.update_one(query, update, upsert=True)

    return {"message": "Absensi berhasil disimpan"}


@api_router.post("/pmq/pengabsen/absensi/scan")
async def scan_pmq_pengabsen_absensi(
    payload: Dict[str, Any],
    sesi: str,
    tanggal: Optional[str] = None,
    current_pengabsen: dict = Depends(get_current_pengabsen_pmq),
):
    """Catat absensi via scan QR.

    QR bisa berupa:
    - {{"type": "siswa_pmq", "id": <siswa_pmq_id>}}
    - QR santri biasa (berisi santri_id) → cari siswa_pmq dengan santri_id tsb.
    """
    tanggal = tanggal or get_today_local_iso()

    siswa_id = None
    kelompok_id = None

    if payload.get("type") == "siswa_pmq":
        siswa = await db.siswa_pmq.find_one({"id": payload.get("id")}, {"_id": 0})
    else:
        # Asumsikan santri QR → cari siswa_pmq yang link dengan santri_id
        santri_id = payload.get("santri_id") or payload.get("id")
        siswa = await db.siswa_pmq.find_one({"santri_id": santri_id}, {"_id": 0})

    if not siswa:
        raise HTTPException(status_code=404, detail="Siswa PMQ tidak ditemukan")

    siswa_id = siswa["id"]
    kelompok_id = siswa.get("kelompok_id")

    if kelompok_id and kelompok_id not in (current_pengabsen.get("kelompok_ids") or []):
        raise HTTPException(status_code=403, detail="Siswa bukan bagian dari kelompok Anda")

    query = {"siswa_id": siswa_id, "tanggal": tanggal, "sesi": sesi}
    update = {
        "$set": {
            "status": "hadir",
            "kelompok_id": kelompok_id,
            "pengabsen_id": current_pengabsen["id"],
            "waktu_absen": datetime.now(timezone.utc).isoformat(),
        }
    }

    await db.absensi_pmq.update_one(query, update, upsert=True)

    return {"message": "Absensi via scan berhasil disimpan"}


@api_router.get("/pmq/pengabsen/riwayat")
async def get_pmq_pengabsen_riwayat(
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    sesi: Optional[str] = None,
    current_pengabsen: dict = Depends(get_current_pengabsen_pmq),
):
    """Riwayat absensi PMQ untuk pengabsen (hanya kelompok yang dimiliki)."""
    if not tanggal_end:
        tanggal_end = tanggal_start

    kelompok_ids = current_pengabsen.get("kelompok_ids", []) or []
    if not kelompok_ids:
        return {"detail": []}

    query: Dict[str, Any] = {
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end},
        "kelompok_id": {"$in": kelompok_ids},
    }
    if sesi:
        query["sesi"] = sesi

    absensi_list = await db.absensi_pmq.find(query, {"_id": 0}).to_list(20000)

    if not absensi_list:
        return {"detail": []}

    siswa_ids = list({a["siswa_id"] for a in absensi_list})
    siswa_list = await db.siswa_pmq.find({"id": {"$in": siswa_ids}}, {"_id": 0}).to_list(5000)
    siswa_map = {s["id"]: s for s in siswa_list}

    kelompok_docs = await db.pmq_kelompok.find({"id": {"$in": kelompok_ids}}, {"_id": 0}).to_list(1000)
    kelompok_map = {k["id"]: k for k in kelompok_docs}

    tingkatan_map = {t["key"]: t["label"] for t in PMQ_TINGKATAN}

    detail: List[Dict[str, Any]] = []
    for a in absensi_list:
        siswa = siswa_map.get(a["siswa_id"])
        if not siswa:
            continue
        k_id = a.get("kelompok_id")
        kelompok = kelompok_map.get(k_id)

        detail.append(
            {
                "id": a.get("id"),
                "siswa_id": a["siswa_id"],
                "siswa_nama": siswa.get("nama", "-"),
                "tingkatan_key": siswa.get("tingkatan_key", ""),
                "tingkatan_label": tingkatan_map.get(
                    siswa.get("tingkatan_key", ""), siswa.get("tingkatan_key", "")
                ),
                "kelompok_id": k_id,
                "kelompok_nama": kelompok.get("nama") if kelompok else None,
                "tanggal": a.get("tanggal", ""),
                "sesi": a.get("sesi", ""),
                "status": a.get("status", ""),
                "waktu_absen": a.get("waktu_absen"),
            }
        )

    # Urutkan agar rapih
    detail.sort(key=lambda x: (x["tanggal"], x["kelompok_nama"] or "", x["siswa_nama"]))

    return {"detail": detail}


@api_router.post("/pengabsen-kelas/{pengabsen_id}/regenerate-kode-akses", response_model=PengabsenKelasResponse)
async def regenerate_pengabsen_kelas_kode(pengabsen_id: str, _: dict = Depends(get_current_admin)):
    pengabsen = await db.pengabsen_kelas.find_one({"id": pengabsen_id}, {"_id": 0})
    if not pengabsen:
        raise HTTPException(status_code=404, detail="Pengabsen kelas tidak ditemukan")
    
    new_kode = generate_kode_akses()
    await db.pengabsen_kelas.update_one({"id": pengabsen_id}, {"$set": {"kode_akses": new_kode}})
    
    updated = await db.pengabsen_kelas.find_one({"id": pengabsen_id}, {"_id": 0})
    return PengabsenKelasResponse(**updated)

@api_router.delete("/pengabsen-kelas/{pengabsen_id}")
async def delete_pengabsen_kelas(pengabsen_id: str, _: dict = Depends(get_current_admin)):
    result = await db.pengabsen_kelas.delete_one({"id": pengabsen_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Pengabsen kelas tidak ditemukan")
    return {"message": "Pengabsen kelas berhasil dihapus"}

@api_router.post("/pengabsen-kelas/login", response_model=PengabsenKelasTokenResponse)
async def login_pengabsen_kelas(data: PengabsenKelasLoginRequest):
    pengabsen = await db.pengabsen_kelas.find_one({"username": data.username}, {"_id": 0})
    if not pengabsen or pengabsen["kode_akses"] != data.kode_akses:
        raise HTTPException(status_code=401, detail="Username atau kode akses salah")
    
    access_token = create_access_token(data={"sub": pengabsen["id"]})
    
    user_data = PengabsenKelasMeResponse(
        id=pengabsen["id"],
        nama=pengabsen["nama"],
        username=pengabsen["username"],
        email_atau_hp=pengabsen.get("email_atau_hp", ""),
        kelas_ids=pengabsen.get("kelas_ids", []),
        created_at=pengabsen["created_at"]
    )
    
    return PengabsenKelasTokenResponse(access_token=access_token, user=user_data)

@api_router.get("/pengabsen-kelas/me", response_model=PengabsenKelasMeResponse)
async def get_pengabsen_kelas_me(current_pengabsen: dict = Depends(get_current_pengabsen_kelas)):
    return PengabsenKelasMeResponse(**current_pengabsen)

# ==================== PEMBIMBING KELAS ENDPOINTS ====================

@api_router.get("/pembimbing-kelas", response_model=List[PembimbingKelasResponse])
async def get_pembimbing_kelas_list(_: dict = Depends(get_current_admin)):
    pembimbing_list = await db.pembimbing_kelas.find({}, {"_id": 0}).to_list(1000)
    return [PembimbingKelasResponse(**p) for p in pembimbing_list]

@api_router.post("/pembimbing-kelas", response_model=PembimbingKelasResponse)
async def create_pembimbing_kelas(data: PembimbingKelasCreate, _: dict = Depends(get_current_admin)):
    # Validate kelas_ids
    for kelas_id in data.kelas_ids:
        kelas = await db.kelas.find_one({"id": kelas_id}, {"_id": 0})
        if not kelas:
            raise HTTPException(status_code=404, detail=f"Kelas {kelas_id} tidak ditemukan")
    
    # Check username uniqueness
    existing = await db.pembimbing_kelas.find_one({"username": data.username})
    if existing:
        raise HTTPException(status_code=400, detail="Username sudah digunakan")
    
    pembimbing = PembimbingKelas(
        **data.model_dump(),
        kode_akses=generate_kode_akses()
    )
    
    doc = pembimbing.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.pembimbing_kelas.insert_one(doc)
    
    return PembimbingKelasResponse(**pembimbing.model_dump())

@api_router.put("/pembimbing-kelas/{pembimbing_id}", response_model=PembimbingKelasResponse)
async def update_pembimbing_kelas(pembimbing_id: str, data: PembimbingKelasUpdate, _: dict = Depends(get_current_admin)):
    pembimbing = await db.pembimbing_kelas.find_one({"id": pembimbing_id}, {"_id": 0})
    if not pembimbing:
        raise HTTPException(status_code=404, detail="Pembimbing kelas tidak ditemukan")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    
    if "username" in update_data:
        existing = await db.pembimbing_kelas.find_one({"username": update_data["username"], "id": {"$ne": pembimbing_id}})
        if existing:
            raise HTTPException(status_code=400, detail="Username sudah digunakan")
    
    if "kelas_ids" in update_data:
        for kelas_id in update_data["kelas_ids"]:
            kelas = await db.kelas.find_one({"id": kelas_id}, {"_id": 0})
            if not kelas:
                raise HTTPException(status_code=404, detail=f"Kelas {kelas_id} tidak ditemukan")
    
    if update_data:
        await db.pembimbing_kelas.update_one({"id": pembimbing_id}, {"$set": update_data})
    
    updated = await db.pembimbing_kelas.find_one({"id": pembimbing_id}, {"_id": 0})
    return PembimbingKelasResponse(**updated)

@api_router.post("/pembimbing-kelas/{pembimbing_id}/regenerate-kode-akses", response_model=PembimbingKelasResponse)
async def regenerate_pembimbing_kelas_kode(pembimbing_id: str, _: dict = Depends(get_current_admin)):
    pembimbing = await db.pembimbing_kelas.find_one({"id": pembimbing_id}, {"_id": 0})
    if not pembimbing:
        raise HTTPException(status_code=404, detail="Pembimbing kelas tidak ditemukan")
    
    new_kode = generate_kode_akses()
    await db.pembimbing_kelas.update_one({"id": pembimbing_id}, {"$set": {"kode_akses": new_kode}})
    
    updated = await db.pembimbing_kelas.find_one({"id": pembimbing_id}, {"_id": 0})
    return PembimbingKelasResponse(**updated)

@api_router.delete("/pembimbing-kelas/{pembimbing_id}")
async def delete_pembimbing_kelas(pembimbing_id: str, _: dict = Depends(get_current_admin)):
    result = await db.pembimbing_kelas.delete_one({"id": pembimbing_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Pembimbing kelas tidak ditemukan")
    return {"message": "Pembimbing kelas berhasil dihapus"}

@api_router.post("/pembimbing-kelas/login", response_model=PembimbingKelasTokenResponse)
async def login_pembimbing_kelas(data: PembimbingKelasLoginRequest):
    pembimbing = await db.pembimbing_kelas.find_one({"username": data.username}, {"_id": 0})
    if not pembimbing or pembimbing["kode_akses"] != data.kode_akses:
        raise HTTPException(status_code=401, detail="Username atau kode akses salah")
    
    access_token = create_access_token(data={"sub": pembimbing["id"]})
    
    user_data = PembimbingKelasMeResponse(
        id=pembimbing["id"],
        nama=pembimbing["nama"],
        username=pembimbing["username"],
        email_atau_hp=pembimbing.get("email_atau_hp", ""),
        kelas_ids=pembimbing.get("kelas_ids", []),
        created_at=pembimbing["created_at"]
    )
    
    return PembimbingKelasTokenResponse(access_token=access_token, user=user_data)

@api_router.get("/pembimbing-kelas/me", response_model=PembimbingKelasMeResponse)
async def get_pembimbing_kelas_me(current_pembimbing: dict = Depends(get_current_pembimbing_kelas)):
    return PembimbingKelasMeResponse(**current_pembimbing)

@api_router.get("/pembimbing-kelas/statistik")
async def get_pembimbing_kelas_statistik(current_pembimbing: dict = Depends(get_current_pembimbing_kelas)):
    """Get statistics for Pembimbing Kelas dashboard"""
    kelas_ids = current_pembimbing.get("kelas_ids", [])
    
    # Get all kelas info
    kelas_list = await db.kelas.find({"id": {"$in": kelas_ids}}, {"_id": 0}).to_list(1000)
    
    # Get total siswa across all kelas
    total_siswa = await db.siswa_madrasah.count_documents({"kelas_id": {"$in": kelas_ids}})
    
    # Get today's attendance
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    absensi_today = await db.absensi_kelas.find({
        "kelas_id": {"$in": kelas_ids},
        "tanggal": today
    }, {"_id": 0}).to_list(10000)
    
    # Count by status
    hadir = sum(1 for a in absensi_today if a["status"] == "hadir")
    alfa = sum(1 for a in absensi_today if a["status"] == "alfa")
    izin = sum(1 for a in absensi_today if a["status"] == "izin")
    sakit = sum(1 for a in absensi_today if a["status"] == "sakit")
    
    # Per kelas stats
    kelas_stats = []
    for kelas in kelas_list:
        siswa_count = await db.siswa_madrasah.count_documents({"kelas_id": kelas["id"]})
        absensi_count = sum(1 for a in absensi_today if a["kelas_id"] == kelas["id"])
        
        kelas_stats.append({
            "kelas_id": kelas["id"],
            "kelas_nama": kelas["nama"],
            "total_siswa": siswa_count,
            "sudah_absen": absensi_count,
            "belum_absen": siswa_count - absensi_count
        })
    
    return {
        "total_kelas": len(kelas_list),
        "total_siswa": total_siswa,
        "hari_ini": {
            "hadir": hadir,
            "alfa": alfa,
            "izin": izin,
            "sakit": sakit,
            "total_absen": len(absensi_today)
        },
        "per_kelas": kelas_stats
    }

@api_router.get("/pembimbing-kelas/absensi-riwayat")
async def get_pembimbing_kelas_riwayat(
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    kelas_id: Optional[str] = None,
    current_pembimbing: dict = Depends(get_current_pembimbing_kelas)
):
    """Get attendance history for Pembimbing Kelas"""
    if not tanggal_end:
        tanggal_end = tanggal_start
    
    kelas_ids = current_pembimbing.get("kelas_ids", [])
    
    query = {
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end},
        "kelas_id": {"$in": kelas_ids}
    }
    
    if kelas_id:
        query["kelas_id"] = kelas_id
    
    absensi_list = await db.absensi_kelas.find(query, {"_id": 0}).to_list(10000)
    
    # Enrich with siswa and kelas names
    siswa_map = {}
    siswa_list = await db.siswa_madrasah.find({}, {"_id": 0}).to_list(10000)
    for siswa in siswa_list:
        siswa_map[siswa["id"]] = siswa["nama"]
    
    kelas_map = {}
    kelas_list = await db.kelas.find({}, {"_id": 0}).to_list(1000)
    for kelas in kelas_list:
        kelas_map[kelas["id"]] = kelas["nama"]
    
    result = []
    for absensi in absensi_list:
        result.append(AbsensiKelasResponse(
            **absensi,
            siswa_nama=siswa_map.get(absensi["siswa_id"], "Unknown"),
            kelas_nama=kelas_map.get(absensi["kelas_id"], "Unknown")
        ))
    
    return result


@api_router.get("/madin/absensi/riwayat")
async def get_madin_absensi_riwayat(
    tanggal_start: str,
    tanggal_end: Optional[str] = None,
    kelas_id: Optional[str] = None,
    gender: Optional[str] = None,
    _: dict = Depends(get_current_admin),
):
    """Riwayat absensi Madrasah Diniyah untuk admin (ringkasan dan detail).

    Data diambil dari koleksi absensi_kelas dan digabung dengan siswa_madrasah + kelas.
    """
    if not tanggal_end:
        tanggal_end = tanggal_start

    # Query utama ke absensi_kelas
    query: Dict[str, Any] = {
        "tanggal": {"$gte": tanggal_start, "$lte": tanggal_end},
    }
    if kelas_id:
        query["kelas_id"] = kelas_id

    absensi_list = await db.absensi_kelas.find(query, {"_id": 0}).to_list(10000)

    if not absensi_list:
        return {"summary": {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "telat": 0}, "detail": []}

    # Ambil siswa & kelas untuk enrichment + filter gender
    siswa_map: Dict[str, Dict[str, Any]] = {}
    siswa_list = await db.siswa_madrasah.find({}, {"_id": 0}).to_list(10000)
    for siswa in siswa_list:
        siswa_map[siswa["id"]] = siswa

    kelas_map: Dict[str, Dict[str, Any]] = {}
    kelas_list = await db.kelas.find({}, {"_id": 0}).to_list(1000)
    for kelas in kelas_list:
        kelas_map[kelas["id"]] = kelas

    detail: List[Dict[str, Any]] = []
    summary = {"hadir": 0, "alfa": 0, "sakit": 0, "izin": 0, "telat": 0}

    for absensi in absensi_list:
        siswa = siswa_map.get(absensi["siswa_id"])
        kelas = kelas_map.get(absensi["kelas_id"])

        # Jika siswa tidak ditemukan, lewati (data tidak konsisten)
        if not siswa:
            continue

        siswa_gender = siswa.get("gender") or siswa.get("jenis_kelamin") or ""
        # Normalisasi: misal "putra" / "L" / "laki-laki" -> tetap kita simpan apa adanya

        if gender and gender != "all":
            # gender filter di frontend: "putra" atau "putri"
            if siswa_gender != gender:
                continue

        status = absensi.get("status", "")
        if status in summary:
            summary[status] += 1

        # Bangun record detail
        row = {
            "id": absensi.get("id"),
            "siswa_id": absensi.get("siswa_id"),
            "siswa_nama": siswa.get("nama") if siswa else "Unknown",
            "kelas_id": absensi.get("kelas_id"),
            "kelas_nama": kelas.get("nama") if kelas else "Unknown",
            "tanggal": absensi.get("tanggal"),
            "status": status,
            "gender": siswa_gender,
            "waktu_absen": absensi.get("waktu_absen"),
        }
        detail.append(row)

    return {"summary": summary, "detail": detail}

# Include router
app.include_router(api_router)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
