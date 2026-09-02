"""Modelos do núcleo monetário.

Três tabelas e nada mais nesta fatia: quem tem dinheiro (``Usuario``), o que
autoriza uma pessoa a entrar (``Convite``) e o registro imutável de todo
centavo que se moveu (``Transacao``).
"""

from datetime import datetime, timezone

import bcrypt
from flask import current_app, has_app_context
from flask_login import UserMixin
from sqlalchemy import CheckConstraint

from .constantes import USUARIO_BANCO_CENTRAL
from .dinheiro import ZERO, Dinheiro
from .extensoes import db
from .nomes import normalizar_nome


def _custo_bcrypt():
    """Custo do bcrypt, configurável só para baixar o tempo da suíte.

    Em produção fica no padrão da biblioteca. Baixar o custo em teste é a
    diferença entre a suíte rodar em segundos ou em minutos — e teste lento
    é teste que ninguém roda.
    """
    if has_app_context():
        return current_app.config.get("BCRYPT_ROUNDS", 12)
    return 12


def agora():
    """Instante atual em UTC, com fuso explícito.

    Datetime ingênuo em ledger é dívida: seis meses depois ninguém sabe se
    aquele horário era de Brasília ou do servidor.
    """
    return datetime.now(timezone.utc)


class Usuario(db.Model, UserMixin):
    """Uma conta com saldo. O Banco Central é uma delas.

    O Banco Central é um ``Usuario`` de propósito: assim ele participa do
    mesmo ``mover()``, do mesmo ledger e da mesma soma de conservação que
    todo mundo. Se ele fosse um caso especial fora da tabela, a verificação
    de massa teria uma exceção — e exceção em invariante é onde massa some.
    """

    __tablename__ = "usuario"

    id = db.Column(db.Integer, primary_key=True)
    #: Como a pessoa escreveu — com maiúscula e acento, se quis. É o que
    #: aparece na tela.
    nome_usuario = db.Column(db.String(50), nullable=False, index=True)
    #: A forma que o sistema compara: sem acento, minúscula. É esta que é
    #: única, e é por ela que se busca no login e na transferência. Sem isso,
    #: "João" e "joao" seriam duas contas.
    nome_normalizado = db.Column(db.String(50), unique=True, nullable=False, index=True)
    nome_exibicao = db.Column(db.String(80), nullable=False)
    senha_hash = db.Column(db.String(128), nullable=True)
    eh_banco_central = db.Column(db.Boolean, nullable=False, default=False)
    #: A conta da casa do Caladinho. É uma conta de verdade no ledger, com
    #: saldo próprio — não uma coluna de configuração. Separada das duas
    #: coisas com que não pode se confundir: não é o Banco Central (que
    #: emite) nem a conta pessoal do dono (que joga). Nasce sem senha, então
    #: não entra pelo site.
    eh_cassino = db.Column(db.Boolean, nullable=False, default=False)
    saldo = db.Column(Dinheiro, nullable=False, default=ZERO)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    __table_args__ = (
        # Saldo negativo não é um estado possível desta economia. Se o banco
        # aceitar um, o bug já passou por aqui — a checagem é a última rede.
        CheckConstraint("saldo >= 0", name="ck_usuario_saldo_nao_negativo"),
        # Havia aqui um CHECK proibindo senha no Banco Central. Ele caiu na
        # migration 353a30f6e6f5: a decisão é que o BC entra pelo site e tem
        # god mode. O que protege a conta agora não é a porta fechada — é a
        # senha (por CLI, com hash), o freio de tentativas, e o fato de tudo
        # que ele faz passar pelo ledger com ator e motivo.
    )

    def definir_nome(self, nome_usuario):
        """Guarda as duas formas de uma vez.

        Existe para que ninguém escreva ``nome_usuario`` sem escrever o
        normalizado junto — os dois andam em par, e um sem o outro deixa a
        conta invisível para o login.
        """
        self.nome_usuario = (nome_usuario or "").strip()
        self.nome_normalizado = normalizar_nome(self.nome_usuario)
        if not self.nome_normalizado:
            raise ValueError("nome de usuário vazio depois de normalizado")
        return self.nome_normalizado

    def definir_senha(self, senha):
        """Guarda o hash bcrypt da senha. Texto puro nunca toca o banco.

        O Banco Central também tem senha, e ela é definida por CLI
        (``flask senha-bc``) — nunca no código, nunca em migration. Quem
        entrar nela é dono de tudo: é a decisão registrada no CLAUDE.md, e o
        que resta ao código é não piorá-la (hash bcrypt, freio de tentativas,
        e todo movimento no ledger com o ator anotado).
        """
        self.senha_hash = bcrypt.hashpw(
            senha.encode("utf-8"), bcrypt.gensalt(_custo_bcrypt())
        ).decode("utf-8")

    def verificar_senha(self, senha):
        """Confere a senha. Conta sem hash (Banco Central) nunca autentica."""
        if not self.senha_hash:
            return False
        return bcrypt.checkpw(senha.encode("utf-8"), self.senha_hash.encode("utf-8"))

    @property
    def is_active(self):
        """Flask-Login: toda conta com senha entra, o Banco Central inclusive.

        Uma conta ainda sem senha definida não entra — é o estado do Banco
        Central logo depois da gênese, antes do ``flask senha-bc``.
        """
        return self.senha_hash is not None

    @property
    def eh_admin(self):
        """Quem tem god mode. Hoje, só o Banco Central."""
        return self.eh_banco_central

    @property
    def eh_conta_de_sistema(self):
        """Banco Central e casa do cassino: não são jogadores.

        Nenhum dos dois resgata convite nem recebe transferência de gente. O
        dinheiro chega neles por caminho próprio — ajuste, aposta.
        """
        return self.eh_banco_central or self.eh_cassino

    def __repr__(self):
        return f"<Usuario {self.nome_usuario} saldo={self.saldo}>"


