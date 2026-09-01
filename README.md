# VavaCoin

Economia simbólica da turma. Supply fixo de 5.000 VVC, que ninguém cunha.
As decisões do projeto estão no `CLAUDE.md` e não se relitigam aqui.

**O que já existe:** o núcleo monetário, a auditoria, a web (entrar por
convite, saldo e extrato próprios, transferir com confirmação) e o painel do
Banco Central. **O que não existe:** cassino e ranking.

## Ambiente

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
```

## Rodar local

```bash
export FLASK_APP=wsgi.py
flask db upgrade
flask genese
flask senha-bc                          # senha do painel, pedida sem eco
flask convite --destinatario "Fulano"   # imprime o código; entregue à pessoa
flask run
```

A pessoa entra em `/cadastro` com o código, escolhe usuário e senha, e a conta
nasce com **saldo zero**. O dinheiro chega depois: por transferência de outra
pessoa, ou por ajuste do Banco Central no painel.

`--destinatario` é opcional — serve de rótulo para quem emite em série e ainda
não sabe quem vai receber cada código.

Os números da economia não são públicos: aparecem no painel do Banco Central
e por `flask auditoria`, na CLI.

| rota | o que é |
| --- | --- |
| `/` | o que é a moeda |
| `/cadastro` | única porta de entrada, e só com convite |
| `/entrar`, `/sair` | login com Flask-Login |
| `/carteira` | seu saldo e seu extrato (só o seu) |
| `/transferir` | monta a transferência; **não move nada** |
| `/transferir/confirmar` | mostra valor e destinatário, e só então efetiva |
| `/painel/` | god mode do Banco Central: contas editáveis na linha, convites, reset, diário, Caladinho |
| `/caladinho/` | o cassino |
| `/caladinho/mines` | mines: rodada ativa ou resultado da última |

## Testes

```bash
.venv/Scripts/python.exe -m pytest
```

Toda operação é cercada pelo helper `conservacao()` (em `testes/conftest.py`):
a soma de **todos** os saldos, incluindo o do Banco Central, é sempre
exatamente 5.000,00.

## Banco e operação

```bash
export FLASK_APP=wsgi.py
flask db upgrade                      # cria as tabelas
flask genese                          # 5.000,00 no Banco Central (idempotente)
flask convite --destinatario "Fulano" # emite um convite; imprime o código
flask criar-conta fulano              # pede a senha sem eco
flask extrato fulano                  # o que entrou e saiu, com saldo em cada linha
flask conservacao                     # confere a massa
flask auditoria                       # massa + o ledger explica cada saldo (sai 1 se não)
flask resetar                         # recolhe de todos e redistribui os 50
```

Estes são os poderes do Banco Central, e é de propósito que só existam aqui:
exercê-los exige acesso ao servidor.

`conservacao` e `auditoria` respondem coisas diferentes, e a diferença importa.
A soma continuar 5.000,00 não prova nada sozinha — quem tira de um e põe no
outro por fora do `mover()` conserva a massa. `auditoria` reconstrói todo saldo
a partir do ledger e acusa a diferença; tem teste exatamente desse caso.

## A tela não explica nada

Regra do projeto: **se a frase existe para convencer alguém de alguma coisa,
ela não vai para a tela.** A interface mostra número, botão e o que aconteceu.

Fica: mensagem de erro e de resultado, rótulo de campo, texto de ajuda curto
quando sem ele a pessoa não sabe o que digitar, e a confirmação da
transferência (valor e destinatário — é a única proteção contra mandar
errado).

Sai: aviso, lista de tópicos, justificativa de decisão. O raciocínio de
projeto mora no `CLAUDE.md`, que é onde alguém vai procurá-lo.

## O visual veio do Benbals

A casca (paleta, espaçamento, cards, hero de saldo, extrato com barra lateral
colorida, menu off-canvas) foi trazida de `static/base.css` e `static/menu.js`
do projeto Benbals, que tem quatro anos de ajuste com gente usando no celular.
Herdar isso é mais barato e melhor do que inventar outra paleta.

O que **não** veio, e por quê:

- **`stylepage.css`**: importa fonte do Google e uma imagem de fundo de um
  bucket S3 — a CSP (`default-src 'self'`) bloqueia as duas. Além disso ele
  estiliza um login de card 3D que não existe aqui.
- **Bootstrap por CDN**: mesma razão. As poucas classes que as telas usavam
  (`.card`, `.btn`, `.form-control`, `.alert`) já tinham estilo próprio no
  `base.css`.
- **Telas de funcionalidade que não existe**: mural, fórum, chat, empresas,
  leilões, títulos, quests, ranking.

E o que precisou de adaptação: o Benbals guarda o CSS do topo e do menu dentro
de um `<style>` no `dashboard.html`, e usa `style=` e `onclick=` à vontade.
Aqui isso desceu para `base.css` e o `menu.js` (que já era `addEventListener`,
então veio quase intacto). O teste `test_nenhum_template_tem_estilo_inline_ou_handler`
é o juiz: falha se algum voltar.

## Caladinho

O cassino. O primeiro jogo é o **mines**, trazido do `cassino_benbal` — mesma
matemática (multiplicador como fração de inteiros, uma só divisão no fim),
mesma vantagem de 2%, mesmo teto de 25×, minas por `secrets` no servidor.

O que é diferente aqui:

- **Sem ficha, sem depósito, sem saque.** A aposta debita VVC pelo `mover()` e
  o prêmio credita pelo mesmo caminho: dois lançamentos no ledger. A auditoria
  fecha com rodada ganha, perdida e abandonada no meio.
- **O caixa é uma conta**, `caladinho`, com saldo próprio. Não é o Banco
  Central e não é a conta de ninguém. Nasce sem senha, então não entra pelo
  site. Criada por `flask criar-cassino`.
- **O teto de banca lê o caixa real**, no momento da aposta, e desconta o que
  as rodadas ativas já comprometeram. O original não desconta porque lá só há
  uma rodada ativa por jogador; com vários jogadores ao mesmo tempo, cada
  aposta passa sozinha e juntas estouram a casa.
- **Ao bater 25× a rodada encerra e paga.** Continuar seria risco sem prêmio,
  e prêmio acima do que a casa cobriu na hora da aposta.
- **Sem JavaScript.** Cada casa é um formulário; o servidor decide e a página
  recarrega. As minas só aparecem quando a rodada encerra.
- **O visual é o do cassino original**, em `static/caladinho.css`: paleta
  escura azulada, tabuleiro 5×5, 💎 e 💣, e a mina em que a pessoa pisou com
  fundo vermelho, diferente das outras. Tudo prefixado `cal-` e escopado em
  `.caladinho`, porque `.casa`, `.card`, `.btn`, `.tabela`, `.rodape` e
  `.sidebar` existem nos dois CSS com significados diferentes — deixar um
  vencer o outro por ordem de carregamento funciona até alguém reordenar os
  `<link>`. A barra de cima continua sendo do `base.css`: o jogo tem cara
  própria, mas a pessoa precisa saber voltar.

A visibilidade do caixa para os jogadores é um **interruptor no painel do
Banco Central**, guardado no banco — trocar de ideia não exige deploy.

## O painel edita na linha

Como o admin do Benbals: `ID · Usuário · Senha · Saldo · Motivo`, cada linha um
formulário com botão de salvar. Digita o número novo, salva, acabou.

O que não mudou: **o saldo continua passando por `ajustar_saldo`**, com
lançamento no ledger e ator. A tabela mudou a tela, não o caminho do dinheiro —
sem isso a auditoria pararia de fechar no primeiro ajuste.

O motivo é **opcional**; em branco grava `ajuste pelo painel`. Exigir a frase
era atrito de verdade, e o lançamento sozinho já responde quem, quando e de
quanto para quanto. O campo continua no modelo e no diário para quem quiser
escrever.

Detalhes: o campo de senha é **de escrita** (com hash não há o que mostrar) —
em branco não mexe. Renomear respeita a unicidade normalizada, então `joao` →
`João` passa e `joao` → nome de outra conta é recusado com a mensagem na
linha. Banco Central e `caladinho` aparecem para consulta, sem senha e sem
renomear; o saldo da casa é editável, que é o atalho para pôr dinheiro no
cassino. Não há excluir conta — é justamente o bug que existe no Benbals.

`<form>` não pode ser filho de `<tr>`, então cada formulário mora fora da
tabela e os campos apontam para ele pelo atributo `form=`. Padrão, e sem
JavaScript.

## O supply para em 10.000

`SUPPLY_MAXIMO` é teto de verdade: emissão que passaria dele é **recusada**,
com a mensagem dizendo quanto ainda cabe. Vale sobre o supply contado do
ledger, nunca sobre um número guardado à parte.

A trava mora no ramo de emissão do `mover()` — o único ponto que cria
dinheiro. Conferir na operação de cima deixaria de fora qualquer caminho novo
que emitisse, e o ponto de ter um teto é ele não depender de quem lembra de
checá-lo.

Não esbarram no teto: ajuste **para baixo**, ajuste **para cima que caiba no
não emitido** do Banco Central (gastar o que ele já tem não é cunhar), e
transferência entre pessoas.

Para emitir: `flask emitir 5000.00 --motivo "..."`. O motivo é obrigatório e
vai para o ledger.

**Consequência a saber antes de esbarrar:** com o supply no teto e o Banco
Central sem saldo não emitido, corrigir o saldo de alguém *para cima* é
recusado. A saída é tirar de outra conta — que é exatamente o ponto do teto.

## Não há saque inicial

O convite dá **entrada na economia, não valor**. Quem resgata começa com zero;
o dinheiro chega por transferência ou por ajuste do Banco Central.

Duas consequências: o reset passou a **só recolher** por padrão (sem saque
inicial não há valor óbvio para devolver a cada um — o parâmetro `saque`
continua existindo para quando o BC quiser redistribuir algo), e sumiu o caso
de "não dá para entrar porque o saldo não emitido acabou".

## Nome de usuário: escreve como quiser, compara normalizado

A pessoa escolhe o nome com maiúscula e acento — `João` aparece `João`. Por
baixo, o sistema guarda também a forma **normalizada** (`joao`: sem acento,
minúscula, espaços colapsados) e é ela que é única e é ela que o login e a
transferência procuram.

São dois problemas, e só o par resolve os dois: sem normalizar, `João` e
`joao` viram duas contas; e quem se cadastrou com acento não consegue entrar
digitando sem, que é o que se faz no celular.

## O supply não é mais uma constante

Era 5.000 fixos. Deixou de ser quando o administrador ganhou o poder de
**ajustar saldo** para consertar valor errado: ajuste para cima cunha.

O supply de verdade passou a ser **o que o ledger diz** — a soma de todo
lançamento sem origem (`moeda.supply_emitido()`). `SUPPLY_INICIAL` continua
sendo 5.000, mas agora é só o ponto de partida, e o painel mostra os dois lado
a lado com o quanto já se cunhou.

O que **não** mudou: nada altera saldo fora do `mover()`. O ajuste é uma
operação em cima dele, não um desvio:

- **para cima** — usa primeiro o saldo não emitido do Banco Central; se faltar,
  emite a diferença numa linha `emissao` (sem origem, com ator e motivo) e só
  então move para a pessoa numa linha `ajuste`. Cunha só o que falta.
- **para baixo** — devolve ao Banco Central como `ajuste`. Não queima: volta a
  ser não emitido, como no dia zero.

Por isso **`flask auditoria` continua fechando depois de um ajuste**, e é esse
o teste que prova que ficou certo. Um alarme que dispara toda vez que o
administrador conserta algo é um alarme que se aprende a ignorar.

## Onde está o quê

| arquivo | o que guarda |
| --- | --- |
| `vavacoin/dinheiro.py` | tipo monetário: `Decimal` em Python, centavos inteiros no banco |
| `vavacoin/moeda.py` | `mover()` — o caminho único —, a gênese e a verificação de massa |
| `vavacoin/operacoes.py` | resgate do convite, transferência, reset |
| `vavacoin/auditoria.py` | extrato, estado da economia, reconstrução do ledger |
| `vavacoin/autoridade.py` | o Banco Central é o único administrador |
| `vavacoin/rotas/admin.py` | o painel de god mode |
| `vavacoin/limite.py` | os dois freios do login |
| `vavacoin/static/base.css` | o visual, herdado do Benbals |
| `vavacoin/static/menu.js` | o menu off-canvas do celular — o único JS do projeto |
| `vavacoin/static/caladinho.css` | o visual do cassino, prefixado `cal-` |
| `vavacoin/nomes.py` | normalização do nome de usuário (acento e caixa) |
| `vavacoin/mines.py` | a matemática do mines, pura |
| `vavacoin/caladinho.py` | o cassino onde ela encosta no ledger |
| `vavacoin/modelos.py` | `Usuario`, `Convite`, `Transacao` (ledger) |
| `vavacoin/constantes.py` | supply inicial e os identificadores das contas de sistema |

## Regras que o código impõe

- Nenhuma função fora de `mover()` escreve em saldo. A exceção é a gênese,
  que só roda com o ledger vazio e sem Banco Central.
- Dinheiro nunca é `float` — `para_decimal()` recusa, não converte.
- Nada de `commit` dentro da biblioteca: quem chama é dono da transação. As
  operações compostas rodam em `SAVEPOINT` para não deixar meio trabalho.
- Senha com hash bcrypt.
- **O Banco Central entra pelo site e tem god mode.** É decisão do autor,
  tomada de olhos abertos: quem entrar nele é dono de tudo. O que o código faz
  é não piorar — senha com hash, definida **só** por `flask senha-bc` (nunca no
  código, nunca em migration); a conta não entra enquanto a senha não for
  definida; os dois freios de login; e rastro de tudo. Ajuste de saldo exige
  motivo escrito, e até olhar o extrato alheio deixa registro.
- **Poder se pede explicitamente.** Criar conta, emitir convite e resetar exigem
  `autoridade=banco_central()`; não basta conseguir importar a função. A
  exceção é o cadastro pelo site, onde a autoridade chega **delegada pelo
  convite** — foi o BC que o emitiu, e ele vale uma conta só.
- **CSRF em todo formulário** (Flask-WTF), e **CSP restrita desde a primeira
  rota**: sem `unsafe-inline`, sem estilo inline, sem `on*=`, sem script. Um
  teste varre os templates e falha se algum entrar — apertar a CSP depois é o
  tipo de retrabalho que nunca acontece.
- **Transferência tem confirmação.** O que executa é o que o servidor guardou
  na sessão e desenhou na tela, não o que voltou no formulário: o confirmado
  não pode divergir do mostrado. A confirmação expira em 10 minutos.
- **Dois freios no login**, e eles fazem coisas diferentes:
  **limite de taxa** (15 tentativas a cada 5 minutos por endereço) protege o
  *servidor* de rajada; **trava por falhas consecutivas** na mesma conta
  (`FALHAS_ATE_TRAVAR`, com espera dobrando de 30s até 1h) protege a *conta*.
  Só o primeiro deixaria 4.320 chutes por dia contra uma senha fraca. Ambos
  em memória: somem no restart e não são compartilhados entre processos. Para
  um worker resolve; se um dia houver mais de um, vira tabela.

## Publicar no PythonAnywhere

Conta `vavacoin` (o Benbals já ocupa o web app da outra conta). Deploy por git,
como no ITA-IME. **Nenhum passo abaixo foi executado** — quem sobe é o autor.

1. **Console Bash** na conta `vavacoin`:

   ```bash
   git clone https://github.com/TuxGames/vavacoin.git vavacoin
   cd vavacoin
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. **Segredo e ambiente.** Gere uma chave e guarde-a fora do repositório:

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

   Guarde-a onde ela não volte para o git. O caminho confiável no
   PythonAnywhere é definir as duas variáveis **no arquivo WSGI do painel,
   antes do import** (ver passo 4); o campo *Environment variables* da aba
   Web não alcança os consoles, então repita o `export` no Bash quando for
   rodar comandos.

   Sem `VAVACOIN_SECRET_KEY`, com `VAVACOIN_ENV=producao`, a aplicação
   **se recusa a subir** — a chave padrão é pública, está aqui no repositório.

