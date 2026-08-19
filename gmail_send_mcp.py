"""
gmail_send_mcp.py

Servizio MCP dedicato all'invio, alla creazione di bozze e alla risposta
alle email, pensato per affiancare navbuildz/gmail-mcp-server (che copre
lettura, archiviazione, etichette e disiscrizione) senza toccarne il codice.

Ogni casella viene identificata dal proprio indirizzo email, la stessa
convenzione usata da navbuildz/gmail-mcp-server, cosi' Claude puo' riferirsi
alle caselle allo stesso modo con entrambi i connettori.

Le email includono automaticamente la firma ufficiale della casella
mittente, se configurata. La casella endolift@corsalis.ch ha tre varianti
di firma selezionabili con il parametro signature_variant ("logistics",
"accounting", "medical", che e' il valore predefinito).

Formattazione HTML: send_email, create_draft e reply_email accettano un
parametro opzionale html_body. Quando e' presente, il messaggio viene
costruito in multipart/alternative: la versione testuale (body + firma
testuale) resta come fallback, la versione HTML (html_body + firma HTML)
diventa la parte principale. La parte HTML viene sempre avvolta dal
connettore in uno stile uniforme (famiglia di carattere, dimensione,
colore, sfondo), definito in STYLE_DEFAULT/STYLE_OVERRIDES piu' sotto:
chi scrive il messaggio non deve occuparsi dello stile, e una eventuale
differenziazione per casella si aggiunge li', non nel corpo delle
funzioni. Se html_body non viene fornito, il comportamento resta quello
di sempre: solo testo semplice, con firma testuale appesa in fondo.

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
    TOKENS_FILE            Percorso del file token su un volume persistente,
                            es. /data/tokens.json
    TOKENS_DATA            Opzionale: contenuto JSON dei token, usato solo
                            come innesco iniziale se TOKENS_FILE e' vuoto
"""

import base64
import contextlib
import json
import os
import secrets
from email.mime.multipart import MIMEMultipart
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
from mcp.server.transport_security import TransportSecuritySettings

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
    if TOKENS_FILE.exists():
        content = TOKENS_FILE.read_text().strip()
        if content:
            return json.loads(content)
    raw = os.environ.get("TOKENS_DATA")
    if raw:
        tokens = json.loads(raw)
        _save_tokens(tokens)  # migra subito il contenuto sul volume persistente
        return tokens
    return {}


def _save_tokens(tokens: dict) -> None:
    TOKENS_FILE.write_text(json.dumps(tokens))


_tokens: dict = _load_tokens()  # indirizzo email -> credenziali cifrate (Fernet)
_pending_pkce: dict = {}  # state OAuth -> code_verifier, tra /connect e /oauth/callback


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
# Firme ufficiali per casella, in due versioni parallele:
#   SIGNATURES_TEXT  alimenta la parte testuale semplice / il fallback
#   SIGNATURES_HTML  alimenta la parte HTML, appesa dopo html_body
# Le due versioni vanno tenute sincronizzate a mano quando cambia una firma.
# Foto, loghi e icone social non sono inclusi perche' non e' disponibile
# il file immagine, solo lo screenshot della firma gia' composta.
# ---------------------------------------------------------------------------

_CORSALIS_DISCLAIMER_TEXT = (
    "The content of this email is confidential and intended for the recipient "
    "specified in message only. It is strictly forbidden to share any part of "
    "this message with any third party, without a written consent of the "
    "sender. If you received this message by mistake, please reply to this "
    "message and follow with its deletion, so that we can ensure such a "
    "mistake does not occur in the future."
)

_CORSALIS_DISCLAIMER_HTML = _CORSALIS_DISCLAIMER_TEXT

SIGNATURES_TEXT = {
    "am.forte@almaval.ch": (
        "Cordialement,\n\n"
        "Dr Alberto M. Forte\n"
        "Directeur médical\n"
        "Psychiatre & psychothérapeute\n\n"
        "am.forte@almaval.ch - am.forte@hin.ch\n"
        "Secrétariat : +41 21 525 35 14\n"
        "Secrétariat (mobile, aussi WhatsApp) : +41 76 702 78 69\n"
        "Ligne directe (aussi WhatsApp) : +41 76 457 72 75\n"
        "Castel de Bois Genoud, 1023 Crissier\n"
        "almaval.ch"
    ),
    "forte.albertomaria@gmail.com": (
        "Cordialement,\n\n"
        "Dr Alberto M. Forte\n"
        "+41 76 615 03 88\n"
        "Ch. du miroir 32, CH-1090 La Croix sur Lutry"
    ),
    "info@corsalis.ch": (
        "Bests,\n\n"
        "Dr Alberto M. Forte\n"
        "CEO\n"
        "Medical director\n"
        "Corsalis\n\n"
        "info@corsalis.ch\n"
        "+41 76 469 1986 (only WhatsApp)\n"
        "Ch. du miroir 32, CH-1090 La Croix\n"
        "corsalis.ch\n\n"
        + _CORSALIS_DISCLAIMER_TEXT
    ),
}

