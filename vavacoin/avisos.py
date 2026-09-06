"""Avisos do reino: o operador escreve uma vez, os cidadãos leem.

Pedido do Bento, operador de Alfheim: um jeito de falar com o reino inteiro de
uma vez. É a primeira coisa do projeto que uma pessoa escreve e outras vinte
leem — daí os cuidados abaixo, que não são sobre dinheiro e mesmo assim são
sobre confiança.

## Quem vê o quê, e por que quem chega depois não vê o de antes

**O aviso é do momento.** Quem entra no reino hoje não recebe o recado de
semana passada: aquele foi escrito para as pessoas que estavam lá, e chegar
atrasado numa conversa não é o mesmo que ter participado dela. Recuperar
histórico é outra feature, com outra pergunta ("o que já foi dito aqui?") e
outra tela.

Isso sai de graça do modelo, sem tabela de entrega: o aviso vale para quem
tinha cidadania ativa quando ele foi escrito, e a cidadania já guarda
``entrou_em``. Quem sai para de ver na hora, porque a cidadania deixa de estar
ativa. Quem sai e volta entra como quem chega: cidadania nova, data nova.

**O operador vê todos os do reino**, mesmo os anteriores a ele e mesmo sem ser
cidadão — é ele quem administra a lista, e não daria para apagar o que não se
enxerga.

## Dispensar não é apagar

``AvisoVisto`` tira o aviso da **carteira** daquela pessoa e de mais ninguém.
Na página do reino ele continua lá: a carteira é por onde se passa, e aviso
que não some de lá vira paisagem em dois dias — junto com o próximo, que podia
importar. A página do reino é onde se vai olhar de propósito.

## O que não existe, e por quê

**Editar não existe.** Não é só preferência: com a dispensa por pessoa, editar
um aviso que metade da turma já dispensou trocaria o texto embaixo de quem já
leu, sem nenhum jeito de avisar de novo. Quem errou apaga e escreve outro — aí
quem tinha dispensado volta a ver, que é o comportamento honesto.

**Nada de HTML.** O texto é escapado na tela pelo Jinja. O teste que prova
isso manda uma tag e exige que ela apareça como letra.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .erros import ValorInvalido
from .extensoes import db
from .modelos import (
    TAMANHO_MAXIMO_DO_AVISO,
    AvisoDoReino,
    AvisoVisto,
    Cidadania,
    agora,
)

#: O mesmo limite do modelo, reexportado para a rota e a tela lerem de um
#: lugar só — o ``maxlength`` do campo e a recusa do servidor têm de ser o
#: mesmo número, senão a tela promete o que o servidor nega.
TAMANHO_MAXIMO = TAMANHO_MAXIMO_DO_AVISO


def _texto_valido(texto):
    """Limpa e confere. O limite é do servidor, não do campo na tela."""
    texto = (texto or "").strip()
    if not texto:
        raise ValorInvalido("o aviso não pode ser vazio")
    if len(texto) > TAMANHO_MAXIMO:
        raise ValorInvalido(
            f"o aviso passa de {TAMANHO_MAXIMO} caracteres ({len(texto)})"
        )
    return texto


def criar_aviso(reino, operador, texto, token=None, sessao=None):
    """Escreve um aviso do reino. Poder de quem tem o papel de operador.

    O ``token`` é UNIQUE no banco: o segundo clique bate no índice e não cria
    nada. É a mesma trava da distribuição, e existe aqui porque dois recados
    idênticos na tela de vinte pessoas ensinam a turma a ignorar o próximo.
    """
    from .reinos import exigir_operador

    sessao = sessao or db.session
    exigir_operador(reino, operador, sessao)
    texto = _texto_valido(texto)

    aviso = AvisoDoReino(
        reino_id=reino.id,
        autor_id=operador.id,
        texto=texto,
        token=token or _token_novo(),
    )
    sessao.add(aviso)
    try:
        sessao.flush()
    except IntegrityError as erro:
        sessao.rollback()
        raise ValorInvalido("esse aviso já foi enviado") from erro
    return aviso


def _token_novo():
    import secrets

    return secrets.token_urlsafe(16)


def _desde_quando(reino, pessoa, sessao, ids=None):
    """A partir de quando esta pessoa enxerga avisos deste reino.

    ``None`` quer dizer "todos" (é o operador). Uma exceção de leitura, não de
    poder: administrar a lista exige vê-la inteira.

    Levanta ``LookupError`` quando a pessoa não tem nada com o reino — a rota
    traduz isso no 404 dela.
    """
    from .reinos import eh_operador

    if eh_operador(reino, pessoa, sessao, ids):
        return None

    cidadania = sessao.execute(
        select(Cidadania).where(
            Cidadania.reino_id == reino.id,
            Cidadania.usuario_id == pessoa.id,
            Cidadania.saiu_em.is_(None),
        )
    ).scalar_one_or_none()
    if cidadania is None:
        raise LookupError("não é cidadão nem operador deste reino")
    return cidadania.entrou_em


def avisos_do_reino(reino, pessoa, sessao=None, ids=None):
    """Os avisos deste reino que **esta pessoa** pode ver, do novo ao velho."""
    sessao = sessao or db.session
    desde = _desde_quando(reino, pessoa, sessao, ids)

    consulta = select(AvisoDoReino).where(AvisoDoReino.reino_id == reino.id)
    if desde is not None:
        consulta = consulta.where(AvisoDoReino.criado_em >= desde)
    return list(sessao.execute(consulta.order_by(AvisoDoReino.criado_em.desc())).scalars())


def avisos_pendentes(pessoa, sessao=None):
    """O que aparece na carteira: os do reino da pessoa que ela não dispensou.

    Devolve lista vazia para quem não é de reino nenhum, em vez de estourar —
    a carteira é a tela de todo mundo.
    """
    sessao = sessao or db.session
    from .reinos import cidadania_de

    cidadania = cidadania_de(pessoa, sessao)
    if cidadania is None:
        return []

    vistos = set(
        sessao.execute(
            select(AvisoVisto.aviso_id).where(AvisoVisto.usuario_id == pessoa.id)
        ).scalars()
    )
    return [
        aviso
        for aviso in avisos_do_reino(cidadania.reino, pessoa, sessao)
        if aviso.id not in vistos
    ]


def marcar_visto(aviso, pessoa, sessao=None):
    """Tira o aviso da carteira desta pessoa. Idempotente.

    Marcar duas vezes é o mesmo que uma: o índice único resolve, e o segundo
    clique não é erro nem da pessoa nem da rede.
    """
    sessao = sessao or db.session
    sessao.add(AvisoVisto(aviso_id=aviso.id, usuario_id=pessoa.id, visto_em=agora()))
    try:
        sessao.flush()
    except IntegrityError:
        sessao.rollback()
    return aviso


def pode_apagar(aviso, pessoa, sessao=None, ids=None):
    """Quem apaga: o autor, se ainda for operador.

    Mesma forma de ``reinos.pode_negociar``, e pela mesma razão nos dois
    lados: quem escreveu é quem tira, mas perdeu o papel, perdeu o poder — e,
    se o autor não opera mais, qualquer operador atual assume, para o aviso
    não ficar pendurado para sempre no reino quando o rei troca.
    """
    from .reinos import eh_operador

    sessao = sessao or db.session
    if pessoa is None or not getattr(pessoa, "id", None):
        return False
    reino = aviso.reino
    if ids is None:
        from .reinos import operadores_ids

        ids = operadores_ids(reino, sessao)
    if not eh_operador(reino, pessoa, sessao, ids):
        return False
    if aviso.autor_id == pessoa.id:
        return True
    return aviso.autor_id not in ids


def apagar_aviso(aviso, quem, sessao=None):
    """Apaga o aviso e as marcas de dispensa dele.

    As marcas vão junto porque não têm vida própria: são "esta pessoa
    dispensou aquilo", e sem o aquilo viram lixo apontando para nada.
    """
    sessao = sessao or db.session
    if not pode_apagar(aviso, quem, sessao):
        raise ValorInvalido("esse aviso não é seu para apagar")

    sessao.execute(db.delete(AvisoVisto).where(AvisoVisto.aviso_id == aviso.id))
    sessao.delete(aviso)
    sessao.flush()
    return aviso
