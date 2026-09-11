import streamlit as st
import pandas as pd
import datetime
import os
import json
import shutil
import re
import zipfile
import io
import sqlite3

# --- 1. ÇALIŞMA DİZİNİ GÜVENLİK KİLİDİ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="ALASAR GRUP - Kalite Yönetim Sistemi", page_icon="🛡️", layout="wide")

# --- DOSYA VE KLASÖR YOLLARI ---
DB_FILE = os.path.join(BASE_DIR, "alasar_kalite.db")
USERS_FILE = os.path.join(BASE_DIR, "users.json")
UPLOAD_DIR = os.path.join(BASE_DIR, "yuklenen_belgeler")
ARCHIVE_DIR = os.path.join(BASE_DIR, "arsivlenenler")

for d in [UPLOAD_DIR, ARCHIVE_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

# --- SQLITE VERİTABANI BAĞLANTISI VE TABLO İLKLEME ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_sqlite_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Departman Dokümanları Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS departman_dokumanlari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarih_saat TEXT,
            departman TEXT,
            dokuman_no TEXT,
            dokuman_adi TEXT,
            revizyon_no TEXT,
            aciklama_not TEXT,
            dosya_adi TEXT,
            ekleyen TEXT,
            revizyon_mu TEXT DEFAULT 'Hayır'
        )
    ''')
    
    # Arşiv Dokümanları Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS arsiv_dokumanlari (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarih_saat TEXT,
            departman TEXT,
            dokuman_no TEXT,
            dokuman_adi TEXT,
            revizyon_no TEXT,
            aciklama_not TEXT,
            dosya_adi TEXT,
            ekleyen TEXT,
            arsivlenme_tarihi TEXT
        )
    ''')
    
    # Bildirimler Tablosu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bildirimler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarih_saat TEXT,
            islemi_yapan TEXT,
            departman_modul TEXT,
            detay_dokuman TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

init_sqlite_db()

# --- YARDIMCI FONKSİYONLAR ---
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

# --- KULLANICI İŞLEMLERİ ---
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

