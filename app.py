import streamlit as st
import pandas as pd
import datetime
import os
import json
import shutil
import re
import zipfile
import io
import gspread
from google.oauth2.service_account import Credentials

# --- 1. ÇALIŞMA DİZİNİ VE GÜVENLİK KİLİDİ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

st.set_page_config(page_title="ALASAR GRUP - Kalite Yönetim Sistemi", page_icon="🛡️", layout="wide")

# --- DOSYA, KLASÖR VE GOOGLE SHEETS AYARLARI ---
SPREADSHEET_ID = "1sepPuuUSmJg3g2Yw-ZXjMLtmut2DiatH4sLqJFDiano"
CREDS_FILE = os.path.join(BASE_DIR, "credentials.json")
USERS_FILE = os.path.join(BASE_DIR, "users.json")
UPLOAD_DIR = os.path.join(BASE_DIR, "yuklenen_belgeler")
ARCHIVE_DIR = os.path.join(BASE_DIR, "arsivlenenler")

for d in [UPLOAD_DIR, ARCHIVE_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

# --- GOOGLE SHEETS BAGLANTI FONKSIYONU ---
@st.cache_resource
def get_gspread_client():
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    if not os.path.exists(CREDS_FILE):
        st.error("⚠️ 'credentials.json' dosyası bulunamadı! Lütfen Google Cloud servis hesabı anahtarınızı proje klasörüne ekleyin.")
        st.stop()
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)

def get_spreadsheet():
    client = get_gspread_client()
    return client.open_by_key(SPREADSHEET_ID)

# --- GOOGLE SHEETS VERİ TABANI YÖNETİMİ ---
def init_google_sheets_db():
    """Tabloda eksik sekmeler varsa oluşturur ve başlıkları yazar."""
    sh = get_spreadsheet()
    existing_sheets = [ws.title for ws in sh.worksheets()]
    
    headers_map = {
        "Departman_Dokumanlari": ["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Ekleyen", "Revizyon Mu"],
        "Arsiv_Dokumanlari": ["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Ekleyen", "Arşivlenme Tarihi"],
        "Bildirimler": ["Tarih / Saat", "İşlemi Yapan", "Departman / Modül", "Detay / Doküman"]
    }

    for sheet_name, headers in headers_map.items():
        if sheet_name not in existing_sheets:
            ws = sh.add_worksheet(title=sheet_name, rows=100, cols=len(headers))
            ws.append_row(headers)

def load_data(sheet_name):
    """Google Sheets üzerinden verileri Pandas DataFrame olarak okur."""
    try:
        sh = get_spreadsheet()
        ws = sh.worksheet(sheet_name)
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        
        if df.empty:
            headers_map = {
                "Departman_Dokumanlari": ["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Ekleyen", "Revizyon Mu"],
                "Arsiv_Dokumanlari": ["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Ekleyen", "Arşivlenme Tarihi"],
                "Bildirimler": ["Tarih / Saat", "İşlemi Yapan", "Departman / Modül", "Detay / Doküman"]
            }
            return pd.DataFrame(columns=headers_map.get(sheet_name, []))
        return df
    except Exception as e:
        init_google_sheets_db()
        return pd.DataFrame()

def save_data(df_new, sheet_name):
    """Pandas DataFrame'i Google Sheets sekmesine tam olarak yazar."""
    sh = get_spreadsheet()
    try:
        ws = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_name, rows=100, cols=20)

    ws.clear()
    
    # NaN ve None değerleri temizleme
    df_clean = df_new.fillna("-")
    
    # Başlıklar ve verileri birleştirip gönderme
    data_to_write = [df_clean.columns.tolist()] + df_clean.astype(str).values.tolist()
    ws.update(range_name="A1", values=data_to_write)

def add_notification(user, modul, detail):
    df_notif = load_data("Bildirimler")
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    new_notif = {
        "Tarih / Saat": now_str,
        "İşlemi Yapan": user,
        "Departman / Modül": modul,
        "Detay / Doküman": detail
    }
    df_notif = pd.concat([pd.DataFrame([new_notif]), df_notif], ignore_index=True)
    save_data(df_notif, "Bildirimler")

