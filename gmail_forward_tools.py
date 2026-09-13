"""
gmail_forward_tools.py

Strumenti di inoltro: trasmettono un messaggio ricevuto a un altro
destinatario CON I SUOI ALLEGATI ORIGINALI, byte per byte, e con la
possibilita' di scegliere quali allegati inoltrare e quali no.

Perche' serve un file a parte. send_email costruisce un messaggio nuovo e
non sa allegare nulla: per mandare una fattura a un servizio contabile
(DEXT, per esempio) il vero inoltro e' l'unica via utile, perche' il PDF
allegato E' il documento, mentre il testo dell'email non lo e'.

Perche' serve la selezione degli allegati. Molti messaggi automatici
portano piu' documenti che dicono la stessa cosa in due forme: Stripe, per
esempio, allega a ogni ricevuta sia Invoice-....pdf sia Receipt-....pdf.
Mandarli entrambi a un servizio contabile significa creare due pezze per
una sola spesa, cioe' un doppione da correggere a mano. I parametri
attachment_include e attachment_exclude accettano motivi in stile glob
(Invoice-*.pdf) e risolvono il caso senza scaricare nulla a mano.

Ambito OAuth. Leggere il contenuto binario di un allegato rientra in
gmail.readonly, inviare rientra in gmail.send: entrambi sono gia' negli
SCOPES del servizio, quindi nessuna casella deve riautorizzare nulla.

Fedelta' del contenuto. Il corpo originale NON passa da
gmail_message_rules.build_message. Quelle regole servono a ripulire i
testi che scriviamo noi (chiusure manuali doppie, trattini lunghi,
spaziatura), e applicate al messaggio di un terzo ne altererebbero il
contenuto, che in un inoltro deve restare intatto. Qui il corpo originale
viene riportato tale e quale dentro un blocco di citazione, e lo stile
della casella si applica soltanto al commento di accompagnamento e alla
firma.

Limite di dimensione. Gmail rifiuta gli invii oltre i 25 MB. Il tool si
ferma prima, a 20 MB di allegati EFFETTIVAMENTE inoltrati, con un errore
esplicito invece di un 500 opaco. Il conteggio avviene dopo il filtro,
quindi escludere un allegato pesante e' anche il modo di far passare un
messaggio altrimenti troppo grande.
"""

import base64
import fnmatch
import html as html_lib
import re
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from gmail_send_mcp import (
    mcp,
    _gmail_service,
    _extract_body_and_attachments,
    _get_signature_html,
    _get_signature_text,
    _get_style,
)

LIMITE_ALLEGATI_BYTE = 20 * 1024 * 1024

_RE_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE)
_RE_TAG_DOCUMENTO = re.compile(r"</?(?:html|head|body)[^>]*>", re.IGNORECASE)


def _decodifica(dato: str) -> bytes:
    return base64.urlsafe_b64decode(dato + "=" * (-len(dato) % 4))


def _intestazioni(messaggio: dict) -> dict:
    return {h["name"]: h["value"] for h in messaggio["payload"].get("headers", [])}


def _raccogli_allegati(service, message_id: str, payload: dict) -> list[dict]:
    """
    Scende nella struttura MIME e riporta ogni parte che ha un nome di
    file, con i suoi byte reali. Gmail non restituisce il contenuto degli
    allegati insieme al messaggio: la parte porta solo un attachmentId,
    che va risolto con una chiamata dedicata.
    """
    allegati: list[dict] = []

    def _percorri(parte: dict) -> None:
        nome = parte.get("filename") or ""
        corpo = parte.get("body", {}) or {}

        if nome:
            dato = corpo.get("data")
            if not dato and corpo.get("attachmentId"):
                risposta = service.users().messages().attachments().get(
                    userId="me",
                    messageId=message_id,
                    id=corpo["attachmentId"],
                ).execute()
                dato = risposta.get("data")
            if dato:
                intestazioni = {
                    h["name"].lower(): h["value"]
                    for h in parte.get("headers", []) or []
                }
                disposizione = intestazioni.get("content-disposition", "")
                content_id = (intestazioni.get("content-id") or "").strip("<>")
                allegati.append({
                    "filename": nome,
                    "mime_type": parte.get("mimeType") or "application/octet-stream",
                    "bytes": _decodifica(dato),
                    "content_id": content_id,
                    "inline": "inline" in disposizione.lower() or bool(content_id),
                })
            return

        for sotto_parte in parte.get("parts", []) or []:
            _percorri(sotto_parte)

    _percorri(payload)
    return allegati


