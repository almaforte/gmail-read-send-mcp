"""
signatures_gestion.py

Firma ufficiale della casella gestion@almaval.ch, da cui partono i
messaggi dei robot Almaval e le risposte ai colleghi.

Perche' vive in un file suo. Le altre firme di questo connettore sono
poche righe e stanno bene dentro gmail_send_mcp.py. Questa e' una tabella
completa, con logo, pittogrammi e social: messa in mezzo agli altri
dizionari renderebbe illeggibile la parte che conta, cioe' quale casella
ha quale firma. gmail_send_mcp.py importa da qui e registra la firma nei
due dizionari come tutte le altre, quindi la convenzione del repository
resta valida.

Da dove viene la forma. Le firme Almaval sono prodotte da Signature Hound
e le loro immagini sono ospitate da lui. La struttura, il logo, i
pittogrammi teal 128da2, il filetto dorato #f7cb4d e i social sono quelli
delle altre caselle della casa. Nessuna immagine e' stata creata per
l'occasione.

LE DUE RIGHE DI INTESTAZIONE, e non una. Una prima versione portava
"Service de gestion administrative" su una riga sola: era una mia
invenzione, ricalcata sulla firma di formation@ che dice "Équipe
Formation". La firma vera, creata da Alberto nella casella, ne porta due:

    Département administratif
    Service de gestion

Sono il dipartimento e poi il servizio, cioe' la struttura di governance
della casa, non un nome unico. Un servizio non si chiama come il suo
dipartimento, e schiacciare i due livelli in una riga sola cancellava
proprio l'informazione che quelle righe portano.

Nessun grassetto su queste due righe: nella firma originale hanno lo
stesso peso delle righe di contatto. La firma di formation@ usa il
grassetto, ma non fa testo qui.

Il logo e' richiamato per URL e non allegato in linea. E' la stessa
scelta della firma di am.forte@almaval.ch in gmail_send_mcp.py: Gmail
mostra di default le immagini remote, e un allegato in linea
costringerebbe a passare per multipart/related anche per questa casella.

Nessun nome di persona. La casella parla a nome del servizio: mai un nome
proprio, nemmeno delle iniziali. La menzione "Validé par AMF", prevista
dalla carta dei robot dell'11.09.2026, e' stata tolta il 23.09.2026 per
questa ragione.

Stile. Le dimensioni e i colori interni alla tabella sono quelli di
Signature Hound e restano qui, perche' descrivono la tabella e non il
corpo del messaggio. Carattere, dimensione e colore del testo normale
arrivano invece da STYLE_DEFAULT in gmail_send_mcp.py, unica fonte di
verita' per il resto del messaggio.
"""

_SH = "https://cdn.signaturehound.com"
_SH_LOGO = f"{_SH}/users/43mcvhklnss78hz/88389069-042b-4926-9676-1db41af5cfdb.png"

_GRIS = "rgb(136,136,136)"
_GRIS_TITRE = "#7b7a7a"
_TEAL = "rgb(18,141,162)"

# Le due righe di intestazione, nell'ordine: prima il dipartimento, poi il
# servizio. Cambiare qui, non nel blocco HTML piu' sotto.
_INTESTAZIONE = ("Département administratif", "Service de gestion")


def _riga(icona: str, contenuto: str, href: str = "", grassetto: bool = False) -> str:
    """
    Una riga della firma: pittogramma a sinistra, testo a destra.

    href vuoto produce testo semplice invece di un link: serve per
    l'indirizzo postale, che non porta da nessuna parte.
    """
    colore = _TEAL if grassetto else _GRIS
    peso = "font-weight:700;" if grassetto else ""
    stile = f"font-size:11px;line-height:14px;white-space:nowrap;color:{colore};{peso}"
    testo = (
        f'<a href="{href}" style="{stile}text-decoration:none" target="_blank">{contenuto}</a>'
        if href
        else f'<span style="{stile}">{contenuto}</span>'
    )
    return (
        '<tr><td style="padding:1px 5px 1px 0;vertical-align:middle">'
        f'<p style="margin:1px"><img src="{_SH}/icons/{icona}_default_128da2.png" alt="" '
        'width="18" height="18" style="display:block;border:0;margin:0;width:18px;height:18px">'
        '</p></td><td style="line-height:14px;padding:1px 0;vertical-align:middle">'
        f'<p style="margin:1px">{testo}</p></td></tr>'
    )


