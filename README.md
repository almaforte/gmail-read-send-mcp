# Gmail Send MCP

Secondo connettore, da usare insieme a navbuildz/gmail-mcp-server. Quello
copre lettura, archiviazione, etichette e disiscrizione. Questo copre invio,
bozze e risposte, che navbuildz non offre ancora.

Le caselle si identificano per indirizzo email in entrambi i connettori,
cosi' puoi dire a Claude "rispondi dalla casella corsalis@..." e funziona
allo stesso modo sui due sistemi.

## Perche' un servizio separato invece di modificare navbuildz

Per integrare l'invio direttamente nel codice di navbuildz servirebbe
leggerne il sorgente esatto e riusarne le funzioni interne di gestione dei
token. Non e' stato possibile verificarlo con certezza dall'esterno, quindi
si e' scelto un servizio indipendente, completo e verificabile riga per
riga, che si affianca al primo senza rischiare di romperlo. Riusa lo stesso
progetto Google Cloud, quindi il lavoro di configurazione aggiuntivo resta
minimo.

## Passo 1: riusa il progetto Google Cloud di navbuildz

Nello stesso progetto Google Cloud gia' usato per navbuildz:

1. APIs & Services -> OAuth consent screen -> modifica gli ambiti e aggiungi,
   se non gia' presenti:
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email`
2. APIs & Services -> Credentials -> apri le stesse credenziali OAuth Web
   application usate per navbuildz, e aggiungi un secondo Authorized
   redirect URI (lo completerai al passo 3, per ora puoi lasciare un
   valore provvisorio come `https://placeholder/oauth/callback`).

Puoi riusare lo stesso Client ID e Client Secret di navbuildz: le scelte di
ambito si fanno per ogni autorizzazione, non sono fissate sul client.

## Passo 2: distribuisci questo servizio su Railway

Crea un **nuovo** servizio Railway, separato da quello di navbuildz, a
partire da questa cartella (repository Git o upload diretto). Imposta le
variabili d'ambiente:

| Variabile | Valore |
|---|---|
| `GOOGLE_CLIENT_ID` | lo stesso di navbuildz |
| `GOOGLE_CLIENT_SECRET` | lo stesso di navbuildz |
| `SERVER_URL` | la completi al passo 3 |
| `ADMIN_PASSWORD` | una password robusta, puo' essere diversa da quella di navbuildz |
| `FERNET_KEY` | genera con: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `PORT` | `3001` |

Nota: `FERNET_KEY` non e' la stessa cosa della `ENCRYPTION_KEY` di navbuildz,
non riusare quel valore qui, genera una chiave nuova con il comando sopra.

## Passo 3: genera il dominio e allinea il redirect URI

Come fatto per navbuildz: Service Settings -> Networking -> Generate
Domain. Aggiorna `SERVER_URL` con questo dominio, e torna in Google Cloud
Console a sostituire il redirect URI provvisorio del passo 1 con
`https://<questo-dominio>/oauth/callback`.

## Passo 4: collega ogni casella

Visita `https://<questo-dominio>/setup`, autenticati con `ADMIN_PASSWORD`
(HTTP Basic Auth, il browser chiedera' utente e password: lascia vuoto lo
username), clicca "Collega una nuova casella" e autorizza con l'account
Google corretto. Ripeti per ciascuna delle tue caselle.

Questo e' un secondo consenso Google per ogni casella, distinto da quello
gia' dato a navbuildz, perche' i permessi richiesti sono diversi (qui invio
e risposta, la' lettura e gestione).

Dopo aver collegato le caselle, copia il contenuto mostrato nella casella di
testo della pagina `/setup` e incollalo come variabile d'ambiente
`TOKENS_DATA` su Railway, altrimenti un redeploy fa perdere le connessioni.

## Passo 5: aggiungi il secondo connettore in Claude

In Impostazioni -> Connettori -> Aggiungi connettore personalizzato, con un
nome diverso dal primo, per esempio "Gmail Almaval - Invio". URL:
`https://<questo-dominio>/mcp`. Campi OAuth lasciati vuoti, come per
navbuildz.

## Uso

Ogni strumento accetta l'indirizzo della casella mittente:

- *"Invia una email da corsalis@... a fornitore@... con oggetto X"*
- *"Rispondi a quell'ultima email nella casella almaval@..., anche a tutti"*
- *"Crea una bozza da delitiae@... senza inviarla"*

## Sicurezza

Stesse cautele indicate per navbuildz: l'endpoint `/mcp` non richiede
un'autenticazione ulteriore oltre alla conoscenza dell'URL, trattalo come un
segreto. Solo `/setup` e' protetta da password. I token sono cifrati a
riposo con Fernet, ma chi ha accesso a `FERNET_KEY` e al contenuto salvato
puo' decifrarli: conserva `FERNET_KEY` con la stessa cura di una password
amministrativa.