# --- YARDIMCI DOSYA VE ZIP FONKSİYONLARI ---
def clean_filename_part(text):
    if not text:
        return ""
    text_str = str(text).strip()
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    clean_str = text_str.translate(tr_map)
    clean_str = re.sub(r'[\\/*?:"<>|]', "", clean_str)
    clean_str = re.sub(r'\s+', "_", clean_str)
    clean_str = re.sub(r'_+', "_", clean_str)
    return clean_str

def generate_standard_filename(doc_no, doc_title, rev_no, file_extension):
    clean_no = clean_filename_part(doc_no)
    clean_title = clean_filename_part(doc_title)
    
    try:
        rev_int = int(str(rev_no).strip())
        formatted_rev = f"{rev_int:02d}"
    except (ValueError, TypeError):
        formatted_rev = str(rev_no).strip()
        
    ext = file_extension if file_extension.startswith(".") else f".{file_extension}"
    return f"{clean_no}_{clean_title}_R{formatted_rev}{ext}"

def save_uploaded_file_standard(uploaded_file, target_dir, target_filename):
    if uploaded_file is not None:
        file_path = os.path.join(target_dir, target_filename)
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        return target_filename
    return "Yok"

def create_system_zip():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for folder in [UPLOAD_DIR, ARCHIVE_DIR]:
            if os.path.exists(folder):
                for root, _, files in os.walk(folder):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.join(os.path.basename(folder), file)
                        zip_file.write(file_path, arcname)
    zip_buffer.seek(0)
    return zip_buffer

# --- KULLANICI YÖNETİMİ ---
DEFAULT_USERS = {
    "Mehmet Alaşar": {"password": "malsr3434.", "role": "Yönetici", "can_edit": False},
    "Dilber Alaşar": {"password": "dalsr4141.", "role": "Yönetici", "can_edit": True},
    "Nilay Kiraz": {"password": "nkrz5151.", "role": "İK", "can_edit": False},
    "Ömer OCAK": {"password": "oock6161.", "role": "Kalite Sistem Mühendisi", "can_edit": True}
}

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return DEFAULT_USERS
    else:
        save_users(DEFAULT_USERS)
        return DEFAULT_USERS

def save_users(users_dict):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users_dict, f, ensure_ascii=False, indent=4)

USERS = load_users()

# --- OTURUM DURUMU ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "username" not in st.session_state:
    st.session_state["username"] = ""
if "role" not in st.session_state:
    st.session_state["role"] = ""
if "can_edit" not in st.session_state:
    st.session_state["can_edit"] = False

# --- GİRİŞ EKRANI ---
if not st.session_state["logged_in"]:
    st.title("🏢 ALASAR GRUP")
    st.subheader("Kalite & Departman Yönetim Sistemi - Giriş Paneli")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            selected_user = st.selectbox("Kullanıcı Seçiniz", list(USERS.keys()))
            input_password = st.text_input("Şifre", type="password")
            submit_login = st.form_submit_button("🔑 Giriş Yap")
            
            if submit_login:
                if input_password == USERS[selected_user]["password"]:
                    st.session_state["logged_in"] = True
                    st.session_state["username"] = selected_user
                    st.session_state["role"] = USERS[selected_user]["role"]
                    st.session_state["can_edit"] = selected_user in ["Ömer OCAK", "Dilber Alaşar"]
                    st.success(f"Hoş geldiniz, {selected_user}!")
                    st.rerun()
                else:
                    st.error("Hatalı şifre! Lütfen tekrar deneyiniz.")
    st.stop()

# --- SOL MENÜ ---
st.sidebar.title("🏢 ALASAR GRUP")
st.sidebar.write(f"👤 **{st.session_state['username']}**")
st.sidebar.caption(f"Rol: {st.session_state['role']}")