3. **Banco:**

   ```bash
   export FLASK_APP=wsgi.py VAVACOIN_ENV=producao VAVACOIN_SECRET_KEY=<a chave>
   .venv/bin/flask db upgrade
   .venv/bin/flask genese
   .venv/bin/flask senha-bc      # senha do painel; pedida sem eco
   .venv/bin/flask criar-cassino # a conta da casa do Caladinho
   .venv/bin/flask conservacao   # deve dizer 5000.00
   ```

4. **Web app:** *Web → Add a new web app → Manual configuration → Python 3*.
   - *Source code*: `/home/vavacoin/vavacoin`
   - *Virtualenv*: `/home/vavacoin/vavacoin/.venv`
   - No arquivo WSGI do painel, apontar para este projeto:

     ```python
     import os
     import sys

     sys.path.insert(0, "/home/vavacoin/vavacoin")
     os.environ["VAVACOIN_ENV"] = "producao"
     os.environ["VAVACOIN_SECRET_KEY"] = "<a chave gerada no passo 2>"

     from wsgi import application  # noqa: E402,F401
     ```

     O arquivo WSGI do painel fica fora do repositório, então a chave não
     corre risco de ser commitada.

   - *Static files*: URL `/static/` → `/home/vavacoin/vavacoin/vavacoin/static`

