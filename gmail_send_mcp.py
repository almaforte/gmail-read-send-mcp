"""
gmail_send_mcp.py

Servizio MCP dedicato all'invio, alla creazione di bozze e alla risposta
alle email, pensato per affiancare navbuildz/gmail-mcp-server (che copre
lettura, archiviazione, etichette e disiscrizione) senza toccarne il codice.

Ogni casella viene identificata dal proprio indirizzo email, la stessa
convenzione usata da navbuildz/gmail-mcp-server, cosi' Claude puo' riferirsi
alle caselle allo stesso modo con entrambi i connettori.

Pagina di gestione account su /setup, protetta da ADMIN_PASSWORD via HTTP
Basic Auth. Endpoint MCP su /mcp, da collegare a Claude come SECONDO
connettore personalizzato (oltre a quello di navbuildz).

Variabili d'ambiente richieste:
    GOOGLE_CLIENT_ID       ID client OAuth (puo' essere lo stesso di navbuildz)
    GOOGLE_CLIENT_SECRET   Segreto client OAuth (idem)
    SERVER_URL             URL pubblico di QUESTO servizio, es. https://xxx.railway.app
    ADMIN_PASSWORD         Password per accedere a /setup
    FERNET_KEY             Chiave di cifratura per i token, generata con:
                            python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    PORT                   Porta di ascolto (default 3001)
    TOKENS_DATA            Opzionale: contenuto JSON dei token, per persistere
                            le connessioni tra un redeploy e l'altro su Railway
"""

import base64
import contextlib
import json
import os
import secrets
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
SERVER_URL = os.environ["SERVER_URL"].rstrip("/")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
FERNET_KEY = os.environ["FERNET_KEY"]

REDIRECT_URI = f"{SERVER_URL}/oauth/callback"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

CLIENT_CONFIG = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI],
    }
}

TOKENS_FILE = Path(os.environ.get("TOKENS_FILE", "./tokens.json"))
fernet = Fernet(FERNET_KEY.encode())


def _load_tokens() -> dict:
    raw = os.environ.get("TOKENS_DATA")
    if raw:
        return json.loads(raw)
    if TOKENS_FILE.exists():
        return json.loads(TOKENS_FILE.read_text())
    return {}


def _save_tokens(tokens: dict) -> None:
    TOKENS_FILE.write_text(json.dumps(tokens))


_tokens: dict = _load_tokens()  # indirizzo email -> credenziali cifrate (Fernet)


def _store_credentials(email: str, creds: Credentials) -> None:
    encrypted = fernet.encrypt(creds.to_json().encode()).decode()
    _tokens[email] = encrypted
    _save_tokens(_tokens)


