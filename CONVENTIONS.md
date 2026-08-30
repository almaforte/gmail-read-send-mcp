# Regole di composizione dei messaggi

Queste regole sono imposte dal codice (`gmail_send_mcp.py`), non sono solo
una convenzione da ricordare a chi chiama gli strumenti. Documentate qui
perche' un vincolo capito e' piu' solido di un vincolo subito, e perche'
un futuro intervento sul codice deve sapere perche' certe righe esistono.

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

## Uno spazio solo tra il corpo e la firma

`SIGNATURES_TEXT` e `SIGNATURES_HTML` iniziano gia' con il proprio
distacco iniziale prima della formula di chiusura ("Cordialement,\n\n..."
in testo, "Cordialement,<br><br>..." in HTML). `_build_mime` unisce corpo
e firma con un solo `"\n"` / `"<br>"` di separazione, non con `"\n\n"` /
`"<br><br>"`: sommare un secondo separatore a quello gia' presente in
testa alla firma produce una doppia riga vuota visibile prima di
"Cordialement," (bug osservato il 26.08.2026 e di nuovo il 30.08.2026, su
una firma con logo intercalato che lo rendeva meno evidente a colpo
d'occhio). Se in futuro cambia il formato di una firma in
`SIGNATURES_TEXT`/`SIGNATURES_HTML`, va mantenuta la convenzione che la
firma stessa porta il proprio spazio di apertura, non chi la usa.

## Spaziatura tra i paragrafi del corpo: NON ancora imposta dal codice

Punto importante da capire bene, perche' e' diverso dai due precedenti: la
regola "una sola riga vuota tra un paragrafo e l'altro" per il momento
**non e'** un vincolo di `gmail_send_mcp.py`. Il connettore impone stile
tipografico (famiglia di carattere, dimensione, colore, tramite
`_inline_style`) ma non tocca margini, interlinea o spaziatura verticale:
non aggiunge `<br>` tra i paragrafi di `html_body`, non li toglie, non
normalizza in alcun modo quanti ce ne sono.

Questo significa che la spaziatura tra i paragrafi dipende interamente da
come chi scrive il messaggio costruisce `html_body`:

- Paragrafi scritti come `<p>...</p><p>...</p>` consecutivi vengono
  renderizzati da Gmail con la propria spaziatura verticale di default tra
  blocchi `<p>` (tipicamente equivalente a una riga vuota), senza bisogno
  di `<br>` espliciti tra un tag e l'altro.
- Aggiungere `<br><br>` tra un `</p>` e il `<p>` successivo, oltre al
  margine gia' applicato da Gmail ai blocchi `<p>`, produce una doppia riga
  vuota, lo stesso tipo di errore visto tra corpo e firma.
- Un `html_body` che usa `<br>` invece di `<p>` per separare i paragrafi
  (una sola stringa con `<br><br>` tra i blocchi di testo) e' un formato
  valido, ma la spaziatura risultante dipende dal numero di `<br>` messi a
  mano, non da una regola imposta dal connettore.

Finche' questa parte non diventa anch'essa un vincolo di codice (es. una
normalizzazione automatica in `_wrap_html`/`_inline_style` che forzi la
spaziatura tra blocchi `<p>` consecutivi indipendentemente da come sono
stati scritti), resta una convenzione di scrittura, non una garanzia
tecnica: chi compone `html_body` deve usare `<p>` per ogni paragrafo,
senza `<br>` aggiuntivi tra un `<p>` e il successivo, e verificare il
risultato prima di considerare una bozza pronta.

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