class Convite(db.Model):
    """O direito de uma pessoa entrar na economia.

    O convite é da **pessoa**, não da conta, e só serve uma vez. Ele já
    carregou um saque de 50 VVC; hoje carrega só o acesso, e o dinheiro chega
    depois por transferência ou por ajuste do Banco Central.
    """

    __tablename__ = "convite"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(32), unique=True, nullable=False, index=True)
    #: Para quem o código foi entregue. Serve para auditoria humana ("esse é
    #: o do Fulano"), não para autenticação.
    destinatario = db.Column(db.String(80), nullable=True)
    #: Conta que resgatou. UNIQUE garante, no banco, que uma conta não resgata
    #: dois códigos, mesmo que dois pedidos cheguem juntos.
    usuario_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), unique=True, nullable=True
    )
    resgatado_em = db.Column(db.DateTime(timezone=True), nullable=True)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)

    usuario = db.relationship("Usuario", backref="convite")

    @property
    def resgatado(self):
        return self.usuario_id is not None

    def __repr__(self):
        estado = "resgatado" if self.resgatado else "livre"
        return f"<Convite {self.codigo} {estado}>"


class Transacao(db.Model):
    """Uma linha do ledger. Só o ``mover()`` escreve aqui.

    Guarda os saldos resultantes dos dois lados porque auditar "onde o
    dinheiro estava na terça" reconstruindo somas do zero é caro e sujeito a
    erro; com o saldo pós-movimento gravado, cada linha se explica sozinha.
    """

    __tablename__ = "transacao"

    id = db.Column(db.Integer, primary_key=True)
    #: NULL quando o dinheiro entrou no mundo: a gênese, ou uma emissão feita
    #: pelo administrador ao ajustar saldo para cima. Somar estas linhas dá o
    #: supply — ver ``moeda.supply_emitido()``.
    origem_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True, index=True)
    #: NULL quando o dinheiro saiu do mundo: uma queima, feita pelo Banco
    #: Central ao baixar o próprio saldo. É o simétrico da emissão, e é o que
    #: permite o supply descer — sem ela o teto seria catraca de uma via.
    destino_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True, index=True)
    valor = db.Column(Dinheiro, nullable=False)
    tipo = db.Column(db.String(30), nullable=False, index=True)
    motivo = db.Column(db.String(200), nullable=True)
    #: Quem mandou fazer, quando não é o dono da origem — o administrador
    #: ajustando o saldo de alguém. É o que responde "por que meu saldo
    #: mudou?" seis meses depois.
    ator_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True, index=True)
    saldo_origem_depois = db.Column(Dinheiro, nullable=True)
    saldo_destino_depois = db.Column(Dinheiro, nullable=True)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora, index=True)

    __table_args__ = (
        CheckConstraint("valor > 0", name="ck_transacao_valor_positivo"),
        CheckConstraint(
            "origem_id IS NULL OR destino_id IS NULL OR origem_id <> destino_id",
            name="ck_transacao_origem_diferente_destino",
        ),
        # Uma linha sem os dois lados não seria movimento nenhum.
        CheckConstraint(
            "origem_id IS NOT NULL OR destino_id IS NOT NULL",
            name="ck_transacao_tem_algum_lado",
        ),
        # Origem ausente é criação de dinheiro. A lista de tipos que podem
        # fazer isso é curta e mora também no banco: qualquer outro tipo sem
        # origem seria moeda aparecendo sem ninguém ter decidido cunhar.
        CheckConstraint(
            "(origem_id IS NOT NULL) OR tipo IN ('genese', 'emissao')",
            name="ck_transacao_sem_origem_so_emite",
        ),
        # Destino ausente é destruição de dinheiro. Mesma disciplina, do
        # outro lado: só a queima pode sumir com moeda.
        CheckConstraint(
            "(destino_id IS NOT NULL) OR tipo = 'queima'",
            name="ck_transacao_sem_destino_so_queima",
        ),
    )

    origem = db.relationship("Usuario", foreign_keys=[origem_id])
    destino = db.relationship("Usuario", foreign_keys=[destino_id])
    ator = db.relationship("Usuario", foreign_keys=[ator_id])

    def __repr__(self):
        return f"<Transacao {self.tipo} {self.valor} -> {self.destino_id}>"