def _get_credentials(email: str) -> Credentials:
    if email not in _tokens:
        raise ValueError(f"Casella '{email}' non collegata. Collegala su {SERVER_URL}/setup")
    decrypted = fernet.decrypt(_tokens[email].encode()).decode()
    creds = Credentials.from_authorized_user_info(json.loads(decrypted), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        _store_credentials(email, creds)
    return creds


def _gmail_service(email: str):
    return build("gmail", "v1", credentials=_get_credentials(email))


# ---------------------------------------------------------------------------
# Strumenti MCP
# ---------------------------------------------------------------------------

mcp = FastMCP("Gmail Send", stateless_http=True)


def _build_mime(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> str:
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc
    if bcc:
        message["bcc"] = bcc
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = references or in_reply_to
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


@mcp.tool()
def list_accounts() -> list[str]:
    """Elenca le caselle collegate a questo connettore di invio."""
    return sorted(_tokens)


@mcp.tool()
def send_email(
    account: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
) -> dict:
    """
    Invia una nuova email da una delle caselle collegate.

    account: indirizzo della casella mittente (vedi list_accounts)
    to, cc, bcc: indirizzi destinatari, separati da virgola se piu' di uno
    """
    service = _gmail_service(account)
    raw = _build_mime(to, subject, body, cc, bcc)
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"id": sent["id"], "threadId": sent["threadId"], "stato": "inviata"}


@mcp.tool()
def create_draft(
    account: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
) -> dict:
    """Crea una bozza in una delle caselle collegate, senza inviarla."""
    service = _gmail_service(account)
    raw = _build_mime(to, subject, body, cc)
    draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return {"id": draft["id"], "stato": "bozza creata"}


@mcp.tool()
def reply_email(account: str, message_id: str, body: str, reply_all: bool = False) -> dict:
    """
    Risponde a un'email esistente restando nello stesso thread.

    account: la casella da cui rispondere
    message_id: l'id del messaggio Gmail a cui rispondere, lo stesso restituito
        dagli strumenti di lettura del connettore Gmail principale (navbuildz)
    body: testo della risposta
    reply_all: se True risponde anche a tutti i destinatari originali,
        non solo al mittente
    """
    service = _gmail_service(account)
    original = service.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["From", "To", "Cc", "Subject", "Message-ID", "References"],
    ).execute()
    headers = {h["name"]: h["value"] for h in original["payload"]["headers"]}

    subject = headers.get("Subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    to = headers.get("From", "")
    cc = None
    if reply_all:
        existing = ", ".join(filter(None, [headers.get("To", ""), headers.get("Cc", "")]))
        cc = existing or None

    references = " ".join(filter(None, [headers.get("References", ""), headers.get("Message-ID", "")]))

    raw = _build_mime(
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        in_reply_to=headers.get("Message-ID"),
        references=references,
    )
    sent = service.users().messages().send(
        userId="me", body={"raw": raw, "threadId": original["threadId"]}
    ).execute()
    return {"id": sent["id"], "threadId": sent["threadId"], "stato": "risposta inviata"}


# ---------------------------------------------------------------------------
# App FastAPI: pagina di setup e callback OAuth, montata insieme a MCP
# ---------------------------------------------------------------------------

security = HTTPBasic()


def _check_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    if not secrets.compare_digest(credentials.password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Password non valida", headers={"WWW-Authenticate": "Basic"})


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(lifespan=lifespan)


@app.get("/setup", response_class=HTMLResponse)
def setup_page(_: None = Depends(_check_admin)):
    accounts = "".join(f"<li>{email}</li>" for email in sorted(_tokens)) or "<li>nessuna casella collegata</li>"
    return f"""
    <html><body style="font-family: sans-serif; max-width: 640px; margin: 40px auto;">
    <h2>Gmail Send MCP &middot; caselle collegate</h2>
    <ul>{accounts}</ul>
    <p><a href="/connect">+ Collega una nuova casella</a></p>
    <hr>
    <p>Per persistere le connessioni tra un redeploy e l'altro su Railway,
    copia questo valore nella variabile d'ambiente <code>TOKENS_DATA</code>:</p>
    <textarea style="width:100%; height:120px;" readonly>{json.dumps(_tokens)}</textarea>
    </body></html>
    """


@app.get("/connect")
def connect(_: None = Depends(_check_admin)):
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _state = flow.authorization_url(access_type="offline", prompt="consent")
    return RedirectResponse(auth_url)


@app.get("/oauth/callback")
def oauth_callback(request: Request):
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=request.query_params.get("state"),
    )
    # Railway (come la maggior parte dei PaaS) termina https sul proprio proxy e inoltra
    # al servizio in http semplice. Senza questa correzione, request.url risulta in http://
    # e la libreria Google rifiuta di completare lo scambio del codice di autorizzazione.
    authorization_response = str(request.url).replace("http://", "https://", 1)
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials

    oauth2 = build("oauth2", "v2", credentials=creds)
    email = oauth2.userinfo().get().execute()["email"]

    _store_credentials(email, creds)
    return RedirectResponse("/setup")


app.mount("/mcp", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 3001)),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
