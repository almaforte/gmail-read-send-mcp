# Regole di composizione dei messaggi

Le regole obbligatorie (html_body sempre obbligatorio, niente trattini
lunghi, niente firma o chiusura scritta a mano duplicata, una sola riga
vuota tra paragrafi e prima della firma) non vivono piu' in questo
repository: sono state estratte il 30.08.2026 nel pacchetto condiviso
[gmail-message-rules](https://github.com/almaforte/gmail-message-rules),
installato da `requirements.txt` sempre dall'ultima versione di `main`.

Questo repository (`gmail_send_mcp.py`) importa `build_message` da quel
pacchetto e gli passa le firme specifiche di queste caselle
(`am.forte@almaval.ch`, `gestion@almaval.ch`, `info@corsalis.ch`,
`endolift@corsalis.ch`, `forte.albertomaria@gmail.com`) e il logo
Corsalis inline: non implementa piu' le regole stesse. La storia
completa di ciascuna regola (perche' esiste, quali bug ha risolto, quali
limiti ha) resta documentata in
[CONVENTIONS.md di gmail-message-rules](https://github.com/almaforte/gmail-message-rules/blob/main/CONVENTIONS.md).

## Perche' la migrazione

Le stesse regole erano nate qui, scritte due volte in una manciata di
giorni perche' un primo tentativo copriva solo parte del problema (vedi
la cronologia in gmail-message-rules/CONVENTIONS.md sul bug della doppia
riga vuota prima della firma). Un secondo repository di Alberto che
invia email da un'altra casella avrebbe dovuto duplicare la stessa
logica, con lo stesso rischio di scoprire gli stessi bug una seconda
volta. Estrarre le regole in un pacchetto condiviso, installato sempre
dall'ultima versione di `main`, significa che un miglioramento futuro (una
nuova formula di chiusura da riconoscere, un caso di spaziatura non
ancora coperto) si applica qui e in ogni altro repository di Alberto che
invia email, senza toccare questo file.

## Cosa resta specifico di questo repository

- Le firme delle cinque caselle (`SIGNATURES_TEXT` / `SIGNATURES_HTML`
  in `gmail_send_mcp.py`), incluse le tre varianti di
  `endolift@corsalis.ch` (`logistics`, `accounting`, `medical`).
- Il contenuto della firma di `gestion@almaval.ch`, che sta in
  `signatures_gestion.py` perche' e' una tabella completa con logo,
  pittogrammi e social: in mezzo agli altri dizionari renderebbe
  illeggibile la parte che conta, cioe' quale casella ha quale firma.
- Il logo Corsalis allegato in linea (Content-ID) alle firme di
  `info@corsalis.ch` ed `endolift@corsalis.ch`.
- OAuth, la pagina `/setup`, e i tool MCP esposti
  (`send_email`, `create_draft`, `reply_email`, `list_emails`,
  `get_email`, `archive_email`, `mark_as_read`, `label_email`,
  `list_accounts`).

Per aggiungere o modificare una firma, vedi `SIGNATURES_TEXT` e
`SIGNATURES_HTML` in `gmail_send_mcp.py`: quella parte resta qui, non nel
pacchetto condiviso, perche' e' specifica di queste caselle.

## Due trappole, e sono costate entrambe qualcosa il 23.09.2026

**Una casella senza voce nei due dizionari invia messaggi nudi, in
silenzio.** Nessun errore, nessun avviso: semplicemente nessuna firma.
`gestion@almaval.ch` e' rimasta cosi' per settimane senza che nessuno
se ne accorgesse, ed e' stata scoperta solo rileggendo il codice HTML di
due messaggi gia' partiti. Per questo la pagina `/setup` elenca ora le
caselle che una firma ce l'hanno: una casella collegata ma assente da
quella lista e' un difetto visibile a colpo d'occhio invece che una
scoperta tardiva.

**Attenzione anche a chi pensasse di configurare la firma nelle
impostazioni Gmail della casella**: non serve a niente per questo
connettore. Gmail applica la firma delle impostazioni nella sua
interfaccia web, mai ai messaggi che partono dall'API, che e' la via di
`send_email`. Verificato con due invii di controllo a corpo nudo: zero
immagini, nessuna chiusura.

**Il Dockerfile copia i sorgenti UNO A UNO, non con `COPY . .`** Un
modulo nuovo aggiunto al repository ma non dichiarato nel Dockerfile non
finisce nell'immagine, e il servizio parte in CRASHED con
`ModuleNotFoundError`. La trappola non lascia alcuna traccia prima del
deploy: in locale `py_compile` passa senza dire niente, perche' l'import
fallisce solo a runtime. E' esattamente cosi' che l'aggiunta di
`signatures_gestion.py` ha messo il servizio fuori uso per qualche
minuto.
