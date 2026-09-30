"""
gmail_lettura_allegati_tools.py

Strumenti di LETTURA degli allegati ricevuti.

Perche' esiste questo modulo
----------------------------
gmail_attach_tools.py sa MANDARE un allegato, non leggerne uno. Finche'
il connettore ha saputo solo elencare i nomi dei file (get_email
restituisce la lista di 'attachments'), il contenuto restava fuori
portata: per sapere cosa c'era dentro un PDF ricevuto bisognava
inoltrare il messaggio a una casella leggibile da un progetto Apps
Script, depositare le pieces sul Drive da li', e rileggerle. E' successo
davvero il 30.09.2026, sul dossier doganale Corsalis (sei PDF di DHL da
verificare prima di inoltrarli al fornitore): tre passaggi, un file
temporaneo depositato in un progetto di produzione, e nessuno di questi
gesti aveva a che vedere con la domanda, che era semplicemente « cosa
c'e' scritto in questi PDF ».

Cosa espone
-----------
elenca_allegati        i file di un messaggio, con tipo e peso
leggi_allegato_testo   il testo di un PDF o di un file di testo
allegato_base64        il contenuto grezzo, quando serve il file stesso

Perche' tre strumenti e non uno
-------------------------------
Il costo di una risposta non e' uguale nei tre casi. L'elenco costa
qualche riga. Il testo di un PDF di dieci pagine costa qualche migliaio
di caratteri, ed e' quasi sempre cio' che serve davvero, per esempio per
sapere se una dichiarazione doganale porta il suo numero MRN. Il
contenuto grezzo di un PDF di 400 Ko costa oltre mezzo milione di
caratteri in base64, e serve solo quando il file va guardato con gli
occhi (una pagina scansionata) o rimandato altrove. Un solo strumento
avrebbe imposto il costo piu' alto a tutti e tre i casi.

Le pagine scansionate
---------------------
Un PDF puo' non avere alcuno strato di testo: e' il caso delle fatture
passate dallo scanner, molto frequenti nei dossier doganali. Il testo
estratto e' allora vuoto, senza errore. Per questo la risposta dice
sempre QUANTE pagine hanno del testo e quali no, invece di rendere una
stringa vuota che si scambierebbe per un documento vuoto. Le pagine
senza testo si guardano con allegato_base64 e un lettore di immagini:
qui non c'e' riconoscimento ottico, e dichiararlo e' piu' onesto che
restituire un risultato a meta'.

Ambiti
------
Nessun ambito nuovo. Gli allegati si leggono con gli stessi ambiti Gmail
che servono gia' a list_emails e get_email.
"""

import base64
import io
import os
from typing import Optional

from gmail_send_mcp import app, mcp, _gmail_service

# Oltre questo peso, allegato_base64 rifiuta invece di restituire una
# risposta che nessun chiamante puo' leggere per intero.
MAX_BASE64_BYTES = 12 * 1024 * 1024

# Difesa del contesto del chiamante: il testo si taglia qui, e la
# risposta dice che e' stato tagliato e come chiedere il seguito.
MAX_CARATTERI_DIFETTO = 40000

_ESTENSIONI_TESTO = (".txt", ".csv", ".md", ".html", ".htm", ".json", ".xml")


def _parti_allegate(payload, raccolte=None, percorso="") -> list:
    """
    Percorre l'albero MIME e raccoglie le parti che sono allegati.

    Un messaggio inoltrato annida le sue parti: l'allegato di un
    messaggio inoltrato non sta al primo livello. Da qui la ricorsione,
    che il codice piu' semplice di una lista piatta avrebbe mancato.
    """
    if raccolte is None:
        raccolte = []
    if not payload:
        return raccolte

    nome = payload.get("filename") or ""
    corpo = payload.get("body") or {}
    if nome and corpo.get("attachmentId"):
        raccolte.append(
            {
                "nome": nome,
                "tipo_mime": payload.get("mimeType") or "",
                "octets": int(corpo.get("size") or 0),
                "id_allegato": corpo["attachmentId"],
                "percorso": percorso or "1",
            }
        )

    for rango, parte in enumerate(payload.get("parts") or [], start=1):
        _parti_allegate(parte, raccolte, f"{percorso}.{rango}" if percorso else str(rango))

    return raccolte


