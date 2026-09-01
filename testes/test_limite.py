"""Os dois freios do login, testados por dentro.

Aqui não há requisição: o que se verifica é a aritmética das esperas, que é
onde mora a diferença entre "protege o servidor" e "protege a conta".
"""

from vavacoin.limite import (
    ESPERA_INICIAL,
    ESPERA_MAXIMA,
    FALHAS_ATE_TRAVAR,
    JANELA_DE_TAXA,
    LIMITE_DE_TAXA,
    LimitadorDeTaxa,
    TravaPorFalhas,
)


# --- limite de taxa ---------------------------------------------------------


def test_taxa_libera_ate_o_limite_e_barra_depois():
    limitador = LimitadorDeTaxa(limite=3, janela=60)
    assert [limitador.registrar("ip") for _ in range(3)] == [0, 0, 0]
    assert limitador.registrar("ip") > 0


def test_taxa_conta_por_chave_separadamente():
    """Um endereço em rajada não pode barrar outro."""
    limitador = LimitadorDeTaxa(limite=2, janela=60)
    limitador.registrar("ip-a")
    limitador.registrar("ip-a")
    assert limitador.registrar("ip-a") > 0
    assert limitador.registrar("ip-b") == 0


def test_taxa_esquece_o_que_saiu_da_janela():
    limitador = LimitadorDeTaxa(limite=2, janela=0)
    limitador.registrar("ip")
    limitador.registrar("ip")
    assert limitador.registrar("ip") == 0


def test_limite_padrao_e_quinze_por_cinco_minutos():
    assert LIMITE_DE_TAXA == 15
    assert JANELA_DE_TAXA == 5 * 60


# --- trava por falhas consecutivas ------------------------------------------


def test_trava_so_comeca_depois_das_falhas_combinadas():
    trava = TravaPorFalhas()
    for _ in range(FALHAS_ATE_TRAVAR - 1):
        trava.registrar_falha("ana")
        assert trava.segundos_de_bloqueio("ana") == 0

    trava.registrar_falha("ana")
    assert trava.segundos_de_bloqueio("ana") > 0


def test_espera_dobra_a_cada_erro_novo():
    """É a progressão que torna o chute paciente inviável.

    O limite de taxa sozinho deixaria 21.600 tentativas por dia; com a espera
    dobrando, a décima falha já custa horas.
    """
    trava = TravaPorFalhas()
    esperas = []
    for _ in range(FALHAS_ATE_TRAVAR + 4):
        trava.registrar_falha("ana")
        esperas.append(trava.segundos_de_bloqueio("ana"))

    travadas = [e for e in esperas if e > 0]
    assert travadas[0] <= ESPERA_INICIAL + 1
    for anterior, seguinte in zip(travadas, travadas[1:]):
        assert seguinte > anterior


def test_espera_tem_teto():
    """Uma tarde de dedo errado não pode trancar a pessoa para sempre."""
    trava = TravaPorFalhas()
    for _ in range(40):
        trava.registrar_falha("ana")
    assert trava.segundos_de_bloqueio("ana") <= ESPERA_MAXIMA + 1


def test_acerto_zera_o_contador():
    trava = TravaPorFalhas()
    for _ in range(FALHAS_ATE_TRAVAR):
        trava.registrar_falha("ana")
    assert trava.segundos_de_bloqueio("ana") > 0

    trava.limpar("ana")
    assert trava.segundos_de_bloqueio("ana") == 0
    trava.registrar_falha("ana")
    assert trava.segundos_de_bloqueio("ana") == 0


def test_espera_cumprida_nao_devolve_credito():
    """Errar, esperar e errar de novo escala; senão a trava vira pausa."""
    trava = TravaPorFalhas(espera_inicial=0)
    for _ in range(FALHAS_ATE_TRAVAR):
        trava.registrar_falha("ana")
    assert trava.segundos_de_bloqueio("ana") == 0  # espera zerada já passou

    trava.registrar_falha("ana")
    assert trava.segundos_de_bloqueio("ana") == 0  # 0 * 2 ainda é 0

    trava_real = TravaPorFalhas()
    for _ in range(FALHAS_ATE_TRAVAR + 2):
        trava_real.registrar_falha("bia")
    assert trava_real.segundos_de_bloqueio("bia") > ESPERA_INICIAL


def test_trava_e_por_conta_nao_por_endereco():
    trava = TravaPorFalhas()
    for _ in range(FALHAS_ATE_TRAVAR):
        trava.registrar_falha("ana")
    assert trava.segundos_de_bloqueio("ana") > 0
    assert trava.segundos_de_bloqueio("bia") == 0
