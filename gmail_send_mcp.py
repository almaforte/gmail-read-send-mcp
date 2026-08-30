"""
gmail_send_mcp.py

Servizio MCP dedicato all'invio, alla creazione di bozze e alla risposta
alle email.

Ogni casella viene identificata dal proprio indirizzo email.

Le email includono automaticamente la firma ufficiale della casella
mittente, se configurata. La casella endolift@corsalis.ch ha tre varianti
di firma selezionabili con il parametro signature_variant ("logistics",
"accounting", "medical", che e' il valore predefinito).

Formattazione HTML: send_email, create_draft e reply_email richiedono un
parametro html_body. Il messaggio viene costruito in multipart/alternative:
la versione testuale (body + firma testuale) resta come fallback, la
versione HTML (html_body + firma HTML) e' la parte principale. html_body
e' obbligatorio: non e' piu' possibile inviare o mettere in bozza un
messaggio in solo testo semplice. Questo vincolo e' intenzionale (vedi
Alberto, 30.08.2026): un client che dimentica html_body deve ricevere un
errore esplicito al momento della chiamata, non produrre silenziosamente
una mail poco curata.

Chiusura manuale duplicata: se chi scrive il messaggio include gia' una
formula di chiusura scritta a mano (es. "Cordialement, Alberto") mentre
l'account ha una firma ufficiale configurata, quella chiusura verrebbe
seguita dalla firma vera, producendo un doppione visibile nel messaggio
finale. _strip_manual_closing rileva ed elimina automaticamente questi
pattern, sia nel testo semplice sia nell'HTML, prima di appendere la
firma. La correzione e' automatica e silenziosa (nessun errore bloccante):
l'obiettivo e' che il messaggio finale sia sempre corretto, non che chi
scrive debba correggersi a mano ogni volta.

Lo stile della parte HTML e' imposto dal connettore, non da chi scrive il
messaggio: famiglia di carattere, dimensione e colore sono definiti in
STYLE_DEFAULT/STYLE_OVERRIDES e vengono applicati tag per tag, sia al
corpo sia alla firma, che dal punto di vista del messaggio e' solo la
coda del corpo. L'applicazione tag per tag e' necessaria perche' Gmail
trasmette il carattere di un div contenitore ai paragrafi ma lo perde
sugli elenchi puntati e lo ignora nelle celle di tabella.

Pagina di gestione account su /setup, protetta da ADMIN_PASSWORD via HTTP
Basic Auth. Endpoint MCP su /mcp.

Variabili d'ambiente richieste:
    GOOGLE_CLIENT_ID       ID client OAuth
    GOOGLE_CLIENT_SECRET   Segreto client OAuth
    SERVER_URL             URL pubblico di QUESTO servizio, es. https://xxx.railway.app
    ADMIN_PASSWORD         Password per accedere a /setup
    FERNET_KEY             Chiave di cifratura per i token, generata con:
                            python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    PORT                   Porta di ascolto (default 3001)
    TOKENS_FILE            Percorso del file token su un volume persistente,
                            es. /data/tokens.json

Variabili d'ambiente facoltative:
    TOKENS_DATA            Contenuto JSON dei token, usato solo come innesco
                            iniziale se TOKENS_FILE e' vuoto
    CORSALIS_LOGO_B64      Logo Corsalis in base64. Di norma non si usa: la
                            stringa e' troppo lunga per il limite di Railway
                            e vive nel file corsalis_logo.b64 del repository.
"""

import base64
import contextlib
import json
import os
import re
import secrets
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.text import MIMEText
from email.utils import getaddresses, formataddr
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
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
# Le firme HTML non contengono piu' indicazioni di carattere, dimensione o
# colore: lo stile arriva da STYLE_DEFAULT, unica fonte di verita'.
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


