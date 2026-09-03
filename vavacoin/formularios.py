"""Formulários.

Todos herdam de ``FlaskForm``, o que traz o token CSRF de graça — e é por isso
que nenhum formulário do projeto é escrito na mão no template.

O valor da transferência é ``StringField``, não ``DecimalField``: o campo do
WTForms faria a conversão por conta própria, e a regra do projeto é que só
``para_decimal()`` decide o que é dinheiro válido.
"""

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import (
    DataRequired,
    EqualTo,
    Length,
    Optional,
    Regexp,
    ValidationError,
)

from .dinheiro import ZERO, para_decimal

#: Maiúscula e acento são permitidos — o nome é da pessoa, e ela escreve
#: como quiser. Espaço fica de fora para o nome caber numa URL sem escape, e
#: a comparação (unicidade, login) usa a forma normalizada, não esta.
NOME_USUARIO = Regexp(
    r"^[A-Za-zÀ-ÖØ-öø-ÿ0-9._-]+$",
    message="Use letras, números, ponto, hífen ou sublinhado — sem espaço.",
)

#: Senha: sem tamanho mínimo, por decisão do dono do projeto — repetida e
#: registrada. O ``DataRequired`` continua: vazia (ou só espaços) é recusada,
#: e a diferença não é detalhe. Sem mínimo é escolha de quem usa; sem senha é
#: conta destrancada. O teto existe só para não deixar entrar um texto enorme
#: no bcrypt.
SENHA = [
    DataRequired(message="Escolha uma senha. Pode ser curta, mas não pode ser vazia."),
    Length(max=200),
]


class FormularioLogin(FlaskForm):
    nome_usuario = StringField("Usuário", validators=[DataRequired()])
    senha = PasswordField("Senha", validators=[DataRequired()])
    enviar = SubmitField("Entrar")


class FormularioCadastro(FlaskForm):
    """Cadastro por convite — a única porta de entrada."""

    #: Opcional no formulário, obrigatório ou não conforme o interruptor
    #: ``cadastro_aberto``. A regra mora na rota, e não aqui, porque ela é
    #: dado no banco — um validador de classe não consegue lê-la.
    codigo = StringField("Código do convite (opcional)", validators=[Optional()])
    nome_usuario = StringField(
        "Usuário",
        validators=[DataRequired(), Length(min=3, max=50), NOME_USUARIO],
    )
    nome_exibicao = StringField(
        "Nome que aparece", validators=[DataRequired(), Length(min=2, max=80)]
    )
    senha = PasswordField("Senha", validators=SENHA)
    confirmacao = PasswordField(
        "Repita a senha",
        validators=[DataRequired(), EqualTo("senha", message="As senhas não batem.")],
    )
    enviar = SubmitField("Criar conta")


class FormularioTransferencia(FlaskForm):
    destinatario = StringField("Para quem", validators=[DataRequired()])
    valor = StringField("Quanto (VVC)", validators=[DataRequired()])
    motivo = StringField("Por quê", validators=[Length(max=200)])
    enviar = SubmitField("Revisar")

    def validate_valor(self, campo):
        """Deixa ``para_decimal()`` mandar: mesma regra do núcleo monetário."""
        try:
            valor = para_decimal(campo.data.strip().replace(",", "."))
        except TypeError as erro:
            raise ValidationError(
                "Valor inválido. Use no máximo dois decimais, como 12.50."
            ) from erro
        if valor <= ZERO:
            raise ValidationError("O valor precisa ser maior que zero.")
        campo.decimal = valor


class FormularioConfirmacao(FlaskForm):
    """Só o token: a transferência confirmada é a que está na sessão.

    O que é confirmado não vem do formulário, e sim do que o servidor guardou
    e mostrou na tela — assim não há como o que foi exibido diferir do que é
    executado.
    """

    token = StringField(validators=[DataRequired()])
    enviar = SubmitField("Confirmar e transferir")


# --- painel do Banco Central ------------------------------------------------


class FormularioEmitirConvite(FlaskForm):
    destinatario = StringField("Para quem (nome do aluno)", validators=[Length(max=80)])
    enviar = SubmitField("Emitir convite")


class FormularioCriarConta(FlaskForm):
    nome_usuario = StringField(
        "Usuário", validators=[DataRequired(), Length(min=3, max=50), NOME_USUARIO]
    )
    nome_exibicao = StringField(
        "Nome que aparece", validators=[DataRequired(), Length(min=2, max=80)]
    )
    senha = PasswordField("Senha", validators=SENHA)
    enviar = SubmitField("Criar conta")


#: O que vai para o ledger quando o administrador não escreve um motivo.
#: Escrever à mão continua valendo mais, mas exigir a frase era o atrito que
#: fazia ele evitar o painel — e o lançamento sozinho já responde quem, quando
#: e de quanto para quanto, que é a maior parte do valor.
MOTIVO_PADRAO = "ajuste pelo painel"


