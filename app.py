import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import os
import io
import datetime
import hashlib
import zipfile
import re

# Page Config
st.set_page_config(
    page_title="ALASAR GRUP - Kalite Yönetim Sistemi",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main { padding: 1rem 2rem; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; }
    .metric-card {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        padding: 15px;
        border-radius: 8px;
        text-align: center;
    }
    .status-active { color: #28a745; font-weight: bold; }
    .status-archive { color: #dc3545; font-weight: bold; }
    div[data-testid="stSidebarUserContent"] { padding-top: 1rem; }
</style>
""", unsafe_allow_html=True)

# Directories
DOC_DIR = "yuklenen_belgeler"
ARCHIVE_DIR = "arsivlenenler"
os.makedirs(DOC_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

def hash_password(password: str) -> str:
    """Şifreyi SHA-256 ile hashler."""
    return hashlib.sha256(password.encode()).hexdigest()

def clean_filename(filename: str) -> str:
    """Dosya adındaki Türkçe karakterleri ve özel simgeleri temizler."""
    char_map = {
        'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G', 'ı': 'i', 'İ': 'I',
        'ö': 'o', 'Ö': 'O', 'ş': 's', 'Ş': 'S', 'ü': 'u', 'Ü': 'U',
        ' ': '_'
    }
    for search, replace in char_map.items():
        filename = filename.replace(search, replace)
    filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '', filename)
    return filename

@st.cache_resource
def init_gspread():
    """Google Sheets bağlantısını kurar."""
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(
                st.secrets["gcp_service_account"], 
                scopes=scope
            )
        else:
            creds = Credentials.from_service_account_file(
                "credentials.json", 
                scopes=scope
            )
            
        client = gspread.authorize(creds)
        
        sheet_name = "alasarvt"
        try:
            spreadsheet = client.open(sheet_name)
        except gspread.SpreadsheetNotFound:
            st.error(f" Google Sheets '{sheet_name}' bulunamadı. Lütfen tablo adını ve servis hesabı yetkilerini kontrol edin.")
            return None
            
        return spreadsheet
    except Exception as e:
        st.error(f"Google Sheets bağlantı hatası: {str(e)}")
        return None

def load_users_from_sheets(spreadsheet):
    """
    Google Sheets üzerindeki 'kullanicilar' sekmesinden 
    kullanıcı verilerini dinamik olarak okur.
    """
    if not spreadsheet:
        return {}
    
    try:
        try:
            worksheet = spreadsheet.worksheet("kullanicilar")
        except gspread.WorksheetNotFound:
            st.warning("Google Sheets üzerinde 'kullanicilar' sayfası bulunamadı!")
            return {}

        records = worksheet.get_all_records()
        users = {}
        for row in records:
            username = str(row.get("KullaniciAdi", "")).strip()
            if username:
                raw_password = str(row.get("Sifre", "")).strip()
                users[username] = {
                    "password_hash": hash_password(raw_password),
                    "name": str(row.get("AdSoyad", "")).strip(),
                    "role": str(row.get("Rol", "")).strip(),
                    "department": str(row.get("Departman", "")).strip(),
                    "status": str(row.get("Durum", "Aktif")).strip(),
                    "permission": str(row.get("Yetki", "OKUMA")).strip()
                }
        return users
    except Exception as e:
        st.error(f"Kullanıcı verileri okunurken hata oluştu: {str(e)}")
        return {}

def load_data(spreadsheet, sheet_name):
    """Belirtilen Google Sheets sayfasından verileri yükler."""
    if not spreadsheet:
        return pd.DataFrame()
    try:
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return pd.DataFrame()
        
        data = worksheet.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Veri yükleme hatası ({sheet_name}): {str(e)}")
        return pd.DataFrame()

def save_data(spreadsheet, df, sheet_name):
    """Dataframe verisini Google Sheets sayfasına kaydeder."""
    if not spreadsheet:
        return False
    try:
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="1000", cols="20")
            
        worksheet.clear()
        
        if not df.empty:
            df_filled = df.fillna("")
            data_to_write = [df_filled.columns.tolist()] + df_filled.values.tolist()
            worksheet.update(data_to_write)
        return True
    except Exception as e:
        st.error(f"Veri kaydetme hatası ({sheet_name}): {str(e)}")
        return False

def add_notification(spreadsheet, title, detail, user_name):
    """Yeni bildirim ekler."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_notif = pd.DataFrame([{
        "Tarih": now,
        "Baslik": title,
        "Detay": detail,
        "Kullanici": user_name
    }])
    
    current_df = load_data(spreadsheet, "Bildirimler")
    updated_df = pd.concat([new_notif, current_df], ignore_index=True)
    save_data(spreadsheet, updated_df, "Bildirimler")

# Session State Initialization
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_info" not in st.session_state:
    st.session_state.user_info = None