# --- SQL İŞLEM YARDIMCILARI ---
def add_notification(user, modul, detail):
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO bildirimler (tarih_saat, islemi_yapan, departman_modul, detay_dokuman)
        VALUES (?, ?, ?, ?)
    ''', (now_str, user, modul, detail))
    conn.commit()
    conn.close()

def get_df_from_query(query, params=()):
    conn = get_db_connection()
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df

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
                    st.session_state["can_edit"] = USERS[selected_user]["can_edit"]
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

# --- CANLI BİLDİRİM PANELİ ---
df_notif_top = get_df_from_query("SELECT * FROM bildirimler ORDER BY id DESC LIMIT 1")
if not df_notif_top.empty:
    latest = df_notif_top.iloc[0]
    st.info(f"🔔 **Son Güncelleme / Revizyon Bildirimi:** [{latest['tarih_saat']}] **{latest['islemi_yapan']}** tarafından **{latest['departman_modul']}** alanında işlem yapıldı: *{latest['detay_dokuman']}*")

# --- GENEL ARAMA MERKEZİ ---
if modul == "🔍 GENEL ARAMA MERKEZİ":
    st.title("🔍 Tüm Sistem Genel Doküman Arama Merkezi")
    st.caption("Sistemdeki tüm aktif ve arşivlenmiş dokümanları SQLite veritabanı üzerinden anında sorgulayabilirsiniz.")

    search_query = st.text_input("🔎 Doküman Adı, Doküman No, Açıklama veya Yükleyen Kişi Ara...", key="global_search").strip()
    
    tab_g1, tab_g2 = st.tabs(["📄 Aktif Dokümanlar İçinde Ara", "📁 Arşiv Dokümanları İçinde Ara"])
    
    with tab_g1:
        if search_query:
            q = "%" + search_query + "%"
            df_docs = get_df_from_query('''
                SELECT tarih_saat AS "Tarih / Saat", departman AS "Departman", dokuman_no AS "Doküman No", 
                       dokuman_adi AS "Doküman Adı", revizyon_no AS "Revizyon No", aciklama_not AS "Açıklama / Not", 
                       dosya_adi AS "Dosya Adı", ekleyen AS "Ekleyen", revizyon_mu AS "Revizyon Mu"
                FROM departman_dokumanlari 
                WHERE dokuman_adi LIKE ? OR dokuman_no LIKE ? OR aciklama_not LIKE ? OR departman LIKE ? OR ekleyen LIKE ?
                ORDER BY id DESC
            ''', (q, q, q, q, q))
        else:
            df_docs = get_df_from_query('''
                SELECT tarih_saat AS "Tarih / Saat", departman AS "Departman", dokuman_no AS "Doküman No", 
                       dokuman_adi AS "Doküman Adı", revizyon_no AS "Revizyon No", aciklama_not AS "Açıklama / Not", 
                       dosya_adi AS "Dosya Adı", ekleyen AS "Ekleyen", revizyon_mu AS "Revizyon Mu"
                FROM departman_dokumanlari ORDER BY id DESC
            ''')

        st.write(f"Bulunan Doküman Sayısı: **{len(df_docs)}**")
        st.dataframe(df_docs, use_container_width=True)

        for idx, row in df_docs.iterrows():
            f_name = row.get("Dosya Adı", "Yok")
            if f_name and f_name != "Yok":
                f_path = os.path.join(UPLOAD_DIR, f_name)
                if os.path.exists(f_path):
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"📄 **[{row.get('Departman')}]** - **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Rev: {row.get('Revizyon No')})*")
                    with open(f_path, "rb") as f:
                        c2.download_button(label="📥 İndir", data=f, file_name=f_name, key=f"glob_act_{idx}_{f_name}")

    with tab_g2:
        if search_query:
            q = "%" + search_query + "%"
            df_arch = get_df_from_query('''
                SELECT tarih_saat AS "Tarih / Saat", departman AS "Departman", dokuman_no AS "Doküman No", 
                       dokuman_adi AS "Doküman Adı", revizyon_no AS "Revizyon No", aciklama_not AS "Açıklama / Not", 
                       dosya_adi AS "Dosya Adı", ekleyen AS "Ekleyen", arsivlenme_tarihi AS "Arşivlenme Tarihi"
                FROM arsiv_dokumanlari 
                WHERE dokuman_adi LIKE ? OR dokuman_no LIKE ? OR aciklama_not LIKE ? OR departman LIKE ? OR ekleyen LIKE ?
                ORDER BY id DESC
            ''', (q, q, q, q, q))
        else:
            df_arch = get_df_from_query('''
                SELECT tarih_saat AS "Tarih / Saat", departman AS "Departman", dokuman_no AS "Doküman No", 
                       dokuman_adi AS "Doküman Adı", revizyon_no AS "Revizyon No", aciklama_not AS "Açıklama / Not", 
                       dosya_adi AS "Dosya Adı", ekleyen AS "Ekleyen", arsivlenme_tarihi AS "Arşivlenme Tarihi"
                FROM arsiv_dokumanlari ORDER BY id DESC
            ''')

        st.write(f"Bulunan Arşiv Doküman Sayısı: **{len(df_arch)}**")
        st.dataframe(df_arch, use_container_width=True)

        for idx, row in df_arch.iterrows():
            f_name = row.get("Dosya Adı", "Yok")
            if f_name and f_name != "Yok":
                f_path = os.path.join(ARCHIVE_DIR, f_name)
                if os.path.exists(f_path):
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"📁 **[{row.get('Departman')}]** - **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Rev: {row.get('Revizyon No')})* - Arşiv: {row.get('Arşivlenme Tarihi')}")
                    with open(f_path, "rb") as f:
                        c2.download_button(label="📥 Eski Versiyonu İndir", data=f, file_name=f_name, key=f"glob_arch_{idx}_{f_name}")

# --- SİSTEM YÖNETİMİ & TEMİZLEME MODÜLÜ ---
elif modul == "⚙️ SİSTEM YÖNETİMİ & BAKIŞ":
    st.title("⚙️ Sistem Yönetimi ve Toplu İşlem Paneli")
    st.warning("⚠️ Bu panel sadece **Ömer OCAK** tarafından görüntülenebilir ve yetkilendirilmiştir.")

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
    st.error("🚨 **DİKKAT:** Bu işlem sistemdeki yüklü tüm dosyaları, arşiv klasörünü ve SQLite veritabanındaki tüm kayıtları kalıcı olarak siler!")
    
    confirm_check = st.checkbox("Sistemdeki tüm belgeleri ve veritabanı kayıtlarını silmek istediğimi onaylıyorum.")
    
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
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM departman_dokumanlari")
        cursor.execute("DELETE FROM arsiv_dokumanlari")
        cursor.execute("DELETE FROM bildirimler")
        conn.commit()
        conn.close()

        add_notification("Ömer OCAK", "Sistem Yönetimi", "Tüm sistem belgeleri ve veritabanı kayıtları sıfırlandı.")
        st.success("✅ Tüm sistem belgeleri ve SQLite veritabanı başarıyla temizlendi!")
        st.rerun()

# --- BİLDİRİM GEÇMİŞİ MODÜLÜ ---
elif modul == "🔔 Bildirim Geçmişi":
    st.title("🔔 Tüm Güncelleme & Revizyon Geçmişi")
    df_notif_all = get_df_from_query('''
        SELECT tarih_saat AS "Tarih / Saat", islemi_yapan AS "İşlemi Yapan", 
               departman_modul AS "Departman / Modül", detay_dokuman AS "Detay / Doküman"
        FROM bildirimler ORDER BY id DESC
    ''')
    st.dataframe(df_notif_all, use_container_width=True)

# --- DEPARTMAN MODÜLLERİ ---
else:
    dept_name = modul.replace("👥 ", "").replace("⚙️ ", "").replace("👔 ", "").replace("🌐 ", "").replace("🛡️ ", "").replace("📦 ", "")
    st.title(f"📂 {dept_name}")
    st.caption(f"{dept_name} bünyesine ait tüm prosedür, talimat, form ve revize dokümanlar bu alanda yönetilir.")
    
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
                uploaded_file = st.file_uploader("Dosya Seçiniz", type=["pdf", "png", "jpg", "jpeg", "xlsx", "docx", "zip", "rar"])
                
                submit_doc = st.form_submit_button("🔍 Dokümanı İncele ve İlerle")
            
            if submit_doc:
                if not doc_no or not doc_title or uploaded_file is None:
                    st.error("Lütfen Doküman Numarası, Doküman Adı giriniz ve bir dosya seçiniz.")
                else:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM departman_dokumanlari WHERE departman=? AND dokuman_no=?", (dept_name, doc_no))
                    existing = cursor.fetchone()
                    conn.close()
                    
                    if existing:
                        st.warning(f"⚠️ **DİKKAT:** `{doc_no}` numaralı **'{existing['dokuman_adi']}'** isimli doküman bu departmanda zaten mevcut!")
                        st.info(f"📌 **Mevcut Dosya:** {existing['dosya_adi']} | **Revizyon:** {existing['revizyon_no']} | **Yükleyen:** {existing['ekleyen']} | **Tarih:** {existing['tarih_saat']}")
                        
                        st.session_state["pending_rev"] = {
                            "dept": dept_name,
                            "doc_no": doc_no,
                            "doc_title": doc_title,
                            "doc_rev": doc_rev,
                            "doc_note": doc_note,
                            "uploaded_file": uploaded_file,
                            "old_row": dict(existing)
                        }
                    else:
                        _, ext = os.path.splitext(uploaded_file.name)
                        standard_fname = generate_standard_filename(doc_no, doc_title, doc_rev, ext)
                        file_name = save_uploaded_file_standard(uploaded_file, UPLOAD_DIR, standard_fname)
                        now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                        
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT INTO departman_dokumanlari 
                            (tarih_saat, departman, dokuman_no, dokuman_adi, revizyon_no, aciklama_not, dosya_adi, ekleyen, revizyon_mu)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Hayır')
                        ''', (now_str, dept_name, doc_no, doc_title, doc_rev, doc_note, file_name, st.session_state["username"]))
                        conn.commit()
                        conn.close()
                        
                        add_notification(st.session_state["username"], dept_name, f"Yeni Doküman Eklendi: {doc_no} - {doc_title} (Rev: {doc_rev})")
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
                                "ext": ext,
                                "doc_no": doc_no_val,
                                "doc_title": base_name,
                                "doc_rev": doc_rev_val
                            })
                    
                    submit_bulk = st.form_submit_button("🚀 SEÇİLİ DOSYALARI DEPARTMANA YÜKLE")

                if submit_bulk and bulk_data:
                    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    
                    for item in bulk_data:
                        standard_fname = generate_standard_filename(item["doc_no"], item["doc_title"], item["doc_rev"], item["ext"])
                        saved_fname = save_uploaded_file_standard(item["file_obj"], UPLOAD_DIR, standard_fname)
                        
                        cursor.execute('''
                            INSERT INTO departman_dokumanlari 
                            (tarih_saat, departman, dokuman_no, dokuman_adi, revizyon_no, aciklama_not, dosya_adi, ekleyen, revizyon_mu)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Hayır')
                        ''', (now_str, dept_name, item["doc_no"].upper(), item["doc_title"], item["doc_rev"], default_note, saved_fname, st.session_state["username"]))
                    
                    conn.commit()
                    conn.close()
                    add_notification(st.session_state["username"], dept_name, f"Toplu Yükleme Yapıldı: {len(bulk_data)} adet doküman eklendi.")
                    st.success(f"🎉 **{len(bulk_data)}** adet dosya başarıyla **{dept_name}** bünyesine eklendi!")
                    st.rerun()

        # REVİZYON ONAYLARI
        if "pending_rev" in st.session_state and st.session_state["pending_rev"]["dept"] == dept_name:
            p = st.session_state["pending_rev"]
            st.error("Lütfen yapmak istediğiniz işlemi seçiniz:")
            
            col_rev1, col_rev2, col_rev3 = st.columns(3)
            
            if col_rev1.button("🔄 EVET, Bu Bir Revizyondur (Eski Dosyayı Arşive Kaldır)"):
                now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                old_info = p["old_row"]
                old_file_name = old_info.get("dosya_adi", "Yok")
                
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
                
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Eski kaydı arşiv tablosuna aktar
                cursor.execute('''
                    INSERT INTO arsiv_dokumanlari 
                    (tarih_saat, departman, dokuman_no, dokuman_adi, revizyon_no, aciklama_not, dosya_adi, ekleyen, arsivlenme_tarihi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (old_info['tarih_saat'], dept_name, old_info['dokuman_no'], old_info['dokuman_adi'], old_info['revizyon_no'], old_info['aciklama_not'], archived_file_ref, old_info['ekleyen'], now_str))
                
                # Aktif tablodaki eski kaydı sil
                cursor.execute("DELETE FROM departman_dokumanlari WHERE id=?", (old_info['id'],))
                
                # Yeni revize kaydı aktif tabloya ekle
                _, ext = os.path.splitext(p["uploaded_file"].name)
                new_standard_fname = generate_standard_filename(p["doc_no"], p["doc_title"], p["doc_rev"], ext)
                new_file_name = save_uploaded_file_standard(p["uploaded_file"], UPLOAD_DIR, new_standard_fname)
                
                cursor.execute('''
                    INSERT INTO departman_dokumanlari 
                    (tarih_saat, departman, dokuman_no, dokuman_adi, revizyon_no, aciklama_not, dosya_adi, ekleyen, revizyon_mu)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Evet')
                ''', (now_str, dept_name, p["doc_no"], p["doc_title"], p["doc_rev"], p["doc_note"], new_file_name, st.session_state["username"]))
                
                conn.commit()
                conn.close()
                
                add_notification(st.session_state["username"], dept_name, f"REVİZYON YAPILDI: {p['doc_no']} - {p['doc_title']} (Rev: {p['doc_rev']})")
                del st.session_state["pending_rev"]
                st.success(f"✅ Revizyon işlendi! Yeni dosya **{new_file_name}** aktifleşti.")
                st.rerun()

            if col_rev2.button("📄 EVET, Farklı Bir Doküman Olarak Ekle"):
                now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                _, ext = os.path.splitext(p["uploaded_file"].name)
                new_standard_fname = generate_standard_filename(p["doc_no"], p["doc_title"], p["doc_rev"], ext)
                new_file_name = save_uploaded_file_standard(p["uploaded_file"], UPLOAD_DIR, new_standard_fname)
                
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO departman_dokumanlari 
                    (tarih_saat, departman, dokuman_no, dokuman_adi, revizyon_no, aciklama_not, dosya_adi, ekleyen, revizyon_mu)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Hayır')
                ''', (now_str, dept_name, p["doc_no"], p["doc_title"], p["doc_rev"], p["doc_note"], new_file_name, st.session_state["username"]))
                conn.commit()
                conn.close()
                
                add_notification(st.session_state["username"], dept_name, f"Yeni Doküman Eklendi: {p['doc_no']} - {p['doc_title']} (Rev: {p['doc_rev']})")
                del st.session_state["pending_rev"]
                st.success(f"✅ Doküman eklendi: **{new_file_name}**")
                st.rerun()

            if col_rev3.button("❌ İŞLEMİ İPTAL ET"):
                del st.session_state["pending_rev"]
                st.info("İşlem iptal edildi.")
                st.rerun()

    # DEPARTMAN İÇİ LİSTELEME VE ARAMA
    dept_search = st.text_input(f"🔍 {dept_name} İçinde Hızlı Dosya Ara...", key=f"search_{dept_name}").strip()
    tab1, tab2, tab3 = st.tabs(["📄 Aktif Dokümanlar", "🔄 Son Revizeler / Değişiklikler", "📁 Arşivlenen Eski Versiyonlar"])

    # TAB 1: AKTİF DOKÜMANLAR
    with tab1:
        if dept_search:
            q = "%" + dept_search + "%"
            dept_active = get_df_from_query('''
                SELECT tarih_saat AS "Tarih / Saat", dokuman_no AS "Doküman No", dokuman_adi AS "Doküman Adı", 
                       revizyon_no AS "Revizyon No", aciklama_not AS "Açıklama / Not", dosya_adi AS "Dosya Adı", ekleyen AS "Ekleyen"
                FROM departman_dokumanlari 
                WHERE departman=? AND (dokuman_adi LIKE ? OR dokuman_no LIKE ? OR aciklama_not LIKE ?)
                ORDER BY id DESC
            ''', (dept_name, q, q, q))
        else:
            dept_active = get_df_from_query('''
                SELECT tarih_saat AS "Tarih / Saat", dokuman_no AS "Doküman No", dokuman_adi AS "Doküman Adı", 
                       revizyon_no AS "Revizyon No", aciklama_not AS "Açıklama / Not", dosya_adi AS "Dosya Adı", ekleyen AS "Ekleyen"
                FROM departman_dokumanlari WHERE departman=? ORDER BY id DESC
            ''', (dept_name,))

        st.dataframe(dept_active, use_container_width=True)

        for idx, row in dept_active.iterrows():
            f_name = row.get("Dosya Adı", "Yok")
            if f_name and f_name != "Yok":
                f_path = os.path.join(UPLOAD_DIR, f_name)
                if os.path.exists(f_path):
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"📄 **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Rev: {row.get('Revizyon No')})*")
                    with open(f_path, "rb") as f:
                        c2.download_button(label="📥 İndir", data=f, file_name=f_name, key=f"act_down_{idx}_{f_name}")

    # TAB 2: REVİZE EDİLENLER
    with tab2:
        dept_revised = get_df_from_query('''
            SELECT tarih_saat AS "Tarih / Saat", dokuman_no AS "Doküman No", dokuman_adi AS "Doküman Adı", 
                   revizyon_no AS "Revizyon No", aciklama_not AS "Açıklama / Not", dosya_adi AS "Dosya Adı", ekleyen AS "Ekleyen"
            FROM departman_dokumanlari WHERE departman=? AND revizyon_mu='Evet' ORDER BY id DESC
        ''', (dept_name,))
        
        if not dept_revised.empty:
            st.dataframe(dept_revised, use_container_width=True)
        else:
            st.info("Bu departmanda henüz revize edilmiş doküman bulunmamaktadır.")

    # TAB 3: ARŞİVLENEN ESKİ VERSİYONLAR
    with tab3:
        if dept_search:
            q = "%" + dept_search + "%"
            dept_archive = get_df_from_query('''
                SELECT tarih_saat AS "Tarih / Saat", dokuman_no AS "Doküman No", dokuman_adi AS "Doküman Adı", 
                       revizyon_no AS "Revizyon No", aciklama_not AS "Açıklama / Not", dosya_adi AS "Dosya Adı", 
                       ekleyen AS "Ekleyen", arsivlenme_tarihi AS "Arşivlenme Tarihi"
                FROM arsiv_dokumanlari 
                WHERE departman=? AND (dokuman_adi LIKE ? OR dokuman_no LIKE ? OR aciklama_not LIKE ?)
                ORDER BY id DESC
            ''', (dept_name, q, q, q))
        else:
            dept_archive = get_df_from_query('''
                SELECT tarih_saat AS "Tarih / Saat", dokuman_no AS "Doküman No", dokuman_adi AS "Doküman Adı", 
                       revizyon_no AS "Revizyon No", aciklama_not AS "Açıklama / Not", dosya_adi AS "Dosya Adı", 
                       ekleyen AS "Ekleyen", arsivlenme_tarihi AS "Arşivlenme Tarihi"
                FROM arsiv_dokumanlari WHERE departman=? ORDER BY id DESC
            ''', (dept_name,))

        st.dataframe(dept_archive, use_container_width=True)

        for idx, row in dept_archive.iterrows():
            f_name = row.get("Dosya Adı", "Yok")
            if f_name and f_name != "Yok":
                f_path = os.path.join(ARCHIVE_DIR, f_name)
                if os.path.exists(f_path):
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"📁 **[{row.get('Doküman No')}]** {row.get('Doküman Adı')} *(Eski Rev: {row.get('Revizyon No')})* - Arşiv Tarihi: {row.get('Arşivlenme Tarihi')}")
                    with open(f_path, "rb") as f:
                        c2.download_button(label="📥 Eski Versiyonu İndir", data=f, file_name=f_name, key=f"arch_down_{idx}_{f_name}")
