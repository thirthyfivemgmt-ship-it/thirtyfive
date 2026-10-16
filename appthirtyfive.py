import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import plotly.graph_objects as go

# ==============================================================================
# 1. CONFIGURAZIONE PAGINA E CSS "STEALTH" (STILE GOOGLE & STRIPE)
# ==============================================================================
st.set_page_config(
    page_title="Thirtyfive | Financial BI",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

def inject_enterprise_css():
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap');

        .stApp {
            background-color: #F8F9FA;
            font-family: 'Roboto', sans-serif;
            color: #202124;
        }

        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        .stDeployButton {display: none !important;}

        .block-container {
            padding-top: 2rem !important;
            padding-bottom: 3rem !important;
            max-width: 1300px;
        }

        .greeting-header {
            font-size: 2.2rem;
            font-weight: 500;
            color: #202124;
            margin-bottom: 0.1rem;
            letter-spacing: -0.5px;
        }
        .greeting-sub {
            font-size: 1rem;
            color: #5F6368;
            margin-bottom: 2rem;
        }

        .fintech-card {
            background-color: #FFFFFF;
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 1px 3px rgba(60,64,67,0.1), 0 4px 8px rgba(60,64,67,0.04);
            border: 1px solid #E8EAED;
            transition: all 0.25s ease-in-out;
            margin-bottom: 16px;
        }
        .fintech-card:hover {
            box-shadow: 0 6px 16px rgba(60,64,67,0.14);
            transform: translateY(-2px);
        }

        .card-label {
            font-size: 0.8rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: #5F6368;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .card-value {
            font-size: 2.1rem;
            font-weight: 700;
            margin: 4px 0;
            letter-spacing: -0.5px;
        }

        .card-subtext {
            font-size: 0.8rem;
            color: #80868B;
            margin-top: 8px;
        }

        .c-blue   { color: #4285F4; }
        .c-red    { color: #EA4335; }
        .c-green  { color: #34A853; }
        .c-yellow { color: #FBBC05; }

        .dot-blue   { width: 8px; height: 8px; border-radius: 50%; background-color: #4285F4; display: inline-block; }
        .dot-red    { width: 8px; height: 8px; border-radius: 50%; background-color: #EA4335; display: inline-block; }
        .dot-green  { width: 8px; height: 8px; border-radius: 50%; background-color: #34A853; display: inline-block; }
        .dot-yellow { width: 8px; height: 8px; border-radius: 50%; background-color: #FBBC05; display: inline-block; }

        .empty-state-box {
            background-color: #FFFFFF;
            border: 2px dashed #DADCE0;
            border-radius: 16px;
            padding: 70px 24px;
            text-align: center;
            margin: 28px 0;
            box-shadow: 0 1px 2px rgba(60,64,67,0.04);
        }
        .empty-state-text {
            font-size: 1.15rem;
            font-weight: 400;
            color: rgba(95, 99, 104, 0.65);
            letter-spacing: 0.2px;
        }

        .badge-active { background-color: #E6F4EA; color: #137333; padding: 4px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
        .badge-expired { background-color: #FCE8E6; color: #C5221F; padding: 4px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }

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

inject_enterprise_css()

# ==============================================================================
# 2. GESTIONE DATABASE SQLITE & AUDIT LOG (PER ROLLBACK)
# ==============================================================================
DB_FILE = "thirtyfive.db"
LICENSE_PRICE = 35.00

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Tabella Negozianti
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            shop_name TEXT NOT NULL,
            expiry_date TEXT NOT NULL,
            total_paid REAL DEFAULT 0.0
        )
    """)
    # Tabella Audit Log per consentire il Rollback (Revert) delle modifiche
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_id INTEGER,
            action_type TEXT,
            old_expiry TEXT,
            new_expiry TEXT,
            old_paid REAL,
            new_paid REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("SELECT COUNT(*) as cnt FROM merchants")
    if cursor.fetchone()["cnt"] == 0:
        today = datetime.now()
        seed_users = [
            ("Mario", "Caffetteria & Bistrot Mario", (today + timedelta(days=18)).strftime("%Y-%m-%d"), 105.00),
            ("Giulia", "Boutique Fiore", (today + timedelta(days=5)).strftime("%Y-%m-%d"), 70.00),
            ("Luca", "Forno Artigiano", (today - timedelta(days=4)).strftime("%Y-%m-%d"), 35.00),
            ("Alessandro", "Osteria del Borgo", (today + timedelta(days=29)).strftime("%Y-%m-%d"), 140.00)
        ]
        cursor.executemany("INSERT INTO merchants (name, shop_name, expiry_date, total_paid) VALUES (?, ?, ?, ?)", seed_users)
        conn.commit()
    conn.close()

def get_all_merchants():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM merchants ORDER BY id ASC").fetchall()
    conn.close()
    return rows

def add_merchant(name, shop_name, trial_days):
    conn = get_db_connection()
    expiry = (datetime.now().date() + timedelta(days=trial_days)).strftime("%Y-%m-%d")
    try:
        conn.execute("INSERT INTO merchants (name, shop_name, expiry_date, total_paid) VALUES (?, ?, ?, 0.0)", (name, shop_name, expiry))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False
    conn.close()
    return success

def apply_license_action(merchant_id, days_to_add, price_to_add, action_name):
    conn = get_db_connection()
    row = conn.execute("SELECT expiry_date, total_paid FROM merchants WHERE id = ?", (merchant_id,)).fetchone()
    if row:
        old_exp = row["expiry_date"]
        old_paid = row["total_paid"]
        
        today = datetime.now().date()
        current_exp = datetime.strptime(old_exp, "%Y-%m-%d").date()
        
        if days_to_add < 0: # Kill-switch immediato
            new_exp = (today - timedelta(days=1)).strftime("%Y-%m-%d")
            new_paid = old_paid
        else:
            base_date = max(today, current_exp)
            new_exp = (base_date + timedelta(days=days_to_add)).strftime("%Y-%m-%d")
            new_paid = old_paid + price_to_add

        # Salva lo stato precedente nel log prima di modificarlo (per il tasto Revert)
        conn.execute(
            "INSERT INTO audit_logs (merchant_id, action_type, old_expiry, new_expiry, old_paid, new_paid) VALUES (?, ?, ?, ?, ?, ?)",
            (merchant_id, action_name, old_exp, new_exp, old_paid, new_paid)
        )
        # Esegue la modifica
        conn.execute("UPDATE merchants SET expiry_date = ?, total_paid = ? WHERE id = ?", (new_exp, new_paid, merchant_id))
        conn.commit()
    conn.close()

def revert_last_action():
    conn = get_db_connection()
    last_log = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 1").fetchone()
    if last_log:
        m_id = last_log["merchant_id"]
        old_exp = last_log["old_expiry"]
        old_paid = last_log["old_paid"]
        # Ripristina i valori precedenti
        conn.execute("UPDATE merchants SET expiry_date = ?, total_paid = ? WHERE id = ?", (old_exp, old_paid, m_id))
        # Elimina il log
        conn.execute("DELETE FROM audit_logs WHERE id = ?", (last_log["id"],))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

init_db()

# ==============================================================================
# 3. DATA STRIPPING (PRIVACY-BY-DESIGN)
# ==============================================================================
def strip_personal_data_from_pos(uploaded_file):
    filename = uploaded_file.name.lower()
    raw_df = pd.read_csv(uploaded_file) if filename.endswith(".csv") else pd.read_excel(uploaded_file)
    original_cols = list(raw_df.columns)

    date_col = next((c for c in raw_df.columns if any(k in str(c).lower() for k in ["data", "date", "giorno", "timestamp"])), raw_df.columns[0])
    amount_col = next((c for c in raw_df.columns if c != date_col and any(k in str(c).lower() for k in ["importo", "totale", "amount", "incasso", "prezzo"])), None)
    
    if amount_col is None:
        num_cols = raw_df.select_dtypes(include=[np.number]).columns
        amount_col = num_cols[0] if len(num_cols) > 0 else raw_df.columns[1]

    clean_df = raw_df[[date_col, amount_col]].copy()
    clean_df.columns = ["Data", "Importo"]
    removed_cols = [c for c in original_cols if c not in (date_col, amount_col)]
    del raw_df

    if clean_df["Importo"].dtype == object:
        clean_df["Importo"] = clean_df["Importo"].astype(str).str.replace("€", "", regex=False).str.replace(".", "", regex=False).str.replace(",", ".", regex=False).str.strip()
    clean_df["Importo"] = pd.to_numeric(clean_df["Importo"], errors="coerce")
    clean_df["Data"] = pd.to_datetime(clean_df["Data"], errors="coerce", dayfirst=True)

    clean_df = clean_df.dropna(subset=["Data", "Importo"])
    clean_df = clean_df[clean_df["Importo"] > 0].sort_values("Data").reset_index(drop=True)
    return clean_df, removed_cols

# ==============================================================================
# 4. SIDEBAR & PROFILAZIONE
# ==============================================================================
merchants_list = get_all_merchants()
merchant_names = [m["name"] for m in merchants_list]
default_index = merchant_names.index("Mario") if "Mario" in merchant_names else 0

with st.sidebar:
    st.markdown("### ⚙️ Pannello Simulazione")
    selected_merchant_name = st.selectbox("Negoziante attivo:", options=merchant_names, index=default_index)

    st.markdown("---")
    st.markdown("### 🧪 Parametri Stima Fiscale")
    costi_operativi_pct = st.slider("Costi Operativi (%)", 10, 60, 28) / 100.0
    tasse_aliquota_pct = st.slider("Imposte / Tasse (%)", 10, 45, 22) / 100.0

    st.markdown("---")
    st.markdown("### 📥 File POS di Prova")
    sample_df = pd.DataFrame({
        "Data": pd.date_range(end=datetime.now(), periods=20, freq="D").strftime("%d/%m/%Y"),
        "Importo": [420.5, 510, 380.2, 610.8, 720, 890.5, 430, 515.4, 620, 580.9, 490, 530.2, 670, 710.5, 840, 395, 460.8, 590, 645.3, 780],
        "Cliente_Riservato": [f"Cliente {i}" for i in range(1, 21)],
        "PAN_Carta": [f"4532 **** 10{i:02d}" for i in range(1, 21)]
    })
    st.download_button("📄 Scarica POS_Test.csv", data=sample_df.to_csv(index=False).encode("utf-8"), file_name="POS_Test.csv", mime="text/csv")

active_merchant = next(m for m in merchants_list if m["name"] == selected_merchant_name)
server_today = datetime.now().date()
merchant_expiry = datetime.strptime(active_merchant["expiry_date"], "%Y-%m-%d").date()
is_license_active = merchant_expiry >= server_today

# ==============================================================================
# 5. HEADER & NAVIGAZIONE PRINCIPALE
# ==============================================================================
st.markdown(f'<div class="greeting-header">Ciao, {active_merchant["name"]}</div>', unsafe_allow_html=True)
st.markdown(f'<div class="greeting-sub"><strong>Thirtyfive BI</strong> • {active_merchant["shop_name"]}</div>', unsafe_allow_html=True)

tab_dashboard, tab_admin = st.tabs(["📊 Dashboard Finanziaria", "🔐 Area Admin (Kill-Switch & Controlli)"])

# ------------------------------------------------------------------------------
# TAB 1: DASHBOARD
# ------------------------------------------------------------------------------
with tab_dashboard:
    if not is_license_active:
        st.error(f"⛔ **LICENZA SCADUTA IL {merchant_expiry.strftime('%d/%m/%Y')}** — Accesso sospeso. Rinnova l'abbonamento o estendi la licenza nella sezione Admin.")
    else:
        uploaded_pos_file = st.file_uploader("Trascina qui il file Excel o CSV del tuo POS (Data Stripping attivo)", type=["xlsx", "xls", "csv"])

        ricavi_totali, tasse_costi_totali, ebitda_stimato, eaitda_stimato, proiezione_30gg = 0.0, 0.0, 0.0, 0.0, 0.0
        clean_pos_df, stripped_columns = None, []

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
                    
                    giorni_osservati = max(1, (clean_pos_df["Data"].max() - clean_pos_df["Data"].min()).days + 1)
                    proiezione_30gg = (ricavi_totali / giorni_osservati) * 30
            except Exception as e:
                st.error(f"Errore di lettura file: {e}")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(f'<div class="fintech-card"><div class="card-label"><span class="dot-blue"></span>Ricavi Lordi POS</div><div class="card-value c-blue">{ricavi_totali:,.2f} €</div><div class="card-subtext">Totale incassato</div></div>', unsafe_allow_html=True)
        with c2:
            st.markdown(f'<div class="fintech-card"><div class="card-label"><span class="dot-red"></span>Tasse e Costi</div><div class="card-value c-red">{tasse_costi_totali:,.2f} €</div><div class="card-subtext">Costi + Imposte stimate</div></div>', unsafe_allow_html=True)
        with c3:
            st.markdown(f'<div class="fintech-card"><div class="card-label"><span class="dot-green"></span>EBITDA Stimato</div><div class="card-value c-green">{ebitda_stimato:,.2f} €</div><div class="card-subtext">EAITDA netto: <strong>{eaitda_stimato:,.2f} €</strong></div></div>', unsafe_allow_html=True)
        with c4:
            st.markdown(f'<div class="fintech-card"><div class="card-label"><span class="dot-yellow"></span>Proiezione (30gg)</div><div class="card-value c-yellow">{proiezione_30gg:,.2f} €</div><div class="card-subtext">Run-rate stimato</div></div>', unsafe_allow_html=True)

        if clean_pos_df is None or clean_pos_df.empty:
            st.markdown('<div class="empty-state-box"><div class="empty-state-text">Nessun dato inserito. Carica il file del tuo POS per iniziare</div></div>', unsafe_allow_html=True)
        else:
            if stripped_columns:
                st.success(f"🛡️ **Privacy Data Stripping attivo:** rimosse {len(stripped_columns)} colonne con dati personali.")

            col_chart, col_table = st.columns([2, 1])
            with col_chart:
                st.markdown("#### 📈 Andamento Incassi (Plotly FinTech)")
                daily_summary = clean_pos_df.groupby(clean_pos_df["Data"].dt.date)["Importo"].sum().reset_index()
                daily_summary.columns = ["Giorno", "Incasso (€)"]
                
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=daily_summary["Giorno"], y=daily_summary["Incasso (€)"],
                    mode="lines+markers",
                    line=dict(color="#4285F4", width=3, shape="spline"),
                    marker=dict(size=6, color="#FFFFFF", line=dict(color="#4285F4", width=2)),
                    fill="tozeroy", fillcolor="rgba(66, 133, 244, 0.1)",
                    hovertemplate="<b>%{x}</b><br>Incasso: <b>%{y:,.2f} €</b><extra></extra>"
                ))
                fig.update_layout(
                    margin=dict(l=10, r=10, t=10, b=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", height=300,
                    xaxis=dict(showgrid=False, linecolor="#E8EAED"),
                    yaxis=dict(showgrid=True, gridcolor="#F1F3F4", zeroline=False, tickSuffix=" €"),
                    hoverlabel=dict(bgcolor="#202124", font_size=13, font_family="Roboto")
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

            with col_table:
                st.markdown("#### 🔒 Dati Anonimizzati")
                preview = clean_pos_df.copy()
                preview["Data"] = preview["Data"].dt.strftime("%d/%m/%Y")
                preview["Importo"] = preview["Importo"].map(lambda x: f"{x:,.2f} €")
                st.dataframe(preview, use_container_width=True, height=290)

# ------------------------------------------------------------------------------
# TAB 2: ADMIN AVANZATO (CON ROLLBACK E PROVA GRATUITA)
# ------------------------------------------------------------------------------
with tab_admin:
    st.markdown("### 🛡️ Console Amministrazione Avanzata Thirtyfive")
    admin_pwd = st.text_input("Master Password:", type="password")

    if admin_pwd == "admin35":
        # BARRA DEGLI STRUMENTI ADMIN (Rollback & Nuovo Utente)
        col_tool1, col_tool2 = st.columns([1, 1])
        with col_tool1:
            if st.button("↩️ Annulla Ultima Modifica (Rollback)", use_container_width=True, help="Ripristina la situazione prima dell'ultimo clic su estendi o kill."):
                if revert_last_action():
                    st.success("Ultima modifica annullata con successo!")
                    st.rerun()
                else:
                    st.info("Nessuna modifica recente da annullare.")
        
        with col_tool2:
            with st.expander("➕ Aggiungi Nuovo Negoziante / Attiva Trial"):
                with st.form("new_merchant_form"):
                    new_name = st.text_input("Nome Negoziante")
                    new_shop = st.text_input("Nome Negozio / Attività")
                    trial_days = st.number_input("Giorni di Prova Gratuita (Trial)", min_value=1, max_value=365, value=14)
                    submitted = st.form_submit_button("Crea Utente")
                    if submitted:
                        if new_name and new_shop:
                            if add_merchant(new_name, new_shop, int(trial_days)):
                                st.success(f"Negoziante '{new_name}' aggiunto con {trial_days} giorni di prova!")
                                st.rerun()
                            else:
                                st.error("Esiste già un negoziante con questo nome.")
                        else:
                            st.warning("Compila tutti i campi.")

        st.markdown("---")

        all_users = get_all_merchants()
        tot_earn = sum(u["total_paid"] for u in all_users)
        act_cnt = sum(1 for u in all_users if datetime.strptime(u["expiry_date"], "%Y-%m-%d").date() >= server_today)
        
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            st.markdown(f'<div class="fintech-card"><div class="card-label"><span class="dot-green"></span>Fatturato Totale</div><div class="card-value c-green">{tot_earn:,.2f} €</div></div>', unsafe_allow_html=True)
        with ac2:
            st.markdown(f'<div class="fintech-card"><div class="card-label"><span class="dot-blue"></span>Licenze Attive</div><div class="card-value c-blue">{act_cnt}</div></div>', unsafe_allow_html=True)
        with ac3:
            st.markdown(f'<div class="fintech-card"><div class="card-label"><span class="dot-red"></span>Licenze Scadute</div><div class="card-value c-red">{len(all_users)-act_cnt}</div></div>', unsafe_allow_html=True)

        st.markdown("#### 👥 Gestione Abbonamenti & Trial")
        
        # Selettore personalizzato dei giorni di estensione per singolo utente
        custom_days = st.sidebar.selectbox("Durata estensione licenza (Admin):", [7, 14, 30, 60, 90], index=2, help="Scegli quanti giorni aggiungere quando clicchi 'Estendi' nella tabella.")

        for u in all_users:
            exp = datetime.strptime(u["expiry_date"], "%Y-%m-%d").date()
            is_act = exp >= server_today
            badge = '<span class="badge-active">ATTIVO</span>' if is_act else '<span class="badge-expired">SCADUTO</span>'
            
            uc1, uc2, uc3, uc4, uc5 = st.columns([2.0, 1.2, 1.6, 1.9, 1.3])
            with uc1: st.markdown(f"**{u['name']}**<br><small style='color:#5F6368'>{u['shop_name']}</small>", unsafe_allow_html=True)
            with uc2: st.markdown(badge, unsafe_allow_html=True)
            with uc3: st.markdown(f"Scadenza: `{exp.strftime('%d/%m/%Y')}`<br><small>Versato: {u['total_paid']:.0f}€</small>", unsafe_allow_html=True)
            with uc4:
                # Calcola il prezzo proporzionale in base ai giorni scelti (es. 30gg = 35€)
                price_proportional = (LICENSE_PRICE / 30) * custom_days
                if st.button(f"➕ +{custom_days}gg (+{price_proportional:.0f}€)", key=f"ext_{u['id']}", use_container_width=True):
                    apply_license_action(u["id"], days_to_add=custom_days, price_to_add=price_proportional, action_name=f"Estensione +{custom_days}gg")
                    st.success(f"Licenza di {u['name']} estesa di {custom_days} giorni!")
                    st.rerun()
            with uc5:
                if st.button("⛔ Kill", key=f"kill_{u['id']}", use_container_width=True):
                    apply_license_action(u["id"], days_to_add=-1, price_to_add=0.0, action_name="Kill-Switch immediato")
                    st.warning(f"Licenza di {u['name']} bloccata.")
                    st.rerun()
            st.divider()
    elif admin_pwd != "":
        st.error("Password errata.")

# ==============================================================================
# 6. DISCLAIMER
# ==============================================================================
st.markdown("""
<div class="legal-footer">
    <strong>Disclaimer Legale:</strong> Thirtyfive elabora stime finanziarie indicative (EBITDA, EAITDA, proiezioni) e non costituisce in alcun modo consulenza fiscale professionale. 
    Per adempimenti normativi e calcolo delle imposte si rimanda al proprio commercialista.
</div>
""", unsafe_allow_html=True)