def _corrisponde(nome: str, motivi: list[str]) -> bool:
    """
    Confronto in stile glob, indifferente a maiuscole e minuscole, su piu'
    motivi: basta che uno corrisponda. Un motivo senza carattere jolly
    vale come nome esatto, quindi 'Invoice-9BF0758D-5807021.pdf' funziona
    tanto quanto 'Invoice-*.pdf'.
    """
    nome_confronto = nome.lower()
    return any(fnmatch.fnmatch(nome_confronto, motivo.lower()) for motivo in motivi)


def _filtra_allegati(
    allegati: list[dict],
    include: Optional[list[str]],
    exclude: Optional[list[str]],
    keep_inline_images: bool,
) -> tuple[list[dict], list[str]]:
    """
    Applica i motivi di selezione e restituisce (tenuti, nomi_scartati).

    Regole, nell'ordine:
      1. se include e' fornito, resta solo cio' che vi corrisponde;
      2. exclude toglie comunque cio' che vi corrisponde, anche se era
         stato incluso al punto 1;
      3. le immagini in linea (quelle con un Content-ID, richiamate dal
         corpo HTML con cid:) sfuggono al solo punto 1, se
         keep_inline_images e' attivo: filtrarle via lascerebbe il corpo
         inoltrato pieno di immagini rotte, il che non e' mai cio' che si
         voleva chiedendo 'mandami solo le fatture'. Restano invece
         soggette a exclude, che e' un ordine esplicito.
    """
    tenuti: list[dict] = []
    scartati: list[str] = []

    for allegato in allegati:
        nome = allegato["filename"]

        if exclude and _corrisponde(nome, exclude):
            scartati.append(nome)
            continue

        if include and not _corrisponde(nome, include):
            if keep_inline_images and allegato["inline"] and allegato["content_id"]:
                tenuti.append(allegato)
                continue
            scartati.append(nome)
            continue

        tenuti.append(allegato)

    return tenuti, scartati


def _citazione_testo(intestazioni: dict) -> str:
    righe = [
        "---------- Messaggio inoltrato ----------",
        f"Da : {intestazioni.get('From', '')}",
        f"Data : {intestazioni.get('Date', '')}",
        f"Oggetto : {intestazioni.get('Subject', '')}",
        f"A : {intestazioni.get('To', '')}",
    ]
    if intestazioni.get("Cc"):
        righe.append(f"Cc : {intestazioni['Cc']}")
    return "\n".join(righe) + "\n\n"


def _stile_inline(stile: dict) -> str:
    return (
        f"font-family:{stile['font_family']};"
        f"font-size:{stile['font_size']};"
        f"color:{stile['color']};"
    )


def _citazione_html(intestazioni: dict, stile: dict) -> str:
    def _esc(valore: str) -> str:
        return html_lib.escape(valore or "")

    righe = [
        "<strong>---------- Messaggio inoltrato ----------</strong>",
        f"Da : {_esc(intestazioni.get('From', ''))}",
        f"Data : {_esc(intestazioni.get('Date', ''))}",
        f"Oggetto : {_esc(intestazioni.get('Subject', ''))}",
        f"A : {_esc(intestazioni.get('To', ''))}",
    ]
    if intestazioni.get("Cc"):
        righe.append(f"Cc : {_esc(intestazioni['Cc'])}")
    return (
        f'<div style="{_stile_inline(stile)}">'
        + "<br>".join(righe)
        + "</div><br>"
    )


