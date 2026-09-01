"""Modelos do núcleo monetário.

Três tabelas e nada mais nesta fatia: quem tem dinheiro (``Usuario``), o que
autoriza uma pessoa a sacar os 50 (``Convite``) e o registro imutável de todo
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

    def __repr__(self):
        return f"<Usuario {self.nome_usuario} saldo={self.saldo}>"


class Convite(db.Model):
    """O direito de uma pessoa sacar os 50 iniciais.

    O convite é da **pessoa**, não da conta: é o código que carrega o saque,
    e ele só serve uma vez. Sem isso, dez cadastros da mesma pessoa virariam
    500 VVC e o supply passaria a crescer com o número de contas.
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
    destino_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False, index=True)
    valor = db.Column(Dinheiro, nullable=False)
    tipo = db.Column(db.String(30), nullable=False, index=True)
    motivo = db.Column(db.String(200), nullable=True)
    #: Quem mandou fazer, quando não é o dono da origem — o administrador
    #: ajustando o saldo de alguém. É o que responde "por que meu saldo
    #: mudou?" seis meses depois.
    ator_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True, index=True)
    saldo_origem_depois = db.Column(Dinheiro, nullable=True)
    saldo_destino_depois = db.Column(Dinheiro, nullable=False)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora, index=True)

    __table_args__ = (
        CheckConstraint("valor > 0", name="ck_transacao_valor_positivo"),
        CheckConstraint(
            "origem_id IS NULL OR origem_id <> destino_id",
            name="ck_transacao_origem_diferente_destino",
        ),
        # Origem ausente é criação de dinheiro. A lista de tipos que podem
        # fazer isso é curta e mora também no banco: qualquer outro tipo sem
        # origem seria moeda aparecendo sem ninguém ter decidido cunhar.
        CheckConstraint(
            "(origem_id IS NOT NULL) OR tipo IN ('genese', 'emissao')",
            name="ck_transacao_sem_origem_so_emite",
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