if st.session_state["username"] == "Ömer OCAK":
    st.sidebar.info("⚡ Superadmin / Tam Sistem Yetkilisi")
elif st.session_state["can_edit"]:
    st.sidebar.success("✏️ Doküman Yükleme / Revize Yetkisi Var")
else:
    st.sidebar.info("👁️ Sadece Okuma / İndirme Yetkisi Var")

if st.sidebar.button("🚪 Çıkış Yap"):
    st.session_state["logged_in"] = False
    st.session_state["username"] = ""
    st.session_state["role"] = ""
    st.session_state["can_edit"] = False
    st.rerun()

st.sidebar.markdown("---")

menu_options = [
    "🔍 GENEL ARAMA MERKEZİ",
    "👥 İNSAN KAYNAKLARI DEPARTMANI",
    "⚙️ ÜRETİM DEPARTMANI",
    "👔 YÖNETİM DEPARTMANI",
    "🌐 ENTEGRE YÖNETİM SİSTEMİ DEPARTMANI",
    "🛡️ KALİTE DEPARTMANI",
    "📦 DEPO-SEVKİYAT DEPARTMANI",
    "🔔 Bildirim Geçmişi"
]

if st.session_state["username"] == "Ömer OCAK":
    menu_options.append("⚙️ SİSTEM YÖNETİMİ & BAKIŞ")

modul = st.sidebar.radio("DEPARTMANLAR VE MENÜ:", menu_options)

st.sidebar.markdown("---")

# --- ŞİFRE DEĞİŞTİRME ---
with st.sidebar.expander("🔑 Şifremi Değiştir"):
    with st.form("change_password_form", clear_on_submit=True):
        old_pass = st.text_input("Mevcut Şifre", type="password")
        new_pass = st.text_input("Yeni Şifre", type="password")
        new_pass_confirm = st.text_input("Yeni Şifre (Tekrar)", type="password")
        btn_pass = st.form_submit_button("Güncelle")
        
        if btn_pass:
            current_user = st.session_state["username"]
            if old_pass != USERS[current_user]["password"]:
                st.error("Mevcut şifreniz hatalı!")
            elif new_pass != new_pass_confirm:
                st.error("Yeni şifreler eşleşmiyor!")
            elif len(new_pass) < 4:
                st.error("Şifre en az 4 karakter olmalıdır!")
            else:
                USERS[current_user]["password"] = new_pass
                save_users(USERS)
                st.success("Şifreniz başarıyla değiştirildi!")

# --- BİLDİRİM PANELİ ---
df_notif_top = load_data("Bildirimler")
if not df_notif_top.empty:
    latest = df_notif_top.iloc[0]
    dept_val = latest.get('Departman / Modül', 'Genel')
    time_val = latest.get('Tarih / Saat', '-')
    user_val = latest.get('İşlemi Yapan', '-')
    detail_val = latest.get('Detay / Doküman', '-')
    st.info(f"🔔 **Son Güncelleme / Revizyon Bildirimi:** [{time_val}] **{user_val}** tarafından **{dept_val}** alanında işlem yapıldı: *{detail_val}*")

