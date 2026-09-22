"""
gmail_attach_tools.py

Strumenti di invio e di bozza CON ALLEGATI.

Perche' un file a parte
-----------------------
gmail_send_mcp.py resta la sede di OAuth, delle firme e degli strumenti
di invio senza allegati, ed e' gia' lungo. La regola scritta in main.py
dice che una nuova famiglia di strumenti si crea in un file
gmail_*_tools.py, lo si aggiunge alla lista di main.py e al Dockerfile, e
non si tocca nient'altro. E' esattamente cio' che fa questo modulo.

Cosa cambia rispetto a send_email e create_draft
------------------------------------------------
Tre cose, e nient'altro.

1. La struttura MIME, che diventa multipart/mixed quando c'e' almeno un
   allegato.
2. La copia nascosta sulle bozze. create_draft non accetta bcc: una
   bozza creata da un motore perderebbe in silenzio la copia alla
   casella contabile, che per le fatture Corsalis non e' un dettaglio.
3. Le intestazioni To, Subject, Cc e Bcc scritte con la maiuscola
   canonica. In minuscolo sono valide per la RFC 5322 e Gmail le
   consegna senza problemi, ma l'API Gmail restituisce il nome
   dell'intestazione com'e' stato scritto: list_emails e list_drafts
   mostrano allora oggetto e destinatario vuoti, proprio quando servono
   a controllare una bozza prodotta da un motore.

Tutte le regole di composizione restano quelle del pacchetto condiviso
gmail_message_rules (html_body obbligatorio, niente trattini lunghi,
niente chiusura scritta a mano duplicata, una sola riga vuota tra i
paragrafi e prima della firma), e la firma ufficiale della casella, logo
Corsalis in linea compreso, resta quella prodotta da gmail_send_mcp.
Nulla di tutto cio' e' riscritto qui.

La struttura del messaggio diventa:

    multipart/mixed
      multipart/related            corpo piu' logo di firma in linea
        multipart/alternative      testo semplice piu' HTML
        image/png                  logo Corsalis, Content-ID
      application/pdf e simili     gli allegati, una parte ciascuno

Senza allegati il messaggio resta identico a quello di send_email: il
livello mixed non viene creato. Cosi' un chiamante puo' usare sempre
questi strumenti, con o senza documento, senza cambiare di codice.

Chi usa questi strumenti
------------------------
Il motore Corsalis del classeur Base dati, che invia la commande Eufoton
con il proprio PDF e deve restare conforme alla charte Corsalis, e piu'
in generale qualunque messaggio che porti un documento.

Perche' il contenuto viaggia in base64
--------------------------------------
Questo servizio possiede solo ambiti Gmail. Chiedere un ambito Drive per
andare a cercare il file da solo allargherebbe i permessi di tutte le
caselle collegate per un solo caso d'uso. Il chiamante, che il file ce
l'ha gia' in mano, lo passa in base64: nessun ambito nuovo, nessuna
dipendenza in piu'.
"""

import base64
import binascii
import os
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from gmail_message_rules import build_message, HtmlBodyRequiredError

from gmail_send_mcp import (
    app,
    mcp,
    _ACCOUNT_LOGO_CID,
    _INLINE_LOGOS,
    _get_signature_html,
    _get_signature_text,
    _get_style,
    _gmail_service,
    _reply_context,
)

# Gmail rifiuta un messaggio grezzo oltre 35 Mo circa. Il tetto qui e'
# volutamente piu' basso: oltre, l'errore arriva da noi con un messaggio
# leggibile invece che da Google a meta' invio.
MAX_TOTAL_BYTES = 20 * 1024 * 1024

