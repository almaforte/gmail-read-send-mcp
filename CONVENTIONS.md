# Regole di composizione dei messaggi

Queste regole sono imposte dal codice (`gmail_send_mcp.py`), non sono solo
una convenzione da ricordare a chi chiama gli strumenti. Documentate qui
perche' un vincolo capito e' piu' solido di un vincolo subito, e perche'
un futuro intervento sul codice deve sapere perche' certe righe esistono.

## Niente trattini lunghi, mai

Ne' nel testo dei messaggi (oggetto, corpo, firma), ne' nei titoli o nel
testo di questo repository (README, questo file, messaggi di commit).
Solo trattino corto normale "-", mai il trattino lungo "em dash" o il
trattino medio "en dash". Se serve una pausa o un inciso, si usa una
virgola, due punti, o una frase separata, non un trattino lungo.

Regola esplicita di Alberto (30.08.2026), dopo averla vista comparire nel
titolo di una bozza di prova. E' imposta a livello di codice da
`_strip_long_dashes`, chiamata all'inizio di `_build_mime` su `subject`,
`body` e `html_body` prima di qualunque altra elaborazione: ogni em dash
(—), en dash (–) o trattino orizzontale (―) viene sostituito con un
trattino corto "-" in automatico e in silenzio, senza bloccare la
chiamata, con la stessa logica gia' usata per `_strip_manual_closing`. Per
il testo di questo repository (README, questo file, messaggi di commit)
resta invece una regola di scrittura da rispettare a mano, perche' quel
testo non passa da `_build_mime`.

## html_body e' obbligatorio

`send_email`, `create_draft` e `reply_email` richiedono sempre `html_body`.
Non esiste piu' un percorso che invii un messaggio in solo testo semplice:
se manca, `_build_mime` solleva un `ValueError` esplicito prima di
costruire qualsiasi cosa.

Motivo: un messaggio in testo semplice, su queste caselle, e' sempre un
incidente, mai una scelta deliberata. Rendere il campo opzionale lasciava
la porta aperta a un client che se ne dimentica; renderlo obbligatorio
sposta l'errore al momento della chiamata, dove e' visibile e correggibile
subito, invece che nel messaggio gia' arrivato al destinatario.