class RegistroAdministrativo(db.Model):
    """Diário do god mode: o que o administrador fez, quando e por quê.

    O ledger já explica todo centavo, mas metade do poder do administrador
    não mexe em dinheiro — emitir convite, criar conta, olhar o extrato de
    alguém. Sem este diário, essas ações não deixariam rastro nenhum.

    Não substitui o ledger e não é fonte de verdade sobre saldo: é o registro
    de decisão, para responder "quem fez isso e por quê" depois.
    """

    __tablename__ = "registro_administrativo"

    id = db.Column(db.Integer, primary_key=True)
    ator_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    #: Verbo curto e estável, para dar para filtrar: "convite", "conta",
    #: "ajuste", "reset", "extrato".
    acao = db.Column(db.String(40), nullable=False, index=True)
    #: Sobre quem ou sobre o quê. Guardado como texto, não como FK, para o
    #: registro sobreviver ao sumiço do alvo.
    alvo = db.Column(db.String(80), nullable=True)
    #: O que aconteceu, em números ("de 50.00 para 80.00").
    detalhe = db.Column(db.String(300), nullable=True)
    #: Por quê, escrito pelo administrador. Obrigatório onde mexe em dinheiro.
    motivo = db.Column(db.String(300), nullable=True)
    criado_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )

    ator = db.relationship("Usuario", foreign_keys=[ator_id])

    def __repr__(self):
        return f"<RegistroAdministrativo {self.acao} {self.alvo}>"


def registrar_acao(ator, acao, alvo=None, detalhe=None, motivo=None, sessao=None):
    """Anota uma ação do god mode. Não faz ``commit``."""
    sessao = sessao or db.session
    registro = RegistroAdministrativo(
        ator_id=ator.id if isinstance(ator, Usuario) else ator,
        acao=acao,
        alvo=alvo,
        detalhe=detalhe,
        motivo=motivo,
    )
    sessao.add(registro)
    sessao.flush()
    return registro


class Configuracao(db.Model):
    """Interruptores do jogo, guardados no banco e não no código.

    Mesma ideia do ``pagina_<x>_visivel`` do Benbals: o que o dono liga e
    desliga durante o jogo é dado, não constante — senão trocar de ideia
    exige deploy.
    """

    __tablename__ = "configuracao"

    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(60), unique=True, nullable=False, index=True)
    valor = db.Column(db.String(200), nullable=False)
    atualizado_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, onupdate=agora
    )

    def __repr__(self):
        return f"<Configuracao {self.chave}={self.valor}>"


#: Se o saldo da casa aparece para os jogadores.
CHAVE_CAIXA_VISIVEL = "caladinho_caixa_visivel"


def config_ligada(chave, padrao=False, sessao=None):
    """A configuração está ligada? Guardada como "1"/"0"."""
    sessao = sessao or db.session
    registro = sessao.execute(
        db.select(Configuracao).where(Configuracao.chave == chave)
    ).scalar_one_or_none()
    if registro is None:
        return padrao
    return registro.valor == "1"


def definir_config(chave, ligada, sessao=None):
    """Grava a configuração. Não faz ``commit``."""
    sessao = sessao or db.session
    valor = "1" if ligada else "0"
    registro = sessao.execute(
        db.select(Configuracao).where(Configuracao.chave == chave)
    ).scalar_one_or_none()
    if registro is None:
        registro = Configuracao(chave=chave, valor=valor)
        sessao.add(registro)
    else:
        registro.valor = valor
        registro.atualizado_em = agora()
    sessao.flush()
    return registro