def _load_corsalis_logo() -> str:
    """
    Il logo Corsalis e' un asset privato, non pubblicato online, quindi
    viene allegato in linea al messaggio (Content-ID) invece che
    referenziato via URL come quello Almaval.

    La stringa base64 vive fuori dal codice: prima si guarda la variabile
    d'ambiente CORSALIS_LOGO_B64, poi il file corsalis_logo.b64 accanto a
    questo sorgente. Railway limita le variabili a 32'768 caratteri, quindi
    per un PNG di queste dimensioni la via praticabile e' il file.
    """
    raw = os.environ.get("CORSALIS_LOGO_B64", "").strip()
    if raw:
        return raw
    logo_file = Path(__file__).parent / "corsalis_logo.b64"
    if logo_file.exists():
        return logo_file.read_text().strip()
    return ""


_CORSALIS_LOGO_B64 = _load_corsalis_logo()

_INLINE_LOGOS = {
    "corsalis_logo": _CORSALIS_LOGO_B64,
}

# Per ogni casella che deve avere un logo allegato in linea nella firma HTML,
# il cid corrispondente in _INLINE_LOGOS.
_ACCOUNT_LOGO_CID = {
    "info@corsalis.ch": "corsalis_logo",
    "endolift@corsalis.ch": "corsalis_logo",
}