# --- GENEL ARAMA MERKEZİ ---
if modul == "🔍 GENEL ARAMA MERKEZİ":
    st.title("🔍 Tüm Sistem Genel Doküman Arama Merkezi")
    st.caption("Google Sheets altyapısı üzerinden tüm aktif ve arşivlenmiş dokümanları arayabilirsiniz.")

    search_query = st.text_input("🔎 Doküman Adı, Doküman No, Açıklama veya Yükleyen Kişi Ara...", key="global_search").strip().lower()
    
    df_docs = load_data("Departman_Dokumanlari")
    df_archive = load_data("Arsiv_Dokumanlari")

    tab_g1, tab_g2 = st.tabs(["📄 Aktif Dokümanlar İçinde Ara", "📁 Arşiv Dokümanları İçinde Ara"])
    
    with tab_g1:
        if not df_docs.empty:
            if search_query:
                filtered_df = df_docs[
                    df_docs["Doküman Adı"].astype(str).str.lower().str.contains(search_query) |
                    df_docs["Doküman No"].astype(str).str.lower().str.contains(search_query) |
                    df_docs["Açıklama / Not"].astype(str).str.lower().str.contains(search_query) |
                    df_docs["Departman"].astype(str).str.lower().str.contains(search_query) |
                    df_docs["Ekleyen"].astype(str).str.lower().str.contains(search_query)
                ]
            else:
                filtered_df = df_docs

            st.write(f"Bulunan Doküman Sayısı: **{len(filtered_df)}**")
            st.dataframe(filtered_df, use_container_width=True)

            for idx, row in filtered_df.iterrows():
                f_name = row.get("Dosya Adı", "Yok")
                if f_name and f_name != "Yok":
                    f_path = os.path.join(UPLOAD_DIR, f_name)
                    if os.path.exists(f_path):
                        c1, c2 = st.columns([3, 1])
                        c1.write(f"📄 **[{row.get('Departman')}]** - **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Rev: {row.get('Revizyon No')})*")
                        with open(f_path, "rb") as f:
                            c2.download_button(label="📥 İndir", data=f, file_name=f_name, key=f"glob_act_{idx}_{f_name}")
        else:
            st.info("Sistemde henüz aktif doküman bulunmuyor.")

    with tab_g2:
        if not df_archive.empty:
            if search_query:
                filtered_arch = df_archive[
                    df_archive["Doküman Adı"].astype(str).str.lower().str.contains(search_query) |
                    df_archive["Doküman No"].astype(str).str.lower().str.contains(search_query) |
                    df_archive["Açıklama / Not"].astype(str).str.lower().str.contains(search_query) |
                    df_archive["Departman"].astype(str).str.lower().str.contains(search_query) |
                    df_archive["Ekleyen"].astype(str).str.lower().str.contains(search_query)
                ]
            else:
                filtered_arch = df_archive

            st.write(f"Bulunan Arşiv Doküman Sayısı: **{len(filtered_arch)}**")
            st.dataframe(filtered_arch, use_container_width=True)

            for idx, row in filtered_arch.iterrows():
                f_name = row.get("Dosya Adı", "Yok")
                if f_name and f_name != "Yok":
                    f_path = os.path.join(ARCHIVE_DIR, f_name)
                    if os.path.exists(f_path):
                        c1, c2 = st.columns([3, 1])
                        c1.write(f"📁 **[{row.get('Departman')}]** - **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Rev: {row.get('Revizyon No')})* - Arşiv: {row.get('Arşivlenme Tarihi')}")
                        with open(f_path, "rb") as f:
                            c2.download_button(label="📥 Eski Versiyonu İndir", data=f, file_name=f_name, key=f"glob_arch_{idx}_{f_name}")
        else:
            st.info("Sistemde henüz arşivlenmiş doküman bulunmuyor.")