class RodadaMines(db.Model):
    """Uma rodada de mines. É a fonte da verdade do servidor.

    O tabuleiro (``minas``) mora só aqui e **nunca vai para o cliente
    enquanto a rodada está ativa** — é o coração do anti-trapaça. O navegador
    informa qual casa foi clicada; quem decide se era mina é o servidor.

    Estados:

    - ``ativa``: em andamento; dá para revelar casa ou retirar;
    - ``estourada``: revelou uma mina e perdeu a aposta (já debitada);
    - ``retirada``: parou e recebeu o prêmio.

    "No máximo uma rodada ativa por jogador" tem duas defesas: o ``FOR
    UPDATE`` mais a checagem na criação, e um **índice único parcial** no
    banco. A segunda existe porque a primeira não cobre duas requisições que
    chegam ao mesmo tempo em processos diferentes.
    """

    __tablename__ = "rodada_mines"

    ATIVA = "ativa"
    ESTOURADA = "estourada"
    RETIRADA = "retirada"

    id = db.Column(db.Integer, primary_key=True)
    jogador_id = db.Column(
        db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True
    )
    aposta = db.Column(Dinheiro, nullable=False)
    minas_escolhidas = db.Column(db.Integer, nullable=False)

    #: Posições das minas (0..24), em CSV. SEGREDO DO SERVIDOR enquanto ativa.
    minas = db.Column(db.String(120), nullable=False)
    #: Casas seguras já abertas, em CSV.
    reveladas = db.Column(db.String(120), nullable=False, default="")

    estado = db.Column(db.String(12), nullable=False, default=ATIVA, index=True)
    #: A casa que estourou. Serve para a tela marcar a mina em que a pessoa
    #: pisou, diferente das outras — e para responder "qual casa eu cliquei?"
    #: se alguém contestar a rodada depois.
    casa_estourada = db.Column(db.Integer, nullable=True)
    #: Multiplicador acumulado, sem teto. Dois decimais exatos, como dinheiro.
    multiplicador = db.Column(Dinheiro, nullable=False, default=ZERO)
    premio = db.Column(Dinheiro, nullable=False, default=ZERO)

    #: As duas pontas no ledger. Toda rodada tem aposta; só a retirada tem
    #: prêmio.
    transacao_aposta_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )
    transacao_premio_id = db.Column(
        db.Integer, db.ForeignKey("transacao.id"), nullable=True
    )

    criada_em = db.Column(
        db.DateTime(timezone=True), nullable=False, default=agora, index=True
    )
    encerrada_em = db.Column(db.DateTime(timezone=True), nullable=True)

    jogador = db.relationship("Usuario", foreign_keys=[jogador_id])

    __table_args__ = (
        CheckConstraint("aposta > 0", name="ck_rodada_aposta_positiva"),
        CheckConstraint(
            "minas_escolhidas >= 1 AND minas_escolhidas <= 24",
            name="ck_rodada_minas",
        ),
        CheckConstraint(
            "estado IN ('ativa', 'estourada', 'retirada')", name="ck_rodada_estado"
        ),
        # Índice único parcial: o banco recusa a segunda rodada ativa do mesmo
        # jogador, mesmo em corrida entre processos.
        db.Index(
            "uq_uma_rodada_ativa_por_jogador",
            "jogador_id",
            unique=True,
            sqlite_where=db.text("estado = 'ativa'"),
            postgresql_where=db.text("estado = 'ativa'"),
        ),
    )

    @staticmethod
    def _lista(texto):
        return [int(c) for c in (texto or "").split(",") if c != ""]

    @property
    def casas_reveladas(self):
        return self._lista(self.reveladas)

    @property
    def casas_com_mina(self):
        return self._lista(self.minas)

    @property
    def encerrada(self):
        return self.estado != self.ATIVA

    def __repr__(self):
        return f"<RodadaMines {self.id} {self.estado} aposta={self.aposta}>"


def buscar_usuario(nome, sessao=None):
    """Acha a conta pelo nome, comparando a forma normalizada.

    É o único jeito de buscar usuário no projeto. Comparar ``nome_usuario``
    direto acha só quem digitou exatamente igual — com o mesmo acento e a
    mesma caixa —, que é justamente o que não se pode exigir de quem está
    digitando no celular.
    """
    sessao = sessao or db.session
    normalizado = normalizar_nome(nome)
    if not normalizado:
        return None
    return sessao.execute(
        db.select(Usuario).where(Usuario.nome_normalizado == normalizado)
    ).scalar_one_or_none()


def banco_central(sessao=None, travado=False):
    """Devolve a conta do Banco Central, ou ``None`` se a gênese não rodou."""
    sessao = sessao or db.session
    consulta = db.select(Usuario).where(
        Usuario.nome_normalizado == USUARIO_BANCO_CENTRAL
    )
    if travado:
        consulta = consulta.with_for_update()
    return sessao.execute(consulta).scalar_one_or_none()
