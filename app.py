import streamlit as st
import pandas as pd
import datetime
import os
import re
import zipfile
import io
import shutil
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --- 1. ÇALIŞMA DİZİNİ GÜVENLİK KİLİDİ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="ALASAR GRUP - Kalite Yönetim Sistemi", page_icon="🛡️", layout="wide")

# --- GOOGLE SHEETS & DRIVE ENTEGRASYONU ---
SPREADSHEET_ID = "1sepPuuUSmJg3g2Yw-ZXjMLtmut2DiatH4sLqJFDiano"
DRIVE_FOLDER_ID = "1hU-W47HVtFHb-if_BMEBw17He3b8mak9"  # App_Dokumanlar Klasör ID

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file"
]

@st.cache_resource
def get_gcp_credentials():
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    else:
        return Credentials.from_service_account_file("credentials.json", scopes=SCOPES)

@st.cache_resource
def get_gspread_client():
    creds = get_gcp_credentials()
    return gspread.authorize(creds)

@st.cache_resource
def get_drive_service():
    creds = get_gcp_credentials()
    return build('drive', 'v3', credentials=creds)

def get_worksheet_by_name(sheet_name):
    client = get_gspread_client()
    sh = client.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet(sheet_name)
    except Exception:
        if sheet_name == "Departman_Dokumanlari":
            cols = ["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Drive Linki", "Ekleyen", "Revizyon Mu"]
        elif sheet_name == "Arsiv_Dokumanlari":
            cols = ["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Drive Linki", "Ekleyen", "Arşivlenme Tarihi"]
        elif sheet_name == "kullanicilar":
            cols = ["KullaniciAdi", "Sifre", "AdSoyad", "Rol", "Departman", "Durum", "Yetki"]
        else:
            cols = ["Tarih / Saat", "İşlemi Yapan", "Departman / Modül", "Detay / Doküman"]
        
        ws = sh.add_worksheet(title=sheet_name, rows="500", cols="20")
        ws.append_row(cols)
        return ws

def load_data(sheet_name):
    expected_columns_map = {
        "Departman_Dokumanlari": ["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Drive Linki", "Ekleyen", "Revizyon Mu"],
        "Arsiv_Dokumanlari": ["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Drive Linki", "Ekleyen", "Arşivlenme Tarihi"],
        "kullanicilar": ["KullaniciAdi", "Sifre", "AdSoyad", "Rol", "Departman", "Durum", "Yetki"],
        "Bildirimler": ["Tarih / Saat", "İşlemi Yapan", "Departman / Modül", "Detay / Doküman"]
    }
    
    default_cols = expected_columns_map.get(sheet_name, ["Tarih / Saat", "İşlemi Yapan", "Departman / Modül", "Detay / Doküman"])

    try:
        ws = get_worksheet_by_name(sheet_name)
        records = ws.get_all_records()
        df = pd.DataFrame(records)
        
        # Eğer veri tabanı boşsa veya DataFrame sütunsuz geldiyse zorunlu sütunları tanımla
        if df.empty:
            return pd.DataFrame(columns=default_cols)

        # Eksik sütunları kontrol et ve eksik olanları varsayılan değerle tamamla
        for col in default_cols:
            if col not in df.columns:
                df[col] = "-"
        return df
    except Exception as e:
        st.error(f"Google Sheets okuma hatası ({sheet_name}): {e}")
        return pd.DataFrame(columns=default_cols)

def save_data(df_new, sheet_name):
    try:
        ws = get_worksheet_by_name(sheet_name)
        ws.clear()
        df_clean = df_new.fillna("-").astype(str)
        data_to_write = [df_clean.columns.values.tolist()] + df_clean.values.tolist()
        ws.update(range_name='A1', values=data_to_write)
    except Exception as e:
        st.error(f"Google Sheets kaydetme hatası ({sheet_name}): {e}")

# --- GOOGLE DRIVE DOSYA YÜKLEME FONKSİYONU ---
def upload_to_google_drive(file_bytes, filename, mime_type="application/octet-stream"):
    try:
        service = get_drive_service()
        file_metadata = {
            'name': filename,
            'parents': [DRIVE_FOLDER_ID]
        }
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
        file = service.files().create(
            body=file_metadata, 
            media_body=media, 
            fields='id, webViewLink',
            supportsAllDrives=True
        ).execute()
        return file.get('webViewLink', '-')
    except Exception as e:
        st.error(f"⚠️ Google Drive'a Yükleme Hatası Detayı: {e}")
        return "-"