def _ripulisci_html_originale(sorgente: str) -> str:
    """
    Il corpo HTML di un'email in arrivo e' spesso un documento completo,
    con doctype, head e body. Innestato dentro un altro messaggio produce
    HTML annidato che alcuni client rendono male: si tolgono solo i tag di
    documento, lasciando intatto tutto il resto, stili compresi.
    """
    ripulito = _RE_DOCTYPE.sub("", sorgente)
    return _RE_TAG_DOCUMENTO.sub("", ripulito).strip()


def _corpo_inoltro(
    account: str,
    intestazioni: dict,
    testo_originale: str,
    html_originale: str,
    commento: str,
    commento_html: str,
    include_signature: bool,
    signature_variant: Optional[str],
    include_original_body: bool,
) -> tuple[str, str]:
    stile = _get_style(account)
    stile_css = _stile_inline(stile)

    firma_testo = _get_signature_text(account, signature_variant) if include_signature else ""
    firma_html = _get_signature_html(account, signature_variant) if include_signature else ""

    parti_testo: list[str] = []
    parti_html: list[str] = []

    if commento:
        parti_testo.append(commento.strip())
    if commento_html:
        parti_html.append(f'<div style="{stile_css}">{commento_html.strip()}</div>')
    elif commento:
        testo_escapato = html_lib.escape(commento.strip()).replace("\n", "<br>")
        parti_html.append(f'<div style="{stile_css}">{testo_escapato}</div>')

    if firma_testo:
        parti_testo.append(firma_testo)
    if firma_html:
        parti_html.append(f'<div style="{stile_css}">{firma_html}</div>')

    if include_original_body:
        parti_testo.append(_citazione_testo(intestazioni) + (testo_originale or "").strip())

        corpo_html = _ripulisci_html_originale(html_originale or "")
        if not corpo_html:
            corpo_html = (
                f'<div style="{stile_css}">'
                + html_lib.escape((testo_originale or "").strip()).replace("\n", "<br>")
                + "</div>"
            )
        parti_html.append(
            '<div style="border-top:1px solid #cccccc;margin-top:16px;padding-top:16px;">'
            + _citazione_html(intestazioni, stile)
            + corpo_html
            + "</div>"
        )

    return "\n\n".join(p for p in parti_testo if p), "<br>".join(p for p in parti_html if p)


def _allega(radice: MIMEMultipart, allegato: dict) -> None:
    tipo = allegato["mime_type"]
    if "/" in tipo:
        principale, secondario = tipo.split("/", 1)
    else:
        principale, secondario = "application", "octet-stream"

    parte = MIMEBase(principale, secondario)
    parte.set_payload(allegato["bytes"])
    encoders.encode_base64(parte)
    parte.add_header(
        "Content-Disposition",
        "inline" if allegato["inline"] else "attachment",
        filename=allegato["filename"],
    )
    if allegato["content_id"]:
        parte.add_header("Content-ID", f"<{allegato['content_id']}>")
    radice.attach(parte)


