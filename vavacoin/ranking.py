"""A ordenação por saldo, e a regra de privacidade que ela obriga.

Existe **uma implementação só** da conta de posições, e é :func:`ranquear`.
O ranking do reino e o ranking geral chamam a mesma função com listas
diferentes de gente.

Isso não é economia de linhas: duas implementações da mesma regra divergem, e
o dia em que divergirem é o dia em que vaza. Se um dia aparecer uma terceira
tela com ordenação, ela chama daqui também.

## Por que quem escondeu o saldo não é posicionado

**A posição vaza o valor.** Quem escondeu, aparecendo entre o terceiro e o
quinto, teria o número entre os dois vizinhos — e esconder deixaria de
esconder. Não existe posição discreta: qualquer lugar na ordem é uma
informação sobre quanto a pessoa tem.

Por isso o ranking é só de quem está público, com as posições contadas entre
eles: 1, 2, 3, sem buracos, porque um buraco onde alguém foi pulado também
contaria alguma coisa.

## A consequência, e o pedido para não "consertar"

A posição de quem é público **não é a posição real na turma** — os escondidos
não entram na conta, e quem é "primeiro" aqui pode ser terceiro de verdade. É
o preço de a escolha de esconder funcionar, e é deliberado.

Somar os escondidos de volta para "corrigir" as posições reintroduz
exatamente o vazamento que este desenho fecha. Se um dia alguém for tentado, é
este parágrafo que responde.

## Posição e número são coisas diferentes

Quem entra no ranking sai de ``saldo_publico`` e de mais nada — sem olhar
quem está vendo. Assim as posições são idênticas para todo mundo, e não há
computação por observador onde um vazamento possa se esconder: se o Banco
Central visse uma ordem e a turma visse outra, a diferença entre as duas diria
quem escondeu o quê.

O **número** continua governado pela regra única
(``Usuario.saldo_visivel_para``): o Banco Central e a própria pessoa veem o
saldo de quem escondeu, na parte de baixo. O que não existe para ninguém é a
**posição** de quem escondeu.
"""

from sqlalchemy import select

from .extensoes import db
from .modelos import Usuario


def ranquear(pessoas):
    """Divide as pessoas em ``(ranking, escondidos)``.

    ``ranking`` é uma lista de ``(posicao, pessoa)``, do maior saldo para o
    menor. ``escondidos`` é quem optou por não mostrar o saldo, em ordem
    alfabética e **sem posição**.

    Saldos iguais dividem a posição, e a seguinte continua da contagem — como
    em qualquer ranking. Empate não conta nada a mais: os dois saldos já estão
    à vista.
    """
    pessoas = list(pessoas)

    publicos = sorted(
        (p for p in pessoas if p.saldo_publico),
        key=lambda p: (-p.saldo, p.nome_normalizado),
    )
    ranking = []
    for indice, pessoa in enumerate(publicos):
        if indice and pessoa.saldo == publicos[indice - 1].saldo:
            posicao = ranking[-1][0]
        else:
            posicao = indice + 1
        ranking.append((posicao, pessoa))

    escondidos = sorted(
        (p for p in pessoas if not p.saldo_publico), key=lambda p: p.nome_normalizado
    )
    return ranking, escondidos


def gente(sessao=None):
    """Todas as contas que são pessoas, para o ranking geral.

    Fora: conta de sistema (Banco Central, cofre de reino, casa do cassino) e
    conta encerrada. Nenhuma das duas é gente com dinheiro na turma — a
    primeira é peça do sistema, e a segunda saiu.
    """
    sessao = sessao or db.session
    return [
        pessoa
        for pessoa in sessao.execute(
            select(Usuario).order_by(Usuario.nome_usuario)
        ).scalars()
        if not pessoa.eh_conta_de_sistema and not pessoa.encerrada
    ]


def reino_de(pessoa, sessao=None):
    """O reino **atual** da pessoa, ou ``None``.

    Atual, e não o congelado nas rodadas: aqui é rótulo de tela, não conta de
    imposto. Quem entrou ontem aparece no reino de hoje.
    """
    from .reinos import cidadania_de

    cidadania = cidadania_de(pessoa, sessao)
    return cidadania.reino if cidadania is not None else None


def reinos_de(pessoas, sessao=None):
    """``{id: reino ou None}`` para a lista inteira, numa consulta só.

    Uma consulta por pessoa transformaria a tela num festival de idas ao
    banco quando a turma crescer.
    """
    from .modelos import Cidadania, Reino

    sessao = sessao or db.session
    ids = [p.id for p in pessoas]
    if not ids:
        return {}

    achados = sessao.execute(
        select(Cidadania.usuario_id, Reino)
        .join(Reino, Reino.id == Cidadania.reino_id)
        .where(Cidadania.usuario_id.in_(ids), Cidadania.saiu_em.is_(None))
    ).all()
    por_pessoa = {usuario_id: reino for usuario_id, reino in achados}
    return {pessoa.id: por_pessoa.get(pessoa.id) for pessoa in pessoas}