`html_body` deve essere HTML vero (paragrafi `<p>`, `<strong>` per
l'enfasi, `<ol>`/`<ul>` per gli elenchi), non testo semplice avvolto in un
unico tag. Lo stile (famiglia di carattere, dimensione, colore) e' imposto
dal connettore stesso via `STYLE_DEFAULT` / `STYLE_OVERRIDES`: chi scrive
il messaggio non deve indicarlo.

## Nessuna firma o formula di chiusura scritta a mano

La firma ufficiale di ogni casella (`SIGNATURES_TEXT` / `SIGNATURES_HTML`)
viene aggiunta automaticamente in coda al messaggio da `_build_mime`. Chi
chiama gli strumenti non deve mai scrivere una formula di chiusura
("Cordialement, Alberto", "Bien cordialement, Dr Forte...", "Bests,
Alberto") nel corpo del messaggio: se lo fa, il messaggio finale
mostrerebbe due chiusure in fila, quella scritta a mano seguita da quella
vera.

Per evitare che questo dipenda dalla disciplina di chi scrive il messaggio
ogni singola volta, `_build_mime` applica `_strip_manual_closing` prima di
appendere la firma: rileva un pattern di chiusura nota ("Cordialement",
"Bien cordialement", "Bests", "Best regards", "Kind regards", "Regards",
"Cordiali saluti", "Distinti saluti", "Un cordiale saluto", con o senza
maiuscola iniziale) quando compare nell'ultimo quarto del messaggio, e
taglia da li' in poi, sia nel testo semplice sia nell'HTML. La rimozione e'
automatica e silenziosa: non blocca la chiamata, non chiede conferma,
semplicemente consegna un messaggio corretto.

Il controllo si applica solo alle caselle che hanno effettivamente una
firma configurata (`_has_signature_configured`): su una casella senza
firma gestita non c'e' rischio di doppione, quindi il testo non viene
toccato.

Limiti noti di questa euristica, da tenere a mente se va estesa:

- Copre le formule di chiusura effettivamente in uso su queste caselle
  (francese, italiano, inglese) piu' pochi equivalenti comuni. Una formula
  non elencata in `_CLOSING_PHRASES` non viene riconosciuta.
- Si applica solo se la formula compare nell'ultimo quarto del testo, per
  non tagliare per errore un paragrafo che cita "cordialement" a meta'
  messaggio per altri motivi. Una chiusura scritta molto piu' in alto nel
  messaggio (raro, ma possibile) non verrebbe rimossa.
- E' un'euristica testuale, non una comprensione del contenuto: resta
  possibile costruire un messaggio che la elude. Non e' pensata come
  misura di sicurezza, solo come rete di protezione contro l'errore piu'
  comune osservato in pratica.

## Una sola riga vuota ovunque: tra i paragrafi, e tra il corpo e la firma

Regola generale: qualunque tag di blocco (`<p>`, `<div>`, `<ul>`, `<ol>`,
`<table>`, i titoli, `<blockquote>`) porta gia' con se' il proprio margine
verticale in Gmail. Ogni `<br>` messo subito prima o subito dopo un tag di
blocco e' quindi ridondante e produce una riga vuota in piu' rispetto a
quella che il tag stesso gia' fornisce. Questo principio unico e' applicato
in due punti del codice, entrambi dentro `_wrap_html`/`_build_mime`.

**Tra i paragrafi del corpo.** `_wrap_html` chiama
`_normalize_paragraph_spacing` prima di `_inline_style` (aggiunto il
30.08.2026). Questa funzione toglie ogni `<br>` (o sequenza di `<br>`) che
si trova direttamente tra due tag di blocco di primo livello nel frammento
HTML. Un `<br>` messo a mano tra un `</p>` e il `<p>` successivo si somma
al margine gia' applicato da Gmail e produce una doppia riga vuota. La
normalizzazione non tocca un `<br>` che sta tra testo semplice e un tag di
blocco, ne' un `<br>` dentro testo non strutturato senza tag attorno.

**Tra il corpo e la firma.** Qui la storia e' stata piu' lunga. Un primo
fix (26.08.2026, poi rivisto il 30.08.2026) aveva ridotto la separazione
da `"\n\n"`/`"<br><br>"` a un singolo `"\n"`/`"<br>"`, ragionando che le
firme in `SIGNATURES_TEXT`/`SIGNATURES_HTML` iniziano gia' con il proprio
distacco prima della formula di chiusura ("Cordialement,\n\n..."). Quel
fix ha risolto il caso di due separatori sommati, ma non il problema di
fondo: quando `html_body` finisce con un tag di blocco (`</p>`, `</div>`,
...), quel tag porta gia' il proprio margine inferiore, quindi anche un
solo `<br>` subito dopo produce comunque una riga vuota in piu'. Il bug e'
ricomparso il 30.08.2026 (schermata di Alberto su una bozza di prova) con
due righe vuote visibili prima di "Cordialement,", nonostante il primo fix
fosse gia' attivo.

La correzione definitiva e' `_ends_with_block_tag`: verifica se l'ultimo
nodo di primo livello di `html_body` (spazi bianchi a parte) e' un tag di
blocco. `_build_mime` la usa per decidere il separatore verso la firma:
stringa vuota se `html_body` finisce in un tag di blocco (il margine del
tag e' gia' sufficiente), il `<br>` esplicito solo se `html_body` finisce
in testo semplice senza tag di blocco (in quel caso serve ancora un ritorno
a capo esplicito). Lo stesso principio delle due regole: il margine di un
tag di blocco non va mai sommato a un `<br>` aggiuntivo nello stesso punto.

## Perche' queste regole sono nel codice e non solo qui

Prima di questa versione, le stesse due regole vivevano solo come
preferenza salvata nella memoria di Claude (file utente, non in questo
repository). Si sono rivelate insufficienti: una preferenza va riletta
attivamente prima di ogni azione perche' faccia effetto, e in almeno
un'occasione (30.08.2026) non lo e' stata, producendo due volte di seguito
lo stesso errore (testo semplice invece di HTML, poi firma duplicata).

Un vincolo nel codice del connettore non dipende dal fatto che chi chiama
lo strumento se ne ricordi: si applica sempre, a ogni chiamata, da
qualunque client MCP arrivi. E' la differenza tra chiedere a qualcuno di
non dimenticarsi un passaggio e rendere quel passaggio impossibile da
saltare.

Nota su questa stessa sezione: anche il fix del 26.08.2026 sulla
spaziatura corpo/firma sembrava corretto al momento, verificato con una
simulazione locale, ma copriva solo una delle due cause possibili dello
stesso sintomo visibile. La lezione pratica: un fix su un problema di
resa visiva (spaziatura, doppioni) va verificato guardando l'email
davvero renderizzata in Gmail, non solo il testo grezzo prodotto dal
codice, perche' il rendering finale dipende anche dal comportamento di
Gmail sui tag di blocco, che il solo output testuale non mostra.