# --- DOSYA VE KLASÖR YOLLARI ---
UPLOAD_DIR = os.path.join(BASE_DIR, "yuklenen_belgeler")
ARCHIVE_DIR = os.path.join(BASE_DIR, "arsivlenenler")

for d in [UPLOAD_DIR, ARCHIVE_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

# --- DOSYA ADI TEMİZLEME VE STANDARTLAŞTIRMA ---
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

# --- CANLI KULLANICI DOĞRULAMA KONTROLÜ ---
def authenticate_user_live(username_input, password_input):
    df_users = load_data("kullanicilar")
    if df_users.empty or "KullaniciAdi" not in df_users.columns:
        return False, "Kullanıcı veritabanına ulaşılamadı veya tablo biçimi hatalı.", None

    user_row = df_users[df_users["KullaniciAdi"].astype(str).str.strip() == str(username_input).strip()]
    if user_row.empty:
        return False, "Kullanıcı adı bulunamadı.", None
    
    u_data = user_row.iloc[0].to_dict()
    
    if str(u_data.get("Durum", "")).strip().lower() != "aktif":
        return False, "Hesabınız pasif durumdadır. Yöneticinizle iletişime geçiniz.", None
        
    if str(u_data.get("Sifre", "")).strip() != str(password_input).strip():
        return False, "Hatalı şifre girdiniz.", None
        
    return True, "Başarılı", u_data

# --- OTURUM DURUMU ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "username" not in st.session_state:
    st.session_state["username"] = ""
if "full_name" not in st.session_state:
    st.session_state["full_name"] = ""
if "role" not in st.session_state:
    st.session_state["role"] = ""
if "dept" not in st.session_state:
    st.session_state["dept"] = ""
if "can_edit" not in st.session_state:
    st.session_state["can_edit"] = False

# --- GİRİŞ EKRANI (LOGIN) ---
if not st.session_state["logged_in"]:
    st.title("🏢 ALASAR GRUP")
    st.subheader("Kalite & Departman Yönetim Sistemi - Canlı Giriş Paneli")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            input_user = st.text_input("Kullanıcı Adı")
            input_password = st.text_input("Şifre", type="password")
            submit_login = st.form_submit_button("🔑 Giriş Yap", use_container_width=True)
            
            if submit_login:
                if not input_user or not input_password:
                    st.warning("Lütfen kullanıcı adı ve şifrenizi giriniz.")
                else:
                    success, msg, u_data = authenticate_user_live(input_user, input_password)
                    if success:
                        st.session_state["logged_in"] = True
                        st.session_state["username"] = u_data.get("KullaniciAdi", input_user)
                        st.session_state["full_name"] = u_data.get("AdSoyad", input_user)
                        st.session_state["role"] = u_data.get("Rol", "-")
                        st.session_state["dept"] = u_data.get("Departman", "-")
                        
                        perm_str = str(u_data.get("Yetki", "")).strip().upper()
                        st.session_state["can_edit"] = (perm_str == "DÜZENLEME")
                        
                        st.success(f"Hoş geldiniz, {st.session_state['full_name']}!")
                        st.rerun()
                    else:
                        st.error(msg)
    st.stop()

# --- ANA SİSTEM SOL MENÜ ---
st.sidebar.title("🏢 ALASAR GRUP")
st.sidebar.write(f"👤 **{st.session_state['full_name']}**")
st.sidebar.caption(f"Rol: {st.session_state['role']} | Dep: {st.session_state['dept']}")

if st.session_state["username"] == "omer.ocak" or "ÖMER" in st.session_state["full_name"].upper():
    st.sidebar.info("⚡ Superadmin / Tam Sistem Yetkilisi")
elif st.session_state["can_edit"]:
    st.sidebar.success("✏️ Doküman Yükleme / Revize Yetkisi Var")
else:
    st.sidebar.info("👁️ Sadece Okuma / İndirme Yetkisi Var")

if st.sidebar.button("🚪 Çıkış Yap"):
    st.session_state["logged_in"] = False
    st.session_state["username"] = ""
    st.session_state["full_name"] = ""
    st.session_state["role"] = ""
    st.session_state["dept"] = ""
    st.session_state["can_edit"] = False
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown(f"📊 **Canlı VT:** [Google Sheets Tablosu](https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID})")
st.sidebar.markdown(f"📁 **Google Drive:** [App_Dokumanlar Klasörü](https://drive.google.com/drive/u/0/folders/{DRIVE_FOLDER_ID})")
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

if st.session_state["username"] == "omer.ocak" or "ÖMER" in st.session_state["full_name"].upper():
    menu_options.append("⚙️ SİSTEM YÖNETİMİ & BAKIŞ")

modul = st.sidebar.radio("DEPARTMANLAR VE MENÜ:", menu_options)
st.sidebar.markdown("---")

# --- CANLI BİLDİRİM PANELİ ---
df_notif_top = load_data("Bildirimler")
if not df_notif_top.empty:
    latest = df_notif_top.iloc[0]
    dept_val = latest.get('Departman / Modül', latest.get('Modül', 'Genel'))
    time_val = latest.get('Tarih / Saat', '-')
    user_val = latest.get('İşlemi Yapan', '-')
    detail_val = latest.get('Detay / Doküman', '-')
    st.info(f"🔔 **Son Güncelleme / Revizyon Bildirimi:** [{time_val}] **{user_val}** tarafından **{dept_val}** alanında işlem yapıldı: *{detail_val}*")

# --- GENEL ARAMA MERKEZİ ---
if modul == "🔍 GENEL ARAMA MERKEZİ":
    st.title("🔍 Tüm Sistem Genel Doküman Arama Merkezi")
    st.caption("Sistemdeki tüm aktif ve arşivlenmiş dokümanları departman bağımsız tek bir arama çubuğu üzerinden anında sorgulayabilirsiniz.")

    search_query = st.text_input("🔎 Doküman Adı, Doküman No, Açıklama veya Yükleyen Kişi Ara...", key="global_search").strip().lower()
    
    df_docs = load_data("Departman_Dokumanlari")
    df_archive = load_data("Arsiv_Dokumanlari")

    tab_g1, tab_g2 = st.tabs(["📄 Aktif Dokümanlar İçinde Ara", "📁 Arşiv Dokümanları İçinde Ara"])
    
    with tab_g1:
        if not df_docs.empty and "Doküman Adı" in df_docs.columns:
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
                d_link = row.get("Drive Linki", "-")
                c1, c2 = st.columns([3, 1])
                c1.write(f"📄 **[{row.get('Departman')}]** - **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Rev: {row.get('Revizyon No')})*")
                
                if d_link and d_link != "-":
                    c2.markdown(f"[📥 Google Drive'da Aç / İndir]({d_link})")
                elif f_name and f_name != "Yok":
                    f_path = os.path.join(UPLOAD_DIR, f_name)
                    if os.path.exists(f_path):
                        with open(f_path, "rb") as f:
                            c2.download_button(label="📥 İndir (Yerel)", data=f, file_name=f_name, key=f"glob_act_{idx}_{f_name}")
        else:
            st.info("Sistemde henüz aktif doküman bulunmuyor.")

    with tab_g2:
        if not df_archive.empty and "Doküman Adı" in df_archive.columns:
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
                d_link = row.get("Drive Linki", "-")
                c1, c2 = st.columns([3, 1])
                c1.write(f"📁 **[{row.get('Departman')}]** - **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Rev: {row.get('Revizyon No')})* - Arşiv: {row.get('Arşivlenme Tarihi')}")
                
                if d_link and d_link != "-":
                    c2.markdown(f"[📥 Drive Arşiv Linki]({d_link})")
                elif f_name and f_name != "Yok":
                    f_path = os.path.join(ARCHIVE_DIR, f_name)
                    if os.path.exists(f_path):
                        with open(f_path, "rb") as f:
                            c2.download_button(label="📥 Eski Versiyonu İndir", data=f, file_name=f_name, key=f"glob_arch_{idx}_{f_name}")
        else:
            st.info("Sistemde henüz arşivlenmiş doküman bulunmuyor.")

# --- SİSTEM YÖNETİMİ & TEMİZLEME MODÜLÜ ---
elif modul == "⚙️ SİSTEM YÖNETİMİ & BAKIŞ":
    st.title("⚙️ Sistem Yönetimi ve Toplu İşlem Paneli")
    st.warning("⚠️ Bu panel sadece **Sistem Yöneticisi** tarafından görüntülenebilir ve yetkilendirilmiştir.")

    st.markdown("### 📦 1. Tüm Belgeleri Toplu İndir (ZIP)")
    st.write("Sistemde yuklenen tüm aktif ve arşivlenmiş dokümanları tek bir arşiv dosyası (.zip) olarak indirebilirsiniz.")
    
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
    st.error("🚨 **DİKKAT:** Bu işlem sistemdeki yüklü tüm dosyaları, arşiv klasörünü ve Google Sheets veri tabanındaki tüm kayıtları kalıcı olarak siler!")
    
    confirm_check = st.checkbox("Sistemdeki tüm belgeleri ve veri tabanı kayıtlarını silmek istediğimi onaylıyorum.")
    
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
        
        save_data(pd.DataFrame(columns=["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Drive Linki", "Ekleyen", "Revizyon Mu"]), "Departman_Dokumanlari")
        save_data(pd.DataFrame(columns=["Tarih / Saat", "Departman", "Doküman No", "Doküman Adı", "Revizyon No", "Açıklama / Not", "Dosya Adı", "Drive Linki", "Ekleyen", "Arşivlenme Tarihi"]), "Arsiv_Dokumanlari")
        save_data(pd.DataFrame(columns=["Tarih / Saat", "İşlemi Yapan", "Departman / Modül", "Detay / Doküman"]), "Bildirimler")

        add_notification(st.session_state["full_name"], "Sistem Yönetimi", "Tüm sistem belgeleri ve veri tabanı kayıtları sıfırlandı.")
        st.success("✅ Tüm sistem belgeleri ve Google Sheets veri tabanı başarıyla temizlendi!")
        st.rerun()

# --- BİLDİRİM GEÇMİŞİ MODÜLÜ ---
elif modul == "🔔 Bildirim Geçmişi":
    st.title("🔔 Tüm Güncelleme & Revizyon Geçmişi")
    st.write("Sistem üzerinde yapılan tüm departman doküman yükleme, revizyon ve arşivleme işlemlerinin dökümü:")
    df_notif_all = load_data("Bildirimler")
    st.dataframe(df_notif_all, use_container_width=True)

# --- DEPARTMAN MODÜLLERİ ---
else:
    dept_name = modul.replace("👥 ", "").replace("⚙️ ", "").replace("👔 ", "").replace("🌐 ", "").replace("🛡️ ", "").replace("📦 ", "")
    st.title(f"📂 {dept_name}")
    st.caption(f"{dept_name} bünyesine ait tüm prosedür, talimat, form ve revize dokümanlar bu alanda yönetilir.")
    
    df_docs = load_data("Departman_Dokumanlari")
    df_archive = load_data("Arsiv_Dokumanlari")
    
    # DOKÜMAN YÜKLEME ALANI
    if st.session_state["can_edit"]:
        st.subheader(f"📤 {dept_name} İçin Doküman Yükleme Paneli")
        
        upload_mode = st.radio("Yükleme Modunu Seçiniz:", ["📄 Tekli Doküman Yükleme / Revize Etme", "📁 Toplu Çoklu Dosya Yükleme"], horizontal=True)

        if upload_mode == "📄 Tekli Doküman Yükleme / Revize Etme":
            with st.form(f"form_single_{dept_name}"):
                col1, col2, col3, col4 = st.columns([1.5, 2, 1, 2])
                doc_no = col1.text_input("Doküman No / Kodu (Örn: PR-01, FR-05)").strip().upper()
                doc_title = col2.text_input("Doküman Adı (Örn: İK Prosedürü, İzin Formu)")
                doc_rev = col3.text_input("Revizyon No", value="00").strip()
                doc_note = col4.text_input("Açıklama / Revizyon Notu (Örn: Maddeler Güncellendi)")
                uploaded_file = st.file_uploader("Dosya Seçiniz (PDF, Word, Excel vb.)", type=["pdf", "png", "jpg", "jpeg", "xlsx", "docx", "zip", "rar"])
                
                submit_doc = st.form_submit_button("🔍 Dokümanı İncele ve İlerle")
            
            if submit_doc:
                if not doc_no or not doc_title or uploaded_file is None:
                    st.error("Lütfen Doküman Numarası, Doküman Adı giriniz ve bir dosya seçiniz.")
                else:
                    existing = df_docs[(df_docs["Departman"] == dept_name) & (df_docs["Doküman No"] == doc_no)] if not df_docs.empty else pd.DataFrame()
                    
                    if not existing.empty:
                        old_row = existing.iloc[0]
                        st.warning(f"⚠️ **DİKKAT:** `{doc_no}` numaralı **'{old_row['Doküman Adı']}'** isimli doküman bu departmanda zaten mevcut!")
                        st.info(f"📌 **Mevcut Dosya:** {old_row['Dosya Adı']} | **Revizyon:** {old_row.get('Revizyon No', '00')} | **Yükleyen:** {old_row['Ekleyen']} | **Tarih:** {old_row['Tarih / Saat']}")
                        
                        st.session_state["pending_rev"] = {
                            "dept": dept_name,
                            "doc_no": doc_no,
                            "doc_title": doc_title,
                            "doc_rev": doc_rev,
                            "doc_note": doc_note,
                            "uploaded_file_bytes": uploaded_file.getvalue(),
                            "uploaded_file_name": uploaded_file.name,
                            "old_row": old_row.to_dict()
                        }
                    else:
                        _, ext = os.path.splitext(uploaded_file.name)
                        standard_fname = generate_standard_filename(doc_no, doc_title, doc_rev, ext)
                        file_bytes = uploaded_file.getvalue()
                        
                        # 1. Yerel Kaydet
                        file_name = save_uploaded_file_standard(uploaded_file, UPLOAD_DIR, standard_fname)
                        # 2. Google Drive Yükle
                        drive_link = upload_to_google_drive(file_bytes, standard_fname, uploaded_file.type)
                        
                        now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                        
                        new_rec = {
                            "Tarih / Saat": now_str,
                            "Departman": dept_name,
                            "Doküman No": doc_no,
                            "Doküman Adı": doc_title,
                            "Revizyon No": doc_rev,
                            "Açıklama / Not": doc_note,
                            "Dosya Adı": file_name,
                            "Drive Linki": drive_link,
                            "Ekleyen": st.session_state["full_name"],
                            "Revizyon Mu": "Hayır"
                        }
                        df_docs = pd.concat([pd.DataFrame([new_rec]), df_docs], ignore_index=True)
                        save_data(df_docs, "Departman_Dokumanlari")
                        add_notification(st.session_state["full_name"], dept_name, f"Yeni Doküman Eklendi: {doc_no} - {doc_title} (Rev: {doc_rev})")
                        st.success(f"✅ `{doc_no}` numaralı yeni doküman eklendi: **{file_name}**")
                        st.rerun()

        elif upload_mode == "📁 Toplu Çoklu Dosya Yükleme":
            uploaded_files = st.file_uploader(
                f"{dept_name} İçin Toplu Dosya Seçiniz", 
                accept_multiple_files=True,
                type=["pdf", "png", "jpg", "jpeg", "xlsx", "docx", "zip", "rar"],
                key=f"bulk_file_uploader_{dept_name}"
            )

            if uploaded_files:
                st.subheader(f"📋 Toplu Dosya Onay Tablosu ({dept_name})")
                
                with st.form(f"bulk_form_{dept_name}"):
                    default_note = st.text_input("Ortak Açıklama / Not (Opsiyonel)", value="Toplu Yükleme", key=f"b_note_{dept_name}")
                    st.markdown("---")
                    
                    bulk_data = []
                    for idx, file in enumerate(uploaded_files):
                        c1, c2, c3, c4 = st.columns([0.5, 3, 2, 1.5])
                        base_name, ext = os.path.splitext(file.name)
                        
                        chk = c1.checkbox("", value=True, key=f"chk_dept_{dept_name}_{idx}")
                        c2.write(f"📄 **{file.name}**")
                        doc_no_val = c3.text_input("Doküman No", value=f"DOC-{idx+1:02d}", key=f"no_dept_{dept_name}_{idx}")
                        doc_rev_val = c4.text_input("Revizyon", value="00", key=f"rev_dept_{dept_name}_{idx}")
                        
                        if chk:
                            bulk_data.append({
                                "file_obj": file,
                                "orig_name": file.name,
                                "ext": ext,
                                "doc_no": doc_no_val,
                                "doc_title": base_name,
                                "doc_rev": doc_rev_val
                            })
                    
                    submit_bulk = st.form_submit_button("🚀 SEÇİLİ DOSYALARI DEPARTMANA YÜKLE")

                if submit_bulk:
                    if not bulk_data:
                        st.warning("Lütfen işlem yapmak için en az bir dosyanın yanındaki tik kutusunu işaretleyin.")
                    else:
                        success_count = 0
                        now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                        
                        for item in bulk_data:
                            standard_fname = generate_standard_filename(item["doc_no"], item["doc_title"], item["doc_rev"], item["ext"])
                            f_bytes = item["file_obj"].getvalue()
                            
                            saved_fname = save_uploaded_file_standard(item["file_obj"], UPLOAD_DIR, standard_fname)
                            d_link = upload_to_google_drive(f_bytes, standard_fname, item["file_obj"].type)
                            
                            new_rec = {
                                "Tarih / Saat": now_str,
                                "Departman": dept_name,
                                "Doküman No": item["doc_no"].upper(),
                                "Doküman Adı": item["doc_title"],
                                "Revizyon No": item["doc_rev"],
                                "Açıklama / Not": default_note,
                                "Dosya Adı": saved_fname,
                                "Drive Linki": d_link,
                                "Ekleyen": st.session_state["full_name"],
                                "Revizyon Mu": "Hayır"
                            }
                            df_docs = pd.concat([pd.DataFrame([new_rec]), df_docs], ignore_index=True)
                            success_count += 1
                        
                        save_data(df_docs, "Departman_Dokumanlari")
                        add_notification(st.session_state["full_name"], dept_name, f"Toplu Yükleme Yapıldı: {success_count} adet doküman eklendi.")
                        st.success(f"🎉 **{success_count}** adet dosya başarıyla **{dept_name}** bünyesine eklendi!")
                        st.rerun()

        # REVİZYON ÇAKIŞMASI ONAY BUTONLARI
        if "pending_rev" in st.session_state and st.session_state["pending_rev"]["dept"] == dept_name:
            p = st.session_state["pending_rev"]
            st.error("Lütfen yapmak istediğiniz işlemi seçiniz:")
            
            col_rev1, col_rev2, col_rev3 = st.columns(3)
            
            if col_rev1.button("🔄 EVET, Bu Bir Revizyondur (Eski Dosyayı Arşive Kaldır)"):
                now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                old_info = p["old_row"]
                old_file_name = old_info.get("Dosya Adı", "Yok")
                
                if old_file_name != "Yok":
                    src_p = os.path.join(UPLOAD_DIR, old_file_name)
                    if os.path.exists(src_p):
                        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                        archived_fname = f"ARCHIVED_{timestamp}_{old_file_name}"
                        dst_p = os.path.join(ARCHIVE_DIR, archived_fname)
                        shutil.move(src_p, dst_p)
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
                    "Drive Linki": old_info.get("Drive Linki", "-"),
                    "Ekleyen": old_info.get("Ekleyen", "-"),
                    "Arşivlenme Tarihi": now_str
                }
                df_archive = pd.concat([pd.DataFrame([old_archive_rec]), df_archive], ignore_index=True)
                save_data(df_archive, "Arsiv_Dokumanlari")
                
                if not df_docs.empty and "Departman" in df_docs.columns and "Doküman No" in df_docs.columns:
                    df_docs = df_docs[~((df_docs["Departman"] == dept_name) & (df_docs["Doküman No"] == p["doc_no"]))]
                
                _, ext = os.path.splitext(p["uploaded_file_name"])
                new_standard_fname = generate_standard_filename(p["doc_no"], p["doc_title"], p["doc_rev"], ext)
                
                file_path = os.path.join(UPLOAD_DIR, new_standard_fname)
                with open(file_path, "wb") as f:
                    f.write(p["uploaded_file_bytes"])
                
                new_drive_link = upload_to_google_drive(p["uploaded_file_bytes"], new_standard_fname)
                
                new_rec = {
                    "Tarih / Saat": now_str,
                    "Departman": dept_name,
                    "Doküman No": p["doc_no"],
                    "Doküman Adı": p["doc_title"],
                    "Revizyon No": p["doc_rev"],
                    "Açıklama / Not": p["doc_note"],
                    "Dosya Adı": new_standard_fname,
                    "Drive Linki": new_drive_link,
                    "Ekleyen": st.session_state["full_name"],
                    "Revizyon Mu": "Evet"
                }
                df_docs = pd.concat([pd.DataFrame([new_rec]), df_docs], ignore_index=True)
                save_data(df_docs, "Departman_Dokumanlari")
                
                add_notification(st.session_state["full_name"], dept_name, f"REVİZYON YAPILDI: {p['doc_no']} - {p['doc_title']} (Rev: {p['doc_rev']})")
                del st.session_state["pending_rev"]
                st.success(f"✅ Revizyon işlendi! Yeni dosya **{new_standard_fname}** canlıya alındı.")
                st.rerun()

            if col_rev2.button("📄 EVET, Farklı Bir Doküman Olarak Ekle"):
                now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                
                _, ext = os.path.splitext(p["uploaded_file_name"])
                new_standard_fname = generate_standard_filename(p["doc_no"], p["doc_title"], p["doc_rev"], ext)
                
                file_path = os.path.join(UPLOAD_DIR, new_standard_fname)
                with open(file_path, "wb") as f:
                    f.write(p["uploaded_file_bytes"])
                
                new_drive_link = upload_to_google_drive(p["uploaded_file_bytes"], new_standard_fname)
                
                new_rec = {
                    "Tarih / Saat": now_str,
                    "Departman": dept_name,
                    "Doküman No": p["doc_no"],
                    "Doküman Adı": p["doc_title"],
                    "Revizyon No": p["doc_rev"],
                    "Açıklama / Not": p["doc_note"],
                    "Dosya Adı": new_standard_fname,
                    "Drive Linki": new_drive_link,
                    "Ekleyen": st.session_state["full_name"],
                    "Revizyon Mu": "Hayır"
                }
                
                df_docs = pd.concat([pd.DataFrame([new_rec]), df_docs], ignore_index=True)
                save_data(df_docs, "Departman_Dokumanlari")
                
                add_notification(st.session_state["full_name"], dept_name, f"Yeni Doküman Eklendi: {p['doc_no']} - {p['doc_title']} (Rev: {p['doc_rev']})")
                del st.session_state["pending_rev"]
                st.success(f"✅ Doküman eklendi! Dosya adı: **{new_standard_fname}**")
                st.rerun()

            if col_rev3.button("❌ İŞLEMİ İPTAL ET"):
                del st.session_state["pending_rev"]
                st.info("İşlem iptal edildi.")
                st.rerun()
                
        st.markdown("---")

    # DEPARTMAN İÇİ CANLI HIZLI ARAMA VE DOKÜMAN LİSTELEME
    st.subheader(f"📋 {dept_name} Mevcut Doküman Listesi")
    dept_search = st.text_input(f"🔍 {dept_name} İçinde Hızlı Dosya Ara (Kod, Ad, Not)...", key=f"search_{dept_name}").strip().lower()

    if not df_docs.empty and "Departman" in df_docs.columns:
        dept_docs = df_docs[df_docs["Departman"] == dept_name]
    else:
        dept_docs = pd.DataFrame()

    if not dept_docs.empty:
        if dept_search:
            filtered_dept_docs = dept_docs[
                dept_docs["Doküman Adı"].astype(str).str.lower().str.contains(dept_search) |
                dept_docs["Doküman No"].astype(str).str.lower().str.contains(dept_search) |
                dept_docs["Açıklama / Not"].astype(str).str.lower().str.contains(dept_search) |
                dept_docs["Ekleyen"].astype(str).str.lower().str.contains(dept_search)
            ]
        else:
            filtered_dept_docs = dept_docs

        st.write(f"Toplam Doküman Sayısı: **{len(filtered_dept_docs)}**")
        st.dataframe(filtered_dept_docs, use_container_width=True)

        for idx, row in filtered_dept_docs.iterrows():
            f_name = row.get("Dosya Adı", "Yok")
            d_link = row.get("Drive Linki", "-")
            
            c1, c2 = st.columns([3, 1])
            c1.write(f"📄 **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Rev: {row.get('Revizyon No')})* - Ekleyen: {row.get('Ekleyen')}")
            
            if d_link and d_link != "-":
                c2.markdown(f"[📥 Drive Linki]({d_link})")
            elif f_name and f_name != "Yok":
                f_path = os.path.join(UPLOAD_DIR, f_name)
                if os.path.exists(f_path):
                    with open(f_path, "rb") as f:
                        c2.download_button(label="📥 İndir (Yerel)", data=f, file_name=f_name, key=f"dept_dl_{idx}_{f_name}")
    else:
        st.info(f"{dept_name} için henüz kayıtlı doküman bulunmamaktadır.")