def _social(icona: str, href: str) -> str:
    return (
        '<td width="30" style="font-size:0;line-height:0;padding:11px 1px 0 0">'
        f'<p style="margin:1px"><a href="{href}" target="_blank">'
        f'<img src="{_SH}/icons/{icona}_default_128da2.png" alt="" width="30" height="30" '
        'style="display:block;border:0;margin:0;width:30px;height:30px"></a></p></td>'
        '<td width="3" style="padding:0 0 1px"></td>'
    )


GESTION_SIGNATURE_TEXT = (
    "Cordialement,\n\n"
    + "\n".join(_INTESTAZIONE)
    + "\n\n"
    "gestion@almaval.ch\n"
    "Secrétariat : +41 21 525 35 14\n"
    "Secrétariat (mobile, aussi WhatsApp) : +41 76 702 78 69\n"
    "Castel de Bois Genoud, 1023 Crissier\n"
    "almaval.ch"
)

_INTESTAZIONE_HTML = "<br>".join(_INTESTAZIONE)

GESTION_SIGNATURE_HTML = (
    "Cordialement,<br><br>"
    '<table cellpadding="0" cellspacing="0"><tbody><tr><td style="padding:0 1px 0 0">'
    '<table cellpadding="0" cellspacing="0"><tbody><tr>'
    '<td align="center" style="padding:0 16px 0 0;vertical-align:top">'
    '<table cellpadding="0" cellspacing="0"><tbody><tr><td style="padding:0 1px 0 0">'
    '<p style="margin:1px"><a href="http://www.almaval.ch/" target="_blank">'
    f'<img src="{_SH_LOGO}" alt="Almaval" width="150" height="157" '
    'style="display:block;border:0;max-width:150px"></a></p>'
    "</td></tr></tbody></table></td>"
    '<td width="5" style="padding:1px 0 0"></td>'
    '<td style="padding:0 1px 0 0;vertical-align:top">'
    '<table cellpadding="0" cellspacing="0"><tbody>'
    '<tr><td style="padding:0 1px 9px 0;border-bottom:2px solid #f7cb4d;'
    'font-size:11px;line-height:14px;white-space:nowrap">'
    f'<p style="font-size:11px;line-height:14px;color:{_GRIS_TITRE};'
    f'margin:1px;white-space:nowrap">{_INTESTAZIONE_HTML}</p></td></tr>'
    '<tr><td style="padding:9px 1px 9px 0;border-bottom:2px solid #f7cb4d">'
    '<table cellpadding="0" cellspacing="0"><tbody>'
    + _riga("email", "gestion@almaval.ch", "mailto:gestion@almaval.ch")
    + _riga("phone", "Secrétariat : +41 21 525 35 14", "tel:+41215253514")
    + _riga("mobile", "Secrétariat : +41 76 702 78 69 (aussi WhatsApp)", "tel:+41767027869")
    + _riga("map", "Castel de Bois Genoud, 1023 Crissier")
    + _riga("website", "almaval.ch", "http://www.almaval.ch/", grassetto=True)
    + "</tbody></table></td></tr>"
    '<tr><td style="padding:0 1px 0 0">'
    '<table cellpadding="0" cellspacing="0" border="0"><tbody><tr>'
    + _social("facebook", "https://www.facebook.com/almavalpsy/")
    + _social("linkedin", "https://www.linkedin.com/company/almaval-psy")
    + "</tr></tbody></table></td></tr>"
    "</tbody></table></td></tr></tbody></table></td></tr></tbody></table>"
)
