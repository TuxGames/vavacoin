"""Cabeçalhos de segurança.

A CSP é restrita **desde a primeira rota**, de propósito. Afrouxar uma CSP
depois é fácil; apertar uma CSP num site que já encheu de ``style=""`` e
``onclick=""`` é retrabalho — e é sempre adiado. Nascer sem essas duas coisas
custa zero.

Nada de ``'unsafe-inline'`` em lugar nenhum: todo estilo vive em
``static/estilo.css`` e não existe uma linha de JavaScript no projeto.
"""

POLITICA = "; ".join(
    [
        # Nada carrega de fora. Não há CDN, fonte externa nem analytics.
        "default-src 'self'",
        # Sem script inline e sem script de terceiro. Hoje não há script nenhum.
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        # Formulário só posta para o próprio site: fecha o caminho de um
        # formulário injetado mandar dados (ou uma transferência) para fora.
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ]
)

CABECALHOS = {
    "Content-Security-Policy": POLITICA,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # O site não usa nenhuma dessas capacidades; desligar é de graça.
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
}


def aplicar_cabecalhos(resposta):
    for nome, valor in CABECALHOS.items():
        resposta.headers.setdefault(nome, valor)
    return resposta