_ALMAVAL_LOGO_URL = "https://almaval.ch/wp-content/uploads/2023/10/cropped-AVL_Logo11.png"

SIGNATURES_HTML = {
    "am.forte@almaval.ch": (
        "Cordialement,<br><br>"
        "Dr Alberto M. Forte<br>"
        "Directeur médical<br>"
        "Psychiatre &amp; psychothérapeute<br><br>"
        f'<img src="{_ALMAVAL_LOGO_URL}" alt="Almaval" width="140" '
        'style="display:block;margin-bottom:12px;border:0;"><br>'
        "am.forte@almaval.ch - am.forte@hin.ch<br>"
        "Secrétariat : +41 21 525 35 14<br>"
        "Secrétariat (mobile, aussi WhatsApp) : +41 76 702 78 69<br>"
        "Ligne directe (aussi WhatsApp) : +41 76 457 72 75<br>"
        "Castel de Bois Genoud, 1023 Crissier<br>"
        "almaval.ch"
    ),
    "forte.albertomaria@gmail.com": (
        "Cordialement,<br><br>"
        "Dr Alberto M. Forte<br>"
        "+41 76 615 03 88<br>"
        "Ch. du miroir 32, CH-1090 La Croix sur Lutry"
    ),
    "info@corsalis.ch": (
        "Bests,<br><br>"
        "Dr Alberto M. Forte<br>"
        "CEO<br>"
        "Medical director<br>"
        "Corsalis<br><br>"
        "info@corsalis.ch<br>"
        "+41 76 469 1986 (only WhatsApp)<br>"
        "Ch. du miroir 32, CH-1090 La Croix<br>"
        "corsalis.ch<br><br>"
        + _CORSALIS_DISCLAIMER_HTML
    ),
}

_ENDOLIFT_ROLE_LINES_TEXT = {
    "logistics": "Logistics Department",
    "accounting": "Accounting Department",
    "medical": "Dr Alberto M. Forte\nMedical director",
}

_ENDOLIFT_ROLE_LINES_HTML = {
    "logistics": "Logistics Department",
    "accounting": "Accounting Department",
    "medical": "Dr Alberto M. Forte<br>Medical director",
}


def _endolift_signature_text(variant: str) -> str:
    role = _ENDOLIFT_ROLE_LINES_TEXT.get(variant, _ENDOLIFT_ROLE_LINES_TEXT["medical"])
    return (
        "Bests,\n\n"
        f"{role}\n"
        "Official Eufoton Distributor in Switzerland\n"
        "Granted by Corsalis\n\n"
        "endolift@corsalis.ch\n"
        "+41 76 469 1986 (only WhatsApp)\n"
        "Scientific committee: +41 79 108 01 24\n"
        "Legal counsel Italy & Switzerland: +39 348 491 2171\n"
        "Ch. du miroir 32, CH-1090 La Croix\n"
        "corsalis.ch\n\n"
        + _CORSALIS_DISCLAIMER_TEXT
    )


def _endolift_signature_html(variant: str) -> str:
    role = _ENDOLIFT_ROLE_LINES_HTML.get(variant, _ENDOLIFT_ROLE_LINES_HTML["medical"])
    return (
        "Bests,<br><br>"
        f"{role}<br>"
        "Official Eufoton Distributor in Switzerland<br>"
        "Granted by Corsalis<br><br>"
        "endolift@corsalis.ch<br>"
        "+41 76 469 1986 (only WhatsApp)<br>"
        "Scientific committee: +41 79 108 01 24<br>"
        "Legal counsel Italy &amp; Switzerland: +39 348 491 2171<br>"
        "Ch. du miroir 32, CH-1090 La Croix<br>"
        "corsalis.ch<br><br>"
        + _CORSALIS_DISCLAIMER_HTML
    )


def _get_signature_text(account: str, signature_variant: str = None) -> str:
    if account == "endolift@corsalis.ch":
        return _endolift_signature_text(signature_variant or "medical")
    return SIGNATURES_TEXT.get(account, "")


def _get_signature_html(account: str, signature_variant: str = None) -> str:
    if account == "endolift@corsalis.ch":
        return _endolift_signature_html(signature_variant or "medical")
    return SIGNATURES_HTML.get(account, "")


# ---------------------------------------------------------------------------
# Stile applicato dal connettore alla parte HTML dei messaggi. E' una
# configurazione, non una costante sparsa nel codice: STYLE_DEFAULT vale
# per tutte le caselle, STYLE_OVERRIDES permette di differenziare una
# singola casella il giorno che servisse, senza toccare nient'altro.
# ---------------------------------------------------------------------------

STYLE_DEFAULT: dict = {
    "font_family": "Verdana, Geneva, sans-serif",
    "font_size": "10px",
    "color": "#666666",
    "background_color": "#ffffff",
}

STYLE_OVERRIDES: dict[str, dict] = {
    # Esempio per differenziare una casella in futuro, senza toccare il resto:
    # "am.forte@almaval.ch": {"color": "#444444"},
}


def _get_style(account: str) -> dict:
    style = dict(STYLE_DEFAULT)
    style.update(STYLE_OVERRIDES.get(account, {}))
    return style