SIGNATURES_HTML = {
    "am.forte@almaval.ch": (
        "Cordialement,<br><br>"
        "Dr Alberto M. Forte<br>"
        "Directeur médical<br>"
        "Psychiatre &amp; psychothérapeute<br><br>"
        f'<img src="{_ALMAVAL_LOGO_URL}" alt="Almaval" width="140" '
        'style="display:block;margin-bottom:12px;border:0;">'
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
        '<img src="cid:corsalis_logo" alt="Corsalis" width="140" '
        'style="display:block;margin-bottom:12px;border:0;">'
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
        '<img src="cid:corsalis_logo" alt="Corsalis" width="140" '
        'style="display:block;margin-bottom:12px;border:0;">'
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


def _has_signature_configured(account: str) -> bool:
    return account == "endolift@corsalis.ch" or account in SIGNATURES_TEXT or account in SIGNATURES_HTML


# ---------------------------------------------------------------------------
# Rimozione automatica di una chiusura scritta a mano ("Cordialement,
# Alberto", "Bien cordialement, Dr Forte...", "Bests, ...") quando la
# casella ha gia' una firma ufficiale configurata. Senza questo controllo,
# la chiusura manuale finisce seguita dalla firma vera, producendo un
# doppione visibile nel messaggio (caso ricorrente del 30.08.2026).
#
# L'euristica taglia tutto cio' che segue una formula di chiusura nota,
# purche' compaia nell'ultimo quarto del messaggio (una chiusura reale sta
# in fondo, non all'inizio o nel mezzo di un paragrafo che la cita per
# altri motivi). Non e' infallibile, ma copre le formule effettivamente in
# uso su queste caselle e i loro equivalenti piu' comuni in francese,
# italiano e inglese.
# ---------------------------------------------------------------------------

_CLOSING_PHRASES = [
    "bien cordialement",
    "cordialement",
    "bests",
    "best regards",
    "kind regards",
    "regards",
    "cordiali saluti",
    "distinti saluti",
    "un cordiale saluto",
]

# Una riga di chiusura: la formula (con virgola o punto opzionali), poi il
# resto del messaggio fino alla fine. re.IGNORECASE per non dipendere dalla
# maiuscola iniziale, re.DOTALL perche' il "resto" attraversa piu' righe.
_CLOSING_PATTERN_TEXT = re.compile(
    r"(?im)^\s*(" + "|".join(_CLOSING_PHRASES) + r")\s*[,.:]?\s*\n.*\Z",
    re.DOTALL,
)

# Versione HTML: stessa logica ma la formula puo' comparire dentro un tag
# (<p>Cordialement,</p> o simile), quindi si cerca nel testo visibile di
# ogni blocco verso la fine del frammento, non riga per riga.
_CLOSING_PATTERN_HTML_BLOCK = re.compile(
    r"(?is)<(p|div)[^>]*>\s*(" + "|".join(_CLOSING_PHRASES) + r")\s*[,.:]?\s*(<br\s*/?>)?\s*</\1>",
)


def _strip_manual_closing_text(body: str) -> str:
    """
    Toglie una formula di chiusura scritta a mano e tutto cio' che la
    segue, solo se compare nell'ultimo quarto del testo (per non tagliare
    per errore un paragrafo che cita "cordialement" a meta' messaggio).
    """
    if not body:
        return body
    match = _CLOSING_PATTERN_TEXT.search(body)
    if not match:
        return body
    if match.start() < len(body) * 0.75:
        return body
    return body[: match.start()].rstrip()


def _strip_manual_closing_html(html_body: str) -> str:
    """
    Equivalente HTML: rimuove il primo blocco (<p>/<div>) che contiene solo
    una formula di chiusura nota, insieme a tutto cio' che lo segue nel
    frammento, sempre limitato all'ultimo quarto del contenuto.
    """
    if not html_body:
        return html_body
    match = _CLOSING_PATTERN_HTML_BLOCK.search(html_body)
    if not match:
        return html_body
    if match.start() < len(html_body) * 0.75:
        return html_body
    return html_body[: match.start()].rstrip()


def _strip_manual_closing(account: str, body: str, html_body: str) -> tuple[str, str]:
    """
    Applica la pulizia della chiusura manuale a body e html_body, solo per
    gli account che hanno una firma ufficiale configurata: su una casella
    senza firma gestita non c'e' rischio di doppione, quindi il testo non
    viene toccato.
    """
    if not _has_signature_configured(account):
        return body, html_body
    return _strip_manual_closing_text(body), _strip_manual_closing_html(html_body)


# ---------------------------------------------------------------------------
# Stile applicato dal connettore alla parte HTML dei messaggi. E' una
# configurazione, non una costante sparsa nel codice: STYLE_DEFAULT vale
# per tutte le caselle, STYLE_OVERRIDES permette di differenziare una
# singola casella il giorno che servisse, senza toccare nient'altro.
#
# Lo stile viene applicato due volte: una sul div contenitore, per i
# frammenti di testo nudo, e una tag per tag su tutto cio' che ha una
# struttura. Il secondo passaggio non e' ridondante: Gmail trasmette il
# carattere del contenitore ai paragrafi, lo perde sugli <li> e lo ignora
# nelle celle di tabella. Senza di esso un elenco puntato o una tabella di
# importi esce in Arial nero in mezzo a un messaggio in Verdana grigio.
# ---------------------------------------------------------------------------

STYLE_DEFAULT: dict = {
    "font_family": "Verdana, Geneva, sans-serif",
    "font_size": "10px",
    "color": "#666666",
}

STYLE_OVERRIDES: dict[str, dict] = {
    # Esempio per differenziare una casella in futuro, senza toccare il resto:
    # "am.forte@almaval.ch": {"color": "#444444"},
}

# Tag ai quali lo stile viene imposto uno per uno. Le immagini sono escluse
# di proposito. I link ricevono carattere e dimensione ma non il colore,
# cosi' restano blu e riconoscibili come link.
_STYLED_TAGS = [
    "p", "div", "span", "li", "ul", "ol",
    "table", "thead", "tbody", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "b", "strong", "em", "i", "u", "small", "pre", "code",
]


def _get_style(account: str) -> dict:
    style = dict(STYLE_DEFAULT)
    style.update(STYLE_OVERRIDES.get(account, {}))
    return style


def _style_attr(style: dict) -> str:
    return (
        f"font-family:{style['font_family']};"
        f"font-size:{style['font_size']};"
        f"color:{style['color']};"
    )


def _link_attr(style: dict) -> str:
    return (
        f"font-family:{style['font_family']};"
        f"font-size:{style['font_size']};"
    )


def _inline_style(fragment: str, style: dict) -> str:
    """
    Impone lo stile della casella a ogni tag di blocco del frammento HTML.
    Lo stile del connettore viene messo PRIMA di un eventuale stile gia'
    presente sul tag: in CSS in linea vince l'ultima dichiarazione, quindi
    un'eccezione scritta a mano nel messaggio resta prioritaria.
    """
    soup = BeautifulSoup(fragment, "html.parser")
    attr = _style_attr(style)
    for tag in soup.find_all(_STYLED_TAGS):
        existing = tag.get("style", "")
        tag["style"] = attr + existing
    link_attr = _link_attr(style)
    for tag in soup.find_all("a"):
        existing = tag.get("style", "")
        tag["style"] = link_attr + existing
    return str(soup)


def _wrap_html(inner_html: str, account: str) -> str:
    style = _get_style(account)
    return f'<div style="{_style_attr(style)}">{_inline_style(inner_html, style)}</div>'


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
    html_body: str = "",
) -> str:
    if not html_body:
        raise ValueError(
            "html_body e' obbligatorio: fornisci il corpo del messaggio in HTML "
            "(paragrafi <p>, eventuali <strong>/<ol>/<ul>), non solo in testo semplice."
        )

    body, html_body = _strip_manual_closing(account, body, html_body)

    # Una sola riga vuota separa il corpo dalla firma: le firme in
    # SIGNATURES_TEXT/SIGNATURES_HTML iniziano gia' con il proprio "\n\n" /
    # "<br><br>" prima della formula di chiusura ("Cordialement,\n\n..."),
    # quindi qui si usa un solo "\n" / "<br>" di separazione, non due. Sommare
    # un "\n\n"/"<br><br>" qui a quello gia' presente in testa alla firma
    # produceva una doppia riga vuota prima di "Cordialement," (26.08.2026 +
    # ricomparso 30.08.2026 su una firma con logo in mezzo).
    text_signature = _get_signature_text(account, signature_variant) if include_signature else ""
    full_text_body = body
    if text_signature:
        full_text_body = f"{body}\n{text_signature}"

    html_signature = _get_signature_html(account, signature_variant) if include_signature else ""
    inner_html = html_body
    if html_signature:
        inner_html = f"{html_body}<br>{html_signature}"
    wrapped_html = _wrap_html(inner_html, account)

    alt_part = MIMEMultipart("alternative")
    alt_part.attach(MIMEText(full_text_body, "plain"))
    alt_part.attach(MIMEText(wrapped_html, "html"))

    logo_cid = _ACCOUNT_LOGO_CID.get(account) if include_signature else None
    # Se il logo non e' disponibile, la firma esce senza immagine invece
    # di far fallire l'invio.
    if logo_cid and _INLINE_LOGOS.get(logo_cid):
        message = MIMEMultipart("related")
        message.attach(alt_part)
        image_bytes = base64.b64decode(_INLINE_LOGOS[logo_cid])
        image_part = MIMEImage(image_bytes, _subtype="png")
        image_part.add_header("Content-ID", f"<{logo_cid}>")
        image_part.add_header("Content-Disposition", "inline", filename=f"{logo_cid}.png")
        message.attach(image_part)
    else:
        message = alt_part

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


def _merge_recipients(*header_values: str, exclude: set) -> Optional[str]:
    """
    Unisce piu' header di destinatari in un solo valore, togliendo i
    duplicati e gli indirizzi in 'exclude', tipicamente la casella mittente
    stessa e il destinatario principale. Senza questo filtro, rispondere a
    tutti finirebbe per mettere in copia se stessi a ogni messaggio.
    """
    seen = set()
    merged = []
    for name, addr in getaddresses([v for v in header_values if v]):
        key = addr.lower().strip()
        if not key or key in exclude or key in seen:
            continue
        seen.add(key)
        merged.append(formataddr((name, addr)))
    return ", ".join(merged) or None


def _reply_context(
    service,
    account: str,
    message_id: str,
    reply_all: bool = False,
) -> dict:
    """
    Recupera dal messaggio originale tutto cio' che serve per restare nello
    stesso thread: destinatario, oggetto con il prefisso Re:, gli header
    In-Reply-To e References, e il threadId che Gmail usa per raggruppare.

    Con reply_all, gli altri destinatari finiscono in copia, esclusi la
    casella mittente e il destinatario principale.
    """
    original = service.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["From", "To", "Cc", "Reply-To", "Subject", "Message-ID", "References"],
    ).execute()
    headers = {h["name"]: h["value"] for h in original["payload"]["headers"]}

    subject = headers.get("Subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    to = headers.get("Reply-To") or headers.get("From", "")

    cc = None
    if reply_all:
        to_addrs = {addr.lower() for _, addr in getaddresses([to]) if addr}
        cc = _merge_recipients(
            headers.get("To", ""),
            headers.get("Cc", ""),
            exclude={account.lower()} | to_addrs,
        )

    references = " ".join(filter(None, [headers.get("References", ""), headers.get("Message-ID", "")]))

    return {
        "to": to,
        "cc": cc,
        "subject": subject,
        "in_reply_to": headers.get("Message-ID"),
        "references": references,
        "thread_id": original["threadId"],
    }


@mcp.tool()
def list_accounts() -> list[str]:
    """Elenca le caselle collegate a questo connettore di invio."""
    return sorted(_tokens)


@mcp.tool()
def list_emails(account: str, query: str = "", max_results: int = 20) -> list[dict]:
    """
    Elenca le email di una casella collegata, con gli stessi filtri di ricerca
    di Gmail (es. "in:inbox", "is:unread", "from:...", "newer_than:7d").

    account: indirizzo della casella da interrogare (vedi list_accounts)
    query: filtro di ricerca Gmail, vuoto per le email piu' recenti senza filtro
    max_results: numero massimo di email da restituire (limite 50)
    """
    service = _gmail_service(account)
    max_results = max(1, min(max_results, 50))

    resp = service.users().messages().list(
        userId="me", q=query or None, maxResults=max_results
    ).execute()
    message_refs = resp.get("messages", [])

    results = []
    for ref in message_refs:
        msg = service.users().messages().get(
            userId="me",
            id=ref["id"],
            format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        results.append({
            "id": msg["id"],
            "threadId": msg["threadId"],
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "labelIds": msg.get("labelIds", []),
        })
    return results


def _extract_body_and_attachments(payload: dict) -> tuple[str, str, list[str]]:
    """
    Percorre la struttura MIME di un messaggio (che puo' essere semplice,
    multipart, con allegati annidati) e restituisce (testo_semplice,
    testo_html, nomi_allegati). Se manca il testo semplice ma c'e' l'HTML,
    il chiamante decide se e come ripiegare su quello.
    """
    plain_text = ""
    html_text = ""
    attachments: list[str] = []

    def _decode(data: str) -> str:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")

    def _walk(part: dict) -> None:
        nonlocal plain_text, html_text
        mime_type = part.get("mimeType", "")
        filename = part.get("filename") or ""
        body = part.get("body", {})

        if filename:
            attachments.append(filename)
            return  # gli allegati non vanno letti come testo

        if mime_type == "text/plain" and body.get("data"):
            plain_text += _decode(body["data"])
        elif mime_type == "text/html" and body.get("data"):
            html_text += _decode(body["data"])

        for sub_part in part.get("parts", []):
            _walk(sub_part)

    _walk(payload)
    return plain_text, html_text, attachments


@mcp.tool()
def get_email(account: str, message_id: str) -> dict:
    """
    Recupera il contenuto completo di una email, corpo del testo incluso.

    account: la casella da cui leggere
    message_id: l'id del messaggio, ottenuto da list_emails
    """
    service = _gmail_service(account)
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}

    plain_text, html_text, attachments = _extract_body_and_attachments(msg["payload"])
    body = plain_text.strip() or html_text.strip() or msg.get("snippet", "")

    return {
        "id": msg["id"],
        "threadId": msg["threadId"],
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "body": body,
        "labelIds": msg.get("labelIds", []),
        "attachments": attachments,
    }


def _get_or_create_label(service, account: str, label_name: str) -> str:
    """
    Trova l'id di un'etichetta dal suo nome visibile (es. "Da trattare"), la
    crea se non esiste ancora su quella casella. Il confronto e' esatto,
    Gmail stesso considera i nomi delle etichette maiuscole/minuscole
    significative.
    """
    resp = service.users().labels().list(userId="me").execute()
    for label in resp.get("labels", []):
        if label["name"] == label_name:
            return label["id"]

    created = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return created["id"]


@mcp.tool()
def archive_email(account: str, message_id: str) -> dict:
    """
    Archivia un'email togliendola dalla posta in arrivo. Resta comunque
    accessibile tramite ricerca o tramite l'etichetta "Tutti i messaggi".

    account: la casella a cui appartiene l'email
    message_id: l'id del messaggio, ottenuto da list_emails
    """
    service = _gmail_service(account)
    service.users().messages().modify(
        userId="me", id=message_id, body={"removeLabelIds": ["INBOX"]}
    ).execute()
    return {"id": message_id, "stato": "archiviata"}


@mcp.tool()
def label_email(account: str, message_id: str, label_name: str) -> dict:
    """
    Applica un'etichetta a un'email, creandola su quella casella se non
    esiste ancora.

    account: la casella a cui appartiene l'email
    message_id: l'id del messaggio, ottenuto da list_emails
    label_name: nome dell'etichetta da applicare, es. "Da trattare"
    """
    service = _gmail_service(account)
    label_id = _get_or_create_label(service, account, label_name)
    service.users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": [label_id]}
    ).execute()
    return {"id": message_id, "etichetta": label_name, "stato": "applicata"}


