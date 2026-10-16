import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import io

# ==============================================================================
# 1. CONFIGURAZIONE PAGINA E MATERIAL DESIGN CSS (GOOGLE STYLE)
# ==============================================================================
st.set_page_config(
    page_title="Thirtyfive | Financial BI",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

def inject_google_material_css():
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap');

        /* Sfondo generale chiaro e minimale stile Google Workspace */
        .stApp {
            background-color: #F8F9FA;
            font-family: 'Roboto', sans-serif;
            color: #202124;
        }

        /* Saluto in alto a sinistra */
        .greeting-header {
            font-size: 2rem;
            font-weight: 500;
            color: #202124;
            margin-bottom: 0.2rem;
            letter-spacing: -0.5px;
        }
        .greeting-sub {
            font-size: 0.95rem;
            color: #5F6368;
            margin-bottom: 1.8rem;
        }

        /* Card Material Design arrotondate */
        .google-card {
            background-color: #FFFFFF;
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 1px 3px rgba(60,64,67,0.12), 0 4px 8px rgba(60,64,67,0.06);
            border: 1px solid #E8EAED;
            transition: box-shadow 0.2s ease-in-out;
            margin-bottom: 16px;
        }
        .google-card:hover {
            box-shadow: 0 4px 12px rgba(60,64,67,0.16);
        }

        .card-label {
            font-size: 0.85rem;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: #5F6368;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .card-value {
            font-size: 2.1rem;
            font-weight: 700;
            margin: 4px 0;
        }

        .card-subtext {
            font-size: 0.8rem;
            color: #80868B;
            margin-top: 6px;
        }

        /* Palette Colori Ufficiale Google */
        .c-blue   { color: #4285F4; }
        .c-red    { color: #EA4335; }
        .c-green  { color: #34A853; }
        .c-yellow { color: #FBBC05; }

        .dot-blue   { width: 10px; height: 10px; border-radius: 50%; background-color: #4285F4; display: inline-block; }
        .dot-red    { width: 10px; height: 10px; border-radius: 50%; background-color: #EA4335; display: inline-block; }
        .dot-green  { width: 10px; height: 10px; border-radius: 50%; background-color: #34A853; display: inline-block; }
        .dot-yellow { width: 10px; height: 10px; border-radius: 50%; background-color: #FBBC05; display: inline-block; }

        /* Empty State Centrale */
        .empty-state-box {
            background-color: #FFFFFF;
            border: 2px dashed #DADCE0;
            border-radius: 16px;
            padding: 64px 24px;
            text-align: center;
            margin: 28px 0;
            box-shadow: 0 1px 2px rgba(60,64,67,0.05);
        }
        .empty-state-text {
            font-size: 1.15rem;
            font-weight: 400;
            color: rgba(95, 99, 104, 0.65); /* Grigio chiaro semiopaco */
            letter-spacing: 0.2px;
        }

        /* Badge Stato Licenza */
        .badge-active {
            background-color: #E6F4EA;
            color: #137333;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.78rem;
            font-weight: 600;
        }
        .badge-expired {
            background-color: #FCE8E6;
            color: #C5221F;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.78rem;
            font-weight: 600;
        }

        /* Disclaimer Legale in fondo */
        .legal-footer {
            margin-top: 60px;
            padding-top: 20px;
            border-top: 1px solid #E8EAED;
            text-align: center;
            font-size: 0.75rem;
            color: #80868B;
            line-height: 1.5;
        }
    </style>
    """, unsafe_allow_html=True)

inject_google_material_css()

# ==============================================================================
# 2. GESTIONE DATABASE SQLITE LOCALE (UTENTI, LICENZE E GUADAGNI)
# ==============================================================================
DB_FILE = "thirtyfive.db"
LICENSE_PRICE = 35.00  # Prezzo mensile per rinnovo in Euro

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            shop_name TEXT NOT NULL,
            expiry_date TEXT NOT NULL,
            total_paid REAL DEFAULT 0.0
        )
    """)
    # Popolamento iniziale se la tabella è vuota
    cursor.execute("SELECT COUNT(*) as cnt FROM merchants")
    if cursor.fetchone()["cnt"] == 0:
        today = datetime.now()
        seed_users = [
            ("Mario", "Caffetteria & Bistrot Mario", (today + timedelta(days=18)).strftime("%Y-%m-%d"), 105.00),
            ("Giulia", "Boutique Fiore", (today + timedelta(days=5)).strftime("%Y-%m-%d"), 70.00),
            ("Luca", "Forno Artigiano", (today - timedelta(days=4)).strftime("%Y-%m-%d"), 35.00), # Scaduto per test Kill-Switch
            (" Alessandro", "Osteria del Borgo", (today + timedelta(days=29)).strftime("%Y-%m-%d"), 140.00)
        ]
        # Pulizia spazi nei nomi seed
        seed_users = [(u[0].strip(), u[1], u[2], u[3]) for u in seed_users]
        cursor.executemany(
            "INSERT INTO merchants (name, shop_name, expiry_date, total_paid) VALUES (?, ?, ?, ?)",
            seed_users
        )
        conn.commit()
    conn.close()

def get_all_merchants():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM merchants ORDER BY id ASC").fetchall()
    conn.close()
    return rows

def extend_license_30_days(merchant_id):
    conn = get_db_connection()
    row = conn.execute("SELECT expiry_date, total_paid FROM merchants WHERE id = ?", (merchant_id,)).fetchone()
    if row:
        today = datetime.now().date()
        current_exp = datetime.strptime(row["expiry_date"], "%Y-%m-%d").date()
        # Se è già scaduto, i 30 giorni partono da oggi; altrimenti si sommano alla scadenza attuale
        base_date = max(today, current_exp)
        new_exp = (base_date + timedelta(days=30)).strftime("%Y-%m-%d")
        new_paid = row["total_paid"] + LICENSE_PRICE
        conn.execute(
            "UPDATE merchants SET expiry_date = ?, total_paid = ? WHERE id = ?",
            (new_exp, new_paid, merchant_id)
        )
        conn.commit()
    conn.close()

def revoke_license_now(merchant_id):
    """Kill-Switch immediato: imposta la scadenza a ieri."""
    conn = get_db_connection()
    yesterday = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    conn.execute("UPDATE merchants SET expiry_date = ? WHERE id = ?", (yesterday, merchant_id))
    conn.commit()
    conn.close()

init_db()

# ==============================================================================
# 3. FUNZIONE DI DATA STRIPPING (PRIVACY-BY-DESIGN)
# ==============================================================================
def strip_personal_data_from_pos(uploaded_file):
    """
    Legge il file del POS, individua solo le colonne Data e Importo,
    e distrugge immediatamente tutte le altre colonne (Nomi, Carte, Email, ID).
    """
    filename = uploaded_file.name.lower()
    if filename.endswith(".csv"):
        raw_df = pd.read_csv(uploaded_file)
    else:
        raw_df = pd.read_excel(uploaded_file)

    original_cols = list(raw_df.columns)

    # 1. Ricerca intelligente colonna DATA
    date_col = None
    date_keywords = ["data", "date", "giorno", "timestamp", "ora", "transazione"]
    for col in raw_df.columns:
        if any(k in str(col).lower() for k in date_keywords):
            date_col = col
            break
    if date_col is None:
        date_col = raw_df.columns[0]

    # 2. Ricerca intelligente colonna IMPORTO
    amount_col = None
    amount_keywords = ["importo", "totale", "amount", "incasso", "prezzo", "valore", "euro", "netto", "lordo"]
    for col in raw_df.columns:
        if col != date_col and any(k in str(col).lower() for k in amount_keywords):
            amount_col = col
            break

    # Fallback: prima colonna numerica disponibile
    if amount_col is None:
        num_cols = raw_df.select_dtypes(include=[np.number]).columns
        if len(num_cols) > 0:
            amount_col = num_cols[0]
        elif len(raw_df.columns) > 1:
            amount_col = raw_df.columns[1]
        else:
            raise ValueError("Impossibile individuare una colonna numerica per l'importo.")

    # Estrazione esclusiva di Data e Importo (Data Stripping)
    clean_df = raw_df[[date_col, amount_col]].copy()
    clean_df.columns = ["Data", "Importo"]

    # Eliminazione esplicita del dataframe grezzo dalla memoria
    removed_cols = [c for c in original_cols if c not in (date_col, amount_col)]
    del raw_df

    # Normalizzazione valuta e date
    if clean_df["Importo"].dtype == object:
        clean_df["Importo"] = (
            clean_df["Importo"]
            .astype(str)
            .str.replace("€", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.strip()
        )
    clean_df["Importo"] = pd.to_numeric(clean_df["Importo"], errors="coerce")
    clean_df["Data"] = pd.to_datetime(clean_df["Data"], errors="coerce", dayfirst=True)

    # Pulizia righe nulle o negative non valide
    clean_df = clean_df.dropna(subset=["Data", "Importo"])
    clean_df = clean_df[clean_df["Importo"] > 0].sort_values("Data").reset_index(drop=True)

    return clean_df, removed_cols

# ==============================================================================
# 4. SIDEBAR (SELETTORE PROFILO NEGOZIANTE & FILE POS DI ESEMPIO)
# ==============================================================================
merchants_list = get_all_merchants()
merchant_names = [m["name"] for m in merchants_list]
default_index = merchant_names.index("Mario") if "Mario" in merchant_names else 0

with st.sidebar:
    st.markdown("### ⚙️ Pannello Simulazione")
    selected_merchant_name = st.selectbox(
        "Negoziante attivo:",
        options=merchant_names,
        index=default_index,
        help="Di default è selezionato 'Mario'. Puoi cambiare utente per testare il blocco licenza (es. 'Luca')."
    )

    st.markdown("---")
    st.markdown("### 🧪 Parametri Stima Fiscale")
    costi_operativi_pct = st.slider("Costi Operativi Stimati (%)", 10, 60, 28) / 100.0
    tasse_aliquota_pct = st.slider("Imposte / Tasse Stimate (%)", 10, 45, 22) / 100.0

    st.markdown("---")
    st.markdown("### 📥 File POS di Prova")
    st.caption("Scarica un tracciato POS di test (con dati personali finti) per verificare il Data Stripping automatico.")
    sample_df = pd.DataFrame({
        "Data": pd.date_range(end=datetime.now(), periods=20, freq="D").strftime("%d/%m/%Y"),
        "Importo": [420.50, 510.00, 380.20, 610.80, 720.00, 890.50, 430.00, 515.40, 620.00, 580.90,
                    490.00, 530.20, 670.00, 710.50, 840.00, 395.00, 460.80, 590.00, 645.30, 780.00],
        "Nome_Cliente_PII": [f"Cliente Riservato {i}" for i in range(1, 21)],
        "PAN_Carta_PII": [f"4532 **** **** 10{i:02d}" for i in range(1, 21)],
        "Email_PII": [f"privacy{i}@email.it" for i in range(1, 21)]
    })
    csv_buffer = sample_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📄 Scarica POS_Test.csv",
        data=csv_buffer,
        file_name="POS_Test_Con_Dati_Sensibili.csv",
        mime="text/csv"
    )

# Recupero dati del negoziante corrente
active_merchant = next(m for m in merchants_list if m["name"] == selected_merchant_name)
server_today = datetime.now().date()
merchant_expiry = datetime.strptime(active_merchant["expiry_date"], "%Y-%m-%d").date()
is_license_active = merchant_expiry >= server_today

# ==============================================================================
# 5. HEADER E NAVIGAZIONE (DASHBOARD vs ADMIN KILL-SWITCH)
# ==============================================================================
st.markdown(f'<div class="greeting-header">Ciao, {active_merchant["name"]}</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="greeting-sub"><strong>Thirtyfive BI</strong> • {active_merchant["shop_name"]}</div>',
    unsafe_allow_html=True
)

tab_dashboard, tab_admin = st.tabs(["📊 Dashboard Finanziaria", "🔐 Area Admin (Kill-Switch)"])

# ==============================================================================
# TAB 1: DASHBOARD FINANZIARIA NEGOZIANTE
# ==============================================================================
with tab_dashboard:
    if not is_license_active:
        st.error(
            f"⛔ **LICENZA SCADUTA IL {merchant_expiry.strftime('%d/%m/%Y')}** — "
            "L'accesso al motore di analisi Thirtyfive è stato sospeso. "
            "Rinnova l'abbonamento o accedi alla sezione Admin per estendere la licenza di 30 giorni."
        )
    else:
        # FILE UPLOADER (PRIVACY-BY-DESIGN)
        uploaded_pos_file = st.file_uploader(
            "Trascina qui il file Excel o CSV esportato dal tuo POS (Data Stripping attivo)",
            type=["xlsx", "xls", "csv"],
            help="Verranno lette esclusivamente le colonne Data e Importo. Tutti i dati personali verranno scartati istantaneamente."
        )

        # Inizializzazione metriche di default (Empty State = 0.00 €)
        ricavi_totali = 0.00
        tasse_costi_totali = 0.00
        ebitda_stimato = 0.00
        eaitda_stimato = 0.00
        proiezione_30gg = 0.00
        clean_pos_df = None
        stripped_columns = []

        if uploaded_pos_file is not None:
            try:
                clean_pos_df, stripped_columns = strip_personal_data_from_pos(uploaded_pos_file)
                if not clean_pos_df.empty:
                    ricavi_totali = float(clean_pos_df["Importo"].sum())
                    costi_operativi = ricavi_totali * costi_operativi_pct
                    ebitda_stimato = ricavi_totali - costi_operativi

                    imposte_stimate = max(0.0, ebitda_stimato * tasse_aliquota_pct)
                    tasse_costi_totali = costi_operativi + imposte_stimate
                    eaitda_stimato = ricavi_totali - tasse_costi_totali

                    # Calcolo Proiezione a 30 giorni sulla media giornaliera
                    giorni_osservati = max(1, (clean_pos_df["Data"].max() - clean_pos_df["Data"].min()).days + 1)
                    media_giornaliera = ricavi_totali / giorni_osservati
                    proiezione_30gg = media_giornaliera * 30
            except Exception as e:
                st.error(f"Errore nel caricamento o nella pulizia del file POS: {e}")

        # 4 CARD GOOGLE-STYLE (Ricavi, Tasse/Costi, EBITDA, Proiezioni)
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.markdown(f"""
            <div class="google-card">
                <div class="card-label"><span class="dot-blue"></span>Ricavi Lordi POS</div>
                <div class="card-value c-blue">{ricavi_totali:,.2f} €</div>
                <div class="card-subtext">Totale incassi transati</div>
            </div>
            """, unsafe_allow_html=True)

        with col2:
            st.markdown(f"""
            <div class="google-card">
                <div class="card-label"><span class="dot-red"></span>Tasse e Costi</div>
                <div class="card-value c-red">{tasse_costi_totali:,.2f} €</div>
                <div class="card-subtext">Costi op. ({int(costi_operativi_pct*100)}%) + Imposte ({int(tasse_aliquota_pct*100)}%)</div>
            </div>
            """, unsafe_allow_html=True)

        with col3:
            st.markdown(f"""
            <div class="google-card">
                <div class="card-label"><span class="dot-green"></span>EBITDA Stimato</div>
                <div class="card-value c-green">{ebitda_stimato:,.2f} €</div>
                <div class="card-subtext">EAITDA netto: <strong>{eaitda_stimato:,.2f} €</strong></div>
            </div>
            """, unsafe_allow_html=True)

        with col4:
            st.markdown(f"""
            <div class="google-card">
                <div class="card-label"><span class="dot-yellow"></span>Proiezione (30gg)</div>
                <div class="card-value c-yellow">{proiezione_30gg:,.2f} €</div>
                <div class="card-subtext">Run-rate su media giornaliera</div>
            </div>
            """, unsafe_allow_html=True)

        # LOGICA EMPTY STATE vs DATI CARICATI
        if clean_pos_df is None or clean_pos_df.empty:
            st.markdown("""
            <div class="empty-state-box">
                <div class="empty-state-text">
                    Nessun dato inserito. Carica il file del tuo POS per iniziare
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            if stripped_columns:
                st.success(
                    f"🛡️ **Privacy Data Stripping completato:** rimosse istantaneamente {len(stripped_columns)} "
                    f"colonne non necessarie/sensibili ({', '.join(map(str, stripped_columns))})."
                )
            else:
                st.info("🛡️ **Privacy Data Stripping attivo:** mantenute esclusivamente le colonne `Data` e `Importo`.")

            col_chart, col_table = st.columns([2, 1])
            with col_chart:
                st.markdown("#### 📈 Andamento Giornaliero Incassi")
                daily_summary = clean_pos_df.groupby(clean_pos_df["Data"].dt.date)["Importo"].sum().reset_index()
                daily_summary.columns = ["Giorno", "Incasso (€)"]
                st.bar_chart(daily_summary.set_index("Giorno"), color="#4285F4")

            with col_table:
                st.markdown("#### 🔒 Dati Anonimizzati")
                preview_df = clean_pos_df.copy()
                preview_df["Data"] = preview_df["Data"].dt.strftime("%d/%m/%Y")
                preview_df["Importo"] = preview_df["Importo"].map(lambda x: f"{x:,.2f} €")
                st.dataframe(preview_df, use_container_width=True, height=300)

# ==============================================================================
# TAB 2: SEZIONE ADMIN (KILL-SWITCH & LICENZE)
# ==============================================================================
with tab_admin:
    st.markdown("### 🛡️ Console di Amministrazione Thirtyfive")
    admin_pwd = st.text_input("Inserisci la Master Password per sbloccare il Kill-Switch:", type="password")

    if admin_pwd == "admin35":
        all_users = get_all_merchants()
        total_thirtyfive_earnings = sum(u["total_paid"] for u in all_users)
        active_count = sum(
            1 for u in all_users
            if datetime.strptime(u["expiry_date"], "%Y-%m-%d").date() >= server_today
        )
        expired_count = len(all_users) - active_count

        # Riepilogo Guadagni Thirtyfive
        a_col1, a_col2, a_col3 = st.columns(3)
        with a_col1:
            st.markdown(f"""
            <div class="google-card">
                <div class="card-label"><span class="dot-green"></span>Guadagni Totali Thirtyfive</div>
                <div class="card-value c-green">{total_thirtyfive_earnings:,.2f} €</div>
                <div class="card-subtext">Fatturato licenze incassato</div>
            </div>
            """, unsafe_allow_html=True)

        with a_col2:
            st.markdown(f"""
            <div class="google-card">
                <div class="card-label"><span class="dot-blue"></span>Licenze Attive</div>
                <div class="card-value c-blue">{active_count}</div>
                <div class="card-subtext">Verificate con data server ({server_today.strftime('%d/%m/%Y')})</div>
            </div>
            """, unsafe_allow_html=True)

        with a_col3:
            st.markdown(f"""
            <div class="google-card">
                <div class="card-label"><span class="dot-red"></span>Licenze Scadute</div>
                <div class="card-value c-red">{expired_count}</div>
                <div class="card-subtext">Utenti bloccati dal Kill-Switch</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("#### 👥 Gestione Licenze Negozianti")

        for user in all_users:
            exp_date = datetime.strptime(user["expiry_date"], "%Y-%m-%d").date()
            is_act = exp_date >= server_today
            badge_html = (
                '<span class="badge-active">ATTIVO</span>'
                if is_act else
                '<span class="badge-expired">SCADUTO</span>'
            )

            with st.container():
                u_col1, u_col2, u_col3, u_col4, u_col5 = st.columns([2.2, 1.3, 1.5, 1.8, 1.4])
                with u_col1:
                    st.markdown(f"**{user['name']}**  \n<small style='color:#5F6368'>{user['shop_name']}</small>", unsafe_allow_html=True)
                with u_col2:
                    st.markdown(badge_html, unsafe_allow_html=True)
                with u_col3:
                    st.markdown(f"**Scadenza:** `{exp_date.strftime('%d/%m/%Y')}`  \n<small>Versato: {user['total_paid']:,.2f} €</small>", unsafe_allow_html=True)
                with u_col4:
                    if st.button(f"➕ Estendi +30gg (+{LICENSE_PRICE:.0f}€)", key=f"ext_{user['id']}", use_container_width=True):
                        extend_license_30_days(user["id"])
                        st.rerun()
                with u_col5:
                    if st.button("⛔ Kill-Switch", key=f"kill_{user['id']}", use_container_width=True):
                        revoke_license_now(user["id"])
                        st.rerun()
                st.divider()

    elif admin_pwd != "":
        st.error("Password errata. Accesso negato.")

# ==============================================================================
# 6. DISCLAIMER LEGALE OBBLIGATORIO
# ==============================================================================
st.markdown("""
<div class="legal-footer">
    <strong>Disclaimer Legale:</strong> L'applicazione <strong>Thirtyfive</strong> elabora esclusivamente stime finanziarie e gestionali a carattere indicativo 
    (inclusi indicatori quali <em>EBITDA</em>, <em>EAITDA</em>, incidenza fiscale e proiezioni dei flussi di cassa) basate sui parametri impostati dall'utente e sui dati aggregati del POS. 
    I risultati mostrati non hanno valore certificato e <strong>non costituiscono in alcun modo consulenza fiscale, contabile o legale professionale</strong>. 
    Per la redazione del bilancio, il calcolo esatto delle imposte e gli adempimenti normativi si rimanda sempre al proprio Dottore Commercialista o consulente abilitato.
</div>
""", unsafe_allow_html=True)