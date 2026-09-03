"""O ranking geral: todo mundo, com o reino ao lado do nome.

Blueprint próprio para o portão ser um só. A visibilidade e o login são
checados em ``before_request``, e não rota a rota — com a checagem repetida em
cada uma, a rota nova esquecida vira a porta aberta.

A conta das posições não mora aqui: é :func:`vavacoin.ranking.ranquear`, a
mesma que o ranking do reino usa. Duas implementações da mesma regra de
privacidade divergem, e o dia em que divergirem é o dia em que vaza.
"""

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required

from ..modelos import CHAVE_RANKING_VISIVEL, config_ligada
from ..ranking import gente, ranquear, reinos_de

bp = Blueprint("ranking", __name__)


@bp.before_request
@login_required
def exigir_pessoa_e_interruptor():
    """Ranking é para quem está na economia, e só com o interruptor ligado.

    Fora do login não vê nada: "público" aqui significa "quem tem conta vê",
    que é bem diferente de aberto na web e indexável.

    O Banco Central atravessa sempre — é ele quem liga o interruptor, e
    precisa conferir a tela antes de mostrá-la para a turma.
    """
    if not config_ligada(CHAVE_RANKING_VISIVEL, padrao=True) and not current_user.eh_admin:
        abort(404)
    return None


@bp.route("/ranking")
def geral():
    """Todo mundo, ordenado por saldo, com o reino atual ao lado do nome.

    O reino mostrado é o **de agora**, não o congelado nas rodadas do cassino:
    aqui é rótulo de tela, e não conta de imposto.
    """
    pessoas = gente()
    ranking, escondidos = ranquear(pessoas)
    return render_template(
        "ranking.html",
        ranking=ranking,
        escondidos=escondidos,
        reinos=reinos_de(pessoas),
    )