_MIME_PAR_EXTENSION = {
    ".pdf": ("application", "pdf"),
    ".png": ("image", "png"),
    ".jpg": ("image", "jpeg"),
    ".jpeg": ("image", "jpeg"),
    ".gif": ("image", "gif"),
    ".csv": ("text", "csv"),
    ".txt": ("text", "plain"),
    ".html": ("text", "html"),
    ".json": ("application", "json"),
    ".zip": ("application", "zip"),
    ".xlsx": (
        "application",
        "vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".docx": (
        "application",
        "vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".pptx": (
        "application",
        "vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
}


def _type_mime(nome_file: str, dichiarato: Optional[str]) -> tuple[str, str]:
    """Il tipo MIME dichiarato dal chiamante, o dedotto dall'estensione."""
    if dichiarato and "/" in dichiarato:
        principale, _, secondario = dichiarato.partition("/")
        return principale.strip() or "application", secondario.strip() or "octet-stream"
    _, estensione = os.path.splitext(nome_file.lower())
    return _MIME_PAR_EXTENSION.get(estensione, ("application", "octet-stream"))


def _parti_allegate(attachments) -> list[MIMEBase]:
    """
    Trasforma la lista ricevuta in parti MIME pronte da agganciare.

    Ogni voce e' un oggetto con:
        filename        nome visibile dell'allegato, obbligatorio
        content_base64  contenuto del file in base64, obbligatorio
        mime_type       facoltativo, dedotto dall'estensione se assente

    Un contenuto illeggibile o un nome mancante fermano tutto prima che
    il messaggio parta: meglio un errore chiaro di un allegato vuoto
    arrivato al destinatario.
    """
    if not attachments:
        return []
    if isinstance(attachments, dict):
        attachments = [attachments]

    parti: list[MIMEBase] = []
    totale = 0

    for rango, voce in enumerate(attachments, start=1):
        if not isinstance(voce, dict):
            raise ValueError(
                f"Allegato {rango}: serve un oggetto con 'filename' e 'content_base64'."
            )

        nome = str(voce.get("filename") or "").strip()
        if not nome:
            raise ValueError(f"Allegato {rango}: 'filename' mancante.")

        grezzo = voce.get("content_base64") or voce.get("content") or ""
        grezzo = "".join(str(grezzo).split())
        if not grezzo:
            raise ValueError(f"Allegato '{nome}': 'content_base64' mancante o vuoto.")

        try:
            dati = base64.b64decode(grezzo, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                f"Allegato '{nome}': contenuto base64 illeggibile ({exc})."
            ) from exc

        if not dati:
            raise ValueError(f"Allegato '{nome}': contenuto vuoto dopo la decodifica.")

        totale += len(dati)
        if totale > MAX_TOTAL_BYTES:
            raise ValueError(
                "Allegati troppo pesanti: {:.1f} Mo, il tetto e' {:.0f} Mo.".format(
                    totale / 1048576, MAX_TOTAL_BYTES / 1048576
                )
            )

        principale, secondario = _type_mime(nome, voce.get("mime_type"))
        parte = MIMEBase(principale, secondario)
        parte.set_payload(dati)
        encoders.encode_base64(parte)
        parte.add_header("Content-Disposition", "attachment", filename=nome)
        parti.append(parte)

    return parti


def _costruisci_mime(
    to: str,
    subject: str,
    body: str,
    html_body: str,
    account: str,
    attachments=None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    signature_variant: Optional[str] = None,
    include_signature: bool = True,
) -> str:
    """Il messaggio completo, in base64 url-safe, pronto per l'API Gmail."""
    signature_text = (
        _get_signature_text(account, signature_variant) if include_signature else ""
    )
    signature_html = (
        _get_signature_html(account, signature_variant) if include_signature else ""
    )

    try:
        costruito = build_message(
            subject=subject,
            body=body,
            html_body=html_body,
            signature_text=signature_text,
            signature_html=signature_html,
            style=_get_style(account),
        )
    except HtmlBodyRequiredError as exc:
        raise ValueError(str(exc))

    alternativa = MIMEMultipart("alternative")
    alternativa.attach(MIMEText(costruito["text_body"], "plain"))
    alternativa.attach(MIMEText(costruito["html_body"], "html"))

    logo_cid = _ACCOUNT_LOGO_CID.get(account) if include_signature else None
    if logo_cid and _INLINE_LOGOS.get(logo_cid):
        corpo = MIMEMultipart("related")
        corpo.attach(alternativa)
        immagine = MIMEImage(base64.b64decode(_INLINE_LOGOS[logo_cid]), _subtype="png")
        immagine.add_header("Content-ID", f"<{logo_cid}>")
        immagine.add_header(
            "Content-Disposition", "inline", filename=f"{logo_cid}.png"
        )
        corpo.attach(immagine)
    else:
        corpo = alternativa

    parti = _parti_allegate(attachments)
    if parti:
        messaggio = MIMEMultipart("mixed")
        messaggio.attach(corpo)
        for parte in parti:
            messaggio.attach(parte)
    else:
        messaggio = corpo

    # Maiuscola canonica: l'API Gmail restituisce il nome dell'intestazione
    # com'e' stato scritto, e un "to" minuscolo esce come destinatario
    # vuoto in list_emails e list_drafts.
    messaggio["To"] = to
    messaggio["Subject"] = costruito["subject"]
    if cc:
        messaggio["Cc"] = cc
    if bcc:
        messaggio["Bcc"] = bcc
    if in_reply_to:
        messaggio["In-Reply-To"] = in_reply_to
        messaggio["References"] = references or in_reply_to

    return base64.urlsafe_b64encode(messaggio.as_bytes()).decode()


@mcp.tool()
def send_email_with_attachments(
    account: str,
    to: str,
    subject: str,
    body: str,
    html_body: str,
    attachments: Optional[list] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    signature_variant: Optional[str] = None,
) -> dict:
    """
    Invia una email con uno o piu' allegati, dalla stessa casella e con la
    stessa firma ufficiale di send_email.

    account: indirizzo della casella mittente (vedi list_accounts)
    to, cc, bcc: destinatari, separati da virgola se piu' di uno
    attachments: lista di oggetti, uno per file:
        {"filename": "Commande 2026-O-12.pdf",
         "content_base64": "JVBERi0xLj...",
         "mime_type": "application/pdf"}
        mime_type e' facoltativo, viene dedotto dall'estensione. Il tetto
        complessivo e' di 20 Mo. Lista vuota o assente: il messaggio parte
        senza allegati, identico a quello di send_email.
    signature_variant: solo per endolift@corsalis.ch, una tra
        "logistics", "accounting", "medical" (default "medical")
    html_body: OBBLIGATORIO, come per send_email. Lo stile e la firma sono
        imposti dal connettore: non scrivere formule di chiusura a mano,
        non usare trattini lunghi.
    """
    service = _gmail_service(account)
    raw = _costruisci_mime(
        to=to,
        subject=subject,
        body=body,
        html_body=html_body,
        account=account,
        attachments=attachments,
        cc=cc,
        bcc=bcc,
        signature_variant=signature_variant,
    )
    inviata = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {
        "id": inviata["id"],
        "threadId": inviata["threadId"],
        "allegati": len(attachments or []),
        "stato": "inviata",
    }


@mcp.tool()
def create_draft_with_attachments(
    account: str,
    body: str,
    html_body: str,
    attachments: Optional[list] = None,
    subject: Optional[str] = None,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    signature_variant: Optional[str] = None,
    reply_to_message_id: Optional[str] = None,
    reply_all: bool = True,
) -> dict:
    """
    Crea una bozza con uno o piu' allegati, senza inviarla, dalla stessa
    casella e con la stessa firma ufficiale di create_draft.

    attachments: lista di oggetti, uno per file:
        {"filename": "Commande 2026-O-12.pdf",
         "content_base64": "JVBERi0xLj...",
         "mime_type": "application/pdf"}
        mime_type e' facoltativo, viene dedotto dall'estensione. Il tetto
        complessivo e' di 20 Mo. Lista vuota o assente: la bozza e'
        identica a quella di create_draft.
    bcc: copia nascosta, che create_draft non prevede. Serve alle bozze
        create da un motore, per esempio la copia alla casella contabile.
    reply_to_message_id: se fornito, la bozza resta nel thread di quel
        messaggio; destinatario, oggetto e copie vengono dedotti
        dall'originale, salvo quelli passati esplicitamente.
    reply_all: attivo per default, mette in copia gli altri destinatari
        del messaggio originale, esclusa la casella mittente.
    subject, to: obbligatori solo se reply_to_message_id non e' fornito.
    signature_variant: solo per endolift@corsalis.ch, una tra
        "logistics", "accounting", "medical" (default "medical")
    html_body: OBBLIGATORIO, come per create_draft.
    """
    service = _gmail_service(account)

    in_reply_to = None
    references = None
    thread_id = None

    if reply_to_message_id:
        contesto = _reply_context(
            service, account, reply_to_message_id, reply_all=reply_all
        )
        to = to or contesto["to"]
        subject = subject or contesto["subject"]
        cc = cc or contesto["cc"]
        in_reply_to = contesto["in_reply_to"]
        references = contesto["references"]
        thread_id = contesto["thread_id"]

    if not to:
        raise ValueError(
            "Serve un destinatario: passa 'to' oppure 'reply_to_message_id'."
        )

    raw = _costruisci_mime(
        to=to,
        subject=subject or "",
        body=body,
        html_body=html_body,
        account=account,
        attachments=attachments,
        cc=cc,
        bcc=bcc,
        in_reply_to=in_reply_to,
        references=references,
        signature_variant=signature_variant,
    )

    corpo_messaggio = {"raw": raw}
    if thread_id:
        corpo_messaggio["threadId"] = thread_id

    bozza = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": corpo_messaggio})
        .execute()
    )
    return {
        "id": bozza["id"],
        "threadId": thread_id,
        "destinatario": to,
        "copia": cc,
        "copia_nascosta": bcc,
        "allegati": len(attachments or []),
        "stato": "bozza creata nel thread" if thread_id else "bozza creata",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 3001)),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