def _wrap_html(inner_html: str, account: str) -> str:
    style = _get_style(account)
    style_attr = (
        f"font-family:{style['font_family']};"
        f"font-size:{style['font_size']};"
        f"color:{style['color']};"
        f"background-color:{style['background_color']};"
    )
    return f'<div style="{style_attr}">{inner_html}</div>'


# ---------------------------------------------------------------------------
# Strumenti MCP
# ---------------------------------------------------------------------------

_server_host = SERVER_URL.split("://", 1)[-1]

mcp = FastMCP(
    "Gmail Send",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", _server_host],
        allowed_origins=[SERVER_URL],
    ),
)


def _build_mime(
    to: str,
    subject: str,
    body: str,
    account: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    signature_variant: Optional[str] = None,
    include_signature: bool = True,
    html_body: Optional[str] = None,
) -> str:
    text_signature = _get_signature_text(account, signature_variant) if include_signature else ""
    full_text_body = body
    if text_signature:
        full_text_body = f"{body}\n\n{text_signature}"

    if html_body is not None:
        html_signature = _get_signature_html(account, signature_variant) if include_signature else ""
        inner_html = html_body
        if html_signature:
            inner_html = f"{html_body}<br><br>{html_signature}"
        wrapped_html = _wrap_html(inner_html, account)

        message = MIMEMultipart("alternative")
        message.attach(MIMEText(full_text_body, "plain"))
        message.attach(MIMEText(wrapped_html, "html"))
    else:
        message = MIMEText(full_text_body)

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
    signature_variant: Optional[str] = None,
    html_body: Optional[str] = None,
) -> dict:
    """
    Invia una nuova email da una delle caselle collegate. La firma ufficiale
    della casella viene aggiunta automaticamente, se configurata.

    account: indirizzo della casella mittente (vedi list_accounts)
    to, cc, bcc: indirizzi destinatari, separati da virgola se piu' di uno
    signature_variant: solo per endolift@corsalis.ch, una tra
        "logistics", "accounting", "medical" (default "medical")
    html_body: se fornito, il messaggio viene inviato in multipart/alternative
        con body come fallback testuale e html_body come parte HTML
        principale. La parte HTML viene avvolta automaticamente nello
        stile configurato per la casella (vedi STYLE_DEFAULT/STYLE_OVERRIDES),
        non serve includere stile nel testo passato qui.
    """
    service = _gmail_service(account)
    raw = _build_mime(
        to, subject, body, account, cc, bcc,
        signature_variant=signature_variant, html_body=html_body,
    )
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"id": sent["id"], "threadId": sent["threadId"], "stato": "inviata"}


@mcp.tool()
def create_draft(
    account: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    signature_variant: Optional[str] = None,
    html_body: Optional[str] = None,
) -> dict:
    """
    Crea una bozza in una delle caselle collegate, senza inviarla. La firma
    ufficiale della casella viene aggiunta automaticamente, se configurata.

    signature_variant: solo per endolift@corsalis.ch, una tra
        "logistics", "accounting", "medical" (default "medical")
    html_body: se fornito, la bozza viene creata in multipart/alternative
        con body come fallback testuale e html_body come parte HTML
        principale, avvolta automaticamente nello stile configurato per
        la casella.
    """
    service = _gmail_service(account)
    raw = _build_mime(
        to, subject, body, account, cc,
        signature_variant=signature_variant, html_body=html_body,
    )
    draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return {"id": draft["id"], "stato": "bozza creata"}


@mcp.tool()
def reply_email(
    account: str,
    message_id: str,
    body: str,
    reply_all: bool = False,
    signature_variant: Optional[str] = None,
    html_body: Optional[str] = None,
) -> dict:
    """
    Risponde a un'email esistente restando nello stesso thread. La firma
    ufficiale della casella viene aggiunta automaticamente, se configurata.

    account: la casella da cui rispondere
    message_id: l'id del messaggio Gmail a cui rispondere, lo stesso restituito
        dagli strumenti di lettura del connettore Gmail principale (navbuildz)
    body: testo della risposta
    reply_all: se True risponde anche a tutti i destinatari originali,
        non solo al mittente
    signature_variant: solo per endolift@corsalis.ch, una tra
        "logistics", "accounting", "medical" (default "medical")
    html_body: se fornito, la risposta viene inviata in multipart/alternative
        con lo stesso involucro HTML usato per i nuovi messaggi, cosi' il
        thread resta coerente invece di alternare messaggi formattati e
        messaggi in solo testo.
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
        account=account,
        cc=cc,
        in_reply_to=headers.get("Message-ID"),
        references=references,
        signature_variant=signature_variant,
        html_body=html_body,
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
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    if flow.code_verifier:
        _pending_pkce[state] = flow.code_verifier
    return RedirectResponse(auth_url)


@app.get("/oauth/callback")
def oauth_callback(request: Request):
    state = request.query_params.get("state")
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state,
        code_verifier=_pending_pkce.pop(state, None),
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


app.mount("/", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 3001)),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
