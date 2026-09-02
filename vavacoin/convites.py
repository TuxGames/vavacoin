"""O link de convite.

**Conveniência em cima do código, não um segundo mecanismo.** O link é a
própria tela de cadastro com o código no caminho: mesma tabela ``Convite``,
mesmo ``cadastrar_por_convite()``, mesma regra de uso único. Não existe token
de link, nem validade separada, nem nada que o resgate precise saber. Quem
recebeu o código solto continua digitando no campo e chega no mesmo lugar.

O domínio nunca é escrito aqui. Dentro de uma requisição, quem manda é o host
por onde a pessoa está navegando — se um dia o site mudar de endereço, o link
muda junto sem ninguém lembrar de trocar uma constante. Fora de requisição
(a CLI) não há host nenhum para perguntar, e aí vale ``BASE_URL``, que sai da
variável de ambiente ``VAVACOIN_BASE_URL``.
"""

from flask import current_app, has_request_context, url_for


def link_de_convite(codigo):
    """URL absoluta da tela de cadastro já com ``codigo`` preenchido.

    O caminho sai do ``url_for``, e não de uma string montada à mão, para que
    mudar a rota do cadastro mude o link junto — um link de convite que aponta
    para uma rota que não existe mais é o tipo de erro que só aparece quando
    alguém já mandou o link para a turma.
    """
    if has_request_context():
        return url_for("auth.cadastro", codigo=codigo, _external=True)

    # Sem requisição (CLI): empresta um contexto só para o `url_for` saber
    # montar a URL absoluta a partir do endereço configurado.
    with current_app.test_request_context(base_url=current_app.config["BASE_URL"]):
        return url_for("auth.cadastro", codigo=codigo, _external=True)