def _inoltra(
    service,
    account: str,
    message_id: str,
    to: str,
    cc: Optional[str],
    bcc: Optional[str],
    subject: Optional[str],
    comment: str,
    comment_html: str,
    include_attachments: bool,
    attachment_include: Optional[list[str]],
    attachment_exclude: Optional[list[str]],
    keep_inline_images: bool,
    include_original_body: bool,
    include_signature: bool,
    signature_variant: Optional[str],
) -> dict:
    originale = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
    intestazioni = _intestazioni(originale)

    testo_originale, html_originale, _ = _extract_body_and_attachments(originale["payload"])

    allegati: list[dict] = []
    scartati: list[str] = []
    if include_attachments:
        tutti = _raccogli_allegati(service, message_id, originale["payload"])
        allegati, scartati = _filtra_allegati(
            tutti, attachment_include, attachment_exclude, keep_inline_images
        )
        if attachment_include and not any(not a["inline"] for a in allegati):
            disponibili = ", ".join(a["filename"] for a in tutti) or "nessun allegato"
            raise ValueError(
                f"Nessun allegato corrisponde a {attachment_include} nel messaggio "
                f"{message_id}. Allegati presenti : {disponibili}. "
                "Il messaggio non e' stato inoltrato, per non mandare una pezza vuota."
            )

    peso = sum(len(a["bytes"]) for a in allegati)
    if peso > LIMITE_ALLEGATI_BYTE:
        raise ValueError(
            f"Allegati troppo pesanti per un invio Gmail : {peso / 1024 / 1024:.1f} MB "
            f"(limite prudenziale {LIMITE_ALLEGATI_BYTE / 1024 / 1024:.0f} MB). "
            "Restringi la selezione con attachment_include o attachment_exclude, "
            "oppure inoltra il messaggio a mano."
        )

    oggetto = subject
    if not oggetto:
        oggetto_originale = intestazioni.get("Subject", "")
        oggetto = (
            oggetto_originale
            if oggetto_originale.lower().startswith(("fwd:", "fw:", "tr:", "i:"))
            else f"Fwd: {oggetto_originale}"
        )

    corpo_testo, corpo_html = _corpo_inoltro(
        account=account,
        intestazioni=intestazioni,
        testo_originale=testo_originale,
        html_originale=html_originale,
        commento=comment,
        commento_html=comment_html,
        include_signature=include_signature,
        signature_variant=signature_variant,
        include_original_body=include_original_body,
    )

    alternativa = MIMEMultipart("alternative")
    alternativa.attach(MIMEText(corpo_testo or " ", "plain", "utf-8"))
    alternativa.attach(MIMEText(corpo_html or " ", "html", "utf-8"))

    if allegati:
        messaggio = MIMEMultipart("mixed")
        messaggio.attach(alternativa)
        for allegato in allegati:
            _allega(messaggio, allegato)
    else:
        messaggio = alternativa

    messaggio["to"] = to
    messaggio["subject"] = Header(oggetto, "utf-8")
    if cc:
        messaggio["cc"] = cc
    if bcc:
        messaggio["bcc"] = bcc

    raw = base64.urlsafe_b64encode(messaggio.as_bytes()).decode()
    inviato = service.users().messages().send(userId="me", body={"raw": raw}).execute()

    return {
        "id": inviato["id"],
        "threadId": inviato["threadId"],
        "message_id_originale": message_id,
        "oggetto": oggetto,
        "destinatario": to,
        "allegati_inoltrati": [a["filename"] for a in allegati],
        "allegati_scartati": scartati,
        "peso_allegati_kb": round(peso / 1024, 1),
        "stato": "inoltrata",
    }


@mcp.tool()
def forward_email(
    account: str,
    message_id: str,
    to: str,
    comment: str = "",
    comment_html: str = "",
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    subject: Optional[str] = None,
    include_attachments: bool = True,
    attachment_include: Optional[list[str]] = None,
    attachment_exclude: Optional[list[str]] = None,
    keep_inline_images: bool = True,
    include_original_body: bool = True,
    include_signature: bool = True,
    signature_variant: Optional[str] = None,
) -> dict:
    """
    Inoltra un messaggio ricevuto a un altro destinatario, con i suoi
    allegati originali intatti, eventualmente solo alcuni. E' il tool da
    usare quando cio' che conta e' il file allegato (fattura, ricevuta,
    contratto) e non il testo: send_email costruirebbe un messaggio nuovo,
    senza allegati.

    account: la casella che possiede il messaggio e da cui parte l'inoltro
    message_id: l'id del messaggio da inoltrare, ottenuto da list_emails
    to, cc, bcc: destinatari, separati da virgola se piu' di uno
    comment: testo di accompagnamento, facoltativo. Compare sopra il
        messaggio inoltrato.
    comment_html: versione HTML del commento. Se assente viene ricavata
        dal testo. Lo stile della casella (Verdana 10px #666666) e'
        applicato dal connettore.
    subject: oggetto, facoltativo. Per difetto l'oggetto originale
        preceduto da "Fwd: ", salvo che lo sia gia'.

    Selezione degli allegati (i nomi si leggono con get_email):
    attachment_include: elenco di motivi in stile glob, es.
        ["Invoice-*.pdf"]. Se fornito, parte SOLO cio' che vi corrisponde.
        Un nome scritto per intero vale come motivo esatto. Se nessun
        allegato corrisponde, il messaggio non parte e il tool lo dice,
        invece di mandare una pezza vuota.
    attachment_exclude: elenco di motivi da escludere, es.
        ["Receipt-*.pdf"]. Ha la precedenza su attachment_include.
    keep_inline_images: attivo per difetto. Conserva le immagini
        richiamate dal corpo HTML (loghi, firme grafiche) anche quando
        attachment_include escluderebbe tutto il resto, per non inoltrare
        un messaggio pieno di immagini rotte. Restano comunque soggette a
        attachment_exclude.
    include_attachments: a False il messaggio parte senza alcun allegato.

    include_original_body: attivo per difetto. A False parte solo il
        commento con gli allegati, senza il corpo del messaggio originale.
    include_signature: attivo per difetto. Conviene metterlo a False
        quando il destinatario e' un servizio automatico (per esempio una
        casella DEXT), dove la firma e' solo rumore.

    Il corpo originale viene riportato tale e quale, senza passare dalle
    regole di riscrittura applicate ai messaggi che scriviamo noi: un
    inoltro deve restare fedele.
    """
    service = _gmail_service(account)
    return _inoltra(
        service=service,
        account=account,
        message_id=message_id,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        comment=comment,
        comment_html=comment_html,
        include_attachments=include_attachments,
        attachment_include=attachment_include,
        attachment_exclude=attachment_exclude,
        keep_inline_images=keep_inline_images,
        include_original_body=include_original_body,
        include_signature=include_signature,
        signature_variant=signature_variant,
    )