# --- SİSTEM YÖNETİMİ ---
elif modul == "⚙️ SİSTEM YÖNETİMİ & BAKIŞ":
    st.title("⚙️ Sistem Yönetimi ve Toplu İşlem Paneli")
    st.warning("⚠️ Bu panel sadece **Ömer OCAK** tarafından görüntülenebilir.")

    st.markdown("### 📦 1. Tüm Belgeleri Toplu İndir (ZIP)")
    zip_data = create_system_zip()
    st.download_button(
        label="📦 Tüm Sistem Dosyalarını İndir (.ZIP)",
        data=zip_data,
        file_name=f"Alasar_Tum_Sistem_Belgeleri_{datetime.date.today()}.zip",
        mime="application/zip",
        key="btn_zip_all"
    )

    st.markdown("---")
    st.markdown("### 🧹 2. Sistem Temizleme ve Tam Sıfırlama")
    st.error("🚨 **DİKKAT:** Bu işlem sistemdeki yüklü tüm dosyaları ve Google Sheets veri tabanını sıfırlar!")
    
    confirm_check = st.checkbox("Sistemdeki tüm belgeleri ve Google Sheets verilerini silmek istediğimi onaylıyorum.")
    
    if st.button("🔴 SİSTEMİ VE TÜM BELGELERİ TEMİZLE", disabled=not confirm_check):
        for folder in [UPLOAD_DIR, ARCHIVE_DIR]:
            if os.path.exists(folder):
                for filename in os.listdir(folder):
                    file_path = os.path.join(folder, filename)
                    try:
                        if os.path.isfile(file_path) or os.path.islink(file_path):
                            os.unlink(file_path)
                        elif os.path.isdir(file_path):
                            shutil.rmtree(file_path)
                    except Exception as e:
                        st.error(f"Dosya silinirken hata oluştu: {file_path} - {e}")
        
        # Google Sheets Verilerini Temizle
        empty_docs = pd.DataFrame(columns=["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Ekleyen", "Revizyon Mu"])
        empty_arch = pd.DataFrame(columns=["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Ekleyen", "Arşivlenme Tarihi"])
        empty_notif = pd.DataFrame(columns=["Tarih / Saat", "İşlemi Yapan", "Departman / Modül", "Detay / Doküman"])
        
        save_data(empty_docs, "Departman_Dokumanlari")
        save_data(empty_arch, "Arsiv_Dokumanlari")
        save_data(empty_notif, "Bildirimler")

        add_notification("Ömer OCAK", "Sistem Yönetimi", "Tüm sistem belgeleri ve Google Sheets kayıtları sıfırlandı.")
        st.success("✅ Tüm sistem belgeleri ve Google Sheets başarıyla temizlendi!")
        st.rerun()

# --- BİLDİRİM GEÇMİŞİ ---
elif modul == "🔔 Bildirim Geçmişi":
    st.title("🔔 Tüm Güncelleme & Revizyon Geçmişi")
    df_notif_all = load_data("Bildirimler")
    st.dataframe(df_notif_all, use_container_width=True)