5. **Convites:** `flask convite --destinatario "Fulano"` por aluno, um cada.

6. **Depois de subir:** `flask auditoria` deve sair com código 0. Vale deixar
   rodando periodicamente — é o que acusa saldo escrito fora do `mover()`.

O banco fica em `/home/vavacoin/vavacoin/vavacoin.sqlite3`, dentro do clone
mas ignorado pelo git — `git pull` não encosta nele.

Atualizar: `git pull && .venv/bin/flask db upgrade` e *Reload* no painel.

### Atenção nas migrations que recriam a tabela `usuario`

Duas fazem isso (`353a30f6e6f5` e `5e2a8edc70ea`), porque o SQLite não tem
`ALTER` para constraint. **Backup antes das duas**, e `flask auditoria`
depois.

Um defeito conhecido, e já corrigido: a `353a30f6e6f5` recriou `usuario` com
`copy_from`, que carrega colunas e CHECKs mas **não carrega índices** — e o
UNIQUE de `nome_usuario` se perdeu. A `5e2a8edc70ea` recria os índices e move
o UNIQUE para `nome_normalizado`. Enquanto só a primeira estiver aplicada, o
banco aceita dois usuários com o mesmo nome.

`testes/test_migracoes.py` existe por causa disso: sobe um banco pelo caminho
real (`flask db upgrade`) e compara com o metadata dos modelos. A suíte usa
`create_all()`, que testa o que os modelos dizem, nunca o que as migrations
fazem — e o erro morava exatamente aí no meio.

### Atenção na migration `353a30f6e6f5` (num banco que já está no ar)

Ela derruba o `CHECK` que proibia senha no Banco Central, e o SQLite não tem
`DROP CONSTRAINT`: a tabela `usuario` — a que guarda os saldos — é **recriada
e copiada**. Antes de rodar:

```bash
cp vavacoin.sqlite3 vavacoin.sqlite3.bak-$(date +%F)
.venv/bin/flask db upgrade
.venv/bin/flask auditoria      # tem que sair com código 0
.venv/bin/flask senha-bc       # só então o painel abre
```

A migração já foi testada sobre um banco com dados do schema antigo: saldos,
convites e chaves estrangeiras preservados, `saldo >= 0` mantido, e o
`PRAGMA foreign_key_check` roda sozinho no fim (`migrations/env.py`) para
recusar a migração se algo ficar órfão.