def _elenco(service, message_id: str) -> list:
    messaggio = (
        service.users().messages().get(userId="me", id=message_id, format="full").execute()
    )
    return _parti_allegate(messaggio.get("payload"))


def _scegli(allegati: list, filename: Optional[str], indice: Optional[int]) -> dict:
    """
    L'allegato designato dal chiamante, per nome o per numero d'ordine.

    Il nome vince sul numero. Un nome che non corrisponde a niente non
    prende il primo file per difetto: rende un errore che elenca i nomi
    disponibili, perche' leggere il documento sbagliato e credere di
    avere letto quello giusto e' il peggiore dei due esiti.
    """
    if not allegati:
        raise ValueError("Questo messaggio non porta allegati.")

    if filename:
        cercato = filename.strip().lower()
        for voce in allegati:
            if voce["nome"].lower() == cercato:
                return voce
        for voce in allegati:
            if cercato in voce["nome"].lower():
                return voce
        disponibili = ", ".join(v["nome"] for v in allegati)
        raise ValueError(
            f"Nessun allegato corrisponde a '{filename}'. Disponibili: {disponibili}"
        )

    if indice:
        if indice < 1 or indice > len(allegati):
            raise ValueError(
                f"Indice {indice} fuori intervallo: il messaggio porta "
                f"{len(allegati)} allegato/i."
            )
        return allegati[indice - 1]

    if len(allegati) == 1:
        return allegati[0]

    disponibili = ", ".join(v["nome"] for v in allegati)
    raise ValueError(
        "Il messaggio porta piu' allegati: indica 'filename' oppure 'indice'. "
        f"Disponibili: {disponibili}"
    )


def _dati(service, message_id: str, voce: dict) -> bytes:
    allegato = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=voce["id_allegato"])
        .execute()
    )
    return base64.urlsafe_b64decode(allegato["data"])


@mcp.tool()
def elenca_allegati(account: str, message_id: str) -> dict:
    """
    Elenca gli allegati di un messaggio ricevuto, con tipo e peso.

    account: la casella che possiede il messaggio (vedi list_accounts)
    message_id: l'id del messaggio, ottenuto da list_emails

    Rende per ogni allegato il nome, il tipo MIME, il peso in byte e il
    suo numero d'ordine, da passare a leggi_allegato_testo o a
    allegato_base64. Gli allegati annidati in un messaggio inoltrato
    sono compresi.
    """
    service = _gmail_service(account)
    allegati = _elenco(service, message_id)
    return {
        "message_id": message_id,
        "numero": len(allegati),
        "allegati": [
            {
                "indice": rango,
                "nome": voce["nome"],
                "tipo_mime": voce["tipo_mime"],
                "octets": voce["octets"],
            }
            for rango, voce in enumerate(allegati, start=1)
        ],
    }


