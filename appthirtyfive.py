"""Thirtyfive Financial BI — hardened local reference implementation.

This is deliberately a small, self-contained Streamlit application. It is a
safer replacement for the pasted demo, not a payment processor or a substitute
for professional accounting, legal, or security review.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import logging
import os
import re
import sqlite3
import time
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st


LOGGER = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_XLSX_FILES = 250
MAX_XLSX_COMPRESSION_RATIO = 100
MAX_ROWS = 50_000
MAX_COLUMNS = 100
SESSION_TIMEOUT_SECONDS = 30 * 60
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 5 * 60


class UserInputError(ValueError):
    """An input that is safe to describe to the signed-in user."""


class UploadError(ValueError):
    """An invalid or unsupported POS upload."""


class DatabaseSetupError(RuntimeError):
    """A database that must be migrated deliberately, never implicitly."""


def setting(name: str, default: str | None = None) -> str | None:
    """Read deploy-time configuration without embedding secrets in source."""
    value = os.getenv(name)
    if value:
        return value
    try:
        value = st.secrets.get(name)
    except Exception:  # Missing secrets.toml is normal during first setup.
        value = None
    return str(value) if value not in (None, "") else default


def business_timezone() -> ZoneInfo:
    timezone_name = setting("APP_TIMEZONE", "Europe/Rome")
    try:
        return ZoneInfo(timezone_name or "Europe/Rome")
    except Exception:
        LOGGER.warning("Invalid APP_TIMEZONE; using Europe/Rome")
        return ZoneInfo("Europe/Rome")


def local_today() -> date:
    return datetime.now(business_timezone()).date()


def utc_now_text() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat(sep=" ") + " UTC"


def database_path() -> Path:
    configured_dir = setting("APP_DATA_DIR")
    data_dir = Path(configured_dir).expanduser() if configured_dir else APP_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "thirtyfive.db"


def get_db_connection() -> sqlite3.Connection:
    """Open a short-lived SQLite connection with safe local defaults."""
    connection = sqlite3.connect(database_path(), timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def write_transaction() -> Iterator[sqlite3.Connection]:
    """Serialize a state transition so licence and ledger values stay aligned."""
    connection = get_db_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def password_hash(password: str, salt: bytes | None = None) -> str:
    """Create a portable scrypt password hash; only this value is stored."""
    if len(password) < 14:
        raise UserInputError("La password deve contenere almeno 14 caratteri.")
    salt = salt or os.urandom(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return "$".join(
        (
            "scrypt",
            str(n),
            str(r),
            str(p),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a scrypt hash while bounding parameters from a compromised DB."""
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$")
        n, r, p = int(n_text), int(r_text), int(p_text)
        if algorithm != "scrypt" or n < 2**14 or n > 2**18 or n & (n - 1) or not (1 <= r <= 16 and 1 <= p <= 4):
            return False
        salt = base64.b64decode(salt_text.encode("ascii"), validate=True)
        expected = base64.b64decode(digest_text.encode("ascii"), validate=True)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, InvalidOperation, binascii.Error):
        return False


def valid_password_hash_format(encoded: str) -> bool:
    """Reject malformed bootstrap secrets before an operator gets locked out."""
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$")
        n, r, p = int(n_text), int(r_text), int(p_text)
        salt = base64.b64decode(salt_text.encode("ascii"), validate=True)
        digest = base64.b64decode(digest_text.encode("ascii"), validate=True)
        return bool(
            algorithm == "scrypt"
            and 2**14 <= n <= 2**18
            and not n & (n - 1)
            and 1 <= r <= 16
            and 1 <= p <= 4
            and len(salt) >= 16
            and len(digest) == 32
        )
    except (ValueError, TypeError, binascii.Error):
        return False


def valid_username(value: str) -> str:
    username = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,64}", username):
        raise UserInputError("Lo username deve avere 3–64 caratteri: lettere, numeri, punto, trattino o underscore.")
    return username


def validated_text(value: str, label: str, minimum: int, maximum: int) -> str:
    text = value.strip()
    if not minimum <= len(text) <= maximum or any(ord(char) < 32 for char in text):
        raise UserInputError(f"{label}: inserisci da {minimum} a {maximum} caratteri stampabili.")
    return text


def validated_reference(value: str) -> str:
    reference = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9._:/-]{4,80}", reference):
        raise UserInputError("Il riferimento pagamento deve avere 4–80 caratteri alfanumerici o . _ : / -.")
    return reference


