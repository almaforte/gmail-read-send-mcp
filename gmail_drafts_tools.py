"""
gmail_drafts_tools.py

Punto di ingresso del servizio, e sede degli strumenti di gestione delle
bozze: elenco, cancellazione di una singola bozza, pulizia delle bozze di
un thread.

Perche' un file a parte. Il modulo gmail_send_mcp.py resta la sede di
OAuth, firme e strumenti di invio, ed e' gia' lungo. Gli strumenti che
seguono si limitano a registrarsi sulla stessa istanza FastMCP importata
da li'. FastMCP legge il catalogo degli strumenti a ogni richiesta, quindi
registrarli dopo la creazione dell'app e' del tutto regolare, e questo
file puo' crescere senza toccare l'altro.

Ambito OAuth. La cancellazione di una bozza rientra in gmail.compose, gia'
concesso da tutte le caselle collegate, quindi non serve alcuna nuova
autorizzazione. Il cestino di un messaggio ricevuto o inviato, invece,
richiederebbe gmail.modify, che non e' tra gli SCOPES di questo servizio:
per questo qui non esiste un tool che cestina le email, solo le bozze.

Attenzione: la cancellazione di una bozza e' definitiva, la bozza non
passa dal cestino.
"""

import os
from typing import Optional

from gmail_send_mcp import app, mcp, _gmail_service


def _elenco_bozze(service, query: str = "", max_results: int = 50) -> list[dict]:
    """
    Restituisce le voci grezze dell'elenco bozze, ciascuna con il proprio
    id di bozza e il messaggio associato (id e threadId).
    """
    resp = service.users().drafts().list(
        userId="me",
        q=query or None,
        maxResults=max(1, min(max_results, 100)),
    ).execute()
    return resp.get("drafts", [])


def _intestazioni_messaggio(service, message_id: str) -> dict:
    msg = service.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["To", "Cc", "Subject", "Date"],
    ).execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    return {
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "snippet": msg.get("snippet", ""),
        "threadId": msg.get("threadId", ""),
    }


@mcp.tool()
def list_drafts(account: str, query: str = "", max_results: int = 20) -> list[dict]:
    """
    Elenca le bozze di una casella collegata, con l'id di bozza necessario
    a cancellarle e il thread in cui si trovano.

    account: indirizzo della casella (vedi list_accounts)
    query: filtro di ricerca Gmail applicato alle bozze, es. "to:mario"
        oppure "subject:fattura". Vuoto per tutte le bozze.
    max_results: numero massimo di bozze da restituire (limite 100)
    """
    service = _gmail_service(account)
    risultati = []
    for voce in _elenco_bozze(service, query, max_results):
        messaggio = voce.get("message", {}) or {}
        dettaglio = {
            "draft_id": voce["id"],
            "message_id": messaggio.get("id", ""),
            "threadId": messaggio.get("threadId", ""),
        }
        if messaggio.get("id"):
            dettaglio.update(_intestazioni_messaggio(service, messaggio["id"]))
        risultati.append(dettaglio)
    return risultati


@mcp.tool()
def delete_draft(
    account: str,
    draft_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> dict:
    """
    Cancella una bozza in modo definitivo. La bozza non passa dal cestino,
    quindi non e' recuperabile: da usare per le versioni scartate di un
    testo, mai su una bozza che qualcuno potrebbe ancora voler leggere.

    account: la casella a cui appartiene la bozza
    draft_id: l'id della bozza, quello restituito da list_drafts o da
        create_draft
    message_id: in alternativa, l'id del messaggio contenuto nella bozza;
        il tool risale da solo alla bozza corrispondente

    Va passato almeno uno dei due identificatori.
    """
    if not draft_id and not message_id:
        raise ValueError("Serve almeno 'draft_id' oppure 'message_id'.")

    service = _gmail_service(account)

    if not draft_id:
        for voce in _elenco_bozze(service, max_results=100):
            if (voce.get("message", {}) or {}).get("id") == message_id:
                draft_id = voce["id"]
                break
        if not draft_id:
            raise ValueError(
                f"Nessuna bozza trovata per il messaggio '{message_id}' in {account}."
            )

    service.users().drafts().delete(userId="me", id=draft_id).execute()
    return {"draft_id": draft_id, "stato": "bozza cancellata definitivamente"}


@mcp.tool()
def delete_drafts_in_thread(
    account: str,
    thread_id: str,
    keep_draft_id: Optional[str] = None,
) -> dict:
    """
    Cancella tutte le bozze presenti in un thread, tranne eventualmente
    quella da conservare. Serve a ripulire un thread dove si sono
    accumulate piu' versioni della stessa risposta, tenendo solo l'ultima.

    account: la casella a cui appartengono le bozze
    thread_id: l'id del thread, restituito da list_emails, list_drafts o
        create_draft
    keep_draft_id: id della bozza da NON cancellare, tipicamente quella
        appena creata. Se omesso, vengono cancellate tutte le bozze del
        thread.

    La cancellazione e' definitiva, le bozze non passano dal cestino.
    """
    service = _gmail_service(account)

    cancellate = []
    for voce in _elenco_bozze(service, max_results=100):
        messaggio = voce.get("message", {}) or {}
        if messaggio.get("threadId") != thread_id:
            continue
        if keep_draft_id and voce["id"] == keep_draft_id:
            continue
        service.users().drafts().delete(userId="me", id=voce["id"]).execute()
        cancellate.append(voce["id"])

    return {
        "threadId": thread_id,
        "bozze_cancellate": cancellate,
        "conservata": keep_draft_id or None,
        "stato": f"{len(cancellate)} bozze cancellate definitivamente",
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
