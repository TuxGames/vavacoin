"""Freios do login.

São **dois**, e fazem coisas diferentes — confundir os dois é como uma senha
fraca cai:

- **Limite de taxa** (:class:`LimitadorDeTaxa`): quantas tentativas saem de um
  mesmo lugar numa janela de cinco minutos. Protege o **servidor** de rajada.
  Sozinho, ainda não protege senha nenhuma: 15 a cada 5 minutos são 4.320 por
  dia, o que continua sendo muito chute para uma senha fraca.
- **Trava por falhas consecutivas** (:class:`TravaPorFalhas`): depois de N
  erros seguidos *na mesma conta*, a espera começa e dobra a cada erro novo.
  É isso que protege a **conta**, porque transforma "4.320 chutes por dia" em
  "meia dúzia por dia".

Os dois guardam estado em memória, com dois limites conhecidos, escritos aqui
para ninguém confundir com garantia: somem quando o processo reinicia e não
são compartilhados entre processos. Para a turma numa conta grátis do
PythonAnywhere, com um worker, resolve. Se um dia houver mais de um worker,
isto vira tabela no banco — o que não pode é não existir nada.
"""

import threading
import time

#: Quantas tentativas de login um mesmo endereço pode fazer na janela abaixo.
#: Freia rajada; não é proteção de senha (ver TravaPorFalhas).
LIMITE_DE_TAXA = 15

#: Cinco minutos. Com o limite acima, dá 4.320 tentativas por dia por
#: endereço — patamar razoável para o servidor, e ainda assim longe de
#: proteger uma senha fraca. Quem faz isso é a TravaPorFalhas.
JANELA_DE_TAXA = 300

#: Quantos erros **seguidos** na mesma conta antes de a espera começar.
#: Esta é a trava que protege a conta: sem ela, o limite de taxa ainda
#: permitiria 4.320 chutes por dia contra uma senha fraca.
FALHAS_ATE_TRAVAR = 5

#: Primeira espera, em segundos. Dobra a cada erro seguinte.
ESPERA_INICIAL = 30

#: Teto da espera. Uma hora já torna o chute inviável sem trancar a pessoa
#: para sempre por causa de uma tarde de dedo errado.
ESPERA_MAXIMA = 3600


class LimitadorDeTaxa:
    """Janela deslizante: no máximo ``limite`` eventos por ``janela`` segundos."""

    def __init__(self, limite=LIMITE_DE_TAXA, janela=JANELA_DE_TAXA):
        self.limite = limite
        self.janela = janela
        self._eventos = {}
        self._trava = threading.Lock()

    def _recentes(self, chave, agora):
        recentes = [t for t in self._eventos.get(chave, []) if agora - t < self.janela]
        if recentes:
            self._eventos[chave] = recentes
        else:
            self._eventos.pop(chave, None)
        return recentes

    def registrar(self, chave):
        """Conta mais uma tentativa e diz se ela passou do limite.

        Conta **toda** tentativa, certa ou errada: o limite é de requisição,
        não de erro.
        """
        agora = time.monotonic()
        with self._trava:
            recentes = self._recentes(chave, agora)
            if len(recentes) >= self.limite:
                return int(self.janela - (agora - recentes[0])) + 1
            self._eventos.setdefault(chave, []).append(agora)
            return 0

    def limpar(self, chave=None):
        with self._trava:
            if chave is None:
                self._eventos.clear()
            else:
                self._eventos.pop(chave, None)


class TravaPorFalhas:
    """Espera crescente depois de erros consecutivos na mesma conta.

    A espera dobra a cada erro além do limite: 30s, 1min, 2min, 4min… até o
    teto. O contador só zera com um acerto — errar, esperar e errar de novo
    não devolve o crédito, senão a trava vira uma pausa e não uma trava.

    Custo aceito: a trava é por conta, então alguém de fora pode incomodar
    quem quiser errando a senha dessa pessoa. A primeira espera é curta de
    propósito por causa disso, e a alternativa — não travar nada — deixa a
    senha fraca exposta, que é o problema pior.
    """

    def __init__(
        self,
        falhas_ate_travar=FALHAS_ATE_TRAVAR,
        espera_inicial=ESPERA_INICIAL,
        espera_maxima=ESPERA_MAXIMA,
    ):
        self.falhas_ate_travar = falhas_ate_travar
        self.espera_inicial = espera_inicial
        self.espera_maxima = espera_maxima
        self._falhas = {}
        self._trava = threading.Lock()

    def _espera(self, quantas):
        if quantas < self.falhas_ate_travar:
            return 0
        return min(
            self.espera_inicial * (2 ** (quantas - self.falhas_ate_travar)),
            self.espera_maxima,
        )

    def segundos_de_bloqueio(self, conta):
        """Quanto falta para a conta poder tentar de novo (0 se liberada)."""
        agora = time.monotonic()
        with self._trava:
            registro = self._falhas.get(conta)
            if not registro:
                return 0
            quantas, ultima = registro
            restante = self._espera(quantas) - (agora - ultima)
            return int(restante) + 1 if restante > 0 else 0

    def registrar_falha(self, conta):
        agora = time.monotonic()
        with self._trava:
            quantas, _ = self._falhas.get(conta, (0, agora))
            self._falhas[conta] = (quantas + 1, agora)

    def limpar(self, conta=None):
        """Acertou a senha: o contador zera."""
        with self._trava:
            if conta is None:
                self._falhas.clear()
            else:
                self._falhas.pop(conta, None)


#: Freia rajada de um mesmo endereço.
limitador_taxa = LimitadorDeTaxa()

#: Protege a conta de chute paciente.
trava_por_falhas = TravaPorFalhas()


def limpar_tudo():
    """Zera os dois freios. Existe para os testes não contaminarem uns aos outros."""
    limitador_taxa.limpar()
    trava_por_falhas.limpar()
