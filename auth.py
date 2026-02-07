import streamlit as st
import requests

# === SUPABASE AUTH CONFIGURATION ===
SUPABASE_URL = st.secrets["supabase"]["url"]
SUPABASE_KEY = st.secrets["supabase"]["key"]


def init_auth_state():
    """Initialisiert Session State für Auth"""
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'user_email' not in st.session_state:
        st.session_state.user_email = None
    if 'user_role' not in st.session_state:
        st.session_state.user_role = None
    if 'access_token' not in st.session_state:
        st.session_state.access_token = None

def login(email: str, password: str) -> bool:
    """Authentifiziert User via Supabase"""
    try:
        response = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={
                "apikey": SUPABASE_KEY,
                "Content-Type": "application/json"
            },
            json={
                "email": email,
                "password": password
            }
        )

        if response.status_code == 200:
            data = response.json()
            st.session_state.authenticated = True
            st.session_state.user_email = data['user']['email']
            st.session_state.access_token = data['access_token']

            # Rolle aus user_metadata auslesen (Default: 'user')
            user_metadata = data['user'].get('user_metadata', {})
            st.session_state.user_role = user_metadata.get('role', 'user')

            return True
        else:
            return False
    except Exception as e:
        st.error(f"Verbindungsfehler: {e}")
        return False

def logout():
    """Loggt User aus"""
    st.session_state.authenticated = False
    st.session_state.user_email = None
    st.session_state.user_role = None
    st.session_state.access_token = None

def is_admin() -> bool:
    """Prüft ob der aktuelle User Admin-Rechte hat"""
    return st.session_state.get('user_role') == 'admin'

def show_login_page():
    """Zeigt Login-Seite"""
    st.title("🔐 PE Fund Analyzer")
    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.subheader("Anmeldung")

        with st.form("login_form"):
            email = st.text_input("E-Mail", placeholder="name@firma.com")
            password = st.text_input("Passwort", type="password")
            submit = st.form_submit_button("Anmelden", width='stretch')

            if submit:
                if email and password:
                    with st.spinner("Anmeldung läuft..."):
                        if login(email, password):
                            st.success("✅ Erfolgreich angemeldet!")
                            st.rerun()
                        else:
                            st.error("❌ Ungültige E-Mail oder Passwort")
                else:
                    st.warning("Bitte E-Mail und Passwort eingeben")

        st.markdown("---")
        st.caption("Kontaktiere den Administrator für Zugangsdaten.")