# --- DEPARTMAN MODÜLLERİ ---
else:
    dept_name = modul.replace("👥 ", "").replace("⚙️ ", "").replace("👔 ", "").replace("🌐 ", "").replace("🛡️ ", "").replace("📦 ", "")
    st.title(f"📂 {dept_name}")
    st.caption(f"{dept_name} bünyesine ait tüm prosedür, talimat, form ve revize dokümanlar bu alanda yönetilir.")
    
    df_docs = load_data("Departman_Dokumanlari")
    df_archive = load_data("Arsiv_Dokumanlari")
    
    if st.session_state["can_edit"]:
        st.subheader(f"📤 {dept_name} İçin Doküman Yükleme Paneli")
        upload_mode = st.radio("Yükleme Modunu Seçiniz:", ["📄 Tekli Doküman Yükleme / Revize Etme", "📁 Toplu Çoklu Dosya Yükleme"], horizontal=True)

        if upload_mode == "📄 Tekli Doküman Yükleme / Revize Etme":
            with st.form(f"form_single_{dept_name}"):
                col1, col2, col3, col4 = st.columns([1.5, 2, 1, 2])
                doc_no = col1.text_input("Doküman No / Kodu (Örn: PR-01, FR-05)").strip().upper()
                doc_title = col2.text_input("Doküman Adı (Örn: İK Prosedürü, İzin Formu)")
                doc_rev = col3.text_input("Revizyon No", value="00").strip()
                doc_note = col4.text_input("Açıklama / Revizyon Notu")
                uploaded_file = st.file_uploader("Dosya Seçiniz", type=["pdf", "png", "jpg", "jpeg", "xlsx", "docx", "zip", "rar"])
                
                submit_doc = st.form_submit_button("🔍 Dokümanı İncele ve İlerle")
            
            if submit_doc:
                if not doc_no or not doc_title or uploaded_file is None:
                    st.error("Lütfen Doküman Numarası, Doküman Adı giriniz ve bir dosya seçiniz.")
                else:
                    existing = df_docs[(df_docs["Departman"] == dept_name) & (df_docs["Doküman No"] == doc_no)]
                    
                    if not existing.empty:
                        old_row = existing.iloc[0]
                        st.warning(f"⚠️ **DİKKAT:** `{doc_no}` numaralı doküman mevcut!")
                        st.session_state["pending_rev"] = {
                            "dept": dept_name,
                            "doc_no": doc_no,
                            "doc_title": doc_title,
                            "doc_rev": doc_rev,
                            "doc_note": doc_note,
                            "uploaded_file": uploaded_file,
                            "old_row": old_row.to_dict()
                        }
                    else:
                        _, ext = os.path.splitext(uploaded_file.name)
                        standard_fname = generate_standard_filename(doc_no, doc_title, doc_rev, ext)
                        file_name = save_uploaded_file_standard(uploaded_file, UPLOAD_DIR, standard_fname)
                        
                        now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                        
                        new_rec = {
                            "Tarih / Saat": now_str,
                            "Departman": dept_name,
                            "Doküman No": doc_no,
                            "Doküman Adı": doc_title,
                            "Revizyon No": doc_rev,
                            "Açıklama / Not": doc_note,
                            "Dosya Adı": file_name,
                            "Ekleyen": st.session_state["username"],
                            "Revizyon Mu": "Hayır"
                        }
                        df_docs = pd.concat([pd.DataFrame([new_rec]), df_docs], ignore_index=True)
                        save_data(df_docs, "Departman_Dokumanlari")
                        add_notification(st.session_state["username"], dept_name, f"Yeni Doküman Eklendi: {doc_no} - {doc_title}")
                        st.success(f"✅ `{doc_no}` numaralı doküman yüklendi: **{file_name}**")
                        st.rerun()

        elif upload_mode == "📁 Toplu Çoklu Dosya Yükleme":
            uploaded_files = st.file_uploader(
                f"{dept_name} İçin Toplu Dosya Seçiniz", 
                accept_multiple_files=True,
                type=["pdf", "png", "jpg", "jpeg", "xlsx", "docx", "zip", "rar"],
                key=f"bulk_file_uploader_{dept_name}"
            )

            if uploaded_files:
                with st.form(f"bulk_form_{dept_name}"):
                    default_note = st.text_input("Ortak Açıklama / Not (Opsiyonel)", value="Toplu Yükleme")
                    bulk_data = []
                    for idx, file in enumerate(uploaded_files):
                        c1, c2, c3, c4 = st.columns([0.5, 3, 2, 1.5])
                        base_name, ext = os.path.splitext(file.name)
                        
                        chk = c1.checkbox("", value=True, key=f"chk_{dept_name}_{idx}")
                        c2.write(f"📄 **{file.name}**")
                        doc_no_val = c3.text_input("Doküman No", value=f"DOC-{idx+1:02d}", key=f"no_{dept_name}_{idx}")
                        doc_rev_val = c4.text_input("Revizyon", value="00", key=f"rev_{dept_name}_{idx}")
                        
                        if chk:
                            bulk_data.append({
                                "file_obj": file,
                                "ext": ext,
                                "doc_no": doc_no_val,
                                "doc_title": base_name,
                                "doc_rev": doc_rev_val
                            })
                    
                    submit_bulk = st.form_submit_button("🚀 SEÇİLİ DOSYALARI YÜKLE")

                if submit_bulk and bulk_data:
                    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                    for item in bulk_data:
                        standard_fname = generate_standard_filename(item["doc_no"], item["doc_title"], item["doc_rev"], item["ext"])
                        saved_fname = save_uploaded_file_standard(item["file_obj"], UPLOAD_DIR, standard_fname)
                        
                        new_rec = {
                            "Tarih / Saat": now_str,
                            "Departman": dept_name,
                            "Doküman No": item["doc_no"].upper(),
                            "Doküman Adı": item["doc_title"],
                            "Revizyon No": item["doc_rev"],
                            "Açıklama / Not": default_note,
                            "Dosya Adı": saved_fname,
                            "Ekleyen": st.session_state["username"],
                            "Revizyon Mu": "Hayır"
                        }
                        df_docs = pd.concat([pd.DataFrame([new_rec]), df_docs], ignore_index=True)
                    
                    save_data(df_docs, "Departman_Dokumanlari")
                    add_notification(st.session_state["username"], dept_name, f"Toplu Yükleme: {len(bulk_data)} adet doküman eklendi.")
                    st.success(f"🎉 **{len(bulk_data)}** dosya yüklendi!")
                    st.rerun()

        # REVİZYON ÇAKIŞMA YÖNETİMİ
        if "pending_rev" in st.session_state and st.session_state["pending_rev"]["dept"] == dept_name:
            p = st.session_state["pending_rev"]
            st.error("Çakışan Doküman İle İlgili İşlem Seçiniz:")
            
            c_r1, c_r2, c_r3 = st.columns(3)
            if c_r1.button("🔄 REVİZYON ET (Eskiyi Arşive Kaldır)"):
                now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                old_info = p["old_row"]
                old_file_name = old_info.get("Dosya Adı", "Yok")
                
                if old_file_name != "Yok":
                    src_p = os.path.join(UPLOAD_DIR, old_file_name)
                    if os.path.exists(src_p):
                        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                        archived_fname = f"ARCHIVED_{timestamp}_{old_file_name}"
                        shutil.move(src_p, os.path.join(ARCHIVE_DIR, archived_fname))
                        archived_file_ref = archived_fname
                    else:
                        archived_file_ref = old_file_name
                else:
                    archived_file_ref = "Yok"
                
                old_archive_rec = {
                    "Tarih / Saat": old_info.get("Tarih / Saat", "-"),
                    "Departman": dept_name,
                    "Doküman No": old_info.get("Doküman No", "-"),
                    "Doküman Adı": old_info.get("Doküman Adı", "-"),
                    "Revizyon No": old_info.get("Revizyon No", "00"),
                    "Açıklama / Not": old_info.get("Açıklama / Not", "-"),
                    "Dosya Adı": archived_file_ref,
                    "Ekleyen": old_info.get("Ekleyen", "-"),
                    "Arşivlenme Tarihi": now_str
                }
                df_archive = pd.concat([pd.DataFrame([old_archive_rec]), df_archive], ignore_index=True)
                save_data(df_archive, "Arsiv_Dokumanlari")
                
                df_docs = df_docs[~((df_docs["Departman"] == dept_name) & (df_docs["Doküman No"] == p["doc_no"]))]
                
                _, ext = os.path.splitext(p["uploaded_file"].name)
                new_standard_fname = generate_standard_filename(p["doc_no"], p["doc_title"], p["doc_rev"], ext)
                new_file_name = save_uploaded_file_standard(p["uploaded_file"], UPLOAD_DIR, new_standard_fname)
                
                new_rec = {
                    "Tarih / Saat": now_str,
                    "Departman": dept_name,
                    "Doküman No": p["doc_no"],
                    "Doküman Adı": p["doc_title"],
                    "Revizyon No": p["doc_rev"],
                    "Açıklama / Not": p["doc_note"],
                    "Dosya Adı": new_file_name,
                    "Ekleyen": st.session_state["username"],
                    "Revizyon Mu": "Evet"
                }
                df_docs = pd.concat([pd.DataFrame([new_rec]), df_docs], ignore_index=True)
                save_data(df_docs, "Departman_Dokumanlari")
                
                add_notification(st.session_state["username"], dept_name, f"REVİZYON YAPILDI: {p['doc_no']} - {p['doc_title']}")
                del st.session_state["pending_rev"]
                st.success("✅ Revizyon başarıyla kaydedildi!")
                st.rerun()

            if c_r2.button("📄 FARKLI DOKÜMAN OLARAK EKLE"):
                now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                _, ext = os.path.splitext(p["uploaded_file"].name)
                new_standard_fname = generate_standard_filename(p["doc_no"], p["doc_title"], p["doc_rev"], ext)
                new_file_name = save_uploaded_file_standard(p["uploaded_file"], UPLOAD_DIR, new_standard_fname)
                
                new_rec = {
                    "Tarih / Saat": now_str,
                    "Departman": dept_name,
                    "Doküman No": p["doc_no"],
                    "Doküman Adı": p["doc_title"],
                    "Revizyon No": p["doc_rev"],
                    "Açıklama / Not": p["doc_note"],
                    "Dosya Adı": new_file_name,
                    "Ekleyen": st.session_state["username"],
                    "Revizyon Mu": "Hayır"
                }
                df_docs = pd.concat([pd.DataFrame([new_rec]), df_docs], ignore_index=True)
                save_data(df_docs, "Departman_Dokumanlari")
                
                del st.session_state["pending_rev"]
                st.success("✅ Doküman eklendi!")
                st.rerun()

            if c_r3.button("❌ İPTAL"):
                del st.session_state["pending_rev"]
                st.rerun()

    # HIZLI ARAMA VE SEKMELER
    dept_search = st.text_input(f"🔍 {dept_name} İçinde Hızlı Dosya Ara...", key=f"search_{dept_name}").strip().lower()
    tab1, tab2, tab3 = st.tabs(["📄 Aktif Dokümanlar", "🔄 Son Revizeler", "📁 Arşivlenenler"])

    dept_active_docs = df_docs[df_docs["Departman"] == dept_name] if not df_docs.empty and "Departman" in df_docs.columns else pd.DataFrame()
    dept_archive_docs = df_archive[df_archive["Departman"] == dept_name] if not df_archive.empty and "Departman" in df_archive.columns else pd.DataFrame()

    with tab1:
        if not dept_active_docs.empty:
            filtered_active = dept_active_docs[dept_active_docs["Doküman Adı"].astype(str).str.lower().str.contains(dept_search)] if dept_search else dept_active_docs
            st.dataframe(filtered_active, use_container_width=True)
            for idx, row in filtered_active.iterrows():
                f_name = row.get("Dosya Adı", "Yok")
                if f_name and f_name != "Yok":
                    f_path = os.path.join(UPLOAD_DIR, f_name)
                    if os.path.exists(f_path):
                        c1, c2 = st.columns([3, 1])
                        c1.write(f"📄 **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Rev: {row.get('Revizyon No')})*")
                        with open(f_path, "rb") as f:
                            c2.download_button(label="📥 İndir", data=f, file_name=f_name, key=f"act_{idx}_{f_name}")
        else:
            st.info("Kayıtlı aktif doküman bulunmuyor.")

    with tab2:
        if not dept_active_docs.empty:
            revised_docs = dept_active_docs[dept_active_docs["Revizyon Mu"] == "Evet"]
            st.dataframe(revised_docs, use_container_width=True) if not revised_docs.empty else st.info("Revize edilmiş doküman yok.")

    with tab3:
        if not dept_archive_docs.empty:
            filtered_arch = dept_archive_docs[dept_archive_docs["Doküman Adı"].astype(str).str.lower().str.contains(dept_search)] if dept_search else dept_archive_docs
            st.dataframe(filtered_arch, use_container_width=True)
            for idx, row in filtered_arch.iterrows():
                f_name = row.get("Dosya Adı", "Yok")
                if f_name and f_name != "Yok":
                    f_path = os.path.join(ARCHIVE_DIR, f_name)
                    if os.path.exists(f_path):
                        c1, c2 = st.columns([3, 1])
                        c1.write(f"📁 **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Eski Rev: {row.get('Revizyon No')})*")
                        with open(f_path, "rb") as f:
                            c2.download_button(label="📥 İndir", data=f, file_name=f_name, key=f"arch_{idx}_{f_name}")
        else:
            st.info("Arşivlenmiş doküman bulunmuyor.")
