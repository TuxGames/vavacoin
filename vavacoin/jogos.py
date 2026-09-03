"""Quais jogos do Caladinho estão no ar.

O dono desligou o crash por decisão de produto — "os usuários querem ver o
avião subindo e explodindo em tempo real, não do jeito que tá agora" —, e um
jogo que sai do ar não pode sair apagado: ele volta quando houver tempo real.
Por isso isto é um **interruptor**, dado no banco, e não código removido.

Fica ao lado da vantagem, no painel do dono da casa, pelo mesmo motivo que a
vantagem foi parar lá: é decisão de quem toca o cassino, e trocar de ideia não
pode exigir deploy.

## Desligar não sequestra nada

Um jogo desligado **esconde do lobby e fecha a rota**. Isso levanta dois
problemas de verdade, e nenhum dos dois pode ficar sem resposta:

1. Rodada aberta segura ``premio_maximo`` na exposição comprometida. Fechar a
   rota com rodadas abertas prenderia esse pedaço do caixa **para sempre**,
   porque o caminho que as encerra é justamente a rota que acabou de fechar.
2. Quem já tinha multiplicador conquistado perderia o que era dele, sem ter
   feito nada — e teria razão em chamar isso de roubo.

A resposta é uma só e resolve os dois: **desligar liquida as rodadas abertas
daquele jogo na hora**, pagando o que já foi conquistado, exatamente como a
expiração de rodada abandonada já faz. O caixa é liberado no mesmo instante e
ninguém perde nada. Só depois a rota fecha.
"""

from .modelos import config_ligada, definir_config, registrar_acao

#: Os jogos do Caladinho. Lista única do projeto — a vantagem lê daqui.
JOGOS = ("mines", "crash", "torre", "dados")

#: Como cada jogo nasce. O crash nasce **desligado** por decisão do dono; os
#: outros já estavam no ar e continuam.
PADRAO = {"mines": True, "crash": False, "torre": True, "dados": True}


def chave_de(jogo):
    return f"caladinho_jogo_{validar_jogo(jogo)}"


def validar_jogo(jogo):
    from .erros import ValorInvalido

    if jogo not in JOGOS:
        raise ValorInvalido(f"jogo desconhecido: {jogo!r}")
    return jogo


def ligado(jogo, sessao=None):
    """O jogo está no ar?"""
    return config_ligada(chave_de(jogo), padrao=PADRAO[jogo], sessao=sessao)


def ligados(sessao=None):
    """Quais jogos aparecem no lobby, na ordem da lista."""
    return tuple(j for j in JOGOS if ligado(j, sessao=sessao))


def todos(sessao=None):
    """O estado de cada jogo, para desenhar o painel do dono."""
    return {j: ligado(j, sessao=sessao) for j in JOGOS}


def definir_ligado(jogo, novo, operador, sessao=None):
    """Liga ou desliga o jogo, e registra quem mexeu.

    **Ao desligar, liquida as rodadas abertas daquele jogo** — pagando o que
    já foi conquistado — antes de a rota fechar. Sem isso, o caixa ficaria
    preso pela exposição de rodadas que ninguém mais consegue encerrar, e
    quem estava jogando perderia o multiplicador que já tinha.

    Mesmo padrão de registro da vantagem: é o que responde "quem tirou o jogo
    do ar, e quando?" sem depender da memória de ninguém.
    """
    from .extensoes import db

    sessao = sessao or db.session
    jogo = validar_jogo(jogo)
    novo = bool(novo)
    anterior = ligado(jogo, sessao=sessao)

    liquidadas = []
    if anterior and not novo:
        liquidadas = liquidar_rodadas_abertas(jogo, sessao=sessao)

    definir_config(chave_de(jogo), novo, sessao=sessao)
    detalhe = "ligado" if novo else "desligado"
    if liquidadas:
        detalhe += f"; {len(liquidadas)} rodada(s) liquidada(s)"
    registrar_acao(operador, "jogo", alvo=jogo, detalhe=detalhe, sessao=sessao)
    return novo


def liquidar_rodadas_abertas(jogo, sessao=None):
    """Encerra agora as rodadas abertas de um jogo, pagando o conquistado.

    Reaproveita, para cada jogo, o caminho que já encerra rodada abandonada —
    e não um segundo caminho paralelo, que é como duas regras de pagamento
    acabam divergindo.

    O crash não entra: a rodada dele já tem desfecho decidido no instante da
    aposta e se resolve sozinha na leitura, então liquidar aqui seria
    antecipar um resultado que o tempo ainda não alcançou. Em vez disso, as
    rodadas de crash abertas são resolvidas pelo relógio, como sempre.
    """
    from .extensoes import db

    sessao = sessao or db.session

    if jogo == "mines":
        from .caladinho import expirar_mines_abandonadas

        return expirar_mines_abandonadas(sessao=sessao, momento=_bem_depois())
    if jogo == "torre":
        from .caladinho import expirar_torres_abandonadas

        return expirar_torres_abandonadas(sessao=sessao, momento=_bem_depois())
    if jogo == "crash":
        from .caladinho import resolver_crash
        from .modelos import RodadaCrash, Usuario

        encerradas = []
        for jogador_id in sessao.execute(
            db.select(RodadaCrash.jogador_id).where(
                RodadaCrash.estado == RodadaCrash.ATIVA
            )
        ).scalars():
            jogador = sessao.get(Usuario, jogador_id)
            rodada = resolver_crash(jogador, sessao=sessao, momento=_bem_depois())
            if rodada is not None:
                encerradas.append(rodada)
        return encerradas
    # Dados resolve na hora da aposta: nunca há rodada aberta.
    return []


def _bem_depois():
    """Um instante bem à frente, para a liquidação alcançar todo mundo.

    A expiração cobra um prazo de inatividade antes de fechar a rodada; aqui o
    prazo não interessa, porque quem está fechando é o dono e a rota vai
    sumir. Empurrar o relógio é o jeito de dizer isso reusando o mesmo código
    em vez de escrever um segundo.
    """
    from datetime import timedelta

    from .modelos import agora

    return agora() + timedelta(days=3650)