@mcp.tool()
def send_email(
    account: str,
    to: str,
    subject: str,
    body: str,
    html_body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    signature_variant: Optional[str] = None,
) -> dict:
    """
    Invia una nuova email da una delle caselle collegate. La firma ufficiale
    della casella viene aggiunta automaticamente, se configurata.

    account: indirizzo della casella mittente (vedi list_accounts)
    to, cc, bcc: indirizzi destinatari, separati da virgola se piu' di uno
    signature_variant: solo per endolift@corsalis.ch, una tra
        "logistics", "accounting", "medical" (default "medical")
    html_body: OBBLIGATORIO. Il messaggio viene sempre inviato in
        multipart/alternative, con body come fallback testuale e html_body
        come parte HTML principale (paragrafi <p>, eventuali <strong>,
        <ol>/<ul>). Lo stile (Verdana 10px #666666) viene imposto dal
        connettore a corpo e firma, non serve indicarlo qui. Non includere
        una formula di chiusura scritta a mano (es. "Cordialement,
        Alberto"): la firma ufficiale della casella viene aggiunta in
        automatico, e una eventuale chiusura manuale rilevata in fondo al
        messaggio viene comunque tolta prima dell'invio per evitare un
        doppione.
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
    body: str,
    html_body: str,
    subject: Optional[str] = None,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    signature_variant: Optional[str] = None,
    reply_to_message_id: Optional[str] = None,
    reply_all: bool = True,
) -> dict:
    """
    Crea una bozza in una delle caselle collegate, senza inviarla. La firma
    ufficiale della casella viene aggiunta automaticamente, se configurata.

    reply_to_message_id: se fornito, la bozza viene creata come risposta a
        quel messaggio e resta nel suo thread. Destinatario, oggetto e
        destinatari in copia vengono ricavati dall'originale, salvo che tu
        li passi comunque con to, subject e cc, che in quel caso hanno la
        precedenza.
    reply_all: attivo per default. Mette in copia gli altri destinatari del
        messaggio originale, esclusa la casella mittente. Passare False per
        rispondere al solo mittente.
    subject, to: obbligatori solo se reply_to_message_id non e' fornito.
    signature_variant: solo per endolift@corsalis.ch, una tra
        "logistics", "accounting", "medical" (default "medical")
    html_body: OBBLIGATORIO. La bozza viene sempre creata in
        multipart/alternative, con body come fallback testuale e html_body
        come parte HTML principale. Lo stile (Verdana 10px #666666) viene
        imposto dal connettore a corpo e firma. Non includere una formula
        di chiusura scritta a mano: la firma ufficiale viene aggiunta in
        automatico, e una chiusura manuale rilevata in fondo al messaggio
        viene comunque tolta prima di creare la bozza.
    """
    service = _gmail_service(account)

    in_reply_to = None
    references = None
    thread_id = None

    if reply_to_message_id:
        ctx = _reply_context(service, account, reply_to_message_id, reply_all=reply_all)
        to = to or ctx["to"]
        subject = subject or ctx["subject"]
        cc = cc or ctx["cc"]
        in_reply_to = ctx["in_reply_to"]
        references = ctx["references"]
        thread_id = ctx["thread_id"]

    if not to:
        raise ValueError("Serve un destinatario: passa 'to' oppure 'reply_to_message_id'.")

    raw = _build_mime(
        to, subject or "", body, account, cc,
        in_reply_to=in_reply_to,
        references=references,
        signature_variant=signature_variant,
        html_body=html_body,
    )

    message_body = {"raw": raw}
    if thread_id:
        message_body["threadId"] = thread_id

    draft = service.users().drafts().create(
        userId="me", body={"message": message_body}
    ).execute()
    return {
        "id": draft["id"],
        "threadId": thread_id,
        "destinatario": to,
        "copia": cc,
        "stato": "bozza creata nel thread" if thread_id else "bozza creata",
    }


@mcp.tool()
def reply_email(
    account: str,
    message_id: str,
    body: str,
    html_body: str,
    reply_all: bool = False,
    signature_variant: Optional[str] = None,
) -> dict:
    """
    Risponde a un'email esistente restando nello stesso thread. La firma
    ufficiale della casella viene aggiunta automaticamente, se configurata.

    account: la casella da cui rispondere
    message_id: l'id del messaggio Gmail a cui rispondere
    body: testo della risposta
    reply_all: se True risponde anche a tutti i destinatari originali,
        non solo al mittente
    signature_variant: solo per endolift@corsalis.ch, una tra
        "logistics", "accounting", "medical" (default "medical")
    html_body: OBBLIGATORIO. La risposta viene sempre inviata in
        multipart/alternative con lo stesso stile usato per i nuovi
        messaggi, cosi' il thread resta coerente invece di alternare
        messaggi formattati e messaggi in solo testo. Non includere una
        formula di chiusura scritta a mano: la firma ufficiale viene
        aggiunta in automatico, e una chiusura manuale rilevata in fondo
        al messaggio viene comunque tolta prima dell'invio.
    """
    service = _gmail_service(account)
    ctx = _reply_context(service, account, message_id, reply_all=reply_all)

    raw = _build_mime(
        to=ctx["to"],
        subject=ctx["subject"],
        body=body,
        account=account,
        cc=ctx["cc"],
        in_reply_to=ctx["in_reply_to"],
        references=ctx["references"],
        signature_variant=signature_variant,
        html_body=html_body,
    )
    sent = service.users().messages().send(
        userId="me", body={"raw": raw, "threadId": ctx["thread_id"]}
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
    if _CORSALIS_LOGO_B64:
        logo_stato = f"presente ({len(_CORSALIS_LOGO_B64)} caratteri)"
    else:
        logo_stato = "assente (né variabile CORSALIS_LOGO_B64 né file corsalis_logo.b64)"
    return f"""
    <html><body style="font-family: sans-serif; max-width: 640px; margin: 40px auto;">
    <h2>Gmail Send MCP &middot; caselle collegate</h2>
    <ul>{accounts}</ul>
    <p><a href="/connect">+ Collega una nuova casella</a></p>
    <hr>
    <p>Logo Corsalis : {logo_stato}</p>
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
