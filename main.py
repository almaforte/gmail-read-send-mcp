"""
main.py

Punto di ingresso unico del servizio.

Importa l'app FastAPI da gmail_send_mcp, poi ogni modulo che registra
strumenti aggiuntivi sulla stessa istanza FastMCP. FastMCP rilegge il
catalogo degli strumenti a ogni richiesta, quindi registrarli dopo la
creazione dell'app e' del tutto regolare.

Perche' esiste questo file. Prima l'avvio passava da
gmail_drafts_tools.py, che importava gmail_send_mcp: comodo finche' i
moduli erano due, ma ogni nuova famiglia di strumenti avrebbe dovuto
innestarsi sul modulo delle bozze, senza averci nulla a che vedere.
Adesso la regola e' semplice: si crea un file gmail_*_tools.py, lo si
aggiunge alla lista qui sotto e al Dockerfile, e non si tocca nient'altro.

Attenzione: se il servizio Railway ha una start command esplicita, deve
puntare a questo file. Se punta ancora a gmail_drafts_tools.py, i moduli
non elencati li' non vengono caricati e i loro strumenti spariscono dal
catalogo senza alcun errore visibile.
"""

import os

from gmail_send_mcp import app  # crea l'app FastAPI e l'istanza FastMCP
import gmail_drafts_tools  # noqa: F401 - registra gli strumenti delle bozze
import gmail_forward_tools  # noqa: F401 - registra gli strumenti di inoltro

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 3001)),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