@mcp.tool()
def leggi_allegato_testo(
    account: str,
    message_id: str,
    filename: Optional[str] = None,
    indice: Optional[int] = None,
    pagina_da: int = 1,
    pagina_a: int = 0,
    max_caratteri: int = MAX_CARATTERI_DIFETTO,
) -> dict:
    """
    Rende il TESTO di un allegato PDF o di un file di testo.

    E' lo strumento da usare per sapere cosa c'e' scritto in un documento
    ricevuto (una fattura, una dichiarazione doganale, un contratto)
    senza farsi mandare il file intero.

    account: la casella che possiede il messaggio
    message_id: l'id del messaggio, ottenuto da list_emails
    filename: il nome dell'allegato, anche parziale. Facoltativo se il
        messaggio ne porta uno solo.
    indice: in alternativa al nome, il numero d'ordine reso da
        elenca_allegati.
    pagina_da, pagina_a: intervallo di pagine di un PDF, 1 e 0 per
        tutto il documento. Servono a riprendere la lettura di un
        documento lungo senza ripetere le pagine gia' lette.
    max_caratteri: tetto della risposta, 40000 per difetto.

    La risposta dice sempre quante pagine ha il documento, quante ne
    portano del testo e quali no. Una pagina senza testo e' una pagina
    scansionata: qui non c'e' riconoscimento ottico, il suo contenuto si
    guarda passando da allegato_base64.
    """
    service = _gmail_service(account)
    allegati = _elenco(service, message_id)
    voce = _scegli(allegati, filename, indice)
    dati = _dati(service, message_id, voce)

    nome_minuscolo = voce["nome"].lower()
    risposta = {
        "nome": voce["nome"],
        "tipo_mime": voce["tipo_mime"],
        "octets": len(dati),
    }

    if nome_minuscolo.endswith(".pdf") or voce["tipo_mime"] == "application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Lettura PDF non disponibile: manca pypdf nell'immagine."
            ) from exc

        lettore = PdfReader(io.BytesIO(dati))
        totale = len(lettore.pages)
        prima = max(1, pagina_da)
        ultima = totale if pagina_a in (0, None) else min(totale, pagina_a)

        pezzi = []
        con_testo = []
        senza_testo = []
        for numero in range(prima, ultima + 1):
            try:
                testo = lettore.pages[numero - 1].extract_text() or ""
            except Exception:  # pragma: no cover - PDF malformato
                testo = ""
            if testo.strip():
                con_testo.append(numero)
                pezzi.append(f"--- pagina {numero} ---\n{testo.strip()}")
            else:
                senza_testo.append(numero)

        testo_intero = "\n\n".join(pezzi)
        risposta.update(
            {
                "pagine": totale,
                "pagine_lette": f"{prima} a {ultima}",
                "pagine_con_testo": con_testo,
                "pagine_scansionate_senza_testo": senza_testo,
                "testo": testo_intero[:max_caratteri],
                "troncato": len(testo_intero) > max_caratteri,
            }
        )
        if senza_testo and not con_testo:
            risposta["nota"] = (
                "Nessuna pagina porta uno strato di testo: documento interamente "
                "scansionato. Per vederlo, usare allegato_base64."
            )
        return risposta

    if nome_minuscolo.endswith(_ESTENSIONI_TESTO) or voce["tipo_mime"].startswith("text/"):
        testo = dati.decode("utf-8", errors="replace")
        risposta.update(
            {
                "testo": testo[:max_caratteri],
                "troncato": len(testo) > max_caratteri,
            }
        )
        return risposta

    raise ValueError(
        f"'{voce['nome']}' non e' un PDF ne' un file di testo "
        f"(tipo {voce['tipo_mime'] or 'sconosciuto'}). "
        "Per il contenuto grezzo, usare allegato_base64."
    )


@mcp.tool()
def allegato_base64(
    account: str,
    message_id: str,
    filename: Optional[str] = None,
    indice: Optional[int] = None,
) -> dict:
    """
    Rende il CONTENUTO GREZZO di un allegato, in base64.

    Da usare solo quando il file serve davvero: una pagina scansionata
    da guardare, un documento da rimandare altrove, un formato che
    leggi_allegato_testo non sa leggere. Per sapere cosa c'e' scritto in
    un PDF, leggi_allegato_testo costa molto meno.

    account: la casella che possiede il messaggio
    message_id: l'id del messaggio, ottenuto da list_emails
    filename: il nome dell'allegato, anche parziale. Facoltativo se il
        messaggio ne porta uno solo.
    indice: in alternativa al nome, il numero d'ordine reso da
        elenca_allegati.

    Il tetto e' di 12 Mo per allegato.
    """
    service = _gmail_service(account)
    allegati = _elenco(service, message_id)
    voce = _scegli(allegati, filename, indice)

    if voce["octets"] > MAX_BASE64_BYTES:
        raise ValueError(
            "Allegato troppo pesante per essere restituito in base64: "
            "{:.1f} Mo, il tetto e' {:.0f} Mo.".format(
                voce["octets"] / 1048576, MAX_BASE64_BYTES / 1048576
            )
        )

    dati = _dati(service, message_id, voce)
    return {
        "nome": voce["nome"],
        "tipo_mime": voce["tipo_mime"],
        "octets": len(dati),
        "content_base64": base64.b64encode(dati).decode(),
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
