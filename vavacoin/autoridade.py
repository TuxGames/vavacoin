"""Quem pode o quê.

O Banco Central é a autoridade do jogo: emite convite, cria conta e executa o
reset. Não existe outro papel de administrador.

O incômodo disso é conhecido e está registrado no CLAUDE.md: o BC é ao mesmo
tempo uma conta com dinheiro e o poder administrativo, então **quem entrar
nele é dono de tudo**. A resposta tem duas metades, e as duas moram no código:

1. O BC **não autentica**. Não tem senha (o banco recusa que tenha), não é
   ``is_active``, o ``user_loader`` o descarta e o ``get_id()`` estoura. Não
   há tela por onde entrar nele.
2. Os poderes são **pedidos explicitamente**, por :func:`exigir_banco_central`.
   Não basta conseguir chamar a função: é preciso passar o BC. Assim o poder
   fica visível na assinatura em vez de implícito em quem tem o import.

Na prática isso deixa os poderes só na CLI, que exige acesso ao servidor.
"""

from .erros import SemAutoridade
from .extensoes import db
from .modelos import Usuario


def exigir_banco_central(autoridade, sessao=None):
    """Confere que ``autoridade`` é o Banco Central; devolve a conta dele.

    Recusa ``None`` de propósito, em vez de assumir o BC por conveniência:
    uma operação privilegiada que se autoriza sozinha quando ninguém pediu é
    exatamente o buraco que se quer evitar.
    """
    sessao = sessao or db.session
    if autoridade is None:
        raise SemAutoridade(
            "operação do Banco Central chamada sem autoridade; "
            "passe autoridade=banco_central()"
        )
    if isinstance(autoridade, int):
        autoridade = sessao.get(Usuario, autoridade)
    if not isinstance(autoridade, Usuario):
        raise SemAutoridade(f"autoridade inválida: {autoridade!r}")
    if not autoridade.eh_banco_central:
        raise SemAutoridade(
            f"{autoridade.nome_usuario} não é o Banco Central; "
            "não existe outro papel de administrador"
        )
    return autoridade