def money_to_cents(value: object) -> int:
    """Turn a validated numeric UI value into integer cents exactly once."""
    try:
        decimal_value = Decimal(str(value))
        if not decimal_value.is_finite():
            raise InvalidOperation
        cents = (decimal_value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise UserInputError("Importo non valido.") from None
    result = int(cents)
    if abs(result) > 100_000_000:
        raise UserInputError("Importo oltre il limite consentito.")
    return result


def format_cents(cents: int | float) -> str:
    value = int(cents)
    sign = "−" if value < 0 else ""
    value = abs(value)
    euros, remainder = divmod(value, 100)
    return f"{sign}{euros:,}".replace(",", ".") + f",{remainder:02d} €"


def init_database() -> None:
    """Create a new schema. A legacy database is refused instead of mutated."""
    connection = get_db_connection()
    try:
        merchant_table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'merchants'"
        ).fetchone()
        if merchant_table_exists:
            merchant_columns = {row["name"] for row in connection.execute("PRAGMA table_info(merchants)")}
            if "total_paid_cents" not in merchant_columns:
                raise DatabaseSetupError(
                    "È stato trovato il database del prototipo precedente. Per evitare una migrazione finanziaria automatica, "
                    "configura un nuovo APP_DATA_DIR e importa i dati con una procedura verificata."
                )

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS merchants (
                id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL,
                shop_name TEXT NOT NULL,
                expiry_date TEXT NOT NULL,
                total_paid_cents INTEGER NOT NULL DEFAULT 0 CHECK(total_paid_cents >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'merchant')),
                merchant_id INTEGER REFERENCES merchants(id) ON DELETE RESTRICT,
                is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(
                    (role = 'admin' AND merchant_id IS NULL) OR
                    (role = 'merchant' AND merchant_id IS NOT NULL)
                )
            );

            CREATE TABLE IF NOT EXISTS payment_records (
                id INTEGER PRIMARY KEY,
                merchant_id INTEGER NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
                amount_cents INTEGER NOT NULL CHECK(amount_cents <> 0),
                kind TEXT NOT NULL CHECK(kind IN ('payment', 'reversal')),
                provider_reference TEXT NOT NULL UNIQUE COLLATE NOCASE,
                recorded_by_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                reversal_of_payment_id INTEGER UNIQUE REFERENCES payment_records(id) ON DELETE RESTRICT,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS license_events (
                id INTEGER PRIMARY KEY,
                merchant_id INTEGER NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
                actor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                event_type TEXT NOT NULL CHECK(event_type IN ('merchant_created', 'license_extension', 'license_kill', 'reversal')),
                old_expiry_date TEXT,
                new_expiry_date TEXT,
                old_paid_cents INTEGER,
                new_paid_cents INTEGER,
                reason TEXT NOT NULL,
                payment_id INTEGER REFERENCES payment_records(id) ON DELETE RESTRICT,
                reversal_of_event_id INTEGER UNIQUE REFERENCES license_events(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_users_merchant_id ON users(merchant_id);
            CREATE INDEX IF NOT EXISTS idx_events_merchant_id_id ON license_events(merchant_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_payments_merchant_id_id ON payment_records(merchant_id, id DESC);
            """
        )
    finally:
        connection.close()


def ensure_bootstrap_admin() -> tuple[bool, str | None]:
    """Create or rotate the explicitly configured bootstrap administrator."""
    username = setting("ADMIN_USERNAME")
    encoded_password = setting("ADMIN_PASSWORD_HASH")
    if not username or not encoded_password:
        return False, "Manca ADMIN_USERNAME o ADMIN_PASSWORD_HASH nella configurazione del server."
    try:
        username = valid_username(username)
    except UserInputError as error:
        return False, str(error)
    if not valid_password_hash_format(encoded_password):
        return False, "ADMIN_PASSWORD_HASH non ha un formato scrypt valido."

    try:
        with write_transaction() as connection:
            existing = connection.execute(
                "SELECT id, role FROM users WHERE username = ?", (username,)
            ).fetchone()
            if existing and existing["role"] != "admin":
                return False, "Lo username dell'amministratore configurato è già assegnato a un commerciante."
            if existing:
                connection.execute(
                    "UPDATE users SET password_hash = ?, is_active = 1 WHERE id = ?",
                    (encoded_password, existing["id"]),
                )
            else:
                connection.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
                    (username, encoded_password),
                )
    except sqlite3.Error:
        LOGGER.exception("Could not bootstrap administrator")
        return False, "Non è stato possibile inizializzare l'amministratore."
    return True, None


def get_user_by_username(username: str) -> sqlite3.Row | None:
    connection = get_db_connection()
    try:
        return connection.execute(
            "SELECT id, username, password_hash, role, merchant_id, is_active FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        connection.close()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    connection = get_db_connection()
    try:
        return connection.execute(
            "SELECT id, username, role, merchant_id, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    finally:
        connection.close()


def clear_authentication() -> None:
    for key in ("current_user_id", "last_seen_at", "login_attempts", "locked_until"):
        st.session_state.pop(key, None)


def current_user() -> sqlite3.Row | None:
    user_id = st.session_state.get("current_user_id")
    last_seen = st.session_state.get("last_seen_at")
    if not user_id or not isinstance(last_seen, (int, float)):
        return None
    if time.time() - last_seen > SESSION_TIMEOUT_SECONDS:
        clear_authentication()
        return None
    user = get_user_by_id(int(user_id))
    if user is None or not user["is_active"]:
        clear_authentication()
        return None
    st.session_state.last_seen_at = time.time()
    return user


def login_screen() -> None:
    st.title("Thirtyfive · Financial BI")
    st.caption("Accesso riservato. I dati del commerciante vengono scelti dal server dopo l'autenticazione.")

    locked_until = float(st.session_state.get("locked_until", 0))
    if time.time() < locked_until:
        seconds = int(locked_until - time.time())
        st.error(f"Troppi tentativi. Riprova tra circa {max(1, seconds // 60 + 1)} minuti.")
        st.stop()

    with st.form("login", clear_on_submit=True):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Accedi", use_container_width=True)

    if submitted:
        user = get_user_by_username(username.strip()) if username.strip() else None
        authenticated = bool(user and user["is_active"] and verify_password(password, user["password_hash"]))
        if not authenticated:
            attempts = int(st.session_state.get("login_attempts", 0)) + 1
            st.session_state.login_attempts = attempts
            if attempts >= MAX_LOGIN_ATTEMPTS:
                st.session_state.locked_until = time.time() + LOGIN_LOCKOUT_SECONDS
                st.session_state.login_attempts = 0
                st.error("Troppi tentativi non riusciti. L'accesso è temporaneamente bloccato.")
            else:
                st.error("Credenziali non valide.")
            return
        st.session_state.current_user_id = int(user["id"])
        st.session_state.last_seen_at = time.time()
        st.session_state.login_attempts = 0
        st.rerun()

    st.stop()


def merchant_by_id(merchant_id: int) -> sqlite3.Row | None:
    connection = get_db_connection()
    try:
        return connection.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,)).fetchone()
    finally:
        connection.close()


def all_merchants() -> list[sqlite3.Row]:
    connection = get_db_connection()
    try:
        return connection.execute("SELECT * FROM merchants ORDER BY display_name COLLATE NOCASE, id").fetchall()
    finally:
        connection.close()


def merchant_for_user(user: sqlite3.Row) -> sqlite3.Row | None:
    merchant_id = user["merchant_id"]
    return merchant_by_id(int(merchant_id)) if merchant_id is not None else None


def require_active_admin(connection: sqlite3.Connection, actor_user_id: int) -> None:
    """Enforce authorization in every mutation, not merely in the visible UI."""
    actor = connection.execute(
        "SELECT role, is_active FROM users WHERE id = ?", (actor_user_id,)
    ).fetchone()
    if actor is None or actor["role"] != "admin" or not actor["is_active"]:
        raise UserInputError("Operazione non autorizzata.")


def create_merchant_and_account(
    actor_user_id: int,
    display_name: str,
    shop_name: str,
    username: str,
    initial_password: str,
    trial_days: int,
) -> str:
    display_name = validated_text(display_name, "Nome commerciante", 2, 100)
    shop_name = validated_text(shop_name, "Attività", 2, 160)
    username = valid_username(username)
    if not 1 <= int(trial_days) <= 365:
        raise UserInputError("Il trial deve durare da 1 a 365 giorni.")
    encoded_password = password_hash(initial_password)
    expiry_date = (local_today() + timedelta(days=int(trial_days))).isoformat()

    try:
        with write_transaction() as connection:
            require_active_admin(connection, actor_user_id)
            merchant_cursor = connection.execute(
                "INSERT INTO merchants (display_name, shop_name, expiry_date) VALUES (?, ?, ?)",
                (display_name, shop_name, expiry_date),
            )
            merchant_id = int(merchant_cursor.lastrowid)
            connection.execute(
                "INSERT INTO users (username, password_hash, role, merchant_id) VALUES (?, ?, 'merchant', ?)",
                (username, encoded_password, merchant_id),
            )
            connection.execute(
                """
                INSERT INTO license_events
                    (merchant_id, actor_user_id, event_type, new_expiry_date, new_paid_cents, reason)
                VALUES (?, ?, 'merchant_created', ?, 0, 'Creazione commerciante e trial')
                """,
                (merchant_id, actor_user_id, expiry_date),
            )
    except sqlite3.IntegrityError as error:
        if "users.username" in str(error).lower() or "unique" in str(error).lower():
            raise UserInputError("Questo username è già in uso.") from None
        LOGGER.exception("Could not create merchant")
        raise UserInputError("Non è stato possibile creare il commerciante.") from None
    return "Commerciante e account creati. Comunica la password iniziale attraverso un canale sicuro."


def record_license_extension(
    actor_user_id: int,
    merchant_id: int,
    days_to_add: int,
    paid_cents: int,
    payment_reference: str,
    reason: str,
) -> str:
    if int(days_to_add) not in {7, 14, 30, 60, 90}:
        raise UserInputError("Scegli una durata prevista dall'offerta.")
    if paid_cents <= 0:
        raise UserInputError("Registra un importo di pagamento maggiore di zero.")
    payment_reference = validated_reference(payment_reference)
    reason = validated_text(reason, "Nota amministrativa", 3, 300)

    try:
        with write_transaction() as connection:
            require_active_admin(connection, actor_user_id)
            merchant = connection.execute(
                "SELECT expiry_date, total_paid_cents FROM merchants WHERE id = ?", (merchant_id,)
            ).fetchone()
            if merchant is None:
                raise UserInputError("Commerciante non trovato.")
            old_expiry = date.fromisoformat(merchant["expiry_date"])
            old_paid = int(merchant["total_paid_cents"])
            new_expiry = max(local_today(), old_expiry) + timedelta(days=int(days_to_add))
            new_paid = old_paid + paid_cents
            payment_cursor = connection.execute(
                """
                INSERT INTO payment_records
                    (merchant_id, amount_cents, kind, provider_reference, recorded_by_user_id)
                VALUES (?, ?, 'payment', ?, ?)
                """,
                (merchant_id, paid_cents, payment_reference, actor_user_id),
            )
            payment_id = int(payment_cursor.lastrowid)
            connection.execute(
                """
                UPDATE merchants
                SET expiry_date = ?, total_paid_cents = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_expiry.isoformat(), new_paid, merchant_id),
            )
            connection.execute(
                """
                INSERT INTO license_events
                    (merchant_id, actor_user_id, event_type, old_expiry_date, new_expiry_date,
                     old_paid_cents, new_paid_cents, reason, payment_id)
                VALUES (?, ?, 'license_extension', ?, ?, ?, ?, ?, ?)
                """,
                (
                    merchant_id,
                    actor_user_id,
                    old_expiry.isoformat(),
                    new_expiry.isoformat(),
                    old_paid,
                    new_paid,
                    reason,
                    payment_id,
                ),
            )
    except sqlite3.IntegrityError:
        raise UserInputError("Il riferimento di pagamento è già stato registrato.") from None
    return "Rinnovo e registrazione contabile manuale salvati nell'audit."


def kill_license(actor_user_id: int, merchant_id: int, reason: str) -> str:
    reason = validated_text(reason, "Motivo del blocco", 8, 300)
    try:
        with write_transaction() as connection:
            require_active_admin(connection, actor_user_id)
            merchant = connection.execute(
                "SELECT expiry_date, total_paid_cents FROM merchants WHERE id = ?", (merchant_id,)
            ).fetchone()
            if merchant is None:
                raise UserInputError("Commerciante non trovato.")
            old_expiry = date.fromisoformat(merchant["expiry_date"])
            old_paid = int(merchant["total_paid_cents"])
            new_expiry = local_today() - timedelta(days=1)
            connection.execute(
                "UPDATE merchants SET expiry_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_expiry.isoformat(), merchant_id),
            )
            connection.execute(
                """
                INSERT INTO license_events
                    (merchant_id, actor_user_id, event_type, old_expiry_date, new_expiry_date,
                     old_paid_cents, new_paid_cents, reason)
                VALUES (?, ?, 'license_kill', ?, ?, ?, ?, ?)
                """,
                (merchant_id, actor_user_id, old_expiry.isoformat(), new_expiry.isoformat(), old_paid, old_paid, reason),
            )
    except sqlite3.Error:
        LOGGER.exception("Could not suspend licence")
        raise UserInputError("Non è stato possibile sospendere la licenza.") from None
    return "Licenza sospesa. L'operazione può essere ricostruita dall'audit." 


def reverse_latest_merchant_action(actor_user_id: int, merchant_id: int, reason: str) -> str:
    """Add a compensating event; audit rows and payment records are never deleted."""
    reason = validated_text(reason, "Motivo dello storno", 8, 300)
    try:
        with write_transaction() as connection:
            require_active_admin(connection, actor_user_id)
            latest_event = connection.execute(
                "SELECT * FROM license_events WHERE merchant_id = ? ORDER BY id DESC LIMIT 1", (merchant_id,)
            ).fetchone()
            if latest_event is None or latest_event["event_type"] not in {"license_extension", "license_kill"}:
                raise UserInputError("Non c'è un'ultima estensione o sospensione che possa essere stornata.")
            existing_reversal = connection.execute(
                "SELECT 1 FROM license_events WHERE reversal_of_event_id = ?", (latest_event["id"],)
            ).fetchone()
            if existing_reversal:
                raise UserInputError("Questa operazione è già stata stornata.")

            merchant = connection.execute(
                "SELECT expiry_date, total_paid_cents FROM merchants WHERE id = ?", (merchant_id,)
            ).fetchone()
            if merchant is None:
                raise UserInputError("Commerciante non trovato.")
            if (
                merchant["expiry_date"] != latest_event["new_expiry_date"]
                or int(merchant["total_paid_cents"]) != int(latest_event["new_paid_cents"])
            ):
                raise UserInputError("Lo stato corrente non corrisponde all'ultima azione: serve una revisione manuale.")

            if latest_event["payment_id"] is not None:
                payment = connection.execute(
                    "SELECT * FROM payment_records WHERE id = ?", (latest_event["payment_id"],)
                ).fetchone()
                if payment is None or payment["kind"] != "payment":
                    raise UserInputError("Il pagamento associato richiede una revisione manuale.")
                connection.execute(
                    """
                    INSERT INTO payment_records
                        (merchant_id, amount_cents, kind, provider_reference, recorded_by_user_id, reversal_of_payment_id)
                    VALUES (?, ?, 'reversal', ?, ?, ?)
                    """,
                    (
                        merchant_id,
                        -int(payment["amount_cents"]),
                        f"REVERSAL-{payment['id']}",
                        actor_user_id,
                        payment["id"],
                    ),
                )

            connection.execute(
                """
                UPDATE merchants
                SET expiry_date = ?, total_paid_cents = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (latest_event["old_expiry_date"], latest_event["old_paid_cents"], merchant_id),
            )
            connection.execute(
                """
                INSERT INTO license_events
                    (merchant_id, actor_user_id, event_type, old_expiry_date, new_expiry_date,
                     old_paid_cents, new_paid_cents, reason, reversal_of_event_id)
                VALUES (?, ?, 'reversal', ?, ?, ?, ?, ?, ?)
                """,
                (
                    merchant_id,
                    actor_user_id,
                    latest_event["new_expiry_date"],
                    latest_event["old_expiry_date"],
                    latest_event["new_paid_cents"],
                    latest_event["old_paid_cents"],
                    reason,
                    latest_event["id"],
                ),
            )
    except sqlite3.IntegrityError:
        raise UserInputError("Lo storno è già stato registrato o richiede una revisione manuale.") from None
    except sqlite3.Error:
        LOGGER.exception("Could not reverse latest merchant action")
        raise UserInputError("Non è stato possibile registrare lo storno.") from None
    return "Storno registrato come nuovo evento compensativo; lo storico originario è conservato."


def recent_audit_events(limit: int = 50) -> list[sqlite3.Row]:
    connection = get_db_connection()
    try:
        return connection.execute(
            """
            SELECT e.created_at, e.event_type, e.reason, e.old_expiry_date, e.new_expiry_date,
                   e.old_paid_cents, e.new_paid_cents, m.display_name, u.username AS actor
            FROM license_events e
            JOIN merchants m ON m.id = e.merchant_id
            JOIN users u ON u.id = e.actor_user_id
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        connection.close()


def validate_xlsx_archive(payload: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_XLSX_FILES:
                raise UploadError("Il file XLSX contiene troppi elementi interni.")
            names = {entry.filename for entry in entries}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise UploadError("Il file non è un XLSX valido.")
            total_uncompressed = 0
            for entry in entries:
                if ".." in Path(entry.filename).parts:
                    raise UploadError("Il file XLSX contiene un percorso non valido.")
                total_uncompressed += entry.file_size
                if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise UploadError("Il contenuto estratto del file XLSX supera il limite consentito.")
                if entry.compress_size and entry.file_size / entry.compress_size > MAX_XLSX_COMPRESSION_RATIO:
                    raise UploadError("Il rapporto di compressione del file XLSX non è consentito.")
    except zipfile.BadZipFile:
        raise UploadError("Il file XLSX è danneggiato o non valido.") from None


def read_uploaded_frame(uploaded_file: object) -> pd.DataFrame:
    """Read a small, explicit CSV/XLSX file in memory without caching raw PII."""
    name = getattr(uploaded_file, "name", "")
    size = int(getattr(uploaded_file, "size", 0))
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise UploadError("Sono ammessi soltanto file CSV UTF-8 e XLSX.")
    if size <= 0 or size > MAX_UPLOAD_BYTES:
        raise UploadError("Il file deve avere una dimensione compresa tra 1 byte e 5 MB.")

    payload = uploaded_file.getvalue()
    try:
        if suffix == ".xlsx":
            validate_xlsx_archive(payload)
            frame = pd.read_excel(
                io.BytesIO(payload), engine="openpyxl", nrows=MAX_ROWS + 1, engine_kwargs={"data_only": True}
            )
        else:
            frame = pd.read_csv(
                io.BytesIO(payload), encoding="utf-8-sig", sep=None, engine="python", nrows=MAX_ROWS + 1
            )
    except (UnicodeDecodeError, pd.errors.ParserError, ValueError, OSError) as error:
        LOGGER.info("Upload rejected while parsing: %s", type(error).__name__)
        raise UploadError("Il file non può essere letto. Usa CSV UTF-8 o XLSX con un'intestazione valida.") from None
    finally:
        payload = b""

    if len(frame) > MAX_ROWS:
        raise UploadError(f"Il file supera il limite di {MAX_ROWS:,} righe.")
    if frame.empty or not 2 <= len(frame.columns) <= MAX_COLUMNS:
        raise UploadError("Il file deve contenere da 2 a 100 colonne e almeno una riga.")
    columns = [str(column).strip() for column in frame.columns]
    if any(not column for column in columns) or len(set(columns)) != len(columns):
        raise UploadError("Le colonne devono avere nomi univoci e non vuoti.")
    frame.columns = columns
    return frame


def parse_amount_to_cents(value: object, locale: str) -> int | None:
    if pd.isna(value):
        return None
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float, Decimal)):
            number = Decimal(str(value))
        else:
            text = str(value).strip().replace("€", "").replace("EUR", "").replace(" ", "")
            if locale == "it":
                number = Decimal(text.replace(".", "").replace(",", "."))
            else:
                number = Decimal(text.replace(",", ""))
        if not number.is_finite():
            return None
        cents = (number * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        result = int(cents)
        return result if abs(result) <= 100_000_000 else None
    except (InvalidOperation, ValueError):
        return None


def clean_pos_data(
    uploaded_file: object, date_column: str, amount_column: str, date_format: str, amount_locale: str
) -> pd.DataFrame:
    if date_column == amount_column:
        raise UploadError("La colonna data e la colonna importo devono essere diverse.")
    frame = read_uploaded_frame(uploaded_file)
    if date_column not in frame.columns or amount_column not in frame.columns:
        raise UploadError("Le colonne selezionate non sono più presenti nel file.")
    try:
        parsed_dates = pd.to_datetime(frame[date_column], format=date_format, errors="coerce")
        parsed_amounts = frame[amount_column].map(lambda value: parse_amount_to_cents(value, amount_locale))
        valid_rows = parsed_dates.notna() & parsed_amounts.notna()
        invalid_count = int((~valid_rows).sum())
        if invalid_count:
            raise UploadError(
                f"{invalid_count} righe hanno data o importo non validi. Correggi il file: non vengono calcolati dati parziali."
            )
        clean = pd.DataFrame(
            {
                "Data": parsed_dates.dt.normalize(),
                "Importo_centesimi": parsed_amounts.astype("int64"),
            }
        ).sort_values("Data", kind="stable").reset_index(drop=True)
        if clean.empty:
            raise UploadError("Il file non contiene transazioni utilizzabili.")
        return clean
    finally:
        # The raw PII-bearing dataframe is deliberately not cached or returned.
        del frame


def percentage_cents(amount_cents: int, percentage: int) -> int:
    return int(
        (Decimal(amount_cents) * Decimal(percentage) / Decimal("100")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def render_financial_metrics(clean: pd.DataFrame, cost_rate: int, tax_rate: int) -> None:
    revenue = int(clean["Importo_centesimi"].sum())
    operating_costs = percentage_cents(revenue, cost_rate)
    ebitda = revenue - operating_costs
    estimated_tax = max(0, percentage_cents(ebitda, tax_rate))
    estimated_net = revenue - operating_costs - estimated_tax
    observed_days = max(1, (clean["Data"].max() - clean["Data"].min()).days + 1)
    projection = int(
        (Decimal(revenue) / Decimal(observed_days) * Decimal("30")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    columns = st.columns(4)
    columns[0].metric("Ricavi POS", format_cents(revenue))
    columns[1].metric("Costi + imposte stimati", format_cents(operating_costs + estimated_tax))
    columns[2].metric("EBITDA stimato", format_cents(ebitda))
    columns[3].metric("Proiezione 30 giorni", format_cents(projection))
    st.caption(f"Risultato stimato dopo imposte: {format_cents(estimated_net)} · {observed_days} giorni osservati")

    chart_data = clean.groupby("Data", as_index=False)["Importo_centesimi"].sum()
    chart_data["Incasso (€)"] = chart_data["Importo_centesimi"] / 100
    st.subheader("Andamento incassi giornalieri")
    st.bar_chart(chart_data.set_index("Data")[["Incasso (€)"]], color="#4285F4")

    preview = clean.copy()
    preview["Data"] = preview["Data"].dt.strftime("%d/%m/%Y")
    preview["Importo"] = preview["Importo_centesimi"].map(format_cents)
    st.subheader("Dati minimizzati")
    st.dataframe(preview[["Data", "Importo"]], use_container_width=True, hide_index=True)


def render_dashboard(merchant: sqlite3.Row) -> None:
    expiry = date.fromisoformat(merchant["expiry_date"])
    st.header("Dashboard finanziaria")
    st.subheader(str(merchant["shop_name"]))
    st.caption(f"Licenza valida fino al {expiry.strftime('%d/%m/%Y')}")

    if expiry < local_today():
        st.error("Licenza scaduta. Contatta l'amministratore per il rinnovo.")
        return

    st.info(
        "Carica soltanto il report strettamente necessario. L'app conserva in memoria, per questa richiesta, "
        "solo le colonne Data e Importo che selezioni; non mostra né salva le altre colonne."
    )
    uploaded_file = st.file_uploader("Report POS", type=["csv", "xlsx"], help="Massimo 5 MB; CSV UTF-8 o XLSX.")
    if uploaded_file is None:
        return

    try:
        inspected = read_uploaded_frame(uploaded_file)
        column_names = list(inspected.columns)
        del inspected
    except UploadError as error:
        st.error(str(error))
        return
    except Exception:
        LOGGER.exception("Unexpected upload inspection error")
        st.error("Il file non può essere elaborato in sicurezza.")
        return

    st.caption("Seleziona esplicitamente le due colonne necessarie: l'app non indovina lo schema del file.")
    with st.form("pos_mapping", clear_on_submit=False):
        left, right = st.columns(2)
        with left:
            date_column = st.selectbox("Colonna data", column_names)
            date_format_label = st.selectbox(
                "Formato della data", ["GG/MM/AAAA", "AAAA-MM-GG", "MM/GG/AAAA"]
            )
        with right:
            amount_column = st.selectbox("Colonna importo", column_names, index=min(1, len(column_names) - 1))
            amount_locale_label = st.selectbox("Formato importo", ["Italiano: 1.234,56", "Internazionale: 1,234.56"])
        calculate = st.form_submit_button("Elabora dati minimizzati", use_container_width=True)

    if not calculate:
        return

    date_formats = {"GG/MM/AAAA": "%d/%m/%Y", "AAAA-MM-GG": "%Y-%m-%d", "MM/GG/AAAA": "%m/%d/%Y"}
    amount_locale = "it" if amount_locale_label.startswith("Italiano") else "international"
    try:
        clean = clean_pos_data(uploaded_file, date_column, amount_column, date_formats[date_format_label], amount_locale)
    except UploadError as error:
        st.error(str(error))
        return
    except Exception:
        LOGGER.exception("Unexpected POS cleaning error")
        st.error("Il file non può essere elaborato in sicurezza.")
        return

    cost_rate, tax_rate = st.columns(2)
    with cost_rate:
        operating_cost_rate = st.slider("Costi operativi stimati (%)", 0, 100, 28)
    with tax_rate:
        estimated_tax_rate = st.slider("Aliquota fiscale indicativa (%)", 0, 100, 22)
    render_financial_metrics(clean, operating_cost_rate, estimated_tax_rate)
    st.caption(
        "Le metriche sono indicative: EBITDA è calcolato prima delle imposte; "
        "la stima non sostituisce contabilità, dichiarazioni o consulenza fiscale professionale."
    )


def merchant_label(merchant: sqlite3.Row) -> str:
    return f"{merchant['display_name']} — {merchant['shop_name']}"


def render_admin(user: sqlite3.Row) -> None:
    st.header("Amministrazione")
    st.warning(
        "Le registrazioni di pagamento qui sotto sono manuali. In produzione devono provenire da un webhook "
        "verificato del provider di pagamento e da un processo di riconciliazione."
    )
    merchants = all_merchants()

    with st.expander("Crea commerciante e account", expanded=not merchants):
        with st.form("create_merchant", clear_on_submit=True):
            display_name = st.text_input("Nome commerciante")
            shop_name = st.text_input("Nome attività")
            username = st.text_input("Username commerciante")
            initial_password = st.text_input("Password iniziale (almeno 14 caratteri)", type="password")
            trial_days = st.number_input("Giorni di trial", min_value=1, max_value=365, value=14, step=1)
            create = st.form_submit_button("Crea account", use_container_width=True)
        if create:
            try:
                message = create_merchant_and_account(
                    int(user["id"]), display_name, shop_name, username, initial_password, int(trial_days)
                )
                st.success(message)
                st.rerun()
            except UserInputError as error:
                st.error(str(error))
            except Exception:
                LOGGER.exception("Unexpected merchant creation error")
                st.error("Non è stato possibile creare l'account.")

    if not merchants:
        st.info("Crea il primo commerciante per gestire trial e licenze.")
        return

    summary = pd.DataFrame(
        [
            {
                "Commerciante": merchant["display_name"],
                "Attività": merchant["shop_name"],
                "Scadenza": date.fromisoformat(merchant["expiry_date"]).strftime("%d/%m/%Y"),
                "Totale registrato": format_cents(merchant["total_paid_cents"]),
            }
            for merchant in merchants
        ]
    )
    st.subheader("Commercianti")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    ids = [int(merchant["id"]) for merchant in merchants]
    by_id = {int(merchant["id"]): merchant for merchant in merchants}
    selected_id = st.selectbox("Commerciante da amministrare", ids, format_func=lambda item: merchant_label(by_id[item]))
    selected = by_id[selected_id]

    action = st.radio("Azione", ["Registra rinnovo", "Sospendi licenza", "Storna ultima azione"], horizontal=True)
    if action == "Registra rinnovo":
        with st.form("license_extension", clear_on_submit=True):
            duration = st.selectbox("Durata", [7, 14, 30, 60, 90], index=2, format_func=lambda days: f"{days} giorni")
            amount = st.number_input("Importo già verificato (€)", min_value=0.01, max_value=1_000_000.00, value=35.00, step=0.01)
            reference = st.text_input("Riferimento univoco pagamento / ricevuta")
            reason = st.text_input("Nota amministrativa")
            confirm = st.checkbox("Confermo di avere verificato il pagamento fuori dall'app.")
            submit_extension = st.form_submit_button("Registra rinnovo", use_container_width=True)
        if submit_extension:
            if not confirm:
                st.error("Conferma la verifica del pagamento prima di procedere.")
            else:
                try:
                    message = record_license_extension(
                        int(user["id"]), selected_id, int(duration), money_to_cents(amount), reference, reason
                    )
                    st.success(message)
                    st.rerun()
                except UserInputError as error:
                    st.error(str(error))
                except Exception:
                    LOGGER.exception("Unexpected licence extension error")
                    st.error("Non è stato possibile registrare il rinnovo.")
    elif action == "Sospendi licenza":
        with st.form("license_kill", clear_on_submit=True):
            reason = st.text_area("Motivo del blocco", max_chars=300)
            confirm = st.checkbox(f"Confermo la sospensione di {merchant_label(selected)}.")
            submit_kill = st.form_submit_button("Sospendi licenza", type="secondary", use_container_width=True)
        if submit_kill:
            if not confirm:
                st.error("Conferma la sospensione prima di procedere.")
            else:
                try:
                    st.warning(kill_license(int(user["id"]), selected_id, reason))
                    st.rerun()
                except UserInputError as error:
                    st.error(str(error))
                except Exception:
                    LOGGER.exception("Unexpected licence suspension error")
                    st.error("Non è stato possibile sospendere la licenza.")
    else:
        with st.form("license_reversal", clear_on_submit=True):
            reason = st.text_area("Motivo dello storno", max_chars=300)
            confirm = st.checkbox("Confermo che lo storno non equivale a un rimborso bancario automatico.")
            submit_reversal = st.form_submit_button("Registra storno compensativo", use_container_width=True)
        if submit_reversal:
            if not confirm:
                st.error("Conferma l'operazione prima di procedere.")
            else:
                try:
                    st.success(reverse_latest_merchant_action(int(user["id"]), selected_id, reason))
                    st.rerun()
                except UserInputError as error:
                    st.error(str(error))
                except Exception:
                    LOGGER.exception("Unexpected reversal error")
                    st.error("Non è stato possibile registrare lo storno.")

    audit_events = recent_audit_events()
    if audit_events:
        audit = pd.DataFrame(
            [
                {
                    "Quando (UTC)": event["created_at"],
                    "Commerciante": event["display_name"],
                    "Azione": event["event_type"],
                    "Da": event["old_expiry_date"] or "—",
                    "A": event["new_expiry_date"] or "—",
                    "Totale precedente": format_cents(event["old_paid_cents"] or 0),
                    "Nuovo totale": format_cents(event["new_paid_cents"] or 0),
                    "Autore": event["actor"],
                    "Motivo": event["reason"],
                }
                for event in audit_events
            ]
        )
        st.subheader("Audit recente (immutabile)")
        st.dataframe(audit, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Thirtyfive | Financial BI", page_icon="📊", layout="wide")
    try:
        init_database()
    except DatabaseSetupError as error:
        st.error(str(error))
        st.stop()
    except Exception:
        LOGGER.exception("Database initialization failed")
        st.error("Non è stato possibile inizializzare l'archivio dati.")
        st.stop()

    configured, configuration_error = ensure_bootstrap_admin()
    if not configured:
        st.title("Configurazione richiesta")
        st.error(configuration_error or "Configurazione dell'amministratore non valida.")
        st.code("ADMIN_USERNAME=admin\nADMIN_PASSWORD_HASH=scrypt$...", language="text")
        st.caption("Genera l'hash con create_password_hash.py e salvalo nei secrets o nelle variabili d'ambiente del server.")
        st.stop()

    user = current_user()
    if user is None:
        login_screen()

    with st.sidebar:
        st.write(f"Accesso: **{user['username']}**")
        st.caption("Sessione con scadenza dopo 30 minuti di inattività.")
        if st.button("Esci", use_container_width=True):
            clear_authentication()
            st.rerun()

    st.title("Thirtyfive · Financial BI")
    if user["role"] == "merchant":
        merchant = merchant_for_user(user)
        if merchant is None:
            st.error("Account commerciante non associato correttamente. Contatta l'amministratore.")
            st.stop()
        render_dashboard(merchant)
    elif user["role"] == "admin":
        dashboard_tab, admin_tab = st.tabs(["Dashboard", "Amministrazione"])
        with dashboard_tab:
            merchants = all_merchants()
            if not merchants:
                st.info("Crea un commerciante dalla sezione Amministrazione.")
            else:
                ids = [int(merchant["id"]) for merchant in merchants]
                by_id = {int(merchant["id"]): merchant for merchant in merchants}
                selected_id = st.selectbox(
                    "Commerciante da consultare", ids, format_func=lambda item: merchant_label(by_id[item]), key="dashboard_merchant"
                )
                render_dashboard(by_id[selected_id])
        with admin_tab:
            render_admin(user)
    else:
        clear_authentication()
        st.error("Ruolo non riconosciuto.")


if __name__ == "__main__":
    main()