# Initialize GS Connection
spreadsheet = init_gspread()

# Giriş Ekranı
if not st.session_state.logged_in:
    st.title("🏢 ALASAR GRUP")
    st.subheader("Kalite Yönetim Sistemi - Giriş")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        username_input = st.text_input("Kullanıcı Adı").strip().lower()
        password_input = st.text_input("Şifre", type="password").strip()
        login_btn = st.button("Giriş Yap")
        
        if login_btn:
            if not username_input or not password_input:
                st.warning("Lütfen kullanıcı adı ve şifrenizi girin.")
            else:
                # Kullanıcı verilerini canlı olarak Google Sheets 'kullanicilar' sayfasından çek
                users_db = load_users_from_sheets(spreadsheet)
                
                if username_input in users_db:
                    user_data = users_db[username_input]
                    if user_data.get("status") != "Aktif":
                        st.error("Bu kullanıcı hesabı pasife alınmıştır.")
                    elif user_data["password_hash"] == hash_password(password_input):
                        st.session_state.logged_in = True
                        st.session_state.user_info = {
                            "username": username_input,
                            "name": user_data["name"],
                            "role": user_data["role"],
                            "department": user_data["department"],
                            "permission": user_data["permission"]
                        }
                        st.success(f"Hoş geldiniz, {user_data['name']}!")
                        st.rerun()
                    else:
                        st.error("Hatalı şifre!")
                else:
                    st.error("Kullanıcı bulunamadı!")
    st.stop()

# Oturum Açılmış Durum
user = st.session_state.user_info

# Sidebar
with st.sidebar:
    st.title("🏢 ALASAR GRUP")
    st.markdown("---")
    st.write(f"**Kullanıcı:** {user['name']}")
    st.write(f"**Departman:** {user['department']}")
    st.write(f"**Rol / Yetki:** {user['role']} ({user['permission']})")
    st.markdown("---")
    
    # Navigasyon Menüsü
    departments = ["Genel Bakış", "Kalite", "İK", "Yonetim", "Uretim", "Satın alma", "Bildirim Geçmişi", "Arama Merkezi"]
    
    # Superadmin Özel Paneli (ÖMER OCAK)
    if user["username"] == "omer.ocak" or user["role"] == "Admin":
        departments.append("⚙️ SİSTEM YÖNETİMİ & BAKIŞ")
        
    selected_menu = st.radio("Menü Seçimi", departments)
    
    st.markdown("---")
    if st.button("Çıkış Yap"):
        st.session_state.logged_in = False
        st.session_state.user_info = None
        st.rerun()

# ---------------------------------------------------------
# MENÜ İÇERİKLERİ
# ---------------------------------------------------------

# 1. GENEL BAKIŞ
if selected_menu == "Genel Bakış":
    st.header("📌 Genel Bakış ve Özet Panel")
    
    docs_df = load_data(spreadsheet, "Departman_Dokumanlari")
    archive_df = load_data(spreadsheet, "Arsiv_Dokumanlari")
    
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Aktif Doküman Sayısı", len(docs_df) if not docs_df.empty else 0)
    with c2:
        st.metric("Arşivlenen Doküman Sayısı", len(archive_df) if not archive_df.empty else 0)
    with c3:
        st.metric("Sistem Durumu", "Aktif / Canlı", delta_color="normal")
        
    st.markdown("---")
    st.subheader("📢 Son Bildirimler")
    notif_df = load_data(spreadsheet, "Bildirimler")
    if not notif_df.empty:
        st.dataframe(notif_df.head(10), use_container_width=True)
    else:
        st.info("Henüz bildirim bulunmuyor.")

# 2. ARAMA MERKEZİ
elif selected_menu == "Arama Merkezi":
    st.header("🔍 Tüm Dokümanlarda Arama")
    
    query = st.text_input("Aramak istediğiniz anahtar kelime, doküman kodu veya adı giriniz:").strip().lower()
    
    if query:
        docs_df = load_data(spreadsheet, "Departman_Dokumanlari")
        archive_df = load_data(spreadsheet, "Arsiv_Dokumanlari")
        
        tab1, tab2 = st.tabs(["Aktif Dokümanlar", "Arşivdeki Dokümanlar"])
        
        with tab1:
            if not docs_df.empty:
                filtered_active = docs_df[
                    docs_df.apply(lambda row: query in str(row.values).lower(), axis=1)
                ]
                st.dataframe(filtered_active, use_container_width=True)
            else:
                st.info("Aktif doküman kaydı bulunamadı.")
                
        with tab2:
            if not archive_df.empty:
                filtered_archive = archive_df[
                    archive_df.apply(lambda row: query in str(row.values).lower(), axis=1)
                ]
                st.dataframe(filtered_archive, use_container_width=True)
            else:
                st.info("Arşiv doküman kaydı bulunamadı.")