class FormularioAjusteDeSaldo(FlaskForm):
    """Ajuste de saldo por nome. O motivo é opcional; em branco vira o padrão."""

    nome_usuario = StringField("Usuário", validators=[DataRequired()])
    novo_saldo = StringField("Saldo correto (VVC)", validators=[DataRequired()])
    motivo = StringField("Motivo", validators=[Optional(), Length(max=300)])
    enviar = SubmitField("Ajustar saldo")

    def validate_novo_saldo(self, campo):
        """Mesma régua do núcleo: quem decide o que é dinheiro é para_decimal."""
        try:
            valor = para_decimal(campo.data.strip().replace(",", "."))
        except TypeError as erro:
            raise ValidationError(
                "Valor inválido. Use no máximo dois decimais, como 12.50."
            ) from erro
        if valor < ZERO:
            raise ValidationError("O saldo não pode ficar negativo.")
        campo.decimal = valor


class FormularioLinhaDaConta(FlaskForm):
    """Uma linha da tabela de contas do painel, editável.

    Nome, senha e saldo de uma vez, sem sair da tela. Cada campo é opcional:
    em branco significa "não mexe nisso". O saldo é o único que sempre vem
    preenchido, porque é o número que a pessoa veio mudar.

    A senha é **de escrita**: com hash bcrypt não há o que mostrar, então o
    campo troca a senha quando preenchido e não faz nada quando vazio.
    """

    nome_usuario = StringField(
        validators=[Optional(), Length(max=50), NOME_USUARIO]
    )
    senha = StringField(validators=[Optional(), Length(max=200)])
    saldo = StringField(validators=[DataRequired()])
    motivo = StringField(validators=[Optional(), Length(max=300)])
    enviar = SubmitField("Salvar")

    def validate_saldo(self, campo):
        """Mesma régua do núcleo: quem decide o que é dinheiro é para_decimal."""
        try:
            valor = para_decimal(campo.data.strip().replace(",", "."))
        except TypeError as erro:
            raise ValidationError("Valor inválido; use até dois decimais.") from erro
        if valor < ZERO:
            raise ValidationError("O saldo não pode ficar negativo.")
        campo.decimal = valor


class FormularioReset(FlaskForm):
    """Reset recolhe o dinheiro de todo mundo; digitar a palavra é o freio."""

    confirmacao = StringField(
        "Digite RESETAR para confirmar", validators=[DataRequired()]
    )
    motivo = StringField("Motivo", validators=[Length(max=300)])
    enviar = SubmitField("Resetar economia")

    def validate_confirmacao(self, campo):
        if campo.data.strip() != "RESETAR":
            raise ValidationError("Digite exatamente RESETAR.")


class FormularioVisibilidadeDoCaixa(FlaskForm):
    """Liga e desliga o caixa da casa para os jogadores."""

    visivel = BooleanField("Mostrar o caixa da casa para os jogadores")
    enviar = SubmitField("Salvar")


class FormularioReinosVisiveis(FlaskForm):
    """Liga e desliga a página dos reinos para a turma.

    **Nasce desligada.** O reino aparece quando o Banco Central montar o cofre
    e nomear o operador — antes disso a tela existiria vazia, e tela vazia
    convida pergunta que ninguém quer responder duas vezes.
    """

    visiveis = BooleanField("Mostrar os reinos para a turma")
    enviar = SubmitField("Salvar")


class FormularioSaldoPublico(FlaskForm):
    """O interruptor do próprio saldo, no perfil. Rótulo curto e mais nada."""

    publico = BooleanField("Mostrar meu saldo para os outros")
    enviar = SubmitField("Salvar")


class FormularioRankingVisivel(FlaskForm):
    """Liga e desliga o ranking geral. Só o Banco Central vê."""

    visivel = BooleanField("Mostrar o ranking geral")
    enviar = SubmitField("Salvar")


class FormularioCadastroAberto(FlaskForm):
    """Liga e desliga a exigência de convite. Só o Banco Central vê."""

    aberto = BooleanField("Deixar criar conta sem convite")
    enviar = SubmitField("Salvar")


class FormularioCaixaDoDono(FlaskForm):
    """Aportar ou retirar do caixa da casa. Só o dono vê."""

    valor = StringField("Valor (VVC)", validators=[DataRequired()])
    enviar = SubmitField("Confirmar")

    def validate_valor(self, campo):
        try:
            valor = para_decimal(campo.data.strip().replace(",", "."))
        except TypeError as erro:
            raise ValidationError("Valor inválido; use até dois decimais.") from erro
        if valor <= ZERO:
            raise ValidationError("O valor precisa ser maior que zero.")
        campo.decimal = valor