@mcp.tool()
def forward_emails(
    account: str,
    message_ids: list[str],
    to: str,
    comment: str = "",
    comment_html: str = "",
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    include_attachments: bool = True,
    attachment_include: Optional[list[str]] = None,
    attachment_exclude: Optional[list[str]] = None,
    keep_inline_images: bool = True,
    include_original_body: bool = True,
    include_signature: bool = True,
    signature_variant: Optional[str] = None,
) -> dict:
    """
    Inoltra piu' messaggi allo stesso destinatario, uno per uno, ciascuno
    con i propri allegati. Serve per i lotti di ricevute da mandare a un
    servizio contabile, dove un unico messaggio riassuntivo non
    servirebbe a nulla: ogni documento deve arrivare come allegato del
    proprio messaggio.

    account: la casella che possiede i messaggi
    message_ids: elenco degli id, ottenuti da list_emails
    to, cc, bcc: destinatari, uguali per tutti i messaggi del lotto

    attachment_include, attachment_exclude, keep_inline_images,
    include_attachments, comment, comment_html, include_original_body,
    include_signature, signature_variant: come in forward_email, applicati
        a ogni messaggio del lotto. Un lotto omogeneo (per esempio dieci
        ricevute Stripe) si tratta bene cosi': attachment_include
        ["Invoice-*.pdf"] manda la fattura di ciascuna e lascia da parte
        la ricevuta, che duplicherebbe la stessa spesa in contabilita'.

    Un errore su un messaggio non ferma gli altri: viene riportato nella
    lista 'falliti', con il suo motivo, e il lotto prosegue.
    """
    service = _gmail_service(account)

    inoltrate = []
    falliti = []
    for message_id in message_ids:
        try:
            inoltrate.append(_inoltra(
                service=service,
                account=account,
                message_id=message_id,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=None,
                comment=comment,
                comment_html=comment_html,
                include_attachments=include_attachments,
                attachment_include=attachment_include,
                attachment_exclude=attachment_exclude,
                keep_inline_images=keep_inline_images,
                include_original_body=include_original_body,
                include_signature=include_signature,
                signature_variant=signature_variant,
            ))
        except Exception as errore:  # noqa: BLE001 - il lotto non si ferma
            falliti.append({"message_id": message_id, "motivo": str(errore)})

    return {
        "destinatario": to,
        "inoltrate": inoltrate,
        "falliti": falliti,
        "stato": f"{len(inoltrate)} messaggi inoltrati, {len(falliti)} falliti",
    }