# 3. BİLDİRİM GEÇMİŞİ
elif selected_menu == "Bildirim Geçmişi":
    st.header("🔔 Tüm İşlem Bildirimleri")
    notif_df = load_data(spreadsheet, "Bildirimler")
    if not notif_df.empty:
        st.dataframe(notif_df, use_container_width=True)
    else:
        st.info("Sistemde henüz kayıtlı bir bildirim bulunmamaktadır.")

# 4. SİSTEM YÖNETİMİ & BAKIŞ (Sadece Admin / Ömer OCAK)
elif selected_menu == "⚙️ SİSTEM YÖNETİMİ & BAKIŞ":
    st.header("⚙️ Sistem Yönetimi ve Toplu Arşiv Paneli")
    
    st.warning("Bu panel üst düzey yöneticiler içindir.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📦 Tüm Yüklenen Dosyaları İndir (ZIP)")
        if st.button("Tüm Aktif Belgeleri ZIP Olarak Hazırla"):
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                for root, dirs, files in os.walk(DOC_DIR):
                    for file in files:
                        filepath = os.path.join(root, file)
                        zip_file.write(filepath, file)
                        
            st.download_button(
                label="⬇️ ZIP Dosyasını İndir",
                data=zip_buffer.getvalue(),
                file_name=f"Alasar_Kalite_Tum_Belgeler_{datetime.date.today()}.zip",
                mime="application/zip"
            )

# 5. DEPARTMAN YÖNETİMİ (Kalite, İK, Yonetim, Uretim, Satın alma)
else:
    dept_name = selected_menu
    st.header(f"📁 {dept_name} Departmanı Doküman Yönetimi")
    
    docs_df = load_data(spreadsheet, "Departman_Dokumanlari")
    
    # Departman Filtrelemesi
    if not docs_df.empty and "Departman" in docs_df.columns:
        dept_docs = docs_df[docs_df["Departman"] == dept_name]
    else:
        dept_docs = pd.DataFrame()
        
    tab_list, tab_add = st.tabs(["📄 Mevcut Dokümanlar", "➕ Yeni Doküman Yükle"])
    
    with tab_list:
        st.subheader(f"{dept_name} Aktif Doküman Listesi")
        if not dept_docs.empty:
            st.dataframe(dept_docs, use_container_width=True)
        else:
            st.info(f"{dept_name} departmanına ait henüz aktif doküman yüklenmemiştir.")
            
    with tab_add:
        st.subheader("Yeni Doküman Ekle / Revize Et")
        
        if user["permission"] != "DÜZENLEME":
            st.warning("Bu departmana yeni doküman yüklemek için 'DÜZENLEME' yetkiniz olması gerekmektedir.")
        else:
            with st.form("add_doc_form"):
                doc_no = st.text_input("Doküman Kodu / No (Örn: PR-01)").strip()
                doc_title = st.text_input("Doküman Adı").strip()
                doc_rev = st.text_input("Revizyon No", value="00").strip()
                uploaded_file = st.file_uploader("Dosya Seçiniz")
                doc_desc = st.text_area("Açıklama / Notlar").strip()
                
                submit_btn = st.form_submit_button("Sisteme Kaydet ve Yükle")
                
                if submit_btn:
                    if not doc_no or not doc_title or not uploaded_file:
                        st.error("Lütfen Doküman Kodu, Adı ve Dosya alanlarını eksiksiz doldurun.")
                    else:
                        ext = os.path.splitext(uploaded_file.name)[1]
                        safe_title = clean_filename(doc_title)
                        safe_no = clean_filename(doc_no)
                        
                        target_filename = f"{safe_no}_{safe_title}_R{doc_rev}{ext}"
                        save_path = os.path.join(DOC_DIR, target_filename)
                        
                        # Dosyayı diske kaydet
                        with open(save_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())
                            
                        # Google Sheets Kaydı
                        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        new_record = {
                            "DokumanNo": doc_no,
                            "DokumanAdi": doc_title,
                            "Revizyon": doc_rev,
                            "Departman": dept_name,
                            "Yukleyen": user["name"],
                            "Tarih": now,
                            "DosyaYolu": save_path,
                            "Aciklama": doc_desc
                        }
                        
                        updated_docs = pd.concat([docs_df, pd.DataFrame([new_record])], ignore_index=True)
                        save_data(spreadsheet, updated_docs, "Departman_Dokumanlari")
                        
                        # Bildirim Ekle
                        add_notification(
                            spreadsheet, 
                            "Yeni Doküman Yüklendi", 
                            f"{dept_name} departmanına '{doc_title}' ({doc_no}) eklendi.", 
                            user["name"]
                        )
                        
                        st.success(f"'{target_filename}' başarıyla yüklendi ve veritabanına işlendi.")
                        st.rerun()
